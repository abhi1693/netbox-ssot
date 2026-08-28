# ADR 0005: Use Ed25519-signed agent batches

- Status: Accepted
- Date: 2026-08-28

## Context

Collectors run in customer network segments and submit observations outbound to the NetBox plugin. A provider API
credential grants read access to the discovered system and must never become a control-plane credential. Shared ingest
secrets would also require the plugin to retain material capable of forging agent submissions.

## Decision

Each Go agent generates an Ed25519 key pair locally. The plugin stores only the public key and explicitly associates
the agent with allowed discovery sources. For every submission, the agent signs a context string, agent UUID, Unix
timestamp, and SHA-256 digest of the exact HTTP body. The plugin rejects unknown, disabled, stale, malformed, or
incorrectly signed requests before parsing or persisting the batch.

HTTPS is mandatory in normal operation. Plain HTTP requires an explicit agent development flag. Deployments that
need mutual TLS enforce it at the reverse proxy in addition to the application signature.

Run IDs are idempotency keys. Repeating an identical signed batch returns the existing run, while reusing a run ID
for different canonical content returns a conflict.

## Consequences

- Provider tokens and signing private keys remain on the customer edge.
- A database disclosure does not expose signing private keys.
- Agent authorization is limited to explicitly assigned source UUIDs.
- Clock synchronization must remain within the configured signature window, which defaults to five minutes.
- Key rotation is performed by enrolling a replacement public key and disabling the old agent identity.
