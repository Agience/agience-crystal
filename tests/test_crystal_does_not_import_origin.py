"""crystal is Apache-2.0 and origin is AGPL-3.0-only. Guards that no non-test source in crystal
imports a copyleft package, so a copyleft dependency stays a service reached over HTTP rather than
a library linked into an Apache package.

If crystal needs to verify a token, a claim, or a scope, `prism.trust` provides it. If it needs one
minted, that is an HTTP call to Origin at `ORIGIN_URI`, not an import. A generic helper — a uvicorn
log format, for instance — that happens to live in an AGPL repo is not a reason for an Apache
package to link it; it is copied or moved to prism instead.

Reads the AST rather than grepping, so `import origin.logging_utils as lu` and `from
origin.logging_utils import build_log_config` are both caught, while the word "origin" in a comment
or docstring is not — crystal's prose uses it constantly and legitimately (`allow_origins`,
`origin_uri`, "the collection's origin root"), which is why a grep would be unusable here. A lazy
import inside a function is caught too — the walk covers the whole tree, not just the module body —
because a runtime dependency is still a dependency.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

#: Copyleft packages. An Apache package importing one of these is a licence violation, not a style
#: problem. Exactly one name is on this list, checked against the manifests rather than assumed by
#: `test_the_copyleft_list_is_true_of_the_actual_manifests` below.
#:
#: `mantle` is deliberately not here: `agience-mantle/pyproject.toml` and its `LICENSE` both declare
#: Apache-2.0. It is covered by SERVICES below instead, for the architecture reason rather than a
#: licence one.
COPYLEFT = {"origin"}

#: Reached over the wire, never linked — `ORIGIN_URI`, `MANTLE_URI`. This is an architecture rule
#: rather than a licence one: crystal is the gateway, and a gateway that imports a service it
#: fronts stops being a gateway.
SERVICES = {"origin", "mantle"}


def _is_test(path: pathlib.Path) -> bool:
    return (any(p in ("tests", "test") for p in path.parts)
            or path.name.startswith("test_")
            or path.name == "conftest.py")


def _imports(path: pathlib.Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:                                    # pragma: no cover — a broken file is
        return set()                                       # someone else's failure, not this one's
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.add(node.module.split(".")[0])
    return found


def _non_test_sources() -> list[pathlib.Path]:
    return [p for p in sorted(SRC.rglob("*.py")) if not _is_test(p)]


def test_no_non_test_source_imports_a_copyleft_package():
    """The ratchet: no non-test source may import a copyleft package."""
    offenders = sorted((p.relative_to(SRC).as_posix(), sorted(_imports(p) & COPYLEFT))
                       for p in _non_test_sources() if _imports(p) & COPYLEFT)
    assert not offenders, (
        "crystal imports a copyleft package again, in: %s\n"
        "crystal is Apache-2.0 and origin is AGPL. It is a SERVICE reached at ORIGIN_URI, not a "
        "library. Verifying a token, a claim or a scope is `prism.trust`; minting one is an HTTP "
        "call; a generic helper gets copied or moved to prism, never linked." % offenders)


def test_no_non_test_source_imports_a_service_it_fronts():
    """The architecture half: crystal is the gateway, and it must reach origin/mantle over HTTP."""
    offenders = sorted((p.relative_to(SRC).as_posix(), sorted(_imports(p) & SERVICES))
                       for p in _non_test_sources() if _imports(p) & SERVICES)
    assert not offenders, (
        "crystal imports a service it fronts, in: %s\nThese are reached at ORIGIN_URI / MANTLE_URI "
        "over HTTP. A gateway that links the services it routes to is not a gateway." % offenders)


def test_the_copyleft_list_is_true_of_the_actual_manifests():
    """The claim under the claim, checked rather than remembered.

    Every assertion in this file rests on "origin is copyleft and crystal is not". This reads the
    manifests directly rather than trusting that sentence: if origin relicenses, or if mantle ever
    becomes AGPL, this fails and the lists above get corrected — instead of a guard quietly
    protecting the wrong thing."""
    try:
        import tomllib
    except ModuleNotFoundError:                                  # pragma: no cover
        import tomli as tomllib                                  # type: ignore[no-redef]

    workspace = SRC.parents[1]
    checked, absent = [], []
    for pkg in sorted(SERVICES):
        manifest = workspace / f"agience-{pkg}" / "pyproject.toml"
        if not manifest.exists():           # crystal must build standalone, so this is legitimate
            absent.append(pkg)
            continue
        lic = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["license"]
        declared = (lic.get("text") if isinstance(lic, dict) else lic) or ""
        is_copyleft = "GPL" in declared.upper()
        assert is_copyleft == (pkg in COPYLEFT), (
            f"agience-{pkg} declares {declared!r}, which contradicts this file's COPYLEFT list "
            f"({sorted(COPYLEFT)}). Correct the list — a guard with a false stated reason is worse "
            f"than no guard.")
        checked.append(pkg)

    # Absence must be proven, not assumed. Skipping every package because the paths were wrong
    # would pass this test forever while checking nothing, so a package left unchecked must be
    # one whose directory genuinely is not here.
    for pkg in absent:
        assert not (workspace / f"agience-{pkg}").exists(), (
            f"agience-{pkg} is checked out but its pyproject.toml was not found at the expected "
            f"path — this test is silently checking nothing")
    assert checked or absent, "the SERVICES list is empty; this test asserts nothing"


def test_the_manifest_does_not_declare_a_copyleft_dependency():
    """The half an import scan cannot see.

    `dependencies = ["agience-origin"]` would put copyleft on the install path of an Apache package
    for anyone running `pip install agience-crystal`, whether or not a single line is ever imported.
    The import and the declaration are two separate edges, and both must stay cut."""
    try:
        import tomllib
    except ModuleNotFoundError:                                  # pragma: no cover
        import tomli as tomllib                                  # type: ignore[no-redef]

    manifest = tomllib.loads(
        (SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))
    declared = manifest["project"]["dependencies"]

    # PEP 639 spells the licence as a plain SPDX string. The deprecated `{text = "..."}` table is
    # accepted here too, so this reads the licence rather than the syntax that happened to express
    # it — the assertion is about crystal being Apache, not about which form the manifest uses.
    licence = manifest["project"]["license"]
    if isinstance(licence, dict):
        licence = licence.get("text")
    assert licence == "Apache-2.0", (
        "crystal stopped being Apache — every assertion in this file is about that fact. "
        "The manifest says %r." % (manifest["project"]["license"],))
    offenders = [d for d in declared
                 if any(d.split("[")[0].strip().lower() == f"agience-{p}" for p in COPYLEFT)]
    assert not offenders, (
        f"crystal's manifest declares {offenders}, which is copyleft. An Apache package must "
        f"not resolve copyleft onto its own install path.")


def test_the_scan_can_actually_see_an_import():
    """The control. A scan pointed at the wrong directory, or an AST walk that missed
    `from x import y`, would pass the assertions above forever. So: prove the corpus is non-empty,
    prove a known-true import is detected, and prove the detector is not simply saying no."""
    sources = _non_test_sources()
    assert len(sources) > 15, f"the scan found only {len(sources)} source files — it is not looking"

    # A known-true positive over the real corpus: prism supplies much of what crystal needs from
    # outside itself, and `crystal_model.py` re-exports the whole contract from it.
    prism_readers = [p for p in sources if "prism" in _imports(p)]
    assert len(prism_readers) >= 5, (
        "fewer than five files import prism — either that dependency regressed or the detector is "
        "broken, and in both cases the assertions above mean nothing")

    # The shape of import this file's checks must catch: lazy, inside a function, `from ... import`.
    fake = ast.parse("def run():\n    from origin.logging_utils import build_log_config\n")
    found = {n.module.split(".")[0] for n in ast.walk(fake)
             if isinstance(n, ast.ImportFrom) and n.module}
    assert "origin" in found, "the AST walk misses the lazy `from origin.x import y` D3 deleted"

    # ...and the aliased module form, which `from`-only matching would sail past.
    fake2 = ast.parse("import origin.logging_utils as lu\n")
    found2 = {a.name.split(".")[0] for n in ast.walk(fake2)
              if isinstance(n, ast.Import) for a in n.names}
    assert "origin" in found2, "the AST walk misses `import origin.x as y`"


def test_the_host_runs_with_the_services_unimportable():
    """The real proof, and the only one source analysis alone cannot give.

    Every check above reads source, and source analysis cannot see an import that happens through
    `importlib`, a `__getattr__` firing on module load, or a transitive pull from a sibling. `origin`
    is installed in this workspace (editable, from the sibling checkout), so an arrow that slipped
    past a source scan would keep working until someone tried to install crystal on its own.

    Blocks `origin` and `mantle` outright with a `meta_path` finder that raises, then imports
    `crystal.host` for real and builds the log config. If anything reaches for either package, this
    fails with a traceback pointing at the line that did.

    Runs in a subprocess because the packages are importable in this environment — blocking them in
    the current interpreter would not survive what is already in `sys.modules`.
    """
    program = f"""
