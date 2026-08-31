# Repository Guidelines

## Project Structure & Module Organization

Python packages live under `packages/`: `contracts` defines vendor-neutral schemas and `plugin` contains the NetBox control plane. The built-in NetBox provider is in `providers/netbox/`. Customer-edge runtime code is Go: the executable starts in `agent/cmd/netbox-ssot-agent`, with reusable logic under `internal/`. NetBox-independent Python tests are in `tests/`; database-backed plugin tests sit beside the plugin in `packages/plugin/src/netbox_ssot/tests/`. Browser checks live in `tests/ui/`, architecture decisions in `docs/adr/`, and static/templates alongside the plugin package.

## Build, Test, and Development Commands

- `uv sync --all-packages --locked` installs the Python workspace exactly from `uv.lock`.
- `uv run ruff check . && uv run mypy` runs Python linting and strict type checks.
- `uv run pytest tests --cov` runs the fast, NetBox-independent suite with coverage.
- `go test ./... && go vet ./...` tests and analyzes the agent and provider runtime.
- `gofmt -w agent internal providers/netbox providers/unifi` formats Go sources.
- `CGO_ENABLED=0 go build -trimpath -o dist/netbox-ssot-agent ./agent/cmd/netbox-ssot-agent` builds the agent.
- `cd tests/ui && npm ci && npx playwright install chromium && npm test` runs opt-in UI/accessibility checks against a configured development NetBox.

## Coding Style & Naming Conventions

Use Python 3.12+, four-space indentation, type annotations, and Ruff’s 120-character line limit. Modules, functions, and tests use `snake_case`; classes use `PascalCase`. Keep the contracts package free of Django, NetBox, and vendor SDK imports. Edge/customer runtime belongs in Go; run `gofmt` before committing. Keep provider payload details behind versioned adapters and manifests.

## Testing Guidelines

Name Python tests `test_*.py` and Go tests `*_test.go`. Add focused tests for every contract, identity rule, and safety invariant. Coverage must remain at least 90%. Root pytest intentionally excludes plugin integration tests; run those in NetBox 4.6 with PostgreSQL and Redis via `manage.py test netbox_ssot.tests`. Also check migrations with `makemigrations netbox_ssot --check --dry-run`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative, sentence-case subjects, such as `Harden dependency-safe provider apply`. Keep commits logically scoped. PRs should explain behavior and safety impact, link relevant issues, list validation commands, and include screenshots for UI changes. Add or update an ADR when changing trust boundaries, public contracts, identity rules, or apply invariants.

## Release Notes

The detailed `v0.0.1` notes are an exception for the first public release. For later releases, follow NetBox's concise changelog style and derive entries from the commits since the preceding release tag because this repository does not yet use pull requests.

Name a release `vX.Y.Z - YYYY-MM-DD`. Start with callouts only when operators must know about a breaking change, required migration, compatibility change, security issue, or pre-release risk. Then group concise entries under the applicable NetBox-style headings, omitting empty sections:

- **Breaking Changes**
- **New Features**
- **Enhancements**
- **Performance Improvements**
- **Bug Fixes**
- **Accessibility**
- **Documentation**
- **Other Changes**

Represent each change as `* [<short SHA>](<commit URL>) - <plain-language outcome>`. Rewrite commit subjects when necessary so entries describe user-visible behavior clearly; do not paste implementation-only subjects without context. Include every material commit in the tag range, combine commits that form one user-facing change, and exclude release mechanics or CI-only corrections unless they affect users. End with a full changelog link comparing the previous and current tags.

Before publishing notes, verify the tag range, classify commits by behavior, and call out any compatibility or upgrade requirement explicitly. After editing the GitHub release, re-read the published body and confirm its title, tag, assets, and checksum manifest remain correct. Never claim support, validation, distribution, or safety behavior that was not verified for that release, and never include credentials or operational secrets.

## Security & Configuration

Never place credentials, tokens, cookies, or private keys in configs, logs, observations, plans, or fixtures. Collectors must remain read-only; reviewed apply is the only mutation path. Do not infer deletion from incomplete collections or turn fuzzy identities into automatic changes.
