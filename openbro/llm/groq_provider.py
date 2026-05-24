"""Groq LLM provider - ultra-fast inference, free tier available."""

import json
import re

import httpx

from openbro.llm.base import LLMProvider, LLMResponse, Message

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


# Llama 3.3 70B on Groq has a known function-call serialization quirk: the
# arguments get glued INTO the name field with all sorts of separators:
#   web={"action":"search"}             <- equals
#   browser={"action":"search"}         <- equals
#   browser{"action":"search"}          <- no separator
#   word,{"action":"append"}            <- comma
#   word {"action":"append"}            <- space
# Groq's tool-call validator then rejects the call because none of those
# garbled names match a registered tool — the request comes back as a 400.
# We salvage the call by detecting `<name><sep?>{<json>}` and splitting it
# back out before the agent loop sees the response. Separator is optional;
# only the leading identifier and the trailing JSON object are required.
_GLUED_NAME = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*[=,:;\-]?\s*(\{.*\})\s*$",
    re.DOTALL,
)


def _sanitize_tool_call(name: str, arguments: str | dict) -> tuple[str, dict]:
    """Recover (name, args dict) from Llama-on-Groq glued tool calls."""
    if isinstance(arguments, dict):
        args = arguments
    else:
        try:
            args = json.loads(arguments) if arguments else {}
        except (TypeError, ValueError):
            args = {}

    if isinstance(name, str):
        m = _GLUED_NAME.match(name.strip())
        if m:
            real_name, glued_args = m.group(1), m.group(2)
            try:
                parsed = json.loads(glued_args)
                if isinstance(parsed, dict):
                    # Glued args win — they're what the model meant to send.
                    args = parsed
                    name = real_name
            except (TypeError, ValueError):
                # Glob looked like name={...} but the {...} isn't valid JSON.
                # Keep the original name; the agent will report 'unknown tool'
                # and the LLM gets a chance to retry.
                pass
    return name, args


def _extract_inline_tool_calls(content: str) -> list[dict]:
    """Recover tool calls that the model dumped as JSON in content.

    Some models (notably llama-3.3-70b-versatile, llama-3.1-8b-instant
    on Groq) ignore the structured tool_calls slot and instead emit
    payloads like:

        [ { "name": "file_ops", "parameters": {"action": "open", ...} } ]
        { "name": "word", "arguments": {"action": "read", "file": "..."} }
        ```json\n[ ... ]\n```

    The agent treats this as plain text, so the call never runs and the
    user sees raw JSON as the reply. Strip code fences, parse, normalize
    'parameters' -> 'arguments', return a list shaped like the proper
    tool_calls field so _execute_tool_batch handles it uniformly.
    Returns [] if nothing recognizable is found.
    """
    if not content or not content.strip():
        return []
    text = content.strip()
    # Strip ```json ... ``` or ``` ... ``` wrappers.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Look for the first JSON value (array or object) in the text.
    # If the model wrapped it with prose, find the bracket span.
    start = -1
    end = -1
    for i, ch in enumerate(text):
        if ch in "[{":
            start = i
            break
    if start < 0:
        return []
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return []
    blob = text[start:end]
    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return []

    candidates = parsed if isinstance(parsed, list) else [parsed]
    out: list[dict] = []
    for i, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool")
        if not name or not isinstance(name, str):
            continue
        args = item.get("arguments")
        if args is None:
            args = item.get("parameters")  # some models say 'parameters'
        if args is None:
            args = item.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        out.append(
            {
                "id": item.get("id") or f"inline_{i}",
                "function": {"name": name, "arguments": args},
            }
        )
    return out


def _serialize_message(m: Message) -> dict:
    """Render a Message in OpenAI/Groq wire format, preserving tool_calls.

    Why this matters: the previous serialization dropped Message.tool_calls
    and Message.tool_call_id and only kept role+content. After a tool
    round-trip, the LLM saw 'Tools called: X(...)' and 'Tool results: ...'
    as plain assistant/user text — and on the next iteration it echoed
    those lines back as its own response (user saw 'Tools called:
    browser({"action": "search"...})' in the chat). Using the proper
    tool_calls + role='tool' schema makes the LLM treat them as
    structured tool round-trips and reply with prose.
    """
    base = {"role": m.role, "content": m.content or ""}
    if m.tool_calls:
        # Assistant message that called tools. content may be empty.
        base["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": (
                        json.dumps(tc.get("function", {}).get("arguments", {}))
                        if isinstance(tc.get("function", {}).get("arguments"), dict)
                        else tc.get("function", {}).get("arguments", "")
                    ),
                },
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id:
        # Tool result message — must include tool_call_id linking back
        # to the call that produced it.
        base["tool_call_id"] = m.tool_call_id
    return base


_DEFAULT_FALLBACK_CHAIN = [
    # Order is failover priority: try primary first, then these on 429 /
    # 503. All currently active on Groq free tier (May 2026). 3.1-70b is
    # decommissioned and omitted; 3.3-70b has tool-call quirks the
    # sanitizer recovers.
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]


class GroqProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
        fallback_models: list[str] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        # Build chain: configured model first, then any unique defaults.
        # This way 'rate limit on primary' silently rolls over to a
        # different family — gpt-oss-20b free tier is tighter than
        # llama-3.3-70b, but both work, so failover keeps the user
        # unblocked instead of the cryptic 'Rate limit hit ho gaya'.
        chain = [model]
        for m in fallback_models or _DEFAULT_FALLBACK_CHAIN:
            if m not in chain:
                chain.append(m)
        self._chain = chain

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        last_error: Exception | None = None
        for attempt_model in self._chain:
            try:
                return self._chat_with_model(attempt_model, messages, tools)
            except RuntimeError as e:
                msg = str(e).lower()
                # Failover triggers:
                # - 429 / rate_limit: primary model out of quota
                # - 400 + 'failed to parse tool call arguments as json':
                #   the model emitted malformed tool serialization.
                #   gpt-oss-20b on Groq does this when it crams raw
                #   python code into the arguments field instead of
                #   wrapping it as {"code": "..."}. A different model
                #   in the chain (especially llama-3.3-70b which we have
                #   the sanitizer for) generates clean tool calls.
                failover = (
                    "429" in msg
                    or "rate_limit" in msg
                    or "rate limit" in msg
                    or "413" in msg  # request too large / TPM throttling
                    or "tokens per minute" in msg
                    or "request too large" in msg
                    # Llama 3.3 tool-call serialization bugs — different
                    # models in the chain generate cleaner output. The
                    # 'failed to call a function' phrasing covers the
                    # <function=name [args]</function> variant; 'failed
                    # to parse tool call arguments' covers raw-code-in-
                    # arguments. Both warrant trying a different model.
                    or ("400" in msg and "failed to call a function" in msg)
                    or ("400" in msg and "failed to parse tool call arguments" in msg)
                )
                if not failover:
                    raise
                last_error = e
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("Groq: no models in fallback chain")

    def _chat_with_model(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict] | None,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [_serialize_message(m) for m in messages],
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        resp = httpx.post(GROQ_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            # Groq puts the real reason in body.error.message AND, for
            # tool-call failures, the model's malformed output in
            # body.error.failed_generation. Surface both so the user can
            # see what the model tried to emit (otherwise 'Failed to call
            # a function. Please adjust your prompt.' is useless — we
            # don't see WHAT got generated).
            body = ""
            try:
                err = resp.json()
                error_obj = err.get("error", {}) if isinstance(err, dict) else {}
                message = error_obj.get("message") or ""
                failed = error_obj.get("failed_generation") or ""
                if message and failed:
                    body = f"{message}\n  failed_generation: {failed[:600]}"
                else:
                    body = message or json.dumps(err)
            except (ValueError, KeyError):
                body = resp.text[:600]

            # 400 on a tool-call request is usually the model emitting bad
            # function-call syntax (Llama 3.3 70B is known for this on Groq
            # when there are many tools). Retry once WITHOUT tools so the
            # user still gets a text reply rather than 'try again later'.
            if resp.status_code == 400 and tools:
                fallback_payload = dict(payload)
                fallback_payload.pop("tools", None)
                try:
                    retry = httpx.post(
                        GROQ_API_URL, json=fallback_payload, headers=headers, timeout=30
                    )
                    if retry.status_code == 200:
                        return self._parse_response(retry.json())
                except httpx.HTTPError:
                    pass

            raise RuntimeError(f"Groq {resp.status_code}: {body}")
        data = resp.json()

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> LLMResponse:
        choice = data["choices"][0]["message"]
        tool_calls = []
        content = choice.get("content", "") or ""
        if choice.get("tool_calls"):
            for tc in choice["tool_calls"]:
                raw_name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                clean_name, clean_args = _sanitize_tool_call(raw_name, raw_args)
                tool_calls.append(
                    {
                        "id": tc["id"],
                        "function": {
                            "name": clean_name,
                            "arguments": clean_args,
                        },
                    }
                )
        # Fallback: model dumped tool call as JSON in content instead
        # of using the tool_calls field. llama-3.3-70b-versatile does
        # this routinely with payloads like
        #   [ { "name": "file_ops", "parameters": {"action":"open",...} } ]
        # or
        #   { "name": "word", "arguments": {"action":"read","file":"..."} }
        # Real user incident (2026-05-25): user asked agent to open a
        # PDF, agent printed the literal JSON as its reply (with a ◆
        # bullet prefix) and the tool never ran. Detect → extract →
        # convert to proper tool_calls; clear content so the user
        # doesn't see the raw JSON.
        if not tool_calls and content:
            extracted = _extract_inline_tool_calls(content)
            if extracted:
                tool_calls = extracted
                content = ""

        return LLMResponse(
            content=content,  # may have been cleared by inline-tool-call extraction
            tool_calls=tool_calls,
            usage={
                "input": data.get("usage", {}).get("prompt_tokens", 0),
                "output": data.get("usage", {}).get("completion_tokens", 0),
            },
            # Echo the model Groq actually served (may differ from
            # self.model if the fallback chain rolled over after a 429).
            model=data.get("model") or self.model,
        )

    def supports_tools(self) -> bool:
        return True

    def name(self) -> str:
        return f"groq/{self.model}"
