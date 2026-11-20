"""MCP real-chain tests against the bundled example stdio server.

Covers the full lifecycle: handshake state machine (operations before
initialize are refused), initialize result, notifications/initialized,
paginated tools/list, tools/call (add + echo), isError propagation,
Tool Registry namespaced registration + permission enforcement, and
session close via stdin EOF.
"""
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.mcp import service
from app.mcp.client import MCPError, StdioMCPSession, result_text
from app.tools.registry import execute_tool, ToolError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SERVER = os.path.join(REPO_ROOT, "examples", "mcp-server", "server.py")
CMD = f"{sys.executable} {SERVER}"


@pytest.fixture()
def session():
    s = StdioMCPSession(CMD)
    s.start()
    yield s
    s.close()


def test_handshake_required_before_operations(session):
    with pytest.raises(MCPError):
        session.list_tools()
    with pytest.raises(MCPError):
        session.call_tool("echo", {"text": "x"})


def test_full_lifecycle_list_and_call(session):
    info = session.initialize()
    assert info["protocolVersion"] == "2024-11-05"
    assert info["serverInfo"]["name"] == "agentflow-example-mcp"
    assert session.state == "ready"
    tools = session.list_tools()  # server paginates with page size 1
    names = [t["name"] for t in tools]
    assert names == ["echo", "add"], names
    assert all("inputSchema" in t for t in tools)
    assert session.list_tools() is tools  # cached
    r = session.call_tool("add", {"a": 2, "b": 3})
    assert r["isError"] is False and result_text(r) == "5"
    r = session.call_tool("echo", {"text": "hello mcp"})
    assert result_text(r) == "hello mcp"
    r = session.call_tool("nope", {})
    assert r["isError"] is True


def test_close_ends_process(session):
    session.initialize()
    pid = session.proc.pid
    session.close()
    assert session.state == "closed"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_registry_bridge_and_permissions(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "mcp.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    srv = models.MCPServer(name="example", transport="stdio", command_or_url=CMD)
    db.add(srv); db.commit(); db.refresh(srv)
    try:
        out = service.connect(db, srv.id)
        assert out["registered_tool_ids"] == [f"mcp.{srv.id}.echo", f"mcp.{srv.id}.add"]
        # agent WITH the namespaced tool in its list can call through the registry
        res = execute_tool(f"mcp.{srv.id}.add", {"a": 10, "b": 7},
                           [f"mcp.{srv.id}.add"])
        assert res["text"] == "17" and res["isError"] is False
        # agent WITHOUT it is refused by the standard permission check
        with pytest.raises(ToolError):
            execute_tool(f"mcp.{srv.id}.add", {"a": 1, "b": 1}, ["file_read"])
    finally:
        service.disconnect(srv.id)
        db.close()
    # after disconnect the namespaced tools are gone from the registry
    with pytest.raises(ToolError):
        execute_tool(f"mcp.{srv.id}.add", {"a": 1, "b": 1}, [f"mcp.{srv.id}.add"])
