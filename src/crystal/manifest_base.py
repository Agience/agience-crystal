"""Per-persona operator manifest helper (framework, persona-agnostic).

Each persona has its own manifest, rather than a single shared crystal `catalog.json`. A persona
declares `REGISTRARS` (its organons' `register_*` functions — the source of truth for each op def)
and uses these helpers to collect them. This is the generic collection machinery only; which
operators a persona owns lives in that persona (chorus). The registration-to-Mantle orchestration is
chorus's (`chorus.operator_manifests`); this stays framework because it is domain-agnostic and every
persona imports it (chorus → crystal is allowed by the DAG).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

Registrar = Callable[[Any], int]


class Capture:
    """A store that records what registration would write. Nothing persists."""

    def __init__(self) -> None:
        self.docs: Dict[str, Dict[str, Any]] = {}

    def get_artifact(self, aid: str):
        return self.docs.get(aid)

    def put_artifact(self, doc: Dict[str, Any]):
        self.docs[doc["id"]] = dict(doc)
        return doc


def collect(registrars: List[Registrar], store: Any = None) -> int:
    """Run every registrar against `store` (a duck-typed put/get artifact store); return the count.
    With no store, registers into a throwaway Capture (used to derive the manifest)."""
    cap = store if store is not None else Capture()
    return sum(r(cap) for r in registrars)


def operators(registrars: List[Registrar]) -> List[Dict[str, Any]]:
    """The manifest: the list of operator-definition artifacts these registrars produce."""
    cap = Capture()
    collect(registrars, cap)
    return list(cap.docs.values())


__all__ = ["Capture", "collect", "operators", "Registrar"]
