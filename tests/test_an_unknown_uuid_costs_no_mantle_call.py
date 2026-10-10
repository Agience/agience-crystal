"""An unauthenticated caller naming UUIDs cannot make the gateway call Mantle per request.

The UUID in `/{uuid}/mcp` is the caller's choice. A persona-map miss refreshes the map from Mantle,
and a UUID that is not a persona is looked up as an artifact in Mantle. Both are bounded here:
misses refresh at most once per interval, and the artifact lookup waits for an authenticated user,
since the only kinds it can find either need one (relay) or are refused (external).
"""
from __future__ import annotations

import uuid as _uuid
from unittest.mock import MagicMock

import pytest

from crystal import gateway_middleware


def _scope(path, token=None):
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return {"type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
            "headers": headers}


async def _drive(mw, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    return sent


async def _app(scope, receive, send):
    raise AssertionError("an unknown UUID reached the inner app")


def _mw(client, *, resolver):
    pmap = gateway_middleware.PersonaMap(gateway_client_factory=lambda: client)
    return gateway_middleware.UniversalMCPGatewayMiddleware(
        _app, persona_map=pmap, gateway_client_factory=lambda: client,
        local_persona_slugs={"aria"}, relay_manager=MagicMock(), user_id_resolver=resolver)


def _client():
    client = MagicMock()
    client.list_personas.return_value = [{"slug": "aria", "artifact_id": str(_uuid.uuid4())}]
    client.get_artifact.return_value = None
    return client


@pytest.mark.asyncio
async def test_many_unknown_uuids_cost_one_refresh_and_no_lookup():
    client = _client()
    mw = _mw(client, resolver=lambda scope: None)          # nobody is authenticated
    for _ in range(50):
        sent = await _drive(mw, _scope(f"/{_uuid.uuid4()}/mcp"))
        assert sent[0]["status"] == 401
    # one load, one miss-triggered refresh; then the interval holds
    assert client.list_personas.call_count == 2
    assert client.get_artifact.call_count == 0


@pytest.mark.asyncio
async def test_an_authenticated_caller_still_reaches_the_lookup():
    """The positive control: the gate stops the unauthenticated, not the lookup itself."""
    client = _client()
    mw = _mw(client, resolver=lambda scope: "alice" if scope["headers"] else None)
    sent = await _drive(mw, _scope(f"/{_uuid.uuid4()}/mcp", token="t"))
    assert sent[0]["status"] == 404
    assert client.get_artifact.call_count == 1


def test_a_miss_after_the_interval_refreshes_again(monkeypatch):
    client = _client()
    pmap = gateway_middleware.PersonaMap(gateway_client_factory=lambda: client)
    clock = [1000.0]
    monkeypatch.setattr(gateway_middleware.time, "monotonic", lambda: clock[0])
    assert pmap.slug_for_uuid("nope-1") is None
    assert pmap.slug_for_uuid("nope-2") is None
    assert client.list_personas.call_count == 2
    clock[0] += getattr(gateway_middleware, "_MISS_REFRESH_INTERVAL_S", 30.0) + 1
    assert pmap.slug_for_uuid("nope-3") is None
    assert client.list_personas.call_count == 3
