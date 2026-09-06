"""A gate over the gates — no test in this repo may check nothing.

This is a test, not a one-off sweep, because a sweep finds a defect once and a gate finds the next
one. A test can look like it checks something while checking nothing: for example, decoding base64
with `validate=False` silently discards invalid characters instead of raising, so a test that relies
on that raise to fail would pass without ever exercising it.

The detector counts an assertion as anything `_checks_something` recognizes: `ast.Assert`,
`ast.Raise`, `pytest.raises`/`warns`, or a mock `assert_*` call — `ast.Assert` alone misses mock
assertions like `destroy.assert_called_once()`, which use no `assert` keyword. It also scans every
test function, not only ones with a docstring, since a test with no docstring at all is the stronger
form of the same gap.

A test that asserts by not raising is legitimate: `f()  # must not raise` is a real check — the call
is the assertion. Those are named in `ALLOWED` with their reason rather than pattern-matched, so each
one is argued and a new one cannot appear silently.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Tests whose assertion is the absence of an exception. Named, not inferred.
ALLOWED = {
    # `self_register` must tolerate a persona object that has no `register` attribute at all; the
    # check is that the call completes rather than raising AttributeError.
    "test_self_register_tolerates_persona_without_register",
    # this gate itself: its controls plant and inspect AST, and assert below like anything else
}


def _checks_something(fn: ast.FunctionDef) -> bool:
    """Does this body check anything? Assert, raise, `pytest.raises`, or a mock `assert_*` call."""
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return True
        if isinstance(n, ast.Call):
            name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if isinstance(name, str) and (name.startswith("assert")
                                          or name in {"raises", "warns", "fail", "approx"}):
                return True
    return False


#: Files this gate could not parse — a module-level record, not swallowed; see `_test_functions`.
UNREADABLE: list = []


def _test_functions():
    """Every test function in this repo, and a record of anything that could not be read.

    Files that fail to parse are recorded in `UNREADABLE` rather than silently skipped: continuing
    past a `SyntaxError` would mean "no test checks nothing" covers fewer files than it claims.
    Decoding is `utf-8-sig`, not `utf-8` with `errors="ignore"`, because a BOM would otherwise
    misdecode, and dropping undecodable bytes can change what the parser sees rather than failing.
    """
    UNREADABLE.clear()
    for path in sorted(TESTS.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            UNREADABLE.append("%s (%s)" % (path.name, type(exc).__name__))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                yield path, node


def test_NO_test_in_this_repo_checks_nothing():
    """The gate. A test that checks nothing does not fail — it succeeds, instantly, forever.

    Fails if a new test's assertion was lost in a refactor, or a test was written around a call that
    "obviously works": either way it becomes a green line that guards nothing, and every count of
    "N tests passing" includes it.
    """
    silent = {}
    for path, fn in _test_functions():
        if not _checks_something(fn) and fn.name not in ALLOWED:
            silent.setdefault(path.name, []).append(fn.name)
    # Coverage is asserted before the verdict: a scan that skipped files cannot support "nothing is
    # vacuous", so the skip list is checked first — otherwise this gate would report a clean result
    # for a scan it did not perform.
    assert not UNREADABLE, (
        "these test files could not be parsed and were therefore NOT scanned: %s. The verdict below "
        "would have covered fewer files than it claims." % UNREADABLE)
    assert not silent, (
        "these tests check nothing and are not named in ALLOWED: %s. Either give them an assertion, "
        "or add the name with the reason its call IS the check." % silent)


def test_the_ALLOW_LIST_cannot_outlive_its_reason():
    """The half that keeps an exemption honest. An allow-list nobody re-reads becomes a place to
    hide things. If an allowed test grows a real assertion, its entry must be removed — otherwise the
    name stays exempt and a future rewrite of that test could silently check nothing again.

    Fails if assertions are added to an allowed test and the entry is left on the list.
    """
    by_name = {fn.name: fn for _, fn in _test_functions()}
    stale = []
    for name in sorted(ALLOWED):
        fn = by_name.get(name)
        if fn is None:
            stale.append("%s (no longer exists)" % name)
        elif _checks_something(fn):
            stale.append("%s (now checks something)" % name)
    assert not stale, (
        "these ALLOWED entries no longer describe anything — remove them so the exemption cannot "
        "mask a future test at the same name: %s" % stale)


def test_THIS_DETECTOR_CAN_ACTUALLY_FAIL():
    """The vacuity control, checking both directions: a detector that never fires reports a clean
    repo forever, and one that fires on everything reports rigorous tests as vacuous.

    Fails if the `ast.Assert` walk is dropped — nothing would ever be reported — or if the
    `assert_*` call branch is dropped, which would report every mock-based test in the repo as
    checking nothing.
    """
    planted = ast.parse(
        "def test_nothing():\n"
        "    x = 1 + 1\n"
        "def test_asserts():\n"
        "    assert 1 == 1\n"
        "def test_mock_assertion():\n"
        "    m.assert_called_once()\n"
        "def test_raises_ctx():\n"
        "    with pytest.raises(ValueError):\n"
        "        boom()\n"
    )
    fns = {n.name: n for n in planted.body if isinstance(n, ast.FunctionDef)}
    assert _checks_something(fns["test_asserts"]) is True
    assert _checks_something(fns["test_mock_assertion"]) is True, (
        "mock assertions are checks — counting them as silent is the false positive this detector "
        "was corrected for")
    assert _checks_something(fns["test_raises_ctx"]) is True
    assert _checks_something(fns["test_nothing"]) is False, "the detector cannot see a silent test"


def test_THE_GATE_ACTUALLY_SCANS_THIS_REPO():
    """A second vacuity guard, for the other way this passes on nothing: if `_test_functions` yielded
    an empty stream — a bad glob, a moved directory — every assertion above would hold trivially.

    Fails if `rglob` points at the wrong root. Asserted against a floor and against this file's own
    presence, so a silent discovery failure cannot read as a clean repo.
    """
    found = list(_test_functions())
    assert len(found) > 100, "discovery collapsed — the gate above is passing on an empty scan"
    assert any(p.name == Path(__file__).name for p, _ in found), (
        "this file is not in its own scan — the discovery root is wrong")
