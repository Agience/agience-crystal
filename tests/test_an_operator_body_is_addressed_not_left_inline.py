"""An operator artifact's body goes to the CAS; the row carries the address.

RULING 2 [John, 2026-08-26 — against exempting the operator catalogue]. Four registrars across
three personas each wrote `store.put_artifact(evolution.preserve_fitness(store, {...}))` with an
inline `content`, and none addressed the body. All 25 rows
`data_integrity_check.artifacts_holding_inline_plaintext` reports on 71/home are theirs —
`op.math.*` 13, `op.dev.*` 8, `op.describe.*` 3, `op.fetch.*` 1 — and 18 of 18 `register_*_operators`
functions write the same way, so 14 more were latent.

Addressing, not sealing, and that distinction cost two reverts. `encrypt_artifact_content` goes
through the key oracle, which needs an acting principal a registrar does not have.
`corpus/stage0_sources._sealed` had already written this down — *"moving it into
`put_artifact`/`put_many` fails every ingest write"* — and two attempts to do exactly that broke 113
tests (69 mantle, 44 ember) before the note was found. `put_content` keys off the KEYS DIRECTORY
instead, so it works with no principal in scope.

THE STORE IS DUCK-TYPED. `put_operator` asks for `address_inline` by name rather than importing
mantle: chorus does not depend on mantle and must not gain that edge for four lines of body-moving.
A caller holding only the `ArtifactStore` face — every chorus test does — gets the old behaviour,
which is why no caller broke.
"""
from __future__ import annotations

from crystal import evolution

OPERATOR_CT = "application/vnd.agience.operator+json"


class _Arts:
    """The bare artifacts face — what every chorus test passes."""

    def __init__(self):
        self.rows = {}

    def get_artifact(self, artifact_id):
        return self.rows.get(artifact_id)

    def put_artifact(self, doc):
        self.rows[doc["id"]] = doc
        return doc


class _Store:
    """A store that CAN address, standing in for `LocalStore` without needing a lattice."""

    def __init__(self):
        self.artifacts = _Arts()
        self.addressed = []

    def address_inline(self, docs):
        for d in docs:
            body = d.get("content")
            if not body or d.get("content_ref"):
                continue
            d["content_ref"] = "cas/" + ("%064x" % abs(hash(body)))
            d["size"] = len(body)
            d.pop("content", None)
            self.addressed.append(d["id"])
        return docs


def _doc(i="op.math.add", body="adds two numbers"):
    return {"id": i, "content_type": OPERATOR_CT, "state": "committed",
            "collection_id": "stage.system", "content": body, "created_by": "ember-local"}


# ── the behaviour ────────────────────────────────────────────────────────────────────────────────

def test_a_store_that_can_address_leaves_no_inline_body():
    s = _Store()
    evolution.put_operator(s, _doc())
    row = s.artifacts.rows["op.math.add"]
    assert not row.get("content"), "the body is still inline: %r" % (row.get("content"),)
    assert row.get("content_ref", "").startswith("cas/"), row
    assert s.addressed == ["op.math.add"]


def test_a_bare_artifacts_face_degrades_rather_than_refusing():
    """*Degrades rather than refuses* — the same trade `_sealed` states. A registrar that dropped
    its operators because no content tier was mounted would swap a confidentiality property for a
    functional one, and every chorus test passes exactly this shape."""
    a = _Arts()
    evolution.put_operator(a, _doc())
    row = a.rows["op.math.add"]
    assert row.get("content") == "adds two numbers", "the body was lost, not kept"
    assert not row.get("content_ref")


def test_an_already_addressed_doc_is_not_re_addressed():
    s = _Store()
    d = _doc()
    d["content_ref"] = "cas/already"
    evolution.put_operator(s, d)
    assert s.artifacts.rows["op.math.add"]["content_ref"] == "cas/already"
    assert s.addressed == [], "a doc that already carries an address was addressed again"


def test_a_doc_with_no_body_is_untouched():
    s = _Store()
    d = _doc()
    d.pop("content")
    evolution.put_operator(s, d)
    assert s.addressed == []
    assert "content" not in s.artifacts.rows["op.math.add"]


def test_fitness_is_still_preserved_through_the_new_writer():
    """`put_operator` REPLACED an idiom that called `preserve_fitness`; dropping it would reset
    every operator's learned fitness on each re-registration, silently."""
    import inspect

    src = inspect.getsource(evolution.put_operator)
    assert "preserve_fitness" in src, (
        "put_operator no longer carries fitness, so re-registering an operator resets its accrued "
        "evidence — the exact thing preserve_fitness exists to prevent")


def test_fitness_is_read_from_the_artifacts_face_not_the_store():
    """`preserve_fitness(store, doc)` calls `store.get_artifact(...)`, which the FULL store does
    not have — only its `.artifacts` face does. Passing the wrong one raises `AttributeError` on
    every registration the moment a caller upgrades to the full store."""
    s = _Store()
    evolution.put_operator(s, _doc())          # would raise if the full store were passed through
    assert "op.math.add" in s.artifacts.rows


def test_every_registrar_uses_it():
    """One home for how an operator artifact is written. A registrar that goes back to
    `put_artifact(preserve_fitness(...))` writes an inline body again, and only the invariant would
    notice — six hours later, on a gate that reads the live store."""
    import pathlib

    # ⛔ THE GUARD BELOW MUST CHECK THE PACKAGE, NOT ITS PARENT. This read
    # `… / "agience-chorus" / "src"` and skipped when that was absent — but the personas moved
    # into `src/agience_chorus/`, so `src/` went on existing while every path built from it
    # stopped resolving. The skip never fired and the test failed with FileNotFoundError instead,
    # which reads as a broken invariant rather than as a stale path.
    chorus = (pathlib.Path(evolution.__file__).resolve().parents[3]
              / "agience-chorus" / "src" / "agience_chorus")
    if not (chorus / "lumen").is_dir():
        import pytest
        pytest.skip("agience-chorus personas are not beside agience-crystal")

    for rel in ("lumen/arithmetic.py", "lumen/dev_ops.py", "sage/operators.py", "astra/fetch.py"):
        src = (chorus / rel).read_text(encoding="utf-8-sig")
        assert "evolution.put_operator(" in src, "%s no longer uses put_operator" % rel
        assert "put_artifact(evolution.preserve_fitness(" not in src, (
            "%s went back to the un-addressed idiom" % rel)
