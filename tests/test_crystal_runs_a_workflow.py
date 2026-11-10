"""The gateway runs a workflow's steps (John, 2026-11-10, BUG-048).

A step served by another persona never ran: Lumen handed each step to the gateway, but a request
reaches Lumen through the gateway as a hop's result, which Origin never exchanges again. The gateway
runs them itself, each step the operator's own invoke, one hop to its persona on the caller's own
delegation.
"""
import asyncio
import json

from crystal.dispatcher import Dispatcher
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry

TRANSFORM = "application/vnd.agience.transform+json"
OPERATOR = "application/vnd.agience.operator+json"


class Mantle:
    def __init__(self, docs, platform=(), may_invoke=True, links=()):
        self.docs, self.platform, self.may, self._links = docs, list(platform), may_invoke, list(links)
        self.calls = []

    async def get_artifact(self, aid, *, token=None, hydrate=False):
        self.calls.append(("get", aid, token))
        return self.docs.get(aid)

    async def my_access(self, rid, action, *, token=None):
        self.calls.append(("my_access", rid, action, token))
        return self.may

    async def platform_operators(self, *, token=None):
        return self.platform

    async def links(self, aid, name, *, token=None):
        return self._links


class Mcp:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    async def call_tool(self, endpoint, tool, args, *, token=None):
        self.calls.append((endpoint, tool, args, token))
        return self.answers.get(tool) or {"content": [{"type": "text", "text": json.dumps({"from": tool, "args": args})}]}


def _registry():
    reg = TypeRegistry()
    reg.register({"content_type": TRANSFORM, "operations": {"invoke": {"requires_grant": "invoke", "dispatch": {
        "kind": "workflow", "server_ref": "lumen", "tool_ref": "execute_transform",
        "arguments": {"transform_id": "$.root_id", "params": "$.body.params?", "context": "$.body.context?"}}}}},
        server="lumen")
    reg.register({"content_type": OPERATOR, "operations": {"invoke": {"requires_grant": "invoke", "dispatch": {
        "kind": "mcp_tool", "server_ref": "$.context.operator.server", "tool_ref": "$.context.operator.tool",
        "arguments": "$.context.operator.arguments"}}}}, server="lumen")
    return reg


def _topology():
    topo = Topology()
    for slug in ("lumen", "sage", "iris"):
        topo.register(slug, f"http://{slug}/mcp")
    return topo


def _dispatcher(mantle, mcp):
    d = Dispatcher(mantle, _registry(), _topology(), mcp=mcp)

    async def no_gate(slug, tool, token):
        return None
    d.operator_gate.check = no_gate
    return d


def _hops():
    made = []

    async def hop(slug):
        made.append(slug)
        return f"hop-{slug}"
    return made, hop


STEPS = [{"name": "List", "key": "s1", "operator_id": "op-list", "arguments": {"limit": 3}},
         {"name": "Tell", "key": "s2", "operator_id": "op-tell", "arguments": {"what": "$.previous"}}]
PLATFORM = [{"id": "op-list", "server": "sage", "tool": "browse_collections"},
            {"id": "op-tell", "server": "iris", "tool": "send_note"}]


def test_run_now_runs_each_step_on_its_own_persona_by_one_hop():
    """Sealed steps come with the request; each platform operator runs on its persona."""
    docs = {"wf": {"id": "wf", "root_id": "wf", "content_type": TRANSFORM, "context": "{}"},
            "op-list": {"id": "op-list", "content_type": OPERATOR, "context": "{}"},
            "op-tell": {"id": "op-tell", "content_type": OPERATOR, "context": "{}"}}
    mcp, (made, hop) = Mcp(), _hops()
    d = _dispatcher(Mantle(docs, PLATFORM), mcp)
    body = {"context": {"run": {"type": "workflow", "steps": STEPS}}}
    out = asyncio.run(d.dispatch("wf", "invoke", body, token="app", persona_token=hop))
    assert made == ["sage", "iris"], "one hop to each step's persona, none to Lumen"
    assert [(c[0], c[1], c[3]) for c in mcp.calls] == [
        ("http://sage/mcp", "browse_collections", "hop-sage"), ("http://iris/mcp", "send_note", "hop-iris")]
    assert mcp.calls[1][2] == {"what": {"from": "browse_collections", "args": {"limit": 3}}}, "$.previous"
    result = json.loads(out["content"][0]["text"])
    assert out["isError"] is False and result["status"] == "completed" and len(result["results"]) == 2


def test_a_timer_reads_its_operators_on_its_grant():
    """Readable steps and operators (a timer's job grant opens them) run as the operator says."""
    op = {"server": "sage", "tool": "browse_collections", "arguments": {"limit": "$.body.params.limit?"}}
    docs = {"wf": {"id": "wf", "root_id": "wf", "content_type": TRANSFORM,
                   "context": json.dumps({"run": {"type": "workflow", "steps": STEPS[:1]}})},
            "op-list": {"id": "op-list", "content_type": OPERATOR, "context": json.dumps({"operator": op})}}
    mcp, (made, hop) = Mcp(), _hops()
    asyncio.run(_dispatcher(Mantle(docs), mcp).dispatch("wf", "invoke", {}, token="run-key", persona_token=hop))
    assert made == ["sage"] and mcp.calls[0][1:3] == ("browse_collections", {"limit": 3})


