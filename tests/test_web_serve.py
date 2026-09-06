"""``web.serve`` — the host capability that serves a declared facet surface.

A surface exists only where something is behind it: a built `dist` directory on disk, or a real
route on the persona's own app. A facet declared with neither is not a surface; it is unbuilt work,
and a resolver that trusted the declaration alone would answer 404 at its address in a way that
reads as a routing bug rather than as unbuilt work.

Invariants this module holds:

  served, not declared    a surface requires something behind it, not just a manifest entry.
  mount order              a persona's own mount (`/aria`) must register after its facet mounts
                           (`/aria/www`), or the facet mount is swallowed whole — declared, logged
                           as mounted, and unreachable, which is visible only over HTTP.
  the slug is the persona  `aria` is both the persona's name and a `www` subdomain. The facet map
                           excludes the slug, so `aria.<base>/mcp` keeps reaching the persona
                           instead of a file server with no file for it.
  idempotent               a prefix rewrite is anchored on the persona prefix, never the facet one,
                           so an already-prefixed path under a facet Host does not double-rewrite.
  containment              the manifest names a surface; it never chooses a filesystem root. A
                           `dist` path that escapes the persona directory is not served.
  logged, not silent       an unbuilt dist (`www/dist` is a gitignored Vite output) is not served,
                           does not crash the host, and logs which half is missing rather than
                           404ing with no explanation.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crystal import web_serve


# ── fakes: the host's own docstring says name + mount_app + register is enough ──────────────────
def _persona(name: str, *, facets=None, dist_root=None):
    sub = FastAPI()

    @sub.get("/ping")
    def ping():
        return {"persona": name}

    @sub.get("/mcp")
    def mcp():
        return {"mcp": name}

    class P:
        pass

    p = P()
    p.name = name
    p.mount_app = sub
    p.operators = []
    if facets is not None:
        p.facets = facets
    if dist_root is not None:
        p.dist_root = dist_root
    return p


def _built_dist(tmp_path, *, persona="aria", rel="www/dist", body="<h1>aria</h1>"):
    """A persona dir with a built bundle in it — the shape aria really has on disk."""
    root = tmp_path / persona
    dist = root / rel
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(body, encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("export default 1;", encoding="utf-8")
    return root


def _aria(tmp_path, **kw):
    """aria as she is declared: a static `www` facet and a rendered `login` facet beside it."""
    root = _built_dist(tmp_path, **kw)
    return _persona(
        "aria",
        dist_root=root,
        facets=[
            {"name": "www", "persona": "aria", "subdomains": ["www", "aria", "@"], "dist": "www/dist"},
            # no `dist` — a login flow converts a surface into a principal; it condenses nothing and
            # has no static bundle. It must stay unserved here.
            {"name": "login", "persona": "aria", "subdomains": ["login", "auth"]},
        ],
    )


def _client(monkeypatch, personas, *, domain="agience.ai"):
    monkeypatch.delenv("CRYSTAL_APEX_PERSONA", raising=False)
    monkeypatch.delenv("CRYSTAL_UPSTREAMS", raising=False)
    if domain is None:
        monkeypatch.delenv("CRYSTAL_HOST_DOMAIN", raising=False)
    else:
        monkeypatch.setenv("CRYSTAL_HOST_DOMAIN", domain)
    host = importlib.import_module("crystal.host")
    return TestClient(host.build_app(personas))


# ── resolution: what is a surface, and what is not ──────────────────────────────────────────────

def test_a_declared_built_dist_becomes_a_surface(tmp_path):
    """The positive control. Without this the negatives below could all pass on a resolver that
    never returns anything at all."""
    found = web_serve.surfaces([_aria(tmp_path)])
    assert [(s.persona, s.facet, s.prefix, s.kind) for s in found] == [("aria", "www", "/aria/www", "static")]


def test_a_declared_facet_with_NOTHING_BEHIND_IT_is_not_a_surface(tmp_path):
    """`login` is declared with no `dist` and no route. A map built from declarations alone would
    still return an address for it, which reads as a routing bug rather than as unbuilt work."""
    found = web_serve.surfaces([_aria(tmp_path)])
    assert "login" not in {s.facet for s in found}


def test_an_unbuilt_dist_is_refused_BY_NAME(tmp_path, caplog):
    """A clean checkout has no `www/dist` (gitignored Vite output) — the ordinary state. The surface
    is not served, and the log names the bundle as unbuilt rather than leaving a front door that
    404s with no explanation."""
    p = _persona("aria", dist_root=tmp_path / "aria",
                 facets=[{"name": "www", "subdomains": ["www"], "dist": "www/dist"}])
    (tmp_path / "aria").mkdir()
    with caplog.at_level("WARNING", logger="crystal.web_serve"):
        found = web_serve.surfaces([p])
    assert found == []
    assert any("not built" in r.getMessage() for r in caplog.records), \
        "an unbuilt surface was skipped SILENTLY"


def test_a_dist_escaping_the_persona_dir_is_REFUSED(tmp_path, caplog):
    """A `dist` of `"../secrets"` would publish bytes from outside the persona directory to the
    open internet. The same manifest with a contained path is served instead, which is what shows
    this is the containment check and not an unrelated resolution failure."""
    root = _built_dist(tmp_path)
    outside = tmp_path / "secrets"
    outside.mkdir()
    (outside / "index.html").write_text("KEYS", encoding="utf-8")

    escaping = _persona("aria", dist_root=root,
                        facets=[{"name": "www", "subdomains": ["www"], "dist": "../secrets"}])
    with caplog.at_level("ERROR", logger="crystal.web_serve"):
        assert web_serve.surfaces([escaping]) == []
    assert any("escapes" in r.getMessage() for r in caplog.records), "the escape was refused silently"

    # the control: identical persona, contained path → served. Without this the assertion above
    # would pass on a resolver that serves nothing at all.
    contained = _persona("aria", dist_root=root,
                         facets=[{"name": "www", "subdomains": ["www"], "dist": "www/dist"}])
    assert len(web_serve.surfaces([contained])) == 1


def test_a_dist_with_no_dist_root_is_refused_rather_than_GUESSED(tmp_path, caplog):
    """With no `dist_root`, nothing is served. The alternative — a cwd-relative fallback — serves
    whatever directory the process happened to start in, and looks identical to working when the
    cwd is right."""
    p = _persona("aria", facets=[{"name": "www", "subdomains": ["www"], "dist": "www/dist"}])
    with caplog.at_level("WARNING", logger="crystal.web_serve"):
        assert web_serve.surfaces([p]) == []
    assert any("dist_root" in r.getMessage() for r in caplog.records)


def test_a_raising_facets_callable_costs_the_facets_and_nothing_else(tmp_path):
    """A broken manifest costs its own facets, not the whole host — the same rule
    `_persona_manifest` already follows: a broken manifest is a finding, never a boot crash."""
    p = _persona("aria", dist_root=tmp_path)

    def _boom():
        raise RuntimeError("bad manifest")

    p.facets = _boom
    assert web_serve.read_facets(p) == []
    assert web_serve.surfaces([p]) == []


# ── a rendered surface: the persona's own route, measured rather than declared ───────────────────

def _sage(*, renders=True):
    """sage as she is declared: `pharos`, a facet with no `dist` whose surface is a persona route.

    The name matches the address: the canon lives in `agience-pharos`.
    """
    p = _persona("sage", facets=[{"name": "pharos", "persona": "sage",
                                  "subdomains": ["pharos"],
                                  "bff_tekton": "op.canon.browse"}])
    if renders:
        @p.mount_app.get("/pharos")
        def pharos(locus: str = "", resolution: str = ""):
            return {"pharos": True, "locus": locus, "resolution": resolution}
    return p


def test_a_persona_route_IS_a_surface_even_with_no_dist():
    """The discriminator is not static-vs-rendered, it is whether there is a surface. A library
    view is condensed, not built — there is no directory to point at, and there is still something
    at the address."""
    found = web_serve.surfaces([_sage()])
    assert [(s.persona, s.facet, s.prefix, s.kind) for s in found] == \
        [("sage", "pharos", "/sage/pharos", "rendered")]
    assert found[0].directory is None


def test_the_SAME_facet_with_no_route_is_not_a_surface():
    """The control for the test above, and the one that makes the measurement real: an identical
    declaration with nothing serving it yields nothing. Without this pair, `surfaces()` returning a
    rendered entry would prove only that it read the manifest."""
    assert web_serve.surfaces([_sage(renders=False)]) == []


def test_a_rendered_surface_is_NOT_mounted_by_the_host():
    """The host mounts static bundles; it does not mount personas' rendered routes. A host route
    mounted at `/sage/pharos` would be registered first and would shadow the persona's own route,
    which would then never be reached."""
    app = FastAPI()
    found = web_serve.mount_surfaces(app, [_sage()])
    assert [s.kind for s in found] == ["rendered"]
    assert not [r for r in app.routes if getattr(r, "path", "") == "/sage/pharos"], \
        "the host mounted over a persona's own rendered route"


def test_a_rendered_facet_subdomain_reaches_the_persona_route(monkeypatch, tmp_path):
    """`pharos.<base>/` reaches sage's rendered view — locally `pharos.home.agience.ai`. The base
    comes from config; the label is the facet's declared name."""
    c = _client(monkeypatch, [_aria(tmp_path), _sage()])
    r = c.get("/", headers={"Host": "pharos.agience.ai"})
    assert r.status_code == 200 and r.json()["pharos"] is True


