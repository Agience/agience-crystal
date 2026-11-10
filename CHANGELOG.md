# Changelog

All notable changes to `agience-crystal` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.6] - 2026-11-10

### Changed
- The gateway runs a workflow's steps (John, 2026-11-10, BUG-048). A step served by another persona never ran, on a timer's firing or on "Run now": Lumen handed each step to the gateway, but a request reaches Lumen through the gateway as a hop's result, which Origin never exchanges again. A `workflow` dispatch kind now runs each step as its operator's own invoke, on the caller's own delegation, by one hop to the persona that serves it, with the step semantics Lumen had (`$.previous`, `$.steps.<name>`, `@link.<name>`, a step answering an error fails the run). An operator's persona and tool are what it says when the caller may read it (a timer's job grant opens it) or what the platform lists for one it provides; an operator neither shows is refused in words. A workflow's sealed steps come with the request (`body.context`) when the caller cannot read them. It takes only what it does exactly as Lumen did: a workflow with no steps, a step that is not an operator, a step with a `condition`, a `retry` block or a run against a state artifact goes to Lumen as before, as does a single step; every other dispatch kind is unchanged. A run taken here records no Run card in the workflow's Runs collection; a timer still records its result where it names.

## [0.2.5] - 2026-11-10

### Fixed
- The Invite card type (`application/vnd.agience.grant-invite+json`) is no longer offered for creation (BUG-017): its `create` named a native service that does not exist, so the card made no invite. Invites are made at Origin, in the person's Origin session (the Share dialog), and reach Mantle from Origin's ledger.
- A service enrolled since Crystal started is taken without a restart (BUG-031): when a service token verifies against no anchor in memory, the anchors are read again from the keys folder, where the release that enrolled it put its anchor. Crystal read them once, at start, so a persona given a key of its own was refused until Crystal restarted.

## [0.2.4] - 2026-11-08

### Changed (review 31)
- The `agience-prism` floor is 0.1.9.

### Changed (review 24)
- The relay dispatch takes only the delegation narrowed to Crystal, never an `mcp_client` token, a person's own token or a persona's; the unused `_persona_client_ids` is removed.
- The registry read refuses an `mcp_client` token and takes the delegation narrowed to Crystal, which the web app reads it on.

### Changed (review 23)
- The desktop relay socket takes only a delegation narrowed to `relay` (scope `relay:host`), never the person's platform token.

### Changed (review 19)
- `/register` takes a slug's registration only from that slug, on a token addressed to Crystal and used once. Crystal's own key registers no other persona, and no type is taken over. The gateway gate pins its audience (`crystal`, `chorus`). A persona's registration carries a `jti`.

### Changed (review 17)
- A type another server already holds is not taken over by a registration, unless the registration is on Crystal's own token. The answer lists what was refused.

### Changed (review 16)
- `POST /register` for a slug takes only a token that slug's own manifest anchor signed as itself, or Crystal's own. A persona's registration is signed with its own enrolled key.

### Changed (review 15)
- `caller_token._verify_origin` checks an Origin token's issuer against the manifest's (`verify_origin_jwt`), as prism does.

### Changed (review 14)
- Crystal's hop assertion carries a fresh `jti` and the `subject_digest` of the narrowed token it exchanges, which Origin now requires.

### Changed (review 12)
- Crystal reaches a persona for a person by a per-hop exchange at Origin (`caller_token.hop`): its own narrowed delegation exchanged (RFC 8693, `audience` = the persona, Crystal's own key as client assertion) for one narrowed to that persona. Operation dispatch (`mcp_tool`) and `GET /personas` work on that path again; a refused hop reaches nothing, and Mantle still sees only Crystal's own server delegation. The token that reaches a persona is held per request, never on the shared dispatcher.

### Changed (review 11)
- Crystal never receives or forwards a person's own access token (`caller_token`): it is refused at the door. A persona's surface takes only the delegation Origin narrows to that persona; Crystal's own work takes the delegation narrowed to Crystal, exchanged at Origin for Crystal's own server delegation before Mantle sees it. A delegation narrowed to one persona reaches no other, and Crystal's own reaches no persona. Tokens issued before their person's sign-in bound are refused.

## [0.2.3] - 2026-10-31

### Fixed
- The suite passes under pytest 9.2. The `edge` fixture in `tests/test_the_edge_checks_before_it_parses.py` saves `app.state` and restores it itself instead of patching it with `monkeypatch.setattr`. Starlette's `State` keeps its values outside the instance `__dict__`, which pytest 9.2 reads to decide what to restore, so a second patch of one attribute made the undo raise and left `KEYS_DIR` and an empty manifest in place for the files after it. Production code is unchanged.

## [0.2.2] - 2026-10-29

### Added
- `.github/workflows/publish.yml`: the Release workflow now also runs on pushes to `release/**` branches. It builds and checks the package as it does for tags, and keeps the resulting files as the `dist` artifact so the platform release deploys exactly those files. Nothing is published from these branches.

## [0.2.1] - 2026-10-17

### Added
- The root type (`*/*`) declares `title` in its `context_schema` as part of the content (`"part": "content"`), so every type inherits it; Mantle seals it with the content.
- An MCP server a person added (a `vnd.agience.mcp-server+json` card, by its id) is reached through Iris on `/personas/{id}/...` and by the card's `invoke`. It answered "Persona '<id>' is not registered".
- A workflow runs: a Transform card's `invoke` passes the card's own id (and the optional workspace, params, input and artifacts) to Lumen, through an optional argument reference (`$.path?`, left out when absent). Every transform invoke failed, because none passed its own id.
- `GET /personas`, `POST /personas/{slug}/resources/read` and `POST /personas/{slug}/tools/call` reach a persona by name through the gateway's persona map, with the caller's token: its tools and resources, a content type's `ui://` view, and the tools that view calls. A persona that is not registered or does not answer is reported as not running (503).
- Crystal publishes each persona's type definitions to Mantle when the persona registers, and its builtin types at startup, through `PUT /type-definitions/{server}` under its service identity. Mantle's indexer reads a type's `offer` from them. A publish runs in the background, is retried while Mantle is unreachable, and never fails a registration.
- `TypeRegistry.definitions_from(server)` returns the definitions one server contributed, as it sent them.
- The root type `*/*` (Artifact). Every type inherits from it: Collection, Workspace (through Collection), File (`application/*`), JSON, Text, Image, Audio, Video and the type definition type. A type nothing declares resolves to its category's wildcard and then to the root (an Agience or `+json` data type straight to the root, never to File), so it answers for an artifact's read, update and delete.
- A persona tool the platform offers as an operator is reached only by a caller who may invoke that operator: on `POST /personas/{slug}/tools/call`, and through any operator card naming it, so an account withheld an operator cannot reach its tool directly or through a card of its own. A tool no platform operator runs is unaffected; the answer is kept 30 seconds per caller, and the gate fails closed.

### Fixed
- `/create` of a collection or workspace (a type whose `create` is native) posts to Mantle's `POST /artifacts`. It posted to `/artifacts/containers`, which Mantle no longer serves, and answered 405.
- The builtin types ship in the wheel (`crystal/types`, package data). They lived in `src/types` outside the package, so no installed Crystal ever loaded them: no Collection or Workspace type reached the web app, which opened a collection as a file.
- An operator's argument map is resolved: `$.body.params.<name>` from the invoke request (`?` when it may be absent) and `@relationship.<name>` from the operator's own links, read as the caller (422 naming the link when it is absent, or when two links share the name). The map declared at `$.context.operator.arguments` was forwarded as it stood, so every operator sent its tool the literal strings `"$.body.params.to"` and `"@relationship.authorizer"`. A map taken from the request stays the caller's data.

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
