"""The proxy tekton tells an upstream who the caller is from the connection, never from the caller.

`crystal/host.py` forwards a declared-upstream host (`CRYSTAL_UPSTREAMS`) to a loopback service —
origin, mantle. Those services run under uvicorn, which trusts `X-Forwarded-For` from 127.0.0.1 and
takes the rightmost untrusted entry as `request.client.host`. Origin keys its per-client rate limits
on that address. So whatever `X-Forwarded-For` the proxy sends IS the client address origin sees,
and a header the caller wrote must not be it.

Invariants, failure mode first:

  Caller's claim dropped — a client-sent `X-Forwarded-For`, `X-Real-IP` or `Forwarded` does not
                           reach the upstream. Otherwise any caller chooses its own rate-limit key.
  Peer address sent      — the upstream receives `X-Forwarded-For: <request.client.host>`, the
                           address this process resolved for the connection.
  Scheme from the request — `X-Forwarded-Proto` is this process's resolved scheme, not the
                           caller's header.
  Host kept              — the Host header still reaches the upstream unchanged; origin's token
                           `iss`/`aud` depend on it.
"""
from __future__ import annotations

import importlib

import httpx
import pytest
from fastapi.testclient import TestClient

_PEER = "203.0.113.9"          # TEST-NET-3: the address the connection comes from
_CLAIM = "198.51.100.77"       # TEST-NET-2: the address the caller asserts in its headers


class _Captured:
    """Stands in for `httpx.AsyncClient` and records the one request the proxy makes."""

    seen: dict = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, content=None):
        _Captured.seen = {"method": method, "url": url,
                          "headers": {k.lower(): v for k, v in (headers or {}).items()}}
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})


def _proxied(monkeypatch, headers):
    monkeypatch.setenv("CRYSTAL_HOST_DOMAIN", "agience.ai")
    monkeypatch.setenv("CRYSTAL_UPSTREAMS", "origin=127.0.0.1:8080")
    monkeypatch.delenv("CRYSTAL_APEX_PERSONA", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _Captured)
    _Captured.seen = {}
    host = importlib.import_module("crystal.host")
    c = TestClient(host.build_app([]), client=(_PEER, 50000))
    r = c.get("/auth/whoami", headers={"Host": "origin.agience.ai", **headers})
    assert r.status_code == 200, r.text
    assert _Captured.seen, "the request never reached the upstream — the fixture measured nothing"
    return _Captured.seen["headers"]


def test_a_client_sent_forwarded_for_does_not_reach_the_upstream(monkeypatch):
    """Fails if the caller's `X-Forwarded-For` is passed through: origin trusts the header from
    loopback, so the caller would choose the address origin rate-limits on."""
    fwd = _proxied(monkeypatch, {"X-Forwarded-For": _CLAIM})
    assert _CLAIM not in fwd.get("x-forwarded-for", ""), fwd


@pytest.mark.parametrize("name,value", [
    ("X-Real-IP", _CLAIM),
    ("Forwarded", f"for={_CLAIM};proto=https"),
])
def test_the_other_client_address_headers_are_dropped(monkeypatch, name, value):
    """Fails if `X-Real-IP` or `Forwarded` survives: an upstream or a proxy added later that reads
    either one would take the caller's claim as the client address."""
    fwd = _proxied(monkeypatch, {name: value})
    assert name.lower() not in fwd, fwd


def test_the_upstream_receives_the_peer_address(monkeypatch):
    """Fails if the proxy appends nothing, or appends to the caller's list: the upstream must see
    exactly the connection's peer as the one forwarded address."""
    fwd = _proxied(monkeypatch, {"X-Forwarded-For": _CLAIM})
    assert fwd.get("x-forwarded-for") == _PEER, fwd


def test_the_scheme_is_the_requests_own_and_the_host_is_kept(monkeypatch):
    """Fails if the caller's `X-Forwarded-Proto` is passed through in place of the scheme this
    process resolved, or if the Host header is rewritten on the way out."""
    fwd = _proxied(monkeypatch, {"X-Forwarded-Proto": "https"})
    assert fwd.get("x-forwarded-proto") == "http", fwd
    assert fwd.get("host") == "origin.agience.ai", fwd