def test_a_rendered_surface_root_carries_NO_trailing_slash(monkeypatch, tmp_path):
    """The mirror of the static case: a static surface's own address is a directory
    (`/aria/www/`), and a rendered one's is a route (`/sage/pharos`). Rewriting `/` to
    `/sage/pharos/` would depend on a redirect, which a POST or a client that does not follow
    redirects breaks. The surface says which kind it is; the router does not guess."""
    c = _client(monkeypatch, [_sage()], domain="agience.ai")
    r = c.get("/", headers={"Host": "pharos.agience.ai"}, follow_redirects=False)
    assert r.status_code == 200, f"the rendered root took a redirect: {r.status_code}"


def test_the_NEED_travels_in_the_query_string(monkeypatch, tmp_path):
    """The zoom is the caller's, so it must survive the rewrite — every state of the library is an
    address."""
    c = _client(monkeypatch, [_sage()])
    r = c.get("/?locus=a/b.md&resolution=section", headers={"Host": "pharos.agience.ai"})
    assert r.json() == {"pharos": True, "locus": "a/b.md", "resolution": "section"}


def test_sages_own_slug_still_reaches_MCP(monkeypatch):
    """The slug rule holds for rendered surfaces too: `sage.<base>/mcp` is the persona, not the
    canon. (`sage` is not in pharos's subdomains, but the exclusion must not depend on that.)"""
    c = _client(monkeypatch, [_sage()])
    assert c.get("/mcp", headers={"Host": "sage.agience.ai"}).json() == {"mcp": "sage"}


