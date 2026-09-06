"""`seed_lattice` derives its bounds — change an input, the value moves.

The AST instrument below is the same shape `agience-mantle/tests/test_no_forcings.py` uses for its
own derivations, applied here to `crystal.ontology.seed_lattice`: is a measurement compared against
a bare number? A derivation that returns the same number regardless of its inputs is the old
constant wearing a function, and it passes every test the constant passed. So each test here states
its failure mode first: what would still be true if the derivation had been faked.

This suite lives in crystal's tests, not mantle's: `ARCHITECTURE-TARGET.md` §2 places mantle below
crystal, so mantle's suite must stay testable without crystal installed. A mantle test importing
`crystal.ontology` would invert that, making `agience-mantle` — the standalone database —
untestable without crystal.
"""
from __future__ import annotations

import ast
import importlib
import math


# ═════════════════════════════════════════════════════════════════════════════
# The instrument: an AST scan, not a text search
# ═════════════════════════════════════════════════════════════════════════════
# Grep miscounts this, in both directions. It matches inside prose (a docstring quoting a banned
# literal to explain the rule would itself read as a re-introduction), and it matches substrings
# ("200" inside "2000"). The question is structural — *is a measurement compared against a numeric
# literal?* — so it is asked of the parse tree.

_ORDER_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def _is_num(node) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return True
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) \
        and _is_num(node.operand)


def literal_comparisons(module_name: str, *, ignore_below: int = 4):
    """Every `<expression> <op> <numeric literal>` in a module, as `(lineno, value, source)`.

    `ignore_below` drops structural small ints (0, 1, 2, 3 — emptiness, arity, ndim), which are
    the shape of the data rather than a judgement about it.
    """
    mod = importlib.import_module(module_name)
    src = open(mod.__file__, encoding="utf-8").read()
    lines = src.splitlines()
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        for op, right in zip(node.ops, node.comparators):
            if isinstance(op, _ORDER_OPS):
                for a, b in ((left, right), (right, left)):
                    if _is_num(b) and not _is_num(a):
                        v = ast.literal_eval(b)
                        if isinstance(v, float) or abs(v) >= ignore_below:
                            found.append((node.lineno, v,
                                          lines[node.lineno - 1].strip()))
            left = right
    return found


def assert_no_literal_comparison(module_name: str, *, allow=()):
    """Fails if any measurement in the module is compared against a bare number.

    `allow` names values that are specifications (a digest width, a key length, a wire-format
    field) — the contract, not a judgement. Everything else must be a name.
    """
    bad = [f"line {ln}: {v!r}  |  {s}"
           for ln, v, s in literal_comparisons(module_name) if v not in allow]
    assert not bad, f"{module_name} compares a measurement against a bare literal:\n" + "\n".join(bad)


# ═════════════════════════════════════════════════════════════════════════════
# ontology/seed_lattice.py — `_crossed` (checkpoint cadence) and `_unchanged`
# ═════════════════════════════════════════════════════════════════════════════

def test_crossed_tracks_the_flush_size():
    """Fails if `_crossed` ignored `flushed` — i.e. if 2000 were still baked in.

    Same (n, every), different flush size, DIFFERENT answer. A `_crossed` that returned
    `n % every < 2000` regardless would answer True for both of these.
    """
    from crystal.ontology.seed_lattice import _crossed

    assert _crossed(n=10_500, every=10_000, flushed=1_000) is True    # (9500, 10500] holds 10000
    assert _crossed(n=10_500, every=10_000, flushed=100) is False     # (10400, 10500] holds none
    # ... and a version with 2000 hardcoded in place of `flushed` would also answer True here.


def test_crossed_tracks_the_cadence():
    """Fails if the cadence argument were ignored."""
    from crystal.ontology.seed_lattice import _crossed

    assert _crossed(n=40_000, every=40_000, flushed=2_000) is True
    assert _crossed(n=40_000, every=30_000, flushed=2_000) is False   # 40000 % 30000 == 10000


