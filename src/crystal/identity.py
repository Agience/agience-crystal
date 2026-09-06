"""Crystal service identity — sign service JWTs + mint delegations.

The gateway is a platform service (`crystal`). It reads its private key from KEYS_DIR
and signs short-lived service JWTs to authenticate to Mantle (subscribing to the
change-feed as a system consumer) and to Origin (minting delegations for
event-driven describers). Stays platform-free — just jose + the PEM on disk; loads
lazily so the gateway runs fine without a key (the event-driven describer is then
disabled and only the inline, gateway-mediated describer fires).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

import httpx
from jose import jwt
from jose.exceptions import JWTError

logger = logging.getLogger("agience.crystal.identity")

_NAME = "crystal"
_KID = "crystal-1"


def load_trust_anchors(keys_dir: str) -> Dict[str, dict]:
    """Per-service inline JWKS from ``KEYS_DIR/authority.manifest.json``.

    Self-contained (no agience-beam dependency — the gateway is deliberately
    core-free): the gateway reads the manifest it was provisioned with and verifies
    service tokens against it. Returns ``{}`` when no manifest is present (standalone
    / dev, no trust mesh) — callers then treat internal endpoints as unauthenticated
    and must not expose them publicly."""
    if not keys_dir:
        return {}
    path = Path(keys_dir) / "authority.manifest.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text())
        return {
            name: anchor["jwks"]
            for name, anchor in (manifest.get("trust_anchors") or {}).items()
            if isinstance(anchor, dict) and anchor.get("jwks")
        }
    except Exception as exc:
        logger.warning("could not read authority manifest at %s: %s", path, exc)
        return {}


def verify_service_token(token: str, anchors: Dict[str, dict]) -> bool:
    """True iff ``token`` is a valid RS256 **service** JWT signed by a trust anchor.

    A valid signature is necessary but not sufficient: `origin` is a trust anchor, and Origin also
    signs ordinary end-user access tokens, so a signature check alone cannot separate a platform
    service from any signed-up user. What separates the two populations is the claim platform
    services stamp — `principal_type: "service"` (see `CrystalIdentity.service_jwt` just below, and
    `origin.service_identity`) — which no end-user token carries.

    `main.py` gates `POST /register` with exactly this check, so accepting a bare valid signature
    here would let any user re-register a persona slug to their own endpoint and have every
    `mcp_tool` dispatch to that persona arrive at it with the caller's delegation JWT attached
    (`mcp_client.py`, `Topology.register`).

    Audience is not pinned. This gateway is reachable as both "crystal" and "chorus", and nothing in
    the tree fixes which name real callers use, so `principal_type` alone is the check: it stops an
    end-user token from being accepted as a service token, but not one platform service from
    replaying another's token here."""
    for jwks in anchors.values():
        try:
            claims = jwt.decode(token, jwks, algorithms=["RS256"],
                                options={"verify_aud": False, "verify_iss": False})
        except JWTError:
            continue
        except Exception:
            continue
        # Signed by a trust anchor — so no other anchor can improve on this verdict. Whether it is
        # accepted turns on what kind of principal it is, not on who signed it.
        return claims.get("principal_type") == "service"
    return False


class CrystalIdentity:
    def __init__(self, private_key_pem: str, origin_uri: str) -> None:
        self._pem = private_key_pem
        self.origin_uri = origin_uri.rstrip("/")

    @classmethod
    def load(cls, keys_dir: str, origin_uri: str) -> Optional["CrystalIdentity"]:
        """Load the crystal identity from KEYS_DIR/crystal.private.pem, or None if
        absent (the gateway then runs without the event-driven describer)."""
        if not keys_dir:
            return None
        path = Path(keys_dir) / "crystal.private.pem"
        if not path.is_file():
            logger.info("No crystal.private.pem in %s — event-driven describers disabled", keys_dir)
            return None
        try:
            return cls(path.read_text(), origin_uri)
        except OSError as exc:
            logger.warning("Could not read crystal identity: %s", exc)
            return None

    def service_jwt(self, audience: str, ttl_seconds: int = 300) -> str:
        """Sign a platform-service JWT (iss=sub=crystal) for a peer service."""
        now = int(time.time())
        claims = {
            "iss": _NAME, "sub": _NAME, "aud": audience,
            "principal_type": "service", "iat": now, "exp": now + ttl_seconds,
        }
        return jwt.encode(claims, self._pem, algorithm="RS256", headers={"kid": _KID})

    async def mint_describe_delegation(self, persona_client_id: str, resource_id: str,
                                       ttl_seconds: int = 300) -> Optional[str]:
        """Mint an operator-rooted system-describe delegation via Origin.

        The event-driven describer has no forwarded user token, so it does not
        impersonate the artifact's owner. Instead Origin fixes the subject to the
        operator-rooted `platform-system` principal under a bounded
        `platform.describe` scope, addressed to the persona that will run the
        describe (sub=system, act=persona, aud=persona). Mantle authorizes it like
        any system-principal call — it only reaches artifacts the system principal
        is granted, and the describer safely no-ops otherwise. Returns the
        delegation token, or None on failure."""
        if not persona_client_id or not resource_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{self.origin_uri}/internal/describe-delegation",
                    headers={
                        "Authorization": f"Bearer {self.service_jwt('origin')}",
                        "Content-Type": "application/json",
                    },
                    json={"persona_client_id": persona_client_id,
                          "resource_id": resource_id, "ttl_seconds": ttl_seconds},
                )
                resp.raise_for_status()
                return resp.json().get("token")
        except Exception as exc:
            logger.warning("describe-delegation mint failed (persona=%s resource=%s): %s",
                           persona_client_id, resource_id, exc)
            return None