import sys

BLOCKED = {sorted(SERVICES)!r}

class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in BLOCKED:
            raise ImportError(
                'BLOCKED BY THE TEST: crystal reached for %r, which is a SERVICE reached over HTTP' % name)
        return None

sys.meta_path.insert(0, _Blocker())
for m in list(sys.modules):
    if m.split('.')[0] in BLOCKED:
        del sys.modules[m]

# ── the control: the blocker must actually bite, or every result below is vacuous. ──
try:
    import origin
except ImportError:
    pass
else:
    raise AssertionError('the blocker did not fire on `import origin` — the proof is vacuous')

# ── the measurement ──
import crystal.host                       # the file that held the arrow
import crystal.main                       # the service entrypoint
from crystal.logging_config import build_log_config

cfg = build_log_config()
assert 'origin' not in repr(cfg), 'the log config still names origin'
assert cfg['filters']['redact_access_query']['()'].startswith('crystal.'), cfg

# Drive `run()` itself: the import this file exists to prevent is lazy, inside this function,
# so merely importing the module executes none of it and a re-added arrow would sail straight
# past every assertion above. Stub `uvicorn.run` to capture its arguments instead of binding a
# socket, then call the function. This is the line that would raise ImportError under the
# blocker if the origin import came back.
import uvicorn
captured = {{}}
uvicorn.run = lambda app, **kw: captured.update(kw)

