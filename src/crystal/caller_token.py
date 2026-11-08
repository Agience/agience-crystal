"""What Crystal does with a caller's bearer (review 11, H3).

Crystal never receives or forwards a person's own access token. The web app exchanges it at Origin
before it calls Crystal (RFC 8693 token exchange):

* for a persona's own surface (`/personas/{name}/...`), a delegation Origin narrows to that persona
  (`aud` = `act.sub` = its name, scope `persona:<name>`): Crystal forwards it to that persona, and to
  no other;
* for Crystal's own work, a delegation narrowed to Crystal (`persona:crystal`): Crystal exchanges it
  at Origin (`/internal/delegation-token`, signed by Crystal's own key) for its own server
  delegation, and calls Mantle on that.

A person's full access token is refused at the door (401). A delegation narrowed to one persona
reaches no other. To reach a persona for a person on Crystal's own delegation (an operation's
`mcp_tool` dispatch, `GET /personas`), Crystal exchanges it at Origin per hop (John, 2026-11-03):
RFC 8693 at Origin's token endpoint, `audience` = that persona, authenticated by Crystal's own key
(an RFC 7523 client assertion), for a delegation narrowed to that persona, the same person, five
minutes, no widening. Origin lists the hops it allows (Crystal to each persona) and records each;
a refused hop reaches nothing. A grant key, a
platform service token and any token Crystal cannot read pass as before; the persona or Mantle
verifies them. Origin's sign-in bounds are honoured (`prism.trust.not_before`).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from fastapi import HTTPException

logger = logging.getLogger(__name__)

CRYSTAL = "crystal"
CRYSTAL_CLIENT_ID = "agience-server-crystal"


def _verify_origin(token: str) -> Optional[dict]:
    """An Origin-signed token's claims, or None (unsigned by Origin, or no manifest here)."""
    if not token or token.count(".") != 2:
        return None
    try:
        # Its signature against the manifest's Origin anchor and its `iss` against the manifest's
        # issuer, as prism checks it (review 15): another issuer's token is refused.
        from prism.trust.authority_trust import verify_origin_jwt
        return verify_origin_jwt(token)
    except Exception:                                             # noqa: BLE001
        return None


def _narrowed_to(claims: dict) -> Optional[str]:
    """The persona an Origin delegation is narrowed to, or None."""
    aud = claims.get("aud")
    act = (claims.get("act") or {}).get("sub")
    scopes = str(claims.get("scope") or "").split()
    if (claims.get("principal_type") == "delegation" and isinstance(aud, str) and aud == act
            and f"persona:{aud}" in scopes
            and all(s.startswith(f"persona:{aud}") for s in scopes)):
        return aud
    return None


def _is_person(claims: dict) -> bool:
    """A person's own access token: Origin-issued, no actor, not a delegation or a program."""
    return (not claims.get("act") and claims.get("principal_type", "user") in ("user", None)
            and not claims.get("token_type"))


_EXCHANGED: Dict[str, Tuple[str, float]] = {}


async def _exchange_for_crystal(narrowed: str) -> Optional[str]:
    """Crystal's own server delegation for a person, from the delegation Origin narrowed to
    Crystal (`/internal/delegation-token`, Crystal's service JWT)."""
    import httpx

    from crystal import config
    from crystal.identity import CrystalIdentity

    held = _EXCHANGED.get(narrowed)
    if held and held[1] > time.time():
        return held[0]
    try:
        ident = CrystalIdentity.load(config.KEYS_DIR, config.ORIGIN_URI)
        if ident is None:
            return None
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                f"{config.ORIGIN_URI.rstrip('/')}/internal/delegation-token",
                headers={"Authorization": f"Bearer {ident.service_jwt('origin')}"},
                json={"server_client_id": CRYSTAL_CLIENT_ID, "subject_token": narrowed})
        if r.status_code != 200:
            return None
        tok = str(r.json().get("token") or r.json().get("access_token") or "")
    except Exception as exc:                                      # noqa: BLE001
        logger.warning("Crystal's delegation exchange failed: %s", exc)
        return None
    if tok:
        _EXCHANGED[narrowed] = (tok, time.time() + 240)
    return tok or None


TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_HOPPED: Dict[Tuple[str, str], Tuple[str, float]] = {}


