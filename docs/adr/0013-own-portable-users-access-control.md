# ADR 0013: Own portable Users access control without credentials

- Status: Accepted
- Date: 2026-08-29

## Context

NetBox's Users app exposes Users, Groups, Object Permissions, API Tokens, Owner Groups, Owners, and a current-user
preferences endpoint. These do not share one safe portability boundary. User, Group, and Object Permission records can
be represented as declarative access policy. API Tokens contain authentication material that cannot be read back or
recreated with the same value, and creating a replacement would silently rotate a credential. UserConfig is private
per-session UI state and cannot enumerate every user's preferences. Passwords and login activity are authentication
state rather than configuration.

Owner Groups and Owners are already collected automatically as infrastructure ownership references. Pulling their
user/group membership bridge into the hidden reference dataset would make every DCIM or Circuits collection implicitly
authoritative for account authorization.

## Decision

Add a selectable **Users and access control** dataset containing Object Permissions, Groups, and Users. Object
Permissions own their selected object types, actions, enabled state, constraints, and description. Groups own their
Object Permission memberships. Users own their Group and direct Object Permission memberships plus username, ordinary
profile fields, and active state. Dependencies are ordered Object Permission, then Group, then User.

Usernames are matched case-insensitively, consistent with NetBox validation. Group and Object Permission names are
matched exactly; duplicate names fail closed as ambiguous identities. New users are created with unusable passwords.
Existing passwords are never read or changed. Superuser state, API Tokens, login timestamps, built-in Django
permissions, UserConfig data, and Owner-to-user/group memberships are not collected or applied.

The dataset uses the same immutable observation, DiffSync preview, append-only review, target revalidation, permission
check, and transactional `source.sync_to(target)` path as other provider bundles. The comparison engine version and
provider implementation patch version advance because the supported resource graph changed.

## Consequences

- Access-control changes are visible as ordinary reviewed records and relationships instead of hidden side effects.
- A source cannot inject passwords, token material, or superuser status into observations or plans.
- Creating a user never creates a usable login credential; a destination administrator must establish authentication
  through a separate approved process.
- Selecting infrastructure datasets does not import Owner membership bridges or the Users dataset implicitly.
- Existing comparisons must be regenerated under the new engine version before apply.
