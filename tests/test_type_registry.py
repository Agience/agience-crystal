"""Unit tests for the type registry + persona registration model."""
from crystal.type_registry import TypeRegistry, parse_type_def, normalize_ct
from crystal.topology import Topology


def _collection_def():
    return {
        "content_type": "application/vnd.agience.collection+json",
        "operations": {
            "commit": {
                "dispatch": {"kind": "native", "target": "collection.commit"},
                "requires_grant": "update",
                "emits": [{"event": "artifact.committed", "phase": "after"}],
                "audit": True,
            },
            "render": {
                "dispatch": {"kind": "mcp_tool", "target": "aria.render"},
                "requires_grant": "read",
            },
        },
        "describer": {"kind": "mcp_tool", "target": "astra.describe"},
    }


def test_parse_type_def():
    td = parse_type_def(_collection_def(), server="aria")
    assert td is not None
    assert td.content_type == "application/vnd.agience.collection+json"
    assert td.server == "aria"
    assert set(td.operations) == {"commit", "render"}
    commit = td.operations["commit"]
    assert (commit.kind, commit.target, commit.requires_grant, commit.audit) == (
        "native", "collection.commit", "update", True)
    assert commit.emits == [{"event": "artifact.committed", "phase": "after"}]
    assert td.operations["render"].kind == "mcp_tool"
    assert td.describer == {"kind": "mcp_tool", "target": "astra.describe"}


def test_registry_register_and_resolve():
    reg = TypeRegistry()
    assert reg.register(_collection_def(), server="aria") is True
    assert reg.known() == ["application/vnd.agience.collection+json"]

    op = reg.resolve_operation("application/vnd.agience.collection+json", "commit")
    assert op is not None and op.kind == "native"
    assert reg.resolve_operation("application/vnd.agience.collection+json", "nope") is None
    assert reg.resolve("text/plain") is None
    assert reg.describer_for("application/vnd.agience.collection+json")["target"] == "astra.describe"


def test_register_normalizes_and_rejects_bad():
    reg = TypeRegistry()
    assert reg.register({"content_type": "Text/Plain ; q=1", "operations": {}}) is True
    assert reg.resolve("text/plain") is not None
    assert reg.register({"operations": {}}) is False        # no content_type
    assert reg.register("not-a-dict") is False


def test_remove_server():
    reg = TypeRegistry()
    reg.register(_collection_def(), server="aria")
    reg.register({"content_type": "x/y", "operations": {}}, server="lumen")
    assert reg.remove_server("aria") == 1
    assert reg.known() == ["x/y"]


def test_topology():
    topo = Topology()
    topo.register("aria", "http://chorus:8082/aria/mcp", client_id="agience-server-aria")
    rec = topo.resolve("aria")
    assert rec is not None and rec.endpoint == "http://chorus:8082/aria/mcp"
    assert topo.known() == ["aria"]
    topo.remove("aria")
    assert topo.resolve("aria") is None


def test_normalize_ct():
    assert normalize_ct("  TEXT/Plain ; q=1 ") == "text/plain"
    assert normalize_ct("") == ""
