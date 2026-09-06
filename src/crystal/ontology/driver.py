"""The ontology driver — the reasoning layer's structure, read from our corpus, not from a package.

What is served here is an ontology; WordNet is the dataset this corpus happens to hold. The id
prefix, content type, POS alphabet and is-a edge labels are read from the stored `language:*`
transducer spec, with the WordNet values as defaults (`_ONTOLOGY_DEFAULTS`). A second ontology drops
in as data.

Concept structure lives in the corpus as artifacts: `hypernyms` (the is-a graph),
`instance_hypernyms`, `ic` (Resnik information content) and `lemma_counts` (SemCor sense-frequency),
alongside the gloss/lemmas/pos. Everything the geometry / activation / templates / forgetting layers
need is in the store, and this module serves it.

Drop-in: `from crystal.ontology import driver as wn` — call sites still spell it `wn`, so `wn.synset(…)`
and `wn.NOUN` read exactly as they always did. It reproduces the small slice of the nltk WordNet API this
codebase actually uses:  NOUN/VERB/… constants · synset(name) · synsets(word, pos) · morphy(word, pos) ·
Synset.name()/pos()/ic()/hypernyms()/instance_hypernyms()/lemmas()/common_hypernyms() · Lemma.name()/count().

The index is loaded once per process from the store (WordNet is static, so it never needs reloading)
and held as a module singleton — first access pays the load, everything after is in-memory.

The store resolves in one order: an explicit argument, then a bound store (`bind`), then the
host's registered default. The third rung is kept because a script that legitimately has no store
(the enrichment drains, `serve`'s warm-up) is a real caller, and it says so through `logging` once
per process rather than being indistinguishable from a correctly-scoped call.

The store arrives by injection because of where this module lives: crystal does not import mantle,
and mantle does not import crystal — the dependency is zero in both directions — so this module
names no store implementation — only a store's shape, duck-typed throughout. The third resolution
rung is `set_default_store_provider(fn)`:
the host names its own process default, exactly as `crystal.crystal` takes its instrument injected
and `beam.reach` takes `keyring=`.

An unregistered, unbound, unpassed read raises `OntologyStoreRequired`. It does not return an empty
ontology and it does not return `None`. An empty ontology reads as *"this corpus does not contain
that concept"* — a measurement, and a fabricated one
([[absence-is-not-an-affirmative-claim]]). This module never answers a question it was never given
a substrate for.

The store contract — what a store must publish for this module to read an ontology from it.

Stated here because it is reached through, not merely called: `_conn()` is `_observe(store).db.read()`,
raw SQL over `edge` through the artifacts face. That reach is part of the contract, not an
implementation detail a double may skip — a double lacking `.db` cannot serve hypernyms for any
synset whose taxonomy lives only in edges:

    store           `.artifacts` -> the artifacts face, or be the face itself (`_arts` accepts both)

    the face        `.get_artifact(id) -> dict | None`         one keyed read — the chat path
                    `.list_artifacts(content_type=…) -> iter`  the whole-corpus build path
                    `.write_mark() -> tuple | None`            the freshness gate (`freshness.write_mark`)
                    `.version_of(id) -> (origin, seq) | None`  the per-artifact discriminator
                    `.edge_mark(id) -> (n, hi, exhaustive)`    the edge half of that discriminator
                    `.db.read() -> DB-API connection`          raw SQL over `edge`, see `_conn`

A face that cannot answer `write_mark` is not broken — `_gate` treats it as a store that will not
certify its own freshness and does not cache against it, which is the honest reading. A face that
cannot answer `.db.read()` cannot serve an ontology at all: hypernyms come off `edge WHERE src=?`
for every corpus that carries no `hypernyms` field on the doc, which is the corpus on node 71.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

# POS constants — same letters WordNet/nltk use (satellite adjectives are "s"). These stay module
# constants because they are the drop-in nltk API surface callers spell as `wn.NOUN`; what varies
# per-ontology is the alphabet and its order, which `_pos_order()` reads from the spec.
NOUN, VERB, ADJ, ADV, ADJ_SAT = "n", "v", "a", "r", "s"
_ALL_POS = (NOUN, VERB, ADJ, ADV, ADJ_SAT)

# ── the ontology's vocabulary, when the spec does not carry it ────────────────────────────────
_ONTOLOGY_DEFAULTS: Dict[str, object] = {
    "id_prefix": "wn-",                 # `wn-dog.n.01` — the artifact id for synset `dog.n.01`
    "content_type": "text/x-wordnet",   # the discriminator `list_artifacts` filters the build on
    "pos_order": list(_ALL_POS),        # nltk's `synsets()` order: noun before verb before adj…
    "isa_labels": ["hypernym"],                     # the edges that mean is-a
    "instance_labels": ["instance_of", "instance_hypernym"],   # …and its instance form
    "entry_label": "lex:en",            # `lemma:<surface> --lex:en--> wn-<synset>` — the entry edge
    "lemma_prefix": "lemma:",           # the entry edge's source-node prefix
}

_LOCK = threading.Lock()
_INDEX: Optional[Tuple[Dict[str, "Synset"], Dict[Tuple[str, str], List[str]]]] = None
_IC_STATS: Optional[Dict[str, int]] = None
# (surface form, pos) -> base lemma. Filled from the source's own `forms` at index load.
_EXC: Dict[Tuple[str, str], str] = {}

# ── What the stored `ic` was measured from ────────────────────────────────────────────────────
# The corpus's own record of its IC measurement, kept as an artifact (the shape
# `projection.BASIS_ID` already uses for the corpus basis) rather than as a field repeated on
# 676,225 synsets: it describes one measurement taken over the whole corpus, so it is one row.
#
IC_BASIS_ID = "geom.ic-basis"
IC_BASIS_CT = "application/vnd.agience.ic-basis+json"
INTRINSIC_IC_SOURCE = "intrinsic"
INTRINSIC_IC_FORMULA = "1 - log(desc(s)+1)/log(N+1)"     # Seco et al., over the is-a tree


def ic_basis(store=None) -> Dict[str, object]:
    """The corpus's record of what its stored `ic` was measured from — `{}` when it carries none.

    `{"source": "intrinsic", "n": <corpus size>, "formula": ...}` for the tree-derived measurement;
    a corpus enriched from an external frequency corpus records its own `source` and is then left
    alone by the derivation (see `_load_index`)."""
    try:
        doc = _observe(store).get_artifact(IC_BASIS_ID) or {}
    except Exception:
        return {}
    b = doc.get("basis")
    return dict(b) if isinstance(b, dict) else {}


def derive_intrinsic_ic(idx: Dict[str, "Synset"]) -> Dict[str, float]:
    """Intrinsic IC (Seco et al.) for every synset in `idx`, read off the is-a tree `idx` carries:

        IC(s) = 1 - log(|descendants(s)| + 1) / log(N + 1)

    A synset with many descendants is general (low IC); a leaf is maximally specific (IC -> 1).
    Nothing is imported and nothing is chosen — the structure is the measurement, and this is the
    single derivation rather than two implementations that can drift apart."""
    import math as _math
    desc: Dict[str, int] = {}
    for _n_ in idx:
        seen_up, stack = set(), list(idx[_n_]._hyper) + list(idx[_n_]._inst)
        while stack:                       # descendants, not children: credit every ancestor once
            q = stack.pop()
            if q in seen_up or q not in idx:
                continue
            seen_up.add(q)
            desc[q] = desc.get(q, 0) + 1
            stack.extend(list(idx[q]._hyper) + list(idx[q]._inst))
    _logN = _math.log(float(len(idx)) + 1.0)
    return {n: 1.0 - (_math.log(desc.get(n, 0) + 1.0) / _logN) for n in idx}


def ic_coverage(*, store=None) -> Dict[str, object]:
    """How much of the loaded WordNet actually carries information content.

    The geometry, the category layer and the forgetting curve all compute over IC and all treat a
    missing value as 0.0. Without this, a corpus that was never enriched — or one caught mid-run,
    since the enrichment drain is keyed on `ic IS NULL` and its `--force` path removes `ic`
    corpus-wide before rewriting — is indistinguishable from a corpus of genuinely zero-IC
    synsets, and every metric derived from it reads as a measurement. Report this next to any
    IC-derived number rather than assuming the enrichment ran."""
    _index(store)                               # ensure loaded
    st = dict(_IC_STATS or {"synsets": 0, "with_ic": 0, "without_ic": 0,
                            "with_ic_se": 0, "without_ic_se": 0})
    n = int(st.get("synsets") or 0)
    st["coverage"] = round(int(st.get("with_ic") or 0) / n, 4) if n else None
    # Reported SEPARATELY, not folded into `coverage`: `ic` and `ic_se` are two channels and a
    # corpus routinely has full coverage of the first and none of the second. One number averaging
    # them would report a half-enriched corpus as half-covered in BOTH, which is false in both
    # directions.
    st["se_coverage"] = round(int(st.get("with_ic_se") or 0) / n, 4) if n else None
    return st


class WordNetError(Exception):
    """Raised for an unknown synset name (mirrors nltk.corpus.reader.wordnet.WordNetError)."""


class RankUnavailable(WordNetError):
    """Raised when a word's senses carry no stored `rank`, so they cannot be ordered by sense
    frequency. Sorting them anyway falls through to the synset name and returns an alphabetical
    list that still presents as sense-rank order — the measured "dog -> the fireplace andiron"
    failure. An ordering the source did not supply is not an ordering."""


class _Lemma:
    """A word-sense pairing. Only .name() and .count() (SemCor frequency) are used by the reasoning."""
    __slots__ = ("_name", "_count")

    def __init__(self, name: str, count: int):
        self._name = name
        self._count = count

    def name(self) -> str:
        return self._name

    def count(self) -> int:
        return self._count

    def __repr__(self) -> str:
        return f"Lemma('{self._name}')"


class Synset:
    """A WordNet sense, backed by its enriched `wn-*` artifact. Immutable; identified by name.

    `_store` is None for a synset built without one; hypernym and instance-hypernym resolution then
    falls back to the ambient index rather than substituting a different store silently — see the
    class note on `hypernyms()`."""
    __slots__ = ("_name", "_pos", "_hyper", "_inst", "_ic", "_ic_se", "_counts", "_store")

    def __init__(self, name: str, pos: str, hyper: List[str], inst: List[str],
                 ic: float, counts: Dict[str, int], ic_se: Optional[float] = None, store=None):
        self._name = name
        self._pos = pos
        self._hyper = hyper
        self._inst = inst
        self._ic = ic
        self._ic_se = ic_se
        self._counts = counts
        self._store = store

    def name(self) -> str:
        return self._name

    def pos(self) -> str:
        return self._pos

    def ic(self) -> float:
        """Resnik information content (stored — derived once from the Brown corpus during enrichment).

        Returns 0.0 when the field is absent, because every downstream consumer does arithmetic
        with this. Use `has_ic()` to tell the two apart — see the class note in `_load_index`."""
        return self._ic if self._ic is not None else 0.0

    def ic_se(self) -> Optional[float]:
        """The second channel: standard error of this synset's IC, in nats, or `None`.

        Written by the Laplace-smoothing enrichment path (`geometry.smooth_ic`) as
        `sqrt((1 - p) / cum')`. Unlike `ic()` this returns `None`, not 0.0, when absent — see
        `geometry.ic_se_of`. 0.0 would assert "this IC is exact", which is a claim, whereas None
        says "unknown", which is the truth for any corpus enriched before `ic_se` existed. Callers
        do not do unconditional arithmetic with an uncertainty the way they do with a value, so the
        `ic()`-style 0.0 convention buys nothing here and costs the distinction."""
        return self._ic_se

    def has_ic_se(self) -> bool:
        """Was a standard error actually stored? (`ic_se() is not None`, spelled for symmetry
        with `has_ic()`.)"""
        return self._ic_se is not None

    def has_ic(self) -> bool:
        """Was information content actually stored for this synset, distinct from `ic()`'s 0.0
        fallback — what makes coverage countable (`ic_coverage()`)."""
        return self._ic is not None

    def hypernyms(self) -> List["Synset"]:
        # The store this synset came from — never the ambient default. See the class note.
        return [s for s in (_resolve(n, self._store) for n in self._hyper) if s is not None]

    def instance_hypernyms(self) -> List["Synset"]:
        return [s for s in (_resolve(n, self._store) for n in self._inst) if s is not None]

    def lemmas(self) -> List[_Lemma]:
        return [_Lemma(n, c) for n, c in self._counts.items()]

    def _closure(self) -> Dict[str, "Synset"]:
        """This synset plus ALL its ancestors (transitive hypernyms + instance-hypernyms), by name.
        Matches nltk's _all_hypernyms (which includes the synset itself)."""
        seen: Dict[str, "Synset"] = {}
        stack = [self]
        while stack:
            s = stack.pop()
            if s._name in seen:
                continue
            seen[s._name] = s
            stack.extend(s.hypernyms())
            stack.extend(s.instance_hypernyms())
        return seen

    def common_hypernyms(self, other: "Synset") -> List["Synset"]:
        a = self._closure()
        b = other._closure()
        return [a[n] for n in a if n in b]

    def __repr__(self) -> str:
        return f"Synset('{self._name}')"

    def __eq__(self, other) -> bool:
        return isinstance(other, Synset) and other._name == self._name

    def __hash__(self) -> int:
        return hash(self._name)


