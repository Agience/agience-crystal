"""The gateway's public edge: credential first, then a bounded body, then the parse.

FastAPI reads and parses a route's body before the handler runs, so a check made inside the handler
comes after the parse. `_BodyGate` makes the same checks first. Each property is shown against a
body FastAPI would reject on its own (invalid JSON answers 422 there), so a 401 can only come from
the gate.

The registry reads (`/types/all`, `/resolve/*`) take an Origin access token or a platform service
token once the gateway has an authority manifest. Tokens are minted here against a manifest this
file writes.
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from crystal import config
from crystal import main
from crystal.identity import load_trust_anchors
from crystal.type_registry import TypeRegistry

ISSUER = "https://platform.test"
CHAT = {"content_type": "application/vnd.agience.chat+json",
        "operations": {"invoke": {"dispatch": {"kind": "mcp_tool", "tool_ref": "chat"}}}}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class _Signer:
    def __init__(self, name):
        self.kid = f"{name}-1"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()).decode()
        n = key.public_key().public_numbers()
        self.jwk = {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": self.kid,
                    "n": _b64u(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
                    "e": _b64u(n.e.to_bytes((n.e.bit_length() + 7) // 8, "big"))}

    def sign(self, **claims):
        now = int(time.time())
        body = {"iat": now, "exp": now + 300}
        body.update(claims)
        return jose_jwt.encode(body, self.pem, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture()
def edge(tmp_path, monkeypatch):
    """The gateway app with an authority manifest (origin + chorus) and a recording dispatcher."""
    signers = {"origin": _Signer("origin"), "chorus": _Signer("chorus")}
    (tmp_path / "authority.manifest.json").write_text(json.dumps({
        "artifact_id": "test-authority", "issuer": ISSUER,
        "trust_anchors": {n: {"jwks": {"keys": [s.jwk]}} for n, s in signers.items()},
    }))
    monkeypatch.setenv("KEYS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "KEYS_DIR", str(tmp_path))
    from prism.trust import authority_trust
    authority_trust.reset_authority_manifest_for_tests()

    calls = []

    class _Dispatcher:
        async def create(self, content_type, body, token):
            calls.append(("create", content_type))
            return {"ok": True}

        async def dispatch(self, artifact_id, op_name, body, token):
            calls.append(("op", artifact_id, op_name))
            return {"ok": True}

    registry = TypeRegistry()
    assert registry.register(CHAT, server="aria")
    monkeypatch.setattr(main.app.state, "trust_anchors", load_trust_anchors(str(tmp_path)), raising=False)
    monkeypatch.setattr(main.app.state, "types", registry, raising=False)
    monkeypatch.setattr(main.app.state, "dispatcher", _Dispatcher(), raising=False)
    from crystal.topology import Topology
    monkeypatch.setattr(main.app.state, "topology", Topology(), raising=False)

    tokens = {
        "access": signers["origin"].sign(iss=ISSUER, sub="alice", aud=ISSUER),
        "refresh": signers["origin"].sign(iss=ISSUER, sub="alice", aud=ISSUER, token_type="refresh"),
        "service": signers["chorus"].sign(iss="chorus", sub="chorus", aud="crystal",
                                          principal_type="service"),
    }
    yield TestClient(main.app), tokens, calls
    authority_trust.reset_authority_manifest_for_tests()


def _auth(token):
    return {"Authorization": "Bearer " + token}


# ── credential before parse ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/create", "/artifacts/a-1/op/read", "/register"])
def test_no_credential_is_refused_before_the_body_is_parsed(edge, path):
    client, tokens, calls = edge
    r = client.post(path, content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 401, (r.status_code, r.text)
    assert calls == []


def test_register_refuses_a_user_token_before_the_parse(edge):
    client, tokens, _ = edge
    r = client.post("/register", content=b"{not json",
                    headers={"content-type": "application/json", **_auth(tokens["access"])})
    assert r.status_code == 401


def test_a_credentialed_request_still_reaches_the_handler(edge):
    """The positive control: the gate replays the body it read, so the handler parses it as usual."""
    client, tokens, calls = edge
    r = client.post("/create", json={"content_type": "text/plain"}, headers=_auth(tokens["access"]))
    assert r.status_code == 200 and calls == [("create", "text/plain")]
    r = client.post("/artifacts/a-1/op/read", json={"x": 1}, headers=_auth(tokens["access"]))
    assert r.status_code == 200 and calls[-1] == ("op", "a-1", "read")
    r = client.post("/register", json={"slug": "zeta", "endpoint": "http://zeta.test/mcp"},
                    headers=_auth(tokens["service"]))
    assert r.status_code == 200, r.text


# ── the body cap ───────────────────────────────────────────────────────────────────────────────────
def test_a_body_over_the_cap_is_refused(edge, monkeypatch):
    client, tokens, calls = edge
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 64, raising=False)
    small = {"content_type": "text/plain"}
    assert client.post("/create", json=small, headers=_auth(tokens["access"])).status_code == 200

    big = {"content_type": "text/plain", "content": "x" * 200}
    r = client.post("/create", json=big, headers=_auth(tokens["access"]))
    assert r.status_code == 413 and len(calls) == 1


def test_a_chunked_body_over_the_cap_is_refused(edge, monkeypatch):
    """No Content-Length to check up front: the gate counts what arrives."""
    client, tokens, calls = edge
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 64, raising=False)

    def chunks():
        yield b'{"content_type": "text/plain", "content": "'
        for _ in range(10):
            yield b"x" * 32
        yield b'"}'

    r = client.post("/artifacts/a-1/op/write", content=chunks(),
                    headers={"content-type": "application/json", **_auth(tokens["access"])})
    assert r.status_code == 413 and calls == []


# ── the registry reads ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/types/all", "/resolve/application/vnd.agience.chat+json"])
def test_the_registry_is_read_only_by_an_authenticated_caller(edge, path):
    client, tokens, _ = edge
    assert client.get(path).status_code == 401
    assert client.get(path, headers=_auth("garbage")).status_code == 401
    assert client.get(path, headers=_auth(tokens["refresh"])).status_code == 401
    assert client.get(path, headers=_auth(tokens["access"])).status_code == 200
    assert client.get(path, headers=_auth(tokens["service"])).status_code == 200


def test_a_configured_but_unreadable_manifest_refuses_registry_reads(edge, monkeypatch):
    client, tokens, _ = edge
    monkeypatch.setattr(main.app.state, "trust_anchors", {}, raising=False)
    assert client.get("/types/all", headers=_auth(tokens["access"])).status_code == 503
