"""The ontology has no store of its own; a missing one raises rather than answering an empty ontology.

The coordinate lives in `crystal.ontology`, not `mantle.ontology`: crystal may not import mantle
(`ARCHITECTURE-TARGET.md` §2), so the driver's third store rung is `set_default_store_provider(fn)`,
named by the host.

The failure mode this file exists for: the cheap way to avoid importing mantle is to let an
unresolved store fall through — `_INDEX` stays empty, `synset()` returns `None`, `synsets()` returns
`[]`, `ic_of()` finds nothing. Every one of those is a well-formed answer, and every caller up the
stack reads them as *"this corpus does not contain that concept"* — a measurement, and a fabricated
one, because nothing was measured. On a node whose store failed to open, that would report an empty
universe with total confidence ([[absence-is-not-an-affirmative-claim]]).

So the four tests here are, in order: the raise happens with no store configured; it does not happen
once a store has actually been named (all three rungs); a caller cannot turn it into an empty answer
by catching a plain `Exception`; and — the control that makes the rest mean anything — the same reads
that raise with no store succeed the moment one is provided, on the same doubles. Without that last
one, a driver that raised unconditionally would pass the first three.

These use an in-repo double: crystal declares what a store must publish and names no
implementation, so a test that could only run against mantle's lattice would be testing mantle. The
double below publishes exactly the contract in `crystal/ontology/driver.py`'s header — including
`.db.read()`, the raw-SQL reach, which is the part a double most easily omits since it sits outside
the ordinary artifact interface.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from crystal.ontology import driver as wn


# ── the store contract, as a double ───────────────────────────────────────────────────────────
class _Db:
    """`.db.read()` — the raw-SQL face. `driver._conn` is `_observe(store).db.read()`."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.execute("CREATE TABLE edge(src TEXT, dst TEXT, label TEXT, props TEXT)")

    def read(self):
        return self.con


class _Arts:
    def __init__(self):
        self.d = {}
        self.db = _Db()
        self._writes = 0
        self._origin = "double-%d" % id(self)

    def put_artifact(self, doc):
        self.d[doc["id"]] = dict(doc)
        self._writes += 1
        return doc

    def get_artifact(self, artifact_id):
        a = self.d.get(artifact_id)
        return dict(a) if a else None

    def list_artifacts(self, *, content_type=None, state=None, collection_id=None,
                       created_by=None, limit=None, skip=0):
        for a in self.d.values():
            if content_type and a.get("content_type") != content_type:
                continue
            yield dict(a)

    def write_mark(self):
        return ((self._origin, self._writes),)

    def version_of(self, artifact_id):
        return (self._origin, self._writes) if artifact_id in self.d else None

    def edge_mark(self, artifact_id, *, direction="out", cap=256):
        rows = self.db.read().execute(
            "SELECT rowid FROM edge WHERE src = ? LIMIT ?", (artifact_id, cap + 1)).fetchall()
        hi = max([int(r[0]) for r in rows], default=0)
        return (min(len(rows), cap), hi, len(rows) <= cap)


class _Store:
    def __init__(self):
        self.artifacts = _Arts()


def _ontology(word="delta"):
    """A store holding ONE synset — enough that a successful read is distinguishable from an
    empty one, which is the whole distinction under test."""
    st = _Store()
    st.artifacts.put_artifact({
        "id": "wn-%s.n.01" % word, "content_type": "text/x-wordnet", "state": "committed",
        "name": "%s.n.01" % word, "pos": "n", "lemmas": [word], "lemma_counts": {word: 1},
        "hypernyms": [], "instance_hypernyms": [], "ic": 0.5,
    })
    return st


@pytest.fixture(autouse=True)
def _no_ambient_store():
    """Every test here starts with all three rungs empty, and leaves the module as it found it.

    `agience-ember` registers a real provider at `import ember`, and this suite shares a process
    with anything else that has imported it. A test that assumed 'nobody registered one' would pass
    or fail on collection order — the exact shape of ordering bug `_fakes._install_offline_wordnet`
    documents. So the state is set explicitly, not assumed."""
    prev_provider, prev_source = wn._PROVIDER, wn._SOURCE
    wn.set_default_store_provider(None)
    wn.bind(None)
    try:
        yield
    finally:
        wn.bind(None)
        wn.set_default_store_provider(prev_provider)
        wn._SOURCE = prev_source


# ── 1. the raise fires with no store configured ──────────────────────────────────────────────
@pytest.mark.parametrize("call", [
    lambda: wn.synsets("delta"),
    lambda: wn.synset("delta.n.01"),
    lambda: wn.morphy("delta", "n"),
    lambda: wn.all_synsets("n"),
    lambda: wn.concept_ids("delta.n.01"),
])
def test_a_read_with_no_store_raises_rather_than_answering_empty(call):
    """Fails if an unresolved store produces an answer of any kind.

    Parametrised across five entry points because the raise has to happen at the point of
    resolution, not at one front door: `synsets` is the one personas call, but `all_synsets` is the
    build path and `concept_ids` is the id resolver, and a guard on only the first would let the
    others answer an empty corpus.

    The assertion is on the exception, not on `== []`. `pytest.raises` failing here means the call
    returned something, and 'something' is the defect."""
    with pytest.raises(wn.OntologyStoreRequired):
        call()


