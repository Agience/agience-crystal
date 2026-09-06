"""Crystal's licence must say the same thing everywhere.

Crystal is Apache so that it can publish while the AGPL platform sits above it — the licence rule
holds by topology, because AGPL components are sinks that nothing imports, and everything imports
crystal. A file header claiming AGPL is the one artifact a reader consults before depending on it.

A licence stated in more than one place can drift, and the copy a human reads is rarely the copy a
tool reads. This test makes the package manifest (`pyproject.toml`) authoritative and every other
mention — `LICENSE`, and any source file that names a licence — derivative of it.
"""

import pathlib
import re
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _declared_licence() -> str:
    """The authoritative licence: the package manifest (see `COMPONENTS.md`)."""
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lic = data["project"]["license"]
    return (lic.get("text") if isinstance(lic, dict) else lic) or ""


#: Every SPDX id this workspace could plausibly carry. Matched anywhere on a line.
_LICENCE = re.compile(r"\b(AGPL-3\.0[\w.-]*|GPL-3\.0[\w.-]*|MIT|BSD-3-Clause|Apache-2\.0)\b")

#: Written out rather than derived. Crystal is a leaf — it cannot see the workspace, and a list
#: built by scanning sibling directories would silently shrink to nothing in a CI checkout holding
#: one repo, turning the exemption below into a blanket pass.
_SIBLINGS = re.compile(r"\b(mantle|ember|chorus|astra|aria|iris|lumen|ophan|sage|seraph|"
                       r"prism|origin|observe|pharos)\b", re.IGNORECASE)


def _contradicts(line: str, declared: str) -> bool:
    """Does this line CLAIM a licence other than the declared one?

    Attributing a licence to another component is not claiming it [2026-08-25]. This used to flag
    every SPDX token on every line, so `context.py`'s licence TABLE —

        mantle    Apache-2.0        crystal   Apache-2.0
        ember     AGPL-3.0-only     chorus    AGPL-3.0-only

    — read as this file contradicting the manifest, and CI was red on it. That table is the
    reasoning for WHY the seam sits where it does; deleting it to appease a grep would have cost the
    explanation and kept the defect. This module's docstring already states the property precisely:
    *"a file header CLAIMING AGPL is the one artifact a reader consults before depending on it."*
    Naming ember's licence is not claiming one.

    A foreign licence is therefore excused only when the line attributes it to a component that is
    NOT crystal. A line naming crystal is never excused, which keeps the check's whole strength:
    `# crystal is AGPL-3.0-only` still fails, and so does a bare header naming no component at all.

    One implementation, used by the tree scan and by its negative control. The control first
    carried its own copy of this logic — which would have kept passing against that copy after the
    real rule changed, the precise vacuity it exists to prevent.
    """
    attributed = bool(_SIBLINGS.search(line)) and not re.search(r"\bcrystal\b", line, re.I)
    return any(hit != declared and not attributed for hit in _LICENCE.findall(line))


def test_the_manifest_declares_a_licence():
    """Precondition. An absent licence would make every comparison below vacuously true."""
    assert _declared_licence().strip(), "pyproject declares no licence, so nothing can agree with it"


def test_the_license_file_matches_the_manifest():
    """`LICENSE` is what a distribution actually ships."""
    text = (_ROOT / "LICENSE").read_text(encoding="utf-8", errors="replace")
    declared = _declared_licence()
    family = declared.split("-")[0]          # Apache-2.0 -> Apache
    assert family.lower() in text.lower(), (
        "LICENSE does not contain %r, but the manifest declares %r" % (family, declared))


def test_no_source_file_states_a_different_licence():
    """Any module naming a licence must name the declared one. A file is free to mention no licence
    at all — this asserts agreement, not presence, because requiring a header everywhere would be a
    style rule rather than a correctness one.
    """
    declared = _declared_licence()
    wrong = []
    for path in sorted((_ROOT / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _contradicts(line, declared):
                wrong.append("%s:%d claims a licence other than %r" % (path.relative_to(_ROOT), i, declared))
    assert not wrong, (
        "a source file contradicts the package manifest — crystal is Apache SO THAT it can publish "
        "beneath the AGPL platform, and the header is what a human reads first:\n  "
        + "\n  ".join(wrong))


def test_the_attribution_exemption_does_not_blanket_pass():
    """The negative control for the rule above, and it is not decoration.

    The exemption added 2026-08-25 lets a line name another component's licence without failing.
    An exemption that is slightly too wide turns this whole file green forever while the property it
    guards — no file in crystal claims crystal is copyleft — quietly stops being checked. Nothing
    else in the suite would notice, because the symptom of a vacuous test is that it passes.

    So the rule is exercised directly, on lines rather than on the tree: attribution passes, and a
    claim fails, including a claim that sits on the SAME LINE as an attribution.
    """
    declared = _declared_licence()

    def flagged(line: str) -> bool:
        return _contradicts(line, declared)

    # Attribution — the real line from `context.py` that made CI red, and its neighbour.
    assert not flagged("    ember     AGPL-3.0-only     chorus    AGPL-3.0-only")
    assert not flagged("mantle ships Apache-2.0 so a store can be taken and shipped by anyone")

    # A claim, in the three shapes one actually appears in. Each MUST still fail.
    assert flagged("# SPDX-License-Identifier: AGPL-3.0-only"), \
        "a bare copyleft header with no component named is exactly what this file exists to catch"
    assert flagged("__license__ = 'GPL-3.0-or-later'"), \
        "a licence dunder naming no component must not be exempt"
    assert flagged("# crystal is AGPL-3.0-only, unlike ember"), \
        "naming crystal on the line must defeat the exemption even when a sibling is named too"
