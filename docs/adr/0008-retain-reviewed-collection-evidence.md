# ADR 0008: Retain reviewed collection evidence

## Status

Accepted

## Context

Automatic agents create complete immutable observation snapshots on every interval. Keeping every snapshot forever
would make database growth depend on source cadence and record count, while deleting evidence implicitly during
collection or page rendering would make operational behavior surprising and could invalidate reviews or application
receipts.

## Decision

Each source defines three bounded settings: successful collection age, maximum successful collection count, and
partial or failed collection age. A successful run becomes eligible when either successful limit is exceeded. A
partial or failed run becomes eligible when its diagnostic age is exceeded.

The newest collection and newest successful collection are always retained. Any collection referenced by a comparison
is also retained, which transitively protects applied collections and their object bindings.

Retention is exposed through `prune_ssot_collections`. The command is a dry run unless `--apply` is supplied. Applying
the plan locks the source and candidate collection rows, excludes reviewed runs again, deletes observations, and then
deletes only the remaining unreferenced collection rows in the same database transaction. This is the sole deliberate
deletion boundary for append-only collection evidence.

## Consequences

- Unattended collection has predictable storage bounds controlled per source.
- Review and application history cannot be invalidated by retention.
- Operators can inspect exact eligible run and observation counts before deletion.
- Scheduling the maintenance command remains an explicit deployment decision; agents and UI requests never trigger
  cleanup.
