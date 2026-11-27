"""`openbro web` — the HTTP server behind the demo chat page.

Endpoints:
    GET  /             the chat page
    GET  /api/info     name, version, model, tools, limits, sample questions
    POST /api/chat     {message, session_id?} → Server-Sent Events stream
    POST /api/reset    {session_id} → forget that conversation
    GET  /healthz      liveness

The chat stream sends one JSON object per event:
    {"type": "session", "id": ...}           first, so the page can keep it
    {"type": "thinking", "step": n}          the model is working
    {"type": "tool", "phase": "start", ...}  a tool call began
    {"type": "tool", "phase": "end", ...}    ... and finished
    {"type": "answer", "text": ...}          the final reply (Markdown)
    {"type": "error", "message": ...}
    {"type": "done"}
"""

import json
import os
import queue
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from openbro import __version__
from openbro.core.activity import ActivityBus
from openbro.web.sessions import RateLimiter, SessionStore, WebSettings

STATIC_DIR = Path(__file__).parent / "static"

SUGGESTIONS = [
    "What is OpenBro and what can it do?",
    "How does OpenBro's agent loop work, step by step?",
    "How does its document search (RAG) rank results?",
    "What's in the tech news today?",
]

_DONE = object()


def _short_args(args) -> str:
    """One line describing a tool call: its action plus query/url."""
    if not isinstance(args, dict):
        return ""
    bits = [str(args[k]) for k in ("action", "query", "url", "operation") if args.get(k)]
    return " · ".join(bits)[:160]