def test_the_refusal_is_not_catchable_as_an_empty_answer():
    """Fails if `OntologyStoreRequired` were a subclass of something a caller routinely swallows.

    The driver is full of `except Exception: return None` at its edges, and so are its callers. An
    exception that inherited from `LookupError` or `KeyError` would be converted into 'not found' by
    the first such handler it met — the fabricated measurement, reintroduced one layer up.
    `RuntimeError` ensures nothing in this path catches it as an absence."""
    assert issubclass(wn.OntologyStoreRequired, RuntimeError)
    assert not issubclass(wn.OntologyStoreRequired, (LookupError, KeyError, ValueError,
                                                     AttributeError, TypeError))


def test_a_provider_that_returns_none_is_a_refusal_and_not_an_empty_store():
    """Fails if a broken provider degrades to the empty answer instead of raising.

    A host can register something that cannot produce a store — a lazily-configured node whose
    store never opened. `None` from a provider is that host failing, not a corpus that contains
    nothing, and the two must not be spelled the same way."""
    wn.set_default_store_provider(lambda: None)
    with pytest.raises(wn.OntologyStoreRequired, match="returned None"):
        wn.synsets("delta")


# ── 2. the control — the same reads succeed once a store is named, all three ways ──────────────
@pytest.mark.parametrize("how", ["passed", "bound", "provided"])
def test_the_same_read_answers_once_a_store_arrives(how):
    """The test that makes the three above mean something. A driver that raised unconditionally —
    or one whose store resolution was broken outright — would pass every raise test in this file.

    Fails if any of the three rungs stops resolving: the read must return `delta.n.01`, the one
    synset in the double, so an empty answer fails just as loudly as an exception would."""
    st = _ontology("delta")
    if how == "passed":
        got = [s.name() for s in wn.synsets("delta", store=st)]
    elif how == "bound":
        wn.bind(st)
        got = [s.name() for s in wn.synsets("delta")]
    else:
        wn.set_default_store_provider(lambda: st)
        got = [s.name() for s in wn.synsets("delta")]
    assert got == ["delta.n.01"], "the %s store did not answer: %r" % (how, got)


def test_the_provider_is_not_called_until_an_ambient_read_happens():
    """Fails if registration itself opened a store.

    `open_store()` touches the disk and takes a `BEGIN IMMEDIATE` on `ensure_schema`.
    `ember/__init__.py` registers the provider at package scope, so a provider called eagerly would
    make `import ember` open the lattice — on every script, every test, every process that merely
    imports the runner.

    One `synsets()` call resolves the ambient store 19 times, not once. That repetition is a
    property of `_arts()` — it calls `open_store().artifacts` on every ambient read and never adopts
    the result as `_SOURCE` (only an explicitly-named store is adopted, which is what makes a bound
    store coherent with its caches) — not of injection: the provider is called exactly where
    `open_store` was called, the same number of times. A move is a no-op or it is a bug; this
    asserts the no-op.

    A host whose provider is expensive per call has the answer already: `bind(store)`."""
    calls = []

    def _provider():
        calls.append(1)
        return _ontology("delta")

    wn.set_default_store_provider(_provider)
    assert calls == [], "registration itself resolved the store"
    wn.synsets("delta")
    assert calls, "the ambient read never resolved the provider at all"

    # …and binding collapses it to zero further resolutions, which is the escape hatch above.
    before = len(calls)
    wn.bind(_ontology("delta"))
    wn.synsets("delta")
    assert len(calls) == before, (
        "a bound store still went through the provider %d more times" % (len(calls) - before))


def test_an_explicit_store_never_reaches_the_provider():
    """Fails if `store=` falls through to the process default anyway: an explicitly passed store
    must always take precedence over the ambient provider."""
    wn.set_default_store_provider(
        lambda: pytest.fail("an explicitly passed store must not reach the provider"))
    st = _ontology("beta")
    assert [s.name() for s in wn.synsets("beta", store=st)] == ["beta.n.01"]


# ── 3. the freshness stamp reads the face's published edge_mark ────────────────────────────────
def test_the_edge_stamp_reads_the_published_stat_and_not_a_hand_rolled_query():
    """Fails if `freshness.stamp(edges=True)` reaches for `arts.db` and a mantle import instead of
    the store's own published stat.

    The double below publishes `edge_mark` but not the module function a hand-rolled query would
    import, so a stamp that comes back complete proves the published route is the one taken. The
    second half is the discrimination that matters: a face without `edge_mark` must read
    unverifiable (`None`), never 'no edges' — a mark over nothing would verify clean for ever."""
    from crystal.ontology import freshness as fr

    st = _ontology("delta")
    aid = "wn-delta.n.01"
    st.artifacts.db.read().execute(
        "INSERT INTO edge (src, dst, label, props) VALUES (?,?,?,?)",
        (aid, "wn-thing.n.01", "hypernym", json.dumps({})))

    stamp = fr.stamp(st, aid, edges=True)
    assert stamp is not None and len(stamp) == 2, stamp
    assert stamp[1] == (1, 1), "the edge half did not come off the published stat: %r" % (stamp,)

    class _NoEdgeMark:
        def __init__(self, inner):
            self.d, self.db = inner.d, inner.db

        def get_artifact(self, i):
            return self.d.get(i)

        def version_of(self, i):
            return ("o", 1) if i in self.d else None

    bare = type("S", (), {"artifacts": _NoEdgeMark(st.artifacts)})()
    assert fr.stamp(bare, aid, edges=True) is None, (
        "a face that cannot report an edge mark was given a stamp anyway — it would verify clean "
        "for ever")
