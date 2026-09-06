"""The operator schema — chorus-defined, versioned. One schema, all hosts.

The operator contract belongs to crystal's schema layer, not beam's signal layer; beam stays
instrument-only.

Answers OPERATOR-ARCHITECTURE.md §7 layer 1: the capability vocabulary is defined here, as a
versioned, typed, deliberately small set. Fine enough to sandbox honestly, coarse enough that
offers actually match needs — new kinds are added here, deliberately, never invented ad-hoc by
a host (an unknown capability kind must fail loudly, not silently qualify).

An operator artifact is the pattern half of execution (pattern × capability). Its schema:

    id              str   — the operator id ("op.math.add")
    content_type    str   — OPERATOR_CONTENT_TYPE
    state           str   — "committed" (draft while authoring)
    offer           str|dict — the transformation it advertises (the discoverable claim)
    needs           list  — capability kinds it requires (from CAPABILITY_KINDS)
    content         str   — the pattern itself (spec/code) or "" with content_ref → CAS bundle
    content_ref     str?  — cas/sha256(bundle) for distributed code (the store is the package manager)
    entry           dict? — how to bind: {"kind": "mcp"|"wasm"|"js"|"py-local", ...} or
                    {"kind": "py-bundle", "bundle": "<group>", "sha256": "<ref>"} — the pattern
                    is distributed python source (definitions/bundles/<group>.json); sha256 of
                    the canonical source payload is the bundle ref (content-addressed), and a
                    host verifies it before executing
    created_by      str   — the author (person id — resolvable; provenance gates hands)
    spec_hash       str   — stamped at registration; a changed spec is a different operator

Fitness fields (invocations/verified/refuted) accrue on the artifact; they are never part of
the registered spec (evidence is earned, not declared).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

# The capability vocabulary is re-exported from its canonical home `prism.capabilities`: the prism
# is what physically offers and enforces these, so it owns the names. crystal depends on prism,
# exactly as `crystal.crystal_model` re-exports `prism.crystal_model`. Every existing
# `from crystal.operator_schema import CAPABILITY_KINDS` importer keeps working unchanged.
#
# The re-export covers the whole of `prism.capabilities.__all__`, not a subset — `OPEN_FAMILIES`
# must ship alongside `CAPABILITY_KINDS`, or an importer reaching through this shim would see the
# closed vocabulary but not the open families, and a bare membership test would silently reject
# `sensor.*`/`actuator.*`, which are valid capability kinds precisely because those families are open.
from prism.capabilities import (  # noqa: F401  (re-exported on purpose)
    CAPABILITY_KINDS,
    OPEN_FAMILIES,
    is_known_capability,
)
from prism.canonical import canonical_string as _jcs_string

SCHEMA_VERSION = "1.0.0"

OPERATOR_CONTENT_TYPE = "application/vnd.agience.operator+json"

# ── Layer 1: the capability vocabulary (versioned; grow deliberately) ─────────
# Defined in `prism.capabilities` and imported above — add new kinds there, not here. The permission
# boundary and the hardware boundary are the same boundary: each kind names something a Prism can
# physically offer AND enforce as a sandbox edge on its platform.

_REQUIRED = ("id", "content_type", "offer", "needs", "created_by")


def spec_hash(doc: Dict[str, Any]) -> str:
    """The version pin: hash of the spec (pattern + contract), not the evidence.

    A changed spec is a different operator — zero inherited fitness, zero inherited earnings.
    Deliberately excludes fitness counters, timestamps and store internals."""
    spec = {k: doc.get(k) for k in
            ("id", "offer", "needs", "content", "content_ref", "entry")}
    blob = _jcs_string(spec)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate_operator(doc: Dict[str, Any]) -> List[str]:
    """Validate an operator definition against the schema. Returns problems ([] = valid).

    Loud on unknown capability kinds: a need outside the vocabulary must fail registration,
    never silently qualify — the vocabulary grows here, deliberately."""
    problems: List[str] = []
    for k in _REQUIRED:
        if not doc.get(k):
            problems.append("missing required field: %s" % k)
    if doc.get("content_type") not in (None, OPERATOR_CONTENT_TYPE):
        problems.append("content_type must be %s" % OPERATOR_CONTENT_TYPE)
    oid = doc.get("id") or ""
    if oid and not oid.startswith("op."):
        problems.append("id must start with 'op.' (got %r)" % oid)
    needs = doc.get("needs")
    if needs is not None:
        if not isinstance(needs, list):
            problems.append("needs must be a list of capability kinds")
        else:
            for n in needs:
                # is_known_capability, not bare membership: `sensor.*`/`actuator.*` are open
                # families, so a real device — `sensor.temperature` — is valid without being
                # enumerated. Bare membership would reject exactly the plug-and-play case.
                if not is_known_capability(n):
                    problems.append("unknown capability kind: %r (the base vocabulary is versioned — "
                                    "add kinds in prism/capabilities.py, never ad-hoc; only "
                                    "sensor.*/actuator.* are open families)" % (n,))
    entry = doc.get("entry")
    if entry is not None:
        if not isinstance(entry, dict) or entry.get("kind") not in (
                "mcp", "wasm", "js", "py-local", "py-bundle"):
            problems.append("entry.kind must be one of mcp|wasm|js|py-local|py-bundle")
        elif entry.get("kind") == "py-bundle":
            # a distributed pattern is content-addressed or it is nothing: the bundle name
            # locates it, the sha256 IS its identity (the integrity gate a host checks
            # before exec). Missing either leaves the entry with no verifiable identity,
            # so validation rejects it here.
            if not entry.get("bundle"):
                problems.append("py-bundle entry needs a bundle group name")
            sha = str(entry.get("sha256") or "")
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                problems.append("py-bundle entry needs sha256 (64 lowercase hex — the bundle ref)")
    if not doc.get("content") and not doc.get("content_ref"):
        problems.append("pattern absent: provide content (inline spec) or content_ref (CAS bundle)")
    return problems
