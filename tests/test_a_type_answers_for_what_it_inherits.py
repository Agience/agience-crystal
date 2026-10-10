"""`inherits` is resolved: a type answers for the operations it declares and, for each one it does
not, for the first type it inherits from that does.

The workspace type declares only `create` and names the collection type in `inherits`; the
collection type is a builtin the gateway loads itself. Before both halves existed — the builtin
load and the walk — every read, update and delete on a workspace answered 404
`Operation ... is not declared`, and so did every operation on a persona type that inherits.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from crystal.main import app
from crystal.type_registry import BUILTIN_SERVER, TypeRegistry

TYPES = Path(__file__).resolve().parent.parent / "src" / "types"
WORKSPACE = "application/vnd.agience.workspace+json"
COLLECTION = "application/vnd.agience.collection+json"
#: Ophan's market-series type, as chorus ships it, cut to what registration reads: no operations, the
#: collection type in `inherits`. Held here, so this suite needs no sibling checkout.
MARKET_SERIES = {
    "content_type": "application/vnd.agience.market-series+json",
    "version": 1,
    "inherits": [COLLECTION],
    "context_schema": {"symbol": {"type": "string", "index": ["lexical"], "mutable": False}},
    "ui": {"id": "market-series", "label": "Market Series", "is_container": True},
}


def _builtins() -> TypeRegistry:
    registry = TypeRegistry()
    assert registry.load_builtin(TYPES) > 0
    return registry


def test_the_builtin_collection_and_workspace_are_loaded():
    registry = _builtins()
    assert registry.resolve(COLLECTION).server == BUILTIN_SERVER
    assert registry.resolve(WORKSPACE).server == BUILTIN_SERVER


def test_a_workspace_reads_updates_and_deletes_as_a_collection_does():
    registry = _builtins()
    for op in ("read", "update", "delete"):
        got = registry.resolve_operation(WORKSPACE, op)
        assert got is not None, f"a workspace does not answer for {op!r}"
        assert got is registry.resolve_operation(COLLECTION, op)


def test_a_type_s_own_operation_wins_over_the_inherited_one():
    registry = _builtins()
    own = registry.resolve_operation(WORKSPACE, "create")
    assert own is registry.resolve(WORKSPACE).operations["create"]


def test_an_operation_nothing_in_the_lineage_declares_is_still_absent():
    assert _builtins().resolve_operation(WORKSPACE, "commit") is None


def test_a_persona_type_inherits_a_builtin():
    """Ophan's market series declares no operations and inherits the collection type."""
    registry = _builtins()
    definition = json.loads(json.dumps(MARKET_SERIES))
    assert registry.register(definition, server="ophan")
    assert registry.resolve_operation(definition["content_type"], "read") is not None


def test_a_persona_re_registering_keeps_the_builtins_and_its_own_type_wins():
    registry = _builtins()
    mine = {"content_type": COLLECTION, "operations": {"read": {"dispatch": {"kind": "mcp_tool",
                                                                              "tool_ref": "x"}}}}
    registry.register(mine, server="aria")
    assert registry.resolve(COLLECTION).server == "aria"
    registry.remove_server("aria")
    assert registry.resolve(COLLECTION).server == BUILTIN_SERVER


def test_a_cycle_in_inherits_terminates():
    registry = TypeRegistry()
    registry.register({"content_type": "a/x", "inherits": ["b/x"], "operations": {}})
    registry.register({"content_type": "b/x", "inherits": ["a/x"],
                       "operations": {"read": {"dispatch": {"kind": "artifact_crud"}}}})
    assert registry.resolve_operation("a/x", "read") is not None
    assert registry.resolve_operation("a/x", "write") is None


def test_a_missing_builtin_directory_loads_nothing(tmp_path):
    assert TypeRegistry().load_builtin(tmp_path / "absent") == 0


def test_resolve_reports_inherited_operations():
    app.state.types = _builtins()
    body = TestClient(app).get(f"/resolve/{WORKSPACE}").json()
    assert {"create", "read", "update", "delete"} <= set(body["operations"]), body
