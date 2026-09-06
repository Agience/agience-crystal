"""Operation dispatcher — the gateway's core.

`dispatch(artifact_id, op_name, body, token)`:
  1. fetch the artifact from Mantle (forwarding the caller's token),
  2. resolve `content_type → operations[op_name]` from the type registry,
  3. route by `dispatch.kind`:
       - artifact_crud → Mantle's CRUD/search API (Mantle enforces keyed access),
       - mcp_tool      → the owning persona's MCP tool (resolved via topology),
       - native        → a Mantle direct primitive (container ops); not yet exposed
                         by Mantle, so dispatch responds 501.

The gateway never bypasses Mantle's access control — it acts on behalf of the
caller by forwarding their bearer token. Op-level `requires_grant` is enforced
before dispatch against Mantle's audited `check_access` chokepoint (GET
/grants/my-access); the bytes are always guarded by Mantle regardless.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, mantle, types, topology, mcp=None, identity=None,
                 describe_dedup_ttl: float = 15.0) -> None:
        self.mantle = mantle
        self.types = types
        self.topology = topology
        self.mcp = mcp  # McpCaller (mcp_tool dispatch); injected for testability
        self.identity = identity  # CrystalIdentity — mints delegations for event-driven describers
        self._described: dict = {}  # artifact_id -> ts; dedups inline vs event + breaks loops
        self._describe_dedup_ttl = describe_dedup_ttl

    async def dispatch(self, artifact_id: str, op_name: str, body: dict,
                       *, token: Optional[str] = None) -> Any:
        """Dispatch, mapping upstream (Mantle/persona) HTTP errors to clean statuses
        instead of leaking a 500."""
        try:
            return await self._run(artifact_id, op_name, body, token)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            raise HTTPException(
                status_code=code,
                detail=f"upstream {code} from {e.request.url.path}") from e

    async def create(self, content_type: str, body: dict,
                     *, token: Optional[str] = None) -> Any:
        """Create a new artifact of `content_type`. Unlike `dispatch`, there is no
        existing artifact to fetch — the content_type alone selects the `create`
        operation. Maps upstream HTTP errors to clean statuses."""
        try:
            result = await self._run_create(content_type, body, token)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            raise HTTPException(
                status_code=code,
                detail=f"upstream {code} from {e.request.url.path}") from e
        # A new artifact is the canonical thing to describe.
        self._fire_describer(content_type, self._artifact_id_of(result), token)
        return result

    async def _run_create(self, content_type: str, body: dict, token: Optional[str]) -> Any:
        payload = dict(body or {})
        payload.setdefault("content_type", content_type)
        op = self.types.resolve_operation(content_type, "create")
        # No declared `create` op → default to a plain child insert. Mantle
        # enforces access; the body must carry a container_id.
        kind = op.kind if op else "artifact_crud"
        if kind == "native":
            # The only native create primitive is top-level container creation.
            return await self.mantle.post("/artifacts/containers", payload, token=token)
        if kind == "artifact_crud":
            return await self.mantle.post("/artifacts", payload, token=token)
        if kind == "mcp_tool":
            return await self._mcp_tool(op, None, payload, token)
        raise HTTPException(status_code=500, detail=f"Unknown dispatch kind '{kind}'")

    async def _run(self, artifact_id: str, op_name: str, body: dict,
                   token: Optional[str]) -> Any:
        artifact = await self.mantle.get_artifact(artifact_id, token=token)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        content_type = artifact.get("content_type") or ""
        op = self.types.resolve_operation(content_type, op_name)
        if op is None:
            raise HTTPException(
                status_code=404,
                detail=f"Operation '{op_name}' is not declared for content_type '{content_type}'",
            )

        # Op-level authorization: a declared `requires_grant` is verified with the caller's
        # token against Mantle's audited `check_access` chokepoint before dispatch. This matters
        # most for mcp_tool ops, where the persona downstream may act with its own credentials —
        # without this gate, `requires_grant` would be decorative on exactly the path that needs it.
        # `read` is already proven: the artifact fetch above succeeded under the caller's
        # token, and that fetch is Mantle's read decision. Everything else round-trips.
        needed = (op.requires_grant or "").strip().lower()
        if needed and needed != "read":
            allowed = await self.mantle.my_access(artifact_id, needed, token=token)
            if not allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Operation '{op_name}' requires '{needed}' on this artifact")

        if op.kind == "artifact_crud":
            result = await self._artifact_crud(op, artifact_id, body, token)
            if (op.target or "").strip().lower() in ("update", "create", "add"):
                self._fire_describer(content_type, artifact_id, token)
            return result
        if op.kind == "mcp_tool":
            return await self._mcp_tool(op, artifact, body, token)
        if op.kind == "native":
            result = await self._native(op, artifact_id, body, token)
            self._fire_describer(content_type, artifact_id, token)
            return result
        raise HTTPException(status_code=500, detail=f"Unknown dispatch kind '{op.kind}'")

    # -- describers ----------------------------------------------------------
    #
    # A describer enriches an artifact's context (extract text, summarize,
    # thumbnail, ...) after it changes. It runs "with delegate perms like any
    # other tool": dispatched exactly like an `mcp_tool`, so Mantle/the persona
    # enforce the same access. Two triggers, one dedup:
    #   - inline: a change made through the gateway forwards the caller's token.
    #   - event:  a change seen on Mantle's change-feed (incl. non-gateway writes)
    #     runs as the operator-rooted system-describe principal (CrystalIdentity) —
    #     no user impersonation; reaches only what the system principal is granted.
    # Fired async + best-effort — enrichment never blocks or fails the trigger.

    @staticmethod
    def _artifact_id_of(result) -> Optional[str]:
        return result.get("id") if isinstance(result, dict) else None

    def _describe_recently(self, artifact_id: str) -> bool:
        """True if a describer fired for this artifact within the dedup window.
        Dedups the inline vs event triggers AND breaks the describer-write-back
        loop (the enrichment write is itself a change event)."""
        import time as _t
        now = _t.monotonic()
        # prune occasionally to bound memory
        if len(self._described) > 4096:
            self._described = {k: v for k, v in self._described.items()
                               if now - v < self._describe_dedup_ttl}
        last = self._described.get(artifact_id)
        if last is not None and (now - last) < self._describe_dedup_ttl:
            return True
        self._described[artifact_id] = now
        return False

    def _fire_describer(self, content_type: str, artifact_id: Optional[str],
                        token: Optional[str]) -> None:
        describer = self.types.describer_for(content_type)
        if not describer or not artifact_id or self._describe_recently(artifact_id):
            return
        try:
            asyncio.create_task(
                self._run_describer(describer, content_type, artifact_id, token))
        except RuntimeError:
            # No running event loop (e.g. a sync caller in tests) — skip silently.
            logger.debug("describer not scheduled (no running loop) for %s", artifact_id)

    async def fire_describer_for_resource(self, content_type: str, artifact_id: str) -> None:
        """Event-driven trigger: fire the describer as the operator-rooted system
        principal (not the change's actor — Origin extends no user impersonation).
        Mints a bounded `platform.describe` delegation (sub=system, act=persona) via
        the crystal identity, so the describer runs under the operator's own grants.
        Reaches only artifacts the system principal is granted; safely no-ops (the
        run is best-effort) otherwise."""
        describer = self.types.describer_for(content_type)
        if not describer or not artifact_id or self.identity is None:
            return
        if self._describe_recently(artifact_id):
            return
        slug = (describer.get("target") or "").partition(".")[0]
        rec = self.topology.resolve(slug)
        client_id = rec.client_id if rec else None
        if not client_id:
            logger.warning("describer persona '%s' has no client_id; cannot mint delegation", slug)
            return
        token = await self.identity.mint_describe_delegation(client_id, artifact_id)
        if not token:
            return
        await self._run_describer(describer, content_type, artifact_id, token)

    async def _run_describer(self, describer: dict, content_type: str,
                             artifact_id: str, token: Optional[str]) -> None:
        try:
            if describer.get("kind") != "mcp_tool":
                return  # only mcp_tool describers are supported today
            slug, _, tool = (describer.get("target") or "").partition(".")
            if not slug or not tool:
                logger.warning("describer target '%s' is not 'persona.tool'", describer.get("target"))
                return
            rec = self.topology.resolve(slug)
            if rec is None or self.mcp is None:
                logger.warning("describer persona '%s' unavailable", slug)
                return
            await self.mcp.call_tool(
                rec.endpoint, tool,
                {"artifact_id": artifact_id, "content_type": content_type},
                token=token)
            logger.info("describer %s ran for artifact %s", describer.get("target"), artifact_id)
        except Exception as exc:  # best-effort: never propagate
            logger.warning("describer for %s failed: %s", artifact_id, exc)

    # -- handlers ------------------------------------------------------------

    async def _artifact_crud(self, op, artifact_id, body, token):
        verb = (op.target or "").strip().lower()
        if verb == "update":
            return await self.mantle.patch(f"/artifacts/{artifact_id}", body, token=token)
        if verb == "read":
            return await self.mantle.get_artifact(
                artifact_id, token=token, hydrate=bool((body or {}).get("hydrate")))
        if verb == "delete":
            return await self.mantle.delete(f"/artifacts/{artifact_id}", token=token)
        if verb == "search":
            return await self.mantle.search(body or {}, token=token)
        if verb in ("create", "add"):
            return await self.mantle.post("/artifacts", body or {}, token=token)
        raise HTTPException(
            status_code=400,
            detail=f"artifact_crud target '{op.target}' — supported: create, add, read, update, delete, search")

    @staticmethod
    def _ref_namespace(artifact, body):
        """The object a `$.`-rooted ref resolves against: the artifact, plus the request `body`.

        `context` is parsed when Mantle hands it back as a JSON STRING rather than an object —
        both shapes are in flight, and `$.context.tool_name` must work either way.
        """
        ns = dict(artifact or {})
        ctx = ns.get("context")
        if isinstance(ctx, str):
            try:
                ns["context"] = json.loads(ctx)
            except (ValueError, TypeError):
                pass                       # leave it a string; a path into it will fail loudly
        ns["body"] = body or {}
        return ns

    @classmethod
    def _resolve_ref(cls, value, ns, *, what):
        """A literal, or a `$.a.b` path walked against `ns`.

        Only `$.`-rooted dotted paths are a path; every other string is a literal, which is what
        makes `"server_ref": "lumen"` and `"server_ref": "$.root_id"` both legal in the same field.
        An unresolvable path is a 500 naming the path — NOT a silent None, which would dispatch
        to the wrong tool or send an empty argument and look like a working call.
        """
        if not isinstance(value, str) or not value.startswith("$."):
            return value
        cur = ns
        for part in value[2:].split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"{what}: path '{value}' does not resolve on this artifact "
                           f"(stopped at '{part}'; available: {sorted(cur)[:8] if isinstance(cur, dict) else type(cur).__name__})")
        return cur

    @classmethod
    def _resolve_arguments(cls, op, ns, body):
        """`arguments` is a mapping of name -> literal-or-path, or a single path to a whole dict.

        Absent, the caller's body is forwarded unchanged — the behaviour before this contract
        existed, and what every `target`-style op still relies on.
        """
        spec = op.arguments
        if spec is None:
            return body or {}
        if isinstance(spec, str):
            resolved = cls._resolve_ref(spec, ns, what="mcp_tool arguments")
            if not isinstance(resolved, dict):
                raise HTTPException(
                    status_code=500,
                    detail=f"mcp_tool arguments: '{spec}' resolved to {type(resolved).__name__}, want an object")
            return resolved
        if isinstance(spec, dict):
            return {k: cls._resolve_ref(v, ns, what=f"mcp_tool argument '{k}'")
                    for k, v in spec.items()}
        raise HTTPException(
            status_code=500,
            detail=f"mcp_tool arguments must be an object or a path, got {type(spec).__name__}")

    async def _mcp_tool(self, op, artifact, body, token):
        # Two declared forms. `server_ref`/`tool_ref` is what every shipped descriptor uses and
        # is tried FIRST: `target` now defaults to the operation name, so a `target`-first order
        # would shadow the referential form on every one of them.
        if op.server_ref is not None and op.tool_ref is not None:
            ns = self._ref_namespace(artifact, body)
            slug = self._resolve_ref(op.server_ref, ns, what="mcp_tool server_ref")
            tool = self._resolve_ref(op.tool_ref, ns, what="mcp_tool tool_ref")
            args = self._resolve_arguments(op, ns, body)
        else:
            slug, _, tool = (op.target or "").partition(".")
            args = body or {}
        if not slug or not tool or not isinstance(slug, str) or not isinstance(tool, str):
            raise HTTPException(
                status_code=500,
                detail=f"Bad mcp_tool dispatch (server={slug!r}, tool={tool!r}) — "
                       f"declare `target` as 'persona.tool', or `server_ref` + `tool_ref`")
        rec = self.topology.resolve(slug)
        if rec is None:
            raise HTTPException(status_code=502, detail=f"Persona '{slug}' is not registered")
        if self.mcp is None:
            raise HTTPException(status_code=500, detail="MCP caller not configured")
        return await self.mcp.call_tool(rec.endpoint, tool, args, token=token)

    async def _native(self, op, artifact_id, body, token):
        # native ops = Mantle direct primitives (container create/move). Mantle does
        # not yet expose these primitives, so every native op responds 501.
        raise HTTPException(
            status_code=501,
            detail=f"native op '{op.target}' pending — Mantle direct primitive not yet exposed",
        )
