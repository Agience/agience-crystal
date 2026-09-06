"""The operator capability schema — crystal's Apache common ground.

The capability vocabulary and the operator-definition contract are a shared thing every host needs
conceptually (chorus defines operators, ember's runner grounds them), so the schema lives in crystal
— the only legal common ground. These pins guard the contract: the vocabulary is versioned and grows
deliberately (an unknown kind fails loudly, never silently qualifies), the spec_hash pins the spec,
not the evidence, and the py-bundle entry is content-addressed or it is nothing.
"""
from __future__ import annotations

from crystal.operator_schema import (
    CAPABILITY_KINDS,
    OPERATOR_CONTENT_TYPE,
    spec_hash,
    validate_operator,
)


def _ok(**kw):
    d = {"id": "op.test.echo", "content_type": OPERATOR_CONTENT_TYPE,
         "offer": "echo the input", "needs": ["compute.local"],
         "content": "identity transform", "created_by": "person-1"}
    d.update(kw)
    return d


# ── validation ────────────────────────────────────────────────────────────────
def test_valid_definition_passes():
    assert validate_operator(_ok()) == []


def test_unknown_capability_kind_fails_loudly():
    probs = validate_operator(_ok(needs=["compute.local", "telepathy"]))
    assert any("unknown capability kind" in p for p in probs)


def test_open_families_are_accepted_without_being_enumerated():
    """`sensor.*`/`actuator.*` are open families. Bare `in CAPABILITY_KINDS` membership would reject
    every real device — the exact plug-and-play case — so an operator needing `sensor.temperature`
    validates even though only `sensor.capture` is listed."""
    assert validate_operator(_ok(needs=["sensor.temperature"])) == []
    assert validate_operator(_ok(needs=["actuator.relay"])) == []


def test_a_bare_family_prefix_is_not_a_capability():
    """`sensor.` names no device — the open rule must not degrade into accepting the prefix."""
    assert any("unknown capability kind" in p
               for p in validate_operator(_ok(needs=["sensor."])))


def test_needs_must_be_a_list():
    assert any("needs must be a list" in p for p in validate_operator(_ok(needs="compute.local")))


def test_pattern_must_exist():
    probs = validate_operator(_ok(content="", content_ref=None))
    assert any("pattern absent" in p for p in probs)


def test_content_ref_alone_satisfies_the_pattern():
    assert validate_operator(_ok(content="", content_ref="cas/sha256(bundle)")) == []


def test_id_prefix_and_required_fields():
    assert any("op." in p for p in validate_operator(_ok(id="math.add")))
    assert any("created_by" in p for p in validate_operator(_ok(created_by="")))


def test_wrong_content_type_rejected():
    assert any("content_type must be" in p
               for p in validate_operator(_ok(content_type="application/json")))


# ── the capability vocabulary ─────────────────────────────────────────────────
def test_vocabulary_separates_read_only_net():
    # the read-only external-operator rule is a capability kind, not a convention
    assert "net.get" in CAPABILITY_KINDS and "net.request" in CAPABILITY_KINDS


def test_vocabulary_covers_store_and_compute_edges():
    for kind in ("store.read", "store.write", "compute.local", "fs.read", "fs.write"):
        assert kind in CAPABILITY_KINDS


# ── the entry binding (how a host grounds the pattern) ─────────────────────────
def test_entry_kind_must_be_known():
    assert any("entry.kind must be one of" in p
               for p in validate_operator(_ok(entry={"kind": "docker"})))


def test_py_bundle_entry_requires_bundle_and_sha():
    probs = validate_operator(_ok(entry={"kind": "py-bundle"}))
    assert any("bundle group name" in p for p in probs)
    assert any("sha256" in p for p in probs)


def test_py_bundle_entry_rejects_bad_sha():
    probs = validate_operator(_ok(entry={"kind": "py-bundle", "bundle": "fetch", "sha256": "nothex"}))
    assert any("64 lowercase hex" in p for p in probs)


def test_py_bundle_entry_accepts_a_real_ref():
    ref = "5b905cf9761057c63b8b6005577372c7d32e70cfe91fbe35b709517d98022726"
    assert validate_operator(_ok(entry={"kind": "py-bundle", "bundle": "fetch", "sha256": ref})) == []


# ── spec_hash: the version pin ─────────────────────────────────────────────────
def test_spec_hash_pins_the_spec_not_the_evidence():
    a, b = _ok(), _ok()
    b["invocations"] = 500                       # evidence must not move the pin
    assert spec_hash(a) == spec_hash(b)
    c = _ok(offer="echo the input LOUDLY")       # a changed spec is a different operator
    assert spec_hash(a) != spec_hash(c)


def test_spec_hash_is_stable_hex():
    h = spec_hash(_ok())
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
