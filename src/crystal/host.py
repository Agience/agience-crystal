"""CRYSTAL — condensation, routing: the persona host / gateway process.

The host grounds the tekton roster (personas = tektons, OPERATOR-ARCHITECTURE §12). Per
``agience-pharos/working/genesis/COMPONENTS.md``: crystal is "condensation, routing;
signal to content type" — the host is the routing layer (mount / gateway / relay / discovery /
registration-orchestration). chorus is "operators, by domain" and owns the persona modules.

This module owns the FastAPI app, lifespan, sub-app mounts, the universal MCP gateway
middleware (UUID -> slug routing), the ``/relay/v1/connect`` WebSocket, and ``/.well-known/mcp``
discovery. It holds no persona roster and imports no persona by name: chorus depends on crystal,
not the reverse, so importing a persona here would create a dependency cycle. Instead the host is
handed a list of persona providers (see :class:`Persona`); the chorus shim discovers chorus's own
persona modules and injects them via :func:`build_app`. Each persona owns its self-registration
(:meth:`Persona.register`), so the host holds no persona list of its own.

Endpoints (each persona serves at ``/<name>/mcp``): whatever the provider hands us — nothing is
hardcoded here. A persona may serve additional routes of its own (Astra's stream webhooks, for
example); those are part of the combined app the provider (chorus) builds, keeping this host
persona-agnostic.

Config:
  MCP_HOST   — bind host (default 0.0.0.0)
  MCP_PORT   — bind port (default 8082)
  LOG_LEVEL  — logging level (default INFO)
"""

from __future__ import annotations

import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional, Protocol, Sequence, runtime_checkable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from crystal import config as _config

#: RFC 9728. A property of the origin (scheme+host), which is why `_route_by_host` exempts the
#: `/.well-known/` prefix from its persona rewrite — see the note there.
_PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"

log = logging.getLogger("crystal.host")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s - %(name)s - %(message)s",
)
# Suppress noisy per-request httpx logs (dozens per chat turn).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# The persona provider surface.
#
# The host consumes this duck-typed interface; it never imports a persona. Real
# providers are built by the chorus shim (chorus/src/personas.py) from the persona
# modules; tests supply fakes. All attributes are read with getattr + defaults so a
# minimal fake (name + mount_app + register) works.
# ---------------------------------------------------------------------------
@runtime_checkable
class Persona(Protocol):
    name: str          # slug, e.g. "aria"
    role: str          # human role, e.g. "Presentation & Interface"
    endpoint: str      # MCP path, e.g. "/aria/mcp"
    mount_app: Any     # ASGI app to mount at /<name> (transport_security already cleared)

    # Optional: session manager whose .run() must be entered for the process lifetime.
    session_manager: Optional[Any]

    async def startup(self) -> None: ...
    def register(self) -> bool: ...


def _persona_slugs(personas: Sequence[Any]) -> set[str]:
    return {p.name for p in personas}


def _discovery_entries(personas: Sequence[Any]) -> list[dict]:
    return [
        {
            "name": p.name,
            "endpoint": getattr(p, "endpoint", f"/{p.name}/mcp"),
            "role": getattr(p, "role", ""),
        }
        for p in personas
    ]


# ---------------------------------------------------------------------------
# Self-registration (the inversion). The host holds no roster: it iterates the
# personas it was handed and calls each one's own register(), which is the source
# of truth for that persona's {name, role, endpoint, types}. Passive-Mantle retry/
# backoff behavior is preserved — Mantle/gateway learn a persona only from its push.
# ---------------------------------------------------------------------------
async def _self_register_all(personas: Sequence[Any]) -> None:
    import asyncio

    remaining = [p for p in personas if getattr(p, "register", None) is not None]
    delay = 2.0
    for _attempt in range(15):
        if not remaining:
            return
        done: list[Any] = []
        for p in list(remaining):
            try:
                from crystal.persona_registration import register_persona
                ok = await asyncio.to_thread(p.register, register_persona)
            except Exception:  # noqa: BLE001 — any failure just means retry
                ok = False
            if ok:
                done.append(p)
        for p in done:
            remaining.remove(p)
        if not remaining:
            log.info("All personas self-registered with Mantle + gateway")
            return
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 15.0)
    if remaining:
        log.warning(
            "Persona self-registration incomplete for: %s",
            ", ".join(getattr(p, "name", "?") for p in remaining),
        )


