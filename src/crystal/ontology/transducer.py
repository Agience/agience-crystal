"""Transducer measurement read and shared state — the instrument side of the transducer.

A transducer is a surface↔concept conversion stored as an artifact (`op.transducer.<name>`). The
conversion classes (`Transducer`/`LanguageTransducer`/`StubTransducer` and `get_transducer` — the
entry/render facet binding) live in `lumen/transducer.py`, chorus's persona facet binding. What
stays here is the measurement side:

  · `persisted_xi` — the keyed measurement read: ξ measured once at build time and stored on the
    language transducer artifact, read off `spec.xi` directly (not through the conversion classes),
    so the chat path never re-derives it. `ember.ontology.match.xi` calls it.
  · `TRANSDUCER_CT` / `TRANSDUCER_OP` — the stored content-type and op-id prefix, shared constants.
  · `_REG` — the `get_transducer` instance cache (a plain dict). It lives here because
    `crystal.ontology.seed_lattice.build()` clears it after a (re)write; lumen's `get_transducer`
    reads and writes the same dict.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from crystal.ontology import driver as _wn
from prism.grounding import TRANSDUCER_OP       # the op-id prefix

TRANSDUCER_CT = "application/x-transducer"

# The get_transducer instance cache — cleared by `seed_lattice.build()` after a (re)write, populated
# by lumen's `get_transducer`. Holds lumen Transducer instances; typed `Any` because this module
# does not import them.
_REG: Dict[str, Any] = {}


def persisted_xi(lang: str = "en") -> Optional[float]:
    """ξ as measured once at build time and stored on the language transducer artifact, read keyed
    (`spec.xi` off the stored doc), so the chat path never triggers the whole-corpus derivation.
    Reads the artifact directly, not via the lumen conversion classes, so this measurement stays
    here with no persona import — `ember.ontology.match.xi` calls it."""
    try:
        doc = _wn._arts().get_artifact(TRANSDUCER_OP + "language." + lang)
    except Exception:
        doc = None
    v = (doc.get("spec") or {}).get("xi") if doc else None
    return float(v) if isinstance(v, (int, float)) else None


__all__ = ["TRANSDUCER_CT", "TRANSDUCER_OP", "persisted_xi", "_REG"]