# ── the subdomain map: which names reach the facet ──────────────────────────────────────────────

def test_the_persona_slug_is_excluded_from_the_facet_map(tmp_path):
    """The slug is the persona: `aria` is declared in the `www` facet's subdomains and is also the
    persona's own slug, and `aria.<base>/mcp` must keep reaching MCP. Mapping the slug to the
    static surface would silently break every MCP call on that host."""
    personas = [_aria(tmp_path)]
    m = web_serve.subdomain_map(personas, web_serve.surfaces(personas))
    assert m["www"].prefix == "/aria/www"
    assert m["@"].prefix == "/aria/www"
    assert "aria" not in m, "the persona slug was mapped to a facet surface"


def test_a_rendered_facets_subdomain_is_absent_from_the_map(tmp_path):
    """`login`/`auth` resolve to the persona mount: an unbuilt surface must not capture an address
    it cannot answer at."""
    personas = [_aria(tmp_path)]
    m = web_serve.subdomain_map(personas, web_serve.surfaces(personas))
    assert "login" not in m and "auth" not in m


# ── over HTTP: the half that only fails when it is really mounted ───────────────────────────────

def test_the_surface_answers_at_its_own_path(monkeypatch, tmp_path):
    """Mount order: `/aria` mounted before `/aria/www` would swallow the facet mount whole, leaving
    the surface declared, logged, and unreachable — invisible to a unit test and visible only over
    HTTP."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    r = c.get("/aria/www/")
    assert r.status_code == 200 and "<h1>aria</h1>" in r.text
    assert c.get("/aria/www/assets/app.js").status_code == 200


def test_the_persona_mount_still_answers_underneath_it(monkeypatch, tmp_path):
    """The other half of mount order: serving the facet must not shadow the persona. `/aria/mcp` is
    the address the whole MCP surface lives at."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    assert c.get("/aria/mcp").json() == {"mcp": "aria"}
    assert c.get("/aria/ping").json() == {"persona": "aria"}


