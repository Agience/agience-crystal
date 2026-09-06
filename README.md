# Agience Crystal

[![PyPI](https://img.shields.io/pypi/v/agience-crystal)](https://pypi.org/project/agience-crystal/)
[![Python](https://img.shields.io/pypi/pyversions/agience-crystal)](https://pypi.org/project/agience-crystal/)
[![License](https://img.shields.io/pypi/l/agience-crystal)](LICENSE)
[![CI](https://github.com/Agience/agience-crystal/actions/workflows/ci.yml/badge.svg)](https://github.com/Agience/agience-crystal/actions/workflows/ci.yml)
[![Sponsor](https://img.shields.io/badge/Sponsor-Agience-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/Agience)

**Condensation and routing — signal to content type.**

Crystal condenses incoming signal into typed content and routes it. As the dispatcher/gateway it
maps a request by its content type to one of the chorus tekton services (aria, astra, iris, lumen,
ophan, sage, seraph) and carries the gateway concerns: identity, topology, and the MCP client.

It reaches everything **over the wire**; nothing links it. Origin is called at `ORIGIN_URI` over
HTTP, and the store is injected by the host rather than imported — crystal imports neither.

## Install

    pip install agience-crystal              # the contract and the ontology driver
    pip install agience-crystal[service]     # the gateway that boots — fastapi, uvicorn, httpx, …
    pip install agience-crystal[ontology]    # adds the coordinate, and numpy with it

**The three are a measured split, not a preference.** 23 of crystal's 34 non-test modules import on
`agience-prism` and the stdlib alone; 11 are the ones that serve, and those are what `[service]`
carries. `[ontology]` is separate again because `crystal.ontology.geometry` imports numpy at module
scope, and a gateway that never computes a coordinate should not install it.

Requires Python 3.11 or newer. The one base dependency is
[`agience-prism`](https://pypi.org/project/agience-prism/), Apache-2.0, whose own base install has
no dependencies at all.

## Run it

Crystal is two servable surfaces on the same package, and they are different jobs.

**The gateway** — routes a request by content type to the tekton that owns it:

    pip install 'agience-crystal[service]'
    CRYSTAL_HOST=127.0.0.1 CRYSTAL_PORT=8085 \
    MANTLE_URI=http://localhost:8082 ORIGIN_URI=http://localhost:8080 \
      python -m crystal.main

**The host** — serves a set of tektons discovered at boot, assembling each with an embodiment:

    MCP_HOST=127.0.0.1 MCP_PORT=8086 python -m crystal.host

Bind loopback and put a reverse proxy in front of anything public.

Configuration is the environment, and it is read in two places:

| read by | variables |
|---|---|
| `config.py` — the gateway | `CRYSTAL_HOST`, `CRYSTAL_PORT` (8085), `CRYSTAL_EVENTS_ENABLED`, `MANTLE_URI`, `MANTLE_API_KEY`, `ORIGIN_URI`, `KEYS_DIR`, `EMBEDDINGS_URI`, `EMBEDDINGS_API_KEY`, `TYPE_CONTENT_TYPE`, `LOG_LEVEL` |
| `host.py` — the tekton host | `MCP_HOST`, `MCP_PORT`, `CRYSTAL_UPSTREAMS`, `CRYSTAL_APEX_PERSONA`, `CRYSTAL_HOST_DOMAIN`, `CRYSTAL_TLS_CERT`, `CRYSTAL_TLS_KEY`, `LOG_LEVEL` |

`type_registration.py` additionally reads `CRYSTAL_URI`, `CHORUS_PUBLIC_URI` and
`AGIENCE_SERVER_HOST_URI` when a tekton pushes its types.

A gateway with no Mantle and no Origin still starts; it answers what it can reach.

## The embodiment is injected, never imported

`import crystal` pulls no instrument and no numpy — checked in CI on a bare install, not asserted
here. The embodiment that measures arrives from the host at assembly, which is what lets the same
crystal run on a full node and on a constrained store.

**No test here reaches a real instrument either.** `tests/test_embodiment_injection.py` carries a
stub embodiment written against `prism.embodiment` in numpy and the stdlib, and the suite runs on
that — an implementation crystal knows nothing about, which is what makes the slot a real slot. The
claim that the *aperture* the host hands over also fits is asserted in `agience-ember`, which
imports crystal; making it from here would have meant importing the repository above this one.

## Layout

| Path | Purpose |
|---|---|
| `src/crystal/` | The dispatcher/gateway service — dispatch, identity, topology, MCP client, registries, host and entrypoint. |
| `src/crystal/ontology/` | The coordinate: coupling, geometry, lookup, freshness, transducer. Behind the `ontology` extra, because `geometry` imports numpy at module scope. |
| `src/types/` | Builtin content-type skeletons — `type.json` plus optional `schema.json`, `behaviors.json`, `preview.json` and `handlers/`. Repository content, read by a test; **not shipped in the wheel**, because nothing at runtime loads it from the package. |
| `tests/` | The suite, including the ratchets that hold the import boundaries above. |
| `pyproject.toml` | Build, packaging and every dependency. There is no `requirements.txt` — the `service` extra is the one list. |

A tekton owns its own types. Each one ships `ui/<top>/<sub>/type.json` and pushes it to the
gateway's `/register` endpoint at host startup, signed with a service JWT. Types and tektons are
gateway state; the store never sees them.

## Where it sits

    agience-prism   the contract, dependency-free        crystal depends on it
    agience-crystal this repository                      the gateway
    agience-ember   the observer unit                    depends on crystal, never the reverse
    agience-mantle  the store                            reached over the wire, never imported

Crystal is **below** ember and mantle in the dependency graph. That direction is what the licence
section below is about.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The suite stands on this repository and `agience-prism`
alone — no sibling checkout, no environment variable — so a fork can run it.

## License

Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)).

The permissive licence is load-bearing, and `pyproject.toml` argues it at length: `pip install
agience-crystal` must not resolve copyleft onto the install path. That is why crystal declares no
dependency on ember even though ember sits above it. A dependency that would pull a copyleft package
onto the install path is a design conversation, not a dependency bump.
