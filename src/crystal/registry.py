"""The operator registry — definitions live in chorus, artifacts live in Mantle.

Operators are organons — the invoked instruments (``op.*`` abbreviates organon); this registry
is the organon registry (OPERATOR-ARCHITECTURE §12).

Chorus defines operators (the schema + this registry); Mantle stores them (operators are
artifacts — provenance, grants, fitness, mesh distribution all come free). The registry is a
thin, honest layer: validate against the schema, stamp `spec_hash`, upsert via the mantle
client, and answer discovery questions (offers matching a need; needs satisfiable by a host's
capability manifest).

No routing here: routing (the least-action discharge) is a separate concern with its own
measured impedance — see OPERATOR-ARCHITECTURE.md §6 (open question: the impedance measure).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from crystal.operator_schema import OPERATOR_CONTENT_TYPE, spec_hash, validate_operator

log = logging.getLogger("operators.registry")

# Operator definitions are per-persona manifests (`chorus/src/<persona>/manifest.py`), collected
# by the host from each persona binding and registered here one definition at a time (`register`).
# There is no shared catalog and no `load_catalog`.


class OperatorRegistry:
    """Register / discover operator definitions through a mantle client.

    `mantle` is duck-typed: needs `put_artifact(doc) -> dict` and
    `list_artifacts(content_type=...) -> iterable[dict]` (the `crystal.mantle_client`
    surface). The registry never touches a store directly — Mantle is the only data path.
    """

    def __init__(self, mantle) -> None:
        self._mantle = mantle

    def register(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and upsert one operator definition. Returns the stored doc.

        `spec_hash` is not stamped here: a definition edited in flight carries the edited spec,
        and the changed-spec-is-a-different-operator rule is enforced by computing the address
        from that spec at the moment the question is asked (`evolution.preserve_fitness`). No
        side-cars: the artifact carries the spec, and the address is derived from the artifact."""
        doc = dict(definition)
        doc.setdefault("content_type", OPERATOR_CONTENT_TYPE)
        doc.setdefault("state", "committed")
        problems = validate_operator(doc)
        if problems:
            raise ValueError("operator %r failed schema validation: %s"
                             % (doc.get("id"), problems))
        return self._mantle.put_artifact(doc)

    def register_many(self, definitions: List[Dict[str, Any]]) -> int:
        """Register a list of operator definitions (a persona's manifest, or the concatenation of all
        persona manifests). Idempotent — puts upsert. Returns count."""
        n = 0
        for d in definitions:
            self.register(d)
            n += 1
        return n

    def list_operators(self) -> List[Dict[str, Any]]:
        return [d for d in self._mantle.list_artifacts(content_type=OPERATOR_CONTENT_TYPE)]

    def find_by_offer(self, need_text: str) -> List[Dict[str, Any]]:
        """Discovery, keyed not clever: operators whose offer mentions the need's terms.
        (The semantic offers/needs match belongs to the store's retrieval, not the registry —
        this is the plain fallback that always works.)"""
        terms = [t for t in str(need_text).lower().split() if t]
        out = []
        for d in self.list_operators():
            offer = json.dumps(d.get("offer", "")).lower()
            if all(t in offer for t in terms):
                out.append(d)
        return out

    @staticmethod
    def runnable_on(definition: Dict[str, Any], host_capabilities: Iterable[str]) -> bool:
        """pattern × capability: can this host ground this pattern? Every need must be offered."""
        have = set(host_capabilities or [])
        return all(n in have for n in (definition.get("needs") or []))
