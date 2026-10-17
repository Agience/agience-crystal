"""Each server's type definitions reach Mantle, whose indexer reads a type's `offer` from them.

A persona's registration publishes that persona's types; startup publishes the builtins. A
publish never blocks or fails a registration.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from crystal import main
from crystal.topology import Topology
from crystal.type_publish import publish_types
from crystal.type_registry import BUILTIN_SERVER, TypeRegistry

TYPES = Path(__file__).resolve().parent.parent / "src" / "types"
LEXICON = {"content_type": "text/x-wordnet", "offer": {"tags": ["lemmas"]}}


class _Identity:
    def service_jwt(self, audience):
        return f"jwt-for-{audience}"


class _Mantle:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def put(self, path, body, *, token=None):
        self.calls.append((path, body, token))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _status_error(code):
    request = httpx.Request("PUT", "http://mantle/type-definitions/sage")
    return httpx.HTTPStatusError("refused", request=request,
                                 response=httpx.Response(code, request=request))


def _run(coro):
    return asyncio.run(coro)


def test_a_publish_sends_the_servers_types_as_crystal():
    mantle = _Mantle({"stored": 1, "offer_changed": ["text/x-wordnet"]})
    result = _run(publish_types(mantle, _Identity(), "sage", [LEXICON]))
    assert result["offer_changed"] == ["text/x-wordnet"]
    assert mantle.calls == [("/type-definitions/sage", {"types": [LEXICON]}, "jwt-for-mantle")]


def test_an_unreachable_mantle_is_retried():
    mantle = _Mantle(httpx.ConnectError("down"), {"stored": 1})
    result = _run(publish_types(mantle, _Identity(), "sage", [LEXICON], retry_delay=0))
    assert result == {"stored": 1} and len(mantle.calls) == 2


def test_a_refusal_is_not_retried():
    mantle = _Mantle(_status_error(403), {"stored": 1})
    assert _run(publish_types(mantle, _Identity(), "sage", [LEXICON], retry_delay=0)) is None
    assert len(mantle.calls) == 1


def test_giving_up_returns_nothing():
    mantle = _Mantle(*[httpx.ConnectError("down")] * 3)
    assert _run(publish_types(mantle, _Identity(), "sage", [LEXICON],
                              attempts=3, retry_delay=0)) is None
    assert len(mantle.calls) == 3


def test_without_an_identity_nothing_is_sent():
    mantle = _Mantle()
    assert _run(publish_types(mantle, None, "sage", [LEXICON])) is None
    assert mantle.calls == []


def test_a_registry_names_what_each_server_contributed():
    registry = TypeRegistry()
    registry.load_builtin(TYPES)
    registry.register(LEXICON, server="sage")
    registry.register({"content_type": "text/markdown", "offer": {"title": ["name"]}},
                      server="astra")
    assert registry.definitions_from("sage") == [LEXICON]
    builtins = registry.definitions_from(BUILTIN_SERVER)
    assert any(d["content_type"] == "text/markdown" for d in builtins), \
        "the builtin stays the builtin's even when a persona overrides it"
    assert all(d.get("offer") is None for d in builtins if d["content_type"] == "text/markdown")


def test_registering_publishes_that_personas_types():
    app = main.app
    app.state.types = TypeRegistry()
    app.state.topology = Topology()
    app.state.mantle = object()
    app.state.identity = _Identity()
    app.state.type_publishes = set()
    with (
        patch.object(main, "_require_platform_service"),
        patch.object(main, "publish_types", new=AsyncMock(return_value={})) as publish,
    ):
        resp = TestClient(app).post("/register", json={
            "slug": "sage", "endpoint": "http://chorus/sage/mcp", "types": [LEXICON]})
    assert resp.status_code == 200, resp.text
    publish.assert_called_once()
    args = publish.call_args.args
    assert args[2] == "sage" and args[3] == [LEXICON]