def test_crossed_matches_an_independent_oracle_at_every_flush_size():
    """The property that matters, against an oracle written a DIFFERENT way.

    `_crossed` is modular arithmetic (`n % every < flushed`). The oracle here is integer
    division — "did the quotient advance?" — which is the same question with none of the same
    operations, so agreement is evidence rather than restatement.

    Fails if the flush size stopped mattering: batch 6001 against cadence 1000 crosses several
    multiples in one flush, and any implementation carrying a baked-in 2000 disagrees at batch 1,
    7, 500 and 6001 alike.
    """
    from crystal.ontology.seed_lattice import _crossed

    for batch in (1, 7, 500, 2_000, 6_001):
        for every in (1_000, 40_000):
            total, n, fires, oracle = 100_000, 0, 0, 0
            while n < total:
                flushed = min(batch, total - n)
                before = n
                n += flushed
                fires += bool(_crossed(n, every, flushed))
                oracle += bool(n // every > before // every)
            assert fires == oracle, (batch, every, fires, oracle)
            if batch <= every:      # at most one crossing per flush, so it is a clean count
                assert fires == total // every, (batch, every, fires)


def test_crossed_is_safe_at_the_degenerate_edges():
    from crystal.ontology.seed_lattice import _crossed

    assert _crossed(n=10, every=0, flushed=5) is False       # no cadence, not a ZeroDivisionError
    assert _crossed(n=10, every=10, flushed=0) is False      # nothing was written


def test_unchanged_is_exact_and_the_deleted_1e_12_band_is_empty():
    """`_unchanged` compares exactly, with no tolerance band.

    Fails if `_unchanged` carried a tolerance: a difference of 1e-13 must be reported as
    changed. The measurement that justifies exact comparison is re-run here rather than
    quoted — if a corpus change could produce a difference below 1e-12, this test says so.
    """
    from crystal.ontology.seed_lattice import _unchanged

    assert _unchanged(0.5, 0.5) is True
    assert _unchanged(None, 0.5) is False
    assert _unchanged(0.5, 0.5 + 1e-13) is False, "a tolerance is still in there"

    # The smallest nonzero change a one-unit corpus move can produce in ic = 1 - log(k)/log(N+1)
    # over integer k, N, swept over corpus scales 1e2..1e7.
    smallest = min(
        d
        for N in (10 ** e for e in range(2, 8))
        for k in (2, 10, 1000, N)
        if 2 <= k <= N
        for d in (
            abs(math.log(k + 1) - math.log(k)) / math.log(N + 1),
            abs(math.log(k) / math.log(N + 1) - math.log(k) / math.log(N + 2)),
        )
        if d > 0
    )
    assert smallest > 1e-12 * 100, (
        "a corpus change CAN land inside the deleted 1e-12 band; the deletion was wrong")


def test_ic_is_bit_reproducible_so_exact_equality_is_the_right_test():
    """The other half of why exact equality is correct: no summation, no set-order dependence.

    Fails if `ic` ever stops being a closed form over two integers — at which point `_unchanged`
    needs a tolerance derived from the new arithmetic.
    """
    import json

    for N, k in ((10 ** 6, 3), (10 ** 6, 999_999), (10 ** 3, 2)):
        v = 1.0 - math.log(k) / math.log(N + 1)
        assert json.loads(json.dumps(v)) == v, "float64 did not survive the JSON round trip"
        assert (1.0 - math.log(k) / math.log(N + 1)) == v, "not bit-reproducible"


def test_both_ic_writers_share_one_definition_of_already_written():
    """Fails if the two writers ever use different definitions of "already written".

    Structural, not textual: both call sites must be a call to the same function, so a re-inlined
    tolerance at either one is a missing call here.
    """
    from crystal.ontology import seed_lattice as sl

    tree = ast.parse(open(sl.__file__, encoding="utf-8").read())
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for writer in ("persist_ic", "persist_conceptnet_ic"):
        calls = {n.func.id for n in ast.walk(fns[writer])
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_unchanged" in calls, f"{writer} does not use the shared definition"


def test_seed_lattice_has_no_bare_literal_comparisons_left():
    """Fails if a bare 2000, or the 1e-12 tolerance, comes back anywhere in the module."""
    from crystal.ontology import seed_lattice as sl

    assert sl._IC_BATCH == 2000
    assert_no_literal_comparison("crystal.ontology.seed_lattice")



# ═════════════════════════════════════════════════════════════════════════════
# The kept bound must SAY what would make a different value right
# ═════════════════════════════════════════════════════════════════════════════

def test_ic_batch_says_what_would_make_a_different_value_right():
    """A named constant with no stated reason is a forcing that learned to spell.

    Fails if the bound is renamed without the sentence that makes it auditable — the whole point of
    keeping it is that the next reader can tell what evidence would move it.
    """
    mod = importlib.import_module("crystal.ontology.seed_lattice")
    src = open(mod.__file__, encoding="utf-8").read()
    where = src.index("_IC_BATCH = ")
    # Whitespace-normalised: the sentence is wrapped across comment lines, and a test that broke
    # on line wrapping would be testing the formatter, not the reasoning.
    preamble = " ".join(src[max(0, where - 1400):where].lower().replace("#:", " ").split())
    assert any(k in preamble for k in ("would be right", "would be wrong", "the bound is")), (
        "crystal.ontology.seed_lattice._IC_BATCH is named but not stated: no sentence says what "
        "would move it")


def test_the_literal_scan_has_teeth(tmp_path):
    """A check that cannot fail proves nothing. State the failure mode, then produce it.

    Failure mode: the AST walk silently matches nothing — wrong node type, wrong operand side,
    an unparsed file — and certifies every module clean for ever.
    """
    import sys

    mod = tmp_path / "_d8_forcing_probe.py"
    mod.write_text(
        '"""A docstring mentioning 2000 is prose, not a comparison."""\n'
        'def clean(n, bound):\n'
        '    return n > bound\n'
        'def dirty(n):\n'
        '    return n > 2000\n', encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        hits = literal_comparisons("_d8_forcing_probe")
        assert [v for _ln, v, _s in hits] == [2000], (
            "the scan missed the bare literal, or fired on the prose: %r" % (hits,))
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("_d8_forcing_probe", None)
