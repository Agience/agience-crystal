"""Thin async client for the label-blind Mantle database.

The gateway reads everything it needs — type-definition artifacts, persona
records, the artifacts it dispatches over — from Mantle's API, and forwards the
caller's bearer token so Mantle enforces keyed access (the gateway never bypasses
it). Pure CRUD/search; no business logic here.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class MantleClient:
    def __init__(self, base_uri: str, api_key: str = "", timeout: float = 15.0) -> None:
        self._base = base_uri.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout)

    def _headers(self, token: Optional[str]) -> dict:
        """Forward the caller's bearer token. Never substitute the service credential.

        A request with no `Authorization` header — `/create` and `/artifacts/{id}/op/{op}` carry
        no auth dependency, so `_bearer(request)` returns None for one — gets no bearer here
        either, and fails with a 401 from Mantle rather than running under the gateway's own
        service identity. Every data-path call site passes `token=token` (the caller's); `ping()`
        does not go through this method.

        `_api_key` is retained for an explicit service-identity call and is not reached by
        default."""
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def ping(self) -> bool:
        """True when Mantle is up and its store is reachable.

        Keyed on `store_status`, the field Mantle's `/status` returns for its single-store
        backend (`mantle/db/backend.py`)."""
        try:
            r = await self._client.get("/status")
            return r.status_code == 200 and bool(r.json().get("store_status"))
        except Exception:
            logger.debug("Mantle ping failed", exc_info=True)
            return False

    async def get_artifact(self, artifact_id: str, *, token: Optional[str] = None,
                           hydrate: bool = False) -> Optional[dict]:
        params = {"hydrate": "true"} if hydrate else None
        r = await self._client.get(f"/artifacts/{artifact_id}", params=params,
                                   headers=self._headers(token))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def my_access(self, resource_id: str, action: str,
                        *, token: Optional[str] = None) -> bool:
        """The caller's effective verdict for one action on one resource, decided by
        Mantle's audited `check_access` chokepoint (`GET /grants/my-access`).
        Raises on transport/HTTP failure — the gate that calls this fails closed."""
        r = await self._client.get("/grants/my-access",
                                   params={"resource_id": resource_id, "action": action},
                                   headers=self._headers(token))
        r.raise_for_status()
        return bool(r.json().get("allowed"))

    async def search(self, body: dict, *, token: Optional[str] = None) -> dict:
        """Mantle's retrieval endpoint, reached by the dispatcher's `search` verb.

        This posted to `/search` and nothing serves that. Mantle's route is `/artifacts/recall`;
        the earlier `/artifacts/search` was deleted deliberately — 404, never a redirect, on the
        stated reasoning that *"every consumer is in-house"*. Four in-house consumers were not
        migrated and this was one of them.

        This one failed LOUDLY, which is why it is the least harmful of the four: `raise_for_status`
        turns the 404 into an exception the dispatcher surfaces. The three in chorus swallowed it —
        a dedup that always answered "unique", grounding silently skipped, a search always empty.
        """
        r = await self._client.post("/artifacts/recall", json=body, headers=self._headers(token))
        r.raise_for_status()
        return r.json()

    async def post(self, path: str, body: dict, *, token: Optional[str] = None) -> Any:
        r = await self._client.post(path, json=body, headers=self._headers(token))
        r.raise_for_status()
        return r.json()

    async def patch(self, path: str, body: dict, *, token: Optional[str] = None) -> Any:
        r = await self._client.patch(path, json=body, headers=self._headers(token))
        r.raise_for_status()
        return r.json()

    async def delete(self, path: str, *, token: Optional[str] = None) -> Any:
        r = await self._client.delete(path, headers=self._headers(token))
        r.raise_for_status()
        return r.json() if r.content else {"deleted": True}

    async def aclose(self) -> None:
        await self._client.aclose()
