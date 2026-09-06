"""An adjective has no position, so it is projected onto the noun it is about — and said to be.

## The gap this closes

Neither an adjective nor an adverb carries a hypernym parent: 0 of 18,156 adjectives and 0 of 3,621
adverbs. So neither has a least common subsumer with anything, `jc_tree` has nothing to measure, and
`wn_synsets_for` refuses them — correctly, because a synset admitted without a position would score
a distance that came from nowhere. That left 15.2% of the lexicon silent in every field.

What a modifier does have is a noun it is ABOUT, and that noun has a coordinate. The source says so
in its own vocabulary, and the corpus was dropping it: `parse_oewn_lmf` took every synset-level
relation whole but filtered sense-level ones to `antonym` alone, discarding

    derivation   74,646    the noun a modifier derives from — `beautiful` -> `beauty`
    pertainym     8,072    an adverb to its adjective — `quickly` -> `quick`

## Projection, not position — and the distinction is load-bearing

`projected_synsets_for` is deliberately separate from `wn_synsets_for`. The latter still answers
"which concepts could this word name", and for an adjective the answer is still none the coordinate
can measure; every existing caller is untouched. A caller that wants the projection must ask for it,
so a projected id cannot arrive somewhere expecting a positioned one. `match.coordinate_coverage`
keeps the two in separate buckets for the same reason.

## Why the whole reachable set is kept

Antonyms would collapse onto one point if a single noun were picked. Keeping every reachable noun
leaves `abundant` at {abundance, quantity} and `scarce` at {quantity, scarcity} — near each other,
because both are about quantity, and distinct, because they differ. Measured over every antonym pair
where both ends project, only 118 land on identical sets: 0.98% of placed adjectives, and those are
pairs where the source itself offers one shared derived noun.

## What this file runs against

A synthetic store holding the exact edge shapes the backfill writes. The offline nltk fixture builds
synsets but no edge table, so it cannot exercise a graph walk — and the walk is the part that could
be wrong.
"""
from __future__ import annotations

import pytest

from crystal.ontology.lookup import projected_nouns_for


class _Cursor:
    """`execute` returns something with `fetchall`, because that is what a sqlite3 cursor does and
    what `_hop` calls. A double that returned a bare list would send `_hop` down its own
    `except Exception` path and every assertion below would pass on an empty walk."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Rows:
    def __init__(self, edges):
        self._edges = edges

    def execute(self, sql, params=()):
        ids = set(params)
        return _Cursor([(dst, label) for src, dst, label in self._edges if src in ids])


class _DB:
    def __init__(self, edges):
        self._rows = _Rows(edges)

    def read(self):
        return self._rows


class _Artifacts:
    def __init__(self, edges):
        self.db = _DB(edges)


class _Store:
    """Only what `_hop` touches: `store.artifacts.db.read().execute(...)`."""

    def __init__(self, edges):
        self.artifacts = _Artifacts(edges)


#: `(src, dst, label)` with the labels and shapes the backfill writes.
#:
#: Ids are bare synset names rather than `wn-`-prefixed. The prefix is `concept_ids` / `driver._n`'s
#: job, read from the store's spec, and this fixture declares no spec — so both resolve to identity
#: and the walk sees the names it is given. That translation is exercised by `related`, which uses
#: the identical pattern against a real store; what is under test here is the walk itself: which
#: labels are followed, in which order, and which part of speech is kept at each step.
_EDGES = [
    # direct derivation: an adjective to its noun
    ("beautiful.a.01", "beauty.n.01", "derivation"),
    # attribute: an adjective as a value of a noun-attribute
    ("hot.a.01", "temperature.n.01", "attribute"),
    ("hot.a.01", "hotness.n.01", "derivation"),
    # satellite with no direct link, reached through its head adjective
    ("scorching.s.01", "hot.a.01", "similar"),
    # adverb -> adjective -> noun
    ("quickly.r.01", "quick.a.01", "pertainym"),
    ("quick.a.01", "quickness.n.01", "derivation"),
    # a modifier the source links to nothing
    ("ineffable.a.01", "unspeakable.a.02", "similar"),
    # noise the projection must ignore: IS-A, and a link to a verb
    ("dog.n.01", "canine.n.02", "hypernym"),
    ("fast.a.01", "hurry.v.01", "derivation"),
]


@pytest.fixture()
def store():
    return _Store(_EDGES)


def test_an_adjective_projects_onto_its_derived_noun(store):
    assert projected_nouns_for(store, "beautiful.a.01") == ["beauty.n.01"]


def test_an_attribute_edge_places_an_adjective_too(store):
    """`derivation` and `attribute` are both direct, so both are taken and the whole set comes
    back — which is what keeps `hot` distinct from `cold` rather than collapsing both onto
    `temperature`."""
    got = projected_nouns_for(store, "hot.a.01")
    assert set(got) == {"temperature.n.01", "hotness.n.01"}, got


def test_a_satellite_is_placed_through_its_head_adjective(store):
    """`scorching` carries no noun link of its own. WordNet hangs a satellite's relations on the
    head of its cluster, so the projection follows `similar` and reads the head's."""
    assert set(projected_nouns_for(store, "scorching.s.01")) == {
        "temperature.n.01", "hotness.n.01"}


