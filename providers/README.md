# Providers

Providers describe a source system and collect its data into the canonical NetBox SSoT observation contract. They are
read-only integrations: a provider can discover source state but cannot acquire permission to change the local NetBox
target.

## Included providers

| Provider | Implementation | Status | Documentation |
| --- | --- | --- | --- |
| NetBox | `0.0.1` | Alpha | [NetBox provider](netbox/README.md) |

Provider implementation versions remain at `0.0.1` during pre-release development. They change only as part of an
explicitly authorized provider release. The shared contract version is managed independently.

## Provider structure

A provider normally includes:

- a declarative `manifest.json` consumed by both the control plane and agent;
- a Python package registered through the `netbox_ssot.providers` entry-point group;
- a Go collector compiled into the customer-edge agent;
- contract, collection, projection, and completeness tests; and
- provider-specific documentation beside the implementation.

The manifest is the source of truth for identity, versions, configuration fields, secret references, capabilities,
datasets, dependencies, and source-to-canonical model mappings. It must validate against the
[shared contracts](../packages/contracts/README.md).

## Safety requirements

- Collectors read source APIs only.
- Credentials are resolved by the agent from `env://` or absolute `file:///` references.
- Secret values never appear in configuration returned to the plugin, observations, logs, or fixtures.
- Unknown datasets and incompatible contract versions fail closed.
- A complete result is emitted only after the full declared dataset scope has been collected.
- Provider-specific payloads are normalized before crossing the observation boundary.

See the [contributing guide](../CONTRIBUTING.md) before adding a provider or changing a public provider contract.
