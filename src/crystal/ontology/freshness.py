"""Freshness — is a cached derivation still the derivation of what is in the store?

A cache keyed on an object's identity misses by construction whenever the underlying data really
changes, rather than by a version number someone has to remember to bump — staleness is made
unrepresentable instead of detected. The identity that matters is the one the store itself keeps.
IC lives on the stored synset rather than on a separate table the caller loads (`load_ic()` returns
`None`), so keying a cache on the identity of an IC table would not discriminate anything. Mantle
keeps two identities that do discriminate, both maintained inside the write transaction because the
writer cannot write without them:

    seq.write_mark(db)      every origin's `last_seq`. Every commit allocates a fresh `_seq`
                            (insert, update and delete; vertex and edge; local and replicated),
                            so this moves on every write to the store and on nothing else.
                            Measured on 71's 5.7 GB lattice: 14.1 us.

    vertex.version_of(id)   one artifact's `(_origin, _seq)` — reallocated on every write to that
                            row. Measured: 6.4 us, against 17.7 us to hydrate the doc and 370 us
                            to rebuild one `wn_store.Synset`.
    edge.edge_mark(db, id)  the edge half: `(degree, max _seq)` out of one node. Measured: 10.4 us.

Neither is a side-car ([[everything-is-an-artifact]]). Nobody writes them beside the data to record
that they touched it; they are the allocator's own bookkeeping, and a writer that failed to move
them could not have written at all.

The gate and the discriminator — the gate is the half that matters. The reading a warm cache wants
is the negative one:

    mark unchanged  =>  nothing in this store was written  =>  every derivation from it is still
                        the derivation of what is in it.    One 14.1 us read, nothing else.

That is what keeps the cache a cache. Only when the mark has moved does anything finer run, and
then `stamp()` attributes the change to particular artifacts, so an unrelated write (a chat message
lands; the corpus did not change) costs one cheap re-verification per touched entry and drops
nothing. See `wn_store._gate` for the two levels wired together.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

Mark = Tuple[Tuple[str, int], ...]
Stamp = Tuple[Any, ...]


def _artifacts(store: Any) -> Any:
    """The artifacts face — the same resolution `wn_store._arts` and `match._offers` use, so a
    bundle and its own `.artifacts` are one thing here too."""
    return getattr(store, "artifacts", None) or store


def write_mark(store: Any) -> Optional[Mark]:
    """The store's whole-of-store write mark, or `None` if this store cannot report one.

    The gate. Unchanged ⇒ nothing was written ⇒ every cache built from this store is still valid,
    with no further read of any kind. Published by `mantle.db.lattice.seq.write_mark`; see there
    for why it moves on every write and cannot be forgotten."""
    arts = _artifacts(store)
    fn = getattr(arts, "write_mark", None)
    if fn is None:
        return None
    try:
        m = fn()
    except Exception:
        return None
    return tuple(m) if m is not None else None


def stamp(store: Any, artifact_id: str, *, edges: bool = False) -> Optional[Stamp]:
    """One artifact's freshness stamp — `(version, [edge_mark])` — or `None` if unverifiable.

    The discriminator, run only after the gate has moved. `version` is the row's own
    `(_origin, _seq)`; `None` when the artifact is absent, which is itself a stamp and a real
    change (an artifact that has been deleted must not go on being served).

    `edges=True` adds `(degree, max _seq)` over the edges leaving the artifact, read off the face's
    published `edge_mark`. It is not optional decoration for the ontology: measured on 71,
    `wn-dog.n.01` carries no `hypernyms` field, so `wn_store._synset_from_doc` reads the taxonomy
    from `edge WHERE src=?` — and an edge write does not move the vertex's `_seq`. A stamp without
    it would verify half of what it claims to."""
    arts = _artifacts(store)
    ver_fn = getattr(arts, "version_of", None)
    if ver_fn is None:
        return None
    try:
        ver = ver_fn(artifact_id)
    except Exception:
        return None
    if not edges:
        return (ver,)
    # Read off the face, not out of a store package. Importing `mantle.db.lattice.edge.edge_mark`
    # directly would reach into a concrete lattice for a function that takes its raw connection,
    # naming a store implementation rather than a store's shape — which is what would keep this file
    # from travelling with the driver that depends on it.
    #
    # The stat is published instead ([[never-handroll-probes]]: a missing stat is a stat to add, not
    # a SELECT to write at the call site) — `mantle.db.lattice.vertex.LatticeArtifactStore.edge_mark`
    # is the same one-line delegation `LatticeGraphStore.edge_mark` already provides. A face that
    # does not publish it reads unverifiable, the same answer returned for a face with no `.db`,
    # which callers must treat as "cannot certify", never as "unchanged".
    fn = getattr(arts, "edge_mark", None)
    if fn is None:
        return None
    try:
        n, hi, exhaustive = fn(artifact_id)
    except Exception:
        return None
    if not exhaustive:
        return None       # a mark over a prefix would verify clean for ever — unverifiable, not rounded
    return (ver, (n, hi))


def set_stamp(store: Any, content_type: str, *, cap: Optional[int] = None) -> Optional[Stamp]:
    """The freshness stamp of a whole content type — `(rows, max _seq)` — or `None` if
    unverifiable.

    For a derivation whose input is a set rather than one artifact. `match._offers` is the case:
    48 operator artifacts, measured 215–270 ms to rebuild, so hanging it on the whole-store
    `write_mark` would discard it every time a chat message landed. This asks the question the
    cache actually depends on — has anything of this type been written? — and nothing wider."""
    if cap is None:
        return None
    arts = _artifacts(store)
    fn = getattr(arts, "content_type_mark", None)
    if fn is None:
        return None
    try:
        rows, hi, exhaustive = fn(content_type, cap=cap)
    except Exception:
        return None
    return (int(rows), int(hi)) if exhaustive else None


__all__ = ["write_mark", "stamp", "set_stamp", "Mark", "Stamp"]
