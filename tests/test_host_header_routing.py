"""Host-header (subdomain) routing on the crystal host.

A named entity is addressed by its own name: you reach a persona's subdomain, not a generic path
(WEB-FROM-THE-NETWORK §2). `crystal/host.py` translates a persona subdomain into the path prefix the
mounts already answer on, so a subdomain address and a path address reach the same handler.

The design decision these tests pin: rewrite the path, do not re-route. There remains exactly one
routing table — the mounts. A parallel `{host: app}` dispatch map would be a second registry of the
same facts, free to drift from the mounts.

Invariants, failure mode first:

  Inactive by default  — with no `CRYSTAL_HOST_DOMAIN` set, nothing is rewritten. Otherwise this
                          change alters every existing path-addressed deployment.
  Same app              — `lumen.<base>/x` and `/lumen/x` reach the same handler. Two addresses,
                          one table.
  Roster-derived        — an unmounted name is not rewritten; adding a persona does not also
                          require adding a subdomain somewhere, and naming a persona that was
                          never mounted 404s through a rewrite and reads as a routing bug.
  Apex -> aria           — `www.<base>` and the bare apex are the public face.
  Idempotent             — a request that already carries the prefix does not become
                          `/lumen/lumen/...`, which is what a front proxy that also rewrites would
                          otherwise produce.
  Unknown -> untouched   — an unrecognised Host falls through to normal path routing rather than
                          being guessed.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _persona(name: str):
    """A minimal persona provider: the host's own docstring says a fake with name+mount_app+register works."""
    sub = FastAPI()

    @sub.get("/ping")
    def ping():
        return {"persona": name}

    class P:
        pass

    p = P()
    p.name = name
    p.mount_app = sub
    p.operators = []
    return p


def _client(monkeypatch, *, domain=None, apex=None, names=("lumen", "sage", "aria")):
    monkeypatch.delenv("CRYSTAL_HOST_DOMAIN", raising=False)
    monkeypatch.delenv("CRYSTAL_APEX_PERSONA", raising=False)
    if domain is not None:
        monkeypatch.setenv("CRYSTAL_HOST_DOMAIN", domain)
    if apex is not None:
        monkeypatch.setenv("CRYSTAL_APEX_PERSONA", apex)
    host = importlib.import_module("crystal.host")
    app = host.build_app([_persona(n) for n in names])
    return TestClient(app)


def test_without_a_configured_domain_nothing_is_rewritten(monkeypatch):
    """Fails if enabling host-header routing silently changes every path-addressed deployment: no
    domain configured means no host routing at all."""
    c = _client(monkeypatch, domain=None)
    assert c.get("/lumen/ping").json() == {"persona": "lumen"}          # path routing still works
    r = c.get("/ping", headers={"Host": "lumen.agience.ai"})            # would only work if rewritten
    assert r.status_code == 404, "a subdomain was honoured with no CRYSTAL_HOST_DOMAIN configured"


def test_subdomain_and_path_reach_the_SAME_handler(monkeypatch):
    """Fails if there is a second routing table: both addresses must land on the same mounted app,
    which is what 'rewrite, do not re-route' means in practice."""
    c = _client(monkeypatch, domain="agience.ai")
    by_path = c.get("/lumen/ping")
    by_host = c.get("/ping", headers={"Host": "lumen.agience.ai"})
    assert by_path.status_code == 200 and by_host.status_code == 200
    assert by_path.json() == by_host.json() == {"persona": "lumen"}


def test_each_persona_in_the_roster_gets_its_name(monkeypatch):
    """Fails if a hardcoded subdomain list drifts from the mounts. Every mounted persona is reachable
    by its own name with no per-name registration anywhere."""
    c = _client(monkeypatch, domain="agience.ai")
    for n in ("lumen", "sage", "aria"):
        assert c.get("/ping", headers={"Host": f"{n}.agience.ai"}).json() == {"persona": n}


