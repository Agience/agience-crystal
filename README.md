# Agience Crystal

[![PyPI](https://img.shields.io/pypi/v/agience-crystal)](https://pypi.org/project/agience-crystal/)
[![Python](https://img.shields.io/pypi/pyversions/agience-crystal)](https://pypi.org/project/agience-crystal/)
[![License](https://img.shields.io/pypi/l/agience-crystal)](LICENSE)
[![CI](https://github.com/Agience/agience-crystal/actions/workflows/ci.yml/badge.svg)](https://github.com/Agience/agience-crystal/actions/workflows/ci.yml)
[![Sponsor](https://img.shields.io/badge/Sponsor-Agience-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/Agience)

**Condensation and routing — signal to content type.**

Crystal condenses incoming signal into typed content and dispatches it. As a gateway it maps a
request by its content type to the upstream service that owns it, and carries the gateway concerns:
identity, topology, the type registry and the MCP client. Everything it depends on at runtime is
reached over the wire and configured by URI.

## Install

```bash
pip install agience-crystal              # the contract and the ontology driver
pip install 'agience-crystal[service]'   # the gateway that boots — fastapi, uvicorn, httpx, …
pip install 'agience-crystal[ontology]'  # adds the coordinate, and numpy with it
```

Requires Python 3.11 or newer. The one base dependency is
[`agience-prism`](https://pypi.org/project/agience-prism/).

The split follows what each module imports: the base install is the contract, `[service]` is what
`crystal.main` and `crystal.host` need to serve, and `[ontology]` is separate again because
`crystal.ontology.geometry` imports numpy at module scope.

## Run it

Two servable surfaces on the same package.

**The gateway** — routes a request by content type to the service that owns it:

```bash
pip install 'agience-crystal[service]'
CRYSTAL_HOST=127.0.0.1 CRYSTAL_PORT=8085 \
MANTLE_URI=http://localhost:8082 ORIGIN_URI=http://localhost:8080 \
  python -m crystal.main
```

**The host** — serves a set of personas discovered at boot, assembling each with an embodiment:

```bash
MCP_HOST=127.0.0.1 MCP_PORT=8086 python -m crystal.host
```

Bind loopback and put a reverse proxy in front of anything public. A gateway whose upstreams are
unreachable still starts, and answers what it can reach.

### Configuration

| read by | variables |
|---|---|
| [`config.py`](src/crystal/config.py) — the gateway | `CRYSTAL_HOST`, `CRYSTAL_PORT` (8085), `CRYSTAL_EVENTS_ENABLED`, `MANTLE_URI`, `MANTLE_API_KEY`, `ORIGIN_URI`, `KEYS_DIR`, `EMBEDDINGS_URI`, `EMBEDDINGS_API_KEY`, `TYPE_CONTENT_TYPE`, `LOG_LEVEL` |
| [`host.py`](src/crystal/host.py) — the persona host | `MCP_HOST`, `MCP_PORT`, `CRYSTAL_UPSTREAMS`, `CRYSTAL_APEX_PERSONA`, `CRYSTAL_HOST_DOMAIN`, `CRYSTAL_TLS_CERT`, `CRYSTAL_TLS_KEY`, `LOG_LEVEL` |
| [`type_registration.py`](src/crystal/type_registration.py) | `CRYSTAL_URI`, `CHORUS_PUBLIC_URI`, `AGIENCE_SERVER_HOST_URI`, when an upstream pushes its types |

## The injected embodiment

The embodiment that measures arrives from the host at assembly time, through
`crystal.ontology.driver.set_default_store_provider` and the `prism.embodiment` contract. The same
crystal therefore runs against a full node and against a constrained store, with no import of
either.

[`tests/test_embodiment_injection.py`](tests/) runs the suite against a stub embodiment written in
numpy and the stdlib, and holds the base install to that allow-list — so the slot stays a real slot
and `import crystal` stays free of the measuring stack.

## Types

A content type is a directory: `type.json`, plus optional `schema.json`, `behaviors.json`,
`preview.json` and `handlers/`. [`src/types/`](src/types/) holds the builtin skeletons — `text`,
`image`, `audio`, `video`, `application` — as repository content read by a test, rather than as
wheel payload, since nothing loads them from the package at runtime.

An upstream owns its own types and pushes them to the gateway's `/register` endpoint at startup,
signed with a service JWT. Types and personas are gateway state.

## Layout

| path | what it is |
|---|---|
| [`src/crystal/`](src/crystal/) | dispatch, identity, topology, the MCP client, the registries, the host and the entrypoint |
| [`src/crystal/ontology/`](src/crystal/ontology/) | the coordinate — `geometry` (the Jiang–Conrath coordinate and the dense basis), `driver` (the ambient read), `lookup`, `coupling`, `freshness`, `transducer`, `seed_lattice` |
| [`src/types/`](src/types/) | the builtin content-type skeletons |
| [`tests/`](tests/) | the suite, including the ratchets holding the import boundaries |
| [`pyproject.toml`](pyproject.toml) | build, packaging and every dependency; the `service` extra is the one list |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md), which lists what the suite needs. It reads the environment
for nothing, so a fork can run it once those packages are installed.

Security issues: email **connect@agience.ai** rather than opening a public issue.

Licensed under Apache-2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
