"""MCP Server — exposes OpenBro's tools to other MCP clients.

Run with `openbro --mcp-server`. Connects on stdio (the standard MCP
transport for desktop apps like Claude Desktop, Cursor, etc).

Other AI clients can then invoke OpenBro's 18 built-in tools (file_ops,
app, browser, word, excel, cli_agent, etc.) over MCP. Effectively this
turns OpenBro into a tool provider for any MCP-aware agent.

Wire format: JSON-RPC 2.0, one message per line, stdio.
"""

from __future__ import annotations

import json
import sys
from typing import IO

PROTOCOL_VERSION = "2024-11-05"


class OpenBroMCPServer:
    """Minimal MCP server that surfaces OpenBro's tool registry."""

    def __init__(self, registry, stdin: IO | None = None, stdout: IO | None = None):
        self.registry = registry
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout

    # ─── transport ──────────────────────────────────────────────

    def _send(self, message: dict) -> None:
        self.stdout.write(json.dumps(message) + "\n")
        self.stdout.flush()

    def _send_result(self, req_id, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _send_error(self, req_id, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": code, "message": message},
            }
        )

    # ─── handlers ───────────────────────────────────────────────

    def handle(self, msg: dict) -> None:
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params", {}) or {}

        if method == "initialize":
            self._handle_initialize(req_id, params)
        elif method == "notifications/initialized":
            return  # nothing to do
        elif method == "tools/list":
            self._handle_tools_list(req_id)
        elif method == "tools/call":
            self._handle_tools_call(req_id, params)
        elif method == "ping":
            self._send_result(req_id, {})
        elif req_id is not None:
            self._send_error(req_id, -32601, f"Method not found: {method}")

    def _handle_initialize(self, req_id, params: dict) -> None:
        self._send_result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "openbro", "version": "1.0.0"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )

    def _handle_tools_list(self, req_id) -> None:
        tools = []
        for schema in self.registry.get_tools_schema():
            tools.append(
                {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "inputSchema": schema.get("parameters", {}),
                }
            )
        self._send_result(req_id, {"tools": tools})

    def _handle_tools_call(self, req_id, params: dict) -> None:
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        if not name:
            self._send_error(req_id, -32602, "Missing 'name' in tools/call")
            return
        try:
            output = self.registry.execute(name, args, confirmed=True)
        except Exception as e:
            self._send_error(req_id, -32603, f"Tool execution error: {e}")
            return
        self._send_result(
            req_id,
            {
                "content": [{"type": "text", "text": str(output)}],
                "isError": False,
            },
        )

    # ─── main loop ──────────────────────────────────────────────

    def serve_forever(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self.handle(msg)
            except Exception as e:
                # Don't crash the server on a single bad request
                req_id = msg.get("id") if isinstance(msg, dict) else None
                if req_id is not None:
                    self._send_error(req_id, -32603, f"Internal error: {e}")


def run_mcp_server() -> None:
    """CLI entry point — `openbro --mcp-server`."""
    from openbro.tools.registry import ToolRegistry
    from openbro.utils.config import load_config

    cfg = load_config()
    registry = ToolRegistry(config=cfg)
    server = OpenBroMCPServer(registry)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
