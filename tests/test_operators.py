"""The operator host's contract, pinned: schema, vocabulary, registry, pattern × capability."""
import pytest

from crystal.operator_schema import CAPABILITY_KINDS, OPERATOR_CONTENT_TYPE, spec_hash, validate_operator
from crystal.registry import OperatorRegistry


def _ok(**kw):
    d = {"id": "op.test.echo", "content_type": OPERATOR_CONTENT_TYPE,
         "offer": "echo the input", "needs": ["compute.local"],
         "content": "identity transform", "created_by": "person-1"}
    d.update(kw)
    return d


class _FakeMantle:
    def __init__(self):
        self.docs = {}

    def put_artifact(self, doc):
        self.docs[doc["id"]] = dict(doc)
        return doc

    def list_artifacts(self, content_type=None):
        return [d for d in self.docs.values()
                if content_type is None or d.get("content_type") == content_type]


# ── schema ───────────────────────────────────────────────────────────────────
def test_valid_definition_passes():
    assert validate_operator(_ok()) == []


def test_unknown_capability_kind_fails_loudly():
    probs = validate_operator(_ok(needs=["compute.local", "telepathy"]))
    assert any("unknown capability kind" in p for p in probs)


def test_pattern_must_exist():
    probs = validate_operator(_ok(content="", content_ref=None))
    assert any("pattern absent" in p for p in probs)


def test_id_prefix_and_required_fields():
    assert any("op." in p for p in validate_operator(_ok(id="math.add")))
    assert any("created_by" in p for p in validate_operator(_ok(created_by="")))


def test_spec_hash_pins_the_spec_not_the_evidence():
    a, b = _ok(), _ok()
    b["invocations"] = 500                       # evidence must not move the pin
    assert spec_hash(a) == spec_hash(b)
    c = _ok(offer="echo the input LOUDLY")       # a changed spec is a different operator
    assert spec_hash(a) != spec_hash(c)


def test_vocabulary_separates_read_only_net():
    # the read-only external-operator rule is a CAPABILITY KIND, not a convention
    assert "net.get" in CAPABILITY_KINDS and "net.request" in CAPABILITY_KINDS


# ── registry ─────────────────────────────────────────────────────────────────
def test_register_upserts_and_carries_NO_spec_hash_side_car():
    """Registration must not persist a derived field. A stored `spec_hash` that equals
    `spec_hash(stored)` is a side-car: a field whose only correct value is one that can always be
    derived, and whose only possible incorrect value is a stale one.

    A changed spec is still a different operator — enforced by asking the question of the spec at
    the moment it matters (`evolution.preserve_fitness`, `push._upsert`) rather than by persisting
    the answer."""
    m = _FakeMantle()
    reg = OperatorRegistry(m)
    stored = reg.register(_ok())

    assert "spec_hash" not in stored, "registration must not persist a derived address"
    assert m.docs["op.test.echo"]["content_type"] == OPERATOR_CONTENT_TYPE
    # The address is still ASKABLE, and still discriminates behaviour — derived, not carried.
    assert spec_hash(stored) == spec_hash(_ok())
    assert spec_hash(stored) != spec_hash(_ok(offer="echo the input LOUDLY"))


def test_register_rejects_invalid():
    reg = OperatorRegistry(_FakeMantle())
    with pytest.raises(ValueError):
        reg.register(_ok(needs=["telepathy"]))


def test_register_many_upserts_a_manifest():
    # There is no shared catalog: the registry registers a list of definitions — a persona's
    # manifest, or the concatenation of all persona manifests the host collects.
    m = _FakeMantle()
    manifest = [_ok(id="op.a.one", offer="alpha"), _ok(id="op.b.two", offer="beta")]
    assert OperatorRegistry(m).register_many(manifest) == 2
    assert "op.a.one" in m.docs and "op.b.two" in m.docs


def test_runnable_on_is_pattern_times_capability():
    d = _ok(needs=["compute.local", "store.read"])
    assert OperatorRegistry.runnable_on(d, ["compute.local", "store.read", "ui.render"])
    assert not OperatorRegistry.runnable_on(d, ["compute.local"])       # missing store.read
    assert not OperatorRegistry.runnable_on(d, [])


def test_find_by_offer_keyed_discovery():
    m = _FakeMantle()
    reg = OperatorRegistry(m)
    reg.register_many([_ok(id="op.math.gcd", offer="greatest common divisor of two integers"),
                       _ok(id="op.math.add", offer="sum of two numbers")])
    hits = reg.find_by_offer("greatest common divisor")
    assert [h["id"] for h in hits] == ["op.math.gcd"]
