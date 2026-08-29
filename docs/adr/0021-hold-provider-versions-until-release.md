# ADR 0021: Hold provider versions until release

## Context

The provider manifest version had been advanced for each pre-release resource-graph change. That made ordinary
development commits look like independently released provider artifacts and repeatedly invalidated otherwise current
agent capability advertisements.

Comparison plans already have a separate engine version for invalidating cached snapshots when identity, ownership, or
diff behavior changes. Provider implementation versions do not need to serve that purpose.

## Decision

Keep every provider manifest `implementation_version` at `0.0.1` throughout pre-release development, regardless of
implementation changes. Change it only during explicitly authorized provider release work.

Continue advancing the comparison engine version when cached comparisons must be invalidated. Contract, plugin,
provider, and agent binary versions remain independent release domains.

## Consequences

- Feature work does not bump provider implementation versions before release.
- Agents need rebuilding when embedded provider behavior changes, but their advertised pre-release provider version
  remains `0.0.1`.
- A future provider release must deliberately choose and update its release version and all compatibility expectations.
