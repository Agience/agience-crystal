"""Dispatcher routing tests — fakes for Mantle/MCP so no live deps are needed."""
import asyncio

import pytest
from fastapi import HTTPException

from crystal.dispatcher import Dispatcher
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry

ARTIFACT = {"id": "a1", "content_type": "x/y"}


class FakeMantle:
    def __init__(self, artifact, access_allowed=True):
        self.artifact = artifact
        self.calls = []
        self.access_allowed = access_allowed

    async def my_access(self, resource_id, action, *, token=None):
        self.calls.append(("my_access", resource_id, action, token))
        return self.access_allowed

    async def get_artifact(self, aid, *, token=None, hydrate=False):
        self.calls.append(("get", aid, token))
        return self.artifact

    async def patch(self, path, body, *, token=None):
        self.calls.append(("patch", path, body, token))
        return {"patched": True}

    async def post(self, path, body, *, token=None):
        self.calls.append(("post", path, body, token))
        return {"posted": True}

    async def delete(self, path, *, token=None):
        self.calls.append(("delete", path, token))
        return {"deleted": True}

    async def search(self, body, *, token=None):
        self.calls.append(("search", body, token))
        return {"hits": []}


class FakeMcp:
    def __init__(self):
        self.calls = []

    async def call_tool(self, endpoint, tool, args, *, token=None):
        self.calls.append((endpoint, tool, args, token))
        return {"tool": tool}


def _registry():
    reg = TypeRegistry()
    reg.register({
        "content_type": "x/y",
        "operations": {
            "commit": {"dispatch": {"kind": "artifact_crud", "target": "update"}},
            "render": {"dispatch": {"kind": "mcp_tool", "target": "aria.render"}},
            "rebalance": {"dispatch": {"kind": "native", "target": "move"}},
        },
    }, server="aria")
    return reg


def test_artifact_crud_update_routes_to_patch():
    m = FakeMantle(ARTIFACT)
    d = Dispatcher(m, _registry(), Topology())
    res = asyncio.run(d.dispatch("a1", "commit", {"context": "{}"}, token="tok"))
    assert res == {"patched": True}
    assert any(c[0] == "patch" and c[3] == "tok" for c in m.calls)


def test_mcp_tool_routes_to_persona_endpoint():
    m = FakeMantle(ARTIFACT)
    topo = Topology()
    topo.register("aria", "http://aria/mcp")
    mcp = FakeMcp()
    d = Dispatcher(m, _registry(), topo, mcp=mcp)
    res = asyncio.run(d.dispatch("a1", "render", {"x": 1}, token="tok"))
    assert res == {"tool": "render"}
    assert mcp.calls[0][:2] == ("http://aria/mcp", "render")
    assert mcp.calls[0][3] == "tok"


def _guarded_registry():
    """Ops that declare requires_grant — the op-auth gate."""
    reg = TypeRegistry()
    reg.register({
        "content_type": "x/y",
        "operations": {
            "run":  {"dispatch": {"kind": "mcp_tool", "target": "aria.run"},
                     "requires_grant": "invoke"},
            "view": {"dispatch": {"kind": "mcp_tool", "target": "aria.view"},
                     "requires_grant": "read"},
        },
    }, server="aria")
    return reg


def test_requires_grant_allowed_checks_then_dispatches():
    m = FakeMantle(ARTIFACT, access_allowed=True)
    topo = Topology(); topo.register("aria", "http://aria/mcp")
    mcp = FakeMcp()
    d = Dispatcher(m, _guarded_registry(), topo, mcp=mcp)
    res = asyncio.run(d.dispatch("a1", "run", {}, token="tok"))
    assert res == {"tool": "run"}
    assert ("my_access", "a1", "invoke", "tok") in m.calls   # verified with the CALLER's token


def test_requires_grant_denied_is_403_and_never_dispatches():
    m = FakeMantle(ARTIFACT, access_allowed=False)
    topo = Topology(); topo.register("aria", "http://aria/mcp")
    mcp = FakeMcp()
    d = Dispatcher(m, _guarded_registry(), topo, mcp=mcp)
    with pytest.raises(HTTPException) as e:
        asyncio.run(d.dispatch("a1", "run", {}, token="tok"))
    assert e.value.status_code == 403
    assert mcp.calls == []                                   # the persona tool never ran


