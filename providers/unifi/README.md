# UniFi Network provider

The UniFi provider collects infrastructure and network configuration through the official, read-only UniFi Network
Integration API. Its Python package publishes the provider descriptor to the NetBox plugin, while its Go collector is
compiled into the SSoT agent.

The implementation version is `0.0.1` and the compatible contract version is `1.0`.

## Requirements

- UniFi Network with the official Integration API enabled
- An API key generated through the UniFi integrations settings
- Outbound HTTPS access from the collector agent to the local console or UniFi cloud connector

Use the Integration API base URL without `/v1`, for example
`https://unifi.example.com/proxy/network/integration`. The collector sends only `GET` requests and authenticates with
`X-API-Key`.

## Configuration

| Field | Purpose |
| --- | --- |
| `api_url` | Official Integration API base URL without the `/v1` suffix |
| `api_key_ref` | Agent-local `env://` or absolute `file:///` API key reference |
| `site_ref` | Required exact site UUID or internal reference |
| `site_name_override` | Optional NetBox site name for one selected UniFi site |
| `site_slug_override` | Optional NetBox site slug for one selected UniFi site |
| `verify_tls` | Whether the agent validates the UniFi TLS certificate |
| `page_size` | Official API page size, from 1 through 200 |
| `timeout_seconds` | Per-request timeout |

The site overrides are useful when UniFi's internal site is named `default` but the established NetBox site has a
different canonical identity. Overrides require `site_ref` and therefore cannot ambiguously rename several sites.

UniFi declares observed-field ownership. For an existing NetBox object, only fields and relationships emitted by the
collector are proposed as changes; destination-only metadata such as tenants, tags, comments, and rack placement is
preserved. The collector requires one exact site because portable identities are site-scoped and duplicate address
space across several UniFi sites would otherwise be ambiguous.

## Dataset boundary

The provider can collect:

- sites;
- adopted devices plus Ubiquiti manufacturer, role, and device-type dependencies;
- physical ports, radio interfaces, management interfaces, device MAC addresses, and management IP addresses;
- network VLANs and IPv4 prefixes; and
- Wi-Fi broadcasts, including state, security family, site scope, and network VLAN.

Connected clients are intentionally excluded because association state is volatile and is not authoritative inventory.
Cables are also excluded: the official API identifies an uplink device but does not expose enough stable endpoint data
to create a reviewed physical cable relationship. API keys and Wi-Fi credentials never enter observations or evidence.

The collector fails closed on unknown datasets, ambiguous site selection, incomplete pagination, unsafe redirects,
unsupported response shapes, and unresolved dependencies. A complete result is emitted only after every page and every
required device or network detail has been read successfully.

## Validation

From the repository root:

```shell
uv run pytest tests/test_unifi_provider_contract.py tests/test_contract_guards.py
go test ./providers/unifi
```

The complete repository gate is documented in the [contributing guide](../../CONTRIBUTING.md).
