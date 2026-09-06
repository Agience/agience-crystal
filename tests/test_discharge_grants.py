"""Discharge has two gates: hardware, and the grant on the energy.

With no authority wired, an organon that needs one stays dormant rather than lighting with
`authorized: None` — "nobody asked" must never resolve to "permitted".
"""
from crystal import Crystal

SPEC = {
    "name": "crystal.sensing",
    "facets": [{"name": "reading", "direction": "out"}],
    "tektons": [{"name": "sense", "domain": "telemetry"}],
    "organons": [{"name": "op.sense", "requires": ["sensor.capture"]}],
    "created_by": "john@ikailo.com",
}


class _Prism:
    def advertises(self):
        return {"sensor.capture"}

    def capability(self, name):
        return lambda: "reading<%s>" % name


class _Bare:
    """A prism with nothing — the hardware gate closes before authority is ever asked."""

    def advertises(self):
        return set()

    def capability(self, name):        # pragma: no cover - must never be called
        raise AssertionError("no handle may be injected while dormant")


class _Authority:
    """Duck-typed grant authority: crystal only ever calls `may_discharge`."""

    def __init__(self, allow):
        self.allow = allow
        self.asked = []

    def may_discharge(self, principal, organon, crystal_sha=None):
        self.asked.append((principal, organon, crystal_sha))
        return self.allow


# ── closed by default: no authority wired means no organon lights ─────────────

def test_no_authority_means_DORMANT_never_lit():
    """Closed by default: with nothing to ask, an organon that touches the world stays dark rather
    than lighting with `authorized: None`. "Nobody asked" must never resolve to "permitted"."""
    d = Crystal(SPEC).discharge("op.sense", _Prism())
    assert d["status"] == "dormant"
    assert d["reason"] == "no_authority"
    assert "handles" not in d                # no capability handle is ever injected unvouched


def test_no_authority_is_distinguishable_from_no_grant_and_no_hardware():
    """Three reasons a discharge stays dormant: 'nothing can vouch for you', 'you may not', and
    'it is not here' — a host needs to tell them apart."""
    reasons = {
        Crystal(SPEC).discharge("op.sense", _Prism())["reason"],
        Crystal(SPEC).discharge("op.sense", _Prism(), energy={"created_by": "x"},
                                authority=_Authority(False))["reason"],
        Crystal(SPEC).discharge("op.sense", _Bare())["reason"],
    }
    assert reasons == {"no_authority", "no_grant", "no_hardware"}


# ── gate 2: the grant ─────────────────────────────────────────────────────────

def test_a_granted_energy_lights_and_is_marked_authorized():
    auth = _Authority(True)
    energy = {"created_by": "john@ikailo.com", "payload": 1}
    d = Crystal(SPEC).discharge("op.sense", _Prism(), energy=energy, authority=auth)
    assert d["status"] == "lit" and d["authorized"] is True
    assert len(auth.asked) == 1
    who, what, sha = auth.asked[0]
    assert (who, what) == ("john@ikailo.com", "op.sense")
    assert sha == Crystal(SPEC).sha        # the crystal is named too — grants ride the crystal


def test_an_ungranted_energy_is_DORMANT_even_though_the_hardware_is_present():
    """The whole point: the prism affords sensor.capture, and it still does not fire."""
    auth = _Authority(False)
    energy = {"created_by": "someone-else"}
    d = Crystal(SPEC).discharge("op.sense", _Prism(), energy=energy, authority=auth)
    assert d["status"] == "dormant"
    assert d["reason"] == "no_grant"
    assert d["principal"] == "someone-else"
    assert "handles" not in d               # no handle is ever injected while dormant


def test_an_energy_with_no_provenance_yields_a_null_principal():
    """An energy that cannot say where it came from cannot be granted anything, but crystal does
    not decide that itself — it hands None to the authority and lets the authority decide."""
    auth = _Authority(False)
    d = Crystal(SPEC).discharge("op.sense", _Prism(), energy={"payload": 1}, authority=auth)
    assert d["status"] == "dormant" and d["principal"] is None
    assert [(w, o) for w, o, _ in auth.asked] == [(None, "op.sense")]


def test_principal_is_read_from_an_object_too():
    class _Signal:
        created_by = "sensor-daemon"

    auth = _Authority(True)
    Crystal(SPEC).discharge("op.sense", _Prism(), energy=_Signal(), authority=auth)
    assert [(w, o) for w, o, _ in auth.asked] == [("sensor-daemon", "op.sense")]


# ── the two gates stay distinguishable ────────────────────────────────────────

def test_hardware_and_grant_refusals_are_different_reasons():
    """'You have no thermometer' and 'you may not touch the thermometer' need opposite responses."""
    hardware = Crystal(SPEC).discharge("op.sense", _Bare())
    assert hardware["status"] == "dormant" and hardware["reason"] == "no_hardware"
    assert hardware["missing"] == ["sensor.capture"]

    grant = Crystal(SPEC).discharge(
        "op.sense", _Prism(), energy={"created_by": "x"}, authority=_Authority(False))
    assert grant["status"] == "dormant" and grant["reason"] == "no_grant"
    assert grant["missing"] == []           # nothing is missing from the environment


def test_hardware_gate_runs_first_and_never_consults_the_authority():
    """No point asking whether you may use what is not there."""
    auth = _Authority(True)
    d = Crystal(SPEC).discharge("op.sense", _Bare(), energy={"created_by": "x"}, authority=auth)
    assert d["reason"] == "no_hardware"
    assert auth.asked == []