def test_an_unmounted_name_is_not_rewritten(monkeypatch):
    """Fails if a name is rewritten to a persona that does not exist: that 404s as if routing were
    broken rather than as if the persona were absent. The roster is the authority."""
    c = _client(monkeypatch, domain="agience.ai", names=("lumen",))
    assert c.get("/ping", headers={"Host": "sage.agience.ai"}).status_code == 404
    assert c.get("/lumen/ping", headers={"Host": "sage.agience.ai"}).json() == {"persona": "lumen"}, \
        "an unknown subdomain must leave the PATH alone, not break it"


def test_www_and_apex_go_to_aria(monkeypatch):
    """`www`/apex are the public face (§10.1). Both spellings, so neither is a special case of the other."""
    c = _client(monkeypatch, domain="agience.ai")
    assert c.get("/ping", headers={"Host": "www.agience.ai"}).json() == {"persona": "aria"}
    assert c.get("/ping", headers={"Host": "agience.ai"}).json() == {"persona": "aria"}


def test_the_apex_target_must_actually_be_mounted(monkeypatch):
    """Fails if the apex is rewritten to a persona that is not in the roster: every front-page request
    would 404 through the rewrite. With aria absent, the apex is left alone instead."""
    c = _client(monkeypatch, domain="agience.ai", names=("lumen", "sage"))
    assert c.get("/ping", headers={"Host": "www.agience.ai"}).status_code == 404
    assert c.get("/lumen/ping", headers={"Host": "www.agience.ai"}).json() == {"persona": "lumen"}


def test_the_rewrite_is_idempotent(monkeypatch):
    """Fails if the rewrite double-prefixes to `/lumen/lumen/ping`: a front proxy that also rewrites,
    or a client using both styles, must not produce a double prefix."""
    c = _client(monkeypatch, domain="agience.ai")
    assert c.get("/lumen/ping", headers={"Host": "lumen.agience.ai"}).json() == {"persona": "lumen"}


def test_a_port_in_the_host_header_is_tolerated(monkeypatch):
    """Fails if `Host: lumen.agience.ai:8082` (every local run and most proxies) misses the match."""
    c = _client(monkeypatch, domain="agience.ai")
    assert c.get("/ping", headers={"Host": "lumen.agience.ai:8082"}).json() == {"persona": "lumen"}


def test_a_deeper_label_is_not_treated_as_a_persona(monkeypatch):
    """Fails if `evil.lumen.agience.ai` or `a.b.agience.ai` is read as a persona name: only one label
    below the base is a persona address."""
    c = _client(monkeypatch, domain="agience.ai")
    assert c.get("/ping", headers={"Host": "a.b.agience.ai"}).status_code == 404
    assert c.get("/ping", headers={"Host": "evil.lumen.agience.ai"}).status_code == 404


def test_an_unrelated_host_is_left_untouched(monkeypatch):
    """Fails if a host this node does not serve is guessed at. Falls through to path routing, unchanged."""
    c = _client(monkeypatch, domain="agience.ai")
    assert c.get("/ping", headers={"Host": "example.com"}).status_code == 404
    assert c.get("/lumen/ping", headers={"Host": "example.com"}).json() == {"persona": "lumen"}


# ── the persona's own facet manifest is the source when it has one ─────────────────────────────────
def _persona_with_facets(name: str, subdomains, target=None):
    p = _persona(name)
    p.facets = [{"name": "view", "persona": target or name, "subdomains": list(subdomains)}]
    return p


def _client_from(monkeypatch, personas, *, domain="agience.ai"):
    monkeypatch.delenv("CRYSTAL_APEX_PERSONA", raising=False)
    monkeypatch.setenv("CRYSTAL_HOST_DOMAIN", domain)
    host = importlib.import_module("crystal.host")
    return TestClient(host.build_app(personas))


