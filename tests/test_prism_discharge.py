"""Sample + pins: an organon lit on a prism via its capabilities — the runtime prism junction.

The scenario is `op.fetch.get` (requires `net.get`) discharged on two prisms: a networked one (measures
`net.get` present → the organon lights up and runs via the injected handle) and an air-gapped one (no
`net.get` → the organon is honestly `dormant`, the gap named). The organon body never names httpx — it
names the capability; the prism is the light. Everything here is an artifact (crystal, prism). See
`agience-pharos/genesis/PRISM-AND-CAPABILITIES.md`.
"""
from __future__ import annotations

from crystal import Crystal
from prism import Capability, Prism, PRISM_CONTENT_TYPE

# ── the crystal: a persona's structure. Its op.fetch.get organon requires the real-world capability. ──
SPEC = {
    "name": "crystal.astra",
    "facets": [{"name": "source", "direction": "in"}, {"name": "indexed", "direction": "out"}],
    "tektons": [{"name": "astra", "domain": "ingestion"}],
    "organons": [{"name": "op.fetch.get", "requires": ["net.get"]}],  # a true organon: touches the world
    "created_by": "john@ikailo.com",
}


class _Granted:
    """A permissive grant authority. Discharge is closed by default, so every test whose subject is
    the hardware gate must supply one — otherwise it would go dark for the wrong reason and stop
    testing what it was written to test."""

    def may_discharge(self, principal, organon, crystal_sha=None):
        return True


# ── the organon body (persona code): codes against the abstract capability, never httpx ───────────────
def fetch_get(url: str, *, net_get) -> str:
    return net_get(url)          # HOW `net.get` works is the prism's business, not the organon's


# ── two prisms — same capability name, different light (or none) ─────────────────────────────────────
# Every capability here carries an explicit probe. `probe=None` means unverified, and an unverified
# capability is not advertised: a host asserting a structural fact must say so with a probe — which is
# what ember does too (`_probe_cpu` returns True unconditionally, and is still a probe). `lambda: True`
# below is that assertion made explicit; it is not boilerplate.
def _networked_prism() -> Prism:
    # net.get is measured present (probe passes) and provided (a fake httpx GET, peer-local).
    return Prism([
        Capability("net.get", handle=lambda u: "BODY<%s>" % u, probe=lambda: True),
        Capability("compute.local", handle=lambda: True, probe=lambda: True),   # asserted, not assumed
    ], node_id="vps-1")


def _airgap_prism() -> Prism:
    # net.get's probe fails here (no outbound socket) → it is not advertised. Honest darkness.
    return Prism([
        Capability("net.get", handle=lambda u: "BODY<%s>" % u, probe=lambda: False),
        Capability("store.read", handle=lambda i: {"id": i}, probe=lambda: True),
    ], node_id="airgap-1")


def _unmeasured_prism() -> Prism:
    """A host that has declared capabilities and measured none of them."""
    return Prism([
        Capability("net.get", handle=lambda u: "BODY<%s>" % u),          # no probe
        Capability("compute.local", handle=lambda: True),                # no probe
    ], node_id="unmeasured-1")


def test_prism_advertises_only_what_it_measurably_affords():
    assert _networked_prism().advertises() == {"net.get", "compute.local"}
    assert _airgap_prism().advertises() == {"store.read"}          # net.get probe failed → dropped


def test_an_UNMEASURED_capability_is_not_advertised():
    """The fail-closed property: a probe-less capability is unverified, not present.

    Unverified is reported separately from absent, because "no GPU here" and "nobody looked" call
    for different actions — buy hardware, versus supply a probe. A host with nothing measured
    advertises nothing; otherwise `discharge`'s hardware gate would light an organon on a host where
    nothing had been measured at all.
    """
    p = _unmeasured_prism()
    assert p.advertises() == set(), "an unmeasured host must advertise NOTHING, not everything"
    assert p.unverified() == {"net.get", "compute.local"}
    assert p.artifact()["capabilities"] == []
    assert p.artifact()["unverified"] == ["compute.local", "net.get"]


