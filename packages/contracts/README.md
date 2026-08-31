# NetBox SSoT contracts

`netbox-ssot-contracts` is the versioned, NetBox-independent Python boundary shared by the plugin and provider
distributions. It defines what a provider may declare and what an agent may submit without importing Django, NetBox,
DiffSync, or a vendor SDK.

The current contract version is `1.0`.

## What belongs here

- Provider manifests, capabilities, dataset declarations, and data-model mappings
- Closed provider configuration schemas and secret-reference metadata
- Agent enrollment, control, assignment, command, and key-rotation messages
- Collection requests, observations, evidence, scope, completeness, and batch results
- Reconciliation proposals, field changes, decisions, and safety policy
- Validation and redaction helpers used at package boundaries

Implementation behavior does not belong here. Collection stays in provider code, persistence and mutation stay in the
plugin, and customer-edge orchestration stays in the Go runtime.

## Design rules

- Contract models are strict and reject unknown or unsafe input.
- Secrets are represented only by opaque references and are never valid observation values.
- Provider manifests are declarative data; they cannot contain import paths, templates, scripts, or HTML.
- Dataset dependencies are explicit and deterministic.
- Provider field ownership is explicit: `complete` owns the full canonical record, while `observed` preserves
  destination fields and relationships that the provider omitted.
- Every complete batch identifies its declared scope and carries a completeness token.
- Observations use canonical resource kinds, stable external identities, typed relationships, and source evidence.
- Breaking changes require a new contract version and an explicit compatibility path.

## Package map

- `agent.py` — agent protocol messages
- `base.py` — strict shared scalar and identifier types
- `manifest.py` — provider capabilities, datasets, mappings, and canonical resource kinds
- `observation.py` — immutable collection records and completeness
- `planning.py` — provider-neutral reconciliation structures
- `provider.py` — provider definitions and collection requests
- `schema.py` — safe configuration-schema normalization, validation, and redaction

## Provider mappings

Dataset declarations map provider-native models to canonical resource kinds. The destination resolves those canonical
kinds to its installed models, so providers do not embed destination-specific model names or URLs.

A provider may declare one non-secret instance URL field and safe relative paths for its source models. The plugin can
then link mapping pages to the source system without storing an absolute source URL in this contract. Missing link
metadata degrades to plain text.

## Development

Install the workspace and run the shared validation gate from the repository root:

```shell
uv sync --all-packages
uv run pytest tests/test_schema_contract.py tests/test_observation_contract.py tests/test_agent_contract.py
```

Changes to public models require contract tests and, when they alter a trust boundary or compatibility rule, an
[architecture decision](../../docs/adr). Do not change the contract version as part of an ordinary implementation
change.
