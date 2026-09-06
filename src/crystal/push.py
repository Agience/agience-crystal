"""Push the operator catalog to Mantle at chorus startup — same pattern as type registration.

Operators are organons — the invoked instruments (``op.*`` abbreviates organon); the catalog
pushed here is the organon registry's content (OPERATOR-ARCHITECTURE §12).

Operators are artifacts (the canon): chorus defines them, Mantle stores them. Mantle is
passive — it never scans chorus; we push at boot with retry and Mantle persists. Idempotent:
upsert is keyed by operator id; `spec_hash` is stamped by the registry so an edited definition
is a different operator (zero inherited evidence).

Auth (wire-verified against a live lattice-backed Mantle): mantle's `resolve_auth` accepts the
plain platform mutual JWT (aud="mantle", principal_type="service") — but the artifacts surface
then rejects it downstream, because the service branch deliberately carries no user:
GET /artifacts/visible → 401 "Missing authorization" (list_visible requires user_id or a
bearer grant) and POST /artifacts → 401 "User identification required" (create_artifact).
The platform's own identity for request-less automation writes is the system principal
(mantle services/acting_principal.py `system_acting_context` — a principal, not a bypass), so
the push signs the existing delegation shape chorus already holds the key for:
`origin.service_identity.sign_delegation_jwt(audience="mantle", user_sub=<system principal>)`
with act.sub=chorus and host_id from the shared instance.uuid — the full identity chain
mantle's delegation branch requires.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from .registry import OperatorRegistry
from crystal.operator_schema import OPERATOR_CONTENT_TYPE, spec_hash as _spec_hash

log = logging.getLogger("operators.push")

PUSH_FAILED = -1

# The one collection the pushed operator artifacts live in (created on first push).
# Needed for content encryption, not tidiness: mantle encrypts inline content under the
# collection's origin root, and a content-bearing top-level create fails closed (the
# creator's grant does not yet reach the new artifact's own origin root at encrypt time).
# Creates into a collection the creator holds grants on encrypt fine.
#: Rows per request while scanning. Above mantle's default of 100 to cut round trips;
#: the scan follows `has_more` regardless, so this is a batch size and never a ceiling.
_PAGE = 200

CATALOG_COLLECTION_NAME = "Operator Catalog"

# The definition fields that ride in the artifact's context JSON (everything except the
# pattern itself, which is the artifact's `content`).
# `spec_hash` is not in this list: it is derived from `offer`/`needs`/`kind`/`spec`, all of which
# are already here, so shipping it too would send the same fact twice and give the receiver a copy
# that could disagree with the definition beside it. Derive, never carry.
_DEF_FIELDS = ("id", "content_type", "state", "offer", "needs",
               "content_ref", "entry", "created_by")


def _service_token() -> Optional[str]:
    """Sign the delegation JWT described in the module docstring, or None (dev only)."""
    from prism.trust import service_identity as si
    try:
        si.get_service_identity()
    except RuntimeError:
        si.init_service_identity("chorus")
    sub = si.get_system_principal_id()
    if not sub:
        raise RuntimeError("system principal unresolvable (KEYS_DIR/instance.uuid missing)")
    return si.sign_delegation_jwt(audience="mantle", user_sub=sub)


class _MantleArtifactClient:
    """The minimal mantle surface the registry needs, over Mantle's REST artifact API.

    Mantle's create model (`CreateArtifactRequest`) has no operator fields — extra body
    keys are dropped and mantle mints its own artifact id — so the wire shape is: the
    operator definition (spec_hash and created_by included) rides as the artifact's
    `context` JSON, the pattern is the artifact `content`, `name` carries the operator id,
    `content_type` is the operator content type. Upsert is client-side (POST cannot name
    an id): match existing by operator id, PATCH when spec_hash changed, no-op otherwise.
    """

    def __init__(self, base: str | None = None) -> None:
        self._base = (base or os.getenv("MANTLE_URI") or "http://localhost:8081").rstrip("/")
        self._token: Optional[str] = None
        self._catalog_id: Optional[str] = None
        self._existing: Optional[Dict[str, Tuple[str, Dict[str, Any]]]] = None

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._token is None:
            try:
                self._token = _service_token()
            except Exception as e:
                log.debug("service JWT unavailable (%s: %s); pushing unauthenticated (dev only)",
                          type(e).__name__, e)
                self._token = ""                     # don't retry per request
        if self._token:
            h["Authorization"] = "Bearer %s" % self._token
        return h

    # -- HTTP ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, str]] = None):
        r = requests.get(self._base + path, params=params, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def _list(self, path: str, params: Optional[Dict[str, str]] = None):
        """Every row a LIST endpoint has, following pagination to exhaustion.

        `/artifacts/visible` and `/children` return `{items, total, has_more}` since
        2026-08-25 (P-6/P-7); a bare list is still read so a push pointed at an older node
        keeps working.

        This used to read one page, and its own docstring flagged the risk without
        closing it. Both callers are FIND-THEN-CREATE, so a truncated read did not degrade
        gracefully — it created a duplicate:

        * `_ensure_catalog` scans for the catalog collection and CREATES ONE when the scan
          comes back without it. A catalog sitting past the first page produced a second
          catalog, and the operators then split across the two.
        * `_index` builds the operator-id → existing-artifact map the upsert depends on.
          Truncated, every operator beyond the page was invisible to it and got RE-CREATED
          on every push — duplicates growing without bound, one set per boot.

        The server default is 100 rows. Neither caller passed a `limit`, so both were
        capped at it, and the failure is silent on both sides: mantle answered correctly
        and the client stopped reading.

        `has_more` is the terminator, never `len(items) < limit` — a page can legitimately
        come back short. An empty page with `has_more` still set would loop for ever, so
        that is a stop too: a server that cannot make progress ends the scan rather than
        hanging the boot.
        """
        rows: list = []
        offset = 0
        while True:
            page = dict(params or {})
            page.update(offset=str(offset), limit=str(_PAGE))
            body = self._get(path, page)
            if not isinstance(body, dict):
                # An older node: a bare list, and no pagination to follow.
                return body or []
            got = body.get("items", [])
            rows.extend(got)
            if not body.get("has_more") or not got:
                return rows
            offset += len(got)

    def _post(self, path: str, body: Dict[str, Any]):
        r = requests.post(self._base + path, json=body, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    def _patch(self, path: str, body: Dict[str, Any]):
        r = requests.patch(self._base + path, json=body, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    # -- wire shape ------------------------------------------------------------
    @staticmethod
    def _definition_of(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Reconstruct the operator definition from a stored artifact, or None."""
        try:
            ctx = json.loads(raw.get("context") or "")
        except (ValueError, TypeError):
            return None
        if not isinstance(ctx, dict) or not str(ctx.get("id", "")).startswith("op."):
            return None
        doc = {k: v for k, v in ctx.items() if k in _DEF_FIELDS}
        doc["content"] = raw.get("content") or ""
        return doc

    def _ensure_catalog(self) -> str:
        if self._catalog_id is None:
            for raw in self._list("/artifacts/visible") or []:
                if raw.get("name") == CATALOG_COLLECTION_NAME:
                    self._catalog_id = raw["id"]
                    break
            else:
                created = self._post("/artifacts", {
                    "name": CATALOG_COLLECTION_NAME,
                    "description": "Platform operator definitions, pushed by chorus at boot.",
                })
                self._catalog_id = created["id"]
        return self._catalog_id

    def _index(self) -> Dict[str, Tuple[str, Dict[str, Any]]]:
        """operator id -> (mantle artifact id, stored definition), built once per push."""
        if self._existing is None:
            self._existing = {}
            for raw in self._list("/artifacts/visible",
                                 {"content_type": OPERATOR_CONTENT_TYPE}) or []:
                d = self._definition_of(raw)
                if d:
                    self._existing[d["id"]] = (raw["id"], d)
        return self._existing

    # -- the registry's duck-typed surface ------------------------------------
    def put_artifact(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        catalog_id = self._ensure_catalog()
        existing = self._index()
        definition = {k: doc[k] for k in _DEF_FIELDS if doc.get(k) is not None}
        body = {"container_id": catalog_id,
                "name": doc["id"],
                "content_type": OPERATOR_CONTENT_TYPE,
                "context": json.dumps(definition),
                "content": doc.get("content") or ""}
        prior = existing.get(doc["id"])
        if prior is None:
            stored = self._post("/artifacts", body)
            existing[doc["id"]] = (stored["id"], definition)
        # Computed on both sides, never read from a stored field: comparing two stored `spec_hash`
        # values would make the answer depend on whether each side happened to have one written, and
        # a value nobody wrote compares equal to another value nobody wrote. Asking the question of
        # the specs themselves is the same rule as `evolution.preserve_fitness`: no side-cars, and
        # the answer cannot depend on when a row happened to be written.
        elif _spec_hash(prior[1]) != _spec_hash(doc):
            self._patch("/artifacts/%s" % prior[0],
                        {"context": body["context"], "content": body["content"]})
            existing[doc["id"]] = (prior[0], definition)
        return dict(doc)

    def list_artifacts(self, content_type: str | None = None) -> List[Dict[str, Any]]:
        params = {"content_type": content_type} if content_type else None
        out = []
        for raw in self._list("/artifacts/visible", params) or []:
            d = self._definition_of(raw)
            out.append(d if d else raw)
        return out


def push_operator_manifests(operators: List[Dict[str, Any]], base: str | None = None) -> int:
    """Register a list of operator definitions (the per-persona manifests, collected by the host from
    each persona binding) with Mantle. No shared catalog. Returns count, or PUSH_FAILED."""
    try:
        reg = OperatorRegistry(_MantleArtifactClient(base))
        n = 0
        for op in operators:
            reg.register(op)
            n += 1
        log.info("operator manifests pushed: %d operators", n)
        return n
    except Exception as e:
        log.debug("operator manifest push failed (will retry): %s: %s", type(e).__name__, e)
        return PUSH_FAILED


async def register_manifests_with_retry(operators: List[Dict[str, Any]]) -> None:
    """Boot task: push the per-persona operator manifests until Mantle is reachable (chorus + mantle
    boot together), then stop. `operators` is the concatenation of every persona's own manifest."""
    import asyncio
    if not operators:
        log.info("no operator manifests to register (no persona declared operators)")
        return
    delay = 2.0
    for _ in range(15):
        n = await asyncio.to_thread(push_operator_manifests, operators)
        if n != PUSH_FAILED:
            log.info("operator manifests registered with Mantle (%d operators)", n)
            return
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 15.0)
    log.warning("operator manifest registration did not complete (mantle unreachable); "
                "definitions remain local until the next restart — %d operators", len(operators))
