"""Agience Crystal — condensation, routing: signal to content type. Apache-2.0.

Mantle is a label-blind database `(content_type, context, content)`; this gateway
resolves `content_type → behavior`: it dispatches operations, routes describers,
and owns the persona/type topology. Types and personas are the gateway's own
state — personas self-register their type definitions and endpoint here
(POST /register); Mantle never sees them. The gateway calls Mantle only for
artifact CRUD during dispatch, acting on behalf of the user.
See agience-pharos/dev-legacy/dev-features/chorus-core-gateway.md.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import asyncio

from crystal import config
from crystal.dispatcher import Dispatcher
from crystal.events import EventDescriberDriver, mantle_ws_url
from crystal.identity import CrystalIdentity, load_trust_anchors, verify_service_token
from crystal.mantle import MantleClient
from crystal.mcp_client import McpCaller
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry


def _bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else None


def _require_bearer(request: Request) -> str:
    """The caller's bearer token, or 401. Never proceed without a caller identity.

    Without this check, `_bearer()` returning None for a request with no `Authorization` header
    would let `MantleClient._headers` substitute the gateway's own `MANTLE_API_KEY` — a bare
    `curl -X POST .../artifacts/<any-id>/op/read` would execute under the gateway's service
    credential instead of being rejected, promoting the absence of an identity to a privileged one.
    `mantle.py` does not perform that substitution; this check is what turns a missing token into a
    clean 401 from the gateway rather than a confusing downstream error."""
    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    return token


def _require_reader(request: Request) -> None:
    """Gate a read of the type registry (`/types/all`, `/resolve/*`) to an authenticated caller.

    The registry holds every persona's type definitions: operation targets, the persona that
    serves each type, and the `ui` blocks. The gateway answers on the public edge (browsers call it
    cross-origin, hence the CORS policy below), and its only browser consumer, Facet's
    `ContentTypesProvider`, runs behind `LoginProtected` and sends the signed-in user's token. So a
    caller must present either an Origin access token (`prism.trust.verify_access_token`, which
    refuses refresh, email-verification and password-reset tokens) or a platform service token.

    No authority manifest follows `_require_platform_service`: KEYS_DIR set means refuse (503),
    KEYS_DIR unset is the dev/standalone mode and stays open."""
    anchors = getattr(request.app.state, "trust_anchors", {}) or {}
    if not anchors:
        if config.KEYS_DIR:
            logger.error("gateway: KEYS_DIR=%s is set but no usable authority manifest was found "
                         "there — refusing a registry read", config.KEYS_DIR)
            raise HTTPException(status_code=503, detail="gateway authority manifest unavailable")
        return
    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    if verify_service_token(token, anchors):
        return
    try:
        from prism.trust.authority_trust import verify_access_token
        verify_access_token(token, delegation_audiences=())
    except Exception:
        raise HTTPException(status_code=401, detail="invalid access token")


def _require_platform_service(request: Request, *, static_key: str = "") -> None:
    """Gate a gateway-internal endpoint (`/register`, `/embed`).

    Mantle and the personas reach the gateway over the private network and present a
    platform service token; these endpoints must never be reachable unauthenticated
    (and must not be exposed on the public edge). When the authority manifest is
    present (any real deployment) this requires a Bearer that is either the configured
    static service key or a service JWT signed by a manifest trust anchor. With no
    manifest (standalone/dev, no trust mesh) the endpoints stay open, with a warning;
    do not expose them publicly in that mode."""
    anchors = getattr(request.app.state, "trust_anchors", {}) or {}
    if not anchors:
        # A misconfigured KEYS_DIR must not silently disable gateway auth. `KEYS_DIR` defaults to
        # `""`, so an unset env var, a typo'd path, and a volume that failed to mount are otherwise
        # indistinguishable from "no manifest wanted". `/register` is the endpoint that repoints a
        # persona at an arbitrary URL, so leaving it open on a broken-but-configured deployment is
        # the same escalation `verify_service_token` guards against, reachable with no token at all.
        # The distinction that matters is configured-but-broken vs genuinely-unconfigured:
        #   • KEYS_DIR set   -> someone intended auth here. A manifest that cannot be read is a
        #                       failure, not an invitation. Fail closed.
        #   • KEYS_DIR unset -> the documented dev/standalone mode. Still open.
        # `prism.Host` applies the same rule for the same reason: an empty resolution from a source
        # that was configured must never read as "no auth wanted".
        if config.KEYS_DIR:
            logger.error(
                "gateway: KEYS_DIR=%s is set but no usable authority manifest was found there — "
                "refusing the request rather than serving /register and /embed unauthenticated",
                config.KEYS_DIR,
            )
            raise HTTPException(status_code=503, detail="gateway authority manifest unavailable")
        logger.warning(
            "gateway: no authority manifest — /register and /embed are UNAUTHENTICATED "
            "(dev/standalone mode). Do not expose these endpoints on a public edge."
        )
        return
    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    if static_key and secrets.compare_digest(token, static_key):
        return
    if not verify_service_token(token, anchors):
        raise HTTPException(status_code=401, detail="invalid service token")

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO))
logger = logging.getLogger("agience.crystal")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mantle = MantleClient(config.MANTLE_URI, config.MANTLE_API_KEY)
    app.state.types = TypeRegistry()
    # The builtin types: the collection, the workspace, and the base types others name in
    # `inherits`. They live beside the package in a source checkout (`src/types`, which Facet's
    # build reads too); `CRYSTAL_TYPES_DIR` points elsewhere for an install that has none there.
    app.state.types.load_builtin(Path(
        os.getenv("CRYSTAL_TYPES_DIR") or Path(__file__).resolve().parent.parent / "types"))
    app.state.topology = Topology()
    # Crystal service identity (optional) — enables event-driven describers.
    app.state.identity = CrystalIdentity.load(config.KEYS_DIR, config.ORIGIN_URI)
    # Trust anchors for verifying service tokens on gateway-internal endpoints
    # (/register, /embed). Empty {} when no manifest (dev) → those endpoints open.
    app.state.trust_anchors = load_trust_anchors(config.KEYS_DIR)
    if app.state.trust_anchors:
        logger.info("gateway auth: verifying service tokens against %d trust anchor(s)",
                    len(app.state.trust_anchors))
    app.state.dispatcher = Dispatcher(
        app.state.mantle, app.state.types, app.state.topology,
        mcp=McpCaller(), identity=app.state.identity)
    logger.info("Gateway up. Mantle = %s | awaiting persona registration", config.MANTLE_URI)

    # Event-driven describers: subscribe to Mantle's change-feed as a system
    # consumer so describers also fire on NON-gateway writes. Needs the crystal
    # identity; otherwise only the inline (gateway-mediated) describer runs.
    app.state.event_driver = None
    event_task = None
    if config.EVENTS_ENABLED and app.state.identity is not None:
        app.state.event_driver = EventDescriberDriver(
            mantle_ws_url(config.MANTLE_URI), app.state.identity, app.state.dispatcher)
        event_task = asyncio.create_task(app.state.event_driver.run())
        logger.info("Event-driven describers ON (system consumer on the change-feed)")
    else:
        logger.info("Event-driven describers OFF (no crystal identity / disabled)")

    yield

    if app.state.event_driver is not None:
        app.state.event_driver.stop()
    if event_task is not None:
        event_task.cancel()
    await app.state.mantle.aclose()


app = FastAPI(title="Agience Crystal (gateway)", lifespan=lifespan)


#: The POST routes whose body this gateway parses. FastAPI reads and parses a route's body before
#: the handler runs, so the handler's own credential check comes after the parse.
_GATED_BODY = re.compile(r"^/(?:register|create|artifacts/[^/]+/op/[^/]+)/?$")


class _BodyGate:
    """Check the credential, then cap the body, before FastAPI parses `/register`, `/create` or an
    operation.

    The same checks the handlers make (`_require_platform_service` for `/register`,
    `_require_bearer` for the others), run first, so an unauthenticated caller costs no JSON parse.
    The body is then read up to `config.MAX_BODY_BYTES`: a declared `Content-Length` over it is
    refused before anything is read, and a chunked body is refused as soon as it passes it. What
    was read is replayed to the app, so the handler sees the request unchanged."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or not _GATED_BODY.match(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            if scope["path"].rstrip("/") == "/register":
                _require_platform_service(request)
            else:
                _require_bearer(request)
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
            return

        limit = config.MAX_BODY_BYTES
        too_large = JSONResponse(
            {"detail": f"request body exceeds this gateway's limit of {limit} bytes "
                       "(CRYSTAL_MAX_BODY_BYTES)"}, status_code=413)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await too_large(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse({"detail": "invalid Content-Length"}, status_code=400)(
                    scope, receive, send)
                return

        chunks, size, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return                                  # the client went away
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                await too_large(scope, receive, send)
                return
            chunks.append(body)
            more = message.get("more_body", False)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


# Added before CORS, so CORS wraps it and a refusal here still carries the CORS headers a browser
# needs to read it.
app.add_middleware(_BodyGate)
# CORS — facet (browser) and external SDK callers dispatch artifact operations
# through the gateway cross-origin, so it needs the same open-but-Bearer-only
# policy as Mantle. allow_origins=["*"] is safe: every authenticated call carries
# a Bearer token (never a cookie), so a malicious origin gains nothing by forging
# a request from the user's browser — it has no token to send.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Persona self-registration — the gateway's type + topology source.
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    slug: str                       # persona slug, e.g. "aria"
    endpoint: str                   # reachable MCP endpoint for the persona
    client_id: Optional[str] = None
    types: List[dict] = []          # type definitions this persona owns


@app.post("/register")
async def register_server(request: Request, body: RegisterRequest):
    """A persona registers itself + the content types it owns. Re-registering
    replaces that persona's previous types (idempotent). Gateway-internal: requires
    a platform service token (personas sign one; see crystal/type_registration.py)."""
    _require_platform_service(request)
    app.state.types.remove_server(body.slug)
    app.state.topology.register(body.slug, body.endpoint, body.client_id)
    n = sum(1 for d in body.types if app.state.types.register(d, server=body.slug))
    logger.info("Registered persona '%s' (%s) with %d type(s)", body.slug, body.endpoint, n)
    return {"registered": body.slug, "endpoint": body.endpoint, "types": n}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mantle_reachable": await app.state.mantle.ping(),
        "types_known": len(app.state.types.known()),
        "personas_known": len(app.state.topology.known()),
    }