def test_the_facet_subdomain_reaches_the_surface(monkeypatch, tmp_path):
    """The point of the whole exercise: the front door serves the front page."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    r = c.get("/", headers={"Host": "www.agience.ai"})
    assert r.status_code == 200 and "<h1>aria</h1>" in r.text
    assert c.get("/assets/app.js", headers={"Host": "www.agience.ai"}).status_code == 200


def test_the_apex_reaches_the_surface(monkeypatch, tmp_path):
    """`@` is declared, so the bare apex is the public face — both spellings, neither a special case
    of the other."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    assert "<h1>aria</h1>" in c.get("/", headers={"Host": "agience.ai"}).text


def test_the_persona_slug_subdomain_still_reaches_MCP(monkeypatch, tmp_path):
    """`aria.agience.ai/mcp` must stay a working address. The slug exclusion is what keeps it that
    way, and it can only be measured over HTTP."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    assert c.get("/mcp", headers={"Host": "aria.agience.ai"}).json() == {"mcp": "aria"}
    assert c.get("/ping", headers={"Host": "aria.agience.ai"}).json() == {"persona": "aria"}


def test_a_facet_rewrite_is_still_IDEMPOTENT(monkeypatch, tmp_path):
    """A front proxy that also rewrites, or a client using both addressing styles, can send an
    already-prefixed path (`/aria/mcp`) under a facet Host. The guard tests the persona prefix,
    never the facet one, or the path would double to `/aria/www/aria/mcp`."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    assert c.get("/aria/mcp", headers={"Host": "www.agience.ai"}).json() == {"mcp": "aria"}


def test_a_rendered_facet_subdomain_still_lands_on_the_persona(monkeypatch, tmp_path):
    """`login` is declared and has no surface. It resolves to the persona mount, never to aria's
    website, which would be a wrong answer dressed as a working one."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    assert c.get("/ping", headers={"Host": "login.agience.ai"}).json() == {"persona": "aria"}
    r = c.get("/", headers={"Host": "login.agience.ai"})
    assert "<h1>aria</h1>" not in r.text, "the login subdomain was served aria's website"


def test_a_persona_with_no_facets_is_entirely_unchanged(monkeypatch, tmp_path):
    """lumen has no facets and routes as any persona with no declared facets does: this module must
    not alter personas that declare nothing."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    assert c.get("/ping", headers={"Host": "lumen.agience.ai"}).json() == {"persona": "lumen"}
    assert c.get("/lumen/ping").json() == {"persona": "lumen"}


