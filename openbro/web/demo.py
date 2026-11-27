"""The demo agent: what a public visitor's OpenBro can and cannot do.

A web page is open to strangers, so the demo agent is built differently
from the terminal one:

- Tools: only `web` (search + fetch of PUBLIC addresses), `datetime` and
  `docs` over a separate demo index. No shell, files, apps, browser
  control, memory writes or sub-agents.
- Permission gate: anything above SAFE risk is refused without asking
  (there is nobody to ask). `docs add/remove` are MODERATE, so they never run.
- Context: none of the host's personal context — no memory notes, no
  OPENBRO.md files, no workspace, folder or drive facts, no user hooks,
  no playbooks.
- History lives in memory only and disappears with the session.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from openbro.core.agent import Agent
from openbro.core.hooks import HookRunner
from openbro.core.permissions import PermissionGate, PermissionRequest
from openbro.memory.manager import MemoryManager
from openbro.memory.transcript import InMemoryTranscriptStore
from openbro.tools.base import RiskLevel
from openbro.tools.datetime_tool import DateTimeTool
from openbro.tools.docs_tool import DocsTool
from openbro.tools.registry import ToolRegistry
from openbro.tools.web_tool import WebTool

DEMO_TOOLS = ("web", "datetime", "docs")

DEMO_PROMPT = """You are OpenBro, an open-source personal AI agent, running here as a \
public demo on a web page. Visitors are often recruiters or developers who want to \
see what OpenBro is and how it works.

What OpenBro is: a terminal-first personal AI agent built in Python. On a user's own \
laptop it runs a full agent loop with permission-gated tools (shell, files, apps, \
Office documents, a real browser it can operate, voice), long-term memory, document \
search (RAG), context compaction, sub-agents, hooks and permission rules. It works \
with many LLM providers, including fully offline local models.

