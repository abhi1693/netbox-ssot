# Go runtime internals

This directory contains the reusable, non-exported Go runtime used by the collector agent. Go's `internal` package
rules prevent these APIs from becoming an accidental public SDK.

## Package map

- `automation` — control polling, assignment scheduling, bounded collection workers, retries, and command lifecycle
- `contracts` — Go representations of the versioned agent and provider protocol
- `provider` — compiled collector interface, registry, compatibility checks, and dataset dependency resolution
- `secrets` — agent-local `env://` and absolute `file:///` secret resolution
- `submission` — enrollment, signed control requests, command updates, ingestion, key rotation, and HTTP lifecycle

The executable entry point is documented under [`../agent/`](../agent/README.md). Provider collectors are documented
under [`../providers/`](../providers/README.md).

## Dependency direction

The command may depend on internal runtime packages and compiled providers. Providers may depend on `internal/contracts`
and `internal/provider`. Internal packages must not import the NetBox Python plugin or assume access to its database.

The Python contracts package is the authoritative public model boundary. The Go protocol types mirror the subset needed
at the edge and must remain wire-compatible with contract version `1.0`.

## Safety invariants

- Edge code reads source systems and communicates outbound to the plugin; it never mutates the destination.
- Secret resolvers return values only at execution time and errors must not reveal those values.
- HTTP requests are bounded by timeouts and response-size limits.
- Signed requests bind the agent identity, timestamp, method, path, and payload.
- Collection completeness and command status are explicit protocol state, not inferred from process exit alone.
- Retries must preserve idempotency and must not create concurrent work for one source.
- Provider and protocol incompatibility fails before collection begins.

## Testing

Keep tests next to each package and run the complete Go gate from the repository root:

```shell
test -z "$(gofmt -l agent internal providers/netbox)"
go test ./...
go vet ./...
```

Changes that alter wire behavior also require the cross-language contract tests described in
[`../CONTRIBUTING.md`](../CONTRIBUTING.md).
