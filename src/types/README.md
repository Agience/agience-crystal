# Content-Type Definitions (`types/`)

This directory contains builtin type skeletons used by Agience Core for type resolution.

## Layout

- `types/<top-level>/<subtype>/type.json`
- optional `schema.json`
- optional `behaviors.json`

Examples:

- `types/text/markdown/type.json`
- `types/application/json/type.json`

## What Belongs Here

Use `types/` for builtin or platform-native type definitions only.

- `type.json` defines identity, inheritance, and the embedded `ui` display metadata
- `schema.json` defines optional JSON-schema validation for structured content
- `behaviors.json` defines optional declarative behavior metadata

## What Lives Elsewhere

Server-owned viewers and vendor-specific handlers live outside `types/`.

- First-party server UI resources live under `servers/<name>/ui/...`
- Presentation discovers viewers through the registry and isolation layers
