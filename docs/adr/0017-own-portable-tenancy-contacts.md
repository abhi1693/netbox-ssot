# ADR 0017: Own portable Tenancy contacts and assignments

- Amended by: ADR 0022

## Context

Tenant Groups and Tenants were already collected as supporting references, but NetBox's contact directory and generic
contact assignments were absent from the provider graph. That left four public writable Tenancy models invisible to
review and prevented a complete Tenancy round trip. Contact assignments can target models across several NetBox apps,
including apps which this provider does not yet own.

## Decision

Add a contact dataset for Contact Groups, Contact Roles, and Contacts, plus a separate assignment dataset whose
dependency closure includes every contact-capable model already represented by the provider. Contacts retain their
group memberships, ownership, tags, and explicit contact fields. Contact Assignments retain their role, priority,
tags, and typed generic target.

Support assignment targets only when the target model both advertises NetBox's contacts feature and has a typed model
in this provider graph. Preserve the source content type for every assignment. If an assignment targets an unsupported
app, collection emits the content-type marker without inventing a relationship; natural-identity validation then
surfaces the record as skipped instead of silently detaching or retargeting it.

Use a hierarchical slug identity for Contact Groups, a slug identity for Contact Roles, and a case-insensitive name
identity for Contacts. Duplicate contact names fail closed as source or destination identity conflicts. A Contact
Assignment is identified by its target object, Contact, and Contact Role, matching NetBox's uniqueness constraint.

## Consequences

- All six public writable Tenancy models now participate in collection, DiffSync comparison, target snapshots,
  dependency ordering, and explicit local apply.
- Selecting contact assignments pulls the contact directory and all supported target datasets into one complete plan.
- Contact details are intentionally treated as portable configuration and therefore appear in immutable evidence and
  review. Operators must scope access to that evidence appropriately.
- Assignments to provider-owned contact-capable models are supported; other target models remain visible as skipped.
- The provider implementation remains at pre-release version `0.0.1`; the comparison engine advances to 12.0. Agents
  must embed the current collector manifest before the new datasets can be assigned.
