"""`openbro web` — the public demo: safety, limits, streaming, HTTP."""

import json
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest

from openbro.core.activity import ActivityBus
from openbro.core.permissions import PermissionRequest
from openbro.llm.base import LLMResponse
from openbro.web import demo
from openbro.web.server import DemoBackend, bus_to_event, create_app
from openbro.web.sessions import RateLimiter, SessionStore, WebSettings


def _call(cid, name, args):
    return {"id": cid, "function": {"name": name, "arguments": args}}


def _provider(responses):
    p = MagicMock()
    p.name.return_value = "fake/model"
    p.supports_tools.return_value = True
    p.chat.side_effect = list(responses)
    return p


def _frames(chunks):
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


# ─── public-address guard ────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/x",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "not a url",
    ],
)
def test_internal_or_odd_urls_are_not_public(url):
    assert demo.is_public_url(url) is False


def test_public_url_checks_every_resolved_address(monkeypatch):
    def fake_resolve(host, *a, **kw):
        addrs = {"good.test": ["93.184.216.34"], "mixed.test": ["93.184.216.34", "10.0.0.1"]}
        return [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in addrs[host]]

    monkeypatch.setattr(demo.socket, "getaddrinfo", fake_resolve)
    assert demo.is_public_url("https://good.test/page") is True
    assert demo.is_public_url("https://mixed.test/page") is False


def test_fetch_blocks_redirect_to_internal_address(monkeypatch):
    monkeypatch.setattr(demo, "is_public_url", lambda u: "127.0.0.1" not in u)
    hop = MagicMock(is_redirect=True, headers={"location": "http://127.0.0.1/admin"})
    get = MagicMock(return_value=hop)
    monkeypatch.setattr(demo.httpx, "get", get)
    out = demo.PublicWebTool()._fetch("https://public.test/")
    assert "not a public internet address" in out
    assert get.call_count == 1  # never requested the internal hop


# ─── gate, tools, agent isolation ────────────────────────────────────


def test_gate_runs_only_safe_calls():
    gate = demo.DemoGate()
    assert gate.request(PermissionRequest(tool="web", args={}, risk="safe")) is True
    assert gate.request(PermissionRequest(tool="docs", args={}, risk="moderate")) is False
    assert gate.request(PermissionRequest(tool="x", args={}, risk="dangerous")) is False


def test_demo_registry_has_only_demo_tools():
    assert set(demo.build_demo_registry().list_tools()) == {"web", "datetime"}
    with_docs = demo.build_demo_registry(docs_service=MagicMock())
    assert set(with_docs.list_tools()) == {"web", "datetime", "docs"}
    assert isinstance(with_docs.get_tool("web"), demo.PublicWebTool)
    assert with_docs.get_risk("docs", {"action": "add", "path": "x"}) != "safe"


def test_demo_agent_has_no_host_context():
    provider = _provider([LLMResponse(content="Hi! I'm OpenBro.")])
    bus = ActivityBus()
    agent = demo.build_demo_agent("s1", provider, bus)
    assert agent.bus is bus
    assert agent.playbooks_enabled is False
    assert not agent.hooks.hooks
    system = agent.history[0].content
    assert "public demo" in system
    assert "USER ENVIRONMENT" not in system and "drives_available" not in system
    assert agent.chat("hello") == "Hi! I'm OpenBro."
    sent = provider.chat.call_args[0][0]
    last_user = sent[-1].content
    assert "Workspace" not in last_user and "cwd" not in last_user.lower()


def test_demo_agent_cannot_reach_other_tools():
    provider = _provider(
        [
            LLMResponse(content="", tool_calls=[_call("c1", "shell", {"command": "whoami"})]),
            LLMResponse(content="", tool_calls=[_call("c2", "docs", {"action": "remove"})]),
            LLMResponse(content="done"),
        ]
    )
    docs = MagicMock()
    agent = demo.build_demo_agent("s2", provider, ActivityBus(), docs_service=docs)
    assert agent.chat("try things") == "done"
    tool_msgs = [m.content for m in agent.history if m.role == "tool"]
    assert "Unknown tool" in tool_msgs[0] or "not" in tool_msgs[0].lower()
    assert "DENIED" in tool_msgs[1]
    docs.remove.assert_not_called()


# ─── limits ──────────────────────────────────────────────────────────


