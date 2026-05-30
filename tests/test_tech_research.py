"""Tests for tech_research playbook + pass_through_to_llm wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openbro.playbooks.base import PlaybookContext
from openbro.playbooks.builtin.tech_research import (
    TechResearchPlaybook,
    _clean_text,
    _extract_code_blocks,
    _extract_urls,
    _looks_technical,
)

# ─── Matching ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "q",
    [
        "how do I get MTP permission in android kiosk mode",
        "what can we do in android app for getting MTP permission in kiosk mode?",
        "how to set up react with vite",
        "best way to deploy fastapi on aws",
        "why does my docker container exit immediately",
        "difference between flexbox and grid in css",
        "how to migrate from express to fastapi",
        "kotlin compose how to handle navigation",
        "postgres index not working error fix",
        "is it possible to use kotlin in jetpack compose for tv",
    ],
)
def test_matches_technical_how_to_questions(q):
    pb = TechResearchPlaybook()
    m = pb.match(q)
    assert m is not None, f"{q!r} should match"


@pytest.mark.parametrize(
    "q",
    [
        "kya time hua",
        "open chrome",
        "kitne pdfs hain",
        "hi",
        "how are you",  # 'how' but no tech keyword
        "explain better",  # blocker
        "tell me more",  # blocker
        "tldr",  # blocker
        "android",  # tech keyword but no question shape
        "what is your name",  # no tech keyword
    ],
)
def test_does_not_match_casual_or_followup(q):
    pb = TechResearchPlaybook()
    assert pb.match(q) is None, f"{q!r} should NOT match"


def test_too_short_queries_decline():
    pb = TechResearchPlaybook()
    assert pb.match("how to docker?") is None  # under 15 chars
    assert pb.match("react?") is None


# ─── Detection helpers ────────────────────────────────────────────────


def test_looks_technical_requires_both_keyword_and_question():
    assert _looks_technical("how to deploy react on aws") is True
    assert _looks_technical("android is cool") is False  # no question
    assert _looks_technical("what is the weather") is False  # no tech
    assert _looks_technical("") is False


def test_extract_urls_pulls_https_links():
    raw = """
    1. How to do X - https://stackoverflow.com/q/123
    2. Title here. https://developer.android.com/training/data-storage
    3. https://github.com/foo/bar/issues/42
    No URL here.
    """
    urls = _extract_urls(raw)
    assert len(urls) == 3
    assert "stackoverflow.com" in urls[0]
    assert "developer.android.com" in urls[1]
    assert "github.com" in urls[2]


def test_extract_urls_dedups_and_strips_trailing_punctuation():
    raw = """
    https://x.com/a,
    https://x.com/a.
    https://x.com/b)
    """
    urls = _extract_urls(raw)
    # Both 'https://x.com/a' variants dedup to one
    assert "https://x.com/a" in urls
    assert "https://x.com/b" in urls
    assert len(urls) == 2


def test_extract_code_blocks_finds_markdown_fenced():
    raw = """
    Some text here.
    ```kotlin
    fun main() {
        println("hello")
    }
    ```
    More text.
    ```bash
    docker run -it foo
    ```
    """
    blocks = _extract_code_blocks(raw)
    assert len(blocks) == 2
    assert "fun main" in blocks[0]
    assert "docker run" in blocks[1]


def test_clean_text_strips_html_and_noise():
    raw = "<p>Hello <b>world</b></p>\n\n\n\nCookie Settings\nReal content."
    out = _clean_text(raw)
    assert "<p>" not in out
    assert "<b>" not in out
    assert "Cookie Settings" not in out
    assert "Real content." in out


# ─── Execute ──────────────────────────────────────────────────────────


def test_execute_returns_empty_when_no_web_tool():
    pb = TechResearchPlaybook()
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = None
    ctx = PlaybookContext(
        user_input="how to set up react",
        tool_registry=fake_registry,
        captures={},
    )
    assert pb.execute(ctx) == ""


def test_execute_returns_empty_when_search_yields_no_urls():
    pb = TechResearchPlaybook()
    fake_web = MagicMock()
    fake_web.run.return_value = "No useful matches found."
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_web
    ctx = PlaybookContext(
        user_input="how to deploy fastapi on aws",
        tool_registry=fake_registry,
        captures={},
    )
    assert pb.execute(ctx) == ""


def test_execute_fetches_top_results_and_renders_with_sources():
    """Live integration: search → fetch top 3 → render markdown with
    excerpts. Verifies the playbook's grounded-context output."""
    pb = TechResearchPlaybook()
    fake_web = MagicMock()

    def fake_run(**kwargs):
        action = kwargs.get("action")
        if action == "search":
            return (
                "1. Android docs - https://developer.android.com/training/mtp\n"
                "2. SO - https://stackoverflow.com/q/12345\n"
                "3. GitHub - https://github.com/foo/bar/issues/7\n"
            )
        if action == "fetch":
            url = kwargs.get("url", "")
            if "developer.android.com" in url:
                return (
                    "Android MTP docs: To request MTP permission, use\n"
                    "```kotlin\n"
                    "val mtpRequest = MtpManager().requestPermission()\n"
                    "```\n" + "More text " * 100
                )
            if "stackoverflow" in url:
                return "SO answer: kiosk mode workaround details... " * 50
            return "Generic page " * 50
        return ""

    fake_web.run.side_effect = fake_run
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_web

    ctx = PlaybookContext(
        user_input="how to get MTP permission in android kiosk mode",
        tool_registry=fake_registry,
        captures={},
    )
    out = pb.execute(ctx)

    assert "Research brief" in out
    assert "Source 1:" in out
    assert "Source 2:" in out
    assert "Source 3:" in out
    assert "developer.android.com" in out
    assert "stackoverflow.com" in out
    # Code block from Android docs extracted into its own fenced block
    assert "MtpManager" in out
    # The 'synthesize grounded answer' instruction is present so the
    # downstream LLM knows what to do with the sources.
    assert "Synthesize" in out or "synthesize" in out.lower()
    # Lazy-phrase warning so the LLM knows not to fall back to filler
    assert "FORBIDDEN phrases" in out


