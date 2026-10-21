"""A persona tool the platform offers as an operator is reached directly only by a caller who may
invoke that operator.

The platform's operators (reconcile accounts, raise an Inbox item, ...) are granted to accounts one
by one, so each can be withheld or metered on its own. Each runs a persona tool, and the same tool is
also reachable on `POST /personas/{slug}/tools/call`; without this gate a withheld operator was one
direct call away. Mantle names the operators, the tool each runs and whether the caller may invoke
it (`GET /artifacts/platform-operators`); a tool no operator runs is not affected, and a person's
own server (by its card's id) is decided by Iris, on `invoke` on that card.

The answer is kept for a short while per caller, so a view calling its persona repeatedly costs one
Mantle read; a grant withdrawn reaches the gate within that time.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

_UUID = re.compile(r"^[0-9a-fA-F-]{32,36}$")
TTL = 30.0


class OperatorGate:
    def __init__(self, mantle: Any, ttl: float = TTL, clock=time.monotonic) -> None:
        self.mantle = mantle
        self.ttl = ttl
        self.clock = clock
        self._seen: Dict[str, Tuple[float, Dict[Tuple[str, str], Tuple[bool, str]]]] = {}

    async def _offered(self, token: Optional[str]) -> Dict[Tuple[str, str], Tuple[bool, str]]:
        key = hashlib.sha256((token or "").encode()).hexdigest()
        now = self.clock()
        hit = self._seen.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        try:
            items = await self.mantle.platform_operators(token=token)
        except Exception as exc:  # the gate fails closed: a tool it cannot clear is not called
            raise HTTPException(status_code=503, detail=f"cannot tell which tools need a grant: {exc}")
        offered: Dict[Tuple[str, str], Tuple[bool, str]] = {}
        for i in items:
            pair = (str(i.get("server") or ""), str(i.get("tool") or ""))
            may, slug = bool(i.get("may_invoke")), str(i.get("slug") or "")
            # Two operators on one tool: either grant clears it.
            offered[pair] = (offered.get(pair, (False, slug))[0] or may, slug)
        if len(self._seen) > 4096:
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.ttl}
        self._seen[key] = (now, offered)
        return offered

    async def check(self, slug: str, tool: str, token: Optional[str]) -> None:
        if _UUID.match(slug or ""):
            return
        found = (await self._offered(token)).get((slug, tool))
        if found is not None and not found[0]:
            raise HTTPException(
                status_code=403,
                detail=f"{slug}.{tool} is the platform operator {found[1]!r}, which you may not invoke")
