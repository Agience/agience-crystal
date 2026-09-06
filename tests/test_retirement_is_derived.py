"""Retirement is derived, not typed — these tests fail if it stops being.

    retire  iff  fitness(op) + z(far) * fitness_resolution(op) < prior_fitness()

with `prior_fitness()` computed from the fitness weights and `fitness_resolution()` computed from
the operator's own Beta posterior.

A derivation that returns the same number regardless of its inputs is a constant wearing a
function, and a test that only checks today's value would pass equally against a constant. Every
test below perturbs an input and asserts the derived quantity moves.
`test_the_prior_is_not_a_hidden_constant` is the one that would catch a regression to a fixed
return value.
"""
from __future__ import annotations

import pytest

from crystal import evolution as E


class _Capture:
    def __init__(self):
        self.docs = {}

    def get_artifact(self, aid):
        return self.docs.get(aid)

    def put_artifact(self, doc):
        self.docs[doc["id"]] = dict(doc)
        return doc


def _op(oid="op.x", *, verified=0, refuted=0, invocations=0, uses=0):
    return {"id": oid, "content_type": E.OPERATOR_CONTENT_TYPE, "state": "committed",
            "verified": verified, "refuted": refuted,
            "invocations": invocations, "output_uses": uses}


# ── the reference the decision is made against ────────────────────────────────────

def test_the_prior_is_not_a_hidden_constant(monkeypatch):
    """`prior_fitness()` tracks the fitness weights: pinned to 0.4 by hand, it would pass a value
    check and fail this one."""
    base = E.prior_fitness()
    assert base == pytest.approx(E.W_VERIFIED * 0.5)

    monkeypatch.setattr(E, "W_VERIFIED", 0.5)
    monkeypatch.setattr(E, "W_DEMAND", 0.5)
    assert E.prior_fitness() != base, (
        "changing the fitness weights left the agnostic prior where it was — it is a constant, "
        "not a property of the mixture it claims to describe"
    )
    assert E.prior_fitness() == pytest.approx(0.25)


def test_the_prior_is_the_fitness_of_an_operator_with_no_record():
    """It is not merely near the no-evidence score; it is that score, because it is computed the
    same way."""
    assert E.prior_fitness() == E.fitness({})
    assert E.prior_fitness() == E.fitness(_op())


# ── the resolution the decision is made at ────────────────────────────────────────

def test_resolution_tightens_with_evidence():
    """It shrinks as 1/sqrt(n). A resolution that ignored the counts would make the whole test a
    fixed offset from the prior — i.e. a floor again."""
    coarse = E.fitness_resolution(_op(verified=1, refuted=1))
    finer = E.fitness_resolution(_op(verified=50, refuted=50))
    assert finer < coarse
    assert E.fitness_resolution(_op(verified=500, refuted=500)) < finer


def test_resolution_tracks_the_verified_weight(monkeypatch):
    before = E.fitness_resolution(_op(verified=3, refuted=4))
    monkeypatch.setattr(E, "W_VERIFIED", 0.4)
    assert E.fitness_resolution(_op(verified=3, refuted=4)) != before


# ── the decision itself ───────────────────────────────────────────────────────────

def test_an_operator_with_no_record_is_never_retired():
    """The `min_invocations` floor is not typed — it falls out of the arithmetic, so the test is
    on the arithmetic."""
    assert not E.is_unfit(_op())
    for far in (0.5, 0.05, 1e-6):
        assert not E.is_unfit(_op(), far=far), (
            f"an operator reality has said NOTHING about was condemned at far={far}"
        )


def test_one_and_two_refutations_spare_but_three_can_condemn():
    """The gap for 0/1/2 consecutive refutations is 0.780 / 0.577 / 0.455 against a prior of
    0.400, so the third is the first that can condemn. This pins the derived evidence floor: if
    the derivation were replaced by a constant, this boundary would move or vanish."""
    assert not E.is_unfit(_op(refuted=1, invocations=1))
    assert not E.is_unfit(_op(refuted=2, invocations=2))
    assert E.is_unfit(_op(refuted=3, invocations=3))


def test_the_verdict_tracks_the_stated_far():
    """A tighter false-alarm level must condemn strictly fewer operators. If `far` does not move
    the verdict anywhere, it is decoration on a fixed rule."""
    borderline = _op(refuted=3, invocations=3)
    assert E.is_unfit(borderline, far=0.05)
    assert not E.is_unfit(borderline, far=1e-4), (
        "sharpening the false-alarm level 500x did not spare a borderline operator — the level "
        "is not entering the decision"
    )


def test_ambiguous_evidence_is_spared_where_the_old_floor_condemned():
    """8 verified / 12 refuted with 20 invocations scores 0.327, close enough to the prior that its
    own evidence cannot distinguish it, so it is spared rather than condemned."""
    op = _op(verified=8, refuted=12, invocations=20)
    assert E.fitness(op) < 0.35, "fixture no longer reproduces the case the old floor condemned"
    assert not E.is_unfit(op)


def test_clear_failure_is_retired_below_the_old_evidence_bar():
    """5 refutations, 0 successes, 5 invocations: unambiguous evidence for retirement, and it
    condemns even though invocations stay under 20 — there is no invocation-count floor to clear."""
    op = _op(verified=0, refuted=5, invocations=5)
    assert op["invocations"] < 20
    assert E.is_unfit(op)


def test_retire_and_sweep_apply_the_derived_verdict():
    s = _Capture()
    s.put_artifact(_op("op.bad", refuted=6, invocations=6))
    s.put_artifact(_op("op.new"))
    s.put_artifact(_op("op.mixed", verified=8, refuted=12, invocations=20))

    assert E.retire(s, "op.bad") is True
    assert s.get_artifact("op.bad")["state"] == "archived"
    assert E.retire(s, "op.new") is False
    assert E.retire(s, "op.mixed") is False
    assert E.retire(s, "op.missing") is False


def test_sweep_retire_condemns_exactly_the_unfit():
    s = _Capture()
    for doc in (_op("op.bad", refuted=9, invocations=9),
                _op("op.good", verified=30, refuted=1, invocations=31, uses=20),
                _op("op.new")):
        s.put_artifact(doc)
    s.list_artifacts = lambda **kw: list(s.docs.values())  # type: ignore[attr-defined]
    assert E.sweep_retire(s) == ["op.bad"]
    assert E.sweep_retire(s) == []          # archived operators are skipped on the second pass