@app.get("/resolve/{content_type:path}")
async def resolve_type(content_type: str, request: Request):
    """Debug: resolve a content_type to its operations, its own and inherited (the type registry).

    Gated by `_require_reader`, as `/types/all` is."""
    _require_reader(request)
    td = app.state.types.resolve(content_type)
    if td is None:
        raise HTTPException(status_code=404, detail=f"No type definition for '{content_type}'")
    return {
        "content_type": td.content_type,
        "server": td.server,
        "operations": {
            name: {"kind": op.kind, "target": op.target, "requires_grant": op.requires_grant}
            for name, op in app.state.types.operations_of(content_type).items()
        },
        "describer": td.describer,
    }


@app.get("/types/all")
async def list_all_types(request: Request):
    """The whole registry, for a client hydrating its own.

    A CLIENT CANNOT BOOTSTRAP FROM `/resolve`. That route answers about a content type the
    caller already names, which is the right shape for dispatching an artifact in hand and the
    wrong one for a browser starting empty: Facet ships generic viewers only, and which viewer a
    type uses — with its `ui.record` and `resource_uri` — arrives from here
    (`astra/web/src/context/contenttypes/ContentTypesProvider.tsx` hydrates before any artifact UI
    renders). Without a list it would have to guess the names to ask for.

    `raw` IS THE DEFINITION, NOT A SUMMARY. `/resolve` returns a projection — operations and the
    describer — which is enough to dispatch and not enough to render; the `ui` block a viewer needs
    lives only in what the persona pushed. Serving the projection here would look correct and lose
    the one thing the caller came for.

    AN EMPTY LIST IS A REAL ANSWER. It means no persona has registered — `/health` reports the
    same thing as `personas_known: 0` — and the client falls back to its build-time primitives.
    Logged, because at the wire an empty catalogue and an unpopulated one are the same bytes.

    AUTHENTICATED, MATCHING `/resolve`, WHICH READS THE SAME REGISTRY. The gateway answers on the
    public edge, and the registry names every persona's operation targets; its one browser caller
    hydrates after sign-in and sends the user's token. `_require_reader` states the rule.
    """
    _require_reader(request)
    types = []
    for content_type in app.state.types.known():
        td = app.state.types.resolve(content_type)
        if td is None:
            continue
        types.append({
            "content_type": td.content_type,
            "definition": td.raw,
            "server": td.server,
        })
    if not types:
        logger.info(
            "types/all is empty: no persona has registered. Clients fall back to their "
            "build-time primitives."
        )
    return {"types": types}