def test_a_failed_step_stops_the_run_and_says_which():
    docs = {"wf": {"id": "wf", "root_id": "wf", "content_type": TRANSFORM, "context": "{}"},
            "op-list": {"id": "op-list", "content_type": OPERATOR, "context": "{}"},
            "op-tell": {"id": "op-tell", "content_type": OPERATOR, "context": "{}"}}
    mcp = Mcp({"browse_collections": {"content": [{"type": "text", "text": "no"}], "isError": True}})
    _, hop = _hops()
    out = asyncio.run(_dispatcher(Mantle(docs, PLATFORM), mcp).dispatch(
        "wf", "invoke", {"context": {"run": {"type": "workflow", "steps": STEPS}}}, token="app", persona_token=hop))
    assert out["isError"] is True and "Step 0 (List) failed: no" in out["content"][0]["text"]
    assert len(mcp.calls) == 1


def test_an_operator_neither_readable_nor_listed_is_refused_in_words():
    docs = {"wf": {"id": "wf", "root_id": "wf", "content_type": TRANSFORM, "context": "{}"},
            "op-mine": {"id": "op-mine", "content_type": OPERATOR, "context": "{}"}}
    steps = [{"name": "Mine", "operator_id": "op-mine", "arguments": {}}]
    mcp, (_, hop) = Mcp(), _hops()
    out = asyncio.run(_dispatcher(Mantle(docs), mcp).dispatch(
        "wf", "invoke", {"context": {"run": {"type": "workflow", "steps": steps}}}, token="app", persona_token=hop))
    assert out["isError"] is True and "cannot be read here" in out["content"][0]["text"] and not mcp.calls


def test_a_step_needs_the_operator_s_invoke_grant():
    docs = {"wf": {"id": "wf", "root_id": "wf", "content_type": TRANSFORM, "context": "{}"},
            "op-list": {"id": "op-list", "content_type": OPERATOR, "context": "{}"}}
    mantle, mcp = Mantle(docs, PLATFORM), Mcp()
    mantle.may = True
    d = _dispatcher(mantle, mcp)

    async def only_the_workflow(rid, action, *, token=None):
        return rid == "wf"
    mantle.my_access = only_the_workflow
    _, hop = _hops()
    out = asyncio.run(d.dispatch("wf", "invoke", {"context": {"run": {"type": "workflow", "steps": STEPS[:1]}}},
                                 token="app", persona_token=hop))
    assert out["isError"] is True and "may not invoke" in out["content"][0]["text"] and not mcp.calls


def test_a_single_step_goes_to_lumen_as_before():
    docs = {"t": {"id": "t", "root_id": "t", "content_type": TRANSFORM,
                  "context": json.dumps({"run": {"type": "mcp-tool", "server": "sage", "tool": "x"}})}}
    mcp, (made, hop) = Mcp(), _hops()
    asyncio.run(_dispatcher(Mantle(docs), mcp).dispatch("t", "invoke", {}, token="app", persona_token=hop))
    assert made == ["lumen"] and mcp.calls[0][1] == "execute_transform"


import pytest  # noqa: E402


@pytest.mark.parametrize("run,params", [
    ({"type": "workflow", "steps": []}, {}),                                              # no steps
    ({"type": "workflow", "steps": [{**STEPS[0], "condition": {"field": "x"}}]}, {}),      # a condition
    ({"type": "workflow", "steps": STEPS[:1], "retry": {"on_field": "x"}}, {}),            # retry
    ({"type": "workflow", "steps": [{"name": "T", "transform_id": "t-2"}]}, {}),           # a Transform step
    ({"type": "workflow", "steps": STEPS[:1]}, {"state_artifact_id": "st-1"}),              # a state artifact
    ({"type": "workflow", "steps": [{"name": "Bad", "operator_id": "x/children"}]}, {}),   # not an id
])
def test_what_this_runner_does_not_do_exactly_as_lumen_did_goes_to_lumen(run, params):
    """The new kind is additive: only what it does exactly as Lumen did is taken; the rest runs in
    Lumen as before."""
    docs = {"wf": {"id": "wf", "root_id": "wf", "content_type": TRANSFORM, "context": json.dumps({"run": run})}}
    mcp, (made, hop) = Mcp(), _hops()
    asyncio.run(_dispatcher(Mantle(docs, PLATFORM), mcp).dispatch(
        "wf", "invoke", {"params": params}, token="app", persona_token=hop))
    assert made == ["lumen"] and [c[1] for c in mcp.calls] == ["execute_transform"]


def test_an_operator_is_given_the_workspace_the_run_is_in():
    op = {"server": "sage", "tool": "browse_collections", "arguments": {"ws": "$.body.workspace_id"}}
    docs = {"wf": {"id": "wf", "root_id": "wf", "collection_id": "ws-1", "content_type": TRANSFORM,
                   "context": json.dumps({"run": {"type": "workflow", "steps": STEPS[:1]}})},
            "op-list": {"id": "op-list", "content_type": OPERATOR, "context": json.dumps({"operator": op})}}
    mcp, (_, hop) = Mcp(), _hops()
    asyncio.run(_dispatcher(Mantle(docs), mcp).dispatch("wf", "invoke", {}, token="k", persona_token=hop))
    assert mcp.calls[0][2] == {"ws": "ws-1"}
