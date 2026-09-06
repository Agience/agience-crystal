"""crystal.ontology — the coordinate. What a corpus contains, and how far apart two things in it are.

crystal is the only component below both consumers that read the ontology — ember runs it, and
chorus's personas read it — and the dependency between crystal and mantle is zero in both
directions: neither imports the other. So this module carries no store implementation of its own:

  * the store is passed (`store=`), bound (`driver.bind(store)`), or supplied by a host-registered
    provider (`driver.set_default_store_provider(open_store)`) — the same pattern crystal already
    uses for its instrument and `beam.reach` uses for `keyring=`/`lightcone=`;
  * a read with none of the three raises `driver.OntologyStoreRequired`, at the point of use. It
    never returns an empty ontology, because an empty ontology answers "this corpus does not contain
    that concept" — a fabricated measurement ([[absence-is-not-an-affirmative-claim]]);
  * the freshness stamp is read off the store face's published `edge_mark`
    ([[never-handroll-probes]]).

The store contract — what a store must publish for this module to read an ontology from it,
including the raw-SQL reach `driver._conn()` uses (`_observe(store).db.read()`,
`SELECT dst, label FROM edge WHERE src=?`, not a graph API call) — is written down in `driver.py`'s
module header. It is part of the contract rather than an implementation detail a double may skip: a
double that omits `.db` cannot serve hypernyms for any synset whose taxonomy lives only in edges.

Modules:
  * `driver`       — the ontology driver: concepts, lemmas, hypernyms, IC, morphology. Holds the
                     store resolution and every cache, and is the only module that touches a store
                     without being handed one.
  * `geometry`     — the coordinate: JC distance, IC bounds, the dense basis. Reads the store zero
                     times; reaches `driver` for structure. Its IC-bound argument is hand-written,
                     not derived.
  * `lookup`       — surface forms -> concepts, and one associative hop.
  * `freshness`    — is a cached derivation still the derivation of what is in the store?
  * `transducer`   — the persisted-xi measurement read + the shared instance cache.
  * `coupling`     — the sign and names a semantic relation carries (data, no store).
  * `seed_lattice` — the build: lays the keyed substrate (stored IC, `lex:<lang>` edges, the
                     transducer artifact). Every function takes `store` explicitly.

`corpus_stats` (df/IDF off `mantle.db.lattice.fts`) and `embed` (fitting against
`mantle.search.anchors`) stay in mantle: both measure a mantle index at module scope, so they are
readings of the store's own structures, not coordinates, and moving them would create the
`crystal -> mantle` edge this module's dependency direction rules out.

`geometry` imports numpy, so `crystal[ontology]` declares it. Base `import crystal` stays free of
numpy and every instrument — see `crystal/__init__.py`.
"""
from __future__ import annotations

__all__ = ["driver", "geometry", "lookup", "freshness", "transducer", "coupling", "seed_lattice"]
