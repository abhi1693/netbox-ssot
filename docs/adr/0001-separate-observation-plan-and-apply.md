# ADR 0001: Separate observation, planning, and apply

- Status: Accepted
- Date: 2026-08-28

## Context

Discovery data can be incomplete, delayed, ambiguous, or collected from a scope narrower than the target. A direct source-to-target synchronization couples those uncertainties to mutation and makes review, replay, and incident analysis difficult.

## Decision

Collection produces immutable observations. Reconciliation produces a durable plan from selected observations and a
target snapshot. Review records append-only record decisions and finalizes one immutable approval or rejection whose
digest binds the plan, item set, and latest decisions. Apply is a separate capability that requires an approved,
non-stale plan, a valid review digest, and a current target re-read. Rejected records block the complete v1 plan rather
than being silently omitted from a partial apply.

Source collectors are read-only. Their interface exposes connection testing and collection only. The current phase has no target capability. Any future mutation belongs exclusively to a separately designed and authorized apply boundary.

## Consequences

- Collection can be replayed and inspected without repeating network access.
- A provider outage cannot directly mutate the target.
- Plans can become stale and must carry target fingerprints.
- Reviewer changes of mind remain auditable, and finalized reviews cannot be edited.
- The system requires explicit persistence and lifecycle states rather than a single synchronization job.
