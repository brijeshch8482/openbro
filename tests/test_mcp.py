"""Tests for MCP client + server (in-memory transports)."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from openbro.mcp.client import MCPClient, MCPServerConfig, _MCPTool, register_mcp_tools
from openbro.mcp.server import OpenBroMCPServer

# ─── MCPServerConfig ────────────────────────────────────────────


def test_server_config_defaults():
    cfg = MCPServerConfig(name="fs", command=["mcp-server-fs", "/data"])
    assert cfg.name == "fs"
    assert cfg.command == ["mcp-server-fs", "/data"]
    assert cfg.enabled is True
    assert cfg.env == {}
    assert cfg.cwd is None


# ─── MCPClient connect / shutdown ───────────────────────────────


def test_client_connect_with_missing_binary_returns_false():
    cfg = MCPServerConfig(name="ghost", command=["does-not-exist-binary-xyz"])
    client = MCPClient(cfg)
    assert client.connect(timeout=1.0) is False


def test_client_shutdown_safe_when_not_connected():
    cfg = MCPServerConfig(name="x", command=["x"])
    client = MCPClient(cfg)
    # Should not raise
    client.shutdown()


# ─── Tool wrapping ──────────────────────────────────────────────


def test_mcp_tool_schema_uses_input_schema():
    cfg = MCPServerConfig(name="srv", command=["x"])
    client = MCPClient(cfg)
    raw = {
        "name": "list_files",
        "description": "list files in a dir",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    }
    tool = _MCPTool(client, raw)
    assert tool.name == "mcp_srv_list_files"
    schema = tool.schema()
    assert schema["name"] == "mcp_srv_list_files"
    assert "description" in schema
    assert schema["parameters"]["properties"]["path"]["type"] == "string"


def test_mcp_tool_run_calls_call_tool():
    cfg = MCPServerConfig(name="srv", command=["x"])
    client = MCPClient(cfg)
    client.call_tool = MagicMock(return_value="42 files")
    raw = {"name": "list_files", "description": "ls"}
    tool = _MCPTool(client, raw)
    out = tool.run(path="/tmp")
    client.call_tool.assert_called_once_with("list_files", {"path": "/tmp"})
    assert out == "42 files"


# ─── register_mcp_tools ─────────────────────────────────────────


def test_register_mcp_tools_handles_failed_connect():
    cfg = MCPServerConfig(name="ghost", command=["does-not-exist-xyz"])
    fake_registry = MagicMock()
    fake_registry._tools = {}
    summary = register_mcp_tools([cfg], fake_registry)
    assert "ghost" in summary["failed"]
    assert summary["tools_added"] == 0


def test_register_mcp_tools_skips_disabled():
    cfg = MCPServerConfig(name="off", command=["x"], enabled=False)
    fake_registry = MagicMock()
    fake_registry._tools = {}
    summary = register_mcp_tools([cfg], fake_registry)
    assert "off" not in summary["failed"]
    assert "off" not in summary["connected"]


def test_register_mcp_tools_injects_into_registry():
    """When connection succeeds, tools should appear in the registry."""
    cfg = MCPServerConfig(name="srv", command=["x"])
    fake_registry = MagicMock()
    fake_registry._tools = {}

    fake_client = MagicMock()
    fake_client.connect.return_value = True
    fake_client.list_tools.return_value = [
        {"name": "t1", "description": "d1", "inputSchema": {}},
        {"name": "t2", "description": "d2", "inputSchema": {}},
    ]
    fake_client.server = cfg

    with patch("openbro.mcp.client.MCPClient", return_value=fake_client):
        summary = register_mcp_tools([cfg], fake_registry)

    assert summary["tools_added"] == 2
    assert "srv" in summary["connected"]
    assert "mcp_srv_t1" in fake_registry._tools
    assert "mcp_srv_t2" in fake_registry._tools


# ─── OpenBroMCPServer (server side) ─────────────────────────────


@pytest.fixture
def fake_registry():
    """A minimal ToolRegistry stub that returns one tool."""
    reg = MagicMock()
    reg.get_tools_schema.return_value = [
        {
            "name": "datetime",
            "description": "current time",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    reg.execute = MagicMock(return_value="2026-05-09T08:00:00")
    return reg


def _make_server(registry, request_text: str):
    """Helper: build a server with stdin holding one JSON-RPC request and a fresh stdout buffer."""
    stdin = io.StringIO(request_text)
    stdout = io.StringIO()
    return OpenBroMCPServer(registry, stdin=stdin, stdout=stdout), stdout


def test_server_initialize_returns_capabilities(fake_registry):
    srv, out = _make_server(
        fake_registry,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "openbro"
    assert "tools" in response["result"]["capabilities"]


def test_server_tools_list_returns_registry_tools(fake_registry):
    srv, out = _make_server(
        fake_registry,
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    tools = response["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "datetime"
    assert "inputSchema" in tools[0]


def test_server_tools_call_executes_and_returns_text(fake_registry):
    srv, out = _make_server(
        fake_registry,
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "datetime", "arguments": {"action": "now"}},
            }
        )
        + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    assert "result" in response
    assert response["result"]["content"][0]["type"] == "text"
    assert "2026-05-09" in response["result"]["content"][0]["text"]
    fake_registry.execute.assert_called_once_with("datetime", {"action": "now"}, confirmed=True)


def test_server_unknown_method_returns_error(fake_registry):
    srv, out = _make_server(
        fake_registry,
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "unknown/method"}) + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    assert "error" in response
    assert response["error"]["code"] == -32601


def test_server_tools_call_missing_name_returns_error(fake_registry):
    srv, out = _make_server(
        fake_registry,
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {}}) + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    assert "error" in response
    assert response["error"]["code"] == -32602


def test_server_tool_execution_error_returns_error(fake_registry):
    fake_registry.execute.side_effect = RuntimeError("boom")
    srv, out = _make_server(
        fake_registry,
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "datetime", "arguments": {}},
            }
        )
        + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    assert "error" in response
    assert "boom" in response["error"]["message"]


def test_server_handles_malformed_json_gracefully(fake_registry):
    srv, out = _make_server(fake_registry, "not valid json {{{\n")
    srv.serve_forever()
    # No response is OK — server just skipped the bad line
    assert out.getvalue() == ""


def test_server_initialized_notification_is_silent(fake_registry):
    """notifications/initialized has no id and no response."""
    srv, out = _make_server(
        fake_registry,
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n",
    )
    srv.serve_forever()
    assert out.getvalue() == ""


def test_server_ping_returns_empty_result(fake_registry):
    srv, out = _make_server(
        fake_registry,
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n",
    )
    srv.serve_forever()
    response = json.loads(out.getvalue().strip())
    assert response["result"] == {}
