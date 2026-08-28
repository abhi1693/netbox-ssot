# ADR 0004: Use Go for every customer-edge runtime

- Status: Accepted
- Date: 2026-08-28

## Context

Remote discovery must work in segmented customer networks without requiring Python environments, dependency resolution, or inbound access. Shipping provider-specific Python workers would recreate the operational complexity this project is intended to remove.

## Decision

The outbound agent, secret resolvers, collection scheduler, transport, and network-facing collectors are written in Go and distributed as versioned static binaries and container images. NetBox and UniFi collectors are compiled into that binary.

Python is confined to the NetBox plugin/control plane because NetBox plugins execute inside Django. Provider manifests and collection envelopes are language-neutral JSON contracts. Python descriptor packages expose manifests to the UI; agent capability advertisements prove which matching collectors are actually available.

## Consequences

- A customer receives one auditable binary with no language runtime dependency.
- Collector upgrades require a new agent artifact and capability advertisement.
- The repository must run conformance fixtures against both Pydantic and Go types.
- Third-party collectors initially require building a custom agent; a safe extension mechanism can be designed later without relying on Go's platform-sensitive dynamic plugin mechanism.
