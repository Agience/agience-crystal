"""`seed_lattice`'s relation signature — measured, never fabricated, and never by `count(*)`.

`_relation_signature` and `relation_vertices` live in `crystal.ontology.seed_lattice`; the counter
they read is mantle's edge-label counter, and mantle tests the counter itself. This suite pins the
oracle proof, the two raise tests, and the `count(*)` guard, all pointed at `crystal/src`.

The guard walks `crystal/src` rather than only the lattice package, because `mantle/db/test_lattice.py`
guards the lattice package alone and so misses a `count(*)` written anywhere in
`ontology/seed_lattice.py`. A guard that never reaches `seed_lattice.py` — the one file `count(*)`
was banned for — scans nothing and reports green forever.

This suite imports `mantle.db.lattice`, and only this suite does — `crystal/src` imports mantle
nowhere (`ARCHITECTURE-TARGET.md` §2; mantle sits below crystal), and nothing in `src/` follows this
test into mantle. The oracle argument needs a real store: it computes both the counter path and the
`count(*)` path on one lattice and demands they agree field for field. A double cannot make that
argument, because a double is written by the same hand as the thing it audits. So the test reaches
for the reference implementation of the contract `crystal/ontology/driver.py`'s header states.

  1. the oracle — the counter-based signature is proved against an independent `count(*)`
     computation, on a store small enough that both paths can be computed;
  2. an unmeasured extent puts no number on anything — `measured: False` and every field `None`;
  3. `relation_vertices` raises rather than publishing a fabricated extent;
  4. no new `count(*)` anywhere in `crystal/src`, and the guard has teeth.
"""
from __future__ import annotations

import ast
import os
import sqlite3

import pytest

# `mantle.db.lattice` has not existed since the lattice package was flattened into `mantle.db`;
# this file imported it and therefore ERRORED AT COLLECTION, providing zero coverage of
# `relation_vertices` for however long the rename has been in. The symbols all survived, only
# the paths moved.
#
# This is the one crystal->mantle reach in the tree, and it is test-only. `pyproject.toml` states
# crystal declares no `agience-mantle` dependency, which is true of `src/` and stays true — the
# layer law is `crystal -> prism` only. It is tolerated here for the same reason the
# manifest already tolerates the unmet `prism[wire]` test dependency: a signature measured over a
# real lattice is the thing under test, and a hand-built stub would test the stub.
from mantle.db import open_lattice
from mantle.db.schema import c_edge_label, c_edge_label_built

from crystal.ontology import seed_lattice as SL


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────


def _fresh(tmp_path, name="lat.db"):
    return open_lattice(str(tmp_path / name), origin="test-origin", leaves=16)


def _corpus(L):
    """A small corpus with three labels, an inverse pair, and a symmetric relation."""
    L.graph.add_edges([
        ("wn-dog", "wn-canine", "hypernym", {}),
        ("wn-cat", "wn-feline", "hypernym", {}),
        ("wn-canine", "wn-dog", "hyponym", {}),
        ("wn-feline", "wn-cat", "hyponym", {}),
        ("wn-hot", "wn-cold", "antonym", {}),
        ("wn-cold", "wn-hot", "antonym", {}),
    ])


def _strip_to_legacy(L):
    """Make the store look like one whose edges were written before the counter existed."""
    with L.db.write() as cur:
        cur.execute("DELETE FROM counter WHERE name LIKE ?", (c_edge_label("") + "%",))
        cur.execute("DELETE FROM counter WHERE name = ?", (c_edge_label_built(),))


def test_the_unmeasured_signature_puts_no_number_on_anything(tmp_path):
    L = _fresh(tmp_path)
    _corpus(L)
    _strip_to_legacy(L)
    sig = SL._relation_signature(L, "hypernym")
    assert sig["measured"] is False
    for k in ("n", "edges", "symmetry", "inverse", "inverse_share"):
        assert sig[k] is None, (
            "%r came back %r on an unmeasured store — every measured field must be None, or a "
            "caller reading it gets a number nobody measured" % (k, sig[k]))


