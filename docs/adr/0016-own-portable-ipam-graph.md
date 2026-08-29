# ADR 0016: Own the portable IPAM graph

## Context

The provider already collected RIRs and ASNs as supporting references, but the rest of NetBox IPAM was outside the
owned graph. That made address, VLAN, routing, FHRP, and service state invisible to reviewed reconciliation and left ASN
Roles as destination lookups. IPAM also contains generic relationships into DCIM and Virtualization plus the secret
FHRP authentication key, so copying API payloads directly would be unsafe.

## Decision

Add dependency-closed datasets for IPAM registries, routing, VLANs, prefixes and ranges, addresses and FHRP, and
services. Model all 18 public writable NetBox 4.6 IPAM resources as typed canonical records. Promote Role and the ASN
Role edge into the first-class support graph.

Support generic relationships only when their target is already owned: DCIM location/rack scopes, physical Interfaces,
Devices, and IPAM FHRP Groups. Records using Virtualization targets retain their source content-type marker and fail
portable identity validation, so review reports them as skipped instead of silently dropping the edge. VPN-owned VLAN
terminations and DCIM-owned primary-IP fields remain outside this bundle.

Treat `FHRPGroup.auth_key` as destination-local secret material. It is absent from canonical attributes and the raw
evidence digest is calculated from the portable projection rather than the source API object.

## Consequences

- Selecting a later IPAM dataset automatically includes its routing, VLAN, DCIM, and reference dependencies.
- Collection, DiffSync comparison, target snapshots, dependency ordering, and explicit local apply share one typed
  graph; no model-specific name lookup is needed for ASN Roles.
- Duplicate NetBox rows without a portable unique key fail closed through duplicate natural identities.
- The provider implementation advances to 0.0.12 and the comparison engine to 11.0. Agents must embed that collector
  manifest before the new datasets can be assigned.
