"""`server_ref` / `tool_ref` / `arguments` — the dispatch contract the descriptors declare.

Measured 2026-08-25: **11 of 11** shipped `mcp_tool` operations declared `server_ref`/`tool_ref`
and **none** declared `target`, while the dispatcher read only `target`. Every one answered
`500 Bad mcp_tool target`. The two were written against different contracts.

Four of the eleven cannot be expressed as a static `target` at all — the tool is named per call
by the CALLER (`$.body.name`) or by the artifact's own content (`$.context.tool_name`). That is
why the fix implements the referential form rather than backfilling `target` strings.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException

from crystal.dispatcher import Dispatcher
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry

ARTIFACT = {
    "id": "a1",
    "content_type": "x/y",
    "root_id": "iris",
    "content": {"cfg": True},
    "context": {"operator": {"server": "lumen", "tool": "synthesize", "arguments": {"a": 1}},
                "server_id": "aria", "tool_name": "narrate"},
}


class FakeMantle:
    def __init__(self, artifact=None):
        self.artifact = ARTIFACT if artifact is None else artifact

    async def my_access(self, *a, **k):
        return True

    async def get_artifact(self, aid, **k):
        return self.artifact


class FakeMcp:
    def __init__(self):
        self.calls = []

    async def call_tool(self, endpoint, tool, args, *, token=None):
        self.calls.append((endpoint, tool, args))
        return {"called": tool}


def _dispatch(dispatch_block, body=None, artifact=None):
    reg = TypeRegistry()
    reg.register({"content_type": "x/y", "operations": {"go": {"dispatch": dispatch_block}}},
                 server="t")
    topo = Topology()
    for slug in ("aria", "iris", "lumen", "seraph"):
        topo.register(slug, "http://%s:9000/mcp" % slug, None)
    d = Dispatcher(FakeMantle(artifact), reg, topo)
    d.mcp = FakeMcp()
    asyncio.run(d.dispatch("a1", "go", body or {}, token="tok"))
    return d.mcp.calls[-1]


def test_literal_refs_resolve():
    ep, tool, _ = _dispatch({"kind": "mcp_tool", "server_ref": "lumen", "tool_ref": "invoke_llm"})
    assert "lumen" in ep and tool == "invoke_llm"


def test_the_caller_can_name_the_tool():
    """`$.body.name` — iris/mcp-server and aria/chat both do this. No static target can."""
    _, tool, _ = _dispatch({"kind": "mcp_tool", "server_ref": "aria", "tool_ref": "$.body.name"},
                           body={"name": "narrate"})
    assert tool == "narrate"


def test_the_artifact_can_name_its_own_server_and_tool():
    """`$.context.*` — lumen/operator and lumen/tool select from the artifact's own content."""
    ep, tool, _ = _dispatch({"kind": "mcp_tool", "server_ref": "$.context.server_id",
                             "tool_ref": "$.context.tool_name"})
    assert "aria" in ep and tool == "narrate"


def test_a_root_id_ref_resolves():
    ep, _, _ = _dispatch({"kind": "mcp_tool", "server_ref": "$.root_id", "tool_ref": "ask_human"})
    assert "iris" in ep


def test_context_arriving_as_a_json_string_still_resolves():
    """Mantle hands `context` back as an object or a STRING depending on the path taken.
    A ref into it must work either way, or the same descriptor breaks on one route only."""
    art = dict(ARTIFACT, context=json.dumps(ARTIFACT["context"]))
    _, tool, _ = _dispatch({"kind": "mcp_tool", "server_ref": "lumen",
                            "tool_ref": "$.context.tool_name"}, artifact=art)
    assert tool == "narrate"


def test_an_arguments_mapping_is_applied():
    """Three shipped ops declare `arguments`; before this they were dropped and the raw body
    was forwarded — the right tool called with the wrong payload."""
    _, _, args = _dispatch(
        {"kind": "mcp_tool", "server_ref": "lumen", "tool_ref": "invoke_llm",
         "arguments": {"connection_artifact_id": "$.root_id", "ws": "$.body.workspace_id",
                       "fixed": "literal"}},
        body={"workspace_id": "w1"})
    assert args == {"connection_artifact_id": "iris", "ws": "w1", "fixed": "literal"}


def test_arguments_may_be_a_single_path_to_an_object():
    """lumen/operator declares `"arguments": "$.context.operator.arguments"`."""
    _, _, args = _dispatch({"kind": "mcp_tool", "server_ref": "$.context.operator.server",
                            "tool_ref": "$.context.operator.tool",
                            "arguments": "$.context.operator.arguments"})
    assert args == {"a": 1}


def test_absent_arguments_forwards_the_body_unchanged():
    _, _, args = _dispatch({"kind": "mcp_tool", "server_ref": "lumen", "tool_ref": "synthesize"},
                           body={"x": 1})
    assert args == {"x": 1}


def test_an_unresolvable_path_fails_loudly_and_names_the_path():
    """The failure that matters. A silent None would dispatch to the wrong tool, or send an
    empty argument, and look like a working call."""
    with pytest.raises(HTTPException) as e:
        _dispatch({"kind": "mcp_tool", "server_ref": "lumen", "tool_ref": "$.context.nope"})
    assert e.value.status_code == 500
    assert "$.context.nope" in str(e.value.detail)


def test_the_flat_target_form_still_works():
    """`target` is the older form and the dispatcher's own tests use it. It must not regress."""
    ep, tool, _ = _dispatch({"kind": "mcp_tool", "target": "seraph.verify_token"})
    assert "seraph" in ep and tool == "verify_token"


def test_refs_win_over_the_defaulted_target():
    """`target` now defaults to the OPERATION NAME, so a target-first order would shadow the
    referential form on every shipped descriptor. This holds the precedence."""
    _, tool, _ = _dispatch({"kind": "mcp_tool", "server_ref": "lumen", "tool_ref": "synthesize"})
    assert tool == "synthesize", "the defaulted target 'go' must not win over tool_ref"
