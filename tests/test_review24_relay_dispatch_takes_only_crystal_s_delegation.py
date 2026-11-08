"""Review 24, M2: Crystal's relay dispatch takes only the delegation narrowed to Crystal.

The dispatch (`/<server uuid>/mcp`) drives the person's desktop over the relay. It took the claim
rule every caller check uses, which admits a third-party client's `mcp_client` token addressed to
any client; Mantle holds those, and could replay one here. It now takes only what the web app
reaches Crystal on: a delegation Origin narrowed to Crystal in the person's session (`aud` =
`act.sub` = `crystal`, scope `persona:crystal`). Tokens come from this suite's existing signing
fixture (`test_relay_takes_only_access_tokens.sign`).
"""
from __future__ import annotations

import pytest

from .test_relay_takes_only_access_tokens import _resolver, _scope, app, sign  # noqa: F401

CRYSTAL = {"aud": "crystal", "act": {"sub": "crystal"}, "principal_type": "delegation",
           "narrowed": True, "scope": "persona:crystal", "jti": "j-1"}


def test_the_dispatch_takes_crystal_s_delegation(app, sign):  # noqa: F811
    assert _resolver(app)(_scope(sign(**CRYSTAL))) == "alice"


@pytest.mark.parametrize("claims", [
    {"principal_type": "mcp_client", "aud": "some-mcp-client", "scopes": ["read"]},
    {},                                                               # the person's own token
    {"aud": "mantle"},                                                # the token for Mantle
    {"aud": "agience-server-zeta", "principal_type": "delegation",
     "act": {"sub": "agience-server-zeta"}},                          # a persona's
    {**CRYSTAL, "aud": "iris", "act": {"sub": "iris"}, "scope": "persona:iris"},
    {**CRYSTAL, "hop": {"from": "lumen", "jti": "x"}},
], ids=["mcp-client", "platform-token", "mantle-token", "persona", "iris", "a-hop"])
def test_the_dispatch_takes_nothing_else(app, sign, claims):  # noqa: F811
    assert _resolver(app)(_scope(sign(**claims))) is None
