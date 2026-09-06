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
pip install -e '.[ontology]'
export AGIENCE_BUNDLE_ROOT=/path/to/agience-observe/bundles
python -m pytest -q src tests
```

Both roots hold tests; that is what CI runs.

Two things the suite needs that the package does not:

- **`AGIENCE_BUNDLE_ROOT`.** `prism.runner` resolves an operator group to a sha-verified payload and
  there is no in-package copy to fall back to, so without it several files fail at
  `UnknownBundleGroupError` before reaching anything they test. Point it at `bundles/` in an
  `agience-observe` checkout.
- **`agience-ember` installed.** `tests/test_crystal.py` and `tests/test_embodiment_injection.py`
  import `ember.optics` at module scope, because those two files are where the HOST lives: they
  assemble a real node and hand the aperture over. That is a test-only reach and it must stay one —
  crystal's product code imports no ember, and `pip install agience-crystal` must never pull it.

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
