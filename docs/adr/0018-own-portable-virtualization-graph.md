# ADR 0018: Own the portable Virtualization graph

- Amended by: ADR 0022

## Context

NetBox's Virtualization app was absent from the provider graph. Clusters and virtual machines therefore appeared only
as unsupported generic references from IPAM, DCIM, Extras, and Tenancy, while none of the app's seven public writable
models could participate in collection, review, or apply.

Virtualization is also not an isolated hierarchy. Clusters may provide VLAN scope and config-context qualifiers;
virtual interfaces may own IP, FHRP, and MAC assignments; virtual machines may own services and contact assignments.
Treating only the seven direct models as portable would leave otherwise valid dependency plans incomplete.

## Decision

Add dependency-closed datasets for cluster organization, virtual machines, and VM components. Model Cluster Types,
Cluster Groups, Clusters, Virtual Machine Types, Virtual Machines, VM Interfaces, and Virtual Disks as typed resources
in the shared registry. Preserve cluster scope, VM placement, interface hierarchy and VLAN relationships, ownership,
tags, and other portable declared fields.

Extend existing generic relationships to recognize Virtualization targets: VM Interfaces for IP addresses, FHRP
assignments, and MAC addresses; Virtual Machines for services and contacts; and Clusters and Cluster Groups for VLAN
and config-context scope. Unsupported content types continue to fail closed instead of being silently detached.

ADR 0022 subsequently makes primary VM IP and interface primary MAC selectors explicit deferred relationships once
their assignment resources exist. Virtual disk rows own a VM's aggregate disk value once they exist.

Use slug identities for organizational types, placement-qualified identities for clusters and VMs, and parent-VM plus
name identities for components. Keep the provider manifest at pre-release version `0.0.1` and advance the comparison
engine to 13.0 so cached comparisons cannot be mistaken for the expanded graph.

## Consequences

- All seven public writable Virtualization models participate in collection, DiffSync comparison, target snapshots,
  dependency ordering, review, and explicit local apply.
- Selecting dependent IPAM, DCIM, Extras, or Tenancy datasets closes over the required Virtualization datasets.
- Virtualization's cross-app generic assignments retain their typed targets and can be recreated safely.
- Agents must embed the current provider manifest before sources can select the new datasets.