# ══ Keyed lazy path ═══════════════════════════════════════════════════════════════════════════════
# The whole-corpus `_load_index` below is the build path — it reads all 555k synsets and both the
# 460 MB doc scan and the (unindexed-label) 42 s hypernym-edge scan to materialize one in-memory index.
# The chat path needs none of that: a synset is one keyed `get_artifact`, its hypernyms one keyed
# `edges WHERE src=?` (ix_e_src), its IC a stored value on the doc, and word→senses a keyed reverse
# `lex:en` edge lookup — the language transducer's entry. This section serves all of that lazily, per name,
# and caches. It activates only once the substrate exists (`op.transducer.language.en` written by the
# tekton, after the `lex:en` edges and stored IC); until then every accessor uses the full load,
# so nothing depends on the build having run.
import json as _json

# name -> (generation it was verified at, its freshness stamp, the Synset, the artifact id it
# resolved to). The stamp is what makes a repair to the corpus reach a running process; see `_gate`
# and `ontology/freshness.py`. The id is carried because a concept name does not determine it — see
# `concept_ids`.
_CACHE: Dict[str, Tuple[int, object, "Synset", str]] = {}
_KEYED_READY: Optional[bool] = None
_LANG_SPEC: Optional[Dict[str, object]] = None
from prism.grounding import TRANSDUCER_OP      # op-id prefix — "op.transducer."
_TRANSDUCER_ID = TRANSDUCER_OP + "language.en"


