#!/usr/bin/env python3
"""Minimal example MCP server (stdio, newline-delimited JSON-RPC).

Original implementation written for AgentFlow OS using only the Python
standard library. Implements the MCP lifecycle surface the client needs:
initialize -> notifications/initialized -> tools/list (paginated,
page size 1 so pagination is genuinely exercised) -> tools/call
(content blocks + isError). All diagnostics go to stderr; stdout carries
JSON-RPC only. Closing stdin ends the server.

Tools:
  echo  {text}      -> returns the text
  add   {a, b}      -> returns a + b
"""
import json
import sys

PROTOCOL_VERSION = "2024-11-05"
PAGE_SIZE = 1

TOOLS = [
    {"name": "echo", "description": "Echo back the given text",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "add", "description": "Add two numbers",
     "inputSchema": {"type": "object",
                     "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                     "required": ["a", "b"]}},
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def result(rid, value):
    send({"jsonrpc": "2.0", "id": rid, "result": value})


def error(rid, code, message):
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def handle_tools_list(rid, params):
    cursor = (params or {}).get("cursor")
    start = int(cursor) if cursor else 0
    page = TOOLS[start:start + PAGE_SIZE]
    out = {"tools": page}
    if start + PAGE_SIZE < len(TOOLS):
        out["nextCursor"] = str(start + PAGE_SIZE)
    result(rid, out)


def handle_tools_call(rid, params):
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}
    if name == "echo":
        result(rid, {"content": [{"type": "text", "text": str(args.get("text", ""))}],
                     "isError": False})
    elif name == "add":
        try:
            total = float(args["a"]) + float(args["b"])
            text = str(int(total)) if total == int(total) else str(total)
            result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:
            result(rid, {"content": [{"type": "text", "text": f"add failed: {e}"}],
                         "isError": True})
    else:
        result(rid, {"content": [{"type": "text", "text": f"unknown tool: {name}"}],
                     "isError": True})


def main():
    initialized = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            print("bad json line ignored", file=sys.stderr)
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            result(rid, {"protocolVersion": PROTOCOL_VERSION,
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "agentflow-example-mcp",
                                        "version": "1.0.0"}})
        elif method == "notifications/initialized":
            initialized = True  # notification: no response
        elif rid is None:
            print(f"notification ignored: {method}", file=sys.stderr)
        elif not initialized and method != "ping":
            error(rid, -32002, "server not initialized")
        elif method == "ping":
            result(rid, {})
        elif method == "tools/list":
            handle_tools_list(rid, msg.get("params"))
        elif method == "tools/call":
            handle_tools_call(rid, msg.get("params"))
        else:
            error(rid, -32601, f"method not found: {method}")
    print("stdin closed, exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
