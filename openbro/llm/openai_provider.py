"""OpenAI (GPT) LLM provider."""

from openbro.llm.base import LLMProvider, LLMResponse, Message
from openbro.llm.wire import parse_arguments, to_openai_messages


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        try:
            import openai
        except ImportError:
            raise ImportError("Install openai: pip install openbro[openai]")
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        # Structured history: the model must see its own tool calls.
        chat_msgs = to_openai_messages(messages)

        kwargs = {"model": self.model, "messages": chat_msgs}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message

        tool_calls = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": parse_arguments(tc.function.arguments),
                        },
                    }
                )

        return LLMResponse(
            content=choice.content or "",
            tool_calls=tool_calls,
            usage={
                "input": resp.usage.prompt_tokens,
                "output": resp.usage.completion_tokens,
            },
            model=self.model,
        )

    def supports_tools(self) -> bool:
        return True

    def name(self) -> str:
        return f"openai/{self.model}"
