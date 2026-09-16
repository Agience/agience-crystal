"""`GET /` on the persona host names the address callers reached it on.

`_discovery_entries` returns relative endpoints, so the `port` in this index is the only absolute
addressing hint the host publishes. It used to come from `MCP_PORT` — an independent second opinion
about where the process is listening, which is wrong the moment the bind comes from anywhere else.

`uvicorn --port 9000` WITH THE VARIABLE UNSET SERVED ON 9000 AND ANNOUNCED 8082, and 8082 is
mantle's port in the standard local layout, so the announcement was not merely stale — it named a
live service that is not this one. `agience-cloud/scripts/service_common.sh` works around it by
exporting `MCP_PORT` beside `--port`, and says why in its own comment: "unset, a service on 8085
tells callers it is on 8082". A workaround in the deployment script is not a property of the app.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from crystal.host import build_app


def _index(base_url: str) -> dict:
    return TestClient(build_app([]), base_url=base_url).get("/").json()


def test_the_port_served_wins_over_the_environment(monkeypatch):
    # The variable is set, and set WRONG — the exact configuration the deploy script guards against.
    monkeypatch.setenv("MCP_PORT", "8082")
    assert _index("http://127.0.0.1:9000")["port"] == 9000


def test_an_unset_variable_does_not_fall_back_to_a_guess(monkeypatch):
    monkeypatch.delenv("MCP_PORT", raising=False)
    assert _index("http://127.0.0.1:9000")["port"] == 9000


def test_behind_an_edge_it_names_the_address_callers_used(monkeypatch):
    """A loopback-bound host behind Caddy must say 443, not its private port.

    The question this answers is "where do I reach you", so the Host header is the right source:
    the socket would name a port no external caller can dial.
    """
    monkeypatch.setenv("MCP_PORT", "8085")
    assert _index("https://crystal.example.com")["port"] == 443
    assert _index("http://crystal.example.com")["port"] == 80


def test_the_index_still_lists_its_personas():
    """The addressing fix must not cost the index its actual content."""
    body = _index("http://127.0.0.1:9000")
    assert body["service"] == "crystal-host"
    assert "servers" in body
