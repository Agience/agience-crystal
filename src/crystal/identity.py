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


#: The names a service token addressed to this gateway carries as its `aud`.
GATEWAY_AUDIENCES = ["crystal", "chorus"]


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

    The audience is pinned to this gateway's own names (review 19), so a token one service sent
    another (Crystal's to Mantle, Mantle's to Origin) is useless here."""
    for jwks in anchors.values():
        try:
            claims = jwt.decode(token, jwks, algorithms=["RS256"],
                                options={"verify_aud": False, "verify_iss": False})
        except JWTError:
            continue
        except Exception:
            continue
        # Signed by a trust anchor — so no other anchor can improve on this verdict. Whether it is
        # accepted turns on what kind of principal it is, and whom it is addressed to.
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        return claims.get("principal_type") == "service" and any(
            a in GATEWAY_AUDIENCES for a in auds)
    return False


#: The jtis of service tokens this gateway has taken, until each expires (review 19).
_SPENT: Dict[str, float] = {}


def _spend(jti: str, exp: float) -> bool:
    """Record a token's jti; False when it was already taken. Expired records are dropped."""
    import time
    now = time.time()
    for k in [k for k, v in _SPENT.items() if v < now]:
        del _SPENT[k]
    if jti in _SPENT:
        return False
    _SPENT[jti] = float(exp)
    return True


def verify_token_of(token: str, anchors: Dict[str, dict], name: str, *,
                    audience: str = "crystal") -> bool:
    """True iff ``token`` is a service JWT that `name` signed as itself for this gateway, once:
    verified against `name`'s own manifest anchor only, with `iss == sub == name` (review 16),
    `aud` this gateway's own name, and a `jti` not seen before (review 19). A token `name` sent
    another service (Mantle's change feed, a type publish) is useless here, and so is one replayed.
    """
    jwks = anchors.get(name)
    if not name or not jwks:
        return False
    try:
        claims = jwt.decode(token, jwks, algorithms=["RS256"], audience=audience,
                            options={"verify_iss": False})
    except Exception:  # noqa: BLE001
        return False
    if not (claims.get("principal_type") == "service" and claims.get("iss") == name
            and claims.get("sub") == name):
        return False
    jti, exp = claims.get("jti"), claims.get("exp")
    if not isinstance(jti, str) or not jti or not isinstance(exp, (int, float)):
        return False
    return _spend(f"{name}:{jti}", exp)


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

    def service_jwt(self, audience: str, ttl_seconds: int = 300,
                    extra: Optional[dict] = None) -> str:
        """Sign a platform-service JWT (iss=sub=crystal) for a peer service; `extra` adds claims
        (a hop assertion's `jti` and `subject_digest`)."""
        now = int(time.time())
        claims = {
            **(extra or {}),
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