def test_execute_drops_empty_fetches():
    """Pages that return very little content (errors, paywalls) shouldn't
    end up in the final output."""
    pb = TechResearchPlaybook()
    fake_web = MagicMock()

    def fake_run(**kwargs):
        if kwargs.get("action") == "search":
            return "1. - https://a.com\n2. - https://b.com\n3. - https://c.com"
        if kwargs.get("action") == "fetch":
            if "a.com" in kwargs.get("url", ""):
                return ""  # empty / error
            return "Real content. " * 100

    fake_web.run.side_effect = fake_run
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_web

    ctx = PlaybookContext(
        user_input="how to deploy react",
        tool_registry=fake_registry,
        captures={},
    )
    out = pb.execute(ctx)
    # a.com filtered out; b.com + c.com present
    assert "a.com" not in out
    assert "b.com" in out
    assert "c.com" in out


# ─── Registry + pass_through wiring ───────────────────────────────────


def test_tech_research_registered():
    from openbro.playbooks import PlaybookRegistry

    reg = PlaybookRegistry()
    names = [p.name for p in reg.list_all()]
    assert "tech_research" in names


def test_pass_through_flag_is_true():
    pb = TechResearchPlaybook()
    assert pb.pass_through_to_llm is True


def test_detect_lazy_response_finds_known_phrases():
    from openbro.playbooks.builtin.tech_research import detect_lazy_response

    for bad in [
        "I cannot directly test or execute code",
        "I was trained on a vast amount of data",
        "Test on different devices to ensure compatibility",
        "Make sure to handle different Android versions",
        "Consider using a library or framework",
        "Iterate through the following steps",
    ]:
        assert detect_lazy_response(bad), f"should flag: {bad!r}"


