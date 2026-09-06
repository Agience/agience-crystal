"""The agnostic operator-framework substrate (crystal shared library): operator fitness semantics
(`evolution.preserve_fitness` — forgery-proof) and the license
boundary (crystal is the base — it imports neither ember nor chorus). The persona organons that use this
substrate live in chorus and are tested there; this pins only the shared, domain-agnostic core."""
from __future__ import annotations

import ast
from pathlib import Path

from crystal import evolution


class _Capture:
    def __init__(self):
        self.docs = {}

    def get_artifact(self, aid):
        return self.docs.get(aid)

    def put_artifact(self, doc):
        self.docs[doc["id"]] = dict(doc)
        return doc


def test_preserve_fitness_carries_on_identical_spec():
    s = _Capture()
    s.put_artifact(evolution.preserve_fitness(s, {
        "id": "op.k", "kind": "composition", "spec": {"steps": [{"op": "op.a"}]},
        "verified": 500, "invocations": 900}))
    again = evolution.preserve_fitness(s, {"id": "op.k", "kind": "composition",
                                           "spec": {"steps": [{"op": "op.a"}]}})
    assert again["verified"] == 500 and again["invocations"] == 900
    assert "spec_change" not in again


def test_preserve_fitness_resets_on_spec_change_and_records_the_reset():
    s = _Capture()
    s.put_artifact(evolution.preserve_fitness(s, {
        "id": "op.k", "kind": "composition", "spec": {"steps": [{"op": "op.harmless"}]},
        "verified": 500, "invocations": 900}))
    redefined = evolution.preserve_fitness(s, {"id": "op.k", "kind": "composition",
                                               "spec": {"steps": [{"op": "op.something.else"}]}})
    for f in evolution.FITNESS_FIELDS:
        assert f not in redefined, "%s was laundered across a spec change" % f
    assert redefined["spec_change"]["discarded"] == {"verified": 500, "invocations": 900}
    assert redefined["spec_change"]["from"] != redefined["spec_change"]["to"]


def test_spec_hash_none_for_code_backed_operators_so_fitness_carries():
    """Code-backed operators registered by name have no (kind, spec); re-registration is genuinely the
    same operator, so fitness legitimately carries."""
    assert evolution.spec_hash({"id": "op.math.add"}) is None
    s = _Capture()
    s.put_artifact({"id": "op.math.add", "verified": 7})
    doc = evolution.preserve_fitness(s, {"id": "op.math.add", "content": "re-registered"})
    assert doc["verified"] == 7


def test_framework_imports_neither_ember_nor_chorus():
    """Crystal is the agnostic base library: its shared substrate imports no downstream package.

    The scan walks `src/crystal/` by AST, not `tests/`: the claim is about the base library — *"crystal
    is the base — it imports neither ember nor chorus"* — not about the suite. A test that imports
    ember is a test acting as a host, which is the only way to prove a crystal conducts against a real
    instrument at all; banning that would ban the conformance proof.

    AST, not a regex, because crystal's modules discuss both `ember` and `chorus` at length in prose:
    a line-anchored regex over source text would also match those words inside a docstring.

    Both import forms are checked. `from ember import optics` names the package in the module path;
    `import ember.optics as o` names it in `names`. A scan keyed on only one of them mis-resolves the
    other.

    `test_embodiment_injection.py::test_crystal_src_imports_no_instrument_anywhere` is the stronger
    ratchet: it blocks the modules at the meta-path and drives the whole capacitor flow with them
    unimportable, a runtime proof rather than a reading. This test stays as the cheap, immediate one,
    reading the same tree that one does.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "crystal"
    assert src.is_dir(), (
        "%s does not exist — this guard would scan nothing and pass. It is the SUBSTRATE that may "
        "not import downstream, so the substrate is what must be on disk." % src)

    banned = {"ember", "chorus"}
    offenders = []
    scanned = 0
    for p in sorted(src.rglob("*.py")):
        if "__pycache__" in p.parts or p.name.startswith("test_"):
            continue
        scanned += 1
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))):
            roots = set()
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots = {node.module.split(".")[0]}
            if roots & banned:
                offenders.append("%s:%d  %s"
                                 % (p.relative_to(src).as_posix(), node.lineno,
                                    ast.unparse(node).strip()))

    assert scanned > 5, (
        "the scan read only %d substrate files — it is not looking" % scanned)
    assert offenders == [], (
        "crystal's substrate imports a downstream package:\n  %s\n\n"
        "crystal is BELOW ember and chorus. An instrument arrives as an injected `embodiment=` / "
        "`conservation=` argument resolved by the HOST at assembly — it is never imported here."
        % "\n  ".join(offenders))