def test_relation_vertices_refuses_rather_than_publishing_a_fabricated_extent(tmp_path,
                                                                              monkeypatch):
    """A relation artifact carries `provenance: observed`, so its count is always measured, never
    invented.

    The backfill inside `relation_vertices` normally makes this unreachable, so it is forced:
    stub the backfill out and confirm the guard fires rather than writing `edges: 0` over a
    corpus it never counted."""
    L = _fresh(tmp_path)
    _corpus(L)
    _strip_to_legacy(L)
    monkeypatch.setattr(type(L.graph), "backfill_edge_label_counters",
                        lambda self, **kw: {"built": False, "scanned": 0,
                                            "labels": 0, "already": False})
    with pytest.raises(RuntimeError, match="not measured"):
        SL.relation_vertices(L)
    assert L.artifacts.get_artifact("rel-hypernym") is None, "nothing may have been written"


# ── 4. the oracle — the signature is unchanged ───────────────────────────────────────────────


def _oracle_signature(store, label, sample=4000):
    """The `count(*)` computation `_relation_signature` is proved against — the independent answer
    the counter-based implementation must agree with.

    It is deliberately a verbatim copy rather than a call into the module: an oracle that shared
    code with the thing it audits would agree with it by construction."""
    conn = store.artifacts.db.read()
    total = int(conn.execute(
        "SELECT COUNT(*) FROM edge WHERE label=?", (label,)).fetchone()[0] or 0)
    if total <= 0:
        return {"n": 0, "edges": 0, "symmetry": 0.0, "inverse": None, "inverse_share": 0.0}
    stride = max(total // max(sample, 1), 1)
    pairs = [(str(a), str(b)) for a, b in conn.execute(
        "SELECT src,dst FROM edge WHERE label=? AND rowid %% %d = 0 LIMIT ?" % stride,
        (label, sample))]
    if not pairs:
        return {"n": 0, "edges": total, "symmetry": 0.0, "inverse": None, "inverse_share": 0.0}
    sym = sum(1 for a, b in pairs if conn.execute(
        "SELECT 1 FROM edge WHERE src=? AND dst=? AND label=? LIMIT 1", (b, a, label)).fetchone())
    inv = {}
    for a, b in pairs:
        for (l2,) in conn.execute("SELECT label FROM edge WHERE src=? AND dst=?", (b, a)):
            if str(l2) != label:
                inv[str(l2)] = inv.get(str(l2), 0) + 1
    best = max(inv, key=lambda k: inv[k]) if inv else None
    return {"n": len(pairs), "edges": total, "symmetry": sym / len(pairs),
            "inverse": best, "inverse_share": (inv[best] / len(pairs)) if best else 0.0}


@pytest.mark.parametrize("sample", [4000, 3, 1])
def test_relation_signature_is_identical_to_the_count_star_oracle(tmp_path, sample):
    """Both paths are computable on one small store, and they must agree field for field.

    `sample` is parametrised down to 1 because the extent feeds the sample stride, not just the
    reported number: an extent off by one selects a different subset of rows and would move
    `symmetry` and `inverse_share`. A test run only at the default sample size would never see
    that, because the stride is 1 there for any small store."""
    L = _fresh(tmp_path)
    _corpus(L)
    L.graph.add_edges([("u%d" % i, "v%d" % i, "part_of", {}) for i in range(31)])
    L.graph.add_edges([("v%d" % i, "u%d" % i, "has_part", {}) for i in range(31)])
    L.graph.delete_edge("u5", "v5", "part_of")

    labels = sorted({str(r["label"]) for r in L.db.read().execute("SELECT label FROM edge")})
    assert labels, "empty corpus — the comparison would be vacuous"
    for lab in labels + ["nonexistent_relation"]:
        new = SL._relation_signature(L, lab, sample=sample)
        old = _oracle_signature(L, lab, sample=sample)
        assert new == dict(old, measured=True), (
            "the counter changed the answer for %r at sample=%d:\n new=%r\n old=%r"
            % (lab, sample, new, old))


def test_relation_vertices_publishes_the_same_extents(tmp_path):
    """End to end: the artifacts written carry the oracle's numbers."""
    L = _fresh(tmp_path)
    _corpus(L)
    n = SL.relation_vertices(L, author="test@ikailo.com")
    assert n == 3
    for lab in ("hypernym", "hyponym", "antonym"):
        doc = L.artifacts.get_artifact("rel-" + lab)
        assert doc is not None, lab
        assert doc["spec"]["edges"] == _oracle_signature(L, lab)["edges"]
        assert doc["spec"]["measured"] is True


#: Files allowed a `count(*)` the planner CANNOT bound. Empty, and that emptiness is the
#: measurement rather than an aspiration: crystal owns no database — it reads whatever store the
#: host injected — so an entry here is a claim that crystal grew a second data path, and it has to
#: say which database and why.
_COUNT_STAR_CEILING: dict = {}

#: Call names whose first argument is SQL. Scoped to the call argument, not to the file, for the
#: reason `mantle/db/test_lattice.py` gives: this codebase deliberately quotes `count(*)` in
#: docstrings and error messages to name the hazard, and a guard that forced those silent would
#: trade real documentation for a proxy. `query` is here because the legacy graph backend spells
#: it that way — an `execute`-only walk would have missed `sync.py` entirely.
_SQL_CALLS = ("execute", "executemany", "executescript", "query")


def _sql_literals(path):
    """Every string constant reaching a SQL call's first argument — an AST walk, not a grep.

    Follows SQL assembled by `%`, `+` or adjacent literals: the operand constants are still
    inside the argument subtree, so a banned construct hidden in a fragment is still caught."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in _SQL_CALLS:
            continue
        for sub in ast.walk(node.args[0]):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.append((sub.value, getattr(sub, "lineno", 0)))
    return out


def _count_star_hits(path):
    return [(text, ln) for text, ln in _sql_literals(path)
            if "count(*)" in text.lower().replace(" ", "")]

def _src_root():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _crystal_modules():
    root = _src_root()
    for dirpath, _dirs, files in os.walk(os.path.join(root, "crystal")):
        for fn in sorted(files):
            if fn.endswith(".py") and not fn.startswith("test_"):
                full = os.path.join(dirpath, fn)
                yield full, os.path.relpath(full, root)


def _plan_of(conn, sql):
    """`(verdict, detail)` for one statement — asked of the PLANNER, not of its spelling.

    REWRITTEN 2026-08-25, from a string ban to a cost measurement. The old guard banned the
    token `count(*)` and it fired on `ontology/lookup.py`'s degree walk, which is the exact
    OPPOSITE of the hazard: that walk counts by label precisely so an unaffordable label is never
    materialised, and both its queries are covering-index SEARCHes bounded by one node's degree.
    Measured on a real lattice the day this was written:

        SELECT label, count(*) FROM edge WHERE src = ? GROUP BY label
            -> SEARCH edge USING COVERING INDEX ix_e_src (src=?)      bounded by degree
        SELECT count(*) FROM edge
            -> SCAN edge USING COVERING INDEX ix_e_leaf               O(table) — the hazard

    Spelling could not tell those apart; `SCAN` vs `SEARCH` does, and it also catches the count the
    string ban would have WAVED THROUGH — one whose index was dropped, or that never had one. The
    invariant was always "no count whose cost is unbounded"; `count(*)` was only ever a proxy.

    Three verdicts, and `unreadable` is the important one: a statement the planner cannot prepare
    is one this guard has NOT measured, so it is reported, never passed. That is the difference
    between a clean run and a silent one.
    """
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql,
                            tuple("x" for _ in range(sql.count("?")))).fetchall()
    except sqlite3.Error as exc:
        # A fragment, a named-parameter form, or a table that is not the lattice's. Any of those
        # may be perfectly fine — but none of them has been MEASURED, and a guard that treats
        # "could not check" as "passed" is the vacuous pass this suite exists to refuse.
        return "unreadable", str(exc)
    details = [str(r[3]) for r in rows]
    if not details:
        return "unreadable", "the planner returned no rows for a statement it accepted"
    scans = [d for d in details if d.strip().upper().startswith("SCAN")]
    if scans:
        return "unbounded", "; ".join(scans)
    return "bounded", "; ".join(details)


def test_no_new_count_star_anywhere_in_crystal_src(tmp_path):
    """No `count(*)` in `crystal/src` whose cost the planner cannot bound to a key.

    An unbounded count dereferences every record — EXPLAIN shows it scanning millions of rows to
    answer one integer, enough to exhaust memory on a large lattice. A count bounded by a covering
    index is a different animal: it is how `ontology/lookup.py` learns the price of a label before
    paying it, and banning that would push callers toward fetching the edges instead, which is
    strictly worse. So the question this asks is the planner's `SCAN` vs `SEARCH`, not the
    presence of a substring — see `_plan_of`.

    The ceiling is empty, and that is the measurement. mantle's copy of this scan carries three
    exemptions, every one of them a count against a different database (the embeddings sidecar, the
    legacy shard store, the legacy graph backend). crystal has no database of its own at all, so
    there is nothing here for an exemption to be about.
    """
    modules = list(_crystal_modules())
    assert len(modules) > 20, "the walk found almost nothing — it could not have failed"
    assert any(rel.endswith(os.path.join("ontology", "seed_lattice.py")) for _f, rel in modules), (
        "the walk does not reach `crystal/ontology/seed_lattice.py` — the ONE file this guard was "
        "written for. A scan that misses its subject reports green for ever")

    # The real schema, from the reference implementation — not a copy of the DDL that would drift
    # out of agreement with the store these queries actually run against.
    conn = _fresh(tmp_path).artifacts.db.read()

    found, offenders, bounded = {}, [], []
    for full, rel in modules:
        for text, ln in _count_star_hits(full):
            verdict, detail = _plan_of(conn, text)
            if verdict == "bounded":
                bounded.append((rel, ln, detail))
                continue
            found[rel] = found.get(rel, 0) + 1
            offenders.append("%s:%d [%s] %r -> %s" % (rel, ln, verdict, text[:60], detail[:90]))

    over = {rel: n for rel, n in found.items() if n > _COUNT_STAR_CEILING.get(rel, 0)}
    assert not over, (
        "a count(*) reaches a database with a cost the planner will not bound: %s\n"
        "`unbounded` means the plan SCANs — read the whole table to answer one integer; that is "
        "banned against the lattice, and the fix is a `counter` row maintained in the write "
        "transaction, not an index.\n"
        "`unreadable` means this guard could NOT measure it — a fragment, or another database's "
        "table. Do not silence it by rephrasing: if it is against some OTHER database, raise that "
        "file's entry in _COUNT_STAR_CEILING and say which database and why." % offenders)

    stale = {rel: n for rel, n in _COUNT_STAR_CEILING.items() if found.get(rel, 0) < n}
    assert not stale, (
        "the ceiling is now looser than the code: %r. Lower it — an exemption nobody needs is "
        "an open door." % stale)


def test_the_retired_site_is_actually_gone():
    """Pins the absence of `count(*)` from `seed_lattice.py`, and the presence of the counter read
    (`count_edges_by_label`) it depends on instead."""
    path = os.path.join(_src_root(), "crystal", "ontology", "seed_lattice.py")
    assert os.path.exists(path), path
    assert _count_star_hits(path) == [], "seed_lattice.py issues count(*) again"
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "count_edges_by_label" in src, "the counter read is not what replaced it"


def test_the_count_star_guard_has_teeth(tmp_path):
    """A check that cannot fail proves nothing. State the failure mode, then produce it.

    Failure mode: the AST walk silently reaches no SQL — wrong call name, wrong argument
    position, unparsed file — and reports every module clean forever."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""Never use count(*) — it dereferences every record."""\n'
        'def f(cur):\n'
        '    raise ValueError("count(*) is banned on the lattice")\n'
        'def g(cur):\n'
        '    return cur.execute("SELECT n FROM counter WHERE name = ?")\n', encoding="utf-8")
    assert _count_star_hits(str(clean)) == [], (
        "the guard fires on prose and error messages — it would force the hazard undocumented")

    dirty = tmp_path / "dirty.py"
    dirty.write_text(
        'def f(cur, t):\n'
        '    a = cur.execute("SELECT COUNT(*) FROM edge WHERE label=?")\n'
        '    b = cur.execute("SELECT count (*) FROM vertex")\n'
        '    c = conn.query("SELECT count(*) AS c FROM Artifact")\n'
        '    d = cur.execute("SELECT count(*) FROM %s" % t)\n'
        '    return a, b, c, d\n', encoding="utf-8")
    hits = _count_star_hits(str(dirty))
    assert len(hits) == 4, (
        "the guard missed a violation — concatenated SQL and the `query` spelling are the two "
        "that hide: %r" % hits)