_SOURCE = None            # the artifacts face every cache below was built from — see `_arts`
_AMBIENT_WARNED = False

# The host's process-default store, `() -> store`. `None` means no host named one — see `_arts`.
_PROVIDER = None

#: The store `_PROVIDER` produced, kept because the provider's answer is a PROCESS FACT and asking
#: for it again cannot produce a different one.
#:
#: `set_default_store_provider` already promised this — "the process default is resolved on first
#: ambient use" — and `_arts` was calling the provider on EVERY ambient read instead. Measured under
#: cProfile on a live pool-20 ranking: 4,895 calls to `_arts`, 4,895 `open_store()` calls behind
#: them, and 96 of the pass's 102 seconds inside `boto3.client` building an S3 content tier per
#: ontology lookup. An ontology lookup reads no content and needs no content tier; it was paying
#: for one thousands of times per query.
#:
#: This is NOT a cache of an ontology read — those are `_INDEX` / `_CACHE` / `_EXC` and they are
#: keyed on the store and invalidated by `_invalidate`. It is the handle itself, and it is dropped
#: whenever the answer could change: a new provider, or a `bind` / `_release` that names a
#: different substrate.
_DEFAULT = None


class OntologyStoreRequired(RuntimeError):
    """No store was passed, none is bound, and no host registered a default.

    Raised at the point of use, never at import, and never in place of an answer. The alternative
    — returning an empty index — makes every `synset()` answer `None` and every `synsets()` answer
    `[]`, which readers up the stack correctly interpret as *this corpus does not contain that
    concept*. That is a measurement, and it would be fabricated: nothing was measured, because
    there was nothing to measure it on."""


def set_default_store_provider(fn) -> None:
    """Register the host's process-default store: `() -> store`, or `None` to unregister.

    The one seam this module has to a store implementation, and it points outward: crystal declares
    what a store must do (see the module header's contract) and never names one; the host — which
    knows what it is running on — hands the concrete one down. `agience-ember` registers
    `mantle.shard.local_store.open_store`; a node running on some other lattice registers that
    instead, and nothing here changes.

    It is a provider, not a store: the process default is resolved on first ambient use, not at
    import, so registering it costs nothing on a host that always passes `store=`. Resolved ONCE —
    `_DEFAULT` holds what it produced, and registering a new provider drops it, because the point
    of registering one is that the next read uses it."""
    global _PROVIDER, _DEFAULT
    _PROVIDER = fn
    _DEFAULT = None

# ── Freshness: the store's own write bookkeeping, so a data change reaches this process ───────
from crystal.ontology import freshness as _freshness

_MARK: object = None      # the store write mark every cache here was last verified against
_GEN: int = 0             # bumped on every move of that mark — see `generation()`
_INDEX_GEN: int = -1      # the generation `_INDEX` / `_IC_STATS` / `_EXC` were built at
_INDEX_FOREIGN = False    # …unless the index was installed, not read — see `install_index`
_SPEC_GEN: int = -1       # the generation `_KEYED_READY` / `_LANG_SPEC` were verified at
_SPEC_STAMP: object = None    # …and the transducer artifact's stamp they were read from


def generation() -> int:
    """How many times this module has seen the store change under it.

    It advances only when the store's write mark moved, i.e. only when something was really
    written. It is not a clock and not a counter of reads: on an idle store it never moves, which
    is exactly what lets the caches below stay caches."""
    return _GEN


def _gate(a) -> None:
    """Observe the store: poll its write mark, and if it moved, mark every cache here as needing
    re-verification.

    A mark that has moved says only that something changed. Attribution is then per-cache and by
    its own dependency:

      · `_CACHE`, `_KEYED_READY`, `_LANG_SPEC` each derive from one named artifact, so each is
        re-verified against `freshness.stamp()` — lazily, on next touch, so an entry nobody asks
        for costs nothing and a write that changed something else drops nothing.
      · `_INDEX` / `_IC_STATS` / `_EXC` derive from the whole `text/x-wordnet` population. There is
        no cheap exact stamp for that, so they are dropped wholesale on any move of the mark — an
        unverifiable freshness claim does not get to be made on the store's behalf
        ([[absence-is-not-an-affirmative-claim]])."""
    global _MARK, _GEN, _INDEX, _IC_STATS
    m = _freshness.write_mark(a)
    if m is not None and m == _MARK:
        return
    _MARK = m
    _GEN += 1
    if _INDEX_FOREIGN:
        return                    # an installed index is not this store's derivation to drop
    if _INDEX is not None or _IC_STATS is not None:
        _INDEX = None
        _IC_STATS = None
        _EXC.clear()


def bind(store) -> None:
    """Bind the store this module reads. The explicit parameterisation personas use.

    `store` may be a bundle (anything with `.artifacts`) or an artifacts face directly.
    `bind(None)` releases the binding and returns the module to the host's registered default —
    or, if no host registered one, to raising `OntologyStoreRequired` on the next read."""
    _arts(store) if store is not None else _release()


