"""``web.serve`` — the host capability that serves a declared facet's surface.

A facet is a conduit whose far side is a browser. `crystal/host.py` reads
`FACETS[*]["subdomains"]` so that `www.<base>` and `library.<base>` resolve to a persona; on their
own they land on the persona's MCP mount, which answers `/mcp` and nothing else. The facet layer
routes; this module is what serves a built static bundle at that same address.

The persona's own manifest is the source, exactly as it is for the router. `aria/manifest.py`
declares `"dist": "www/dist"` on its `www` facet, and calls that same roster the
*"declarative source-of-truth for the host→facet router"*. This module reads the same list. Nothing
here names a persona, a facet or a directory — a facet added there is served by construction, and
one that is not declared has no address.

Serving is the host's job and not the persona's. aria's own `CRYSTAL` block states it: *"Serving
the static facet is a host capability (`web.serve`), not aria's."* A persona that served its own
bytes would be answering at an address only the host knows, and the host is the one component that
sees every persona at once.

What is and is not served here
-------------------------------
Only a facet carrying a ``dist`` — a built static bundle — has a surface this module can serve. A
facet whose surface is rendered (sage's `library`, condensed by `op.canon.browse`; aria's `login`,
which converts a surface into a principal) has no `dist`, is not served here, and its subdomain
keeps resolving to the persona. Rendering is the persona's act; inventing a static bundle for it
here would serve an empty directory at the front door.

Every gap is named
-------------------
A declared surface that cannot be served is logged with the resolved path and the reason, so a
front door that 404s despite a declared manifest still shows which half — the declaration or the
build — is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Optional, Sequence

log = logging.getLogger("crystal.web_serve")

#: Extensions a browser asks for as an ASSET and never as a route. A request for one of these that
#: is not on disk keeps its 404.
#:
#: The fallback below must not reach these. Answering a missing `.js` with index.html serves HTML
#: under a script URL: the browser caches it against the hashed filename, the page fails to parse
#: it, and the failure survives the deploy that fixed the build — a 404 says "this asset is
#: missing" and is the honest answer. The facets' own `nginx.conf` (`astra/web/nginx.conf`) makes
#: exactly this call for exactly this reason and names the same extensions; this is the same rule
#: on the path that actually serves them here.
_ASSET_SUFFIXES = frozenset({
    ".js", ".mjs", ".cjs", ".css", ".map", ".json", ".wasm",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".webm", ".ogg", ".wav", ".pdf", ".txt", ".xml", ".webmanifest",
})


class Surface(NamedTuple):
    """One servable facet surface: WHERE it answers, and what answers there."""

    persona: str
    facet: str
    prefix: str                  # the path it answers at, e.g. "/aria/www" or "/sage/library"
    kind: str                    # "static" (a built dist, mounted here) | "rendered" (the persona's route)
    directory: Optional[Path]    # the resolved static bundle; None for a rendered surface
    subdomains: tuple            # the names that address it (persona slugs excluded — see `subdomain_map`)


def read_facets(persona: Any) -> list:
    """This persona's declared facets, read tolerantly.

    One reader, used by both consumers: the host-header router and this module both need the
    facet roster, so a shared getattr/callable/except dance avoids the two-copy drift this
    codebase deletes elsewhere.

    A broken `facets()` is a finding, never a boot crash — the same rule `_persona_manifest`
    follows. A persona whose manifest raises loses its facets and keeps its mount.
    """
    facets = getattr(persona, "facets", None)
    if callable(facets):
        try:
            facets = facets()
        except Exception:  # noqa: BLE001 — a broken manifest must not take the host down
            facets = None
    return [f for f in (facets or ()) if isinstance(f, dict)]


def _resolve_dist(persona: Any, name: str, facet: dict) -> Optional[Path]:
    """Resolve a facet's declared ``dist`` under the persona's own directory, or say why it could
    not be resolved, naming the facet.

    ``dist`` is declared relative to the persona dir (aria: ``"www/dist"``), so the host needs the
    root to resolve it against. The persona binding carries it as ``dist_root``; a provider that
    carries none cannot have a static surface resolved, and says so rather than guessing at a cwd.
    """
    rel = str(facet.get("dist") or "").strip()
    if not rel:
        return None                              # not a static surface — nothing to refuse

    root = getattr(persona, "dist_root", None)
    if not root:
        log.warning(
            "web.serve: facet %s/%s declares dist=%r but the persona carries no dist_root — "
            "the surface cannot be resolved (a cwd-relative guess would serve whatever the "
            "process happened to start in)", name, facet.get("name"), rel)
        return None

    root = Path(root).resolve()
    target = (root / rel).resolve()

    # Containment, and it is the security half. A declared `"../../etc"` — or an absolute path —
    # would publish bytes from outside the persona to the open internet. The manifest is trusted to
    # name a surface, never to choose where on the filesystem it comes from.
    if target != root and root not in target.parents:
        log.error("web.serve: facet %s/%s dist %r escapes the persona dir (%s) — REFUSED",
                  name, facet.get("name"), rel, target)
        return None

    if not target.is_dir():
        # `www/dist` is a gitignored Vite output, so an unbuilt bundle is the ordinary state of
        # a fresh checkout; the warning names the missing build rather than leaving the front
        # door to 404 with no stated cause.
        log.warning("web.serve: facet %s/%s declares dist %s, which is not built — not served",
                    name, facet.get("name"), target)
        return None

    if not (target / "index.html").is_file():
        # Served anyway: the assets under it are real bytes at real paths. Reported because a
        # browser asking for the facet's own address gets a 404 and the cause is one missing file.
        log.warning("web.serve: facet %s/%s dist %s has no index.html — its own address will 404",
                    name, facet.get("name"), target)

    return target


def _renders_at(persona: Any, facet: str) -> bool:
    """Does this persona's own app really answer at ``/<facet>``? Measured, never declared.

    The discriminator is not static-vs-rendered — it is whether there is a surface. For a static
    facet that is measurable on disk (the dist exists). For a rendered one the equivalent
    measurement is the persona's route table: sage's `library` view is a route on sage's own app, so
    the host can look and see it.
    """
    want = "/" + facet
    for r in (getattr(getattr(persona, "mount_app", None), "routes", None) or ()):
        path = getattr(r, "path", None)
        if isinstance(path, str) and (path == want or path.startswith(want + "/")):
            return True
    return False


def surfaces(personas: Sequence[Any]) -> list:
    """Every declared facet surface that can actually be served, in roster order.

    The prefix is ``/<persona>/<facet>`` — a real path, so the surface is addressable both ways
    (by path and, via the host-header router, by name). That is the same *"rewrite, do not
    re-route"* rule the router is built on: one routing table, two addresses.

    A declared facet with nothing behind it is not a surface and is absent from this list. Its
    subdomain keeps resolving to the persona instead.
    """
    out: list = []
    for p in personas:
        name = getattr(p, "name", "")
        if not name:
            continue
        for f in read_facets(p):
            fname = str(f.get("name") or "").strip()
            if not fname:
                continue
            subs = tuple(str(s).strip().lower() for s in (f.get("subdomains") or ()) if str(s).strip())
            directory = _resolve_dist(p, name, f)
            if directory is not None:
                kind, where = "static", directory
            elif _renders_at(p, fname):
                kind, where = "rendered", None
            else:
                continue
            out.append(Surface(persona=name, facet=fname, prefix=f"/{name}/{fname}",
                               kind=kind, directory=where, subdomains=subs))
    return out


def subdomain_map(personas: Sequence[Any], surfaces_: Sequence[Surface]) -> dict:
    """``{subdomain -> Surface}`` for the host-header router. ``"@"`` means the apex.

    The surface, not just its prefix: the router needs the kind too, because a static surface's
    own address is a directory (`/aria/www/`) and a rendered one's is a route (`/sage/library`).
    Handing over the prefix alone would force the host to re-derive that, a second opinion about a
    fact this module already holds.

    A persona's own name is excluded on purpose. `aria` appears in the `www` facet's `subdomains`,
    and it is also the persona's slug — the address by which `aria.<base>/mcp` reaches the MCP
    endpoint. Mapping it to the static surface would send every MCP call on that host to a file
    server that does not have the file. The name addresses the persona; a facet subdomain
    addresses the facet. Both still reach the same mount table.
    """
    names = {getattr(p, "name", "") for p in personas}
    out: dict = {}
    for s in surfaces_:
        for sub in s.subdomains:
            if sub in names:
                continue                          # the slug belongs to the persona, not the facet
            out.setdefault(sub, s)
        if "@" in s.subdomains:
            out.setdefault("@", s)
    return out


def mount_surfaces(app: Any, personas: Sequence[Any]) -> list:
    """Mount every static surface on ``app`` and return all surfaces (static and rendered).

    Must be mounted before the persona mounts, by the caller: Starlette matches routes in order,
    and `/aria` mounted first would swallow `/aria/www` whole — the surface would exist, be
    declared, be logged as mounted, and never be reached.

    A rendered surface is not mounted here: it is already a route on the persona's own app, which
    the caller mounts at `/<persona>` immediately after. Mounting anything of our own at its prefix
    would shadow the persona's route with a host-owned one.
    """
    from starlette.exceptions import HTTPException as _StarletteHTTPException
    from starlette.staticfiles import StaticFiles

    class _SpaFiles(StaticFiles):
        """``StaticFiles`` plus the fallback a client-side router needs to be reachable at all.

        Why the fallback is not optional. Every `dist` served here is a
        bundler's output for an app that routes in the browser: `aria/www` and `astra/web` both
        build a `react-router` bundle, and a router's paths exist only once index.html has loaded
        and run. `StaticFiles(html=True)` serves index.html for a DIRECTORY and 404s everything
        else, so a deep link answers 404 and nothing behind it ever executes.

        MEASURED 2026-08-27 against the unchanged mount, aria's own fixture: `/aria/www/` 200,
        `/aria/www/assets/app.js` 200, `/aria/www/auth/callback` **404**. That last path is not a
        deep link anyone chose — it is the `redirect_uri` an OIDC Authorization Code flow lands on,
        so a facet mounted this way could not COMPLETE A SIGN-IN. The surface was reachable, the
        front page served, and every route inside it was 404 — which reads as a broken build rather
        than as a missing fallback.

        A missing ASSET keeps its 404; see `_ASSET_SUFFIXES` for why that half matters as much.
        """

        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except _StarletteHTTPException as exc:
                if exc.status_code != 404:
                    raise
                if PurePosixPath(path).suffix.lower() in _ASSET_SUFFIXES:
                    raise
                # index.html, at 200 — the router reads the path from the address bar itself.
                # Still 404s if the bundle has no index.html, which `_resolve_dist` already warned
                # about by name; inventing a body here would hide a broken build.
                return await super().get_response("index.html", scope)

    found = surfaces(personas)
    for s in found:
        if s.kind != "static":
            log.info("web.serve: facet %s/%s -> %s (rendered by the persona)", s.persona, s.facet, s.prefix)
            continue
        app.mount(s.prefix, _SpaFiles(directory=str(s.directory), html=True), name=f"facet-{s.persona}-{s.facet}")
        log.info("web.serve: facet %s/%s -> %s (%s)", s.persona, s.facet, s.prefix, s.directory)
    return found


__all__ = ["Surface", "read_facets", "surfaces", "subdomain_map", "mount_surfaces"]
