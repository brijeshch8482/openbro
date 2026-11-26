"""Text tool-call protocol for providers without native function calling.

The subscription CLI providers and the local specialist models return
plain text — there is no structured `tool_calls` slot. For those providers the
agent describes OpenBro's tools in the system prompt and asks the model to
emit calls as tags:

    <tool_call name="file_ops">{"action": "read", "path": "D:/notes.txt"}</tool_call>

`parse_tool_calls` lifts those tags back into the same `tool_calls` shape
native providers return, so the agent loop (risk → permission gate →
execute → audit → role="tool" message) handles both paths identically.

`render_transcript` is the inverse for CLI providers that take one prompt
string: it writes earlier tool calls back as tags and tool output as
`<tool_result>` blocks, so the model sees its own protocol in the history.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

# <tool_call name="x">{...}</tool_call>  — name attribute form (primary).
_NAMED_RE = re.compile(
    r"<tool_call\s+name\s*=\s*[\"']?(?P<name>[\w.-]+)[\"']?\s*>(?P<body>.*?)</tool_call\s*>",
    re.DOTALL | re.IGNORECASE,
)
# <tool_call>{"name": "x", "arguments": {...}}</tool_call>  — name-inside-JSON form.
_BARE_RE = re.compile(
    r"<tool_call\s*>(?P<body>.*?)</tool_call\s*>",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _schema_parts(schema: dict) -> tuple[str, str, dict]:
    """Return (name, description, parameters) for either schema shape:
    OpenBro's flat {name, description, parameters} or OpenAI's
    {"type": "function", "function": {...}}."""
    fn = schema.get("function") if isinstance(schema.get("function"), dict) else schema
    return (
        str(fn.get("name", "")),
        str(fn.get("description", "")).strip(),
        fn.get("parameters") or {},
    )


def _param_line(pname: str, spec: dict, required: bool) -> str:
    ptype = spec.get("type", "any")
    if isinstance(ptype, list):
        ptype = "|".join(str(t) for t in ptype)
    bits = [str(ptype)]
    if required:
        bits.append("required")
    enum = spec.get("enum")
    if enum:
        bits.append("one of: " + " | ".join(str(e) for e in enum))
    desc = str(spec.get("description", "")).strip()
    line = f"  - {pname} ({', '.join(bits)})"
    return f"{line}: {desc}" if desc else line


def render_tool_instructions(schemas: Iterable[dict]) -> str:
    """System-prompt block that teaches a text-only model to call tools."""
    lines = [
        "## TOOLS — how to call them",
        "You have no native function-calling here. To use a tool, write a tag:",
        "",
        '<tool_call name="TOOL_NAME">{"arg": "value"}</tool_call>',
        "",
        "Rules:",
        "- The body is ONE JSON object with the tool's arguments.",
        "- You may write several <tool_call> tags in one reply; they run in order.",
        "- After your tool calls, STOP and wait. Results come back as"
        " <tool_result> blocks in the next message.",
        "- Use ONLY the tools listed below. Do not use any built-in tools of"
        " your own runtime (shell, file editing, web) — OpenBro's tools are"
        " permission-gated and audited, yours are not.",
        "- When the task is done, reply in plain text with no <tool_call> tag.",
        "",
        "Available tools:",
    ]
    for schema in schemas:
        name, desc, params = _schema_parts(schema)
        if not name:
            continue
        lines.append("")
        lines.append(f"### {name}")
        if desc:
            lines.append(desc)
        props = params.get("properties") or {}
        required = set(params.get("required") or [])
        if props:
            lines.append("Arguments:")
            for pname, spec in props.items():
                lines.append(_param_line(pname, spec or {}, pname in required))
    return "\n".join(lines)


def _load_json_object(body: str) -> dict | None:
    text = _FENCE_RE.sub("", body.strip()).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _coerce_args(args) -> dict:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            return {}
    return args if isinstance(args, dict) else {}


def parse_tool_calls(text: str, known_names: Iterable[str]) -> tuple[str, list[dict]]:
    """Extract `<tool_call>` tags from model text.

    Returns (clean_text, tool_calls) where clean_text has every tag
    removed and tool_calls is shaped like native provider output:
    [{"id": "text_0", "function": {"name": ..., "arguments": {...}}}].
    Calls naming an unknown tool are dropped (left out of the call list
    but still stripped from the text) so prose that merely mentions the
    tag can never run something unexpected.
    """
    if not text or "<tool_call" not in text.lower():
        return text or "", []
    known = set(known_names)
    found: list[tuple[int, str, dict]] = []
    spans: list[tuple[int, int]] = []

    for m in _NAMED_RE.finditer(text):
        spans.append(m.span())
        args = _load_json_object(m.group("body"))
        name = m.group("name")
        if args is not None and name in known:
            found.append((m.start(), name, args))

    for m in _BARE_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        spans.append(m.span())
        payload = _load_json_object(m.group("body"))
        if not payload:
            continue
        name = payload.get("name") or payload.get("tool")
        if not isinstance(name, str) or name not in known:
            continue
        args = payload.get("arguments")
        if args is None:
            args = payload.get("parameters", payload.get("args"))
        found.append((m.start(), name, _coerce_args(args)))

    found.sort(key=lambda item: item[0])
    calls = [
        {"id": f"text_{i}", "function": {"name": name, "arguments": args}}
        for i, (_, name, args) in enumerate(found)
    ]

    clean = text
    for start, end in sorted(spans, reverse=True):
        clean = clean[:start] + clean[end:]
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, calls


def render_transcript(messages) -> str:
    """Flatten a message list into one prompt string for CLI providers.

    Tool calls made by the assistant are written back as `<tool_call>`
    tags and tool output as `<tool_result>` blocks, so a text-protocol
    model sees a consistent history.
    """
    names_by_id: dict[str, str] = {}
    parts: list[str] = []
    for m in messages:
        role = (getattr(m, "role", "") or "user").lower()
        content = getattr(m, "content", "") or ""
        if role == "system":
            if content:
                parts.append(f"## System\n{content}")
        elif role == "user":
            if content:
                parts.append(f"## User\n{content}")
        elif role == "assistant":
            body = [content] if content else []
            for call in getattr(m, "tool_calls", None) or []:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                name = fn.get("name", "")
                if not name:
                    continue
                names_by_id[call.get("id", "")] = name
                args = json.dumps(_coerce_args(fn.get("arguments", {})), ensure_ascii=False)
                body.append(f'<tool_call name="{name}">{args}</tool_call>')
            if body:
                parts.append("## Assistant\n" + "\n".join(body))
        elif role == "tool":
            name = names_by_id.get(getattr(m, "tool_call_id", None) or "", "")
            attr = f' name="{name}"' if name else ""
            parts.append(f"## Tool output\n<tool_result{attr}>\n{content}\n</tool_result>")
    return "\n\n".join(parts).strip()
