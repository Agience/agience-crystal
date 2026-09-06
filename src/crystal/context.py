"""The `context` seam — the permissive half of the two-tier mint. Domain knowledge, and only that.

`mantle.services.mint_context` records what a STORE can know without knowing what the content
means: when, by whom, into which collection, addressed how, and what else was on the screen. It is
complete on its own and a store that never loads this module mints correctly.

This is the sharp arm. It answers the one question mantle deliberately cannot:

    **what does this content MEAN, in a vocabulary something else already holds?**

## Wiring

    MANTLE_CONTEXT_HOST=crystal.context

Resolved by NAME at call time, so mantle's import graph names no host — the same mechanism
`mantle.search.ranking` uses for `match` via `MANTLE_ONTOLOGY_HOST`.

WHY CRYSTAL — and this module was written for astra first, then ember, and BOTH were wrong.

Two constraints have to hold at once, and only one repo satisfies both.

**1. It must be importable in the mantle process.** A seam is resolved by `importlib`. Measured in
the live 71/home node environment:

    crystal.ontology  OK
    ember             OK
    astra             ModuleNotFoundError

Astra is a tekton, reached over the wire. 📄 `agience-chorus`: *"Everything else reaches Chorus over
the wire (HTTP/MCP) and never links it — the boundary is deliberate."*

**2. It must not put copyleft on an Apache store's mint path.** 📄 `search/beacon/__init__`: *"mantle
ships Apache so a store can be taken, built on and shipped by anyone."* From the licence table:

    mantle    Apache-2.0        crystal   Apache-2.0
    ember     AGPL-3.0-only     chorus    AGPL-3.0-only

**Ember satisfies (1) and fails (2).** Moving the seam there would have made an Apache store's
correct mint depend on an AGPL package — the same defect as astra, one repo over, and harder to see
because ember imports cleanly.

📄 Crystal's own NOTICE states the property that settles it: *"Crystal is the agnostic base library
(an instrument), so it is permissive and can be a shared dependency without forcing copyleft."* It is
also where `crystal.ontology` — the vocabulary this seam reads — already lives.

Astra and ember keep the halves that are genuinely theirs, and both happen BEFORE the mint:
connectors, format extraction, chunking. Those reach the store as ordinary caller context, over the
wire, with no import anywhere.

## What belongs here, and what does not

HERE — needs a vocabulary, a format or a service:

    anchors      the concepts the text names, via `crystal.ontology`. This is the one that
                 unblocks the rest: an artifact with no `lex:en` anchors is invisible to
                 `ember.consolidate.diagram`, so the colimit cannot run and the ontology reach
                 arm has no position to propagate from.
    structure    heading depth, section, ordinal — markdown/HTML knowledge

Not here — mantle already recorded it, and re-deriving it would create a second, disagreeing answer:
minting, placement, addressing, the structural screen. Not here either — the caller's own
assertions (source path, licence, upstream revision): those arrive from the writer and mantle keeps
them verbatim under `caller`. A seam that re-stated them would be laundering an assertion into an
observation.

## The offer is anchored; the body is not, by default

Measured 2026-08-24 against the live 71/home lattice, ontology bound:

    offer text  (63 chars)     0.794 s cold · **0.002 s warm**  ->  5 synsets
    body text (3,475 chars)    0.115 s                          -> 12 synsets
    a full create through the live MCP path                      0.22 s

So anchoring the offer costs ~1% of a write and anchoring the body ~50%. 📄 `pipeline_unified` draws
the same line for the same reason: *"An artifact's offer is bounded by the artifact. Its body is
bounded by nothing."* The body is anchored only when a caller asks for it, and asking is a decision
about that corpus rather than a default every writer pays.

## It may not fail a write

Every path returns a dict or `None`. `mint_context._enrich` logs and drops anything that raises,
so a mistake here degrades to the thin mint — which is complete. That is the contract this side
must not abuse: **an enrichment that is wrong is worse than one that is absent**, because absent is
visible in the coverage gate and wrong is not.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

#: Anchor the body only when the caller says so. See the module docstring for the measurement.
ANCHOR_BODY_KEY = "anchor_body"

#: How much offer text to anchor. The offer is bounded by the artifact, but "bounded" is not
#: "small" — a pathological description should not turn one write into a corpus read.
_OFFER_CHARS = 2000
#: And the body, when explicitly asked for.
_BODY_CHARS = 20000

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _offer_text(base: Mapping[str, Any]) -> str:
    """The text this artifact announces itself as — title, name, description, tags.

    Read from the caller's own context, because that is where a writer states the offer.
    `mint_context` keeps it verbatim under `caller` and never merges it, so this is reading an
    assertion and treating it as one: the anchors derived here are marked as enrichment, not as
    something the store observed.
    """
    caller = base.get("caller") or {}
    if not isinstance(caller, Mapping):
        return ""
    parts = []
    for key in ("title", "name", "description", "heading"):
        v = caller.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    tags = caller.get("tags")
    if isinstance(tags, (list, tuple)):
        parts.extend(str(t) for t in tags if t)
    return " ".join(parts)[:_OFFER_CHARS]


_BOUND = False


def _bind_ontology(db: Any) -> None:
    """Point `crystal.ontology` at the store this mint is for. Once per process.

    Without this the seam runs and anchors nothing. Measured: a re-mint through the live server
    returned `enriched` on 9/9 rows with `anchors: []` — the seam fired, the ontology answered
    nothing, and the artifact recorded a truthful "asked, found none" that was actually "asked the
    wrong store". `driver` warns about exactly this — *"no store passed and none bound — reading the
    PROCESS-DEFAULT store. The ontology read is a MEASUREMENT; pass `store=` or call
    `wn_store.bind(store)` so it is measured on the store the caller actually holds."*

    An empty answer that looks like a measured absence is the failure mode this whole module is
    written against, so the binding is not optional and its absence is logged.
    """
    global _BOUND
    if _BOUND or db is None:
        return
    try:
        from crystal.ontology import driver
        driver.bind(db)
        _BOUND = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not bind the ontology to the minting store (%s: %s); "
                       "anchors would be measured against the wrong store, so none are recorded",
                       type(exc).__name__, exc)


def _anchors(text: str) -> Optional[list]:
    """The concepts this text names. `None` when the ontology is not reachable in this process.

    `None` and `[]` are different answers and both are kept: `[]` means the ontology was asked
    and named nothing, `None` means it was never asked. An artifact whose anchors are absent
    because no ontology was bound must not read like one that genuinely names no concept — that is
    the same /distinction the store keeps everywhere else.
    """
    if not text.strip():
        return None
    try:
        from crystal.ontology.lookup import offer_synsets
    except Exception:  # noqa: BLE001 — a store with no ontology present is a supported deployment
        return None
    try:
        return list(offer_synsets(text) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("anchor resolution failed (%s: %s); the mint keeps its thin context",
                       type(exc).__name__, exc)
        return None


def _structure(content: Optional[str], content_type: Optional[str]) -> Optional[Dict[str, Any]]:
    """Markdown structure, when the content declares itself as markdown.

    Gated on the DECLARED `content_type` rather than sniffed: guessing a format from bytes is a
    judgement, and a wrong guess writes a false statement into the record. A caller that mislabels
    its content gets structure that matches the label it chose.
    """
    if not content or not content_type:
        return None
    if "markdown" not in str(content_type).lower():
        return None
    heads = _HEADING.findall(content[:_BODY_CHARS])
    if not heads:
        return None
    depth, first = len(heads[0][0]), heads[0][1]
    return {"format": "markdown", "heading": first, "heading_depth": depth,
            "headings": len(heads)}


def enrich(base: Mapping[str, Any], *, artifact_id: str = "",
           content_type: Optional[str] = None,
           content: Optional[str] = None, db: Any = None,
           **_: Any) -> Optional[Dict[str, Any]]:
    """The seam entry point. Returns what a domain knows, or `None` when it knows nothing.

    `None` rather than `{}` is deliberate: `mint_context` writes no `enriched` key at all for
    `None`, so a mint with no enrichment stays visibly thin instead of carrying an empty section
    that reads as "asked and found nothing".
    """
    out: Dict[str, Any] = {}
    _bind_ontology(db)

    offer = _offer_text(base)
    anchors = _anchors(offer)
    if anchors is not None:
        out["anchors"] = anchors
        out["anchors_from"] = "offer"

    caller = base.get("caller") or {}
    if isinstance(caller, Mapping) and caller.get(ANCHOR_BODY_KEY) and content:
        body_anchors = _anchors(content[:_BODY_CHARS])
        if body_anchors is not None:
            merged = list(dict.fromkeys(list(out.get("anchors") or []) + body_anchors))
            out["anchors"] = merged
            out["anchors_from"] = "offer+body"

    structure = _structure(content, content_type)
    if structure:
        out["structure"] = structure

    return out or None
