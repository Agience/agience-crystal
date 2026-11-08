"""Review 23, H1: the desktop relay socket takes only a delegation narrowed to the relay.

The socket made its caller the person's desktop relay on the person's platform token, which
Mantle holds on every call, so a Mantle taken over could become the person's relay. It now takes
only what the person's Origin session issues for it: a delegation narrowed to `relay` (`aud` =
`act.sub` = `relay`, scope `relay:host`), never the platform token. Tokens come from this suite's
existing signing fixture (`test_relay_takes_only_access_tokens.sign`).
"""
from __future__ import annotations

import pytest

from .test_relay_takes_only_access_tokens import _socket_accepts, app, sign  # noqa: F401

RELAY = {"aud": "relay", "act": {"sub": "relay"}, "principal_type": "delegation",
         "narrowed": True, "scope": "relay:host", "jti": "j-1"}


def test_the_socket_takes_the_relay_delegation(app, sign, monkeypatch):  # noqa: F811
    assert _socket_accepts(app, sign(**RELAY), monkeypatch)


@pytest.mark.parametrize("claims", [
    {},                                                                # the platform token
    {**RELAY, "scope": "persona:relay"},
    {**RELAY, "aud": "ember", "act": {"sub": "ember"}, "scope": "ember:read"},
    {**RELAY, "hop": {"from": "crystal", "jti": "x"}},
    {**RELAY, "narrowed": False},
], ids=["platform-token", "other-scope", "ember", "a-hop", "not-narrowed"])
def test_the_socket_takes_nothing_else(app, sign, claims, monkeypatch):  # noqa: F811
    assert not _socket_accepts(app, sign(**claims), monkeypatch)
