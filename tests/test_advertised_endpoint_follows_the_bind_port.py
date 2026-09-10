"""The address a persona advertises must follow the port the host actually binds.

⛔ WHY THIS EXISTS. `push_server_types` builds each persona's `endpoint` — the address crystal
dispatches every `mcp_tool` operation to. Its fallback was the literal `http://localhost:8082`
while `crystal.host` chose its bind port from `MCP_PORT`. Two independent constants for one port.

On srv the host binds 8085 and 8082 belongs to another service, so all seven personas registered
an address that answered **404**, while the real endpoint answered 401. Nothing detected it:
registration SUCCEEDS either way — crystal records whatever address it is handed, logs
"Registered persona", and reports `personas_known: 7`. The registry reads as perfectly healthy
right up until an operation is dispatched into a dead port.

So the property under test is not "the default is 8085". It is that the advertised port and the
bind port are read from the SAME place and therefore cannot disagree.
"""
from __future__ import annotations

import crystal.type_registration as tr


def _endpoint(monkeypatch, **env) -> str:
    """The `endpoint` that would be pushed, captured without any network."""
    for k in ("CHORUS_PUBLIC_URI", "AGIENCE_SERVER_HOST_URI", "MCP_PORT", "CRYSTAL_URI"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    seen = {}
    monkeypatch.setattr(tr, "collect_server_types", lambda f: {"t": {"content_type": "x/y"}})

    # Intercepted at `httpx.post`, which is the real boundary. An earlier version of this helper
    # patched a `_post_types` that does not exist; `monkeypatch.setattr(..., raising=False)`
    # accepted it silently and the test made a genuine network call to localhost instead.
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"registered": "aria", "types": 1}

    def _capture(url, json=None, headers=None, timeout=None):
        seen["endpoint"] = (json or {}).get("endpoint")
        return _Resp()

    monkeypatch.setattr(tr.httpx, "post", _capture)
    tr.push_server_types("aria", "server.py")
    return seen.get("endpoint") or ""


def test_the_advertised_port_follows_mcp_port(monkeypatch):
    """⛔ THE REGRESSION. With MCP_PORT set and no override, the endpoint must name that port."""
    ep = _endpoint(monkeypatch, MCP_PORT="8085")
    assert ep, "no endpoint was built — the capture hook missed; this test proves nothing"
    assert ":8085/" in ep, f"advertised {ep!r} while the host binds 8085"
    assert ":8082/" not in ep, "the old hard-coded port is back"


def test_an_explicit_override_still_wins(monkeypatch):
    """An operator naming the address outranks any derivation — that is what the var is for."""
    ep = _endpoint(monkeypatch, CHORUS_PUBLIC_URI="https://chorus.agience.ai", MCP_PORT="8085")
    assert ep.startswith("https://chorus.agience.ai/"), ep


def test_the_endpoint_names_the_persona(monkeypatch):
    """Guard on the guard: a check that only looked at the port would pass on a truncated URL."""
    ep = _endpoint(monkeypatch, MCP_PORT="8085")
    assert ep.endswith("/aria/mcp"), ep