In this web demo you only have three tools: `web` (search the web, fetch public \
pages), `datetime`, and `docs` (search OpenBro's own documentation: README, harness \
and RAG design). Use `docs` for questions about OpenBro itself and cite the document \
you used by its file name, e.g. (RAG.md) — never doc ids or bracket markers. \
You cannot touch this server's files, run commands, or remember visitors \
between sessions — if asked, explain that those features run on the user's own \
machine, not in the public demo.

Be clear, friendly and concise. Use Markdown (short paragraphs, lists, `code`). \
Never invent tool results: if a tool fails, say so."""

DEMO_PRINCIPLES = """
## HOW TO WORK
1. If a tool can answer, call it instead of guessing; then answer from its result.
2. Questions about OpenBro, its design or its code: search `docs` first. One or two \
searches are usually enough — then answer.
3. Current facts (news, prices, products, weather): use `web` search, then fetch a \
result page if the snippets are not enough.
4. Keep answers short; offer to go deeper."""


# ─── safety: public addresses only ───────────────────────────────────


def is_public_url(url: str) -> bool:
    """True when `url` is http(s) and every address its host resolves to
    is a public internet address. Blocks localhost, private networks,
    link-local (cloud metadata) and other internal targets."""
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


class PublicWebTool(WebTool):
    """`web` for the demo: fetch follows redirects by hand and checks every
    hop, so a public link cannot bounce the server to an internal address."""

    MAX_REDIRECTS = 5
    MAX_CHARS = 3000

    def run(self, *args, **kwargs) -> str:
        return _cap(super().run(*args, **kwargs), self.MAX_CHARS)

    def _fetch(self, url: str) -> str:
        if not url:
            return "URL required for fetch"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            for _ in range(self.MAX_REDIRECTS + 1):
                if not is_public_url(url):
                    return f"Fetch blocked: {url} is not a public internet address."
                resp = httpx.get(url, timeout=15, follow_redirects=False)
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                resp.raise_for_status()
                return f"Status: {resp.status_code}\nContent:\n{resp.text[:5000]}"
            return "Fetch error: too many redirects"
        except Exception as e:
            return f"Fetch error: {e}"


def _cap(text: str, limit: int) -> str:
    """Keep tool output small: the free tier allows ~8K tokens per minute."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[{len(text) - limit} more characters cut to keep the demo fast]"


class DemoDocsTool(DocsTool):
    """`docs` for the demo: search, read and list only; short results; paths
    shown relative to the demo documents, not as host paths."""

    MAX_HITS = 3
    MAX_CHARS = 3000

    def __init__(self, service=None, root: Path | None = None):
        super().__init__(service=service)
        self._root = str(root) if root else ""

    def run(self, action: str = "", **kwargs) -> str:
        action = (action or "").lower().strip()
        if action not in ("search", "read", "list"):
            name = action or "(none)"
            return f"docs {name} is not available in the web demo (use search, read or list)."
        if action == "search":
            kwargs["k"] = min(int(kwargs.get("k") or self.MAX_HITS), self.MAX_HITS)
        out = super().run(action=action, **kwargs)
        if self._root:
            out = out.replace(self._root + os.sep, "").replace(self._root, "")
        return _cap(out, self.MAX_CHARS)


class DemoGate(PermissionGate):
    """Runs SAFE tool calls, refuses everything else — nobody is there to ask."""

    def __init__(self):
        super().__init__(mode="normal", channel="silent")

    def request(self, req: PermissionRequest) -> bool:
        return req.risk == RiskLevel.SAFE  # RiskLevel is a str enum: 'safe'


class DemoMemory(MemoryManager):
    """Per-visitor memory: the conversation only, nothing from the host."""

    def __init__(self, session_id: str):
        super().__init__(
            user_id=f"web-{session_id}",
            channel="web",
            session_id=session_id,
            transcripts=InMemoryTranscriptStore(),
        )

    def memory_block(self, query: str = "", cwd: Path | None = None) -> str:
        return ""

    def context_prompt(self) -> str:
        return ""

    def save_summary(self, *args, **kwargs) -> None:
        return None


class DemoAgent(Agent):
    """An Agent with no host context. See the module docstring."""

    def _workspace_block(self) -> str:
        return ""

    def _world_facts_block(self) -> str:
        return ""

    def _instruction_files(self) -> list:
        return []

    def _intent_check_block(self) -> str:
        return DEMO_PRINCIPLES

    def _maybe_record_goal(self, user_input: str) -> None:
        return None

    def end_session(self) -> list[str]:
        return []


def build_demo_registry(docs_service=None, docs_root: Path | None = None) -> ToolRegistry:
    """A registry holding only the demo tools (fresh instances).

    Without a demo docs service the `docs` tool is left out: DocsTool
    would otherwise fall back to the host's own document index."""
    base = ToolRegistry()
    registry = base.view(set(DEMO_TOOLS))
    registry._tools = {"web": PublicWebTool(), "datetime": DateTimeTool()}
    if docs_service is not None:
        registry._tools["docs"] = DemoDocsTool(service=docs_service, root=docs_root)
    registry._builtin_names = set(registry._tools)
    registry._deferred = set()
    registry._loaded = set()
    return registry


# History budget for one visitor: small, because the free tier allows
# ~8K tokens per minute per model.
DEMO_CONTEXT_TOKENS = 6000


def build_demo_agent(
    session_id: str,
    provider,
    bus,
    docs_service=None,
    max_steps: int = 5,
    docs_root: Path | None = None,
):
    """One visitor's agent."""
    agent = DemoAgent(
        memory=DemoMemory(session_id),
        interactive=False,
        permission_gate=DemoGate(),
        provider=provider,
        tool_registry=build_demo_registry(docs_service, docs_root),
        role_prompt="",  # non-None: no console banner, no stop hook
        max_steps=max_steps,
        bus=bus,
    )
    agent.hooks = HookRunner()  # the host's hooks never run for visitors
    agent.playbooks_enabled = False  # playbooks call tools directly
    agent.compactor.config.max_context_tokens = DEMO_CONTEXT_TOKENS
    agent.base_system_prompt = DEMO_PROMPT
    agent.refresh_system_prompt()
    return agent


# ─── provider and demo documents ─────────────────────────────────────


def build_demo_provider():
    """The LLM for the demo. A free API key from the environment wins
    (`GROQ_API_KEY`, model from `OPENBRO_WEB_MODEL`); otherwise the
    provider from the OpenBro config is used."""
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        from openbro.llm.groq_provider import GroqProvider

        model = os.environ.get("OPENBRO_WEB_MODEL", "").strip()
        return GroqProvider(api_key=groq_key, model=model) if model else GroqProvider(groq_key)
    from openbro.llm.router import create_provider

    return create_provider()


def default_demo_docs() -> list[Path]:
    """README + docs/ of the repo this package runs from, when present."""
    root = Path(__file__).resolve().parents[2]
    return [p for p in (root / "README.md", root / "docs") if p.exists()]


def demo_rag_config() -> dict:
    """Embedding settings for the demo, from the environment.

    `OPENBRO_WEB_EMBED_MODEL` picks the embedding model (the host's
    default when unset); `none` turns meaning search off (keywords only).
    Small hosts use a small English model: the multilingual default
    needs ~640 MB of RAM, `BAAI/bge-small-en-v1.5` ~240 MB."""
    model = os.environ.get("OPENBRO_WEB_EMBED_MODEL", "").strip()
    if not model:
        return {}
    if model.lower() == "none":
        return {"embedder": "none"}
    return {"embed_model": model}


def build_demo_docs_service(data_dir: Path, sources: list[Path]):
    """A separate document index for the demo (never the host's own)."""
    from openbro.rag import build_docs_service

    data_dir.mkdir(parents=True, exist_ok=True)
    svc = build_docs_service(
        {"storage": {"base_dir": str(data_dir), "backend": "local"}, "rag": demo_rag_config()}
    )
    for src in sources:
        try:
            svc.add(str(src))
        except Exception:
            continue
    return svc