def _release() -> None:
    global _SOURCE, _DEFAULT
    with _LOCK:
        _SOURCE = None
        # The next ambient read resolves the default again. A release says "read whatever the host
        # points at now", and holding the handle from before it would answer the old question.
        _DEFAULT = None
    _invalidate()


def invalidate() -> None:
    """Drop every cache, because the substrate underneath them changed.

    Callers that legitimately change the substrate (an ingest, a transducer build) call this. There
    is nothing else to know."""
    _invalidate()


def install_index(idx: Dict[str, "Synset"], word_index: Dict[Tuple[str, str], List[str]],
                  *, ic_stats: Optional[Dict[str, int]] = None) -> None:
    """Install an index this module did not read from a store.

    Nothing in `src/` calls this: the callers are `agience-ember/tests/_fakes.py::
    _install_offline_wordnet` and `agience-chorus/src/sage/tests/test_match.py`, two test doubles
    that share this one back door rather than each keeping its own copy."""
    global _INDEX, _IC_STATS, _INDEX_FOREIGN
    _INDEX = (dict(idx), dict(word_index))
    _IC_STATS = dict(ic_stats) if ic_stats else None
    _INDEX_FOREIGN = True


def _invalidate(*, keep_installed: bool = False) -> None:
    """Drop every cache. Called when the store the caches were built from changes underneath them.

    The distinction is between a caller saying which substrate to read (`bind` / `_release` —
    those still clear it, exactly as `install_index` documents) and a store merely appearing as an
    argument. Only the second is exempt, and the index it preserves is still only ever consulted for
    a store that cannot be asked for an ontology at all (`_index`). A store that CAN be asked always
    answers for itself, even when its honest answer is an empty ontology."""
    global _INDEX, _IC_STATS, _KEYED_READY, _LANG_SPEC, _MARK, _GEN, _SPEC_STAMP, _INDEX_FOREIGN
    if not (keep_installed and _INDEX_FOREIGN and _INDEX is not None):
        _INDEX_FOREIGN = False
        _INDEX = None
    _IC_STATS = None
    _KEYED_READY = None
    _LANG_SPEC = None
    _SPEC_STAMP = None
    _MARK = None
    _GEN += 1
    _CACHE.clear()
    _EXC.clear()


def _arts(store=None):
    """The artifacts face to read from: explicit argument, then bound store, then the host's
    registered default, loudly — and `OntologyStoreRequired` when there is none of the three.

    Adopting an explicitly-named store as `_SOURCE` is deliberate: the index below is a process
    singleton, so this module can only be coherent with one store at a time, and the store the
    caller just named is the one it must be coherent with. A different store invalidates the caches
    rather than silently answering from the previous one's index — threading a `store=` argument
    through while keeping one cache keyed on nothing would just swap a wrong-store read for a
    first-store-wins read. A process that only ever passes nothing never triggers this branch."""
    global _SOURCE, _AMBIENT_WARNED, _DEFAULT
    a = getattr(store, "artifacts", None) or store
    if a is None:
        if _SOURCE is not None:
            return _SOURCE
        if _PROVIDER is None:
            raise OntologyStoreRequired(
                "the ontology has no store: nothing was passed as `store=`, nothing is bound via "
                "`bind(store)`, and no host has called `set_default_store_provider(...)`. "
                "An ontology read is a MEASUREMENT and there is nothing here to measure — see "
                "`crystal/ontology/driver.py`'s header for the store contract.")
        if not _AMBIENT_WARNED:
            _AMBIENT_WARNED = True
            _log.warning(
                "wn_store: no store passed and none bound — reading the PROCESS-DEFAULT store. "
                "The ontology read is a MEASUREMENT; pass `store=` or call wn_store.bind(store) "
                "so it is measured on the store the caller actually holds.")
        if _DEFAULT is None:
            s = _PROVIDER()
            if s is None:
                raise OntologyStoreRequired(
                    "the host's registered default-store provider returned None. A provider that "
                    "cannot produce a store must raise; returning None would make the ontology "
                    "answer 'unknown' for every concept in the corpus.")
            _DEFAULT = getattr(s, "artifacts", None) or s
        return _DEFAULT
    if a is not _SOURCE:
        #
        #     _SOURCE=None _INDEX=None _CACHE=0 _EXC=0 _KEYED_READY=None _LANG_SPEC=None
        #
        # holds at boot, when `_arts` is called exactly once — by `bind()` itself. There is no
        # earlier reader to be inconsistent with. A warning that cannot be true is the same defect as
        # a check that cannot fail: it costs nothing to emit, it reads as a real fault in the log,
        # and it trains the reader to ignore the line that will one day matter.
        #
        # The guard tests store identity; what it protects is cache validity. Those coincide only
        # when a cache exists. Nothing stale can be dropped when nothing has been built, so the
        # invalidation and the warning both belong behind that condition — and the genuine
        # cross-store thrash still warns, loudly.
        if _INDEX is not None or _CACHE or _EXC or _KEYED_READY is not None or _LANG_SPEC is not None:
            _log.warning("wn_store: reading a different store than the caches were built from — "
                         "dropping the loaded index and caches (was %s)",
                         "the process default" if _SOURCE is None else type(_SOURCE).__name__)
            # A store appearing in a call is not a caller saying which substrate to read, so an
            # installed index survives it — see `_invalidate`. Every store-derived cache still goes.
            _invalidate(keep_installed=True)
        _SOURCE = a
    return a


def _observe(store=None):
    """`_arts()`, plus the gate — the resolution to use when a store read is about to happen.

    Two names for two different things, deliberately. `_arts` answers *which store*; this answers
    *which store, as it is right now*. Everything in this module that goes to disk goes through
    here, and everything that answers from a cache does not — which is what keeps the poll off the
    hot path without anyone having to remember where the hot path is."""
    a = _arts(store)
    _gate(a)
    return a


def _conn(store=None):
    """The raw read connection. Observes the store, because it is about to read it anyway."""
    return _observe(store).db.read()


def _spec_str(key: str, store=None) -> str:
    v = _lang_spec(store).get(key)
    return str(v) if isinstance(v, str) and v else str(_ONTOLOGY_DEFAULTS[key])


def _spec_list(key: str, store=None) -> List[str]:
    v = _lang_spec(store).get(key)
    if isinstance(v, (list, tuple)) and v:
        return [str(x) for x in v if x]
    return list(_ONTOLOGY_DEFAULTS[key])            # type: ignore[arg-type]


def _prefix(store=None) -> str:
    """The ontology's artifact-id prefix (`wn-`), from the spec."""
    return _spec_str("id_prefix", store)


def _ct(store=None) -> str:
    """The ontology's content type (`text/x-wordnet`), from the spec."""
    return _spec_str("content_type", store)


