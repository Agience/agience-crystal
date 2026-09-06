# Contributing to Agience Crystal

Crystal condenses signal into typed content and routes it - the dispatcher that fronts the tektons.
**It reaches everything over the wire. Nothing links it.**

## The licence is load-bearing

Crystal is Apache-2.0 **deliberately**: `pip install agience-crystal` must not resolve copyleft onto
the install path. That is why it declares no dependency on ember even though ember sits above it.

**Do not add a dependency that pulls copyleft onto the install path.** A PR that does will not
merge, however well it works. Today the path is one package: `agience-prism`, Apache-2.0, whose base
install has no dependencies at all.

The distribution is **`agience-prism`**; the repository it is built from is `agience-prism-py`. The
two names differ, and `pip install agience-prism-py` resolves to nothing — that spelling was in this
manifest once and every editable checkout in the developer workspace hid it, because an editable
install satisfies an import without ever resolving the requirement.

## Tests

```bash
pip install -e '.[ontology,dev]'
python -m pytest -q src tests
```

Both roots hold tests; that is what CI runs. **The suite needs nothing but this repository and
`agience-prism`** — no sibling checkout, no environment variable, no operator bundles.

Keep it that way. Three tests used to reach the repository ABOVE crystal — two injected the real
aperture (`ember.optics`), one read ember's ingest vocabulary — and the cost was not theoretical:
when this repository went public, CI could no longer read those private siblings and the whole
suite job failed at checkout, on claims about crystal that needed neither of them. They now live in
`agience-ember`, which imports crystal and can make them with the arrow pointing the way the
packages already point.

A new test that needs ember, mantle, chorus or observe belongs in that repository, not here. What
crystal proves about the injected embodiment it proves against the stub in
`tests/test_embodiment_injection.py` — an implementation written against `prism.embodiment` in
numpy and the stdlib that crystal knows nothing about, which is what makes the slot a real slot.

## Rules

- **Over the wire, never by linking.** If a change makes Crystal import a service it currently
  reaches by HTTP/MCP, reconsider the change.
- `src/types` is vendored into `agience-chorus` (under `src/astra/web/vendor/package/types`) and a
  workspace check holds the two byte-identical. **This copy is canonical** - edit here and re-sync;
  never edit the vendored side to make the check pass.
- **`src/` holds `crystal/` and two directories that are not part of the distribution** —
  `tests/` and `types/`. `packages.find` in `pyproject.toml` carries an explicit
  `include = ["crystal*"]` for that reason: without it setuptools shipped `src/tests/` in the wheel,
  so installing crystal put a top-level `tests` module into the user's site-packages.
  `tests/test_the_wheel_ships_only_crystal.py` builds the real wheel and holds it.
- The capability vocabulary is re-exported from `prism.capabilities`. Do not fork it.

## Contributing

Fork, branch from `main`, sign off every commit (`git commit -s`) to certify the
[DCO](https://developercertificate.org/), open a PR. Commit format: `fix:` / `feat(scope):` /
`docs:` / `test:` / `chore:`. By contributing you agree your contribution is Apache-2.0 (per section
5), including its section 3 patent grant.

**Security vulnerabilities: do not open a public issue** - email **connect@agience.ai**.

## License

Apache-2.0 - see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
