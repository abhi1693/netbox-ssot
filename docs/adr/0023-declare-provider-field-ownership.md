# ADR 0023: Declare provider field ownership

- Status: Accepted
- Date: 2026-08-31

## Context

The original NetBox provider projects the complete supported serializer surface for each collected record. A vendor
collector such as UniFi has a narrower source model: it can authoritatively observe names, operational state, device
catalog relationships, interfaces, networks, and wireless configuration, but it does not know destination-only NetBox
metadata such as tenants, tags, comments, rack placement, or custom fields.

The existing comparison engine interpreted every missing source field and relationship as absent desired state. If a
sparse provider reused that behavior, a reviewed apply could clear valid destination metadata even though the provider
never observed it. Collection completeness describes enumeration of objects in a declared scope; it does not establish
field-level authority.

## Decision

Add `field_ownership` to the provider manifest with two modes. `complete` means the provider owns the full canonical
record and retains the existing replacement behavior. `observed` means the provider owns only attributes and
relationships present in its observations.

For an `observed` provider, comparison overlays emitted source values onto the freshly loaded matching target record
before calculating and persisting the reviewed desired state. Source-present values win. Target-only values and
relationships are preserved. New records contain only source-present values and destination defaults. Exact natural
identity matching, conflict handling, completeness checks, review requirements, target snapshot validation, atomic
application, and the no-delete invariant remain unchanged.

The NetBox provider explicitly declares `complete`. The UniFi provider declares `observed`. Existing third-party
manifests that omit the field retain `complete` as the contract default for compatibility. Increment the comparison
engine version to `1.1` so comparisons produced before this ownership rule cannot be reused.

## Consequences

- UniFi can update fields it actually observes without erasing destination-only NetBox context.
- A provider cannot clear an omitted field or relationship in `observed` mode. A future contract must add an explicit
  unset operation if that behavior is needed.
- Field ownership is independent of dataset completeness: complete collection still means all objects in the declared
  scope were enumerated, not that every canonical field was observed.
- Adding a sparse provider now requires an explicit ownership decision and tests covering preservation behavior.
