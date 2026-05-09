"""MCP — Model Context Protocol integration.

OpenBro speaks MCP (https://modelcontextprotocol.io/) two ways:

1. **MCP Client** — connects to any MCP server (filesystem, github, custom).
   The remote server's tools register into OpenBro's tool registry like
   built-ins. Configured under `mcp.servers` in config.yaml.

2. **MCP Server** — exposes OpenBro's tools / skills / memory to other
   MCP clients (Claude Desktop, Cursor, etc.). Run with:
       openbro --mcp-server

The transport is JSON-RPC 2.0 over stdio (the default MCP wire format).
We implement enough of the spec to be useful without pulling in a heavy
dependency: initialize, tools/list, tools/call.
"""

from openbro.mcp.client import MCPClient, MCPServerConfig
from openbro.mcp.server import OpenBroMCPServer, run_mcp_server

__all__ = [
    "MCPClient",
    "MCPServerConfig",
    "OpenBroMCPServer",
    "run_mcp_server",
]
