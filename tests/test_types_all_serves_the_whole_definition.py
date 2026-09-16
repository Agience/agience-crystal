"""`GET /types/all` — the registry as a client hydrating its own needs to receive it.

Facet ships generic viewers and learns every server-owned type at runtime: which viewer to use,
`ui.record`, `resource_uri`. All of that lives in what a persona pushed, and nowhere else. So the
one thing this route must not do is serve a summary that looks right.

`/resolve` IS THAT SUMMARY. It returns operations and the describer — enough to dispatch an
artifact already in hand, and missing the `ui` block entirely. A bulk route built by looping
`/resolve` would answer 200 with well-formed entries for every type and leave every viewer
unresolved, which presents as "this node has no viewers" rather than as a wrong projection.
`test_the_ui_block_survives` is the guard, and it fails against exactly that mistake.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from crystal.main import app
from crystal.type_registry import TypeRegistry

CHAT = {
    "content_type": "application/vnd.agience.chat+json",
    "ui": {"viewer": "chat", "record": "title", "resource_uri": "ui://aria/chat.html"},
    "describer": {"kind": "builtin"},
    "operations": {"invoke": {"dispatch": {"kind": "mcp_tool", "tool_ref": "chat"}}},
}


def _client_with(*definitions, server="aria") -> TestClient:
    """A client over a registry holding exactly these definitions.

    The registry is replaced rather than added to: `app.state.types` is process-wide, and a test
    that appends passes or fails depending on what ran before it.
    """
    registry = TypeRegistry()
    for definition in definitions:
        assert registry.register(definition, server=server), definition.get("content_type")
    app.state.types = registry
    return TestClient(app)


def test_an_empty_registry_is_an_empty_list_not_an_error():
    # No persona has registered. The client falls back to its build-time primitives, so this is a
    # real answer and not a failure — `/health` reports the same state as `personas_known: 0`.
    body = _client_with().get("/types/all").json()
    assert body == {"types": []}


def test_a_registered_type_is_returned_with_its_server():
    body = _client_with(CHAT).get("/types/all").json()
    assert [t["content_type"] for t in body["types"]] == ["application/vnd.agience.chat+json"]
    assert body["types"][0]["server"] == "aria"


def test_the_ui_block_survives():
    """The whole point: a viewer cannot be resolved from the `/resolve` projection."""
    body = _client_with(CHAT).get("/types/all").json()
    definition = body["types"][0]["definition"]
    assert definition["ui"] == CHAT["ui"], "the pushed `ui` block must reach the client intact"
    assert definition["operations"], "operations travel too — one read, not two"


def test_resolve_does_not_carry_what_this_route_exists_for():
    """The projection this route must not become, asserted rather than described.

    If `/resolve` ever grows `ui`, this fails — and that is the moment to ask whether the bulk
    route should still exist, rather than discovering later that two routes disagree.
    """
    client = _client_with(CHAT)
    resolved = client.get("/resolve/application/vnd.agience.chat+json").json()
    assert "ui" not in resolved
    assert "ui" in client.get("/types/all").json()["types"][0]["definition"]


def test_a_malformed_definition_never_reaches_a_client():
    """`register` drops what it cannot parse, so the catalogue carries only usable entries."""
    registry = TypeRegistry()
    assert registry.register({"no_content_type": True}) is False
    app.state.types = registry
    assert TestClient(app).get("/types/all").json() == {"types": []}
