# ADR 0010: Expand DCIM through dependency-closed bundles

- Status: Accepted
- Date: 2026-08-29

## Context

Advertising a NetBox model after only adding its REST endpoint creates a demo collector, not a reconciliation provider.
Every actionable kind also needs a portable natural identity, complete core-field projection, relationship closure, a
fresh target snapshot, deterministic dependency ordering, destination permissions, and an atomic write implementation.
Racks depend on geography and catalog objects, while Device Types and Rack Types depend on Manufacturers and Platforms.

Some adjacent fields cross separate ownership boundaries. Config Templates contain destination-managed executable
configuration content. Custom fields, contact assignments, images, IPAM, virtualization, circuits, and wireless
objects require policies beyond the core DCIM model. Those boundaries must remain explicit even when a DCIM object has
a generic relation to one of them.

## Decision

Add selectable, dependency-closed datasets for the entire public writable NetBox 4.6 DCIM surface: **Device catalog**,
**Racks**, **Module catalog**, **Component templates**, **Devices**, **Device components**, **Rack reservations**,
**Power**, and **Cabling**. The Go collector, canonical comparison, target snapshot, review, permission checks,
dependency planner, and apply writer support every advertised kind together. Supporting geography, ownership, tenancy,
tagging, and ASN records are collected automatically through the existing dependency graph.

Natural identities follow NetBox uniqueness context: globally unique slugs for organizational models; parent plus slug
for hierarchical roles; manufacturer plus model for catalog types; parent plus name for templates and components;
device placement/name context; and complete supported cable endpoints. Duplicate natural identities fail closed as
conflicts. Config Templates and ASN Roles are resolve-only by an exact unique value, and Rack Reservation users resolve
by unique local username. Source images, custom fields, and contact assignments are omitted explicitly.

Cable terminations and port mappings are aggregates of their owning resource, not standalone resources. A cable with a
circuit or wireless termination and a MAC assigned to a VM interface are rejected as incomplete cross-app graphs; the
provider never creates a half-cable or silently drops a generic assignment.

The comparison engine version advances because the supported target snapshot and identity rules changed. The NetBox
provider implementation advances by one patch version. Apply remains one reviewed serializable transaction, never
deletes destination-only objects, and never creates resolve-only references implicitly.

## Consequences

- Selecting Racks collects and validates its complete dependency closure automatically.
- Selecting any later DCIM dataset automatically includes every earlier dependency it needs.
- A missing or ambiguous Config Template blocks apply instead of silently dropping or recreating it.
- Existing comparisons use the prior engine version and must be regenerated before apply.
- Every public writable DCIM resource has a collector mapping, portable identity, target snapshot, dependency rule,
  permission check, writer, and database-backed round-trip test.
- Cross-app fields remain future ownership bundles and cannot be inferred from DCIM support.
