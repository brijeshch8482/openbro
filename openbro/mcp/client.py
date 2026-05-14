"""MCP Client — connects to external MCP servers, registers their tools.

The MCP protocol is JSON-RPC 2.0 over stdio (or SSE / websocket; we focus
on stdio since that's what most MCP servers ship with).

Lifecycle:
  1. Spawn the MCP server subprocess
  2. Send `initialize` request
  3. Send `tools/list` to discover the server's tools
  4. Wrap each remote tool as a BaseTool and inject into OpenBro's registry
  5. On call, forward to `tools/call` JSON-RPC

Public API:
    cfg = MCPServerConfig(name="fs", command=["mcp-server-filesystem", "/data"])
    client = MCPClient(cfg)
    client.connect()
    for tool in client.list_tools():
        # ... register into our tool registry ...
    client.shutdown()
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from queue import Empty, Queue

from openbro.tools.base import BaseTool, RiskLevel

PROTOCOL_VERSION = "2024-11-05"


@dataclass
class MCPServerConfig:
    """One entry in config.mcp.servers — describes how to launch an MCP server."""

    name: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPClient:
    """Stdio JSON-RPC client for a single MCP server."""

    def __init__(self, server: MCPServerConfig):
        self.server = server
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._pending: dict[int, Queue] = {}
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._tools_cache: list[dict] | None = None

    # ─── lifecycle ────────────────────────────────────────────────

    def connect(self, timeout: float = 5.0) -> bool:
        """Spawn the server, send initialize, wait for ack."""
        try:
            self._proc = subprocess.Popen(
                self.server.command,
                cwd=self.server.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env={**(self.server.env or {})} if self.server.env else None,
            )
        except (FileNotFoundError, OSError):
            return False

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        try:
            init_response = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "openbro", "version": "1.0.0"},
                },
                timeout=timeout,
            )
        except (TimeoutError, OSError):
            self.shutdown()
            return False

        if not init_response or "error" in init_response:
            self.shutdown()
            return False

        # Per spec, send `notifications/initialized` after init
        self._notify("notifications/initialized", {})
        return True

    def shutdown(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    # ─── public API ──────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """Return the server's tool schemas. Cached after first call."""
        if self._tools_cache is not None:
            return self._tools_cache
        try:
            resp = self._request("tools/list", {}, timeout=5.0)
        except (TimeoutError, OSError):
            return []
        result = (resp or {}).get("result", {})
        self._tools_cache = result.get("tools", [])
        return self._tools_cache

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Invoke a remote tool. Returns its text content (joined)."""
        try:
            resp = self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=60,
            )
        except TimeoutError:
            return f"MCP call to '{tool_name}' timed out."
        except OSError as e:
            return f"MCP call failed: {e}"
        if not resp:
            return "No response from MCP server."
        if "error" in resp:
            return f"MCP error: {resp['error'].get('message', resp['error'])}"
        result = resp.get("result", {})
        content = result.get("content", [])
        # MCP returns a list of content items; we flatten the text ones
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts) if texts else json.dumps(result)

    # ─── transport ───────────────────────────────────────────────

    def _request(self, method: str, params: dict, timeout: float = 10.0) -> dict | None:
        """Send a JSON-RPC request and wait for the matching response."""
        if not self._proc or not self._proc.stdin:
            raise OSError("MCP server not connected")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
        q: Queue = Queue()
        self._pending[req_id] = q
        envelope = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        self._proc.stdin.write(json.dumps(envelope) + "\n")
        self._proc.stdin.flush()
        try:
            return q.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError(f"MCP request '{method}' timed out") from exc
        finally:
            self._pending.pop(req_id, None)

    def _notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._proc or not self._proc.stdin:
            return
        envelope = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._proc.stdin.write(json.dumps(envelope) + "\n")
            self._proc.stdin.flush()
        except Exception:
            pass

    def _read_loop(self) -> None:
        """Read JSON-RPC frames from server stdout, dispatch to pending queues."""
        if not self._proc or not self._proc.stdout:
            return
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            req_id = msg.get("id")
            if req_id is not None and req_id in self._pending:
                self._pending[req_id].put(msg)
            # Notifications + server-side requests: ignored for now (we're
            # a minimal client). Future: handle resources/notify, prompts/list.


# ─── tool wrapper ────────────────────────────────────────────────


class _MCPTool(BaseTool):
    """Wraps an MCP-server tool as a BaseTool so the agent can invoke it
    through the regular tool registry."""

    def __init__(self, client: MCPClient, raw_tool: dict):
        self.name = f"mcp_{client.server.name}_{raw_tool.get('name', 'tool')}"
        self.description = raw_tool.get("description", "")
        self.risk = RiskLevel.MODERATE  # MCP tools touch external resources
        self._client = client
        self._raw = raw_tool

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._raw.get("inputSchema") or {"type": "object", "properties": {}},
        }

    def run(self, **kwargs) -> str:
        return self._client.call_tool(self._raw.get("name", ""), kwargs)


def register_mcp_tools(servers: list[MCPServerConfig], registry) -> dict:
    """Connect to every configured MCP server and inject its tools into
    the OpenBro tool registry. Returns a summary dict for diagnostics.

    Servers with declared-but-empty env vars (e.g. github with no token) are
    silently deferred — they show up in summary['pending_creds'] so the UI
    can surface a hint. The user can populate the missing value later via
    `openbro mcp creds <server>` and a subsequent restart picks it up; the
    server never crashes on startup just because a credential is missing.
    """
    summary = {
        "connected": [],
        "failed": [],
        "pending_creds": [],
        "tools_added": 0,
    }
    for cfg in servers:
        if not cfg.enabled:
            continue
        if cfg.env:
            missing = [k for k, v in cfg.env.items() if not v]
            if missing:
                summary["pending_creds"].append({"server": cfg.name, "missing": missing})
                continue
        client = MCPClient(cfg)
        if not client.connect(timeout=5.0):
            summary["failed"].append(cfg.name)
            continue
        try:
            tools = client.list_tools()
        except Exception:
            tools = []
        for t in tools:
            try:
                wrapped = _MCPTool(client, t)
                # Inject via private dict — registry.execute() looks up here
                registry._tools[wrapped.name] = wrapped
                summary["tools_added"] += 1
            except Exception:
                continue
        summary["connected"].append(cfg.name)
    return summary
