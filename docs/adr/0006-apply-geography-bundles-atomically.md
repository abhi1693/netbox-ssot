# ADR 0006: Apply geography bundles atomically

- Status: Accepted
- Date: 2026-08-28

## Context

A reviewed comparison can become stale before execution. Region and Location trees require parents to exist before
children, while Sites and Locations depend on Tags, ownership, tenancy, Site Groups, RIRs, and ASNs. Embedded reference
names do not contain enough data to recreate those models faithfully.

## Decision

The plugin exposes a separate, permission-gated apply command for a complete geography bundle. Regions, Sites, and
Locations remain the selectable datasets; a hidden dependency dataset automatically collects Tags, Owner Groups,
Owners, Tenant Groups, Tenants, Site Groups, RIRs, and ASNs. The command requires explicit human confirmation and
revalidates the complete collection, source digest, comparison summary, engine version, and full target snapshot.

Apply fails closed when a comparison contains conflicts, skips, missing relationships, dependency cycles, or missing or
ambiguous resolve-only references. Supported creates and updates are ordered across the full dependency graph and
committed in one database transaction. On PostgreSQL, apply uses a top-level serializable transaction whose snapshot is
established before the target is revalidated. A concurrent NetBox writer therefore causes the apply to roll back and
require a fresh comparison instead of allowing reviewed data to overwrite the newer edit. Destination-only objects are
untouched and deletion is not implemented. Owner user/group memberships are destination-owned, and ASN Roles remain
resolve-only.

Every successful application stores an immutable run and per-item receipts. A durable binding associates each source
external identity with its local NetBox object and latest applied observation fingerprint. A one-to-one comparison
receipt makes retries idempotent.

## Consequences

- A target change after review forces a fresh comparison.
- An apply cannot be nested inside another PostgreSQL transaction because its isolation level must be established before
  any target read.
- One invalid object or unresolved reference prevents partial target mutation.
- Operators need both the plugin apply permission and the relevant NetBox add/change permissions.
- Supporting models are visible in comparisons and receipts but are not independent selectable datasets.
