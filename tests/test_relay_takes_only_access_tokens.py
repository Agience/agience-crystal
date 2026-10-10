"""The relay takes an Origin *access* token as the caller, and nothing else Origin signs.

Origin signs its refresh, email-verification and password-reset tokens with the same key as an
access token, so a signature check alone accepts all of them. Both relay entry points — gateway
dispatch (`user_id_resolver`) and the desktop socket (`/relay/v1/connect`) — are checked here with
tokens minted in this file against a manifest this file writes, each beside a positive control.
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

from crystal import host
from crystal.gateway_middleware import UniversalMCPGatewayMiddleware

ISSUER = "https://platform.test"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def _noop_asgi(scope, receive, send):
    return None


class _Persona:
    def __init__(self, name):
        self.name, self.role, self.endpoint = name, "", f"/{name}/mcp"
        self.mount_app, self.session_manager = _noop_asgi, None

    async def startup(self):
        return None

    def register(self, register_fn=None):
        return True


@pytest.fixture()
def sign(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
    n = key.public_key().public_numbers()
    jwk = {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "origin-1",
           "n": _b64u(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
           "e": _b64u(n.e.to_bytes((n.e.bit_length() + 7) // 8, "big"))}
    (tmp_path / "authority.manifest.json").write_text(json.dumps({
        "artifact_id": "test-authority", "issuer": ISSUER,
        "trust_anchors": {"origin": {"jwks": {"keys": [jwk]}}},
    }))
    monkeypatch.setenv("KEYS_DIR", str(tmp_path))
    from prism.trust import authority_trust
    authority_trust.reset_authority_manifest_for_tests()

    def _sign(**claims):
        now = int(time.time())
        body = {"iss": ISSUER, "sub": "alice", "aud": ISSUER, "iat": now, "exp": now + 300}
        body.update(claims)
        return jose_jwt.encode(body, pem, algorithm="RS256", headers={"kid": "origin-1"})

    yield _sign
    authority_trust.reset_authority_manifest_for_tests()


@pytest.fixture()
def app():
    return host.build_app([_Persona("zeta")])


def _resolver(app):
    for m in app.user_middleware:
        if m.cls is UniversalMCPGatewayMiddleware:
            return m.kwargs["user_id_resolver"]
    raise AssertionError("the gateway middleware is not installed")


def _scope(token):
    return {"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]}


_NOT_ACCESS = {
    "refresh": {"token_type": "refresh"},
    "email_verify": {"token_type": "email_verify"},
    "pwd_reset": {"token_type": "pwd_reset"},
    "server": {"aud": "agience", "principal_type": "server"},
    "other-audience": {"aud": "https://another-authority.test"},
    "delegation-for-another-server": {"aud": "mantle", "principal_type": "delegation",
                                      "act": {"sub": "platform-mail"}},
}


def test_dispatch_takes_an_access_token(app, sign):
    assert _resolver(app)(_scope(sign())) == "alice"


def test_dispatch_takes_a_delegation_minted_for_its_own_persona(app, sign):
    tok = sign(aud="agience-server-zeta", principal_type="delegation",
               act={"sub": "agience-server-zeta"})
    assert _resolver(app)(_scope(tok)) == "alice"


@pytest.mark.parametrize("claims", list(_NOT_ACCESS.values()), ids=list(_NOT_ACCESS))
def test_dispatch_refuses_what_is_not_an_access_token(app, sign, claims):
    resolve = _resolver(app)
    assert resolve(_scope(sign())) == "alice", "positive control failed FIRST"
    assert resolve(_scope(sign(**claims))) is None


class _Socket:
    """What `relay_connect` touches of a WebSocket: its headers and `close`.

    The handler is called directly rather than through a client. Routed, the endpoint refuses every
    connection with 1008 before it runs: `host.py` has `from __future__ import annotations` and
    imports `WebSocket` inside `build_app`, so FastAPI cannot resolve the annotation and reads
    `websocket` as a required query parameter. The auth below is therefore what the socket will
    apply once that is fixed; it is not reachable in production today."""

    def __init__(self, token):
        self.headers = {"authorization": "Bearer " + token}
        self.closed = None

    async def close(self, code=1000, reason=""):
        self.closed = code


def _socket_accepts(app, token, monkeypatch) -> bool:
    import asyncio
    from crystal import relay_manager

    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/relay/v1/connect")
    connected = []

    class _Stop(Exception):
        pass

    async def _connect(ws, user_id):
        connected.append(user_id)
        raise _Stop

    monkeypatch.setattr(relay_manager.get_relay_manager(), "connect", _connect)
    ws = _Socket(token)
    try:
        asyncio.run(endpoint(ws))
    except _Stop:
        pass
    if connected:
        assert connected == ["alice"] and ws.closed is None
        return True
    assert ws.closed == 4401, ws.closed
    return False


@pytest.mark.parametrize("claims", list(_NOT_ACCESS.values()) + [
    {"aud": "agience-server-zeta", "principal_type": "delegation",
     "act": {"sub": "agience-server-zeta"}}],
    ids=list(_NOT_ACCESS) + ["persona-delegation"])
def test_the_desktop_socket_takes_only_the_users_access_token(app, sign, claims, monkeypatch):
    assert _socket_accepts(app, sign(), monkeypatch), "positive control failed FIRST"
    assert not _socket_accepts(app, sign(**claims), monkeypatch)
