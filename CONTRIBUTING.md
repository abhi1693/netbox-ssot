# Contributing

## Development workflow

1. Add or update an architecture decision when changing a trust boundary, public contract, identity rule, or apply invariant.
2. Keep provider-specific payloads behind provider adapters. The contracts package must not import Django, NetBox, or a vendor SDK.
3. Implement code that runs on a customer edge in Go. Python is reserved for the NetBox plugin/control plane.
4. Add tests for every contract and safety rule before connecting it to a live system.
5. Increment a changed provider's manifest `implementation_version` by exactly one patch version. Provider versions
   begin at `0.0.1`; do not advance the major or minor component.
6. Run the complete local gate before submitting a change:

   ```shell
   uv sync --all-packages
   uv run ruff check .
   uv run mypy
   uv run pytest tests --cov
   go test ./...
   go vet ./...
   ```

   The plugin's database-backed suite must also run inside a NetBox 4.6 environment with PostgreSQL and Redis. Install
   the workspace packages into that environment, put `tests/` on `PYTHONPATH`, and run:

   ```shell
   export NETBOX_CONFIGURATION=netbox_ssot_test_configuration
   python /path/to/netbox/netbox/manage.py makemigrations netbox_ssot --check --dry-run
   python /path/to/netbox/netbox/manage.py test netbox_ssot.tests --noinput
   ```

## Non-negotiable safety rules

- Do not place credentials, tokens, session cookies, or private keys in provider configuration, logs, observations, plans, or test fixtures.
- Do not add provider-controlled JavaScript, Django templates, Python import paths, or HTML to manifests.
- Do not call DiffSync synchronization methods from the planning boundary.
- Do not mutate a discovered system from a source collector.
- Do not turn an inferred or fuzzy identity into an automatic change.
- Do not infer deletion from a partial, failed, truncated, or differently scoped collection.
