# ADR 0002: Use versioned provider entry points and declarative manifests

- Status: Accepted
- Date: 2026-08-28

## Context

A dynamic provider ecosystem needs discovery and configuration UI without database-controlled code loading or provider-controlled executable frontend content.

## Decision

Provider distributions register descriptor factories in the `netbox_ssot.providers` Python entry-point group. Each factory exposes a validated `ProviderManifest`; it does not perform collection. The corresponding collector is compiled into the Go agent and advertises the same provider ID and contract version.

The manifest uses a constrained JSON Schema subset plus safe UI annotations. It cannot contain JavaScript, HTML, Django templates, Python paths, or remote schema references. The plugin converts it into its own normalized field model before rendering.

## Consequences

- Installing a control-plane descriptor remains an explicit administrator trust decision, while deploying an agent binary determines the available edge collectors.
- Source records store stable provider IDs, never import paths.
- Broken or incompatible providers can be isolated in the catalog without preventing plugin startup.
- Rich provider-specific UI must be expressed through supported schema features or added to the shared contract deliberately.
