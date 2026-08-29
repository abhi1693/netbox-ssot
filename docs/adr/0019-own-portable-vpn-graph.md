# ADR 0019: Own the portable VPN graph

## Context

NetBox's VPN app contains cryptographic policy, overlay, and generic termination models which were absent from the
provider graph. Tunnels and L2VPNs therefore could not be reviewed or applied, and contact assignments targeting them
were surfaced as unsupported. VPN terminations also cross app boundaries: tunnels terminate on physical or virtual
interfaces, while L2VPNs terminate on those interfaces or VLANs.

IKE policies contain pre-shared keys. Copying those values into immutable observations would expose credentials and
would incorrectly make source secret material part of a portable comparison.

## Decision

Add dependency-closed datasets for VPN cryptography, tunnels, and L2VPNs. Model IKE Proposals, IKE Policies, IPsec
Proposals, IPsec Policies, IPsec Profiles, Tunnel Groups, Tunnels, Tunnel Terminations, L2VPNs, and L2VPN Terminations
as typed resources in the shared registry.

Preserve proposal membership, profile policy references, ownership, tenancy, tags, route targets, outside IPs, and
typed generic termination targets. Tunnel terminations support DCIM Interfaces and VM Interfaces. L2VPN terminations
support those interface types plus VLANs. Unsupported content types retain their source marker and fail closed rather
than being detached or retargeted.

Treat IKE pre-shared keys as destination-local credentials. They are excluded from observations, evidence digests,
comparison, and apply. Creating an IKE Policy leaves its key blank; updating one preserves the destination value.

Use name identities for globally unique cryptographic and tunnel objects, slug identities for Tunnel Groups and
L2VPNs, and assigned-object identities for both termination models. Advance the provider manifest to 0.0.15 and the
comparison engine to 14.0 so older agents and cached comparisons cannot be mistaken for this expanded graph.

## Consequences

- All ten public writable VPN models participate in collection, DiffSync comparison, target snapshots, dependency
  ordering, review, and explicit local apply.
- Selecting tunnels closes over cryptographic policy, IP address, and interface dependencies. Selecting L2VPNs closes
  over route targets, VLANs, and both physical and virtual interfaces.
- Contact assignments can retain Tunnel Group, Tunnel, and L2VPN targets.
- Operators must provision or retain IKE pre-shared keys independently on each destination.
- Agents must embed provider manifest 0.0.15 before sources can select the new datasets.
