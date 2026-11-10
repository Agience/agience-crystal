"""Review 16, M1: `POST /register` repoints a persona slug at an endpoint, so only that persona
may do it: a service token whose issuer is the slug's own manifest anchor (`iss == sub == slug`,
signed by that anchor's key), or Crystal's own. Any other anchored service (Mantle, another
persona) is refused, and a persona registers itself signing as itself."""
from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk as jose_jwk
from jose import jwt


def _anchor():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return pem, {"keys": [jose_jwk.construct(pub, algorithm="RS256").to_dict()]}


def _tok(pem, name, *, sub=None, aud="crystal", jti="fresh"):
    """A service token, addressed to Crystal and single-use (review 19) unless told otherwise:
    `jti="fresh"` mints a new one each call, `jti=None` leaves it out."""
    import uuid
    now = int(time.time())
    claims = {"iss": name, "sub": sub or name, "aud": aud, "principal_type": "service",
              "iat": now, "exp": now + 300}
    if jti is not None:
        claims["jti"] = uuid.uuid4().hex if jti == "fresh" else jti
    return jwt.encode(claims, pem, algorithm="RS256")


@pytest.fixture(autouse=True)
def _restore_state():
    """`app.state` is shared by every test file: restored by hand afterwards, the way
    `test_the_edge_checks_before_it_parses` does (Starlette's State keeps a private dict)."""
    from crystal import main as M
    state = M.app.state
    saved = {name: state[name] for name in state}
    yield
    for name in list(state):
        del state[name]
    for name, value in saved.items():
        state[name] = value


def _gateway():
    from crystal import main as M
    from crystal.topology import Topology
    from crystal.type_registry import TypeRegistry
    keys = {n: _anchor() for n in ("mantle", "lumen", "iris", "crystal")}
    st = M.app.state
    st.trust_anchors = {n: k[1] for n, k in keys.items()}
    st.topology = Topology()
    st.types = TypeRegistry()
    st.mantle = None
    st.topology.register("lumen", "http://lumen.internal:9000/mcp", "agience-server-lumen")
    return TestClient(M.app), st, keys


def _register(c, token, endpoint="http://elsewhere:1/mcp"):
    return c.post("/register", json={"slug": "lumen", "endpoint": endpoint,
                                     "client_id": "agience-server-lumen", "types": []},
                  headers={"Authorization": f"Bearer {token}"})


def test_another_anchored_service_does_not_repoint_a_persona():
    c, st, keys = _gateway()
    for name in ("mantle", "iris"):
        r = _register(c, _tok(keys[name][0], name))
        assert r.status_code in (401, 403), (name, r.text)
    # A token naming lumen but signed by another anchor's key is refused too.
    assert _register(c, _tok(keys["mantle"][0], "lumen")).status_code in (401, 403)
    assert st.topology.resolve("lumen").endpoint == "http://lumen.internal:9000/mcp"


def test_only_the_persona_itself_registers_it():
    """Review 19: Crystal's own token registers no other persona's slug either."""
    c, st, keys = _gateway()
    r = _register(c, _tok(keys["lumen"][0], "lumen"), "http://lumen.new:9000/mcp")
    assert r.status_code == 200, r.text
    assert st.topology.resolve("lumen").endpoint == "http://lumen.new:9000/mcp"
    r = _register(c, _tok(keys["crystal"][0], "crystal"), "http://lumen.again:9000/mcp")
    assert r.status_code == 403, r.text
    assert st.topology.resolve("lumen").endpoint == "http://lumen.new:9000/mcp"


def test_a_token_for_another_service_or_used_before_registers_nothing():
    """Review 19, H1: the token Crystal sends Mantle (aud mantle) is useless at Crystal, and a
    registration token is good once."""
    from crystal.identity import CrystalIdentity
    c, st, keys = _gateway()
    feed_token = CrystalIdentity(keys["crystal"][0], "http://origin.test").service_jwt("mantle")
    assert _register(c, feed_token).status_code in (401, 403)
    assert _register(c, _tok(keys["lumen"][0], "lumen", aud="mantle")).status_code in (401, 403)
    assert _register(c, _tok(keys["lumen"][0], "lumen", jti=None)).status_code == 403
    once = _tok(keys["lumen"][0], "lumen", jti="j-1")
    assert _register(c, once, "http://lumen.one:1/mcp").status_code == 200
    assert _register(c, once, "http://lumen.two:1/mcp").status_code == 403
    assert st.topology.resolve("lumen").endpoint == "http://lumen.one:1/mcp"