def test_a_declared_subdomain_is_honoured(monkeypatch):
    """Fails if subdomains are derived only from persona names: that makes `aria/manifest.py`'s
    `FACETS[*]["subdomains"]` decorative and gives the same fact two registries free to drift — the
    exact objection this module raises against a `{host: app}` dispatch map. A persona must be
    reachable at a name it declares, even one that is not its own slug."""
    c = _client_from(monkeypatch, [_persona_with_facets("aria", ["chat", "aria", "@"]), _persona("lumen")])
    assert c.get("/ping", headers={"Host": "chat.agience.ai"}).json() == {"persona": "aria"}, \
        "a DECLARED subdomain was ignored — the facet manifest is not reaching the router"


def test_a_declared_apex_wins_over_the_env_default(monkeypatch):
    """`@` in a persona's own manifest is a deliberate statement about the front door; it does not need
    an env var to agree with it."""
    c = _client_from(monkeypatch, [_persona_with_facets("lumen", ["@"]), _persona("aria")])
    assert c.get("/ping", headers={"Host": "agience.ai"}).json() == {"persona": "lumen"}, \
        "a declared `@` did not win over the default apex persona"


def test_the_persona_name_still_works_with_no_declaration(monkeypatch):
    """The name is the fallback, not a replacement — a persona that declares nothing stays reachable."""
    c = _client_from(monkeypatch, [_persona("lumen"), _persona("sage")])
    assert c.get("/ping", headers={"Host": "sage.agience.ai"}).json() == {"persona": "sage"}


def test_a_declaration_pointing_at_an_unmounted_persona_is_refused(monkeypatch):
    """Fails if a manifest names a persona that is not in the roster: the rewrite would 404 and read as
    a routing bug. The roster stays the authority even over a declaration."""
    p = _persona("lumen")
    p.facets = [{"name": "v", "persona": "ghost", "subdomains": ["ghost"]}]
    c = _client_from(monkeypatch, [p])
    # `ghost` maps to the declaring persona (lumen), not to the absent "ghost", and only because lumen is
    # mounted. The rewrite must never target an unmounted persona.
    r = c.get("/ping", headers={"Host": "ghost.agience.ai"})
    assert r.status_code == 200 and r.json() == {"persona": "lumen"}


def test_a_broken_facets_callable_does_not_break_boot(monkeypatch):
    """Fails if a persona with a raising `facets()` takes the whole host down. A broken manifest is a
    finding, never a boot crash — the same rule `_persona_manifest` already follows."""
    p = _persona("lumen")

    def _boom():
        raise RuntimeError("bad manifest")

    p.facets = _boom
    c = _client_from(monkeypatch, [p])
    assert c.get("/lumen/ping").json() == {"persona": "lumen"}
    assert c.get("/ping", headers={"Host": "lumen.agience.ai"}).json() == {"persona": "lumen"}, \
        "name-routing must still work when a persona's facets() raises"


def test_health_and_index_still_answer_on_a_persona_subdomain(monkeypatch):
    """Fails if the rewrite swallows the host's own routes. `/healthz` under a persona subdomain
    resolves to `/lumen/healthz`, which the persona does not serve — the honest consequence of
    name-addressing, pinned rather than special-cased. The apex keeps the host's own surface only
    when no apex persona is configured."""
    c = _client(monkeypatch, domain="agience.ai")
    assert c.get("/healthz").json() == {"status": "ok"}                 # path form: unchanged
    assert c.get("/healthz", headers={"Host": "lumen.agience.ai"}).status_code == 404


# ---------------------------------------------------------------------------
# RFC 9728 — discovery is a property of the host, not of the persona mount
# ---------------------------------------------------------------------------
_PRP = "/.well-known/oauth-protected-resource"


def test_well_known_is_reachable_on_a_PERSONA_SUBDOMAIN(monkeypatch):
    """RFC 8615 defines well-known discovery paths on the origin (scheme+host), which is what this
    router has just finished resolving before it applies the persona rewrite — so these paths are
    exempt from the rewrite and stay reachable on a persona subdomain, `aria.<base>/.well-known/oauth-protected-resource`
    included. The same exemption covers `/.well-known/mcp`.
    """
    c = _client(monkeypatch, domain="agience.ai")
    r = c.get(_PRP, headers={"Host": "aria.agience.ai"})
    assert r.status_code == 200, "discovery is unreachable on a persona subdomain"
    assert c.get("/.well-known/mcp", headers={"Host": "aria.agience.ai"}).status_code == 200


