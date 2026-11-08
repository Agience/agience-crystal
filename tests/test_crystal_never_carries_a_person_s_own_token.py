"""Review 11, H3: Crystal never receives or forwards a caller's full person token.

The web app exchanges the person's token at Origin before it calls Crystal: for a persona's own
surface (`/personas/{name}/...`) a delegation narrowed to that persona, which Crystal forwards to
that persona alone; for Crystal's own work a delegation narrowed to Crystal, which Crystal exchanges
at Origin for its own server delegation before it calls Mantle. A person's full access token is
refused at Crystal's door, and Origin's sign-in bounds are honoured.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from crystal import caller_token as C
from crystal.main import app
from crystal.operator_gate import OperatorGate
from crystal.personas import PersonaGateway
from crystal.topology import Topology


def _claims(**kw):
    base = {"iss": "https://platform.test", "sub": "user-1", "iat": 1, "exp": 2**40}
    base.update(kw)
    return base


PERSON = _claims(aud="https://platform.test")
FOR_SERAPH = _claims(aud="seraph", act={"sub": "seraph"}, principal_type="delegation",
                     scope="persona:seraph")
FOR_IRIS = _claims(aud="iris", act={"sub": "iris"}, principal_type="delegation",
                   scope="persona:iris")
FOR_CRYSTAL = _claims(aud="crystal", act={"sub": "crystal"}, principal_type="delegation",
                      scope="persona:crystal")
TOKENS = {"person-token": PERSON, "seraph-token": FOR_SERAPH, "iris-token": FOR_IRIS,
          "crystal-token": FOR_CRYSTAL}


@pytest.fixture(autouse=True)
def origin_signed(monkeypatch):
    monkeypatch.setattr(C, "_verify_origin", lambda t: TOKENS.get(t))


class FakeMcp:
    def __init__(self):
        self.calls = []

    async def request(self, endpoint, method, params, *, token=None):
        self.calls.append((endpoint, method, token))
        return {"content": [{"type": "text", "text": "ok"}]}


class _NoOperators:
    async def platform_operators(self, *, token=None):
        return []


def _client(mcp):
    topo = Topology()
    topo.register("seraph", "http://127.0.0.1:8085/seraph/mcp")
    app.state.personas = PersonaGateway(topo, mcp)
    app.state.operator_gate = OperatorGate(_NoOperators())
    return TestClient(app)


def _call(client, token):
    return client.post("/personas/seraph/tools/call", json={"name": "t", "arguments": {}},
                       headers={"Authorization": f"Bearer {token}"})


def test_a_person_s_own_token_is_refused_at_the_door():
    mcp = FakeMcp()
    r = _call(_client(mcp), "person-token")
    assert r.status_code == 401 and mcp.calls == []


def test_a_persona_gets_only_the_delegation_narrowed_to_it():
    mcp = FakeMcp()
    c = _client(mcp)
    assert _call(c, "seraph-token").status_code == 200
    assert mcp.calls == [("http://127.0.0.1:8085/seraph/mcp", "tools/call", "seraph-token")]
    for other in ("iris-token", "crystal-token"):
        assert _call(c, other).status_code == 403
    assert len(mcp.calls) == 1


def test_crystal_s_own_delegation_is_exchanged_before_mantle_sees_it(monkeypatch):
    asked = []

    async def exchange(narrowed):
        asked.append(narrowed)
        return "crystal-server-delegation"

    monkeypatch.setattr(C, "_exchange_for_crystal", exchange)
    import asyncio
    caller = asyncio.run(C.resolve("crystal-token"))
    assert asyncio.run(caller.for_mantle()) == "crystal-server-delegation"
    assert asyncio.run(caller.for_mantle()) == "crystal-server-delegation"
    assert asyncio.run(caller.for_persona("seraph")) is None
    assert asyncio.run(C.resolve("seraph-token")).person is False


def test_a_token_from_before_its_person_s_sign_in_bound_is_refused(monkeypatch):
    from prism.trust import not_before
    nb = not_before.NotBefore("http://origin.test")
    nb.apply({"entries": [{"sub": "user-1", "not_before": 10}]})
    monkeypatch.setattr(not_before, "_shared", nb)
    mcp = FakeMcp()
    assert _call(_client(mcp), "seraph-token").status_code == 401 and mcp.calls == []
