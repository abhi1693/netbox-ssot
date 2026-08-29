# ADR 0012: Expand the provider through Circuits bundles

- Status: Accepted
- Date: 2026-08-29

## Context

The complete DCIM provider deliberately rejected Circuit Terminations as cable endpoints because the Circuits app did
not yet have a portable identity, dependency graph, target adapter, or write policy. Collecting only the REST endpoints
would reintroduce parallel model dispatch and could create incomplete physical or virtual connectivity.

NetBox 4.6 exposes 11 public writable Circuits models. They include generic relationships from Circuit Terminations to
geographic or provider-network targets and from Circuit Group Assignments to physical or virtual circuits. Virtual
Circuit Terminations also require virtual DCIM Interfaces. These edges must participate in the same reviewed DiffSync
plan as their dependencies.

## Decision

Add four selectable dependency-closed datasets: **Circuit catalog**, **Circuits**, **Virtual circuits**, and **Circuit
group assignments**. They cover Providers, Provider Accounts, Provider Networks, Circuit Types, Virtual Circuit Types,
Circuit Groups, Circuits, Circuit Terminations, Virtual Circuits, Virtual Circuit Terminations, and Circuit Group
Assignments. Cabling now depends on Circuits and accepts Circuit Terminations as typed endpoints.

Extend the shared declarative resource registry used to compile the DiffSync adapters. Natural identities follow
NetBox uniqueness constraints: catalog slugs; Provider plus account or network name; Provider plus circuit ID; Circuit
plus termination side; Provider Network plus virtual circuit ID; the globally unique Interface of a Virtual Circuit
Termination; and Circuit Group plus the typed assigned member. Generic termination targets are restricted to Region, Site Group, Site, Location, and
Provider Network. Generic group members are restricted to Circuit and Virtual Circuit.

The Go collector remains read-only. Preview and reviewed `source.sync_to(target)` execution use the same canonical
fields and relationships. Destination-only records and deletion remain out of scope. The comparison engine version and
provider implementation patch version advance because the supported graph and cable identity boundary changed.

## Consequences

- Selecting a Circuits dataset automatically collects every reference, geography, catalog, device, or interface it
  needs for a complete plan.
- All 11 public writable Circuits models have collection, identity, target snapshot, dependency, permission, and write
  support.
- Physical cables can terminate on Circuit Terminations without being skipped as an unsupported cross-app graph.
- Virtual circuit application fails closed unless its Interface dependency exists and is virtual according to NetBox
  validation.
- Existing comparisons must be regenerated under the new engine version before apply.