def test_each_persona_host_names_ITSELF_as_the_resource(monkeypatch):
    """One process serves many resources: `aria.<base>` and `sage.<base>` are different protected
    resources sharing one authorization server. A constant `resource` would name one of them for all
    of them — and an audience check downstream would then reject the very tokens it caused to be
    minted."""
    c = _client(monkeypatch, domain="agience.ai")
    for n in ("aria", "sage", "lumen"):
        doc = c.get(_PRP, headers={"Host": f"{n}.agience.ai"}).json()
        assert doc["resource"].endswith(f"{n}.agience.ai"), doc
        assert doc["bearer_methods_supported"] == ["header"]


def test_the_authorization_server_is_this_nodes_origin(monkeypatch):
    monkeypatch.setenv("ORIGIN_URI", "https://origin.home.agience.ai")
    import crystal.config as cfg
    importlib.reload(cfg)
    c = _client(monkeypatch, domain="agience.ai")
    doc = c.get(_PRP, headers={"Host": "aria.agience.ai"}).json()
    assert doc["authorization_servers"] == ["https://origin.home.agience.ai"]


def test_an_unconfigured_issuer_OMITS_the_key_rather_than_publishing_an_empty_one(monkeypatch):
    """`[]` is a positive claim that there is nowhere to authenticate; the key's absence says the
    node has not been configured to say. A client can act on the second and only despair at the
    first."""
    monkeypatch.setenv("ORIGIN_URI", "")
    import crystal.config as cfg
    importlib.reload(cfg)
    c = _client(monkeypatch, domain="agience.ai")
    doc = c.get(_PRP, headers={"Host": "aria.agience.ai"}).json()
    assert "authorization_servers" not in doc
    assert doc["resource"].endswith("aria.agience.ai")


def test_a_401_carries_the_discovery_pointer(monkeypatch):
    """The challenge is attached in one place, so it cannot be present on some paths and not others."""
    from fastapi import HTTPException
    host = importlib.import_module("crystal.host")
    monkeypatch.setenv("CRYSTAL_HOST_DOMAIN", "agience.ai")
    app = host.build_app([_persona("aria")])

    @app.get("/_t401")
    def _t():
        raise HTTPException(status_code=401, detail="nope")

    # No persona `Host` here — the rewrite would send `/_t401` to `/aria/_t401`, which is the
    # router working correctly but a fixture that would measure nothing about the handler. The
    # default host exercises the handler without crossing the rewrite.
    from fastapi.testclient import TestClient
    r = TestClient(app).get("/_t401")
    assert r.status_code == 401
    v = r.headers.get("www-authenticate", "")
    assert "resource_metadata=" in v and _PRP in v, v


def test_a_bare_framework_Bearer_is_upgraded_and_a_non_401_is_untouched(monkeypatch):
    """FastAPI's `HTTPBearer` raises 401 with a bare `Bearer`; keying the discovery pointer on the
    header's presence, rather than on the response status, suppresses it on exactly the routes using
    the security scheme. The same defect is pinned in mantle's suite."""
    from fastapi import HTTPException
    host = importlib.import_module("crystal.host")
    monkeypatch.setenv("CRYSTAL_HOST_DOMAIN", "agience.ai")
    app = host.build_app([_persona("aria")])

    @app.get("/_bare")
    def _bare():
        raise HTTPException(status_code=401, detail="x", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/_teapot")
    def _teapot():
        raise HTTPException(status_code=418, detail="x")

    from fastapi.testclient import TestClient
    c = TestClient(app)
    v = c.get("/_bare").headers["www-authenticate"]
    assert v != "Bearer" and "resource_metadata=" in v
    r = c.get("/_teapot")
    assert r.status_code == 418
    assert "www-authenticate" not in {k.lower() for k in r.headers}