crystal.host.run(app=object())

assert captured, '`run()` never reached uvicorn.run — the drive did not happen'
reached = captured['log_config']
assert reached == build_log_config(), 'the config uvicorn received is not the one crystal builds'
assert 'origin' not in repr(reached), 'an origin-owned config reached uvicorn'

import logging.config
logging.config.dictConfig(reached)        # and it must be a config that actually applies

print('CRYSTAL OK')
"""
    r = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                       cwd=str(SRC))
    assert "CRYSTAL OK" in r.stdout, (
        "crystal could not be imported with origin and mantle blocked:\n"
        + (r.stderr or r.stdout)[-2500:])


def test_the_prose_is_not_what_is_being_measured():
    """The other control, and the one specific to this repo.

    crystal's source says "origin" constantly and legitimately — `allow_origins=["*"]`, `origin_uri`,
    `is_origin` edges, "the collection's origin root". A grep-based scan would be red on every one of
    them. Proves the scan reads imports, not prose: a file that is nothing but the word in prose and
    identifiers must scan clean."""
    prose = ast.parse(
        '"""The collection\'s origin root, cross-origin."""\n'
        'origin_uri = "http://localhost:8080"\n'
        'allow_origins = ["*"]\n'
        'def check(origin): return origin.rstrip("/")\n')
    found = set()
    for node in ast.walk(prose):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.add(node.module.split(".")[0])
    assert "origin" not in found, "the scan is matching prose, not imports — it will be weakened"

    # And the real corpus proves the same thing is live: files that SAY origin without importing it.
    talkers = [p for p in _non_test_sources()
               if "origin" in p.read_text(encoding="utf-8") and "origin" not in _imports(p)]
    assert len(talkers) >= 3, (
        "expected several crystal sources to mention origin in prose while importing nothing — if "
        "none do, this control is not exercising the distinction it claims to")
