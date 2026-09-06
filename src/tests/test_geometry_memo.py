"""The spanning tree built by `tree_path` dominates the cost of an answer, walking through
`canonical_parent`, `ic_of` and `hypernyms` far more than any other step. Memoizing it turns
repeated spanning-tree computation into a lookup.

The function is pure over a static ontology, so the memo can return the identical object; the
only real question is when the ontology moves, which the driver answers with `generation()`.
These tests verify the memo against the computation it stands in for, rather than against itself.

They cover:
  - the memo answers exactly what the computation answers.
  - a stale tree is not served after the store moves; `generation()` advancing drops it.
  - a store that publishes no `generation` disables the memo rather than caching
    uninvalidatably — caching wrong is silent, where not caching is merely slow.
  - two different IC tables do not share one entry, so one table's tree is never served to the
    other's caller.
"""
import pytest

from crystal.ontology import geometry as g


class _S:
    """A synset the geometry can walk: a name and its hypernyms, nothing else."""

    def __init__(self, name, ic, parents=()):
        self._name, self._ic, self._parents = name, ic, list(parents)

    def name(self):
        return self._name

    def ic(self):
        return self._ic

    def hypernyms(self):
        return list(self._parents)

    def instance_hypernyms(self):
        return []


def _chain():
    """root <- mid <- leaf, with IC increasing downward as a real ontology's does."""
    root = _S("root", 0.0)
    mid = _S("mid", 3.0, [root])
    leaf = _S("leaf", 7.0, [mid])
    return root, mid, leaf


@pytest.fixture(autouse=True)
def _clean_memo():
    """Each test starts from an empty memo; leaking one between tests would let a stale entry pass
    for a fresh computation and hide the very thing being asserted."""
    g._MEMO_TREE.clear(); g._MEMO_PARENT.clear()
    g._MEMO_GEN, g._MEMO_IC = -1, None
    yield
    g._MEMO_TREE.clear(); g._MEMO_PARENT.clear()
    g._MEMO_GEN, g._MEMO_IC = -1, None


@pytest.fixture
def gen(monkeypatch):
    """A controllable `generation()` — the store's write mark, under the test's hand."""
    box = {"n": 0}
    from crystal.ontology import driver as d
    monkeypatch.setattr(d, "generation", lambda: box["n"], raising=False)
    return box


def test_the_memo_answers_EXACTLY_what_the_computation_answers(gen):
    """The one that matters: cold and warm must agree on names, order and length."""
    root, mid, leaf = _chain()
    ic = {}
    cold = [s.name() for s in g.tree_path(leaf, ic)]
    warm = [s.name() for s in g.tree_path(leaf, ic)]
    assert cold == ["leaf", "mid", "root"]
    assert warm == cold


def test_a_MOVED_store_drops_the_memo(gen):
    """Changing the ontology under a bumped generation asserts the invalidation itself: without
    the bump, the memo keeps serving the stale answer."""
    root, mid, leaf = _chain()
    ic = {}
    assert [s.name() for s in g.tree_path(leaf, ic)] == ["leaf", "mid", "root"]

    leaf._parents = [root]                    # the ontology changed: leaf now hangs off root
    assert [s.name() for s in g.tree_path(leaf, ic)] == ["leaf", "mid", "root"], \
        "the memo serves the old tree while the generation has not moved"

    gen["n"] += 1                             # …the store moved
    assert [s.name() for s in g.tree_path(leaf, ic)] == ["leaf", "root"]


def test_a_store_with_NO_generation_disables_the_memo(monkeypatch):
    """With no generation, nothing can ever invalidate the memo, so caching would serve a stale
    tree forever, silently. Not caching is slow and correct; caching here would be fast and
    wrong."""
    from crystal.ontology import driver as d
    monkeypatch.delattr(d, "generation", raising=False)
    root, mid, leaf = _chain()
    ic = {}
    assert [s.name() for s in g.tree_path(leaf, ic)] == ["leaf", "mid", "root"]
    assert not g._MEMO_TREE, "the memo was populated with no way to invalidate it"

    leaf._parents = [root]                    # a change no generation could announce…
    assert [s.name() for s in g.tree_path(leaf, ic)] == ["leaf", "root"], \
        "…and it must still be seen, because nothing was cached"


def test_a_generation_that_RAISES_is_treated_as_absent(monkeypatch):
    """A store that errors on the gate is not a store that says 'unchanged'. Same rule as absent:
    the memo stays disabled rather than assume the negative."""
    from crystal.ontology import driver as d

    def boom():
        raise RuntimeError("store gone")

    monkeypatch.setattr(d, "generation", boom, raising=False)
    _root, _mid, leaf = _chain()
    assert [s.name() for s in g.tree_path(leaf, {})] == ["leaf", "mid", "root"]
    assert not g._MEMO_TREE


def test_a_DIFFERENT_ic_table_does_not_reuse_the_entry(gen):
    """Two IC tables give different canonical parents for the same synset, so a memo keyed on the
    name alone would serve one table's tree to the other's caller. Identity of the table is part of
    the key; the reference is held so its `id` cannot be recycled underneath the check."""
    root = _S("root", 0.0)
    a = _S("a", 5.0, [root])
    b = _S("b", 9.0, [root])
    leaf = _S("leaf", 12.0, [a, b])           # two parents: the max-IC one wins

    ic1, ic2 = {"tag": 1}, {"tag": 2}
    first = [s.name() for s in g.tree_path(leaf, ic1)]
    assert first == ["leaf", "b", "root"]     # b has the higher IC

    b._ic = 1.0                               # under the second table, a is now the max-IC parent
    second = [s.name() for s in g.tree_path(leaf, ic2)]
    assert second == ["leaf", "a", "root"], "the memo was reused across two different IC tables"


def test_a_SECOND_REGISTRY_with_the_same_names_is_not_served_the_first_s_trees(gen):
    """Keying on `synset.name()` alone assumes one `Synset` object per name in the process. That
    holds for the driver's singleton index, but not for any caller holding its own registry — and
    such a caller exists: `agience-ember/tests/test_geometry_lattice.py` builds a registry of
    smoothed-IC synsets and calls `jc_tree(s1, s2, None)`. A name-only key would serve those reads
    trees computed from the unsmoothed objects.

    Both calls pass the same `ic`. The IC lives on the synsets, so the `ic`-identity guard is
    blind here — object identity is the only thing that separates the two registries.
    """
    ic = {}
    root_a = _S("root", 0.0)
    mid_a = _S("mid", 3.0, [root_a])
    leaf_a = _S("leaf", 7.0, [mid_a])
    assert [s.name() for s in g.tree_path(leaf_a, ic)] == ["leaf", "mid", "root"]

    # A second registry: same names, different objects, different structure — as a re-measured
    # corpus would be. Nothing about the name distinguishes it from the first.
    root_b = _S("root", 0.0)
    leaf_b = _S("leaf", 7.0, [root_b])
    assert [s.name() for s in g.tree_path(leaf_b, ic)] == ["leaf", "root"], \
        "the memo served one registry's tree to another registry's synset of the same name"


def test_the_cached_list_is_returned_and_callers_must_not_mutate_it(gen):
    """The returned list is shared, not copied, because copying per call would reintroduce an
    allocation on the hottest path in the system. Sharing is safe as long as every caller only
    iterates it."""
    _root, _mid, leaf = _chain()
    ic = {}
    assert g.tree_path(leaf, ic) is g.tree_path(leaf, ic)