def _client_assertion(subject: str) -> Optional[str]:
    """Crystal's own signed assertion for Origin's token endpoint, or None (no key here). It is
    used once (a fresh `jti`, which Origin spends) and bound to the subject token it travels with
    (`subject_digest` = that token's SHA-256), so a captured assertion opens no other hop."""
    import hashlib
    import uuid

    from crystal import config
    from crystal.identity import CrystalIdentity

    ident = CrystalIdentity.load(config.KEYS_DIR, config.ORIGIN_URI)
    if ident is None:
        return None
    return ident.service_jwt(f"{config.ORIGIN_URI.rstrip('/')}/auth/token", ttl_seconds=60,
                             extra={"jti": uuid.uuid4().hex,
                                    "subject_digest": hashlib.sha256(subject.encode()).hexdigest()})


async def _post_token_exchange(form: dict) -> Optional[str]:
    """POST the exchange to Origin's token endpoint; the access token, or None (refused)."""
    import httpx

    from crystal import config

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{config.ORIGIN_URI.rstrip('/')}/auth/token", data=form)
    except Exception as exc:                                      # noqa: BLE001
        logger.warning("Crystal's hop exchange failed: %s", exc)
        return None
    if r.status_code != 200:
        logger.info("Origin refused Crystal's hop to %s (%s)", form.get("audience"),
                    r.status_code)
        return None
    try:
        return str(r.json().get("access_token") or "") or None
    except ValueError:
        return None


async def hop(narrowed: str, slug: str) -> Optional[str]:
    """The delegation narrowed to persona `slug`, exchanged at Origin from Crystal's own narrowed
    delegation `narrowed`, or None (Origin refused, or Crystal holds no key)."""
    held = _HOPPED.get((narrowed, slug))
    if held and held[1] > time.time():
        return held[0]
    assertion = _client_assertion(narrowed)
    if not assertion:
        return None
    tok = await _post_token_exchange({
        "grant_type": TOKEN_EXCHANGE, "subject_token": narrowed,
        "subject_token_type": ACCESS_TOKEN, "audience": slug,
        "client_id": CRYSTAL_CLIENT_ID, "client_assertion_type": JWT_BEARER,
        "client_assertion": assertion})
    if tok:
        _HOPPED[(narrowed, slug)] = (tok, time.time() + 240)
    return tok


@dataclass
class Caller:
    raw: str
    claims: Optional[dict] = None
    narrowed: Optional[str] = None
    person: bool = False
    _mantle: Optional[str] = field(default=None, repr=False)

    async def for_persona(self, slug: str) -> Optional[str]:
        """The token to forward to persona `slug`, or None (refused)."""
        if self.claims is None:
            return self.raw                       # a grant key, a service, an opaque token
        if self.narrowed is not None:
            return self.raw if self.narrowed == slug else None
        return self.raw if not self.person else None

    async def for_persona_hop(self, slug: str) -> Optional[str]:
        """The token to reach persona `slug` for this caller from Crystal's own work: Crystal's
        delegation exchanged at Origin for one narrowed to `slug`; a delegation already narrowed
        to `slug` as it is; otherwise as `for_persona`."""
        if self.narrowed == CRYSTAL:
            return await hop(self.raw, slug)
        return await self.for_persona(slug)

    async def for_mantle(self) -> Optional[str]:
        """The token to call Mantle on for this caller, or None (refused)."""
        if self.claims is None:
            return self.raw
        if self.narrowed == CRYSTAL:
            if self._mantle is None:
                self._mantle = await _exchange_for_crystal(self.raw)
            return self._mantle
        if self.narrowed is not None:
            return None                           # a persona's delegation is the persona's
        return self.raw if not self.person else None


async def resolve(raw: str) -> Caller:
    """Classify a bearer; refuse (401) a person's own token or one from before their sign-in
    bound."""
    claims = _verify_origin(raw)
    if claims is None:
        return Caller(raw=raw)
    from prism.trust import not_before
    if not_before.refused(claims):
        raise HTTPException(status_code=401, detail="this token was issued before its person "
                            "signed out; sign in again")
    narrowed = _narrowed_to(claims)
    if narrowed is None and _is_person(claims):
        raise HTTPException(status_code=401, detail="Crystal takes no person's own token: send "
                            "the delegation Origin narrows to this persona (or to Crystal)")
    return Caller(raw=raw, claims=claims, narrowed=narrowed, person=False)


async def for_persona(raw: str, slug: str) -> str:
    caller = await resolve(raw)
    tok = await caller.for_persona(slug)
    if not tok:
        raise HTTPException(status_code=403, detail=f"this delegation is not addressed to {slug}; "
                            f"a persona takes only the delegation Origin narrows to it")
    return tok


async def for_mantle(raw: str) -> str:
    caller = await resolve(raw)
    tok = await caller.for_mantle()
    if not tok:
        raise HTTPException(status_code=403, detail="this delegation does not reach Crystal's "
                            "work: send the delegation Origin narrows to Crystal")
    return tok