def _pos_order(store=None) -> List[str]:
    """The POS alphabet in order — noun before verb before … — from the spec.

    This is ontology knowledge, not ours: it is what makes `synsets()` return senses in nltk's
    order. A source with a different tag set names it and needs no code change here."""
    return _spec_list("pos_order", store)


def _isa_labels(store=None) -> Tuple[List[str], List[str]]:
    """`(is_a, instance_of)` edge labels, from the spec. §13.1: relations are edges, and the label
    that means is-a is the source's word for it — `instance_hypernym` is OEWN's, `instance_of` the
    legacy corpus's. A reader that knew only one silently lost every instance edge on the other."""
    return _spec_list("isa_labels", store), _spec_list("instance_labels", store)


def _n(x: str, store=None) -> str:
    x = str(x or "")
    p = _prefix(store)
    return x[len(p):] if p and x.startswith(p) else x


def concept_ids(name: str, store=None) -> Tuple[str, ...]:
    """The artifact id(s) a concept name addresses, in resolution order — the inverse of `_n`.

    ConceptNet is the larger half of this ontology (1,165,110 vertices against WordNet's 676,225),
    so treating the reader as a WordNet-only index is not a tidiness point; it would leave most of
    the corpus unaddressable. A consolidated concept is more of a concept than its members, not less.

    Our own prefix is tried first, so the hot path (a WordNet name) is one keyed read, and the
    second candidate costs nothing unless the first misses."""
    p = _prefix(store)
    n = str(name or "")
    if not n:
        return ()
    if p and n.startswith(p):
        return (n,)                       # already an id in our own space — do not prefix twice
    return (p + n, n) if p else (n,)


def is_lemma_id(x: str, store=None) -> bool:
    """Is this vertex a lemma (a surface form) rather than a concept?

    The ontology's spec already names the lemma vertex prefix (`lemma_prefix`, `"lemma:"`), so this
    is read, not typed. It matters because a lexical entry edge (`lemma:cow --lex:en--> wn-cow.n.01`)
    joins a concept to its own surface, not to a neighbouring concept — a propagator that treats it
    as a hop is walking out of the ontology and into the dictionary.

    A lemma is not a concept, and no prefix of a concept id is consulted. Excluding lemma vertices
    here keeps ~12k futile resolutions per turn off the hot path — measured, cold spread
    2.44 s -> 4.89 s without it."""
    p = _spec_str("lemma_prefix", store)
    return bool(p) and str(x or "").startswith(p)


def concept_artifact(name: str, store=None, *, arts=None) -> Dict[str, object]:
    """The stored artifact a concept name addresses — `{}` when the store holds none.

    The rendering half of `concept_ids`. The answer path fetches a concept's artifact to read its
    gloss and its citation; a caller that instead types `"wn-" + name` directly fetches nothing for
    a ConceptNet or colimit-canonical concept, and reporting an empty gloss with `cited_from`
    defaulted to `cite.wordnet` would cite a source that was never read
    ([[absence-is-not-an-affirmative-claim]]). Absent evidence must come back absent, and present
    evidence must come back under its own source's name."""
    face = arts if arts is not None else _observe(store)
    for aid in concept_ids(name, store if arts is None else None):
        doc = face.get_artifact(aid)
        if doc:
            return doc
    return {}


def _spec_fresh(arts) -> None:
    """Re-verify `_KEYED_READY` / `_LANG_SPEC` against the transducer artifact, once per generation.

    Both derive from exactly one artifact, so the exact question is askable and cheap: has
    `op.transducer.language.en` itself been written since we read it? Measured 5.1 µs, and only
    after the gate has already reported a write. A changed stamp drops both; an unchanged one is a
    positive verification, not an assumption. `stamp() is None` means the store cannot answer, and
    that drops them too — see `freshness` on why unverifiable must not read as fresh, which would
    otherwise let a stale spec answer with the wrong alphabet."""
    global _KEYED_READY, _LANG_SPEC, _SPEC_GEN, _SPEC_STAMP
    if _SPEC_GEN == _GEN:
        return
    st = _freshness.stamp(arts, _TRANSDUCER_ID)
    if st is None or st != _SPEC_STAMP:
        _KEYED_READY = None
        _LANG_SPEC = None
        _SPEC_STAMP = st
    _SPEC_GEN = _GEN


def _installed_index_answers(store) -> bool:
    """An installed index answers on its own, because there is no store to ask.

    Deliberately narrow: it requires that nothing was passed and nothing is bound. A caller that
    names a store gets that store, always — an installed index never overrides an explicit one."""
    return store is None and _SOURCE is None and _INDEX_FOREIGN and _INDEX is not None


def _keyed_ready(store=None) -> bool:
    """Is the keyed substrate present? The tekton writes the transducer artifact last — after the `lex:en`
    entry edges and the stored IC — so its presence is the single, keyed readiness flag."""
    global _KEYED_READY
    if _installed_index_answers(store):
        return False                         # an installed index is not the keyed substrate
    arts = _arts(store)                      # resolve first: it may invalidate _KEYED_READY
    _spec_fresh(arts)                        # …and the transducer artifact may have been rewritten
    if _KEYED_READY is None:
        arts = _observe(store)               # going to the store: observe it while we are here
        with _LOCK:
            if _KEYED_READY is None:
                try:
                    _KEYED_READY = arts.get_artifact(_TRANSDUCER_ID) is not None
                except Exception:
                    _KEYED_READY = False
    return bool(_KEYED_READY)


def _lang_spec(store=None) -> Dict[str, object]:
    """The `language:<lang>` transducer artifact's spec — carried as data in the artifact (read once,
    keyed, cached). It carries morphy's exceptions, and (when the writer emits them) the ontology's
    own id prefix, content type, POS order and edge labels."""
    global _LANG_SPEC
    arts = _arts(store)                      # resolve first: it may invalidate _LANG_SPEC
    _spec_fresh(arts)                        # …and the transducer artifact may have been rewritten
    if _LANG_SPEC is None:
        arts = _observe(store)               # going to the store: observe it while we are here
        try:
            doc = arts.get_artifact(_TRANSDUCER_ID) or {}
        except Exception:
            doc = {}
        _LANG_SPEC = dict((doc.get("spec") or {}))
    return _LANG_SPEC


