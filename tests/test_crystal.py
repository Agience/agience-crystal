"""The base `Crystal` object: structure over the prism contract, flow over an injected embodiment.

These pin the two halves and the seam between them: (1) identity/gating delegate to the stdlib-only
contract and never require the instrument; (2) the capacitor flow delegates to the membrane, where
the coupling's sign is measured, never declared; (3) binding is persona-local and does not change
the crystal's shareable sha; (4) zero domain logic — the base bakes in no facet, tekton, or gauge.

The instrument arrives as an argument. `ember` is imported here, in a test, and nowhere under
`src/` — the host is what fills the slot. `test_embodiment_injection.py` proves the same crystal
runs against a second, entirely independent embodiment.

The two halves come from two packages, and the split is the point: `conservation` is
`prism.conservation`, behind `prism[wire]`; `optics` is the aperture, `ember.optics`. This file
imports each directly from its own package.
"""
from __future__ import annotations

import numpy as np
import pytest

from ember import optics as _optics
from prism import conservation as _conservation

from crystal import Crystal
from crystal.crystal_model import crystal_sha

# A minimal, valid two-facet crystal (the contract needs ≥1 facet, ≥1 named tekton, created_by).
SPEC = {
    "name": "crystal.test.pair",
    "facets": [
        {"name": "a", "direction": "both"},
        {"name": "b", "direction": "both"},
        {"name": "read", "direction": "out"},
    ],
    "tektons": [{"name": "sage", "domain": "test"}],
    "organons": [
        {"name": "op.retrieve", "requires": ["store.read"]},
        {"name": "op.reason", "requires": ["compute.local"]},
    ],
    "created_by": "john@ikailo.com",
}

# an ordered (T=6, D=3) frame with structure on every axis
_F = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [0, 1, 1], [1, 0, 1]], dtype=float)


def _energized(spec=SPEC, **kw) -> Crystal:
    """A crystal with the real aperture injected — what a full node assembles.

    The modules go in unadapted: `ember.optics` is an `Embodiment` and `prism.conservation` is a
    `Conservation`, because `prism/embodiment.py` names its members to match the embodiment
    implementation, not the reverse."""
    return Crystal(spec, embodiment=_optics, conservation=_conservation, **kw)


def _identity_crystal() -> Crystal:
    c = _energized()
    c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    c.bind("b", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    return c


# ── structure / identity (no aperture required) ───────────────────────────────────────────────────

def test_invalid_spec_refuses_to_ground():
    with pytest.raises(ValueError):
        Crystal({"name": "x", "facets": [], "tektons": [], "created_by": "j"})  # sealed, no tekton


def test_structure_delegates_to_the_contract():
    c = Crystal(SPEC)
    assert c.name == "crystal.test.pair"
    assert [f["name"] for f in c.facets] == ["a", "b", "read"]
    assert [t["name"] for t in c.tektons] == ["sage"]
    assert c.sha == crystal_sha(SPEC)


def test_construction_does_not_touch_the_aperture():
    """Reading structure must not build a membrane: the embodiment is only reached once flow is
    actually driven, and crystal's own source never imports the instrument at all."""
    c = Crystal(SPEC)
    _ = (c.name, c.facets, c.sha, c.required_capabilities())
    assert c._m is None, "the membrane must not exist until the flow is driven"


def test_artifact_roundtrips_and_verify_refuses_tamper():
    c = Crystal(SPEC)
    art = c.artifact()
    back = Crystal.from_artifact(art)
    assert back.sha == c.sha
    art["content"] = art["content"].replace("crystal.test.pair", "crystal.evil")
    with pytest.raises(ValueError):
        Crystal.from_artifact(art)  # the sha does not match the mutated structure — verification fails


# ── the prism junction gate (per-organon and whole-crystal) ─────────────────────────────────────────

def test_activates_on_is_a_capability_subset():
    c = Crystal(SPEC)
    assert c.required_capabilities() == ["compute.local", "store.read"]
    assert not c.activates_on(["store.read"])              # missing compute.local
    assert c.activates_on(["store.read", "compute.local", "extra"])
    assert c.can_discharge("op.retrieve", ["store.read"])  # this one organon is satisfied
    assert not c.can_discharge("op.reason", ["store.read"])
    with pytest.raises(KeyError):
        c.can_discharge("op.nope", ["store.read"])


# ── binding is persona-local and does not change the shareable identity ──────────────────────────────

def test_binding_does_not_change_the_sha():
    c = Crystal(SPEC)
    before = c.sha
    c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    assert c.sha == before, "conversions are persona code — they never enter the shared structure"


def test_bind_enforces_direction():
    c = Crystal(SPEC)
    with pytest.raises(ValueError):
        c.bind("a", entry=lambda x: x)               # 'both' needs an inverse too
    with pytest.raises(ValueError):
        c.bind("read", inverse=None, entry=None)     # 'out' needs an inverse
    c.bind("read", inverse=lambda X: X)              # out-only: inverse alone is enough
    with pytest.raises(KeyError):
        c.bind("ghost", entry=lambda x: x)


# ── the capacitor flow, on the membrane ─────────────────────────────────────────────────────────────

def test_couple_sign_is_measured_not_declared():
    c = _identity_crystal()
    c.conduct("a", _F)
    c.conduct("b", _F)
    assert c.couple("a", "b") > 0.0, "identical sides attract (+)"
    c.clear("b")
    c.conduct("b", -_F)
    assert c.couple("a", "b") < 0.0, "opposed sides detract (−)"


def test_condense_and_placed_track_the_flow():
    c = _identity_crystal()
    assert c.placed == []
    c.conduct("a", _F)
    assert "a" in c.placed
    res = c.condense()             # absorption: settled resolution (or None = silence)
    assert res is None or hasattr(res, "shape")


def test_conduct_blocked_on_out_only_facet():
    c = _energized().bind("read", inverse=lambda X: X)
    with pytest.raises(ValueError):
        c.conduct("read", _F)      # 'out' cannot conduct a surface in


def test_conduct_requires_binding_first():
    c = _energized()
    with pytest.raises(ValueError):
        c.conduct("a", _F)         # not bound yet


def test_emit_renders_through_the_inverse():
    c = _identity_crystal()
    c.conduct("a", _F)
    out = c.emit("a", _F)          # render a concept out through the bound inverse
    assert np.asarray(out).shape[-1] == _F.shape[-1]