def test_rate_limiter_window_per_key_and_global():
    now = [1000.0]
    rl = RateLimiter(per_key=2, global_limit=3, clock=lambda: now[0])
    assert rl.check("a")[0] and rl.check("a")[0]
    ok, retry = rl.check("a")
    assert not ok and 0 < retry <= 3601
    assert rl.check("b")[0]
    assert not rl.check("c")[0]  # global cap of 3 reached
    now[0] += 3601
    assert rl.check("a")[0]


def test_session_store_expires_and_caps():
    now = [0.0]
    store = SessionStore(lambda sid: object(), ttl_seconds=10, max_sessions=2, clock=lambda: now[0])
    a = store.get_or_create(None)
    assert store.get_or_create(a.id) is a
    store.get_or_create(None)
    store.get_or_create(None)  # over the cap: oldest goes
    assert len(store) == 2
    now[0] += 11
    fresh = store.get_or_create(a.id)
    assert fresh.id != a.id and len(store) == 1


# ─── backend streaming ───────────────────────────────────────────────


class _ScriptedAgent:
    def __init__(self, bus, answer="All good.", delay=0.0):
        self.bus = bus
        self.answer = answer
        self.delay = delay
        self.messages = []

    def chat(self, message):
        self.messages.append(message)
        self.bus.emit("llm_start", "calling LLM", step=1)
        args = {"action": "search", "query": "q"}
        self.bus.emit("tool_start", "web (safe)", tool="web", args=args)
        time.sleep(self.delay)
        self.bus.emit("tool_end", "res", tool="web", ok=True, elapsed=0.25, preview="3 results")
        return self.answer


def _backend(settings=None, delay=0.0):
    agents = {}

    def factory(sid, bus):
        agents[sid] = _ScriptedAgent(bus, delay=delay)
        return agents[sid]

    return DemoBackend(settings or WebSettings(), factory, "fake/model", ["web"]), agents


def test_run_turn_streams_steps_then_answer():
    backend, agents = _backend()
    frames = _frames(backend.run_turn("hello", None))
    kinds = [f["type"] for f in frames]
    assert kinds[0] == "session" and kinds[-1] == "done"
    assert kinds[1:-1] == ["thinking", "tool", "tool", "answer"]
    assert frames[2]["detail"] == "search · q"
    assert frames[3] == {
        "type": "tool",
        "phase": "end",
        "tool": "web",
        "ok": True,
        "elapsed": 0.25,
        "preview": "3 results",
    }
    assert frames[-2]["text"] == "All good."
    sid = frames[0]["id"]
    _frames(backend.run_turn("again", sid))
    assert agents[sid].messages == ["hello", "again"]  # same visitor, same agent


def test_sessions_never_share_events():
    backend, _ = _backend(delay=0.2)
    results = {}

    def run(name):
        results[name] = _frames(backend.run_turn(name, None))

    threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for frames in results.values():
        assert [f["type"] for f in frames].count("tool") == 2


def test_check_refuses_empty_long_and_rate_limited():
    backend, _ = _backend(WebSettings(max_message_chars=10, per_ip_per_hour=1))
    assert backend.check("  ", "1.1.1.1")[0] == 400
    assert backend.check("x" * 11, "1.1.1.1")[0] == 413
    assert backend.check("hi", "1.1.1.1") is None
    status, reason = backend.check("hi", "1.1.1.1")
    assert status == 429 and "limit" in reason


def test_turn_cap_and_reset():
    backend, _ = _backend(WebSettings(max_turns_per_session=1))
    sid = _frames(backend.run_turn("one", None))[0]["id"]
    frames = _frames(backend.run_turn("two", sid))
    assert any(f["type"] == "error" and "limit" in f["message"] for f in frames)
    assert backend.reset(sid) is True
    assert backend.reset(sid) is False


def test_busy_when_all_turn_slots_taken():
    backend, _ = _backend(WebSettings(max_concurrent_turns=1), delay=0.3)
    first = []
    t = threading.Thread(target=lambda: first.extend(_frames(backend.run_turn("a", None))))
    t.start()
    time.sleep(0.1)
    frames = _frames(backend.run_turn("b", None))
    t.join()
    assert frames[0]["type"] == "error" and "busy" in frames[0]["message"]
    assert first[-2]["type"] == "answer"
    # the slot is free again afterwards
    assert _frames(backend.run_turn("c", None))[-2]["type"] == "answer"


def test_bus_to_event_ignores_other_kinds():
    ev = MagicMock(kind="system", meta={}, text="x")
    assert bus_to_event(ev) is None


# ─── HTTP ────────────────────────────────────────────────────────────