def _synset_from_doc(name: str, doc: Dict[str, object], store=None, *, aid: str = None) -> "Synset":
    """Build one Synset from its keyed artifact. Hypernyms come from keyed `edges WHERE src=?`
    (falling back to the doc field for corpora that still carry it); IC is the stored value.

    `aid` is the id the doc was read from — passed in rather than re-derived, because a name does
    not determine its id (`concept_ids`). Re-deriving it here would key the taxonomy read on
    `wn-cn-cow` and hand back a concept with no parents, which reads exactly like a root."""
    pos = doc.get("pos") or (name.rsplit(".", 2)[-2] if name.count(".") >= 2 else NOUN)
    _c = doc.get("lemma_counts")
    counts = {str(k): int(v) for k, v in _c.items()} if isinstance(_c, dict) else {}
    if not counts:
        counts = {str(x): 0 for x in (doc.get("lemmas") or []) if x}
    _ic_raw = doc.get("ic")
    _ic = None if _ic_raw is None else float(_ic_raw)
    _se_raw = doc.get("ic_se")
    _se = None if _se_raw is None else float(_se_raw)
    hyper: List[str] = [_n(h, store) for h in (doc.get("hypernyms") or [])]
    inst: List[str] = [_n(h, store) for h in (doc.get("instance_hypernyms") or [])]
    if not hyper and not inst:
        isa, inst_labels = _isa_labels(store)
        labels = list(isa) + list(inst_labels)
        try:
            src = aid or (concept_ids(name, store) or (_prefix(store) + name,))[0]
            rows = _conn(store).execute(
                "SELECT dst, label FROM edge WHERE src=? AND label IN (%s)"
                % ",".join("?" * len(labels)),
                tuple([src] + labels)).fetchall()
        except Exception:
            rows = []
        for dst, label in rows:
            (inst if label in inst_labels else hyper).append(_n(dst, store))
    return Synset(str(name), str(pos), hyper, inst, _ic, counts, _se, store=store)


def _get_synset(name: str, store=None) -> Optional["Synset"]:
    """Keyed, lazy, cached read of one synset by name — verified against the artifact it came from.

    A rebuild replaces the object rather than mutating it. That is what lets a cache built on this
    module's output (`geometry._DENSE_CACHE`, keyed on `generation()`) miss by construction instead
    of holding a vector derived from values the Synset no longer carries."""
    ent = _CACHE.get(name)
    if ent is not None and ent[0] == _GEN:
        return ent[2]                        # verified at the current observation: no store touch
    arts = _observe(store)                   # we are going to the store: observe it while we do
    gen = _GEN                               # …after the observation, which may have moved it
    # A cached entry is re-verified against the id it resolved to. Re-deriving the candidate order
    # here would re-verify `wn-<name>` for a concept that lives at `<name>` and read "changed" on
    # every call — the stamp must be taken on the artifact the Synset was actually built from.
    cands = (ent[3],) if ent is not None else concept_ids(name, store)
    for aid in cands:
        st = _freshness.stamp(arts, aid, edges=True)
        if ent is not None and st is not None and st == ent[1]:
            _CACHE[name] = (gen, st, ent[2], aid)    # re-verified: same artifact, same edges
            return ent[2]
        doc = arts.get_artifact(aid)
        if not doc:
            continue                                 # not in this id space — try the next candidate
        s = _synset_from_doc(name, doc, store, aid=aid)
        if st is not None:
            _CACHE[name] = (gen, st, s, aid)         # unverifiable ⇒ uncached, never assumed fresh
        return s
    _CACHE.pop(name, None)                           # gone from the store is a change, not a hit
    return None


def _resolve(name: str, store=None) -> Optional["Synset"]:
    """One synset by name — keyed when the substrate is ready, else via the whole-corpus index."""
    if _keyed_ready(store):
        return _get_synset(name, store)
    return _index(store).get(name)


def _entry_names(word: str, pos: Optional[str] = None, store=None) -> List[str]:
    """The language:en transducer's entry, keyed: `lemma:<word> --lex:en--> wn-<synset>`, ordered as nltk's
    `synsets()` orders — POS first (noun before verb before …), then the source's own (proper-noun,
    sense-rank). POS is carried on the edge, so filtering and ordering need no doc read."""
    order = _pos_order(store)
    rank = {p: i for i, p in enumerate(order)}
    rows = _conn(store).execute(
        "SELECT dst, props FROM edge WHERE src=? AND label=?",
        (_spec_str("lemma_prefix", store) + word, _spec_str("entry_label", store))).fetchall()
    out: List[Tuple[int, int, float, str]] = []
    for dst, props in rows:
        p = _json.loads(props) if props else {}
        _pos = p.get("pos")
        if pos is not None and _pos != pos:
            continue
        out.append((rank.get(_pos, len(order)),         # POS group first — noun before verb, as nltk
                    int(p.get("proper", 0)),                # common before proper
                    float(p.get("rank")) if isinstance(p.get("rank"), (int, float)) else float("inf"),
                    _n(dst, store)))                        # then sense number, then name
    if out and all(_rk == float("inf") for _po, _pr, _rk, _nm in out):
        raise RankUnavailable(
            f"no sense rank stored for {word!r} ({len(out)} senses); ordering would be alphabetical, "
            "not sense-frequency — re-run the enrichment that materializes `rank`"
        )
    out.sort()
    return [nm for _po, _pr, _rk, nm in out]


def entry_prefix_exists(surface: str, store=None) -> bool:
    """Does the lexicon hold any entry whose surface starts with `surface`?

    The window is worth extending exactly while some entry still begins with it — asking the
    lexicon where to stop rather than measuring a maximum and capping at it. When none does, no
    longer window can match either, so the walk ends. That is a trie's own stopping rule: it needs
    no bound, and it is linear in the token count rather than quadratic, which is what makes
    removing the cap affordable on a document as well as on a query.

    Keyed range read on the indexed `edge.src` (`ix_e_src`), `LIMIT 1`, microsecond-scale for both a
    hit and a miss. The upper bound is the prefix with the highest code point appended, so the range
    is exactly the prefix's subtree of the index.

    A store that cannot answer returns `False`, which is the honest direction: an unanswerable
    lexicon does not extend the window, so the single tokens stand, exactly as if the compound were
    not held. It never invents a compound."""
    p = _spec_str("lemma_prefix", store) + (surface or "")
    try:
        row = _conn(store).execute(
            "SELECT 1 FROM edge WHERE src >= ? AND src < ? LIMIT 1",
            (p, p + "\U0010FFFF")).fetchone()
    except Exception:
        return False
    return row is not None