def test_with_no_configured_domain_the_surface_is_still_path_addressable(monkeypatch, tmp_path):
    """Inactive-by-default applies to the router, not to the mount. A deployment with no
    `CRYSTAL_HOST_DOMAIN` gets no host rewriting, and the surface is still a real path — rewriting,
    not re-routing: the path is the table."""
    c = _client(monkeypatch, [_aria(tmp_path)], domain=None)
    assert "<h1>aria</h1>" in c.get("/aria/www/").text
    # not `== 404`: with no rewriting, `/` is the crystal host's own index route and answers 200.
    # What must be true is that the Host header bought nothing — the facet was not reached.
    r = c.get("/", headers={"Host": "www.agience.ai"})
    assert "<h1>aria</h1>" not in r.text, "a subdomain was honoured with no CRYSTAL_HOST_DOMAIN set"
    assert r.json().get("service") == "crystal-host"


# ── the SPA fallback: a client-side route is reachable, a missing asset is not ──────────────────
#
# A `dist` here is a bundler's output for an app that routes in the browser. Its paths exist only
# after index.html has loaded and run, so `StaticFiles(html=True)` — index.html for a directory,
# 404 for everything else — makes every route inside the surface unreachable. These four pin both
# halves of the answer: the fallback that makes routing work, and the 404 it must not swallow.

def test_a_CLIENT_SIDE_ROUTE_under_the_surface_serves_the_app(monkeypatch, tmp_path):
    """`/auth/callback` is not a deep link anyone chose — it is the `redirect_uri` an OIDC
    Authorization Code flow lands on. Measured 404 before the fallback existed, which meant a facet
    mounted here could not COMPLETE A SIGN-IN while its front page served perfectly."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    for route in ("/aria/www/auth/callback", "/aria/www/workspaces/abc", "/aria/www/a/b/c"):
        r = c.get(route)
        assert r.status_code == 200, f"{route} answered {r.status_code} — the router never runs"
        assert "<h1>aria</h1>" in r.text


def test_the_same_route_reached_by_SUBDOMAIN_serves_the_app(monkeypatch, tmp_path):
    """The address a browser actually uses. The host rewrite puts `/auth/callback` under the
    facet prefix, so this exercises the rewrite and the fallback together — which is the only
    combination a real sign-in takes."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    r = c.get("/auth/callback", headers={"Host": "www.agience.ai"})
    assert r.status_code == 200 and "<h1>aria</h1>" in r.text


def test_a_MISSING_ASSET_keeps_its_404(monkeypatch, tmp_path):
    """The half the fallback must not swallow. Answering a missing `.js` with index.html serves
    HTML under a script URL; the browser caches it against the hashed filename and the failure
    outlives the deploy that fixed the build. A 404 says which asset is missing."""
    c = _client(monkeypatch, [_aria(tmp_path), _persona("lumen")])
    for asset in ("/aria/www/assets/gone.js", "/aria/www/assets/gone.css",
                  "/aria/www/missing.png", "/aria/www/absent.json"):
        assert c.get(asset).status_code == 404, f"{asset} was answered with the SPA shell"
    # and the assets that DO exist are still served as themselves
    assert c.get("/aria/www/assets/app.js").status_code == 200


def test_a_bundle_with_no_index_html_does_not_INVENT_one(monkeypatch, tmp_path):
    """`_resolve_dist` already warns that such a bundle's own address will 404. The fallback must
    not convert that warning into a 200 with an empty body — an unbuilt surface answering 200 is
    the one failure this module's whole 'served, not declared' rule exists to prevent."""
    root = tmp_path / "aria"
    (root / "www" / "dist" / "assets").mkdir(parents=True)
    (root / "www" / "dist" / "assets" / "app.js").write_text("export default 1;", encoding="utf-8")
    p = _persona("aria", dist_root=root,
                 facets=[{"name": "www", "persona": "aria", "subdomains": ["www"], "dist": "www/dist"}])
    c = _client(monkeypatch, [p])
    assert c.get("/aria/www/some/route").status_code == 404
    assert c.get("/aria/www/assets/app.js").status_code == 200
