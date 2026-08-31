# NetBox provider

The NetBox provider collects portable infrastructure and configuration data from a source NetBox REST API. Its
Python package publishes the provider descriptor to the plugin, while its Go collector is compiled into the SSoT
agent.

The implementation version is `0.0.1` and the compatible contract version is `1.0`.

The provider declares complete-field ownership because it projects the full supported canonical record surface. An
omitted supported field therefore remains meaningful desired state for NetBox-to-NetBox reconciliation.

## Configuration

The provider accepts:

| Field | Purpose |
| --- | --- |
| `base_url` | Source NetBox URL without the `/api` suffix |
| `token_ref` | Agent-local `env://` or absolute `file:///` API token reference |
| `verify_tls` | Whether the agent validates the source TLS certificate |
| `page_size` | Requested REST page size |
| `timeout_seconds` | Per-request timeout |

`token_ref` is the only secret-bearing field. The plugin stores the reference, while the agent resolves its value only
when executing a request.

## Dataset boundary

The manifest defines dependency-closed datasets across:

- Core data sources and Extras configuration
- Users, groups, permissions, ownership, and tenancy contacts
- IPAM registries, routing, VLANs, prefixes, addresses, FHRP, and services
- DCIM geography, catalogs, templates, racks, devices, components, power, and cabling
- Circuits, circuit groups, and virtual circuits
- Virtualization clusters, machines, interfaces, and disks
- VPN cryptography, tunnels, and Layer 2 VPNs
- Wireless LANs, groups, and links

Supporting records are collected automatically when a selected dataset needs them. The complete dataset definitions,
dependencies, source paths, and canonical resource mappings live in [`manifest.json`](manifest.json) and are rendered
in the plugin UI.

## Intentionally local data

The provider does not copy secrets or installation-specific runtime state. This includes passwords, API tokens,
superuser status, login activity, private user preferences, data-source credentials, generated files, job and audit
history, background-worker state, binary attachments, webhook credentials, and FHRP authentication keys.

Some aggregate or helper models are represented through their owning object instead of as standalone resources. A
record whose required relationship crosses the supported graph is reported as skipped or blocked; the provider does
not silently discard the relationship or construct a partial object.

Destination-only records are preserved and deletes are not proposed. The enabled write direction is source NetBox to
local NetBox; remote mutation is not implemented.

## Implementation map

- [`manifest.json`](manifest.json) — provider identity, configuration schema, datasets, and mappings
- [`collector.go`](collector.go) — REST collection, pagination, normalization, and completeness
- `src/netbox_ssot_provider_netbox/` — Python entry point and typed descriptor loading
- [`collector_test.go`](collector_test.go) — collector behavior and safety tests

The collector enforces bounded pagination and response sizes. It reports partial or failed collections explicitly so
the destination cannot interpret missing records as deletion evidence.

## Validation

From the repository root:

```shell
uv run pytest tests/test_provider_contract.py tests/test_contract_guards.py
go test ./providers/netbox
```

Model and field closure against NetBox is also covered by the plugin's database-backed tests. See the
[contributing guide](../../CONTRIBUTING.md) for the complete validation gate.