def test_an_adverb_is_placed_through_its_adjective(store):
    """Two hops, and neither is optional: `quickly` has no noun and no `similar`, only a pertainym."""
    assert projected_nouns_for(store, "quickly.r.01") == ["quickness.n.01"]


def test_a_modifier_the_source_does_not_link_places_nowhere(store):
    """`[]` is the measurement. The corpus holds no link, so the token is reported unplaced rather
    than projected onto something plausible — the same rule the tokeniser follows for `Bjørn`."""
    assert projected_nouns_for(store, "ineffable.a.01") == []


def test_a_noun_is_not_projected(store):
    """A noun has its own position, so asking for a projection must return nothing rather than
    wander off along its hypernyms."""
    assert projected_nouns_for(store, "dog.n.01") == []


def test_the_projection_never_travels_is_a(store):
    """`jc_tree` already travels the hypernym tree. A projection that followed it too would count the
    tree twice, which is the reason `related` excludes IS-A as well."""
    assert "canine.n.02" not in projected_nouns_for(store, "dog.n.01")


def test_only_nouns_come_back(store):
    """`fast` derives from a VERB here. A verb has a position, but it is not the noun this projection
    promises, and a caller mixing the two would compare across taxonomies where `jc_tree` returns the
    disjoint sum."""
    assert projected_nouns_for(store, "fast.a.01") == []


def test_a_store_that_cannot_be_read_projects_nothing(store):
    """A store fault reads as "no projection", never as an exception into a grounding call."""
    class _Broken:
        class artifacts:
            class db:
                @staticmethod
                def read():
                    raise RuntimeError("no")

    assert projected_nouns_for(_Broken(), "beautiful.a.01") == []


def test_the_backfilled_labels_are_the_ones_the_parser_keeps():
    """The projection reads labels the ingest must actually write. These two sets drifting apart is
    how the walk would come back empty on a correctly-backfilled store, silently."""
    from crystal.ontology import lookup

    from ember.corpus.stage0_sources import _SENSE_RELATIONS_KEPT

    sense_level = {"derivation", "pertainym"}
    assert sense_level <= _SENSE_RELATIONS_KEPT, (
        "the parser drops %s, so the projection has no edges to walk"
        % (sense_level - _SENSE_RELATIONS_KEPT))
    walked = set(lookup._TO_NOUN) | set(lookup._ADVERB_TO_ADJECTIVE) | set(lookup._TO_HEAD_ADJECTIVE)
    assert sense_level <= walked, "the projection stopped reading a label the parser writes"
