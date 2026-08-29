# ADR 0022: Close the portable NetBox model and field graph

- Status: Accepted
- Date: 2026-08-30

## Context

The app-specific provider expansions covered all intended REST models, but several portable fields still crossed app
boundaries without an ownership rule. Object-valued custom fields contained source database primary keys; device and
VM primary selectors, Device clusters, installed Device Bay members, physical Interface VLAN/VRF/wireless fields, and
Config Context tag qualifiers were incomplete. Owner membership also sat outside the selectable Users graph. A model
count alone therefore overstated end-to-end portability.

## Decision

Treat the provider as one typed dependency graph across Core, Extras, Users, Tenancy, IPAM, DCIM, Circuits,
Virtualization, VPN, and Wireless. The graph owns 119 of NetBox 4.6's 126 public writable REST models. The seven
intentional exclusions are API Token, Bookmark, Image Attachment, Journal Entry, Notification, Script Module, and
Subscription because they are credential-bearing, personal, binary, historical, generated, or executable surfaces.

Carry custom-field values for all 89 supported models whose NetBox 4.6 REST serializers expose them. Primitive values
remain in the canonical `/custom_fields` map. Object and multi-object values use typed relationships and natural
identities; source primary keys never become destination values. Each populated custom field depends on its Custom
Field definition. Definitions are in the dependency closure before ordinary infrastructure records. Unsupported
related object types produce an explicit skipped record instead of a lossy value. Custom-field storage exists on four
additional models but is not exposed by their serializers: Config Context Profile, VLAN Translation Policy, VLAN
Translation Rule, and Circuit Group Assignment. Those values remain destination-local.

Move Owner Groups and Owners into the Users dataset and own their user/group membership bridges. Model Rack
Reservation users as required User relationships and include the Users dataset in their dependency closure. Use Tag
slugs as stable source identifiers for API surfaces which serialize only slugs, including Config Context qualifiers. Add typed
ownership for Device clusters, installed Device Bay members, physical Interface VLANs, VLAN translation policy, VRF,
wireless LAN membership, and device/VM primary IP and VM-interface primary MAC selectors.

Primary IP, primary MAC, and wireless membership fields are projected only when the dataset which owns their target
records is present. Their writes run after assignment records exist. This breaks dependency cycles and prevents a
partial collection from interpreting an omitted cross-app field as a request to clear destination state.

Keep `implementation_version` at `0.0.1` under ADR 0021. Do not advance the comparison engine version until that
separate invalidation step is explicitly authorized.

## Consequences

- All 119 portable models have collector mappings, canonical identities and fields, target projections, dependency
  rules, permission checks, and write paths.
- Custom Field definitions and values can be created in the same reviewed transaction without source-PK coupling.
- Infrastructure collections include the Users ownership principals they reference.
- Rack Reservations can create their required Users in the same dependency-safe plan.
- Partial dataset selections preserve destination-owned selectors and memberships outside their collected graph.
- The intentional seven-model exclusion list is explicit and can be regression-audited against future NetBox releases.
