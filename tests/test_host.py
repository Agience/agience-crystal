"""Host tests — persona discovery via injected providers, and the self-registration inversion.

`crystal.host` holds no persona roster and imports no persona by name. It is handed a list of
persona providers; the roster (mounts, gateway slug set, discovery) derives entirely from that
list, and each persona owns its own register().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from crystal import host


async def _noop_asgi(scope, receive, send):  # minimal ASGI app for app.mount
    return None


@dataclass
class FakePersona:
    """A stand-in persona provider — self-describing, self-registering."""

    name: str
    role: str
    endpoint: str
    mount_app: Any = field(default=_noop_asgi)
    session_manager: Optional[Any] = None
    startup_called: int = 0
    register_calls: int = 0
    fail_registers: int = 0  # return False this many times before succeeding

    async def startup(self) -> None:
        self.startup_called += 1

    def register(self, register_fn=None) -> bool:
        # register_fn is the registration helper the host injects; personas do not import it themselves.
        self.register_calls += 1
        if self.register_calls <= self.fail_registers:
            return False
        return True


def _personas() -> list[FakePersona]:
    # Deliberately NOT the real persona names/roles — proves nothing is hardcoded.
    return [
        FakePersona("zeta", "Made-up Role A", "/zeta/mcp"),
        FakePersona("qux", "Made-up Role B", "/qux/mcp"),
    ]


# ---------------------------------------------------------------------------
# The host holds no roster.
# ---------------------------------------------------------------------------
def test_host_module_has_no_hardcoded_roster():
    assert not hasattr(host, "_PERSONAS")
    # No module-level list/tuple/dict of persona-looking dicts.
    for name in dir(host):
        val = getattr(host, name)
        if isinstance(val, (list, tuple)) and val and isinstance(val[0], dict):
            assert "role" not in val[0], f"{name} looks like a hardcoded persona roster"


def test_roster_derives_from_injected_personas():
    personas = _personas()
    assert host._persona_slugs(personas) == {"zeta", "qux"}
    entries = host._discovery_entries(personas)
    assert [e["name"] for e in entries] == ["zeta", "qux"]
    assert entries[0] == {"name": "zeta", "endpoint": "/zeta/mcp", "role": "Made-up Role A"}


def test_build_app_mounts_only_injected_personas():
    personas = _personas()
    app = host.build_app(personas)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/zeta" in paths and "/qux" in paths
    # No real persona name leaks in (nothing hardcoded).
    assert "/aria" not in paths and "/sage" not in paths
    assert "/.well-known/mcp" in paths
    assert any("/relay/v1/connect" in getattr(r, "path", "") for r in app.routes)


def test_index_reflects_injected_personas():
    personas = _personas()
    app = host.build_app(personas)
    index_route = next(r for r in app.routes if getattr(r, "path", "") == "/")
    body = index_route.endpoint()
    assert [s["name"] for s in body["servers"]] == ["zeta", "qux"]


# ---------------------------------------------------------------------------
# The self-registration inversion: the host calls each persona's own register().
# ---------------------------------------------------------------------------
def test_self_register_calls_each_persona_register():
    import asyncio

    personas = _personas()
    asyncio.run(host._self_register_all(personas))
    assert all(p.register_calls == 1 for p in personas)


def test_self_register_retries_until_persona_succeeds():
    import asyncio

    personas = [FakePersona("zeta", "R", "/zeta/mcp", fail_registers=2)]
    with patch("asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(host._self_register_all(personas))
    # Failed twice, succeeded on the 3rd attempt.
    assert personas[0].register_calls == 3


def test_self_register_tolerates_persona_without_register():
    import asyncio

    @dataclass
    class Bare:
        name: str = "bare"

    # Should not raise even though Bare has no register() attribute.
    asyncio.run(host._self_register_all([Bare()]))


# ---------------------------------------------------------------------------
# Standalone discovery imports no persona by name and is empty by default.
# ---------------------------------------------------------------------------
def test_discover_personas_empty_without_entry_points():
    # No `agience.personas` entry points are installed in the test env.
    assert host.discover_personas() == []