def test_requires_grant_read_is_proven_by_the_fetch():
    # The artifact fetch under the caller's token IS Mantle's read decision —
    # no second round-trip for requires_grant == "read".
    m = FakeMantle(ARTIFACT, access_allowed=False)           # would deny if asked
    topo = Topology(); topo.register("aria", "http://aria/mcp")
    mcp = FakeMcp()
    d = Dispatcher(m, _guarded_registry(), topo, mcp=mcp)
    res = asyncio.run(d.dispatch("a1", "view", {}, token="tok"))
    assert res == {"tool": "view"}
    assert not any(c[0] == "my_access" for c in m.calls)


def test_native_is_501_pending():
    d = Dispatcher(FakeMantle(ARTIFACT), _registry(), Topology())
    with pytest.raises(HTTPException) as e:
        asyncio.run(d.dispatch("a1", "rebalance", {}))
    assert e.value.status_code == 501


def test_unknown_op_is_404():
    d = Dispatcher(FakeMantle(ARTIFACT), _registry(), Topology())
    with pytest.raises(HTTPException) as e:
        asyncio.run(d.dispatch("a1", "nope", {}))
    assert e.value.status_code == 404


def test_missing_artifact_is_404():
    d = Dispatcher(FakeMantle(None), _registry(), Topology())
    with pytest.raises(HTTPException) as e:
        asyncio.run(d.dispatch("a1", "commit", {}))
    assert e.value.status_code == 404


def test_mcp_tool_unregistered_persona_is_502():
    d = Dispatcher(FakeMantle(ARTIFACT), _registry(), Topology(), mcp=FakeMcp())
    with pytest.raises(HTTPException) as e:
        asyncio.run(d.dispatch("a1", "render", {}))
    assert e.value.status_code == 502


# -- create routing ------------------------------------------------------------

def _create_registry():
    reg = TypeRegistry()
    reg.register({
        "content_type": "app/container",
        "operations": {"create": {"dispatch": {"kind": "native", "target": "container.create"}}},
    }, server="aria")
    reg.register({
        "content_type": "app/doc",
        "operations": {"create": {"dispatch": {"kind": "artifact_crud", "target": "create"}}},
    }, server="aria")
    reg.register({
        "content_type": "app/viatool",
        "operations": {"create": {"dispatch": {"kind": "mcp_tool", "target": "aria.make"}}},
    }, server="aria")
    return reg


def test_create_native_routes_to_container_primitive():
    m = FakeMantle(None)
    d = Dispatcher(m, _create_registry(), Topology())
    res = asyncio.run(d.create("app/container", {"name": "Proj"}, token="tok"))
    assert res == {"posted": True}
    post = next(c for c in m.calls if c[0] == "post")
    assert post[1] == "/artifacts/containers"
    assert post[2]["content_type"] == "app/container"   # injected from the route
    assert post[2]["name"] == "Proj"
    assert post[3] == "tok"


def test_create_artifact_crud_routes_to_artifacts():
    m = FakeMantle(None)
    d = Dispatcher(m, _create_registry(), Topology())
    asyncio.run(d.create("app/doc", {"container_id": "c1"}, token="t"))
    post = next(c for c in m.calls if c[0] == "post")
    assert post[1] == "/artifacts"


def test_create_no_op_defaults_to_child_insert():
    m = FakeMantle(None)
    d = Dispatcher(m, _create_registry(), Topology())
    asyncio.run(d.create("app/unknown", {"container_id": "c1"}, token="t"))
    post = next(c for c in m.calls if c[0] == "post")
    assert post[1] == "/artifacts"
    assert post[2]["content_type"] == "app/unknown"


def test_create_mcp_tool_routes_to_persona():
    topo = Topology()
    topo.register("aria", "http://aria/mcp")
    mcp = FakeMcp()
    d = Dispatcher(FakeMantle(None), _create_registry(), topo, mcp=mcp)
    res = asyncio.run(d.create("app/viatool", {"x": 1}, token="t"))
    assert res == {"tool": "make"}
    assert mcp.calls[0][:2] == ("http://aria/mcp", "make")


# -- describers (run as a delegated tool after a change) ----------------------

def _describer_registry():
    reg = TypeRegistry()
    reg.register({
        "content_type": "app/note",
        "operations": {"create": {"dispatch": {"kind": "artifact_crud", "target": "create"}}},
        "describer": {"kind": "mcp_tool", "target": "aria.describe"},
    }, server="aria")
    reg.register({
        "content_type": "app/plain",
        "operations": {"create": {"dispatch": {"kind": "artifact_crud", "target": "create"}}},
    }, server="aria")
    return reg


