"""Anthropic (Claude) LLM provider."""

from openbro.llm.base import LLMProvider, LLMResponse, Message


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        try:
            import anthropic
        except ImportError:
            raise ImportError("Install anthropic: pip install openbro[anthropic]")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        system_msg = None
        chat_msgs = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_msgs.append({"role": m.role, "content": m.content})

        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": chat_msgs,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = tools

        resp = self.client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "function": {"name": block.name, "arguments": block.input},
                    }
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
            model=self.model,
        )

    def supports_tools(self) -> bool:
        return True

    def name(self) -> str:
        return f"anthropic/{self.model}"