def _load_index(store=None) -> None:
    """Read every ontology artifact once and build (name -> Synset) + ((lemma,pos) -> [names])
    indexes, through `_arts`, the single store-resolution point."""
    global _INDEX, _IC_STATS
    arts = _observe(store)                   # a full-corpus read: observe the store before taking it
    prefix = _prefix(store)
    idx: Dict[str, Synset] = {}
    word: Dict[Tuple[str, str], List[str]] = {}
    n_missing_ic = 0
    n_missing_se = 0

    for a in arts.list_artifacts(content_type=_ct(store)):
        aid = a.get("id") or ""
        name = aid[len(prefix):] if prefix and aid.startswith(prefix) else aid
        if not name:
            continue
        pos = a.get("pos") or (name.rsplit(".", 2)[-2] if name.count(".") >= 2 else NOUN)
        counts = a.get("lemma_counts") or {}
        if not isinstance(counts, dict):
            counts = {}
        counts = {str(k): int(v) for k, v in counts.items()}
        #
        # The lemmas the row does carry are the answer, in source order (primary first). Their
        # count is 0 — honestly "no measured SemCor frequency", not a fabricated one — and every
        # caller of `count()` already defaults to 0 for an unknown word.
        if not counts:
            counts = {str(x): 0 for x in (a.get("lemmas") or []) if x}
        # `a.get("ic")` is read directly rather than via `float(a.get("ic") or 0.0)`, which would
        # fold three states into one number: field absent, field present-and-zero, and (because `or`
        # treats 0 as falsy) a genuinely stored 0. Reading it directly preserves absence as None, so
        # `has_ic()` can distinguish it, while the arithmetic value is unaffected. One pair is still
        # conflated at the source: `enrich_wordnet.py` writes a literal `ic=0.0` for synsets absent
        # from the nltk build, indistinguishable from a real root-level zero, and that one cannot be
        # recovered here.
        _ic_raw = a.get("ic")
        _ic_val = None if _ic_raw is None else float(_ic_raw)
        # The second channel, counted separately. A corpus can carry `ic` and no `ic_se` (every
        # corpus enriched before smoothing existed does), so one coverage number cannot stand for
        # both -- see `ic_coverage()`.
        _se_raw = a.get("ic_se")
        _se_val = None if _se_raw is None else float(_se_raw)
        node = Synset(name, pos, list(a.get("hypernyms") or []),
                      list(a.get("instance_hypernyms") or []), _ic_val, counts, _se_val,
                      store=store)
        if _ic_val is None:
            n_missing_ic += 1
        if _se_val is None:
            n_missing_se += 1
        idx[name] = node
        # word -> senses: from the synset's lemma names (fall back to the plain `lemmas` field)
        lemma_names = list(counts.keys()) or list(a.get("lemmas") or [])
        # `lemmas` is lowercased (it is the keyed-lookup field and lookup is case-insensitive);
        # `sense_ranks` keeps the written form, because that is where the source's case lives.
        # Match them case-insensitively and read the case off the rank key.
        for _lm, _fs in (a.get("forms") or {}).items():
            for _f in (_fs or []):
                _EXC.setdefault((str(_f).lower().replace(" ", "_"), pos), str(_lm).lower().replace(" ", "_"))
        _ranks = a.get("sense_ranks") or {}
        _by_lower = {str(k).lower(): (str(k), v) for k, v in _ranks.items()}
        for lm in lemma_names:
            _written, _r = _by_lower.get(lm.lower(), (lm, None))
            proper = 1 if _written != _written.lower() else 0
            word.setdefault((lm.lower().replace(" ", "_"), pos), []).append(
                (proper, float(_r) if isinstance(_r, (int, float)) else float("inf"), name))

    # ── The tree comes from edges (§13.1: relations are edges) ───────────────────────────────────
    # The OEWN ingest writes a synset's hypernyms as `hypernym` edges in the lattice. Read the
    # edges; fall back to the doc field for corpora that carry it instead — both shapes coexist,
    # and this reads whichever a given corpus provides.
    _isa, _inst_labels = _isa_labels(store)
    _labels = list(_isa) + list(_inst_labels)
    try:
        conn = arts.db.read()
        rows = conn.execute(
            "SELECT src, dst, label FROM edge WHERE label IN (%s)" % ",".join("?" * len(_labels)),
            tuple(_labels)).fetchall()
    except Exception:
        rows = []
    if rows:
        def _strip(x: str) -> str:
            x = str(x or "")
            return x[len(prefix):] if prefix and x.startswith(prefix) else x
        for src, dst, label in rows:
            node = idx.get(_strip(src))
            if node is None:
                continue
            target = _strip(dst)
            bucket = node._inst if label in _inst_labels else node._hyper
            if target not in bucket:
                bucket.append(target)

    # ── INTRINSIC IC — the metric derived from the tree, not imported ────────────────────────────
    # Jiang-Conrath needs information content: `IC(s1)+IC(s2)-2·IC(lcs)`.
    #
    # Intrinsic IC (Seco et al.) reads the same quantity off the tree we already have:
    #     IC(s) = 1 - log(|hyponyms(s)| + 1) / log(N)
    # A synset with many descendants is general (low IC); a leaf is maximally specific (IC -> 1).
    # Nothing is imported and nothing is chosen — the structure IS the measurement, which is why
    # this is the self-contained form rather than a substitute for the external one.
    #
    #
    # The trigger for re-deriving is provenance, not absence. `ic_basis(store)` is the corpus's own
    # record of what its IC was measured from (`geom.ic-basis`, written by `seed_lattice`). The
    # measurement is re-taken when that record says it was taken against a different corpus, or when
    # there is no record at all — a stored number that cannot state what it was measured from is not
    # a measurement this module may keep ([[absence-is-not-an-affirmative-claim]]). A corpus carrying
    # an external measurement (Resnik over Brown, `scripts/enrich_wordnet.py`) records a basis whose
    # `source` is not `intrinsic`, and is left untouched: the two shapes coexist, each stating what
    # it is rather than being told apart by a count of holes.
    _basis = ic_basis(store)
    _src = _basis.get("source")
    if _src and _src != INTRINSIC_IC_SOURCE:
        _fresh = True                 # an external measurement — `N` does not describe it
    else:
        _fresh = (_src == INTRINSIC_IC_SOURCE
                  and _basis.get("n") == len(idx) and not n_missing_ic)
    if idx and not _fresh:
        for _n_, _v_ in derive_intrinsic_ic(idx).items():
            idx[_n_]._ic = _v_
        n_missing_ic = 0

    # ── SENSE ORDER (§13.14) ─────────────────────────────────────────────────────────────────────
    #
    # Sense order follows the source's own sense number (LMF `sense_ranks`) for each word. A corpus
    # that carries no ranks sorts by name, which is not a statement about meaning.
    for key, pairs in list(word.items()):
        pairs.sort(key=lambda t: (t[0], t[1], t[2]))   # common before proper, then sense number
        word[key] = [n for _p, _r, n in pairs]
    _IC_STATS = {"synsets": len(idx), "with_ic": len(idx) - n_missing_ic, "without_ic": n_missing_ic,
                 "with_ic_se": len(idx) - n_missing_se, "without_ic_se": n_missing_se}
    _INDEX = (idx, word)


