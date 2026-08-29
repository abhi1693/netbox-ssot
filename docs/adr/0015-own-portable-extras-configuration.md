# ADR 0015: Own portable Extras configuration without executable, personal, or secret state

- Amended by: ADR 0022

## Context

NetBox's Extras app mixes declarative configuration with executable Python, user-specific UI preferences, generated
notifications and history, binary attachments, and credential-bearing webhook fields. DCIM Device Roles, Platforms,
and Devices also reference Config Templates, which were previously resolved as destination-local objects and prevented
the provider graph from being self-contained.

## Decision

Add five dependency-closed datasets covering Custom Field Choice Sets, Custom Fields, Custom Links, Export Templates,
Saved Filters, Table Configurations, Config Context Profiles, Config Contexts, Config Templates, Webhooks, Notification
Groups, and Event Rules. Config Templates become first-class resources and DCIM relationships.

Object-type assignments and supported ownership, tag, user/group, context qualifier, and action relationships are typed
graph edges. Export/config templates and contexts carry their materialized inline content; Data File bindings and sync
state are not copied. Applying these definitions detaches a destination Data File binding so the reviewed inline value
is the actual resulting state.

Webhook secret, additional-header, and CA-path fields stay destination-local. The collector hashes only the portable
webhook projection, so those values cannot enter immutable evidence through a digest. Event Rules targeting Webhooks
or Notification Groups are supported; Script actions fail closed. ADR 0022 subsequently adds supported Virtualization
Config Context qualifiers and defines Tag slugs as stable identifiers for tag qualifiers.

Script Modules and Scripts, Dashboards, Bookmarks, Notifications, Subscriptions, TaggedItem join rows, Image
Attachments, and Journal Entries are excluded because they are executable, personal, generated, binary, or historical
rather than portable desired state.

## Consequences

- The NetBox provider implementation remains at pre-release version `0.0.1`; the comparison engine advances to 10.0.
- DCIM collections that use Config Templates automatically include the template dataset.
- A reviewed plan can create an entire supported Extras dependency graph without local name-based template lookups.
- Explicit credentials and unsupported executable/runtime surfaces are never copied implicitly.
