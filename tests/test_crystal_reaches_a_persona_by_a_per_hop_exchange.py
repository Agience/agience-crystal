"""Review 12, John's decision 2: cross-persona work goes through a per-hop exchange at Origin.

Crystal holds, for a person, only the delegation Origin narrowed to Crystal. To reach a persona for
that person (an operation's `mcp_tool` dispatch, `GET /personas`), Crystal exchanges that delegation
at Origin's token endpoint (RFC 8693, `audience` = the persona) for one narrowed to the persona: the
same person, five minutes, no widening. Crystal authenticates the exchange with its own key (an RFC
7523 client assertion). Origin lists which hops it allows and records each. A refused hop reaches
nothing; Mantle still sees only Crystal's own server delegation.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from crystal import caller_token as C
from crystal.dispatcher import Dispatcher
from crystal.main import app
from crystal.operator_gate import OperatorGate
from crystal.personas import PersonaGateway
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry


def _claims(**kw):
    base = {"iss": "https://platform.test", "sub": "user-1", "iat": 1, "exp": 2**40}
    base.update(kw)
    return base


FOR_CRYSTAL = _claims(aud="crystal", act={"sub": "crystal"}, principal_type="delegation",
                      scope="persona:crystal")
FOR_SAGE = _claims(aud="sage", act={"sub": "sage"}, principal_type="delegation",
                   scope="persona:sage")
TOKENS = {"crystal-token": FOR_CRYSTAL, "sage-token": FOR_SAGE}
_REAL_ASSERTION = C._client_assertion


@pytest.fixture(autouse=True)
def origin(monkeypatch):
    """Origin's token endpoint, as far as a hop goes: it answers `hop:<persona>` and records the
    form it was sent."""
    monkeypatch.setattr(C, "_verify_origin", lambda t: TOKENS.get(t))
    asked = []

    async def post(form):
        asked.append(form)
        if form.get("audience") == "refused":
            return None
        return f"hop:{form['audience']}"

    monkeypatch.setattr(C, "_post_token_exchange", post)
    monkeypatch.setattr(C, "_client_assertion", lambda subject: "crystal-assertion")
    C._HOPPED.clear()
    return asked


def test_a_hop_is_an_rfc_8693_exchange_authenticated_by_crystal_s_own_key(origin):
    caller = asyncio.run(C.resolve("crystal-token"))
    assert asyncio.run(caller.for_persona_hop("sage")) == "hop:sage"
    form = origin[0]
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert form["subject_token"] == "crystal-token" and form["audience"] == "sage"
    assert form["client_id"] == C.CRYSTAL_CLIENT_ID
    assert form["client_assertion"] == "crystal-assertion"
    assert form["client_assertion_type"] == \
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    assert asyncio.run(caller.for_persona_hop("sage")) == "hop:sage" and len(origin) == 1


def test_a_delegation_narrowed_to_another_persona_makes_no_hop(origin):
    caller = asyncio.run(C.resolve("sage-token"))
    assert asyncio.run(caller.for_persona_hop("iris")) is None      # sage's reaches only sage
    assert asyncio.run(caller.for_persona_hop("sage")) == "sage-token"
    assert origin == []


class _Mcp:
    def __init__(self):
        self.calls = []

    async def call_tool(self, endpoint, tool, args, *, token=None):
        self.calls.append((endpoint, tool, token))
        return {"tool": tool}

    async def request(self, endpoint, method, params, *, token=None):
        self.calls.append((endpoint, method, token))
        return {"tools": [], "resources": []}


class _Mantle:
    async def platform_operators(self, *, token=None):
        return []

    async def get_artifact(self, aid, *, token=None, hydrate=False):
        self.read_on = token
        return {"id": aid, "content_type": "x/y"}


def _registry():
    reg = TypeRegistry()
    reg.register({"content_type": "x/y", "operations": {
        "render": {"dispatch": {"kind": "mcp_tool", "target": "sage.render"}}}}, server="sage")
    return reg


def test_op_dispatch_reaches_the_persona_on_its_hop_and_mantle_on_crystal_s_own(origin):
    topo = Topology()
    topo.register("sage", "http://sage/mcp")
    mcp, mantle = _Mcp(), _Mantle()
    d = Dispatcher(mantle, _registry(), topo, mcp=mcp)
    caller = asyncio.run(C.resolve("crystal-token"))
    res = asyncio.run(d.dispatch("a1", "render", {}, token="crystal-server",
                                 persona_token=caller.for_persona_hop))
    assert res == {"tool": "render"}
    assert mantle.read_on == "crystal-server"
    assert mcp.calls == [("http://sage/mcp", "render", "hop:sage")]


def test_a_refused_hop_reaches_no_persona(origin):
    topo = Topology()
    topo.register("sage", "http://sage/mcp")
    mcp = _Mcp()
    d = Dispatcher(_Mantle(), _registry(), topo, mcp=mcp)

    async def refused(slug):
        return None

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(d.dispatch("a1", "render", {}, token="crystal-server",
                               persona_token=refused))
    assert e.value.status_code == 403 and mcp.calls == []


def test_get_personas_describes_each_persona_on_its_own_hop(origin, monkeypatch):
    async def own(raw):
        return "crystal-server"

    monkeypatch.setattr(C, "_exchange_for_crystal", own)
    topo = Topology()
    topo.register("sage", "http://sage/mcp")
    topo.register("iris", "http://iris/mcp")
    mcp = _Mcp()
    app.state.personas = PersonaGateway(topo, mcp)
    app.state.operator_gate = OperatorGate(_Mantle())
    r = TestClient(app).get("/personas", headers={"Authorization": "Bearer crystal-token"})
    assert r.status_code == 200, r.text
    tokens = {(ep, tok) for ep, _m, tok in mcp.calls}
    assert ("http://sage/mcp", "hop:sage") in tokens and ("http://iris/mcp", "hop:iris") in tokens
    assert all(tok.startswith("hop:") for _ep, tok in tokens)


def test_crystal_s_hop_assertion_is_single_use_and_bound_to_its_subject(monkeypatch):
    """Origin spends each hop assertion's `jti` and checks its `subject_digest` against the subject
    token, so Crystal signs a fresh `jti` and the digest of the narrowed token it exchanges."""
    import hashlib

    from jose import jwt

    from crystal.identity import CrystalIdentity

    from .test_identity import _test_pem

    ident = CrystalIdentity(_test_pem(), "http://origin:8080")
    monkeypatch.setattr(CrystalIdentity, "load", staticmethod(lambda *a, **k: ident))
    one = jwt.get_unverified_claims(_REAL_ASSERTION("narrowed-a"))
    two = jwt.get_unverified_claims(_REAL_ASSERTION("narrowed-a"))
    assert one["jti"] and one["jti"] != two["jti"]
    assert one["subject_digest"] == hashlib.sha256(b"narrowed-a").hexdigest()
    assert one["exp"] - one["iat"] <= 300
