# ADR 0011: Use typed DiffSync adapters for reviewed execution

- Status: Accepted
- Date: 2026-08-29
- Supersedes: ADR 0003

## Context

The initial implementation used DiffSync only to create a preview, then duplicated model dispatch and change execution
in a separate apply engine. That kept review and mutation separate, but it made every supported NetBox model expand
several parallel switch statements. It also prevented the architecture from naturally supporting the inverse
synchronization direction.

A direct `sync_to()` call at collection time is still unsafe. Changes must remain reviewable and application must fail
when the immutable source, target snapshot, review digest, dependency graph, or permissions no longer match.

## Decision

Compile distinct DiffSync models for each canonical resource kind from one declarative attribute and relationship
registry. Canonical fields become individual typed model attributes; they are not hidden in generic attribute or
relationship blobs. Source and target adapters share those model classes.

Preview calls `source.diff_to(target)` and persists an immutable proposal. Apply locks the comparison, reloads the
target, revalidates all evidence and review invariants, recalculates the delta, and verifies its create/update actions
against the approved proposal. It then passes that exact diff to `source.sync_to(target)`. DiffSync model CRUD hooks
delegate to the target adapter's mutation backend, which owns destination-specific loading and persistence.

Adapters explicitly advertise read, write, delete, and atomicity capabilities. Local NetBox writes execute inside the
existing serializable database transaction. Deletion remains disabled and destination-only objects remain skipped.
Synchronization direction is durable. Source-to-target is currently executable; target-to-source fails closed until a
provider supplies an authenticated write backend.

## Consequences

- Preview, review, staleness protection, and receipts remain separate from mutation authority.
- Adding a normal model field or relationship changes the shared registry instead of parallel loader/planner/writer
  dispatch trees.
- The same `source.sync_to(target)` protocol supports future bidirectional providers by swapping adapters and selecting
  a capable mutation backend.
- Destination-specific edge cases still require small backend hooks, including generic relations, port mappings, and
  deferred cyclic relationships.
- DiffSync upgrades require conformance tests for both preview translation and reviewed synchronization behavior.
