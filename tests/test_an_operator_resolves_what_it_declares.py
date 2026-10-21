"""An operator's argument map is a mapping, not a payload: each value is resolved — `$.body.params.x`
from the invoke request, `@relationship.<name>` from the operator's own links, read as the caller.

2026-10-21: the dispatcher returned `$.context.operator.arguments` as it stood, so every operator
sent its tool the literal strings `"$.body.params.to"` and `"@relationship.authorizer"`. The
platform email operator could not have sent through Crystal; nothing had tried.
"""
import asyncio

import pytest
from fastapi import HTTPException

from crystal.dispatcher import Dispatcher
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry

OPERATOR = "application/vnd.agience.operator+json"
INVOKE = {"kind": "mcp_tool", "server_ref": "$.context.operator.server",
          "tool_ref": "$.context.operator.tool", "arguments": "$.context.operator.arguments"}


def _operator(arguments):
    return {"id": "op1", "root_id": "op1", "content_type": OPERATOR,
            "context": {"operator": {"server": "iris", "tool": "send_email", "arguments": arguments}}}


class FakeMantle:
    def __init__(self, artifact, links=()):
        self.artifact, self._links, self.asked = artifact, list(links), []

    async def my_access(self, *a, **k):
        return True

    async def get_artifact(self, aid, **k):
        return self.artifact

    async def links(self, aid, name, *, token=None):
        self.asked.append((aid, name, token))
        return [k for k in self._links if k["relationship"] == name or k["relationship"].startswith(name + ".")]


class FakeMcp:
    def __init__(self):
        self.calls = []

    async def call_tool(self, endpoint, tool, args, *, token=None):
        self.calls.append((tool, args))
        return {"ok": True}


def _invoke(arguments, body, links=()):
    reg = TypeRegistry()
    reg.register({"content_type": OPERATOR, "operations": {"invoke": {"dispatch": INVOKE}}}, server="t")
    topo = Topology()
    topo.register("iris", "http://iris:9000/mcp", None)
    mantle = FakeMantle(_operator(arguments), links)
    d = Dispatcher(mantle, reg, topo)
    d.mcp = FakeMcp()
    asyncio.run(d.dispatch("op1", "invoke", body, token="tok"))
    return d.mcp.calls[-1][1], mantle


def test_the_map_resolves_from_the_request_and_leaves_out_what_is_optional():
    args, _ = _invoke({"to": "$.body.params.to", "cc": "$.body.params.cc?", "kind": "notice"},
                      {"params": {"to": "a@example.invalid"}})
    assert args == {"to": "a@example.invalid", "kind": "notice"}


def test_a_link_resolves_to_what_it_links_read_as_the_caller():
    args, mantle = _invoke({"authorizer_artifact_id": "@relationship.authorizer"}, {"params": {}},
                           links=[{"relationship": "authorizer", "root_id": "authz-1"},
                                  {"relationship": "authorizer.backup", "root_id": "authz-2"}])
    assert args == {"authorizer_artifact_id": "authz-1"}
    assert mantle.asked == [("op1", "authorizer", "tok")]


def test_a_missing_link_is_named_unless_it_is_optional():
    args, _ = _invoke({"inbox": "@relationship.inbox?"}, {"params": {}})
    assert args == {}
    with pytest.raises(HTTPException) as e:
        _invoke({"authorizer_artifact_id": "@relationship.authorizer"}, {"params": {}})
    assert e.value.status_code == 422 and "authorizer" in e.value.detail


def test_two_links_of_one_name_are_refused():
    with pytest.raises(HTTPException) as e:
        _invoke({"a": "@relationship.authorizer"}, {"params": {}},
                links=[{"relationship": "authorizer", "root_id": "x"}, {"relationship": "authorizer", "root_id": "y"}])
    assert e.value.status_code == 422


def test_a_callers_value_is_data_even_when_it_reads_like_a_link():
    args, mantle = _invoke({"subject": "$.body.params.subject"}, {"params": {"subject": "@relationship.authorizer"}})
    assert args == {"subject": "@relationship.authorizer"} and mantle.asked == []


def test_a_map_taken_from_the_request_is_forwarded_as_it_is():
    reg = TypeRegistry()
    reg.register({"content_type": OPERATOR, "operations": {"invoke": {"dispatch": {
        **INVOKE, "arguments": "$.body.params"}}}}, server="t")
    topo = Topology()
    topo.register("iris", "http://iris:9000/mcp", None)
    d = Dispatcher(FakeMantle(_operator({})), reg, topo)
    d.mcp = FakeMcp()
    asyncio.run(d.dispatch("op1", "invoke", {"params": {"x": "$.context.operator"}}, token="tok"))
    assert d.mcp.calls[-1][1] == {"x": "$.context.operator"}
