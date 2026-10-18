# Changelog

All notable changes to `agience-crystal` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.1] - 2026-10-17

### Added
- `GET /personas`, `POST /personas/{slug}/resources/read` and `POST /personas/{slug}/tools/call` reach a persona by name through the gateway's persona map, with the caller's token: its tools and resources, a content type's `ui://` view, and the tools that view calls. A persona that is not registered or does not answer is reported as not running (503).
- Crystal publishes each persona's type definitions to Mantle when the persona registers, and its builtin types at startup, through `PUT /type-definitions/{server}` under its service identity. Mantle's indexer reads a type's `offer` from them. A publish runs in the background, is retried while Mantle is unreachable, and never fails a registration.
- `TypeRegistry.definitions_from(server)` returns the definitions one server contributed, as it sent them.
- The root type `*/*` (Artifact). Every type inherits from it: Collection, Workspace (through Collection), File (`application/*`), JSON, Text, Image, Audio, Video and the type definition type. A type nothing declares resolves to its category's wildcard and then to the root (an Agience or `+json` data type straight to the root, never to File), so it answers for an artifact's read, update and delete.

### Fixed
- The builtin types ship in the wheel (`crystal/types`, package data). They lived in `src/types` outside the package, so no installed Crystal ever loaded them: no Collection or Workspace type reached the web app, which opened a collection as a file.

## [0.2.0] - 2026-10-09

### Breaking
- `GET /types/all` and `GET /resolve/{content_type}` now require an Origin access token or a platform service token, and answer 401 otherwise. When `KEYS_DIR` is set but no authority manifest is found, they answer 503. With `KEYS_DIR` unset (dev/standalone), they stay open.
- Relay dispatch (`user_id_resolver`) and the `/relay/v1/connect` socket accept only Origin access tokens. Refresh, email-verification, password-reset, server-principal and other-audience tokens are refused (401 on dispatch, close code 4401 on the socket). Dispatch also accepts a delegation minted for one of the host's own personas; the socket accepts no delegation.
- `POST /register`, `POST /create` and `POST /artifacts/{id}/op/{op}` check the credential before the body is parsed. They refuse bodies over `CRYSTAL_MAX_BODY_BYTES` (default 16 MiB) with 413.
- Artifacts the gateway writes or defaults, such as operators in `crystal.registry`, the ontology IC basis and relation artifacts, and language transducers, now carry state `"live"` instead of `"committed"`. Retired operators are `"archived"`.
- The `commit` and `commit_preview` operations are removed from the `application/vnd.agience.collection+json` type.
- `agience-prism>=0.1.4` is now required, for `prism.trust.verify_access_token`.

### Added
- The `CRYSTAL_MAX_BODY_BYTES` setting in `crystal.config` sets the largest request body, in bytes, read on the gateway's body-parsing POST routes.
- `crystal.type_registry` loads builtin types (collection, workspace and the base types others inherit from) at startup from `src/types`, or from the directory named by `CRYSTAL_TYPES_DIR`. A persona re-registering never removes them, and a persona's own type takes precedence.
- `inherits` is resolved. A type answers for its own operations and for the first inherited type that declares each other one, so a workspace reads, updates and deletes as a collection does. `GET /resolve/{content_type}` lists inherited operations.

### Changed
- A persona-map miss in `crystal.gateway_middleware` refreshes from Mantle at most once per 30 seconds. An unauthenticated request for a UUID that is not a persona is answered 401 before any artifact lookup.
- The relay no longer forwards `Authorization`, `Cookie`, `Proxy-Authorization`, forwarding or hop-by-hop headers to the desktop runtime.

### Security
- The declared-upstream proxy in `crystal.host` drops any caller-supplied `X-Forwarded-For`, `X-Real-IP` and `Forwarded` headers. It sets `X-Forwarded-For` to the single peer address and sets `X-Forwarded-Proto` and `X-Forwarded-Host` itself.