def build_app(personas: Sequence[Any]) -> FastAPI:
    """Build the host FastAPI app around the given persona providers.

    ``personas`` is the only source of the roster: mounts, gateway slug set, discovery,
    and self-registration all derive from it. Nothing about any specific persona is
    hardcoded here.
    """
    personas = list(personas)

    # Lazily import the beam/crystal plumbing so `import crystal.host` stays cheap and
    # so a missing optional dep surfaces at build time, not import time.
    from crystal.mantle_client import get_gateway_client
    from crystal.gateway_middleware import PersonaMap, UniversalMCPGatewayMiddleware

    _persona_map = PersonaMap(gateway_client_factory=get_gateway_client)

    _session_managers = [
        p.session_manager for p in personas if getattr(p, "session_manager", None) is not None
    ]
    _startup_fns = [p.startup for p in personas if getattr(p, "startup", None) is not None]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        # A client hanging up is not an error. On Windows the Proactor transport calls
        # `socket.shutdown()` while tearing down a connection the peer already closed, which raises
        # `ConnectionResetError` (WinError 10054) inside an asyncio callback; asyncio has nowhere to
        # return it, so it reaches the default exception handler and prints at ERROR with a full
        # stack — for a browser closing a tab.
        #
        # An operator reading that line cannot tell it from a real fault without knowing the
        # platform quirk, which is what lets a genuine ERROR get skimmed past. Nothing is suppressed
        # broadly — only this exact shape (a connection-reset with no live task to receive it) is
        # demoted to debug; every other unhandled exception still reaches the default handler
        # untouched.
        def _quiet_peer_disconnect(loop, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError) and context.get("future") is None:
                log.debug("peer disconnected mid-teardown (%s)", exc)
                return
            loop.default_exception_handler(context)

        try:
            asyncio.get_running_loop().set_exception_handler(_quiet_peer_disconnect)
        except Exception:                       # never let logging hygiene block a boot
            pass

        for startup_fn in _startup_fns:
            await startup_fn()
        # Self-register (server + types, per persona) and push each persona's OWN operator manifest in
        # the background so persona serving isn't delayed while Mantle comes up. No shared catalog: the
        # host registers whatever operators the persona bindings carry (chorus owns the manifests; crystal
        # imports no tekton by name).
        _reg_task = asyncio.create_task(_self_register_all(personas))
        from crystal.push import register_manifests_with_retry as _reg_ops

        _manifest_ops = [op for p in personas for op in getattr(p, "operators", []) or []]
        _operators_task = asyncio.create_task(_reg_ops(_manifest_ops))
        async with contextlib.AsyncExitStack() as stack:
            for sm in _session_managers:
                await stack.enter_async_context(sm.run())
            try:
                yield
            finally:
                for _t in (_reg_task, _operators_task):
                    _t.cancel()
                    with contextlib.suppress(Exception):
                        await _t

    app = FastAPI(title="Agience Crystal Host", lifespan=lifespan)

    # -- Universal MCP gateway middleware (UUID -> slug routing) --------------
    from prism.trust.authority_trust import verify_jwt as _verify_jwt
    from crystal.relay_manager import get_relay_manager

    _relay_manager = get_relay_manager()

    def _resolve_user_id_from_scope(scope: dict) -> Optional[str]:
        """Resolve the verified ``sub`` from the inbound JWT for relay dispatch.

        The relay path returns without invoking any persona middleware, so this is the
        only place the caller identity is verified for relay dispatch — it must verify the
        signature (never trust an unsigned payload). A forged ``sub`` that fails signature
        verification yields None, which the middleware renders as 401.
        """
        headers = scope.get("headers") or []
        raw_auth = ""
        for k, v in headers:
            if k.lower() == b"authorization":
                raw_auth = v.decode("latin-1", errors="ignore")
                break
        if not raw_auth.lower().startswith("bearer "):
            return None
        token = raw_auth[7:].strip()
        if not token:
            return None
        try:
            claims = _verify_jwt(token, expected_issuer_service="origin")
        except Exception:
            return None
        sub = claims.get("sub")
        return str(sub) if sub else None

    app.add_middleware(
        UniversalMCPGatewayMiddleware,
        persona_map=_persona_map,
        gateway_client_factory=get_gateway_client,
        local_persona_slugs=_persona_slugs(personas),
        relay_manager=_relay_manager,
        user_id_resolver=_resolve_user_id_from_scope,
    )

    # -- Relay WebSocket endpoint — desktop runtime connects here ------------
    from fastapi import WebSocket, WebSocketDisconnect
    from jose.exceptions import JWTError

    @app.websocket("/relay/v1/connect")
    async def relay_connect(websocket: WebSocket) -> None:
        raw_auth = websocket.headers.get("authorization", "")
        if not raw_auth.lower().startswith("bearer "):
            await websocket.close(code=4401, reason="Missing bearer token")
            return
        token = raw_auth[7:].strip()
        try:
            claims = _verify_jwt(token, expected_issuer_service="origin")
        except (KeyError, JWTError):
            await websocket.close(code=4401, reason="Invalid bearer token")
            return
        user_id = claims.get("sub")
        if not user_id:
            await websocket.close(code=4401, reason="Token missing sub")
            return
        session = await _relay_manager.connect(websocket, user_id=str(user_id))
        try:
            while True:
                envelope = await websocket.receive_json()
                await _relay_manager.handle_response_envelope(session.session_id, envelope)
        except WebSocketDisconnect:
            pass
        finally:
            await _relay_manager.disconnect(session.session_id)

    # -- Facet surfaces (`web.serve`) — mounted first -----------------------
    # Order is load-bearing: Starlette matches routes in order, so `/aria` mounted first would
    # swallow `/aria/www` whole and the surface would be declared, logged and unreachable. See
    # `crystal/web_serve.py` for what is and is not served (only a facet carrying a built `dist`;
    # a rendered facet is the persona's act and keeps resolving to the persona mount).
    from crystal import web_serve as _web_serve

    _surfaces = _web_serve.mount_surfaces(app, personas)

    # -- Mounts (each provider supplies its own mount app) ------------------
    for p in personas:
        app.mount(f"/{p.name}", p.mount_app)

    # -- Host-header (subdomain) routing: `<name>.<base>` -> the SAME mount ---
    # `agience-pharos/genesis/WEB-FROM-THE-NETWORK.md` §1: a named entity is addressed by its name everywhere —
    # you reach Lumen, not "the chat service" — so a subdomain deployment needs a place to land
    # alongside path routing.
    #
    # The subdomain is translated into the path prefix the mounts already answer on, so there is
    # exactly one routing table — the mounts above — rather than a parallel host->app dispatch map,
    # which would be a second registry of the same facts, free to drift from the mounts. Both
    # addresses reach the same app object.
    #
    # `personas` is the roster's only source; deriving the accepted names from it means adding a
    # persona cannot forget to add its subdomain, and nothing here hardcodes a name list.
    _base = (os.getenv("CRYSTAL_HOST_DOMAIN") or "").strip().lower().lstrip(".")
    _names = {p.name for p in personas}

    # A persona's own facet manifest is the source of its subdomains, when it declares one.
    # `aria/manifest.py` declares `FACETS[*]["subdomains"] = ["www", "aria", "@"]` as the
    # declarative source-of-truth for this router. Deriving subdomains only from persona names
    # would make that declaration decorative and give the same fact two registries free to drift.
    # So declared subdomains win, and a persona's own name is the fallback for a persona that
    # declares nothing. `@` means the apex.
    #
    # A persona only reaches us with facets if the binding layer carries them
    # (`PersonaBinding.facets`); absent that attribute this degrades to name-routing, so the two
    # are independent and neither half breaks alone.
    #
    # `web_serve.read_facets` performs the tolerant read this loop needs (callable-or-list, a
    # raising `facets()` costs the persona its facets and nothing more), and `web.serve` reads the
    # same roster through that one function rather than a second copy of the same logic.
    _declared: dict = {}
    _declared_apex = None
    for p in personas:
        for f in _web_serve.read_facets(p):
            for sub in (f.get("subdomains") or ()):
                s = str(sub).strip().lower()
                if s == "@":
                    _declared_apex = _declared_apex or p.name
                elif s:
                    _declared.setdefault(s, p.name)

    # `www`/apex are the public face -> aria by default (WEB-FROM-THE-NETWORK §1), overridable by
    # env, but a declared `@` wins over the default because the persona said so. Honoured only if
    # the target is really mounted: naming a persona that was never mounted would 404 through the
    # rewrite and read as a routing bug rather than a missing persona.
    _apex_to = (_declared_apex or os.getenv("CRYSTAL_APEX_PERSONA", "aria")).strip().lower()
    _apex_target = _apex_to if _apex_to in _names else None

    def _persona_for_host(host: str):
        """`<name>.<base>` -> name; `www.<base>`/`<base>` -> the apex persona; anything else -> None.

        None leaves the request alone — an unknown host falls through to normal path routing rather
        than being guessed at. A rewrite is only ever applied to a name that is really mounted.
        """
        if not _base or not host:
            return None
        h = host.split(":", 1)[0].strip().lower().rstrip(".")     # drop port, normalise
        if h == _base or h == "www." + _base:
            return _apex_target
        if h.endswith("." + _base):
            label = h[: -(len(_base) + 1)]
            # There is no separate depth check. `a.b.<base>` yields the label `"a.b"`, and
            # `evil.lumen.<base>` yields `"evil.lumen"` — neither is a declared subdomain nor a
            # persona name, so the membership tests below already reject them
            # ([[verification-that-cannot-fail]]). Membership is the depth guard, because both a
            # persona name and a declared subdomain are single labels.
            declared = _declared.get(label)
            if declared:
                return declared if declared in _names else None
            return label if label in _names else None
        return None

    # ── The proxy tekton — hosts this crystal fronts but does not mount ────────────────────────
    # Some names under the base are served by a process that is not a mounted persona — origin and
    # mantle are their own services. `Host -> upstream` is the same condensation as `Host -> persona`,
    # so it is routed here rather than in a separate component.
    #
    # Declared, not discovered: `CRYSTAL_UPSTREAMS="origin=127.0.0.1:8080,mantle=127.0.0.1:8082"`.
    # A subdomain with no persona and no upstream still resolves to None and is left alone — an
    # unknown name is never guessed at, whichever table it missed.
    _upstreams: dict = {}
    for _pair in (os.getenv("CRYSTAL_UPSTREAMS") or "").split(","):
        _pair = _pair.strip()
        if "=" in _pair:
            _label, _, _addr = _pair.partition("=")
            _label, _addr = _label.strip().lower(), _addr.strip()
            if _label and _addr and _label not in _names:   # a mounted persona always wins
                _upstreams[_label] = _addr
    if _upstreams:
        log.info("crystal proxy tekton: %s", ", ".join(f"{k}->{v}" for k, v in sorted(_upstreams.items())))

    def _upstream_for_host(host: str):
        """`<label>.<base>` -> "host:port" for a declared upstream, else None."""
        if not _base or not host or not _upstreams:
            return None
        h = host.split(":", 1)[0].strip().lower().rstrip(".")
        if not h.endswith("." + _base):
            return None
        return _upstreams.get(h[: -(len(_base) + 1)])

    @app.middleware("http")
    async def _proxy_upstream(request, call_next):
        """Forward a declared-upstream host verbatim. The Host header is preserved so the upstream
        sees the name the caller used — origin mints tokens whose `iss`/`aud` depend on it, and
        rewriting it to 127.0.0.1 turns a working node into 'Invalid token audience'."""
        addr = _upstream_for_host(request.headers.get("host", ""))
        if not addr:
            return await call_next(request)
        import httpx
        from starlette.responses import Response

        url = f"http://{addr}{request.url.path}"
        if request.url.query:
            url += "?" + request.url.query
        # `host` is kept; hop-by-hop headers are not forwarded (they describe this connection).
        fwd = {k: v for k, v in request.headers.items()
               if k.lower() not in ("connection", "keep-alive", "transfer-encoding", "upgrade")}
        try:
            async with httpx.AsyncClient(timeout=60.0) as cx:
                up = await cx.request(request.method, url, headers=fwd,
                                      content=await request.body())
        except httpx.HTTPError as exc:
            # A proxy that cannot reach its upstream names which upstream, in both the log and the
            # response body — a bare 502 gives an operator nothing to act on.
            log.warning("upstream %s unreachable: %s", addr, exc)
            return Response(f"upstream {addr} unreachable", status_code=502,
                            media_type="text/plain")
        drop = {"connection", "keep-alive", "transfer-encoding", "upgrade", "content-encoding",
                "content-length"}
        return Response(content=up.content, status_code=up.status_code,
                        headers={k: v for k, v in up.headers.items() if k.lower() not in drop},
                        media_type=up.headers.get("content-type"))

    # ── Which subdomains reach a served surface, rather than the persona mount ──────────────────
    # A facet subdomain (`www`, and the apex) addresses the facet: it rewrites to the surface's own
    # path, `/aria/www`. A persona slug (`aria`) still addresses the persona — excluded from
    # `web_serve.subdomain_map`, because `aria.<base>/mcp` reaching a file server that does not have
    # the file is a regression, not a feature. A declared facet with no built surface is absent from
    # this map entirely, so its subdomain resolves to the persona — `login` and `library` are
    # declared, rendered rather than static, and reach the persona this way.
    _facet_prefix = _web_serve.subdomain_map(personas, _surfaces)

    def _facet_for_host(host: str):
        """The served surface for this Host, or None to use the persona mount."""
        if not _base or not host or not _facet_prefix:
            return None
        h = host.split(":", 1)[0].strip().lower().rstrip(".")
        if h == _base or h == "www." + _base:
            return _facet_prefix.get("www") or _facet_prefix.get("@")
        if h.endswith("." + _base):
            return _facet_prefix.get(h[: -(len(_base) + 1)])
        return None

    @app.middleware("http")
    async def _route_by_host(request, call_next):
        target = _persona_for_host(request.headers.get("host", ""))
        if target:
            path = request.scope.get("path", "/")
            persona_prefix = "/" + target
            surface = _facet_for_host(request.headers.get("host", ""))
            prefix = surface.prefix if surface else persona_prefix
            # A static facet's own address is a directory, so the root request keeps its trailing
            # slash: rewriting `/` to `/aria/www` 404s (Starlette's Mount matches it and hands
            # StaticFiles an empty path) while `/aria/www/` serves index.html. A rendered surface is
            # a route, not a directory — `/sage/library/` is not `/sage/library` and only survives
            # on a redirect. The persona case is left exactly as it was (`/` -> `/aria`, no slash),
            # since that is a mount prefix and changing it would move every path-addressed
            # deployment. Three shapes, and the surface says which it is.
            root = "/" if (surface and surface.kind == "static") else ""
            # Idempotent, and anchored on the persona prefix even when rewriting to a facet. A
            # request already carrying `/aria` is already persona-addressed — prefixing it again
            # gives `/aria/www/aria/mcp`, which is the `/lumen/lumen/...` defect wearing a facet.
            # So the guard tests the persona prefix, never the facet one.
            # `/.well-known/*` belongs to the host, not the persona mount, and has to be exempt from
            # the rewrite or it cannot be served at all: rewriting
            # `aria.<base>/.well-known/oauth-protected-resource` to `/aria/.well-known/…` 404s,
            # making the one document that tells a client where to authenticate unreachable on
            # exactly the hosts that need it. RFC 8615 defines these paths as properties of the
            # origin (scheme+host), which is precisely what this router has just finished resolving.
            if path.startswith("/.well-known/"):
                pass
            elif path != persona_prefix and not path.startswith(persona_prefix + "/"):
                request.scope["path"] = prefix + (root if path == "/" else path)
                raw = request.scope.get("raw_path")
                if raw:
                    request.scope["raw_path"] = prefix.encode() + (root.encode() if path == "/" else raw)
        return await call_next(request)

    # -- Discovery / index / health ----------------------------------------
    @app.get("/")
    def index():
        return {
            "service": "crystal-host",
            "port": int(os.getenv("MCP_PORT", "8082")),
            "servers": _discovery_entries(personas),
        }

    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok"}

    @app.get(_PROTECTED_RESOURCE_PATH, include_in_schema=False)
    def oauth_protected_resource(request: Request):
        """RFC 9728 — where a client authenticates to reach this persona.

        The entry point to delegation. A tekton acts for a person by exchanging that person's token
        for an RFC 8693 delegation (`prism.trust.server_auth` → Origin `/internal/delegation-token`),
        and an organon touching a caller-supplied resource id carries no authority without one —
        verified on sage. The token has to come from somewhere, and a client with no way to discover
        the authorization server has nowhere to get it; this document is what gives it one.

        The resource is derived from the request, deliberately. One host process answers for many
        personas by `Host` header — `aria.<base>` and `sage.<base>` are different protected
        resources sharing one authorization server — so a constant here would name one of them for
        all of them, and an audience check downstream would then reject the tokens it minted.

        Public by necessity: a document you must be authenticated to read cannot tell you how to
        authenticate. It carries no secret — the issuer and the resource URL are both about to be
        disclosed by the redirect anyway — and it describes rather than decides: nothing reads it
        back, and verification stays with the persona's own `ServerAuth`.
        """
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("host", "") or request.url.netloc
        resource = f"{scheme}://{host}".rstrip("/")
        doc = {
            "resource": resource,
            "bearer_methods_supported": ["header"],
        }
        issuer = (getattr(_config, "ORIGIN_URI", "") or "").rstrip("/")
        # Omitted, not empty, when no issuer is configured. `[]` is a positive claim that there is
        # nowhere to authenticate; the key's absence says this node has not been configured to say.
        if issuer:
            doc["authorization_servers"] = [issuer]
        return JSONResponse(content=doc, headers={"Cache-Control": "public, max-age=300"})

    @app.exception_handler(StarletteHTTPException)
    async def _challenge_on_401(request: Request, exc: StarletteHTTPException):
        """Attach the discovery pointer to every 401 this host emits, in one place rather than at
        each raise site.

        A resource discoverable down some paths and not others is worse than one that is not
        discoverable at all: the first client to take the wrong path concludes the server is broken.
        Mantle's handler carries the same rule: FastAPI's own `HTTPBearer` raises 401 with a bare
        `WWW-Authenticate: Bearer`, so a guard keyed on the header merely existing would suppress the
        useful challenge on exactly the routes that use the security scheme. The test is
        `resource_metadata`, never presence.
        """
        headers = dict(getattr(exc, "headers", None) or {})
        if exc.status_code == 401:
            key = next((k for k in headers if k.lower() == "www-authenticate"), None)
            existing = (headers.pop(key) if key else "").strip()
            if "resource_metadata=" not in existing:
                scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
                host = request.headers.get("host", "") or request.url.netloc
                pointer = f'resource_metadata="{scheme}://{host}{_PROTECTED_RESOURCE_PATH}"'
                params = (existing[len("Bearer"):].strip().lstrip(",").strip()
                          if existing[:6].lower() == "bearer" else "")
                headers["WWW-Authenticate"] = f"Bearer {params}, {pointer}" if params else f"Bearer {pointer}"
            else:
                headers["WWW-Authenticate"] = existing
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=headers
        )

    @app.get("/.well-known/mcp")
    def well_known_mcp():
        """Per-deployment discovery: slug routes + each persona's artifact UUID (from Mantle)."""
        if not _persona_map.loaded:
            _persona_map.refresh()
        uuid_map = {e["slug"]: e["artifact_id"] for e in _persona_map.all_personas()}
        enriched = [
            {**p, "artifact_id": uuid_map.get(p["name"])} for p in _discovery_entries(personas)
        ]
        return {
            "service": "crystal-host",
            "transport": "streamable-http",
            "discovery": {"personas": enriched, "uuid_routing_enabled": bool(uuid_map)},
        }

    return app


