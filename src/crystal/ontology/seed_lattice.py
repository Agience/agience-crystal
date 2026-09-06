"""The seed-lattice build — laying the language transducer's substrate once (a grounding job).

`build()` is the one-time tekton mechanism that lays the keyed substrate the whole chat path depends
on — stored IC, `lex:<lang>` entry edges, persisted ξ, and the transducer artifact written last as
the readiness flag. It is grounding (it writes the lattice), so it stays ember-side (Apache-adjacent
runtime); the transducer's runtime conversion classes (`Transducer`/`LanguageTransducer`) are the
persona-owned facet binding, in `lumen/transducer.py`. `crystal/ontology/transducer.py` keeps only
the measurement read (`persisted_xi`) and the shared constants/cache.

`TRANSDUCER_CT` and the runtime registry `_REG` live in `crystal/ontology/transducer.py`; imported
here so a completed build invalidates the (lumen-populated) process cache — the same `_REG` dict —
and stamps the transducer artifact's content-type. lumen reads/writes this cache; this module's
`build` clears it. (Same shape as `match._OFFER_CACHE`/`invalidate` for the `select` tekton.)
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List

from crystal.ontology import driver as _wn   # the ontology driver
from crystal.ontology.transducer import TRANSDUCER_CT, _REG

#: `() -> {"xi": float, "gap": float} | None`. The host's geometry derivation, if it has one.
_GEOMETRY_PROVIDER = None


def set_geometry_provider(fn) -> None:
    """Wire the derivation that fills the transducer's `xi`/`gap` summary. `None` unwires it.

    The derivation needs the store and the signal together, so it lives above this layer and is
    handed down rather than reached up for. Unwired is a supported state, not a failure."""
    global _GEOMETRY_PROVIDER
    _GEOMETRY_PROVIDER = fn


def _wal(store) -> None:
    # `db.write()` is a context manager, not a connection — it must be entered. Calling `.execute` on
    # the manager raises AttributeError, which a bare `except` would swallow, so the checkpoint would
    # silently never run and the WAL would grow unbounded.
    try:
        with store.artifacts.db.write() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


#: Rows accumulated before one `put_many`. A write-size bound: the same rows land in the same
#: order at any value; only the size of the transaction and the peak list change. A checkpoint
#: fires on the size actually flushed (see `_crossed`), never on this constant, so this number is
#: free to change without breaking the checkpoint cadence — right as long as a `put_many` at this
#: size fits the node's envelope; the bound is the envelope, not this number.
_IC_BATCH = 2000


def _unchanged(prev, val: float) -> bool:
    """Is the stored `ic` already the measurement this corpus yields? One definition, both writers.

    Exact equality, not an approximate one. Both IC families are `1 - log(k)/log(N+1)` over two
    integers — a descendant count and a vertex count — so a re-derivation over an unchanged corpus
    reproduces the same float64 bit for bit, and JSON round-trips float64 exactly. Because k and N
    are integers, the smallest nonzero change either can produce is bounded well above float64
    rounding noise at every corpus scale swept (1e2..1e7, minimum 2.67e-10), so exact equality
    never mistakes a real change for none, nor drift for a real one.

    A tolerance would be right again if `ic` ever stopped being a closed form over integers — a
    summation, or a value read back through a lossy encoding — at which point it must be derived
    from that arithmetic, not typed.
    """
    return prev is not None and float(prev) == float(val)


def _crossed(n: int, every: int, flushed: int) -> bool:
    """Did the running total `n` pass a multiple of `every` during the last `flushed` rows?

    Derived, not tuned. The write loop advances `n` by exactly `flushed` each flush, so the half-open
    interval just covered is `(n - flushed, n]`; it contains a multiple of `every` iff
    `n mod every < flushed`. The comparison is against the size of the write that just happened — a
    measurement the caller already holds — so no batch size is restated here, and the test stays
    exact even when the final batch is short.

    `every <= 0` means "never checkpoint on a cadence" rather than a ZeroDivisionError.
    """
    if every <= 0 or flushed <= 0:
        return False
    return (n % every) < flushed


def persist_ic(store, idx, docs, *, author: str = "john@ikailo.com",
               checkpoint_every: int = 40000, log=print) -> int:
    """Write the corpus's IC measurement onto the synset rows that disagree with it, then record
    what that measurement was taken from (`_wn.IC_BASIS_ID`, the ontology driver). Returns rows
    written.

    `Synset.ic()` returns 0.0 for an absent ic — that is its documented contract, and `has_ic()`
    exists precisely to tell absence from a real zero ([[absence-is-not-an-affirmative-claim]]).
    Intrinsic IC is 0.0 only for a node that subsumes the entire corpus, so writing it for a
    synset that was never measured writes an absence as if it were a value; downstream,
    `sparse_vec` keeps only edges of weight `IC(c) - IC(p) > 0`, so a zero coordinate loses all its
    outgoing weight, and `dense_vec` returns an all-zero coordinate — the concept becomes
    unreachable in the ontology geometry. Intrinsic IC is also a function of the whole corpus
    (`1 - log(desc+1)/log(N+1)`), so it changes whenever a source lands, even for a synset that
    already carries a value — writing only where the field is absent would freeze the first
    measurement forever.

    So: never write what was not measured (`has_ic()`), and write wherever the stored number
    differs from the one this corpus yields. Idempotent — a second run writes nothing."""
    from prism.grounding import _now, P_OBSERVED
    arts = store.artifacts
    n = 0
    batch: List[Dict[str, Any]] = []
    for doc in docs:
        name = _wn._n(doc.get("id") or "")
        syn = idx.get(name)
        if syn is None or not syn.has_ic():
            continue
        v = float(syn.ic())
        if _unchanged(doc.get("ic"), v):
            continue                      # already the measurement this corpus yields
        doc["ic"] = v
        batch.append(doc)
        if len(batch) >= _IC_BATCH:
            arts.put_many(batch)
            flushed = len(batch)
            n += flushed
            batch = []
            if _crossed(n, checkpoint_every, flushed):
                _wal(store)
                log("  ic: %d written" % n)
    if batch:
        arts.put_many(batch)
        n += len(batch)
    _wal(store)
    # The measurement's own record, written after the values it describes — so a run that dies
    # part-way leaves the basis stale and the next load re-derives, rather than claiming a
    # corpus-wide measurement that only half landed.
    arts.put_artifact({
        "id": _wn.IC_BASIS_ID, "content_type": _wn.IC_BASIS_CT, "state": "committed",
        "basis": {"source": _wn.INTRINSIC_IC_SOURCE, "n": len(idx),
                  "formula": _wn.INTRINSIC_IC_FORMULA},
        "content": "information content over %d synsets, %s (%s)"
                   % (len(idx), _wn.INTRINSIC_IC_FORMULA, _wn.INTRINSIC_IC_SOURCE),
        "lemmas": ["information", "content", "basis"], "provenance": P_OBSERVED,
        "created_by": author, "created_time": _now(),
    })
    _wal(store)
    log("persist_ic: %d rows written, basis n=%d" % (n, len(idx)))
    return n


def refresh_ic(store, *, author: str = "john@ikailo.com", checkpoint_every: int = 40000,
               log=print) -> Dict[str, Any]:
    """Re-take the corpus's IC measurement and persist it — the standalone half of `build`'s phase 2.

    Intrinsic IC is a measurement of the corpus, so ingesting a source invalidates it: the `N` in
    `1 - log(desc+1)/log(N+1)` changes for every synset, and the new source has no value at all.
    `build()` re-takes it as part of laying the transducer substrate; this runs the same measurement
    on its own, for a corpus whose lexicon is already built and only needs its geometry re-measured
    after an ingest. `_unchanged` still guards each row inside `persist_ic`, so only synsets whose
    IC actually moved are rewritten."""
    _wn.invalidate()
    idx = _wn._index()                    # derives the measurement — see `wn_store.ic_basis`
    ct = _wn._ct(store)
    arts = store.artifacts

    def _pages():
        after, seen = "", 0
        while True:
            rows = arts.page_by_id(after=after, limit=2000, content_type=ct)
            if not rows:
                log("  scanned %d synset rows" % seen)
                return
            for r in rows:
                yield r["doc"]
            after, seen = rows[-1]["id"], seen + len(rows)

    written = persist_ic(store, idx, _pages(), author=author,
                         checkpoint_every=checkpoint_every, log=log)
    _wn.invalidate()                      # the stored values are the measurement now — re-read them
    return {"synsets": len(idx), "ic_written": written}


#: The associative entry label. `lemma:<surface> --assoc:<lang>--> cn-<term>`.
#:
ASSOC_LABEL_PREFIX = "assoc:"


def anchor_associative(store, *, lang: str = "en", edge_batch: int = 1000,
                       checkpoint_every: int = 40000, log=print) -> int:
    """Anchor the associative lexicon (ConceptNet) onto the lemma vertices WordNet already anchors.

        cn-cat   capable_of 111 · desires 17 · used_for 16 · has_property 7 · is_a 20 · related_to 149
        cat --capable_of--> meow          <- the answer to "what does a cat say", already in the store

    and the two halves are joined by nothing:

        edges wn-* -> cn-* : 0
        edges cn-* -> wn-* : 0

    So the reasoning has one relation type available to it — `hypernym`, the one that can only say
    "X is a kind of Y" — even though the data was never missing: the two universes are disconnected
    (`PAPER` §26 and §31 record the disjointness).

    Idempotent — the graph store dedupes on its own edge key, so re-running writes nothing new.
    Returns the number of edges written."""
    label = ASSOC_LABEL_PREFIX + lang
    try:
        conn = store.artifacts.db.read()
    except Exception:
        return 0
    # The set is chosen by the corpus, not by a limit: single-token ConceptNet terms whose surface
    # is already a lemma vertex carrying transducer entries.
    sql = ("SELECT v.id FROM vertex v WHERE v.id LIKE 'cn-%' AND instr(v.id,'_')=0 "
           "AND EXISTS (SELECT 1 FROM edge e WHERE e.label=? AND e.src='lemma:'||substr(v.id,4))")
    written, chunk = 0, []
    try:
        rows = conn.execute(sql, ("lex:" + lang,)).fetchall()
    except Exception:
        return 0
    for (vid,) in rows:
        term = str(vid)[3:]
        if not term:
            continue
        # `props` records what this edge is: an association entry, from ConceptNet, carrying no
        # sense rank — so a reader cannot mistake it for a transducer entry that lost its rank.
        chunk.append(("lemma:" + term, str(vid), label,
                      {"source": "conceptnet", "rank": None, "associative": 1}))
        if len(chunk) >= checkpoint_every:
            written += store.graph.add_edges(chunk, batch=edge_batch)
            chunk = []
            _wal(store)
    if chunk:
        written += store.graph.add_edges(chunk, batch=edge_batch)
    _wal(store)
    try:
        log("  assoc: %d %s edges (%d candidate terms)" % (written, label, len(rows)))
    except Exception:
        pass
    return written


CN_IC_SOURCE = "conceptnet-is_a-intrinsic"
CN_IC_FORMULA = "1 - log(desc+1)/log(N+1)"


def conceptnet_ic(store, *, log=print) -> Dict[str, float]:
    """Intrinsic IC for the ConceptNet concepts, over ConceptNet's own `is_a` taxonomy. Derives
    only — writes nothing. Returns `{vertex: ic}`.

    Joining the two universes is necessary but not sufficient: `cn-moo` and `cn-cow` exist as
    artifacts carrying `ic = None`. Without information content they have no ontology coordinate, so
    their rows are all-zero and cannot enter a frame at all — the join would deliver 338 edges that
    the instrument then drops in silence.

    Transitive closure by a per-vertex breadth-first search: a concept reachable by two routes is
    counted once because it lands in the same visited set."""
    conn = store.artifacts.db.read()
    kids: Dict[str, set] = {}
    verts: set = set()
    for a, b in conn.execute(
            "SELECT src,dst FROM edge WHERE label='is_a' AND src LIKE 'cn-%' AND dst LIKE 'cn-%'"):
        kids.setdefault(str(b), set()).add(str(a))
        verts.add(str(a))
        verts.add(str(b))
    n_total = len(verts)
    if n_total < 2:
        return {}
    import math as _m

    #
    # A plain BFS per vertex is order-independent and cycle-safe by construction: the visited set
    # is the descendant set, and a cycle simply revisits nothing. It costs more than memoisation
    # and it is reproducible, which is the trade the corpus is entitled to.
    from collections import deque as _dq
    denom = _m.log(n_total + 1)
    ic: Dict[str, float] = {}
    for v in verts:
        seen_v = {v}
        q = _dq(kids.get(v, ()))
        seen_v.update(q)
        while q:
            u = q.popleft()
            for k in kids.get(u, ()):
                if k not in seen_v:
                    seen_v.add(k)
                    q.append(k)
        ic[v] = 1.0 - (_m.log(len(seen_v)) / denom)   # |desc| + 1 == len(seen_v), v included
    log("conceptnet_ic: %d vertices over %d is_a edges (%s)" % (n_total, sum(len(s) for s in kids.values()), CN_IC_FORMULA))
    return ic


CN_IC_BASIS_ID = "geom.conceptnet-ic-basis"


def persist_conceptnet_ic(store, *, author: str = "john@ikailo.com",
                          checkpoint_every: int = 40000, log=print) -> Dict[str, Any]:
    """Write the ConceptNet concepts' own IC onto their artifacts. Returns `{derived, written}`.

    The sibling of `persist_ic` above, for the other universe, and it obeys the same two rules:

      · Never write an absence. A `cn-` concept outside the `is_a` taxonomy — 83.6% of them — gets
        no `ic` key at all, not 0.0. Intrinsic IC of 0.0 means "subsumes the entire corpus", which
        at most one concept can hold; writing it for everything unmeasured would give every
        unmeasured concept the same phantom coordinate, the failure `has_ic()` guards against in
        `persist_ic`.
      · Idempotent. A row already holding this value is skipped, so a second run writes nothing.

    For example, derived over ConceptNet's own `is_a` edges:

        cn-living_creature 0.0350   cn-cattle 0.6168   cn-bark 0.7179
        cn-animal          0.0350   cn-cow    0.6775   cn-moo  1.0000   cn-purr 1.0000

    Generic low, specific high — the ordering IC has to have for a coordinate to mean anything, and
    derived from ConceptNet alone with no scale borrowed from WordNet. It gives `cn-moo` a
    coordinate; it does not rank `cn-moo` against `cn-purr`."""
    from prism.grounding import _now, P_OBSERVED
    ic = conceptnet_ic(store, log=log)
    if not ic:
        return {"derived": 0, "written": 0}
    arts = store.artifacts
    n = 0
    batch: List[Dict[str, Any]] = []
    for vid, val in ic.items():
        try:
            doc = arts.get_artifact(vid)
        except Exception:
            continue
        if not doc:
            continue                       # no artifact to carry it — nothing is invented here
        if _unchanged(doc.get("ic"), val):
            continue                       # already the measurement this corpus yields
        doc["ic"] = float(val)
        batch.append(doc)
        if len(batch) >= _IC_BATCH:
            arts.put_many(batch)
            flushed = len(batch)
            n += flushed
            batch = []
            if _crossed(n, checkpoint_every, flushed):
                _wal(store)
                log("  cn-ic: %d written" % n)
    if batch:
        arts.put_many(batch)
        n += len(batch)
    _wal(store)
    # The basis, written after the values it describes — a run that dies part-way leaves no claim
    # that a corpus-wide measurement landed.
    arts.put_artifact({
        "id": CN_IC_BASIS_ID, "content_type": "geom.ic-basis", "state": "committed",
        "basis": {"source": CN_IC_SOURCE, "n": len(ic), "formula": CN_IC_FORMULA},
        "content": "ConceptNet information content over %d concepts, %s (%s)"
                   % (len(ic), CN_IC_FORMULA, CN_IC_SOURCE),
        "lemmas": ["information", "content", "basis", "conceptnet"],
        "provenance": P_OBSERVED, "created_by": author, "created_time": _now(),
    })
    _wal(store)
    log("persist_conceptnet_ic: derived %d, wrote %d" % (len(ic), n))
    return {"derived": len(ic), "written": n}


RELATION_PREFIX = "rel-"
RELATION_CT = "ontology.relation"
RELATION_BASIS = "relation-vertex/extensional/2026-08-02"


#: What a signature says when the relation's extent has never been measured. Every measured field
#: is `None` — not `0`, not `0.0`. A relation with a million edges and one that has never been
#: counted would otherwise return the same `{"n": 0, "edges": 0}`, and the second one would be a
#: fabricated measurement wearing a real one's clothes. `measured` is the field to branch on.
_UNMEASURED_SIGNATURE = {"n": None, "edges": None, "measured": False,
                         "symmetry": None, "inverse": None, "inverse_share": None}


def _relation_signature(store, label: str, sample: int = 4000) -> dict:
    """One relation's measured spin: `{"n", "edges", "measured", "symmetry", "inverse",
    "inverse_share"}`.

    The extent is a counter lookup, never `count(*)`: no edge index leads with `label` (they are
    `(src,label)` and `(dst,label)`), so `SELECT COUNT(*) FROM edge WHERE label=?` would scan the
    whole edge table once per relation. `graph.count_edges_by_label` is one indexed read of one
    `counter` row, maintained in the same transaction as the write that changed it.

    `total` is doing three jobs, which is why one number still covers them:

      1. **the zero test** — only needs existence, but a measured zero, distinguishable from an
         unmeasured one; that distinction is `count_edges_by_label` returning `None`.
      2. **the sample stride** (`total // sample`) — needs the exact extent, not an estimate: a
         stride off by one selects a different subset of rows, so `symmetry` and `inverse_share`
         would change. An approximation here would silently change the answer.
      3. **the reported `edges`** — is published on the relation artifact as an observed
         measurement, so it must be the count, or it must be absent."""
    conn = store.artifacts.db.read()
    total = store.graph.count_edges_by_label(label)
    if total is None:
        # Not measured. The store's edges predate the per-label counter and nothing has run the
        # backfill, so this relation's extent is unknown. Returning 0 here would be a fabricated
        # measurement — worse than the slow query, because it is plausible.
        return dict(_UNMEASURED_SIGNATURE)
    if total <= 0:
        return {"n": 0, "edges": 0, "measured": True,
                "symmetry": 0.0, "inverse": None, "inverse_share": 0.0}
    stride = max(total // max(sample, 1), 1)
    pairs = [(str(a), str(b)) for a, b in conn.execute(
        "SELECT src,dst FROM edge WHERE label=? AND rowid %% %d = 0 LIMIT ?" % stride,
        (label, sample))]
    if not pairs:
        return {"n": 0, "edges": total, "measured": True,
                "symmetry": 0.0, "inverse": None, "inverse_share": 0.0}
    sym = sum(1 for a, b in pairs if conn.execute(
        "SELECT 1 FROM edge WHERE src=? AND dst=? AND label=? LIMIT 1", (b, a, label)).fetchone())
    inv: dict = {}
    for a, b in pairs:
        for (l2,) in conn.execute("SELECT label FROM edge WHERE src=? AND dst=?", (b, a)):
            if str(l2) != label:
                inv[str(l2)] = inv.get(str(l2), 0) + 1
    best = max(inv, key=lambda k: inv[k]) if inv else None
    return {"n": len(pairs), "edges": total, "measured": True, "symmetry": sym / len(pairs),
            "inverse": best, "inverse_share": (inv[best] / len(pairs)) if best else 0.0}


def relation_vertices(store, *, author: str = "john@ikailo.com") -> int:
    """Every relation becomes a vertex. Returns how many were written.

    A relation carried as a string in an edge's `label` column is a member of a closed 85-value
    enumeration — the shape [[ember-holds-a-second-typed-propagation-model]] names as forbidden,
    and the reason a need can never propagate to a relation: you cannot travel to a column value.
    As a vertex it is an artifact like any other, reachable, citable, and carrying its own measured
    properties."""
    from prism.grounding import _now, P_OBSERVED   # single-sourced grounding constants
    arts = store.artifacts
    # Measure first, then read. A store whose edges predate the per-label counter has no extent
    # to read, and every signature below would come back `measured: False`. This is the one place
    # the corpus is walked, in keyset pages, in one transaction — a no-op on a store the write
    # path has maintained all along (which is every store created since the counter existed).
    #
    # `labels_with_edges()` is one indexed read of the small `counter` table, rather than
    # `SELECT DISTINCT label FROM edge` (no index leads with `label`, so that scans the whole edge
    # table). It filters zero extents, so a relation whose last edge was deleted drops out exactly
    # as `DISTINCT` over the rows would.
    store.graph.backfill_edge_label_counters()
    labels = store.graph.labels_with_edges()
    if labels is None:                     # the backfill above is the only way this can happen
        raise RuntimeError(
            "relation_vertices: the per-label edge counters are not measured on this store and "
            "the backfill did not certify them. Refusing to write relation artifacts carrying "
            "fabricated extents — run graph.backfill_edge_label_counters() and retry.")
    n = 0
    for lab in labels:
        sig = _relation_signature(store, lab)
        if not sig["measured"]:
            # Cannot happen after the backfill above; asserted rather than assumed, because the
            # failure it guards is silent — a relation artifact published with `edges: 0` over a
            # million-edge relation, carrying `provenance: OBSERVED`, is a lie with a rung on it.
            raise RuntimeError(
                "relation_vertices: relation %r has no measured extent. A relation artifact is "
                "an OBSERVED measurement and will not be written from an unmeasured one." % lab)
        arts.put_artifact({
            "id": RELATION_PREFIX + lab, "content_type": RELATION_CT, "state": "committed",
            "kind": "relation", "spec": dict(sig, basis=RELATION_BASIS, label=lab),
            "context": "ontology relation — %s" % lab,
            "content": "the relation `%s`, as the corpus uses it: %d edges, symmetry %.3f%s"
                       % (lab, sig["edges"], sig["symmetry"],
                          (", inverse of `%s`" % sig["inverse"]) if sig["inverse"] else ""),
            "lemmas": [p for p in lab.replace(":", " ").replace("_", " ").split() if p],
            # OBSERVED, not HUMAN: a signature is measured off the edge table, and the rung has
            # to say which ([[provenance-needs-authority]]). Nobody asserted that `antonym` is
            # symmetric — the corpus was counted.
            "provenance": P_OBSERVED, "created_by": author, "created_time": _now(),
        })
        n += 1
    _wal(store)
    return n


def build(store, *, lang: str = "en", author: str = "john@ikailo.com",
          edge_batch: int = 1000, checkpoint_every: int = 40000, log=print) -> Dict[str, Any]:
    """Lay the language:<lang> transducer substrate from the ingested WordNet, once:

      1. **stored IC** — the intrinsic information content (computed over the tree) written onto each
         synset, so the chat path reads it keyed instead of recomputing it corpus-wide.
      2. **`lex:<lang>` entry edges** — `lemma:<surface> --lex:<lang>--> wn-<synset>`, the source's
         (proper-noun, sense-rank, pos) carried on the edge so entry needs no doc read to order/filter.
      3. **persisted ξ** — the propagator scale, measured once and stored in the transducer.
      4. **the transducer artifact** — `op.transducer.language.<lang>`, written last; its presence flips
         `wn_store` onto the keyed path.

    WAL-checkpointed throughout. Idempotent on IC and the transducer artifact; entry edges are deduped by
    the graph store's own key.
    """
    from prism.grounding import _now, P_HUMAN, P_OBSERVED  # single-sourced grounding constants
    arts = store.artifacts
    label = "lex:" + lang
    summary: Dict[str, Any] = {"ic_stored": 0, "lex_edges": 0, "xi": None, "exceptions": 0}

    # phase 0 — the full load computes intrinsic IC + the tree structure (build path, once)
    _wn.invalidate()                      # public API, not a reach into the driver's privates
    idx = _wn._index()

    # phase 1 — scan the docs once for the fields entry/IC need (sense_ranks, lemma_counts, forms)
    meta: Dict[str, Dict[str, Any]] = {}
    for a in arts.list_artifacts(content_type="text/x-wordnet"):
        name = _wn._n(a.get("id") or "")
        if name:
            meta[name] = a

    # phase 2 — store the corpus's IC measurement on each synset (in place, where it disagrees)
    summary["ic_stored"] = persist_ic(store, idx, meta.values(), author=author,
                                      checkpoint_every=checkpoint_every, log=log)

    # phase 3 — the lex:<lang> entry edges + the source's irregular forms (morphy exceptions)
    exceptions: Dict[str, str] = {}

    def _lex_edges():
        for name, doc in meta.items():
            # English docs (OEWN, PWN) carry no `lang`; OMW docs carry theirs. So absent means "the
            # language being built", present means "only if it matches". Without this filter, a
            # foreign-language homograph can absorb a lemma's only sense, so `_entry_names` raises
            # RankUnavailable (every sense of that surface form lacks an English rank) and every
            # sentence using it loses its English reading entirely.
            doc_lang = doc.get("lang") or lang
            if doc_lang != lang:
                continue
            pos = doc.get("pos") or (name.rsplit(".", 2)[-2] if name.count(".") >= 2 else _wn.NOUN)
            counts = doc.get("lemma_counts") if isinstance(doc.get("lemma_counts"), dict) else {}
            lemma_names = list((counts or {}).keys()) or list(doc.get("lemmas") or [])
            ranks = doc.get("sense_ranks") or {}
            by_lower = {str(k).lower(): (str(k), v) for k, v in ranks.items()}
            for lm in lemma_names:
                if not lm:
                    continue
                written, rank = by_lower.get(str(lm).lower(), (str(lm), None))
                proper = 1 if written != written.lower() else 0
                surface = str(lm).lower().replace(" ", "_")
                props = {"pos": pos, "proper": proper,
                         "rank": float(rank) if isinstance(rank, (int, float)) else None}
                yield ("lemma:" + surface, "wn-" + name, label, props)
            for _lm, _fs in (doc.get("forms") or {}).items():
                for _f in (_fs or []):
                    exceptions[str(_f).lower().replace(" ", "_") + "|" + pos] = \
                        str(_lm).lower().replace(" ", "_")

    # Chunk the edge writes with periodic WAL checkpoints — ~1M edges in one call would let the WAL
    # grow unbounded before a single trailing collapse.
    chunk: List[tuple] = []
    for e in _lex_edges():
        chunk.append(e)
        if len(chunk) >= checkpoint_every:
            summary["lex_edges"] += store.graph.add_edges(chunk, batch=edge_batch)
            chunk = []
            _wal(store)
    if chunk:
        summary["lex_edges"] += store.graph.add_edges(chunk, batch=edge_batch)
    summary["exceptions"] = len(exceptions)
    _wal(store)

    # phase 3b — anchor the associative lexicon onto the same lemmas
    summary["assoc_edges"] = anchor_associative(store, lang=lang, edge_batch=edge_batch,
                                                checkpoint_every=checkpoint_every, log=log)

    # phase 4 — the corpus's geometry, measured once
    #
    # The whole read is persisted, carrying its own basis and the corpus size it was taken against;
    # `match._persisted_geometry` requires both to be stated. `spec.xi` stays beside it — written
    # from the same read, never derived a second time — because `crystal.ontology.transducer.
    # persisted_xi` is a published surface: one derivation, two spellings, so they cannot drift
    # apart the way two derivations would.
    #
    geom = None
    if _GEOMETRY_PROVIDER is not None:
        try:
            geom = _GEOMETRY_PROVIDER()
        except Exception:
            geom = None
    summary["xi"] = None if geom is None else geom["xi"]
    summary["gap"] = None if geom is None else geom["gap"]

    # phase 5 — the transducer artifact, written last (the readiness flag)
    spec = {
        "name": "language." + lang, "kind": "language", "lang": lang, "entry_label": label,
        "frame_t": "surface-sequence", "frame_f": "ontology-coordinate",
        "xi": summary["xi"], "geometry": geom, "exceptions": exceptions,
    }
    # op-id routed through the one constant.
    from prism.grounding import TRANSDUCER_OP
    gid = TRANSDUCER_OP + "language." + lang
    gdoc = {
        "id": gid, "content_type": TRANSDUCER_CT, "state": "committed", "kind": "transducer",
        "spec": spec,
        "context": "language:%s transducer — surface↔concept conversion" % lang,
        "content": "language:%s transducer (data-driven): lemma↔synset over the shared lattice" % lang,
        "lemmas": ["transducer", "language", lang], "provenance": P_HUMAN,
        "created_by": author, "created_time": _now(),
    }
    arts.put_artifact(gdoc)
    _wal(store)

    # The substrate is live now — clear the process caches so this run reads the keyed path.
    _wn.invalidate()
    _REG.clear()
    summary["transducer"] = gid
    log("transducer.build(%s): %s" % (lang, summary))
    return summary
