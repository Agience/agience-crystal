"""The crystal as a live object — the gauge helper (facet + tekton = transmit + absorb).

A crystal is pure structure (the `crystal_model` contract: facets + tektons + organons, an inert
content-addressed artifact). This module adds the energized form: a thin object that grounds that
structure on a screen (the two-way membrane where sides meet, not the measurement aperture) and
drives the capacitor flow.

    facet (a view)      ─conduct→  place on the screen          (passes signals — transmission in)
    tekton (a tool)     ─condense→ resolution() above the floor  (handles condensed signals; the absorb)
    organon (a capability, on the lattice) ─discharge→ gated by activates_on(prism)  (the tekton passes
                                                                  to it; the lattice holds it)
    facet (a view)      ─emit→     render (the inverse) back out  (passes signals — transmission out)

The three parts, precisely: a **facet is a view** — it passes signals (a conduit) and/or displays the
signal the crystal produces (a web UI is a facet whose far side is a human); either way it never
transforms the band. A **tekton is a tool** — it handles condensed signals and sometimes passes them to
an organon; it absorbs its matched band, removing it from propagation (a sink). An **organon is a
capability attached to the lattice** — not code this object owns; `can_discharge`/`activates_on` gate
the reach to it (the prism junction), and the host/persona invokes it on the lattice.

Each facet registers on the screen as a `Lens` (a side): its `entry` (surface → the screen's shared
coordinates) and `inverse` (back out), plus that side's own laws. The crystal is the helper, not the
gauge — a gauge is emergent (`carrier + information`, a fractal 0→0 band that is measured, never
registered). The crystal only conducts (facet) and condenses (tekton); the coupling between two sides
is read at the screen (`couple`), never declared. See
`agience-pharos/genesis/CRYSTALS-GAUGES-AND-THE-MEMBRANE.md` and `agience-pharos/genesis/SCREEN-AND-GAUGES.md`.

No domain logic lives here. No facet, tekton, gauge, or operator is baked in. A persona (chorus)
defines a crystal by supplying the spec and binding its own `entry`/`inverse` conversions onto the
named facets (persona-local code). Nothing WordNet-, price-, or space-specific enters this base — a
new domain is a new facet in a persona, not a change here.

Two homes, deliberately: the contract (validate/hash/activates_on) is stdlib-only in prism, so a bare
host verifies a crystal before grounding it; the instrument is only needed to energize one.

The instrument is injected, never imported: a direct import would make crystal undeployable anywhere
the instrument's package is not installed, and would pull ember's AGPL licence (the aperture
wrapper is the copyleft hook) into what is otherwise a stdlib-light module. The embodiment arrives as
a constructor argument checked against `prism.embodiment`'s protocols, the same shape `prism.reach`
already proves with `keyring=` and `lightcone=`: one keyword per contract, duck-typed, resolved by
the host at assembly.

    from ember import optics                                # the host imports these, crystal never does
    from prism import conservation                          # arithmetic, not the domain-specific instrument
    c = Crystal(spec, embodiment=optics, conservation=conservation)

Two slots, because they are two contracts (see `prism/embodiment.py` for the discriminators between
them): the instrument is domain-specific (the aperture and beacon legitimately read a
collapsed axis differently), while the accountant is domain-free arithmetic (numpy; `‖X‖²` cannot
diverge between domains). Neither implies the other here — `certificate()` on a crystal that
conducted nothing needs the accountant and never touches the instrument; `condense()` needs the
instrument and never touches the accountant.

This buys one thing: the same crystal runs on a full node and on a constrained store. The package
itself needs neither the aperture nor beacon; a deployment supplies whichever its embodiment requires.

With nothing injected, measurement raises `EmbodimentRequired`, naming the contract, the member, and
the operation, rather than degrading to a zero, a default basis, or a fabricated reading. Structure
and identity are unaffected: `import crystal`, validate, hash, `activates_on`, and `discharge` all
work with no instrument anywhere — exactly the split that lets a bare host verify a crystal before
grounding it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from prism.embodiment import require as _require

from crystal.crystal_model import (
    activates_on as _activates_on,
    capability_reach as _capability_reach,
    crystal_artifact,
    crystal_sha,
    required_capabilities as _required_capabilities,
    validate,
    verify,
)

# A binding is a persona's own surface<->concept conversions for one facet. It is NOT part of the
# shareable spec (structure ships; conversions are persona-local code) — the waveform-provenance
# boundary: the facet declares only its ordered conduit; the conversion that meets the shared basis
# rides with the persona.
Entry = Callable[[Any], Any]
Inverse = Callable[[Any], Any]


def _principal_of(energy: Any) -> Optional[str]:
    """The principal an energy carries — read off its own provenance, never passed alongside it.

    `created_by` is the provenance field throughout (`mantle/db/access.py`: "Authorship is
    provenance (`created_by`), which grants no access"). Provenance identifies who, not whether —
    authorization is the grant's business, separately. This only answers "whose energy is this?" so
    an authority can look up the grant.

    Duck-typed over a mapping or an object. Returns None when the energy cannot say where it came
    from; an energy without provenance cannot be granted anything, and it is the authority's business
    to decide that, not crystal's to guess.
    """
    if energy is None:
        return None
    if isinstance(energy, dict):
        got = energy.get("created_by")
    else:
        got = getattr(energy, "created_by", None)
    return got if isinstance(got, str) and got else None


class Crystal:
    """The live crystal: its structure validated by the prism contract, its flow driven on a screen.

    Construct with a spec dict (the `crystal_model` shape). Bind each facet's conversions with
    `bind(...)`, then drive the capacitor flow: `conduct` a surface in, `condense` the absorption,
    `emit` the residual out, `couple` two sides to READ their coupling. Structure/identity delegate to
    the prism contract; the flow delegates to the injected embodiment. Nothing else.

    `embodiment` fills `prism.embodiment.Embodiment` (`absorb_transmit`, `membrane_screen`) and
    `conservation` fills `Conservation` (`energy`, `PathLedger`). Both keyword-only, both defaulting
    to None — an un-injected crystal is a legitimate object that verifies, hashes and gates, and
    cannot measure. Remaining keywords are forwarded to the screen the embodiment produces.
    """

    __slots__ = ("_spec", "_facets", "_tektons", "_organons", "_bound", "_m", "_m_kwargs",
                 "_incident", "_split", "_embodiment", "_conservation")

    def __init__(self, spec: Dict[str, Any], *, embodiment: Any = None,
                 conservation: Any = None, **screen_kwargs: Any) -> None:
        problems = validate(spec)
        if problems:
            # An invalid crystal never half-loads: construction raises before any partial state
            # exists (the contract's own rule).
            raise ValueError("invalid crystal: " + "; ".join(problems))
        self._spec: Dict[str, Any] = dict(spec)
        self._facets: Dict[str, Dict[str, Any]] = {f["name"]: f for f in self._spec.get("facets", [])}
        self._tektons: Dict[str, Dict[str, Any]] = {t["name"]: t for t in self._spec.get("tektons", [])}
        self._organons: Dict[str, Dict[str, Any]] = {o["name"]: o for o in (self._spec.get("organons") or [])}
        self._bound: Dict[str, Dict[str, Any]] = {}  # facet name -> {entry, inverse, energy, zero}
        # Only forwarded to Screen() if the caller supplied them — never re-declare the aperture's
        # derived defaults (far/null/seed) here (no arbitrary caps).
        self._m_kwargs = screen_kwargs
        self._m = None  # the screen is created lazily on first flow use (keeps import stdlib-light)
        self._incident = None   # the beam this crossing is accounting for
        self._split = None      # (absorbed, transmitted, k), computed once per conduct
        # The two slots. Stored as handed over — never probed, never validated here. Whether an
        # embodiment can do what's being asked is answered at the ask (`_require`), because a
        # constructor-time completeness check would reject a partial embodiment that is perfectly
        # able to do what this crystal actually needs.
        self._embodiment = embodiment
        self._conservation = conservation

    # ── structure / identity (delegate to the prism contract — the ONE home) ──────────────────────

    @property
    def name(self) -> str:
        return self._spec["name"]

    @property
    def sha(self) -> str:
        """Content-address of the structure (everything but the sha field) — the stable cross-host ref."""
        return crystal_sha(self._spec)

    @property
    def spec(self) -> Dict[str, Any]:
        return dict(self._spec)  # a copy — the live object never lets its structure be mutated in place

    @property
    def facets(self) -> List[Dict[str, Any]]:
        return list(self._spec.get("facets", []))

    @property
    def tektons(self) -> List[Dict[str, Any]]:
        return list(self._spec.get("tektons", []))

    @property
    def organons(self) -> List[Dict[str, Any]]:
        return list(self._spec.get("organons") or [])

    def facet(self, name: str) -> Dict[str, Any]:
        """The facet's declared conduit metadata (name/direction/content_type?)."""
        return dict(self._require_facet(name))

    def artifact(self) -> Dict[str, Any]:
        """The shareable store artifact — validated and sha-stamped, so tampering does not verify
        (structure only; the bound conversions and the screen charge never ship)."""
        return crystal_artifact(self._spec)

    def required_capabilities(self) -> List[str]:
        return _required_capabilities(self._spec)

    def activates_on(self, prism_capabilities: Optional[List[str]]) -> bool:
        """The bidirectional prism junction: does a prism advertising these capabilities ground the
        whole crystal? (every organon's `requires` a subset of the prism's set)."""
        return _activates_on(self._spec, prism_capabilities)

    def can_discharge(self, organon: str, prism_capabilities: Optional[List[str]]) -> bool:
        """Per-organon discharge gate: this one organon's `requires` a subset of the prism's set. An
        organon is a real-world / observer-interface capability — a hardware or environmental interface
        (sensor/actuator, external network, filesystem, install), not pure computation and not code this
        object owns. That is why the gate exists: the `requires` names a physical capability a prism
        must advertise. Pure-compute tekton tools need no organon and no gate; many tektons declare
        none. The base only gates the reach; the host/persona invokes the `op.*` at the world boundary."""
        o = self._organons.get(organon)
        if o is None:
            raise KeyError("no such organon on %r: %r" % (self.name, organon))
        return set(o.get("requires") or []) <= set(prism_capabilities or [])

    def discharge(self, organon: str, prism: Any, *,
                  energy: Any = None, authority: Any = None) -> Dict[str, Any]:
        """Light an organon on a prism (its environment). Gates the organon's `requires` against the
        prism's measured capabilities (`prism.advertises()`); if the environment affords them,
        injects the required capability handles (`prism.capability(name)` — the organon's light) and
        returns them; otherwise a typed dormant result with the gap named (never a crash, never a
        silent no-op). This is the runtime half of the prism junction (`can_discharge` is the gate
        alone).

        No domain logic: the base only wires abstract capabilities to the prism's concrete handles.
        The organon body (persona code) runs with the returned handles; how each capability works is
        the prism's business (httpx here, a cellular modem there) — the organon never names it. So the
        same crystal, discharged on different prisms, lights up different organons.

        Two independent gates, and they stay distinguishable: the hardware gate (does this
        environment afford the capability?) and the grant gate (is this energy granted access to
        actuate?). "You have no thermometer" and "you may not touch the thermometer" call for
        opposite responses, so every dormant result carries a `reason`.

        `prism` is duck-typed: `advertises() -> set[str]` (measured, peer-local) and
        `capability(name) -> handle`.

        `authority` is duck-typed the same way — `may_discharge(principal, organon, crystal_sha) ->
        bool`. Crystal holds no grant logic; it only asks, because the grant machinery is mantle's
        (`mantle/db/access.py`) and crystal does not depend on mantle, so crystal cannot
        import it.

        `energy` is the signal being discharged; its principal is read from its own provenance
        (`created_by` — authorship is provenance, per `mantle/db/access.py`), never passed
        alongside it. An energy that cannot say where it came from yields `principal=None`, and
        deciding what to do with that is the authority's business.

        With `authority=None` the grant gate is not evaluated. That is not the same as "authorized":
        a lit result carries `authorized: True | None`, where `None` means the gate was never wired.

        Returns `{"status":"lit","organon",...,"handles":{cap:handle},"authorized":True|None}` or
        `{"status":"dormant","organon",...,"reason":"no_hardware"|"no_grant", ...}`.
        """
        o = self._organons.get(organon)
        if o is None:
            raise KeyError("no such organon on %r: %r" % (self.name, organon))
        requires = list(o.get("requires") or [])
        advertised = sorted(prism.advertises())
        # Gate 1 — hardware. The gap is measured, not just named. `capability_reach` reports, per
        # unmet requirement, the nearest thing this prism actually affords — so a dormant organon says
        # "you want sensor.temperature, this prism affords sensor.capture (1 hop)" instead of only
        # naming a string that was absent. A reach gap, not a spelling gap. Nearness is reported,
        # never honoured: matching is propagation, authorization is the grant (see
        # `agience-pharos/genesis/PRISM-AND-CAPABILITIES.md`).
        reach = [m for m in _capability_reach(requires, advertised) if m["basis"] != "exact"]
        if reach:
            return {"status": "dormant", "organon": organon, "reason": "no_hardware",
                    "missing": sorted(m["required"] for m in reach),
                    "reach": reach}

        # Gate 2 — grant. Owning a capability name authorizes nothing; the energy must be granted.
        # Closed by default: with no authority wired there is nobody to ask, and "nobody asked" does
        # not mean "permitted" — that is the check that cannot fail. An organon that touches the world
        # stays dark until something can vouch for the energy.
        if authority is None:
            return {"status": "dormant", "organon": organon, "reason": "no_authority",
                    "missing": [], "reach": []}
        principal = _principal_of(energy)
        # The crystal's own content address goes with the question: a grant is minted against the
        # crystal (that is what a person grants) and propagates to the organons it contains, so the
        # authority needs to know which crystal is asking, not only which organon.
        if not authority.may_discharge(principal, organon, self.sha):
            return {"status": "dormant", "organon": organon, "reason": "no_grant",
                    "principal": principal, "missing": [], "reach": []}

        return {"status": "lit", "organon": organon, "authorized": True,
                "handles": {r: prism.capability(r) for r in requires}}

    # ── binding: a persona's own conversions onto a named facet (persona-local, never in the spec) ──

    def bind(self, facet: str, *, entry: Optional[Entry] = None, inverse: Optional[Inverse] = None,
             energy: Optional[Callable] = None, zero: Optional[Callable] = None) -> "Crystal":
        """Wire a facet's surface<->basis conversions. Direction-checked against the declared conduit:
        an `in`/`both` facet needs `entry` (surface→shared basis); an `out`/`both` facet needs
        `inverse` (basis→surface). Conversions are persona code — they are NOT written into the spec,
        so the crystal's sha (its shareable identity) is unchanged by binding. Returns self to chain."""
        f = self._require_facet(facet)
        direction = f.get("direction")
        if direction in ("in", "both") and entry is None:
            raise ValueError("facet %r conducts in (direction=%r) — an `entry` conversion is required"
                             % (facet, direction))
        if direction in ("out", "both") and inverse is None:
            raise ValueError("facet %r conducts out (direction=%r) — an `inverse` conversion is required"
                             % (facet, direction))
        self._bound[facet] = {"entry": entry, "inverse": inverse, "energy": energy, "zero": zero}
        self._register(facet)
        return self

    # ── the capacitor flow (delegate to the screen — the ONE aperture) ──────────────────────────

    def conduct(self, facet: str, surface: Any) -> Any:
        """Transmission in: place a surface signal on the facet's conduit. Requires the facet to
        conduct in and to be bound. Runs the facet's `entry` and puts the band on the screen.

        The entry's output is the beam — an ordered `(T, F)` frame on the screen's shared
        coordinates. It is kept as the incident frame so the crossing can be accounted for; a
        crossing whose incident is unknown cannot report what it lost."""
        self._require_direction(facet, ("in", "both"), "conduct a surface in")
        self._require_bound(facet)
        # The incident beam is what the facet's entry produces — that is the definition of a facet
        # (surface -> the screen's shared coordinates), so it is read from the binding rather than
        # inferred from whatever the screen happens to hand back. The band is still placed on the
        # screen, because the lens/coupling machinery is what `couple`/`certify` read.
        self._incident = self._bound[facet]["entry"](surface)
        self._split = None                          # a new signal invalidates the last crossing

        # Silence is a state, not an error. A surface that fires nothing yields no frame — exactly
        # the input the whole design says must produce the computed null for free, not a crash.
        # Nothing conducted means an empty crossing: incident 0, absorbed 0, transmitted 0, trivially
        # conserved. The signal simply was not there.
        if self._incident is None:
            return None
        return self._screen().place(facet, surface)

    # ── the membrane ───────────────────────────────────────────────────────────────────────────
    # The split is the projection membrane (`ember.optics.absorb_transmit`): the band is the
    # projection onto the resolved correlation modes, and the projector is orthogonal, so
    # conservation is exact by construction rather than by assertion.
    #
    # At a crossing there is no loss: an orthogonal split cannot lose energy, so a
    # `ledger()["conserved"]` of False means the membrane itself is broken. Loss belongs between
    # crossings — propagation attenuates by `prism.law.attenuate` over the distance travelled — and
    # is accounted there, against a measured distance, not folded in here as slack.
    def _crossing(self):
        """`(absorbed, transmitted, k)` for the incident beam — computed once per conduct."""
        if self._split is not None:
            return self._split
        if self._incident is None:
            return None
        absorb_transmit = _require(self._embodiment, "absorb_transmit", contract="embodiment",
                                   at="splitting the incident frame at the membrane "
                                      "(condense / transmit / ledger / certificate)")
        self._split = absorb_transmit(self._incident)
        return self._split

    def condense(self) -> Any:
        """Absorption: the tekton condenses the matched band into its settled resolution (projection
        onto the resolved basis, above the noise floor) — and in doing so removes that band from
        propagation. A tekton is a sink for the band it is tuned to: what it absorbs is consumed
        (condensed into a typed artifact), gone from the beam, so it does not keep travelling and get
        re-absorbed downstream. Only the residual — the transmission (`incident − absorbed`, see
        `transmit`) — propagates on. `None` is silence: nothing resolved (the null, for free); silence
        stays silence, never a synthesised answer. Conservation: ‖incident‖² = ‖absorbed‖² +
        ‖transmitted‖², and `ledger()` reports it rather than asserting it."""
        r = self._crossing()
        if r is None:
            return None
        absorbed, _t, k = r
        return absorbed if k else None          # k == 0 is silence: nothing coupled here

    def transmit(self, facet_from: str = None, facet_to: str = None) -> Any:
        """Transmission: the residual that was not absorbed at the crossing — what keeps propagating,
        as a frame ready for the next boundary. The complement of `condense`: absorption removes its
        band from the beam; this is everything the tekton did not sink, still travelling on.

        The facet arguments are accepted for call-site compatibility and ignored: the residual is a
        property of the crossing, not of a pair of sides — a crystal with one facet still has its own
        residual to report."""
        r = self._crossing()
        return None if r is None else r[1]

    def ledger(self) -> Optional[Dict[str, float]]:
        """The full account of this crossing — every joule in, and where it went.

        `{"incident", "absorbed", "transmitted", "k", "conserved"}`. `conserved` is checked, not
        claimed: an orthogonal split cannot lose energy, so False means the membrane is broken and
        the caller must not treat the absorbed band as the work.

        The tolerance is `prism.conservation`'s derived band: the forward-error bound of the float
        summation actually performed (`ε·E₀·(terms + elements)`), not a fixed constant. A fixed
        constant would be either too tight for a large frame, where honest rounding exceeds it, or
        too loose for a small one, where a real leak hides under it — a conservation check whose
        threshold is fitted proves whatever the threshold was chosen to prove."""
        # Required up front, including for the empty crossing that computes nothing. This read is the
        # conservation claim, and reporting `conserved: True` with no accountant behind it would be
        # exactly the check that cannot fail.
        PathLedger = _require(self._conservation, "PathLedger", contract="conservation",
                              at="the crossing ledger")
        energy = _require(self._conservation, "energy", contract="conservation",
                          at="the crossing ledger")

        if self._incident is None:
            # The empty crossing: nothing was conducted, so nothing was absorbed and nothing
            # propagates. Reported rather than withheld — a caller must be able to tell "silence"
            # from "not measured", and both are legitimate answers.
            return {"incident": 0.0, "absorbed": 0.0, "transmitted": 0.0, "k": 0, "conserved": True}
        r = self._crossing()
        if r is None:
            # A frame that exists but cannot carry a read: the band was not this tekton's to take,
            # so ALL of it propagates on, intact and unabsorbed.
            inc = energy(self._incident)
            return {"incident": inc, "absorbed": 0.0, "transmitted": inc, "k": 0, "conserved": True}
        absorbed, transmitted, k = r
        led = PathLedger(self._incident, at=self.name)
        led.absorb(absorbed, transmitted, at=self.name, k=k)
        cert = led.certificate()
        return {"incident": cert["incident"], "absorbed": cert["absorbed"],
                "transmitted": energy(transmitted), "k": int(k), "conserved": cert["closed"]}

    def certificate(self) -> Dict[str, Any]:
        """The 0 → 1 → 0 certificate for this crossing, as a complete path.

        `ledger()` says this membrane's arithmetic is exact. That is the local claim, and a system
        can pass it at every element and still leak — see `prism.conservation`. This is the global
        one: the crystal's residual is not dropped, it is returned by `transmit()` for the next
        boundary, so the path closes by emission and `balanced` is a claim about the whole crossing
        rather than about one subtraction.

        A crystal that is the end of a path (nothing consumes its `transmit()`) is exactly the case
        `terminated` exists to catch — but the crystal cannot know that from here, so it certifies
        what it can guarantee: it handed the residual on. Whoever drops it owns the loss.

        Needs the accountant, and, on an empty path, no instrument at all — a crystal that conducted
        nothing certifies `0 → 0` without ever reaching `_crossing()`. This is a real deployment in
        which only one of the two slots is filled."""
        PathLedger = _require(self._conservation, "PathLedger", contract="conservation",
                              at="the 0 → 1 → 0 path certificate")

        led = PathLedger(self._incident, at=self.name)
        r = self._crossing() if self._incident is not None else None
        if r is not None:
            absorbed, transmitted, k = r
            led.absorb(absorbed, transmitted, at=self.name, k=k)
        led.emit(at=self.name)
        return led.certificate()

    def emit(self, facet: str, concept: Any = None) -> Any:
        """Transmission out: render a concept (or, if `None`, the settled resolution) out through the
        facet's inverse. Requires the facet to conduct out and to be bound."""
        self._require_direction(facet, ("out", "both"), "emit a surface out")
        self._require_bound(facet)
        return self._screen().render(facet, concept)

    def couple(self, facet_a: str, facet_b: str) -> float:
        """The measured signed coupling between two sides at the screen (+ attract / − detract;
        magnitude = strength; 0 when nothing resolves). Read, never declared — this is what retires
        every hand-typed relation sign."""
        return self._screen().couple(facet_a, facet_b)

    def coupling(self, facet_a: str, facet_b: str) -> Any:
        """The full `Coupling` record for the two sides (sign, strength, and the read it rests on)."""
        return self._screen().coupling(facet_a, facet_b)

    def read(self) -> Any:
        """The aperture measurement of the joint frame (K_signal, coherence, correlation length, …) on
        the UN-folded basis — a concentrated frame keeps its carrier."""
        return self._screen().read()

    def balance(self) -> Any:
        """The self-balancing zero (0→0) and each side's open flow — the conservation closure."""
        return self._screen().balance()

    def certify(self, facet: str, surface: Any) -> Any:
        """The losslessness certificate for a facet: the residual of `inverse ∘ entry` vs the surface
        (0.0 = perfect conversion). The conversion is lossless or it is leaking, and this reports which."""
        return self._screen().certify(facet, surface)

    def transfer(self, facet_from: str, facet_to: str) -> Any:
        """Energy accounting of a crossing between two sides (nonimaging: participation · τ)."""
        return self._screen().transfer(facet_from, facet_to)

    def clear(self, facet: Optional[str] = None) -> None:
        """Clear one placed side (or all) — the charge, never the structure."""
        self._screen().clear(facet)

    @property
    def placed(self) -> List[str]:
        """Which facets currently carry a placed signal on the screen."""
        return list(self._screen().placed)

    # ── construction from a shared artifact ───────────────────────────────────────────────────────

    @classmethod
    def from_artifact(cls, artifact: Dict[str, Any], *, embodiment: Any = None,
                      conservation: Any = None, **screen_kwargs: Any) -> "Crystal":
        """Ground a crystal shared as a store artifact: `verify` re-hashes and raises on mismatch
        (integrity before grounding), then builds the live object from the verified structure.

        The slots are named explicitly rather than swept up by `**screen_kwargs`, because a
        misspelled `embodyment=` would otherwise be forwarded to the screen as a construction
        keyword and the crystal would come back silently un-instrumented. The artifact travels
        without them: an embodiment is the host's, never the artifact's."""
        return cls(verify(artifact), embodiment=embodiment, conservation=conservation,
                   **screen_kwargs)

    # ── internals ─────────────────────────────────────────────────────────────────────────────────

    def _screen(self):
        if self._m is None:
            # lazy: only energizing a crystal reaches the membrane. `membrane_screen()` returns the
            # folded Screen class — the object model this membrane needs (place/register/render/
            # couple/transfer/certify). It is not the embodiment's measurement surface: a projection
            # read has no placement or coupling concept, so it cannot stand in here. Reached by an
            # explicit call so the choice of door is visible.
            #
            # The member most likely to be absent, and naming the gap here is the point. An
            # embodiment can fill `absorb_transmit` from a rank plus an SVD basis without holding
            # anything of the Screen's shape — mantle's beacon is exactly that today. Such a host
            # splits frames and cannot place them, and this names the gap here rather than failing
            # somewhere inside.
            membrane_screen = _require(self._embodiment, "membrane_screen", contract="embodiment",
                                       at="building the membrane screen (conduct / emit / couple / "
                                          "read / balance / certify / transfer)")
            self._m = membrane_screen()(**self._m_kwargs)
            for facet in self._bound:  # re-apply any bindings made before the screen existed
                self._register(facet)
        return self._m

    def _register(self, facet: str) -> None:
        if self._m is None:
            return  # deferred until the screen is created (see _screen)
        b = self._bound[facet]
        entry = b["entry"] or _out_only_entry(facet)  # a guard for out-only facets (never placed in)
        kwargs: Dict[str, Any] = {"entry": entry, "inverse": b["inverse"]}
        if b["energy"] is not None:
            kwargs["energy"] = b["energy"]
        if b["zero"] is not None:
            kwargs["zero"] = b["zero"]
        self._m.register(facet, **kwargs)

    def _require_facet(self, name: str) -> Dict[str, Any]:
        f = self._facets.get(name)
        if f is None:
            raise KeyError("no such facet on %r: %r" % (self.name, name))
        return f

    def _require_direction(self, facet: str, allowed: tuple, action: str) -> None:
        d = self._require_facet(facet).get("direction")
        if d not in allowed:
            raise ValueError("facet %r (direction=%r) cannot %s" % (facet, d, action))

    def _require_bound(self, facet: str) -> None:
        if facet not in self._bound:
            raise ValueError("facet %r is not bound — call bind(%r, entry=..., inverse=...) first"
                             % (facet, facet))

    def __repr__(self) -> str:
        return "Crystal(name=%r, facets=%d, tektons=%d, organons=%d)" % (
            self.name, len(self._facets), len(self._tektons), len(self._organons))


def _out_only_entry(facet: str) -> Entry:
    """An `entry` for an out-only facet: placing a surface into it is illegal (it only renders out), so
    the guard raises if ever invoked. Registration needs an entry callable; conduct() already blocks
    the path by direction before this could fire — the guard is a second layer for the same
    invariant."""
    def _guard(_surface: Any) -> Any:
        raise TypeError("facet %r is out-only — nothing conducts in through it" % facet)
    return _guard


__all__ = ["Crystal"]