def discover_personas() -> list[Any]:
    """Discover persona providers from installed ``agience.personas`` entry points.

    This is the standalone (`python -m crystal.host`) path. It imports no persona by name;
    a provider package advertises itself via an entry point in the ``agience.personas`` group
    whose value resolves to a zero-arg callable returning a list of :class:`Persona` providers.
    Returns ``[]`` when nothing is installed — the supported production entrypoint is the
    chorus shim, which injects its personas via :func:`build_app` directly.
    """
    try:
        from importlib.metadata import entry_points
    except Exception:
        return []
    providers: list[Any] = []
    try:
        eps = entry_points(group="agience.personas")
    except TypeError:  # Python <3.10 selectable API
        eps = entry_points().get("agience.personas", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            factory = ep.load()
            providers.extend(list(factory()))
        except Exception as exc:  # noqa: BLE001
            log.warning("persona entry point %r failed to load: %s", getattr(ep, "name", ep), exc)
    return providers


def run(
    personas: Optional[Sequence[Any]] = None,
    *,
    app: Optional[FastAPI] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Build (if needed) and serve the host with uvicorn.

    Pass either a prebuilt ``app`` or a ``personas`` list. The chorus shim passes ``app=``
    (already built from its injected personas); ``python -m crystal.host`` passes neither and
    falls back to entry-point discovery.
    """
    import uvicorn

    # crystal is Apache-2.0 and origin is AGPL (copyleft); importing origin's logging helper here
    # would create an Apache→AGPL import arrow, which §1's licence requirement rules out since the
    # AGPL components are sinks. See `crystal/logging_config.py` for the copied helper and what of
    # it was left out. Origin is still reached — over the wire at ORIGIN_URI, a service call, not
    # an import.
    from crystal.logging_config import build_log_config

    if app is None:
        app = build_app(personas if personas is not None else discover_personas())
    bind_host = host or os.getenv("MCP_HOST", "0.0.0.0")
    bind_port = port if port is not None else int(os.getenv("MCP_PORT", "8082"))

    # ── TLS, from the same env the deploy already exports ────────────────────────────────────────
    # `agience-cloud/scripts/service_common.sh` resolves `CRYSTAL_TLS_CERT`/`_KEY` (the Caddy-obtained wildcard for
    # `CRYSTAL_HOST_DOMAIN`) and exports them, then passes `--ssl-certfile` to the uvicorn CLI. This
    # entry point reads the same declared variables, so a host started here serves HTTPS on the same
    # certificate as one started through that script.
    #
    # A cert that is named and missing raises rather than degrading to plaintext: a cert that is
    # named means TLS was intended, and falling back to plaintext there would serve the front door
    # in the clear while the log said the host was up. Absent variables are a plain HTTP host, which
    # is a different and stated thing.
    _cert = (os.getenv("CRYSTAL_TLS_CERT") or "").strip()
    _key = (os.getenv("CRYSTAL_TLS_KEY") or "").strip()
    _tls = {}
    if _cert or _key:
        missing = [n for n, v in (("CRYSTAL_TLS_CERT", _cert), ("CRYSTAL_TLS_KEY", _key)) if not v]
        missing += [v for v in (_cert, _key) if v and not os.path.isfile(v)]
        if missing:
            raise RuntimeError(
                "TLS was asked for and cannot be served: %s. A host that quietly fell back to "
                "plaintext here would serve the front door in the clear while reporting healthy."
                % ", ".join(missing))
        _tls = {"ssl_certfile": _cert, "ssl_keyfile": _key}

    log.info("Starting crystal host — host=%s port=%s tls=%s", bind_host, bind_port, bool(_tls))
    uvicorn.run(app, host=bind_host, port=bind_port, access_log=True,
                log_config=build_log_config(), **_tls)


if __name__ == "__main__":
    run()
