"""Disposable loopback fixture for the native MCP client. Standard-library only."""

import json
import sys
from pathlib import Path

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    method = request["method"]
    if method == "initialize":
        reply["result"] = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture", "version": "1"},
        }
    elif method == "tools/list":
        reply["result"] = {
            "tools": [
                {
                    "name": "stamp",
                    "description": "Write the test marker",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
    elif method == "tools/call":
        with Path("mcp-count.txt").open("a") as stream:
            stream.write("x")
        reply["result"] = {"content": [{"type": "text", "text": "MCP-SESSION-MARK-42"}]}
    else:
        reply["error"] = {"code": -32601, "message": "method unavailable"}
    print(json.dumps(reply), flush=True)