def _cannot_serve_an_ontology(store) -> bool:
    """Can this store be asked for an ontology at all? Not: does it have one.

    A delegate's store is its cognition store; in production it is the lattice and carries both a
    `list_artifacts` face and a `.db` connection, but a minimal fake can carry neither — so a handle
    missing either cannot serve an ontology, regardless of what an installed index holds."""
    a = getattr(store, "artifacts", store)
    return not (callable(getattr(a, "list_artifacts", None)) and getattr(a, "db", None) is not None)


def _index(store=None) -> Dict[str, "Synset"]:
    global _INDEX, _INDEX_GEN, _INDEX_FOREIGN
    if _installed_index_answers(store):
        return _INDEX[0]                     # installed, not read — there is no store to observe
    # An installed index answers for a store that cannot be asked. This is not the ambient fallback:
    # nothing is opened, nothing is guessed, and a store that CAN be asked always answers for itself
    # even when its answer is an empty ontology.
    if store is not None and _INDEX_FOREIGN and _INDEX is not None and _cannot_serve_an_ontology(store):
        return _INDEX[0]
    _observe(store)                          # resolve + observe first: either may invalidate _INDEX
    _stale = (not _INDEX_FOREIGN) and _INDEX_GEN != _GEN
    if _INDEX is None or _stale:
        with _LOCK:
            if _INDEX is None or ((not _INDEX_FOREIGN) and _INDEX_GEN != _GEN):
                g = _GEN
                _load_index(store)
                _INDEX_GEN = g
                _INDEX_FOREIGN = False       # this one IS the store's derivation
    return _INDEX[0]


def _word_index(store=None) -> Dict[Tuple[str, str], List[str]]:
    _index(store)
    return _INDEX[1]


# ── module-level API (the nltk.corpus.wordnet slice this codebase uses) ───────────────────────────
#
def synset(name: str, *, store=None) -> Synset:
    _observe(store)
    n = _resolve(name, store)
    if n is None:
        raise WordNetError(f"no synset {name!r} in store")
    return n


def synsets(word: str, pos: Optional[str] = None, *, store=None) -> List[Synset]:
    """All senses of `word` (optionally restricted to a POS), in sense-number order — as nltk returns.

    The lemma index is what makes this safe: a candidate is only accepted if it IS a lemma we hold,
    so `bus` -> `bu` (strip "s") never resolves, since `bu` is not a word we hold. No guessing
    survives."""
    _observe(store)                                      # public entry: observe the store — see `_gate`
    w = word.lower().replace(" ", "_")
    if _keyed_ready(store):                              # keyed language-transducer entry — no full load
        names = _entry_names(w, pos, store)
        if not names:
            base = morphy(w, pos, store=store)
            if base and base != w:
                names = _entry_names(base, pos, store)
        return [s for s in (_get_synset(n, store) for n in names) if s is not None]
    wi = _word_index(store)
    _poss = _pos_order(store)
    if pos is not None:
        names = wi.get((w, pos), [])
    else:
        names = [n for p in _poss for n in wi.get((w, p), [])]
    if not names:
        base = morphy(w, pos, store=store)
        if base and base != w:
            if pos is not None:
                names = wi.get((base, pos), [])
            else:
                names = [n for p in _poss for n in wi.get((base, p), [])]
    idx = _index(store)
    return [idx[n] for n in names if n in idx]


# nltk's morphy strips inflection via exception lists + suffix rules; we approximate with the rules and
# validate the candidate against our own lemma index (we know every real lemma). Used only to normalize
# taught-relation verbs for matching, so an approximation is sufficient.
_RULES = {
    NOUN: [("s", ""), ("ses", "s"), ("xes", "x"), ("zes", "z"), ("ches", "ch"), ("shes", "sh"),
           ("men", "man"), ("ies", "y")],
    VERB: [("s", ""), ("ies", "y"), ("es", "e"), ("es", ""), ("ed", "e"), ("ed", ""),
           ("ing", "e"), ("ing", "")],
    ADJ: [("er", ""), ("est", ""), ("er", "e"), ("est", "e")],
    ADV: [],
}


def morphy(word: str, pos: Optional[str] = None, *, store=None) -> Optional[str]:
    """The base form of an inflected word, or None. Exceptions first, then the detachment rules,
    every candidate validated against the lemma index we actually hold."""
    _observe(store)                                 # public entry: observe the store — see `_gate`
    w = word.lower().replace(" ", "_")
    # The rule table is keyed by POS, so the alphabet to try is the spec's minus the satellite
    # (which carries no detachment rules of its own) — i.e. exactly the tags `_RULES` names.
    poss = [pos] if pos else [p for p in _pos_order(store) if p in _RULES]
    if _keyed_ready(store):                              # keyed: existence = a `lex:en` entry edge
        exc = _lang_spec(store).get("exceptions") or {}  # source irregulars, carried in the transducer artifact
        def _known(cand: str, p: str) -> bool:
            return bool(_entry_names(cand, p, store))
        for p in poss:                                   # already a known lemma of that POS
            if _known(w, p):
                return w
        for p in poss:                                   # the SOURCE's irregulars, before any rule
            base = exc.get(w + "|" + p) or (exc.get(w) if isinstance(exc.get(w), str) else None)
            if base and _known(base, p):
                return base
        for p in poss:                                   # else the detachment rules, validated keyed
            for suf, repl in _RULES.get(p, []):
                if suf and w.endswith(suf):
                    cand = w[: -len(suf)] + repl
                    if _known(cand, p):
                        return cand
        return None
    wi = _word_index(store)
    for p in poss:                                  # already a known lemma of that POS
        if (w, p) in wi:
            return w
    for p in poss:                                  # the SOURCE's irregulars, before any rule
        base = _EXC.get((w, p))
        if base and (base, p) in wi:
            return base
    for p in poss:                                  # else try inflection rules, validate against index
        for suf, repl in _RULES.get(p, []):
            if suf and w.endswith(suf):
                cand = w[: -len(suf)] + repl
                if (cand, p) in wi:
                    return cand
    return None


def all_synsets(pos: Optional[str] = None, *, store=None) -> List[Synset]:
    idx = _index(store)
    return [s for s in idx.values() if pos is None or s.pos() == pos]


__all__ = [
    "NOUN", "VERB", "ADJ", "ADV", "ADJ_SAT", "WordNetError", "Synset",
    "bind", "synset", "synsets", "morphy", "all_synsets", "ic_coverage",
    "entry_prefix_exists",
]
