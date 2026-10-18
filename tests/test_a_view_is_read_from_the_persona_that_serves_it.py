"""A content type's view and the tools it calls are reached on the persona that serves it, by name.

2026-10-18: the client resolved `seraph` to an MCP-server artifact and dispatched `resources_read`
on it. No such artifact exists and the op named a removed Mantle primitive, so every `vnd.agience.*`
viewer said its server was not running. `/personas/{slug}/...` reaches the persona through the
gateway's own `slug -> endpoint` map.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from crystal.main import app
from crystal.personas import PersonaGateway
from crystal.topology import Topology

AUTH = {"Authorization": "Bearer user-token"}
VIEW = "ui://seraph/vnd.agience.key.html"


class FakeMcp:
    """Stands in for `McpCaller.request`: records each call and answers like a persona."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def request(self, endpoint, method, params, *, token=None):
        self.calls.append((endpoint, method, params, token))
        if self.fail:
            raise self.fail
        if method == "resources/read":
            return {"contents": [{"uri": params["uri"], "mimeType": "text/html;profile=mcp-app",
                                  "text": "<html>key</html>"}]}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "called " + params["name"]}]}
        if method == "tools/list":
            return {"tools": [{"name": "create_api_key", "description": "d", "inputSchema": {}}]}
        if method == "resources/list":
            return {"resources": [{"uri": VIEW, "name": "key view", "mimeType": "text/html"}]}
        raise AssertionError(method)


def _client(mcp, *slugs):
    topo = Topology()
    for s in slugs:
        topo.register(s, f"http://127.0.0.1:8085/{s}/mcp")
    app.state.personas = PersonaGateway(topo, mcp)
    return TestClient(app)


def test_the_view_is_read_from_the_persona_it_names_with_the_callers_token():
    mcp = FakeMcp()
    r = _client(mcp, "seraph").post("/personas/seraph/resources/read", json={"uri": VIEW}, headers=AUTH)
    assert r.status_code == 200 and r.json()["text"] == "<html>key</html>"
    assert mcp.calls == [("http://127.0.0.1:8085/seraph/mcp", "resources/read", {"uri": VIEW}, "user-token")]


def test_a_tool_is_called_on_the_persona_with_its_arguments():
    mcp = FakeMcp()
    r = _client(mcp, "seraph").post("/personas/seraph/tools/call", headers=AUTH,
                                    json={"name": "create_api_key", "arguments": {"name": "k"}})
    assert r.status_code == 200 and r.json()["content"][0]["text"] == "called create_api_key"
    assert mcp.calls[0][1:] == ("tools/call", {"name": "create_api_key", "arguments": {"name": "k"}},
                                "user-token")


def test_an_unregistered_persona_is_not_running():
    r = _client(FakeMcp()).post("/personas/seraph/resources/read", json={"uri": VIEW}, headers=AUTH)
    assert r.status_code == 503 and "not running" in r.json()["detail"]


def test_a_persona_that_does_not_answer_is_not_running():
    mcp = FakeMcp(fail=httpx.ConnectError("refused"))
    r = _client(mcp, "seraph").post("/personas/seraph/resources/read", json={"uri": VIEW}, headers=AUTH)
    assert r.status_code == 503


def test_a_refusal_keeps_its_status():
    req = httpx.Request("POST", "http://x")
    mcp = FakeMcp(fail=httpx.HTTPStatusError("no", request=req, response=httpx.Response(403, request=req)))
    r = _client(mcp, "seraph").post("/personas/seraph/tools/call", json={"name": "t"}, headers=AUTH)
    assert r.status_code == 403


@pytest.mark.parametrize("path,body", [("/personas/seraph/resources/read", {"uri": VIEW}),
                                       ("/personas/seraph/tools/call", {"name": "t"})])
def test_no_caller_no_call(path, body):
    mcp = FakeMcp()
    assert _client(mcp, "seraph").post(path, json=body).status_code == 401
    assert mcp.calls == []


def test_the_listing_names_every_persona_and_marks_one_that_is_down():
    class Mixed(FakeMcp):
        async def request(self, endpoint, method, params, *, token=None):
            if "/iris/" in endpoint:
                raise httpx.ConnectError("refused")
            return await super().request(endpoint, method, params, token=token)

    items = _client(Mixed(), "seraph", "iris").get("/personas", headers=AUTH).json()["items"]
    by = {i["server"]: i for i in items}
    assert by["iris"]["status"] == "down"
    assert by["seraph"]["status"] == "ok"
    assert by["seraph"]["tools"][0]["name"] == "create_api_key"
    assert by["seraph"]["resources"][0]["uri"] == VIEW
