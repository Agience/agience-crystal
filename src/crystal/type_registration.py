"""Self-registration of server-owned content types with the gateway.

Each persona pushes the types it owns at host startup: read every
``ui/<top>/<sub>/type.json`` the server ships and POST them to the gateway's
(agience-prism-py) ``/register`` endpoint, signed with a Chorus service JWT.
Chorus owns every part of the type — including the ``view.html`` viewer, which
Chorus serves itself as a ``ui://`` resource and merely points at via
``ui.resource_uri`` in the pushed ``type.json``. Types and personas are the
gateway's state; Mantle never sees them.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from prism.trust import service_identity

log = logging.getLogger(__name__)

_TIMEOUT_S = 10.0

# Sentinels for the retrying caller: 0 = nothing to register (handled),
# >0 = registered that many, PUSH_FAILED = retry, PUSH_REFUSED = do not retry.
#
# "Could not reach" and "answered no" are different conditions and get different sentinels: folding
# a 404 (the gateway answering clearly that this endpoint does not exist) into the same value as a
# connection refused would retry a permanent condition on a backoff built for a service that is
# still booting, burning seconds of a cold start re-asking a question already answered. Retry is for
# "not yet"; it is not for "no".
#
# The split follows HTTP's own semantics: 5xx and transport errors are the server failing to
# answer (it may answer later); 4xx is the server answering that the request is wrong (it will
# answer the same way forever).
PUSH_FAILED = -1
PUSH_REFUSED = -2


def _content_type_from_rel(parts: tuple[str, ...]) -> Optional[str]:
    """Map a ``ui/<top>/<sub>`` folder pair to a content type. ``_wildcard`` →
    ``*`` (category fallback)."""
    if len(parts) != 2:
        return None
    top, sub = parts
    if sub == "_wildcard":
        sub = "*"
    return f"{top}/{sub}"


def collect_server_types(server_file: str) -> Dict[str, Any]:
    """Return ``{content_type: type_json_dict}`` for every type the server ships
    under its ``ui/`` tree."""
    ui_root = Path(server_file).parent / "ui"
    types: Dict[str, Any] = {}
    if not ui_root.exists() or not ui_root.is_dir():
        return types
    for type_json_path in sorted(ui_root.glob("*/*/type.json")):
        rel = type_json_path.parent.relative_to(ui_root).parts
        ct = _content_type_from_rel(rel)
        if not ct:
            continue
        try:
            # utf-8-sig tolerates a leading BOM (Windows editor artifact).
            defn = json.loads(type_json_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            log.warning("Skipping unreadable type.json at %s", type_json_path)
            continue
        if not isinstance(defn, dict):
            continue
        defn.setdefault("content_type", ct)
        types[ct] = defn
    return types


def push_server_types(
    server_name: str, server_file: str, *, gateway_uri: Optional[str] = None
) -> int:
    """Register one server's owned types + endpoint with the gateway (agience-prism-py).

    Types and personas are the gateway's state — Mantle never sees them. Returns
    the count registered, ``0`` if the server ships no types, or :data:`PUSH_FAILED`
    on a transport/HTTP error (so the caller can retry until the gateway is up)."""
    types = collect_server_types(server_file)
    if not types:
        return 0
    base = (gateway_uri or os.getenv("CRYSTAL_URI") or "http://localhost:8085").rstrip("/")
    chorus_base = (
        os.getenv("CHORUS_PUBLIC_URI")
        or os.getenv("AGIENCE_SERVER_HOST_URI")
        or "http://localhost:8082"
    ).rstrip("/")
    payload = {
        "slug": server_name,
        "endpoint": f"{chorus_base}/{server_name}/mcp",
        "client_id": f"agience-server-{server_name}",
        "types": list(types.values()),
    }
    headers = {"Content-Type": "application/json"}
    try:
        # /register does not authenticate requests; the signed identity is sent regardless.
        headers["Authorization"] = f"Bearer {service_identity.sign_service_jwt(audience='crystal')}"
    except Exception:
        pass
    try:
        resp = httpx.post(f"{base}/register", json=payload, headers=headers, timeout=_TIMEOUT_S)
    except httpx.HTTPError as exc:
        log.warning("Type registration to gateway failed for %s (unreachable?): %s", server_name, exc)
        return PUSH_FAILED
    if resp.status_code != 200:
        # 4xx = the gateway answered and the answer is no. Retrying re-asks a settled question.
        permanent = 400 <= resp.status_code < 500
        log.warning(
            "Gateway /register returned %d for %s%s: %s",
            resp.status_code, server_name,
            " (permanent — not retrying)" if permanent else " (will retry)",
            resp.text[:200],
        )
        return PUSH_REFUSED if permanent else PUSH_FAILED
    log.info("Registered %d type(s) for %s with the gateway", len(types), server_name)
    return len(types)