class FakeMantleWithId(FakeMantle):
    async def post(self, path, body, *, token=None):
        self.calls.append(("post", path, body, token))
        return {"id": "new-artifact-1", "content_type": body.get("content_type")}


def test_describer_dispatches_persona_tool_with_token():
    async def go():
        topo = Topology()
        topo.register("aria", "http://aria/mcp")
        mcp = FakeMcp()
        d = Dispatcher(FakeMantle(None), TypeRegistry(), topo, mcp=mcp)
        await d._run_describer({"kind": "mcp_tool", "target": "aria.describe"},
                               "app/note", "art1", "tok")
        assert mcp.calls[0][:2] == ("http://aria/mcp", "describe")
        assert mcp.calls[0][2] == {"artifact_id": "art1", "content_type": "app/note"}
        assert mcp.calls[0][3] == "tok"   # caller's token forwarded (delegate perms)
    asyncio.run(go())


def test_create_fires_declared_describer():
    async def go():
        topo = Topology()
        topo.register("aria", "http://aria/mcp")
        mcp = FakeMcp()
        d = Dispatcher(FakeMantleWithId(None), _describer_registry(), topo, mcp=mcp)
        await d.create("app/note", {"container_id": "c1"}, token="tok")
        await asyncio.sleep(0)   # let the fire-and-forget describer task run
        assert mcp.calls and mcp.calls[0][1] == "describe"
    asyncio.run(go())


def test_create_without_describer_fires_nothing():
    async def go():
        topo = Topology()
        topo.register("aria", "http://aria/mcp")
        mcp = FakeMcp()
        d = Dispatcher(FakeMantleWithId(None), _describer_registry(), topo, mcp=mcp)
        await d.create("app/plain", {"container_id": "c1"}, token="tok")
        await asyncio.sleep(0)
        assert mcp.calls == []   # no describer declared → no tool call
    asyncio.run(go())


# -- event-driven describer (non-gateway writes; actor-rooted) ----------------

class FakeIdentity:
    def __init__(self):
        self.minted = []

    async def mint_describe_delegation(self, persona_client_id, resource_id, ttl_seconds=300):
        self.minted.append((persona_client_id, resource_id))
        return f"describe-for-{resource_id}"


def test_fire_describer_for_resource_mints_and_dispatches():
    async def go():
        topo = Topology()
        topo.register("aria", "http://aria/mcp", client_id="agience-server-aria")
        mcp = FakeMcp()
        ident = FakeIdentity()
        d = Dispatcher(FakeMantle(None), _describer_registry(), topo, mcp=mcp, identity=ident)
        await d.fire_describer_for_resource("app/note", "art-9")
        # minted a system-describe delegation scoped to the persona + resource
        # (no user impersonation — the subject is fixed by Origin to the system principal)
        assert ident.minted == [("agience-server-aria", "art-9")]
        # dispatched the describer tool with the minted delegation
        assert mcp.calls[0][:2] == ("http://aria/mcp", "describe")
        assert mcp.calls[0][3] == "describe-for-art-9"
    asyncio.run(go())


def test_fire_describer_for_resource_dedups_with_inline():
    async def go():
        topo = Topology()
        topo.register("aria", "http://aria/mcp", client_id="agience-server-aria")
        mcp = FakeMcp()
        ident = FakeIdentity()
        d = Dispatcher(FakeMantleWithId(None), _describer_registry(), topo, mcp=mcp, identity=ident)
        # inline fire (gateway-mediated create) marks the artifact described
        await d.create("app/note", {"container_id": "c1"}, token="tok")
        await asyncio.sleep(0)
        first = len(mcp.calls)
        # the event-driven path for the SAME artifact must dedup (no double-fire / loop)
        await d.fire_describer_for_resource("app/note", "new-artifact-1")
        assert ident.minted == []          # never even minted — deduped
        assert len(mcp.calls) == first      # no second dispatch
    asyncio.run(go())


def test_fire_describer_for_resource_without_identity_noops():
    async def go():
        topo = Topology()
        topo.register("aria", "http://aria/mcp", client_id="agience-server-aria")
        mcp = FakeMcp()
        d = Dispatcher(FakeMantle(None), _describer_registry(), topo, mcp=mcp)  # no identity
        await d.fire_describer_for_resource("app/note", "art-1")
        assert mcp.calls == []   # can't mint without an identity → no-op
    asyncio.run(go())
