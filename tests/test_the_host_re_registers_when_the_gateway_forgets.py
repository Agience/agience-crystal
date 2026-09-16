"""The host keeps the gateway's knowledge of it true, rather than asserting it once.

Personas push their {name, role, endpoint, types} to the gateway at host startup, and the gateway
holds that in memory — `POST /register` is the only way it ever learns them.

⛔ REGISTRATION WAS A ONE-SHOT AGAINST STATE THAT DOES NOT SURVIVE A RESTART. `_self_register_all`
returns for good once every persona has landed, so restarting the gateway emptied the registry and
nothing refilled it. Measured 2026-09-14 on a live local node: restarting took `types_known` from
39 to 0, and it stayed 0 until the host itself was restarted by hand. Op dispatch and the whole
type catalogue are dead for that entire window, and nothing logs a reason.

The tests below pin the three answers the watch has to get right, because two of them are ways of
being wrong that look like working:

  * forgotten  → re-register. The failure this exists for.
  * still knows → do NOT re-register. `/register` replaces a persona's types (`remove_server` then
    re-add), so a re-push on every tick opens a window where a type is briefly absent and dispatch
    arriving in it 404s. A fix that churns is a new fault, not a cure.
  * unreachable → do NOT re-register. A gateway that is down comes back holding what it had;
    pushing into a refused socket only burns the interval. `None` is not `0`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crystal import host  # noqa: E402


class _Recorder:
    """Stands in for `_self_register_all`, counting how often the host pushes."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, personas) -> None:
        self.calls += 1


def _probe_returning(*values):
    """A gateway health probe that yields `values` in order, then ends the watch.

    The watch loops forever by design, so the harness — not a timeout — decides when it stops:
    once the script is exhausted it raises `CancelledError`, which is what a shutting-down host
    would deliver anyway.
    """
    seq = list(values)

    async def probe():
        if not seq:
            raise asyncio.CancelledError
        return seq.pop(0)

    return probe


async def _run(probe, register):
    with pytest.raises(asyncio.CancelledError):
        await host._keep_registered(
            [object()], interval=0, probe=probe, register=register
        )


@pytest.mark.asyncio
async def test_it_registers_once_at_startup_before_watching():
    reg = _Recorder()
    await _run(_probe_returning(), reg)
    assert reg.calls == 1, "the initial push must happen before any health check"


@pytest.mark.asyncio
async def test_a_gateway_that_forgot_is_re_registered():
    reg = _Recorder()
    await _run(_probe_returning(0), reg)
    assert reg.calls == 2, "startup push, then one more when the gateway reported nothing"


@pytest.mark.asyncio
async def test_a_gateway_that_still_knows_is_left_alone():
    reg = _Recorder()
    await _run(_probe_returning(7, 7, 7), reg)
    assert reg.calls == 1, "re-pushing over a live registry opens a window where dispatch 404s"


@pytest.mark.asyncio
async def test_an_unreachable_gateway_is_not_treated_as_an_empty_one():
    reg = _Recorder()
    await _run(_probe_returning(None, None), reg)
    assert reg.calls == 1, "unreachable is not forgotten — it comes back holding what it had"


@pytest.mark.asyncio
async def test_it_recovers_on_a_later_tick_not_only_the_first():
    """Loss can happen at any point in the host's life, not only right after it starts."""
    reg = _Recorder()
    await _run(_probe_returning(7, None, 0), reg)
    assert reg.calls == 2


@pytest.mark.asyncio
async def test_a_health_body_without_the_field_is_not_a_zero():
    """`personas_known` absent means a gateway that does not answer the question."""
    reg = _Recorder()
    await _run(_probe_returning(None), reg)
    assert reg.calls == 1
