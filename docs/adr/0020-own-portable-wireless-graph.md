# ADR 0020: Own the portable Wireless graph

## Context

NetBox's Wireless app contains hierarchical LAN groups, optionally scoped wireless LANs, and point-to-point links
between device interfaces. None participated in provider collection, comparison, or apply, so wireless intent remained
outside the reviewed synchronization graph.

Wireless LANs and links can contain pre-shared keys. Copying those values into immutable observations would expose
credentials and incorrectly make source secret material part of portable identity or comparison evidence.

## Decision

Add one dependency-closed Wireless networks dataset and model Wireless LAN Groups, Wireless LANs, and Wireless Links
as typed resources in the shared registry.

Preserve group hierarchy, ownership, tenancy, tags, VLAN assignments, all four supported DCIM scope types, interface
endpoints, authentication mode and cipher, distance, and operational status. Unsupported Wireless LAN scope types
retain their source marker and fail closed rather than being silently made global.

Treat Wireless LAN and Wireless Link pre-shared keys as destination-local credentials. They are excluded from
observations, evidence digests, comparison, and apply. Creating either resource leaves its key blank; updating one
preserves the destination value.

Use hierarchical slug identities for LAN groups, scoped SSID identities for LANs, and an order-independent pair of
interface identities for links. Keep the provider manifest at pre-release version `0.0.1` and advance the comparison
engine to 15.0 so cached comparisons cannot be mistaken for this expanded graph.

## Consequences

- All three public writable Wireless models participate in collection, DiffSync comparison, target snapshots,
  dependency ordering, review, and explicit local apply.
- Selecting Wireless networks closes over DCIM geography, VLAN, device, and interface dependencies.
- Reversing the A and B ends of a link does not create a duplicate portable identity.
- Operators must provision or retain wireless pre-shared keys independently on each destination.
- Agents must embed the current provider manifest before sources can select the new dataset.