def test_a_raising_probe_is_UNVERIFIED_not_absent():
    """A probe that errors has told you nothing; `False` would claim it told you something.

    Treating a raising probe as definite absence would make the same failed probe mean "not here" in
    prism and "not measured" in ember — two SDKs disagreeing about one host.
    """
    def _boom():
        raise OSError("no socket")
    p = Prism([Capability("net.get", handle=lambda u: u, probe=_boom)], node_id="broken-1")
    assert p.advertises() == set()
    assert p.unverified() == {"net.get"}, "a raising probe must be UNVERIFIED, not a denial"


def test_organon_lights_up_and_runs_on_a_prism_that_affords_it():
    c = Crystal(SPEC)
    d = c.discharge("op.fetch.get", _networked_prism(), authority=_Granted())
    assert d["status"] == "lit"
    # the base injected the required capability handles; the persona's organon body runs on them
    out = fetch_get("http://x", net_get=d["handles"]["net.get"])
    assert out == "BODY<http://x>"


def test_organon_is_dormant_where_the_prism_lacks_the_capability():
    c = Crystal(SPEC)
    d = c.discharge("op.fetch.get", _airgap_prism(), authority=_Granted())
    assert d["status"] == "dormant"
    assert d["missing"] == ["net.get"]                            # the gap is named, not a crash
    assert "handles" not in d


def test_dormant_reports_a_REACH_gap_not_only_a_missing_name():
    """Naming only an absent string tells you nothing about how far away the affordance is. `reach`
    reports the nearest thing the prism actually affords."""
    spec = dict(SPEC, organons=[{"name": "op.sense", "requires": ["sensor.temperature"]}])

    class _Thermal:
        def advertises(self):
            return {"sensor.capture", "compute.local"}

        def capability(self, name):
            raise AssertionError("must not be reached while dormant")

    d = Crystal(spec).discharge("op.sense", _Thermal(), authority=_Granted())
    assert d["status"] == "dormant"
    assert d["missing"] == ["sensor.temperature"]          # unchanged, still named
    [m] = d["reach"]
    assert m["basis"] == "family" and m["matched"] == "sensor.capture" and m["hops"] == 1


def test_reach_does_not_light_a_near_miss():
    """The gate did not move: measuring nearness must not grant it. Loosening the gate before
    discharge, even grant-authorized, would widen the permission surface."""
    spec = dict(SPEC, organons=[{"name": "op.sense", "requires": ["sensor.temperature"]}])

    class _Thermal:
        def advertises(self):
            return {"sensor.capture"}

        def capability(self, name):
            raise AssertionError("a near miss must never be handed a handle")

    assert Crystal(spec).discharge("op.sense", _Thermal(), authority=_Granted())["status"] == "dormant"
    assert not Crystal(spec).can_discharge("op.sense", ["sensor.capture"])


def test_same_crystal_different_prism_different_light():
    """The whole point: one crystal, discharged on different environments, is lit or dark accordingly."""
    c = Crystal(SPEC)
    assert c.discharge("op.fetch.get", _networked_prism(), authority=_Granted())["status"] == "lit"
    assert c.discharge("op.fetch.get", _airgap_prism(), authority=_Granted())["status"] == "dormant"


def test_prism_is_an_artifact():
    art = _networked_prism().artifact()
    assert art["content_type"] == PRISM_CONTENT_TYPE
    assert art["node_id"] == "vps-1"
    assert art["capabilities"] == ["compute.local", "net.get"]    # sorted, measured set
    assert len(art["sha256"]) == 64                               # content-addressed (seedable/loadable)


def test_can_discharge_gate_matches_the_measured_advertisement():
    c = Crystal(SPEC)
    # the pure gate (capability names only) agrees with the runtime discharge
    assert c.can_discharge("op.fetch.get", list(_networked_prism().advertises())) is True
    assert c.can_discharge("op.fetch.get", list(_airgap_prism().advertises())) is False