@app.post("/create")
async def create_artifact(request: Request, body: dict = Body(default_factory=dict)):
    """Create a new artifact. Resolves `content_type` (from the body) to its
    `create` operation and routes it: native → Mantle's top-level container
    primitive, artifact_crud → child insert into `container_id`, mcp_tool → the
    owning persona. Forwards the caller's bearer token so Mantle/personas enforce
    access. content_type alone selects the op — there is no artifact yet."""
    content_type = (body or {}).get("content_type")
    if not content_type:
        raise HTTPException(status_code=400, detail="body.content_type is required")
    return await app.state.dispatcher.create(content_type, body, token=_require_bearer(request))


@app.post("/artifacts/{artifact_id}/op/{op_name}")
async def run_operation(artifact_id: str, op_name: str, request: Request,
                        body: dict = Body(default_factory=dict)):
    """Dispatch an operation on an artifact (the operation surface relocated from
    Mantle). Resolves content_type -> op, routes by kind. Forwards the caller's
    bearer token so Mantle/personas enforce access."""
    return await app.state.dispatcher.dispatch(
        artifact_id, op_name, body, token=_require_bearer(request))


# `POST /embed` is not a route: an observer computes the coordinate where it is needed rather than
# offering it as a service, and the platform has no models to proxy an embedding request to.
#
# `_require_platform_service` still gates `/register`. `EMBEDDINGS_URI`/`EMBEDDINGS_API_KEY` in
# `config.py` are read by Mantle's own `AgienceHTTPEmbeddings` client.


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("crystal.main:app", host=config.CRYSTAL_HOST, port=config.CRYSTAL_PORT,
                log_level=config.LOG_LEVEL.lower())