def test_detect_lazy_response_passes_specific_answers():
    from openbro.playbooks.builtin.tech_research import detect_lazy_response

    for ok in [
        "Use MtpManager.requestPermission() [Source 1].",
        "Add <uses-permission android:name='android.permission.MTP' /> "
        "to your manifest [Source 2].",
        "The kiosk mode workaround is documented at developer.android.com",
    ]:
        assert detect_lazy_response(ok) == [], f"should pass: {ok!r}"


def test_site_augmented_query_picks_right_qualifier():
    from openbro.playbooks.builtin.tech_research import _site_augmented_query

    assert "developer.android.com" in _site_augmented_query("how to get MTP in android")
    assert "react.dev" in _site_augmented_query("how to set state in react")
    assert "docs.docker.com" in _site_augmented_query("docker compose fix")
    assert _site_augmented_query("random non-tech question") is None


def test_agent_reflection_retries_lazy_responses(monkeypatch):
    """When the LLM returns a 'I cannot directly test' response after
    tech_research injected sources, the agent should detect that, inject
    a corrective system message, and retry ONCE."""
    from unittest.mock import patch

    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True

        # First call returns lazy text, second returns a real answer
        responses = [
            LLMResponse(
                content="I cannot directly test or execute code on devices. "
                "However, I can suggest you test on different devices.",
                usage={"input": 100, "output": 30},
            ),
            LLMResponse(
                content="## Answer\nUse MtpManager API [Source 1]. "
                "Steps: 1. Add permission. 2. Call requestPermission().",
                usage={"input": 110, "output": 40},
            ),
        ]
        fake_provider.chat.side_effect = responses
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)
        agent.playbook_registry._playbooks = []  # skip playbooks for this test

        out = agent.chat("how to get MTP permission in android")
        # Reflection caught the lazy response and retried -> we got the
        # second (specific) answer
        assert "MtpManager" in out
        assert fake_provider.chat.call_count == 2

        # The history should have a [REFLECTION RETRY] system message
        retry_markers = [
            m
            for m in agent.history
            if m.role == "system" and "REFLECTION RETRY" in (m.content or "")
        ]
        assert len(retry_markers) == 1


def test_agent_reflection_caps_at_one_retry(monkeypatch):
    """Don't retry forever — if the second response is ALSO lazy, accept
    it (so the loop terminates) and surface to the user as-is."""
    from unittest.mock import patch

    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True

        # Both responses lazy → agent should retry exactly once, then accept
        fake_provider.chat.side_effect = [
            LLMResponse(
                content="I cannot directly test this code.",
                usage={"input": 50, "output": 10},
            ),
            LLMResponse(
                content="Still: test on different devices to verify.",
                usage={"input": 60, "output": 12},
            ),
        ]
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)
        agent.playbook_registry._playbooks = []

        out = agent.chat("how to deploy fastapi on aws")
        assert fake_provider.chat.call_count == 2  # exactly one retry
        assert out  # something was returned


def test_agent_prunes_transient_research_after_synthesis(monkeypatch):
    """Captured failure: research context (~15K chars) stayed in
    agent.history forever, so the SECOND turn after a tech_research
    cascaded through Groq fallback chain hitting 413 / 'context
    overflow' on both the cloud retry and the local fallback. After
    the LLM synthesises, the [TRANSIENT_RESEARCH] system message must
    be removed from history."""
    from unittest.mock import patch

    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse
    from openbro.playbooks.base import Playbook

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        # First LLM call returns a clean answer (no lazy markers)
        fake_provider.chat.return_value = LLMResponse(
            content="## Answer\nUse X [Source 1].",
            usage={"input": 200, "output": 30},
        )
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)

        class _PassThrough(Playbook):
            name = "tech_research_test"
            pass_through_to_llm = True

            def execute(self, ctx):
                return "FETCHED CONTENT: 15K chars of source pages would be here"

        import re as _re

        pb = _PassThrough()
        pb.triggers = [(_re.compile(r"how to set up react"), 1.0)]
        agent.playbook_registry._playbooks = [pb]

        # Run a turn that triggers the pass-through playbook.
        out = agent.chat("how to set up react with vite")
        assert "Use X" in out

        # The [TRANSIENT_RESEARCH] system message should be GONE from
        # history after the synthesis completes.
        transient = [
            m
            for m in agent.history
            if m.role == "system" and "TRANSIENT_RESEARCH" in (m.content or "")
        ]
        assert transient == [], "transient research context leaked into history"

        # But the assistant's final answer IS persisted
        assistant_msgs = [m for m in agent.history if m.role == "assistant"]
        assert any("Use X" in m.content for m in assistant_msgs)