def test_http_page_info_chat_and_limits():
    from fastapi.testclient import TestClient

    backend, _ = _backend(WebSettings(per_ip_per_hour=1))
    client = TestClient(create_app(backend))

    page = client.get("/")
    assert page.status_code == 200 and "OpenBro" in page.text
    assert client.get("/static/app.js").status_code == 200
    info = client.get("/api/info").json()
    assert info["model"] == "fake/model" and info["suggestions"]

    res = client.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = _frames([res.text])
    assert frames[-2] == {"type": "answer", "text": "All good."}

    limited = client.post("/api/chat", json={"message": "again"})
    assert limited.status_code == 429 and "limit" in limited.json()["error"]
    assert client.post("/api/reset", json={"session_id": frames[0]["id"]}).json() == {"reset": True}
    assert client.get("/healthz").json()["ok"] is True


# ─── Groq failover (the demo's free provider) ────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        "Groq 404: The model `llama-3.3-70b-versatile` does not exist",
        "Groq 400: Tool call validation failed: attempted to call tool 'docs<|channel|>x'",
        "Groq 429: rate_limit_exceeded",
    ],
)
def test_groq_moves_to_next_model(monkeypatch, error):
    from openbro.llm.groq_provider import GroqProvider

    provider = GroqProvider("key", model="llama-3.3-70b-versatile")
    tried = []

    def fake(model, messages, tools):
        tried.append(model)
        if len(tried) == 1:
            raise RuntimeError(error)
        return LLMResponse(content=f"answered by {model}")

    monkeypatch.setattr(provider, "_chat_with_model", fake)
    assert provider.chat([], None).content == "answered by openai/gpt-oss-120b"
    assert tried == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]


def test_demo_docs_tool_limits_actions_and_output():
    svc = MagicMock()
    svc.search.return_value = []
    svc.status.return_value = {"documents": 2}
    tool = demo.DemoDocsTool(service=svc)
    assert "not available" in tool.run(action="add", path="C:/secret")
    svc.add.assert_not_called()
    tool.run(action="search", query="x", k=50)
    assert svc.search.call_args.kwargs["k"] == demo.DemoDocsTool.MAX_HITS
    assert len(demo._cap("y" * 5000, 100)) < 200


def test_empty_final_reply_gets_one_nudge():
    provider = _provider(
        [
            LLMResponse(content="", tool_calls=[_call("c1", "datetime", {"action": "now"})]),
            LLMResponse(content=""),  # reasoning-only reply, nothing visible
            LLMResponse(content="It is 9 AM."),
        ]
    )
    agent = demo.build_demo_agent("s3", provider, ActivityBus())
    assert agent.chat("time?") == "It is 9 AM."
    assert provider.chat.call_count == 3


def test_prepare_indexes_given_paths(monkeypatch, tmp_path, capsys):
    from openbro.web import prepare

    seen = {}

    def fake_build(data_dir, paths):
        seen["args"] = (data_dir, paths)
        svc = MagicMock()
        svc.status.return_value = {"documents": 4}
        svc.mode.return_value = "hybrid"
        return svc

    monkeypatch.setattr(demo, "build_demo_docs_service", fake_build)
    doc = tmp_path / "README.md"
    doc.write_text("# hi", encoding="utf-8")
    assert prepare.main(["--data-dir", str(tmp_path / "data"), str(doc)]) == 0
    assert seen["args"] == (tmp_path / "data", [doc])
    assert "Indexed 4 documents" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        prepare.main(["--data-dir", str(tmp_path), str(tmp_path / "missing")])


def test_demo_embed_model_from_environment(monkeypatch):
    monkeypatch.delenv("OPENBRO_WEB_EMBED_MODEL", raising=False)
    assert demo.demo_rag_config() == {}
    monkeypatch.setenv("OPENBRO_WEB_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    assert demo.demo_rag_config() == {"embed_model": "BAAI/bge-small-en-v1.5"}
    monkeypatch.setenv("OPENBRO_WEB_EMBED_MODEL", "none")
    assert demo.demo_rag_config() == {"embedder": "none"}


def test_warm_up_loads_embedder_in_background():
    from openbro.web.server import warm_up

    assert warm_up(None) is None
    assert warm_up(MagicMock(embedder=None)) is None
    svc = MagicMock()
    warm_up(svc).join(timeout=5)
    svc.embedder.embed_query.assert_called_once()
    svc.embedder.embed_query.side_effect = RuntimeError("no model")
    warm_up(svc).join(timeout=5)  # errors are swallowed
