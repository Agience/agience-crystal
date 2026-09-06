"""Every URL a caller builds against Crystal must reach a route this node serves.

Mantle has the same guard for mantle-bound calls, and it is scoped to mantle-bound URLs by design —
so nothing watched the Crystal plane. `prism.server.client.AgienceClient` carried an `embed()` that
posted to `{CRYSTAL_URI}/embed`, a route Crystal does not serve and says in its own source it will
not: "an observer computes the coordinate where it is needed rather than offering it as a service".
It reached the eve of prism's first PyPI release. prism's own routing test passed throughout,
because it pins which base a path is sent to and not whether the path exists.

The check runs from the live route table (`crystal.main.app`), so a route retired tomorrow is
covered without anyone editing a list.

Two call shapes are read, because the callers do not agree on one:

  * `prism.server.client.AgienceClient` — methods call `self.get`/`self.post` with a path, and
    `AgienceClient._base_for` decides which plane it goes to. That function is imported and called
    rather than reimplemented: a copy of prism's routing rule here would agree on the day it was
    written and drift afterwards, which is the failure this file exists to catch.
  * `artifact_url(CRYSTAL_URI, id, *suffix)` — how the chorus personas reach Crystal. It builds
    `{base}/artifacts/{id}/{suffix...}`.

`test_the_scan_finds_real_crystal_calls` is the control, and it is the one that matters: a detector
that reads nothing reports a clean plane for ever. It is asserted against the total, not per shape,
because a shape with no current callers is not evidence of a broken scan.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from crystal.main import app

_WORKSPACE = pathlib.Path(__file__).resolve().parents[2]
_SKIP = {".git", "node_modules", "_ci-work", "dist", "build", "__pycache__", ".venv", "_archive"}

#: Sibling trees that call Crystal.
SIBLINGS = ("agience-chorus", "agience-mantle", "agience-ember", "agience-observe")

#: Names that hold a Crystal base URL at the call site, read from the real call sites.
_CRYSTAL_BASES = ("CRYSTAL_URI", "crystal_uri", "CRYSTAL_BASE", "_CRYSTAL")


def _live_matchers():
    """One regex per mounted route. `{p}` stands for one segment; `{p:path}` for any number."""
    out = []
    for path in {getattr(r, "path", "") for r in app.routes}:
        if not path:
            continue
        pattern = re.sub(r"\{[^}:]+:path\}", ".+", path)
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", pattern)
        out.append(re.compile("^" + pattern + "/?$"))
    return out


def _probe(fragment: str) -> str:
    """A concrete path to test a template against — every placeholder filled with one segment."""
    return re.sub(r"\{[^}]*\}", "zzz", fragment).split("?")[0].rstrip("/") or "/"


# ── shape 1: prism's published client ────────────────────────────────────────────────────────────

def _prism_client_paths():
    """Every path `AgienceClient` builds, with the plane prism itself routes it to.

    Yields `(path_template, base)` where base is the sentinel URL prism chose, so the caller can
    ask "did prism send this to Crystal?" without knowing prism's rule.
    """
    prism_client = pytest.importorskip(
        "prism.server.client", reason="prism's server surface is not installed")
    from prism.server.auth import Server

    probe = Server("route-gate", "http://mantle.invalid", crystal_uri="http://crystal.invalid")
    client = prism_client.AgienceClient(probe)

    source = pathlib.Path(prism_client.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("get", "post")
                and isinstance(fn.value, ast.Name) and fn.value.id == "self"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            template = arg.value
        elif isinstance(arg, ast.JoinedStr):
            template = "".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in arg.values)
        else:
            continue
        if not template.startswith("/"):
            continue
        yield template, client._base_for(template.replace("{}", "zzz"))


# ── shape 2: artifact_url(CRYSTAL_URI, id, *suffix) ──────────────────────────────────────────────

def _artifact_url_paths(tree: ast.AST):
    """`artifact_url(CRYSTAL_URI, aid, "op", "invoke")` -> `/artifacts/{id}/op/invoke`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name != "artifact_url" or not node.args:
            continue
        base = ast.unparse(node.args[0])
        if not any(b in base for b in _CRYSTAL_BASES):
            continue
        suffix = [a.value for a in node.args[2:]
                  if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        yield "/".join(["/artifacts/{id}"] + suffix), node.lineno


def _sibling_sources():
    for name in SIBLINGS:
        root = _WORKSPACE / name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in _SKIP for part in path.parts):
                continue
            if path.name.startswith("test_") or "tests" in path.parts:
                continue
            yield path


def _crystal_bound_calls():
    """Every crystal-bound path any caller builds, as `(where, template)`."""
    found = []
    for template, base in _prism_client_paths():
        if "crystal.invalid" in base:
            found.append(("prism/server/client.py", template))
    for path in _sibling_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for template, _lineno in _artifact_url_paths(tree):
            found.append((path.relative_to(_WORKSPACE).as_posix(), template))
    return found


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────

def test_no_caller_builds_a_crystal_url_that_reaches_nothing():
    """The gate. Fails on a path sent to Crystal that matches no route Crystal serves — a
    well-formed request into a 404, which every caller's own test reports as correct routing."""
    matchers = _live_matchers()
    offenders = sorted({
        "%s -> %s" % (where, template)
        for where, template in _crystal_bound_calls()
        if not any(m.match(_probe(template)) for m in matchers)
    })
    assert not offenders, (
        "crystal-bound calls that reach no route this node serves:\n  "
        + "\n  ".join(offenders))


def test_the_scan_finds_real_crystal_calls():
    """The control. A detector that matches nothing passes the gate above for ever, which is
    exactly how the Crystal plane went unwatched while a dead call sat in a published SDK."""
    calls = _crystal_bound_calls()
    assert len(calls) >= 3, (
        "only %d crystal-bound calls seen (%s) — the scan is not reaching them, and a guard that "
        "reads nothing passes for ever" % (len(calls), calls))


def test_the_gate_can_fail():
    """Vacuity control for the matcher itself. If `_live_matchers` ever matched everything, the
    gate would accept any path at all."""
    matchers = _live_matchers()
    assert matchers, "crystal declares no routes — the matcher list is empty"
    assert not any(m.match("/a-route-crystal-does-not-serve") for m in matchers), (
        "the route matchers accept an invented path, so the gate cannot fail")
    assert any(m.match("/create") for m in matchers), (
        "the route matchers reject /create, which crystal does serve — the gate fires on everything")
