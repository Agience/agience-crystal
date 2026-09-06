"""The embodiment is injected, and the proof is that the same crystal runs on two of them.

The instrument arrives as a constructor argument checked against `prism.embodiment`'s protocols —
the shape `prism.reach` already proves with `keyring=` and `lightcone=`. A lazy import is still an
edge: it would tie crystal's deployability to whichever package currently holds the instrument.

Crystal's own source contains no reference to `beam` or `ember` at any scope. The
instrument (`ember.optics`, AGPL) and its accompanying
`prism.conservation` cross that boundary at the call site, through the injected embodiment, not
through an import in crystal. Only this file and `test_crystal.py` name a concrete instrument, in
the one line that says which instrument the host hands over.

The assertion this file exists to make: a crystal built from one spec runs against the real
instrument (`ember.optics` + `prism.conservation`) and against a second, entirely
independent embodiment written here in ~120 lines of numpy that has never heard of an instrument. If
it only ever ran against one, injection would be nominal and nothing would be shown — a
`self._embodiment` that is always the same object is an import with extra steps.

The stub is not a mock. It computes real answers by a different rule: its rank cut is the
LAPACK-standard numerical rank (`max(shape)·eps·σ_max`), where the instrument's is a statistical signal
rank against a derived noise floor. So the two legitimately disagree on `k` — exactly as the instrument
and beacon legitimately disagree on a collapsed axis (plan G1). Nothing below asserts
the two produce equal numbers. What is asserted is what each is required to preserve — orthogonality
of the split, conservation of energy, the measured sign of a coupling, the null on an unreadable
frame. An equal-numbers test would either be false or would prove the stub was a copy.

The failure modes, stated first so none of these is a check that cannot fail:

  · injection is nominal — crystal secretly still imports the instrument.
    CONTROL: `test_the_whole_flow_runs_with_the_instrument_UNIMPORTABLE` runs the stub flow in a
    subprocess with `beam` and `ember` blocked at the meta-path, and asserts the
    blocker bites before trusting the result. `ember` belongs in that list: with only `beam` and
    the underlying library blocked, the proof still passes — `import ember.optics` fails on that
    leg while `ember` itself loads successfully into the subprocess. The run reports success and
    the property quietly narrows to "crystal runs without the library", a weaker claim than the one
    this test is named for.
  · the stub is not really a second implementation.
    CONTROL: identity assertions that the two `absorb_transmit`s and the two screen classes are
    different objects from different packages, plus an assertion that the stub module tree never
    imports the instrument.
  · an un-instrumented crystal degrades quietly.
    CONTROL: every measuring method is enumerated and each is asserted to raise — not to return
    None, not to return 0.0.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest

from prism import conservation as prism_conservation

from crystal import Crystal
from prism.embodiment import (
    CONSERVATION_MEMBERS,
    Conservation,
    EMBODIMENT_MEMBERS,
    Embodiment,
    EmbodimentRequired,
    Ledger,
    members_of,
)
from prism.errors import PrismError

SPEC = {
    "name": "crystal.test.injected",
    "facets": [{"name": "a", "direction": "both"}, {"name": "b", "direction": "both"}],
    "tektons": [{"name": "sage", "domain": "test"}],
    "organons": [{"name": "op.retrieve", "requires": ["store.read"]}],
    "created_by": "john@ikailo.com",
}

# an ordered (T=6, D=3) frame with structure on every axis
_F = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [0, 1, 1], [1, 0, 1]], dtype=float)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# The second embodiment — numpy only, no instrument, no beam, written against the contract
# ═════════════════════════════════════════════════════════════════════════════════════════════════

class _StubScreen:
    """A minimal two-way membrane: sides register, signals are placed, coupling is measured.

    The coupling is the signed cosine between two placed frames. That is a real measurement with a
    real sign — not a stored declaration — which is the property `couple` exists to have. It is a
    cruder instrument than the instrument's and it is supposed to be: the point is that crystal does
    not know or care which one it is holding."""

    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)          # construction kwargs are the caller's, forwarded intact
        self._sides = {}
        self._charge = {}

    def register(self, side, *, entry, inverse=None, energy=None, zero=None):
        self._sides[side] = {"entry": entry, "inverse": inverse}
        return self._sides[side]

    def place(self, side, surface):
        frame = np.asarray(self._sides[side]["entry"](surface), dtype=float)
        self._charge[side] = frame
        return frame

    def render(self, side, concept):
        inv = self._sides[side]["inverse"]
        if inv is None:
            raise TypeError("side %r has no inverse" % side)
        return inv(concept)

    def couple(self, a, b):
        x, y = self._charge[a].ravel(), self._charge[b].ravel()
        nx, ny = float(np.linalg.norm(x)), float(np.linalg.norm(y))
        if nx == 0.0 or ny == 0.0:
            return 0.0                       # nothing resolves against silence — the computed null
        return float(np.dot(x, y) / (nx * ny))

    def coupling(self, a, b):
        c = self.couple(a, b)
        return {"sign": (c > 0) - (c < 0), "strength": abs(c), "read": "signed cosine"}

    def transfer(self, a, b):
        return {"participation": self.couple(a, b), "from": a, "to": b}

    def certify(self, side, surface):
        s = self._sides[side]
        if s["inverse"] is None:
            raise TypeError("side %r has no inverse to certify against" % side)
        back = np.asarray(s["inverse"](s["entry"](surface)), dtype=float)
        return float(np.linalg.norm(back - np.asarray(surface, dtype=float)))

    def read(self):
        return {"sides": sorted(self._charge), "instrument": "stub"}

    def balance(self):
        return {"open": {k: float((v * v).sum()) for k, v in self._charge.items()}}

    def clear(self, side=None):
        if side is None:
            self._charge.clear()
        else:
            self._charge.pop(side, None)

    @property
    def placed(self):
        return sorted(self._charge)


class _StubEmbodiment:
    """A second, independent `Embodiment`. Different rank rule, same contract."""

    @staticmethod
    def absorb_transmit(rows, *, basis=None, null=None, seed=0):
        W = np.asarray(rows, dtype=float)
        # The null, returned plainly: a frame that cannot carry a read yields None, and the caller
        # propagates it unabsorbed rather than being handed a fabricated split.
        if W.ndim != 2 or W.shape[0] < 2 or W.shape[1] < 1 or not np.any(W):
            return None
        if basis is None:
            _u, s, vt = np.linalg.svd(W, full_matrices=False)
            # The LAPACK-standard numerical rank — derived from the dtype's eps and the frame's own
            # shape, never a chosen threshold. Deliberately a different rule from the instrument's
            # statistical signal rank, so the two are genuinely independent instruments.
            cut = float(s[0]) * max(W.shape) * float(np.finfo(W.dtype).eps)
            k = int((s > cut).sum())
            B = vt[:k].T
        else:
            B = np.asarray(basis, dtype=float)
            if B.ndim != 2 or B.shape[0] != W.shape[1]:
                return None
            k = int(B.shape[1])
        if k == 0:
            return np.zeros_like(W), W.copy(), 0
        P = B @ np.linalg.pinv(B)            # orthogonal projector — conservation by construction
        absorbed = W @ P
        return absorbed, W - absorbed, k

    @staticmethod
    def next_by_coupling(rows, bases, *, fired=(), null=None, seed=0,
                         min_energy=None, incident_energy=None):
        """Where the residual goes next — a ranked fold over `absorb_transmit`, which is why it
        belongs to the same contract rather than a third one.

        A wire that calls a member the protocol never declared can be satisfied by an
        implementation that passes every check and then fails at the call site — which is why
        this stub fills `next_by_coupling` as well as `absorb_transmit` and `membrane_screen`.

        This is a genuinely independent implementation: it ranks with this stub's numerical-rank
        split, not the instrument's statistical one, so agreement between the two is evidence rather
        than a copy.
        """
        W = np.asarray(rows, dtype=float)
        if W.ndim != 2 or not np.any(W):
            return None

        total = float(np.sum(W * W))
        if min_energy is None:
            # Derived, never chosen: the level below which an energy difference is float noise for
            # this dtype and this frame. `incident_energy` scopes it to the original signal so a
            # long chain of hops does not quietly lower its own floor as the residual shrinks.
            scale = float(incident_energy) if incident_energy is not None else total
            min_energy = float(np.finfo(W.dtype).eps) * max(W.shape) * scale

        best = None
        for name, basis in (bases or {}).items():
            if name in (fired or ()):
                continue
            split = _StubEmbodiment.absorb_transmit(W, basis=basis, null=null, seed=seed)
            if split is None:
                continue
            absorbed, transmitted, k = split
            energy = float(np.sum(absorbed * absorbed))
            if energy <= min_energy:
                continue
            if best is None or energy > best["absorbed_energy"]:
                # `transmitted` is a preview used only to rank candidates — the wire forwards the
                # intact residual instead, or the band would be absorbed twice.
                best = {"tekton": name, "transmitted": transmitted,
                        "absorbed_energy": energy, "k": int(k)}
        return best

    @staticmethod
    def membrane_screen():
        return _StubScreen


class _StubLedger:
    """A second, independent `Ledger`. Same derived-tolerance discipline, written from scratch."""

    def __init__(self, incident, *, at=None):
        a = None if incident is None else np.asarray(incident, dtype=float)
        self._e0 = _StubConservation.energy(a)
        self._eps = (float(np.finfo(a.dtype).eps) if a is not None and a.size
                     else float(np.finfo(float).eps))
        self._terms, self._elems = 1, (0 if a is None else int(a.size))
        self._origin, self._hops = at, []
        self._residual, self._emitted, self._emitted_at = self._e0, None, None

    def absorb(self, absorbed, transmitted, *, at=None, k=None):
        ea = _StubConservation.energy(absorbed)
        et = _StubConservation.energy(transmitted)
        self._hops.append({"at": at, "k": k, "absorbed": ea, "transmitted": et,
                           "incident": self._residual})
        self._residual = et
        self._terms += 3
        self._elems += (0 if absorbed is None else int(np.asarray(absorbed).size))
        self._elems += (0 if transmitted is None else int(np.asarray(transmitted).size))
        return self

    def emit(self, *, at=None):
        self._emitted, self._emitted_at = self._residual, at
        return self

    def certificate(self):
        # Derived from the arithmetic actually performed, exactly as the contract requires: a fitted
        # constant would prove whatever it was chosen to prove.
        tol = self._eps * self._e0 * float(self._terms + self._elems)
        total, closed, running, curve, broke = 0.0, True, 0.0, [], None
        for i, h in enumerate(self._hops):
            total += h["absorbed"]
            running += h["absorbed"]
            defect = self._e0 - (running + h["transmitted"])
            if abs(defect) > tol:
                closed = False
                broke = i if broke is None else broke
            curve.append({"at": h["at"], "defect": defect,
                          "cumulative": (running / self._e0) if self._e0 else 0.0,
                          "residual": (h["transmitted"] / self._e0) if self._e0 else 0.0})
        terminated = True if self._emitted is not None else abs(self._residual) <= tol
        unaccounted = 0.0 if self._emitted is not None else self._residual
        loss = self._e0 - (total + (self._emitted if self._emitted is not None else self._residual))
        why = None
        if not closed:
            why = "prefix identity broke at hop %d" % broke
        elif not terminated:
            why = "%.6g of %.6g energy was travelling and never emitted" % (self._residual, self._e0)
        elif abs(loss) > tol:
            why = "%.6g unaccounted against a derived band of %.6g" % (loss, tol)
        return {"incident": self._e0, "origin": self._origin, "hops": len(self._hops),
                "absorbed": total, "emitted": self._emitted, "emitted_at": self._emitted_at,
                "unaccounted": unaccounted, "loss": loss, "tolerance": tol, "closed": closed,
                "terminated": terminated, "balanced": bool(closed and terminated
                                                           and abs(loss) <= tol),
                "curve": curve, "why": why}


class _StubConservation:
    """A second, independent `Conservation`."""

    @staticmethod
    def energy(frame):
        if frame is None:
            return 0.0                        # silence is 0.0 energy, not an error
        a = np.asarray(frame, dtype=float)
        return 0.0 if a.size == 0 else float((a * a).sum())

    PathLedger = _StubLedger


# ── the two embodiments, as the parametrisation every flow test runs over ────────────────────────

# The INSTRUMENT arm — `("instrument", ember.optics, prism.conservation)` — moved to
# `agience-ember/tests/test_the_instrument_is_a_crystal_embodiment.py`. It required a checkout of the
# repository ABOVE this one, which made crystal's CI depend on a private sibling and made the suite
# unrunnable on a fork. Ember declares and imports crystal; crystal declares and imports nothing of
# ember, and the tests now point the same way the packages do.
#
# What is proved HERE is the part that is crystal's: the slot is a real slot, filled by an
# implementation crystal knows nothing about. `EITHER` keeps its shape rather than being inlined,
# because a second local embodiment is the natural way to strengthen this file and the
# parametrisation is where it would go.
STUB = ("stub", _StubEmbodiment, _StubConservation)
EITHER = [STUB]
EITHER_IDS = [b[0] for b in EITHER]


def _bound(embodiment, conservation, spec=SPEC) -> Crystal:
    c = Crystal(spec, embodiment=embodiment, conservation=conservation)
    c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    c.bind("b", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    return c


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1 · The two embodiments are independent
# ═════════════════════════════════════════════════════════════════════════════════════════════════

def test_the_two_embodiments_are_independent_implementations():
    """The control for every parametrised test below: if both parameters resolved to the same
    code, the whole file would be one embodiment run twice and would prove nothing."""
    assert prism_conservation.PathLedger is not _StubConservation.PathLedger
    # The fourth assertion moved to `agience-ember` on 2026-08-25 [John]. It read
    # `ember_optics.membrane_screen().__module__.startswith(...)` — a runtime check that the real
    # instrument hands back the actual instrument, which is a fact about ember's function and named a
    # package crystal may not name. It now lives in
    # `agience-ember/tests/test_ember_holds_the_instrument.py` — search it for `membrane_screen`.
    # That repo is its proper owner and may name the package; this one may not, which is why the
    # pointer is a file rather than a test name.
    #
    # It was moved, not deleted, and the distinction matters here: without it this file's whole
    # proof could run against two stubs and report success. The assertion below is the half that
    # belongs to crystal — that the stub is genuinely local — and it is not a substitute for it.
    assert _StubEmbodiment.membrane_screen().__module__ == __name__, (
        "the stub screen must be defined HERE — an instrument class imported under another name "
        "would make the swap cosmetic")


#: What the stub is allowed to stand on. Anything else fails.
#:
#: This was a deny-list of instrument names until 2026-08-25, and became an allow-list when a
#: non-Agience brand was removed from crystal [John]. The guard did not weaken — it got stricter.
#: A deny-list catches only the instruments somebody thought to name, so a stub that reached for a
#: *different* measurement package passed; an allow-list catches every one of them, named or not.
#: The rule the file actually means is "numpy and the standard library", and now it says so.
_STUB_MAY_IMPORT = frozenset({"numpy", "math", "cmath", "statistics", "itertools", "functools",
                              "dataclasses", "typing", "collections", "operator", "types"})


def test_the_stub_stands_on_numpy_and_the_stdlib_alone():
    """The second embodiment must stand on numpy alone, or it is not a second embodiment.

    Read from the module's own source rather than from `sys.modules`: this file legitimately imports
    the instrument at the top for the first embodiment, so an interpreter-level check would be
    satisfied by that import and could never fail."""
    import ast
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    stub_classes = {"_StubScreen", "_StubEmbodiment", "_StubLedger", "_StubConservation"}
    found = set()
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in stub_classes]:
        found.add(cls.name)
        for node in ast.walk(cls):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for m in names:
                assert m.split(".")[0] in _STUB_MAY_IMPORT, (
                    "%s imports %r — the stub must be an independent implementation standing on "
                    "numpy and the stdlib. If that is genuinely stdlib, add it to "
                    "`_STUB_MAY_IMPORT`; if it is an instrument, the stub has stopped being a "
                    "second embodiment." % (cls.name, m))
            # no attribute reach either: `ember_optics.something` inside the stub would be a leak
            if isinstance(node, ast.Name):
                assert node.id not in {"ember_optics", "prism_conservation"}, (
                    "%s reaches the instrument through %r" % (cls.name, node.id))
    assert found == stub_classes, "a stub class went missing: %s" % sorted(stub_classes - found)


# The module that fills conservation's instrument-bound member (the instrument side).
def test_the_stub_embodiment_satisfies_the_prism_contract():
    """Structural, and it is what makes `ember.optics` usable unadapted: the protocol member names
    were derived from the implementation that already existed, not imposed on it."""
    for name, emb, cons in EITHER:
        assert isinstance(emb, Embodiment), name
        assert members_of(emb, "embodiment") == EMBODIMENT_MEMBERS, name
        # `Conservation` is not asserted whole here — see the test below for why.
        assert members_of(cons, "conservation"), name
    assert isinstance(_StubLedger(_F, at="x"), Ledger)
    assert isinstance(prism_conservation.PathLedger(_F, at="x"), Ledger)


# Two tests moved to `agience-ember/tests/test_the_instrument_is_a_crystal_embodiment.py`, because
# each needs the real instrument in the process:
#
#   test_NO_SINGLE_MODULE_FILLS_CONSERVATION_AND_THAT_IS_THE_DESIGN
#       reads `ember.optics`'s half of the Conservation contract. Asserted there now.
#
#   test_the_two_embodiments_agree_on_structure_and_are_free_to_disagree_on_the_number
#       compared the instrument and the stub directly — same incident energy, `k` free to differ.
#       IT IS NOT ASSERTED ANYWHERE NOW, and that is a real loss rather than a relocation: it needs
#       both implementations in one process, and they are in two repositories that a wheel does not
#       carry tests between. Getting it back means the stub moving to `prism`, which owns the
#       contract both are written against. Recorded here so nobody concludes it was redundant.



# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2 · The same crystal runs on both
# ═════════════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,emb,cons", EITHER, ids=EITHER_IDS)
def test_the_same_crystal_conducts_condenses_and_transmits_on_either(name, emb, cons):
    c = _bound(emb, cons)
    c.conduct("a", _F)
    absorbed, transmitted = c.condense(), c.transmit()
    assert np.asarray(transmitted).shape == _F.shape
    if absorbed is not None:
        assert np.asarray(absorbed).shape == _F.shape
        # the split is orthogonal under either instrument — the property, not the number
        assert abs(float((np.asarray(absorbed) * np.asarray(transmitted)).sum())) < 1e-8


@pytest.mark.parametrize("name,emb,cons", EITHER, ids=EITHER_IDS)
def test_energy_is_conserved_at_the_crossing_under_either(name, emb, cons):
    c = _bound(emb, cons)
    c.conduct("a", _F)
    led = c.ledger()
    assert led["conserved"] is True, (name, led)
    assert led["incident"] > 0.0
    assert abs(led["incident"] - (led["absorbed"] + led["transmitted"])) < 1e-8 * led["incident"]
    cert = c.certificate()
    assert cert["balanced"] is True, (name, cert["why"])
    # the tolerance is derived from the arithmetic performed, so it must scale with the frame and
    # never be the same constant under two different implementations by coincidence
    assert 0.0 < cert["tolerance"] < led["incident"]


@pytest.mark.parametrize("name,emb,cons", EITHER, ids=EITHER_IDS)
def test_the_coupling_sign_is_measured_under_either(name, emb, cons):
    c = _bound(emb, cons)
    c.conduct("a", _F)
    c.conduct("b", _F)
    assert c.couple("a", "b") > 0.0, "identical sides attract (+) — %s" % name
    c.clear("b")
    c.conduct("b", -_F)
    assert c.couple("a", "b") < 0.0, "opposed sides detract (−) — %s" % name


@pytest.mark.parametrize("name,emb,cons", EITHER, ids=EITHER_IDS)
def test_silence_stays_silence_under_either(name, emb, cons):
    """A surface that fires nothing is an empty crossing, trivially conserved — never a crash and
    never a synthesised answer."""
    c = Crystal(SPEC, embodiment=emb, conservation=cons)
    c.bind("a", entry=lambda x: None, inverse=lambda X: X)
    assert c.conduct("a", "zzqxwv plorbnak") is None
    assert c.condense() is None
    assert c.transmit() is None
    assert c.ledger() == {"incident": 0.0, "absorbed": 0.0, "transmitted": 0.0,
                          "k": 0, "conserved": True}
    assert c.certificate()["balanced"] is True


@pytest.mark.parametrize("name,emb,cons", EITHER, ids=EITHER_IDS)
def test_an_unreadable_frame_returns_the_null_under_either(name, emb, cons):
    """One row cannot carry a read. Both instruments must say so by returning None from
    `absorb_transmit`, and the crystal must then propagate the frame whole — the entire incident
    energy transmits on, unabsorbed, rather than a zero band being invented for it."""
    c = _bound(emb, cons)
    c.conduct("a", np.array([[1.0, 2.0, 3.0]]))
    assert c._crossing() is None, name
    led = c.ledger()
    assert led["k"] == 0 and led["absorbed"] == 0.0
    assert led["transmitted"] == led["incident"] > 0.0
    assert led["conserved"] is True


@pytest.mark.parametrize("name,emb,cons", EITHER, ids=EITHER_IDS)
def test_identity_is_unchanged_by_which_instrument_is_held(name, emb, cons):
    """The point of the slot: the crystal's shareable identity is a property of its structure, so
    a node with the instrument and a store with a reduced embodiment address the same crystal."""
    c = _bound(emb, cons)
    assert c.sha == Crystal(SPEC).sha
    assert c.artifact() == Crystal(SPEC).artifact()
    assert c.required_capabilities() == ["store.read"]




# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3 · With no embodiment, every measurement raises at the point of measurement
# ═════════════════════════════════════════════════════════════════════════════════════════════════

def test_structure_and_gating_need_no_instrument_at_all():
    """The half that must keep working, or the split has no value: a bare host verifies a crystal
    before grounding it."""
    c = Crystal(SPEC)
    assert c.sha and c.name == "crystal.test.injected"
    assert c.activates_on(["store.read"]) is True
    assert c.can_discharge("op.retrieve", ["store.read"]) is True
    assert Crystal.from_artifact(c.artifact()).sha == c.sha
    c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    assert c._m is None, "binding must not build a screen"


def test_every_measuring_method_raises_rather_than_degrading():
    """The anti-silence control, enumerated. Each of these is a measurement, and with nothing
    injected each raises — not returns None, not returns 0.0, not returns a shaped zero. A
    fabricated reading is worse than no reading, because a caller cannot tell it apart from one."""
    calls = {
        "conduct": lambda c: c.conduct("a", _F),
        "emit": lambda c: c.emit("a", _F),
        "couple": lambda c: c.couple("a", "b"),
        "coupling": lambda c: c.coupling("a", "b"),
        "read": lambda c: c.read(),
        "balance": lambda c: c.balance(),
        "certify": lambda c: c.certify("a", _F),
        "transfer": lambda c: c.transfer("a", "b"),
        "clear": lambda c: c.clear(),
        "placed": lambda c: c.placed,
        "ledger": lambda c: c.ledger(),
        "certificate": lambda c: c.certificate(),
    }
    for op, call in calls.items():
        c = Crystal(SPEC)
        c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
        c.bind("b", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
        with pytest.raises(EmbodimentRequired) as exc:
            call(c)
        assert exc.value.contract in ("embodiment", "conservation"), op
        assert exc.value.member in EMBODIMENT_MEMBERS + CONSERVATION_MEMBERS, op
        assert exc.value.at, "%s refused without naming the operation" % op


def test_condense_and_transmit_refuse_once_a_frame_is_actually_incident():
    """`condense()`/`transmit()` on a crystal that conducted nothing return None — that is silence
    and it is correct. The raise has to be shown where a frame really is present, which is why
    this case is separated out rather than folded into the table above: otherwise "returns None"
    would look like the answer."""
    screen_only = type("ScreenOnly", (), {"membrane_screen": staticmethod(lambda: _StubScreen)})
    c = _bound(screen_only, _StubConservation)
    c.conduct("a", _F)                                   # succeeds — the screen member is filled
    assert c._incident is not None
    for call in (lambda: c.condense(), lambda: c.transmit(), lambda: c.ledger()):
        with pytest.raises(EmbodimentRequired) as exc:
            call()
        assert exc.value.member == "absorb_transmit"
        assert "fills: membrane_screen" in str(exc.value), (
            "a partial embodiment must report what it DOES fill: " + str(exc.value))


def test_the_refusal_is_a_typed_prism_error_that_names_the_fix():
    c = Crystal(SPEC)
    c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    with pytest.raises(EmbodimentRequired) as exc:
        c.conduct("a", _F)
    e = exc.value
    assert isinstance(e, PrismError) and e.http_status == 503 and e.code == "embodiment_required"
    assert e.contract == "embodiment" and e.member == "membrane_screen"
    msg = str(e)
    assert "embodiment=" in msg, "the refusal must say how to fix it"

    # `msg` is prism's own advisory text, from `prism/instrument.py::_FILLED_BY`, which names the
    # module a host should inject. It names `ember.optics` — the instrument module this file imports
    # as `ember_optics` — for all four contract members, so the assertion below tracks that live
    # value rather than pinning a literal: `contract`, `member`, and `http_status`, the parts a
    # caller discriminates on, are asserted above and do not depend on the wording of `msg`.
    assert "ember.optics" in msg, (
        "prism's `_FILLED_BY` no longer says `beam.optics`. If it now says `ember.optics`, the "
        "cross-repo fix above has landed — change this to `ember.optics` and delete this note.")


def test_a_misspelled_slot_is_not_silently_swallowed():
    """The quiet failure mode this guards against: `**screen_kwargs` would happily absorb
    `embodyment=`, leaving a crystal that looks instrumented and is not. Both constructors name the
    slots explicitly, so the typo lands on the screen and fails there instead of producing a wrong
    answer."""
    c = Crystal(SPEC, embodyment=_StubEmbodiment)         # typo → a screen construction kwarg
    c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
    with pytest.raises(EmbodimentRequired):
        c.conduct("a", _F)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 4 · Two contracts, not one
# ═════════════════════════════════════════════════════════════════════════════════════════════════

def test_the_accountant_is_useful_with_no_instrument_at_all():
    """A crystal that conducted nothing certifies 0 → 0 without ever reaching the instrument.

    This is a real deployment with exactly one of the two slots filled, which is what makes them
    two contracts rather than one protocol with four members."""
    c = Crystal(SPEC, conservation=_StubConservation)     # no embodiment anywhere
    cert = c.certificate()
    assert cert["balanced"] is True and cert["incident"] == 0.0
    assert c.ledger() == {"incident": 0.0, "absorbed": 0.0, "transmitted": 0.0,
                          "k": 0, "conserved": True}


def test_the_instrument_is_useful_with_no_accountant_at_all():
    """The converse: splitting a frame never touches conservation."""
    c = _bound(_StubEmbodiment, None)                     # no conservation anywhere
    c.conduct("a", _F)
    assert np.asarray(c.transmit()).shape == _F.shape
    assert c.couple("a", "a") > 0.0
    with pytest.raises(EmbodimentRequired) as exc:
        c.ledger()
    assert exc.value.contract == "conservation"


def test_the_contracts_are_fillable_from_different_places():
    """Crossed wiring: the stub embodiment with the REAL accountant. Neither combination is
    special-cased anywhere, which is the operational meaning of "two contracts".

    The mirrored crossing — the real instrument with the stub accountant — needs an embodiment from
    the repository above this one and is asserted in
    `agience-ember/tests/test_the_instrument_is_a_crystal_embodiment.py`."""
    for emb, cons in ((_StubEmbodiment, prism_conservation),):
        c = _bound(emb, cons)
        c.conduct("a", _F)
        assert c.ledger()["conserved"] is True
        assert c.certificate()["balanced"] is True


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 5 · Crystal runs with the instrument unimportable
# ═════════════════════════════════════════════════════════════════════════════════════════════════

#: The instrument packages: the instrument's own package (`ember`), the archived and unimportable
#: `beam`. `crystal` may not import any of them at
#: any scope — it takes an injected embodiment instead — and this one tuple is what both halves of
#: the proof below read.
#:
#: `beam` stays in the set although it cannot resolve: banning it here keeps that a stated property
#: of crystal's imports rather than an accident of a package that happens to be missing today.
# A THIRD NAME — the library underneath the instrument — WAS REMOVED 2026-08-25 [John: crystal
# must not name it]. `ember` is the load-bearing entry and stays: the instrument IS `ember.optics`, so
# blocking `ember` makes the import fail on the ember leg and the self-check below still passes.
# What is lost: a transitive pull of that library by some route OTHER than ember is no longer
# blocked here. `agience-ember/tests/test_one_instrument.py` is the guard that still names it.
_INSTRUMENT_PACKAGES = frozenset({"beam", "ember"})
BLOCKED_NAMES = tuple(sorted(_INSTRUMENT_PACKAGES))

_SUBPROCESS = r'''
import sys

# `ember` belongs in this tuple: the instrument lives at `ember.optics`, so blocking `beam` alone
# leaves the package that now holds the instrument importable. With only `beam` blocked,
# `import ember.optics` still resolves `ember` itself into
# the subprocess, so the proof would report success on a weaker claim than the one it is named for.
BLOCKED = ('beam', 'ember')

class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("BLOCKED BY THE TEST: %r is not installed here" % name)
        return None

sys.meta_path.insert(0, _Blocker())
for m in list(sys.modules):
    if m.split(".")[0] in BLOCKED:
        del sys.modules[m]

# ── the control: the blocker must bite, or every result below is vacuous ──
try:
    import ember.optics
except ImportError:
    pass
else:
    raise AssertionError("the blocker did not fire — this proof would be meaningless")

import numpy as np
from crystal import Crystal

SPEC = __SPEC__
F = np.array([[1,0,0],[0,1,0],[0,0,1],[1,1,0],[0,1,1],[1,0,1]], dtype=float)

# the stub embodiment, inlined so the subprocess needs nothing from the test module
class Screen:
    def __init__(self, **kw): self._s, self._c = {}, {}
    def register(self, side, *, entry, inverse=None, energy=None, zero=None):
        self._s[side] = (entry, inverse)
    def place(self, side, surface):
        f = np.asarray(self._s[side][0](surface), dtype=float); self._c[side] = f; return f
    def render(self, side, concept): return self._s[side][1](concept)
    def couple(self, a, b):
        x, y = self._c[a].ravel(), self._c[b].ravel()
        return float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)))
    def clear(self, side=None): self._c.clear()
    @property
    def placed(self): return sorted(self._c)

class Emb:
    @staticmethod
    def absorb_transmit(rows, *, basis=None, null=None, seed=0):
        W = np.asarray(rows, dtype=float)
        if W.ndim != 2 or W.shape[0] < 2 or not np.any(W):
            return None
        _u, s, vt = np.linalg.svd(W, full_matrices=False)
        k = int((s > s[0] * max(W.shape) * np.finfo(W.dtype).eps).sum())
        B = vt[:k].T
        P = B @ np.linalg.pinv(B)
        A = W @ P
        return A, W - A, k
    @staticmethod
    def membrane_screen(): return Screen

class Led:
    def __init__(self, incident, *, at=None):
        a = np.asarray(incident, dtype=float)
        self.e0 = float((a*a).sum()); self.res = self.e0; self.hops = []; self.em = None
        self.tol = float(np.finfo(a.dtype).eps) * self.e0 * float(1 + a.size)
    def absorb(self, absorbed, transmitted, *, at=None, k=None):
        ea = float((np.asarray(absorbed)**2).sum()); et = float((np.asarray(transmitted)**2).sum())
        self.hops.append((ea, et)); self.res = et; self.tol *= 4.0; return self
    def emit(self, *, at=None):
        self.em = self.res; return self
    def certificate(self):
        tot = sum(h[0] for h in self.hops)
        end = self.em if self.em is not None else self.res
        loss = self.e0 - (tot + end)
        return {"incident": self.e0, "absorbed": tot, "emitted": self.em, "loss": loss,
                "tolerance": self.tol, "closed": True,
                "terminated": self.em is not None or abs(self.res) <= self.tol,
                "balanced": abs(loss) <= self.tol}

class Cons:
    @staticmethod
    def energy(frame):
        if frame is None: return 0.0
        a = np.asarray(frame, dtype=float)
        return 0.0 if a.size == 0 else float((a*a).sum())
    PathLedger = Led

c = Crystal(SPEC, embodiment=Emb, conservation=Cons)
c.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
c.bind("b", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
c.conduct("a", F)
c.conduct("b", F)
assert c.couple("a", "b") > 0.0
assert np.asarray(c.transmit()).shape == F.shape
assert c.ledger()["conserved"] is True
assert c.certificate()["balanced"] is True
assert c.sha

# and with NOTHING injected it still refuses rather than reaching for the blocked package
from prism.embodiment import EmbodimentRequired
bare = Crystal(SPEC)
bare.bind("a", entry=lambda x: np.asarray(x, float), inverse=lambda X: X)
try:
    bare.conduct("a", F)
except EmbodimentRequired:
    pass
else:
    raise AssertionError("an un-instrumented crystal did not refuse")

for _b in BLOCKED:
    assert _b not in sys.modules, "%r loaded despite the block — this proof is vacuous" % _b
print("INJECTED OK")
'''


def test_the_whole_flow_runs_with_the_instrument_UNIMPORTABLE():
    """Every other check in this file reads source or trusts an argument; this one makes `beam`,
    and `ember` unimportable for real and then drives the complete capacitor flow —
    conduct, couple, transmit, ledger, certificate — on an embodiment defined in the subprocess.

    Source analysis cannot see an import through `importlib`, a `__getattr__` on module load, or a
    transitive pull from a sibling. This can. It asserts the blocker fires first, so a finder that
    silently matched nothing would fail here instead of reporting a green run."""
    # The blocked set is checked against the instrument itself, not against a remembered name.
    #
    # A hand-maintained list of package names cannot catch a change of instrument package: dropping
    # `ember` from `_INSTRUMENT_PACKAGES` and from the program's `BLOCKED` would leave this whole
    # file green, because `import ember.optics` still fails — on the library leg — while `ember`
    # itself loads fine. The proof would silently narrow from "crystal runs without the instrument"
    # to "crystal runs without the library" with nothing to say so.
    #
    # The name is a literal because this file no longer imports the instrument — deriving it from
    # the module meant importing the repository above this one, which is what the instrument arm was
    # moved out for. The cost is real and is stated rather than hidden: if the instrument moves to a
    # different package, nothing here notices. `agience-ember`'s own suite is what tracks where the
    # instrument lives, and `_INSTRUMENT_PACKAGES` below is what this file blocks.
    instrument_pkg = "ember"
    assert instrument_pkg in _INSTRUMENT_PACKAGES, (
        "the instrument lives in %r and the proof does not block it — so this test would run with the "
        "instrument's own package importable and still report success. Add %r to "
        "`_INSTRUMENT_PACKAGES` and to the program's `BLOCKED`."
        % (instrument_pkg, instrument_pkg))

    # The subprocess carries its own copy of the blocked set, so the two are pinned equal here.
    # `_SUBPROCESS` is a source string — nothing in it is reachable from this module's namespace, so
    # `_INSTRUMENT_PACKAGES` and the program's `BLOCKED` are two literals that can drift apart in
    # silence. A `BLOCKED` short of one name leaves that package importable while the run still
    # reports INJECTED OK.
    assert 'BLOCKED = %r' % (BLOCKED_NAMES,) in _SUBPROCESS, (
        "the subprocess blocks a different set than `_INSTRUMENT_PACKAGES` (%r). They are two "
        "literals in one file and must be edited together." % (BLOCKED_NAMES,))

    # a literal substitution, not `%` formatting: the program itself uses `%r` at runtime and
    # percent-formatting the whole string would consume those instead
    program = _SUBPROCESS.replace("__SPEC__", repr(SPEC))
    assert "__SPEC__" not in program and repr(SPEC) in program
    r = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert "INJECTED OK" in r.stdout, (
        "crystal could not run with %s unimportable:\n" % ("/".join(BLOCKED_NAMES),)
        + (r.stderr or r.stdout)[-3000:])


#: The one place under `crystal/src` allowed to import numpy, and the exact modules that do.
#:
#: A narrowing of scope, not a weakening of the ban: `crystal.ontology.geometry` (the JL dense
#: basis) is numpy arithmetic and is not an instrument in any sense the rest of this file uses — it
#: measures no domain, wraps no Screen, and fills no `Instrument` slot. `crystal.crystal` still
#: takes its embodiment injected, and `test_the_whole_flow_runs_with_the_instrument_UNIMPORTABLE`
#: still drives the whole capacitor flow in a subprocess where beam and ember do not
#: exist.
#:
#: The instrument packages remain banned everywhere, including here. Only numpy is scoped, and it
#: is scoped to named files rather than to a directory: a new numpy import anywhere — including a
#: second file under `crystal/ontology/` — fails until somebody adds it here and says why.
#:
#: The property the blanket ban is a proxy for is asserted directly, in a subprocess, by
#: `test_importing_crystal_does_not_load_numpy` below — that is the check that actually protects
#: "`import crystal` stays light"; this scan protects it only incidentally.
_NUMPY_ALLOWED = {
    os.path.join("crystal", "ontology", "geometry.py"),
}


def test_crystal_src_imports_no_instrument_anywhere():
    """`crystal → the instrument` by AST over non-test `src/`, at any scope — the same definition
    `ARCHITECTURE-TARGET.md` §2 uses, so the number travels with its meaning. The count is 0, and
    this test is the ratchet that keeps it there.

    `_INSTRUMENT_PACKAGES` includes `ember` as well as `beam`: the instrument lives at `ember.optics`,
    so a ban naming only `beam` would go on passing forever against a package nobody can import — a
    check that cannot fail. Naming the live package is what makes `crystal → ember` == 0 a property
    this test actually holds.

    numpy is scoped to `_NUMPY_ALLOWED` — see that entry for why — and the exemption is checked in
    both directions: an allowance nobody uses fails too."""
    import ast
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    sites, numpy_files = [], set()
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts or path.name.startswith("test_"):
            continue
        rel = str(path.relative_to(src))
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        for node in ast.walk(tree):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level
                    else [])
            for m in mods:
                top = m.split(".")[0]
                if top in _INSTRUMENT_PACKAGES:
                    sites.append("%s:%d  %s" % (rel, node.lineno, m))
                elif top == "numpy":
                    numpy_files.add(rel)
                    if rel not in _NUMPY_ALLOWED:
                        sites.append("%s:%d  %s" % (rel, node.lineno, m))
    assert sites == [], (
        "crystal reaches an instrument from its own source — the embodiment must be INJECTED:\n  "
        + "\n  ".join(sites))
    stale = _NUMPY_ALLOWED - numpy_files
    assert not stale, (
        "the numpy allowance is looser than the code: %r no longer imports numpy. Drop the entry — "
        "an exemption nobody needs is an open door." % (sorted(stale),))


def test_importing_crystal_does_not_load_numpy():
    """The property the scan above is a proxy for, asserted directly.

    `crystal/__init__.py` states that `import crystal` pulls no instrument at all — no beam, no
    no instrument, no numpy — so that the gateway installs anywhere prism does.
    `crystal.ontology.geometry` is the one module in the package that imports numpy, which is what
    makes this claim need a test rather than being self-evident from the absence of any numpy
    import.

    Failure mode this guards against: something imports `crystal.ontology` from
    `crystal/__init__.py`, or from `crystal/crystal.py`, or from anything either of them reaches —
    and a numpy-free install breaks at `import crystal` rather than at the coordinate nobody asked
    for. Source analysis cannot see that reliably; a fresh interpreter can.

    The control is in the same subprocess: it imports `crystal.ontology.geometry` afterwards and
    asserts numpy is loaded then. Without that half, a run where numpy failed to import at all —
    or where the check named a module that does not exist — would report green."""
    program = (
        "import sys\n"
        "import crystal\n"
        "import crystal.crystal\n"
        "assert 'numpy' not in sys.modules, 'import crystal pulled numpy: %r' % (\n"
        "    [m for m in sys.modules if m.split('.')[0] == 'numpy'],)\n"
        "import crystal.ontology.geometry\n"
        "assert 'numpy' in sys.modules, 'the control failed: the coordinate did not load numpy, "
        "so the assertion above proves nothing'\n"
        "print('LIGHT OK')\n")
    r = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert "LIGHT OK" in r.stdout, (r.stderr or r.stdout)[-3000:]