def test_research_context_does_not_accumulate_across_multiple_turns(monkeypatch):
    """After 3 consecutive tech_research turns, history should NOT
    contain 3 research blocks — only 0 (all pruned)."""
    from unittest.mock import patch

    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse
    from openbro.playbooks.base import Playbook

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        fake_provider.chat.return_value = LLMResponse(
            content="Answer.", usage={"input": 100, "output": 10}
        )
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)

        class _PT(Playbook):
            name = "pt"
            pass_through_to_llm = True

            def execute(self, ctx):
                return "Research block " * 200  # ~3.6K chars per turn

        import re as _re

        pb = _PT()
        pb.triggers = [(_re.compile(r"how to"), 1.0)]
        agent.playbook_registry._playbooks = [pb]

        for q in [
            "how to do thing X",
            "how to do thing Y",
            "how to do thing Z",
        ]:
            agent.chat(q)

        transient = [
            m
            for m in agent.history
            if m.role == "system" and "TRANSIENT_RESEARCH" in (m.content or "")
        ]
        assert transient == [], f"research context accumulated: {len(transient)} blocks survived"


def test_agent_falls_through_to_llm_when_playbook_pass_through(monkeypatch):
    """`pass_through_to_llm=True` playbooks should NOT short-circuit —
    their output gets injected into history as system context and the
    LLM runs one synthesis pass."""
    from unittest.mock import patch

    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse
    from openbro.playbooks.base import Playbook

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        fake_provider.chat.return_value = LLMResponse(
            content="Synthesised answer using sources.",
            usage={"input": 200, "output": 30},
        )
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)

        class _PassThroughPlaybook(Playbook):
            name = "test_pt"
            pass_through_to_llm = True

            def execute(self, ctx):
                return "RESEARCHED CONTEXT: sources are here"

        import re as _re

        pb = _PassThroughPlaybook()
        pb.triggers = [(_re.compile(r"research me"), 1.0)]
        agent.playbook_registry._playbooks = [pb]

        # Capture what the LLM SAW at the moment chat() was invoked —
        # the [TRANSIENT_RESEARCH] context is injected before the LLM
        # call and pruned after, so a post-call inspection won't see
        # it (that's the whole point — the prune is what we want).
        seen_during_call: list = []

        def fake_chat(messages, tools=None):
            seen_during_call.extend(messages)
            return LLMResponse(
                content="Synthesised answer using sources.",
                usage={"input": 200, "output": 30},
            )

        fake_provider.chat.side_effect = fake_chat
        # Clear the default return_value so side_effect runs
        fake_provider.chat.return_value = None

        response = agent.chat("research me please")
        # LLM was actually called (NOT short-circuited)
        fake_provider.chat.assert_called()
        # The playbook output WAS in the messages list the LLM saw
        injected = [
            m
            for m in seen_during_call
            if m.role == "system" and "RESEARCHED CONTEXT" in m.content
        ]
        assert len(injected) >= 1
        # And the LLM's synthesised answer came back to the user
        assert "Synthesised" in response or "synth" in response.lower()