def test_the_planner_verdicts_are_the_ones_measured(tmp_path):
    """`_plan_of` is now the whole guard, so its three verdicts are demonstrated on a REAL lattice.

    Without this, a `_plan_of` that returned "bounded" for everything would look exactly like a
    clean tree — the precise failure the string ban had, in a new place."""
    conn = _fresh(tmp_path).artifacts.db.read()

    # 1. bounded — the shape `ontology/lookup.py` uses, and the reason this guard was rewritten.
    for sql in ("SELECT label, count(*) FROM edge WHERE src = ? GROUP BY label",
                "SELECT label, count(*) FROM edge WHERE dst = ? GROUP BY label"):
        verdict, detail = _plan_of(conn, sql)
        assert verdict == "bounded", (sql, verdict, detail)
        assert "COVERING INDEX" in detail, (
            "bounded, but no longer by a covering index — the cost argument has changed: " + detail)

    # 2. unbounded — the hazard itself. If an index were added that made this a SEARCH, the guard
    #    would stop firing here and this test would say so.
    verdict, detail = _plan_of(conn, "SELECT count(*) FROM edge")
    assert verdict == "unbounded", (verdict, detail)
    assert detail.upper().startswith("SCAN")

    # 3. unreadable — a statement the planner cannot prepare is NOT a pass. This is the vacuous-
    #    pass guard: it is the branch a broken `_plan_of` would take for every query in the tree.
    verdict, _detail = _plan_of(conn, "SELECT count(*) FROM a_table_the_lattice_does_not_have")
    assert verdict == "unreadable", verdict
    verdict, _detail = _plan_of(conn, "SELECT count(*) FROM edge WHERE label = ")
    assert verdict == "unreadable", "a truncated fragment must not be reported as measured"


def test_the_guard_still_fires_on_an_unbounded_count(tmp_path):
    """End to end, through the real walk: a seeded module with a table-scanning count is caught,
    and one with only a key-bounded count is not.

    The string ban could not draw this line at all — both files below contain `count(*)`."""
    conn = _fresh(tmp_path).artifacts.db.read()

    dirty = tmp_path / "dirty.py"
    dirty.write_text('def f(cur):\n'
                     '    return cur.execute("SELECT count(*) FROM vertex")\n', encoding="utf-8")
    hits = _count_star_hits(str(dirty))
    assert len(hits) == 1
    assert _plan_of(conn, hits[0][0])[0] == "unbounded"

    clean = tmp_path / "clean.py"
    clean.write_text('def f(cur, node):\n'
                     '    return cur.execute(\n'
                     '        "SELECT label, count(*) FROM edge WHERE src = ? GROUP BY label",\n'
                     '        (node,))\n', encoding="utf-8")
    hits = _count_star_hits(str(clean))
    assert len(hits) == 1
    assert _plan_of(conn, hits[0][0])[0] == "bounded", (
        "the guard would reject the degree walk again — the rewrite has been undone")