def bus_to_event(ev) -> dict | None:
    """Map an agent ActivityBus event to what the page shows."""
    meta = ev.meta or {}
    if ev.kind == "llm_start":
        return {"type": "thinking", "step": meta.get("step", 1)}
    if ev.kind == "tool_start":
        return {
            "type": "tool",
            "phase": "start",
            "tool": meta.get("tool", "?"),
            "detail": _short_args(meta.get("args")),
        }
    if ev.kind == "tool_end":
        preview = str(meta.get("preview") or ev.text or "")
        return {
            "type": "tool",
            "phase": "end",
            "tool": meta.get("tool", "?"),
            "ok": bool(meta.get("ok", True)),
            "elapsed": round(float(meta.get("elapsed") or 0.0), 2),
            "preview": preview[:240],
        }
    return None


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class DemoBackend:
    """Sessions + limits + running one turn. Kept apart from FastAPI so
    tests can drive it directly."""

    def __init__(
        self,
        settings: WebSettings,
        agent_factory: Callable[[str, ActivityBus], object],
        model_name: str = "",
        tool_names: list[str] | None = None,
    ):
        self.settings = settings
        self.model_name = model_name
        self.tool_names = tool_names or []
        self.limiter = RateLimiter(settings.per_ip_per_hour, settings.global_per_hour)
        self._turns = threading.BoundedSemaphore(settings.max_concurrent_turns)

        def _make(sid: str):
            # Each visitor's agent gets its own bus, so events never mix.
            return agent_factory(sid, ActivityBus(max_history=50))

        self.sessions = SessionStore(_make, settings.session_ttl_seconds, settings.max_sessions)

    def info(self) -> dict:
        s = self.settings
        return {
            "name": "OpenBro",
            "version": __version__,
            "model": self.model_name,
            "tools": self.tool_names,
            "limits": {
                "max_message_chars": s.max_message_chars,
                "per_ip_per_hour": s.per_ip_per_hour,
                "max_turns_per_session": s.max_turns_per_session,
            },
            "suggestions": SUGGESTIONS,
        }

    def check(self, message: str, client_ip: str) -> tuple[int, str] | None:
        """(http_status, reason) when the request must be refused."""
        message = (message or "").strip()
        if not message:
            return 400, "Type a message first."
        if len(message) > self.settings.max_message_chars:
            return 413, f"Please keep messages under {self.settings.max_message_chars} characters."
        ok, retry = self.limiter.check(client_ip)
        if not ok:
            minutes = max(1, retry // 60)
            return 429, f"Demo limit reached — try again in about {minutes} min."
        return None

    def run_turn(self, message: str, session_id: str | None) -> Iterator[str]:
        """Run one turn and yield SSE frames. Never raises."""
        if not self._turns.acquire(blocking=False):
            yield _sse({"type": "error", "message": "OpenBro is busy right now — try again soon."})
            yield _sse({"type": "done"})
            return
        try:
            session = self.sessions.get_or_create(session_id)
        except Exception as e:
            self._turns.release()
            yield _sse({"type": "error", "message": f"Could not start a session: {e}"})
            yield _sse({"type": "done"})
            return
        yield _sse({"type": "session", "id": session.id})

        if session.turns >= self.settings.max_turns_per_session:
            self._turns.release()
            yield _sse(
                {"type": "error", "message": "This chat is at its limit — start a new chat."}
            )
            yield _sse({"type": "done"})
            return
        if not session.lock.acquire(blocking=False):
            self._turns.release()
            yield _sse({"type": "error", "message": "Still answering your last message."})
            yield _sse({"type": "done"})
            return

        events: queue.Queue = queue.Queue()
        bus = getattr(session.agent, "bus", None)

        def _on_event(ev) -> None:
            mapped = bus_to_event(ev)
            if mapped:
                events.put(mapped)

        unsub = bus.subscribe(_on_event) if bus else None

        def _work() -> None:
            # Locks are released here, not in the generator, so a visitor
            # closing the tab mid-answer never leaks them.
            try:
                answer = session.agent.chat(message.strip())
                events.put({"type": "answer", "text": answer or ""})
            except Exception as e:
                events.put({"type": "error", "message": f"Something went wrong: {e}"})
            finally:
                session.turns += 1
                if unsub:
                    unsub()
                session.lock.release()
                self._turns.release()
                events.put(_DONE)

        threading.Thread(target=_work, daemon=True).start()
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is _DONE:
                break
            yield _sse(item)
        yield _sse({"type": "done"})

    def reset(self, session_id: str) -> bool:
        return self.sessions.drop(session_id)


def create_app(backend: DemoBackend):
    """The FastAPI app around a DemoBackend."""
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="OpenBro", version=__version__, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def client_ip(request: Request) -> str:
        if backend.settings.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/api/info")
    def info():
        return backend.info()

    @app.post("/api/chat")
    async def chat(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = str(body.get("message") or "")
        session_id = body.get("session_id") or None
        refused = backend.check(message, client_ip(request))
        if refused:
            status, reason = refused
            return JSONResponse({"error": reason}, status_code=status)
        return StreamingResponse(
            backend.run_turn(message, session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/reset")
    async def reset(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        sid = body.get("session_id")
        return {"reset": bool(sid and backend.reset(str(sid)))}

    return app


def warm_up(docs_service) -> threading.Thread | None:
    """Load the embedding model in the background as the server starts.

    On a small instance (0.1 CPU) the first load takes ~15 s; doing it
    now means the first visitor's search doesn't wait for it."""
    embedder = getattr(docs_service, "embedder", None)
    if embedder is None:
        return None

    def _run() -> None:
        try:
            embedder.embed_query("warm up")
        except Exception:
            pass  # search falls back to keywords; nothing to do here

    thread = threading.Thread(target=_run, name="openbro-warmup", daemon=True)
    thread.start()
    return thread


def build_backend(settings: WebSettings, docs: list[Path] | None, data_dir: Path) -> DemoBackend:
    """The real backend: demo provider, demo docs index, demo agents."""
    from openbro.web.demo import (
        build_demo_agent,
        build_demo_docs_service,
        build_demo_provider,
        build_demo_registry,
        default_demo_docs,
    )

    provider = build_demo_provider()
    sources = docs if docs is not None else default_demo_docs()
    docs_service = build_demo_docs_service(data_dir, sources) if sources else None
    # Paths in results are shown relative to the documents' common folder.
    docs_root = None
    if sources:
        docs_root = Path(os.path.commonpath([str(p.resolve().parent) for p in sources]))
    tool_names = build_demo_registry(docs_service).list_tools()
    warm_up(docs_service)

    def factory(sid: str, bus: ActivityBus):
        return build_demo_agent(
            sid,
            provider,
            bus,
            docs_service=docs_service,
            max_steps=settings.max_steps,
            docs_root=docs_root,
        )

    return DemoBackend(settings, factory, model_name=provider.name(), tool_names=tool_names)
