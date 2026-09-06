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
import secrets
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
async def resolve_type(content_type: str):
    """Debug: resolve a content_type to its operations (from the type registry)."""
    td = app.state.types.resolve(content_type)
    if td is None:
        raise HTTPException(status_code=404, detail=f"No type definition for '{content_type}'")
    return {
        "content_type": td.content_type,
        "server": td.server,
        "operations": {
            name: {"kind": op.kind, "target": op.target, "requires_grant": op.requires_grant}
            for name, op in td.operations.items()
        },
        "describer": td.describer,
    }


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
