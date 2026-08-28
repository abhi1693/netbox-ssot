# ADR 0003: Use DiffSync for comparison only

- Status: Accepted
- Date: 2026-08-28

## Context

DiffSync provides a useful shared-model comparison engine, but its synchronization hooks can perform immediate CRUD. Immediate mutation cannot satisfy durable review, field ownership, staleness detection, dependency planning, or apply receipts.

## Decision

DiffSync adapters are in-memory projections. Their model CRUD methods never call NetBox or provider APIs. The planner invokes diff operations only and always begins with destination-only records skipped. It translates the resulting diff into internal change proposals.

Deletion is governed outside DiffSync. Hard deletion is disabled by default, and absence requires repeated complete snapshots of the same scope plus an elapsed grace period.

## Consequences

- DiffSync remains replaceable behind the planner interface.
- Planning and application can evolve independently.
- We must implement a dependency graph and apply handlers rather than relying on DiffSync processing order for writes.
- DiffSync upgrades require conformance tests against the internal proposal model.
