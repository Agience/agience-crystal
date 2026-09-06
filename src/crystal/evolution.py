# Agnostic operator-fitness mechanism (crystal base library). Like `registry`/`push`, this is generic
# operator machinery with no domain knowledge — it computes fitness for any operator. The specific
# operators that use it live in chorus personas; this only provides the fitness/selection primitives.
"""Operator improvement — principled selection over observers.

Principle: every observation is recorded; the universe selects for complex observers; so operators
improve over time. An operator is an observer — it resolves structure from input to output.
Selection favours observers that resolve more structure, verifiably.

Mechanism (deterministic, gated, no LLM):
  record   every invocation (operator, verified?) + every use (retrieved to satisfy a need)
           -> the operator's track record accrues on its artifact = its mass.
  fitness  = verified, demanded coverage. Laplace-smoothed verified-rate, weighted by usage.
           (Fidelity can also be measured as resolved coherence — `ember.optics`'s K_signal read.)
  select   among operators offering the same need, the fittest serves; retire the unfit.
  vary     propose operators by inference (content+context->operator; see arithmetic.learn +
           category.infer) and composition (category generators); the gate (verify_change /
           ground-truth) admits only variants that raise fitness. You cannot adopt faster than
           you can verify — fidelity buys complexity.

This module is the record + fitness + select substrate; vary reuses category/arithmetic + the gate.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import List, Optional
from prism.canonical import canonical_string as _jcs_string

OPERATOR_CONTENT_TYPE = "application/vnd.agience.operator+json"


FITNESS_FIELDS = ("invocations", "verified", "refuted", "output_uses", "uses")


# Platform vocabulary: author ids that are processes, not people (like operator_schema's
# CAPABILITY_KINDS — a reserved-id set, agnostic). Needed by preserve_fitness's creator-clobber guard:
# a re-registration by a process author must never overwrite a resolved human creator.
#
# Read from `prism.principals`, the same source `mantle/person.py`'s set reads, so the two frozensets
# stay in step. Re-exported unchanged for existing callers.
from prism.principals import PROCESS_AUTHORS, is_process_author       # noqa: F401  (re-export)


def spec_hash(doc: dict) -> Optional[str]:
    """The content address of an operator's executable content — `(kind, spec)`, canonicalized.

    This is what makes fitness honest: fitness is evidence about a behaviour, so it must be keyed
    to the behaviour, not to the name someone happened to give it. Returns `None` for artifacts
    with no executable content (the code-backed operators registered by name), for which
    re-registration is genuinely the same operator and fitness legitimately carries.
    """
    import hashlib
    import json
    kind = doc.get("kind")
    if not kind:
        return None
    payload = _jcs_string({"kind": kind, "spec": doc.get("spec") or {}})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def put_operator(store, doc: dict) -> dict:
    """Write one operator artifact: address its body, carry its fitness, store it. Returns the doc.

    One home for "how an operator artifact is written" [ruling 2, John 2026-08-26]. Four registrars
    across three personas each spelled this as
    ``store.put_artifact(evolution.preserve_fitness(store, {...}))`` and none of them addressed the
    body — all 25 rows `artifacts_holding_inline_plaintext` reports on 71/home are theirs
    (`op.math.*` 13, `op.dev.*` 8, `op.describe.*` 3, `op.fetch.*` 1), and 18 of 18
    ``register_*_operators`` functions write the same way, so 14 more were latent.

    Addressing, not sealing. ``encrypt_artifact_content`` goes through the key oracle, which needs
    an acting principal a registrar does not have; ``corpus/stage0_sources._sealed`` had already
    recorded that in writing, and two attempts to put sealing in ``put_artifact``/``put_many`` broke
    113 tests before that note was found. Addressing keys off the KEYS DIRECTORY instead, so it works
    with no principal in scope, and the row then carries only ``content_ref`` — which every reader
    already prefers.

    THE STORE IS DUCK-TYPED, DELIBERATELY. ``store`` may be a full ``LocalStore`` or the bare
    ``ArtifactStore`` face; every chorus test passes the latter. The addressing capability is asked
    for by name (``address_inline``) rather than imported, because chorus does not depend on mantle
    and should not gain that edge for four lines of body-moving. A store without it — or without a
    content tier — keeps the body inline, which is what the row already does: *degrades rather than
    refuses*, the same trade `_sealed` states.
    """
    address = getattr(store, "address_inline", None)
    if callable(address):
        address([doc])
    arts = getattr(store, "artifacts", store)
    arts.put_artifact(preserve_fitness(arts, doc))
    return doc


def preserve_fitness(store, doc: dict) -> dict:
    """Carry an operator's accrued fitness counters onto a re-registration doc, so re-registering
    (e.g. on every serve startup) never resets its learned fitness. Also carries provenance/citation
    (GENESIS §12: no artifact loses its citation on re-registration). Idempotent.

    Fitness is carried only when the spec hash is unchanged. A redefinition with a different spec
    is a different hash, so it starts from zero evidence, exactly as a new operator would — this
    keeps fitness evidence about a behaviour rather than something a redefinition could inherit or
    forge. `spec_change` is stamped on the doc so the reset is visible rather than silent.

    Citation/provenance still carry across a spec change — those are about never losing a citation
    on re-registration (§12), which is a different concern from evidence about behaviour.
    """
    ex = store.get_artifact(doc.get("id", ""))
    if ex:
        # Both sides are computed fresh from the specs actually carried, so the comparison always
        # compares like with like and never depends on when a row happened to be written or which
        # canonicalizer wrote it. `spec_hash` is not stored anywhere (see
        # `prism.trust.opsign.sign_operator`); this is the only thing that ever needed it, and it
        # needs a question answered, not a field persisted.
        old, new = spec_hash(ex), spec_hash(doc)
        same_behaviour = (old == new)
        if same_behaviour:
            for f in FITNESS_FIELDS:
                if f in ex:
                    doc[f] = ex[f]
        else:
            # Record what the evidence was about before it was discarded. A reset that leaves no
            # trace is indistinguishable from an operator that was never exercised.
            doc["spec_change"] = {"from": old, "to": new,
                                  "discarded": {f: ex[f] for f in FITNESS_FIELDS if f in ex}}
        for f in ("cited_from", "provenance"):      # never drop a citation on re-registration
            if ex.get(f) and not doc.get(f):
                doc[f] = ex[f]
        # Never clobber a resolved creator with a process author. Every serve boot re-registers
        # operators with the default author 'ember-local' — a process, not a person (person.py
        # §5.7). Provenance is established at creation (the is_origin edge is authoritative); a
        # re-registration is not a creation, so the existing creator stands. A doc naming a real
        # (non-process) creator explicitly still wins — that is a correction, not a default.
        if ex.get("created_by") and doc.get("created_by") != ex["created_by"]:
            incoming = doc.get("created_by") or ""
            if not incoming or is_process_author(incoming):
                doc["created_by"] = ex["created_by"]
    # `spec_hash` is not stamped onto the doc: it is a question ("is this the same behaviour?"),
    # answered above by computing both sides fresh. The artifact already carries the spec, so the
    # address is always recomputable and never needs to be kept in sync with it.
    return doc


def record_invocation(store, operator_id: str, *, verified: Optional[bool] = None) -> None:
    """Log one invocation on the operator's artifact. `verified` True/False credits the gate
    outcome (passed reality / was refuted); None just counts the invocation."""
    op = store.get_artifact(operator_id)
    if not op:
        return
    op["invocations"] = int(op.get("invocations", 0)) + 1
    if verified is True:
        op["verified"] = int(op.get("verified", 0)) + 1
    elif verified is False:
        op["refuted"] = int(op.get("refuted", 0)) + 1
    store.put_artifact(op)


def record_use(store, artifact_id: str) -> None:
    """Log that an artifact was used (retrieved to answer a need) — the demand signal — and
    credit the operator that produced it, if recorded (the content-context-operator edge)."""
    a = store.get_artifact(artifact_id)
    if not a:
        return
    a["uses"] = int(a.get("uses", 0)) + 1
    store.put_artifact(a)
    producer = a.get("operator") or a.get("via")
    if producer:
        op = store.get_artifact(producer)
        if op:
            op["output_uses"] = int(op.get("output_uses", 0)) + 1
            store.put_artifact(op)


#: How the two signals are mixed into one fitness. Named rather than written inline because the
#: retirement rule below derives its decision point from them: the fitness of an operator reality
#: has said nothing about is `W_VERIFIED * 0.5`, and that is the only reference a retirement
#: decision has. Written inline, the reference could not be computed and had to be typed.
W_VERIFIED = 0.8
W_DEMAND = 0.2

#: Where the fitness scale is reported to. A display precision, not a decision — no comparison in
#: this module reads it.
_FITNESS_DP = 4


def fitness(op: dict) -> float:
    """Verified, demanded fitness in [0,1]. Laplace-smoothed verified fraction, nudged up by
    how much the operator's output is actually used. An unproven operator sits near 0.5
    (agnostic prior); reality moves it. Deterministic."""
    ver = int(op.get("verified", 0))
    ref = int(op.get("refuted", 0))
    verified_rate = (ver + 1) / (ver + ref + 2)                 # Laplace prior 0.5
    inv = int(op.get("invocations", 0))
    uses = int(op.get("output_uses", 0))
    demand = uses / (uses + inv + 1)                            # 0..~1, saturating
    return round(W_VERIFIED * verified_rate + W_DEMAND * demand, _FITNESS_DP)


def prior_fitness() -> float:
    """The fitness of an operator reality has not spoken about — the agnostic point on this scale.

    Computed by asking `fitness` itself about an operator with no record, so it cannot drift from
    the mixture it describes: the Laplace prior puts `verified_rate` at exactly 0.5 and an operator
    with no output has no demand, giving `W_VERIFIED * 0.5`.

    This is the reference a retirement decision needs, and the reason it can be a decision at all:
    "unfit" means below what no evidence would score, which is a statement about the operator, not
    a level somebody chose. Compare against this computed prior, never against a typed floor."""
    return fitness({})


def fitness_resolution(op: dict) -> float:
    """The smallest fitness difference this operator's own evidence can resolve.

    `verified_rate` is the mean of the Beta(ver+1, ref+1) posterior the Laplace prior already
    implies, so its uncertainty is that posterior's standard deviation — `sqrt(ab/((a+b)^2(a+b+1)))`
    — and no distribution is assumed that `fitness` was not already assuming. Scaling by
    `W_VERIFIED` puts it in fitness units.

    Same idiom as `lumen/enrich.py::Observer.resolution`: a difference smaller than this is not a
    smaller finding, it is a difference this evidence cannot see. Shrinks as `1/sqrt(n)`, so an
    operator earns a sharper verdict by being invoked rather than by being granted one."""
    a = int(op.get("verified", 0)) + 1
    b = int(op.get("refuted", 0)) + 1
    n = a + b
    return W_VERIFIED * math.sqrt(a * b / (n * n * (n + 1)))


#: The one stated tolerance of this module. How often is this system willing to retire an operator
#: that was actually fine? A policy choice — the same shape as a false-alarm level — and the only
#: number the retirement rule takes that is not computed from the evidence in front of it.
#:
#: A different value is right if the cost of losing a good operator changes relative to the cost of
#: keeping a bad one. Retirement here is archival, which is reversible, so 0.05 errs where the
#: cheaper mistake is.
DEFAULT_RETIREMENT_FAR = 0.05


def _all_operators(store) -> List[dict]:
    """Every operator artifact. Duck-typed on the store, honoring the typed contract of
    mantle's lattice store (`list_by_content_type(content_type, cap=…) -> (docs, exhaustive)`,
    the shape ember's stats helper consumes): the indexed fetch is accepted only when it
    reports itself exhaustive — a full page means the value was not selective (or the engine
    truncated), and the exact `list_artifacts(content_type=…)` stream runs instead. "I did
    not finish" must never be representable as "there is nothing". Stores without the typed
    method (capture/test doubles) take the plain stream — exact, and fine for the ~50-row
    operator set on any store that is not a multi-million-row corpus."""
    typed = getattr(store, "list_by_content_type", None)
    if callable(typed):
        docs, exhaustive = typed(OPERATOR_CONTENT_TYPE, cap=10_000)
        if exhaustive:
            return list(docs)
    return [a for a in store.list_artifacts(content_type=OPERATOR_CONTENT_TYPE)]


def rank_operators(store, *, prefix: Optional[str] = None) -> List[dict]:
    """Operators, fittest first — the leaderboard of observers."""
    ops = [a for a in _all_operators(store)
           if not prefix or a["id"].startswith(prefix)]
    return sorted(ops, key=fitness, reverse=True)


def select(store, candidate_ids: List[str]) -> Optional[str]:
    """Pick the fittest operator among candidates that offer the same need (variation ->
    selection). Ties broken by invocation count (more evidence), then id (stable)."""
    cands = [store.get_artifact(c) for c in candidate_ids]
    cands = [c for c in cands if c]
    if not cands:
        return None
    best = max(cands, key=lambda c: (fitness(c), int(c.get("invocations", 0)), c["id"]))
    return best["id"]


def is_unfit(op: dict, *, far: float = DEFAULT_RETIREMENT_FAR) -> bool:
    """Has reality spoken against this operator, beyond what its own evidence could produce by
    chance? The whole retirement decision, derived, in one predicate.

        retire  iff  fitness(op) + z(far) * fitness_resolution(op) < prior_fitness()

    Read it as: the operator's fitness sits below the agnostic prior by more than `z` resolutions
    of its own evidence. `z` is the normal quantile at the stated level (`statistics.NormalDist`,
    stdlib — no scipy, no table), so sharpening `far` sharpens the test with nothing to retune.

    The boundary is derived from the evidence rather than a hand-typed floor: the prior is computed
    by `prior_fitness`, and "enough evidence" is exactly the point at which the gap between an
    operator's fitness and that prior clears its own resolution.

    An operator with 0, 1, or 2 refutations and no successes is spared — the left side of the
    inequality sits at 0.780, 0.577, 0.455 against a prior of 0.400 — and the third consecutive
    refutation is the first that can condemn. So "never retire an unproven operator" is a property
    of the arithmetic, not a separate `min_invocations` branch to trust.

    It moves both ways: an ambiguous split like 8 verified / 12 refuted sits at a fitness of 0.327,
    close enough to the prior that its own evidence cannot distinguish it, so it is spared; a
    smaller but cleaner run of failures can condemn with far fewer invocations than any fixed floor
    would require. Evidence quality decides, not evidence count alone."""
    z = NormalDist().inv_cdf(1.0 - float(far))
    return fitness(op) + z * fitness_resolution(op) < prior_fitness()


def retire(store, operator_id: str, *, far: float = DEFAULT_RETIREMENT_FAR) -> bool:
    """Retire (archive) an operator reality has spoken against — the persistently unfit observer.
    Returns True if retired. Never retires an unproven operator; see :func:`is_unfit` for why that
    is now a consequence of the test rather than a separate guard."""
    op = store.get_artifact(operator_id)
    if not op or not is_unfit(op, far=far):
        return False
    op["state"] = "archived"
    store.put_artifact(op)
    return True


def sweep_retire(store, *, far: float = DEFAULT_RETIREMENT_FAR) -> List[str]:
    """Selection pressure, applied: retire every committed operator reality has spoken against, in
    one pass. Returns the ids retired. Unproven operators are always spared — you condemn an
    observer only once its own evidence can tell it apart from one nothing is known about."""
    retired: List[str] = []
    for op in _all_operators(store):                 # indexed fetch — this runs every worker tick
        if op.get("state") == "archived":
            continue
        if retire(store, op["id"], far=far):
            retired.append(op["id"])
    return retired