def test_a_persona_signs_its_own_registration(monkeypatch):
    from crystal import type_registration as tr
    from prism.trust import service_identity

    pem, _jwks = _anchor()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    persona = service_identity.ServiceIdentity(name="lumen", kid="lumen-1",
                                               private_key=load_pem_private_key(pem.encode(),
                                                                                password=None))
    monkeypatch.setattr(service_identity, "load_persona_identity",
                        lambda name: persona if name == "lumen" else None)
    monkeypatch.setattr(tr, "collect_server_types", lambda _f: {"t": {"name": "t"}})
    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"types": 1}

    def post(url, json, headers, timeout):
        sent.update(headers)
        return _Resp()

    monkeypatch.setattr(tr.httpx, "post", post)
    tr.push_server_types("lumen", "unused.py", gateway_uri="http://crystal.test")
    claims = jwt.get_unverified_claims(sent["Authorization"].split()[1])
    assert claims["iss"] == "lumen" and claims["sub"] == "lumen"
    assert claims["aud"] == "crystal" and claims["jti"]               # single-use (review 19)


# -- review 17, L2: a type belongs to the server that registered it -----------------------------

def test_a_type_another_server_holds_is_not_taken_over():
    c, st, keys = _gateway()
    t = {"content_type": "application/vnd.test.thing+json", "operations": {}}
    r = c.post("/register", json={"slug": "lumen", "endpoint": "http://lumen:1/mcp",
                                  "client_id": "agience-server-lumen", "types": [t]},
               headers={"Authorization": f"Bearer {_tok(keys['lumen'][0], 'lumen')}"})
    assert r.status_code == 200 and r.json()["types"] == 1, r.text
    r = c.post("/register", json={"slug": "iris", "endpoint": "http://iris:1/mcp",
                                  "client_id": "agience-server-iris", "types": [t]},
               headers={"Authorization": f"Bearer {_tok(keys['iris'][0], 'iris')}"})
    assert r.status_code == 200 and r.json()["types"] == 0, r.text
    assert r.json()["refused"] == ["application/vnd.test.thing+json"]
    assert st.types._cache["application/vnd.test.thing+json"].server == "lumen"
    # Crystal's own token registers no other persona (review 19), so it moves nothing either:
    # the holder gives the type up by registering again without it.
    r = c.post("/register", json={"slug": "iris", "endpoint": "http://iris:1/mcp",
                                  "client_id": "agience-server-iris", "types": [t]},
               headers={"Authorization": f"Bearer {_tok(keys['crystal'][0], 'crystal')}"})
    assert r.status_code == 403, r.text
    r = c.post("/register", json={"slug": "lumen", "endpoint": "http://lumen:1/mcp",
                                  "client_id": "agience-server-lumen", "types": []},
               headers={"Authorization": f"Bearer {_tok(keys['lumen'][0], 'lumen')}"})
    assert r.status_code == 200, r.text
    r = c.post("/register", json={"slug": "iris", "endpoint": "http://iris:1/mcp",
                                  "client_id": "agience-server-iris", "types": [t]},
               headers={"Authorization": f"Bearer {_tok(keys['iris'][0], 'iris')}"})
    assert r.status_code == 200 and r.json()["types"] == 1, r.text
    assert st.types._cache["application/vnd.test.thing+json"].server == "iris"


def test_a_persona_enrolled_since_the_start_registers_without_a_restart(monkeypatch):
    """2026-11-10, BUG-031: Sage's anchor was added to the manifest after Crystal started; Crystal
    read its anchors once and refused Sage until a restart. A slug with no anchor loaded is looked
    up again on disk, once, and then registers like any other."""
    from crystal import main as M
    c, st, keys = _gateway()
    sage_pem, sage_jwks = _anchor()
    monkeypatch.setattr(M, "load_trust_anchors", lambda _d: {**st.trust_anchors, "sage": sage_jwks})
    r = c.post("/register", json={"slug": "sage", "endpoint": "http://sage.internal:1/mcp",
                                  "client_id": "agience-server-sage", "types": []},
               headers={"Authorization": f"Bearer {_tok(sage_pem, 'sage')}"})
    assert r.status_code == 200, r.text
    assert st.topology.resolve("sage") is not None and "sage" in st.trust_anchors
    # A slug still unknown on disk is refused as before, and the anchors are left as they were.
    other_pem, _ = _anchor()
    r = c.post("/register", json={"slug": "ophan", "endpoint": "http://x:1/mcp",
                                  "client_id": "agience-server-ophan", "types": []},
               headers={"Authorization": f"Bearer {_tok(other_pem, 'ophan')}"})
    assert r.status_code in (401, 403)          # refused at the service gate or the slug's own
