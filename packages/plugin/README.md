# NetBox SSoT plugin

The plugin is the NetBox control plane for SSoT. It owns source and agent administration, signed observation ingestion,
comparison, review, guarded application, retention, and the user interface.

It supports NetBox 4.6 and requires Python 3.12 or newer.

## Install a release

Tagged GitHub releases include wheels and source distributions for `netbox-ssot-contracts`,
`netbox-ssot-provider-netbox`, and `netbox-ssot`. Download the three matching wheels from the release and install them
together so pip can resolve their exact internal dependency versions:

```shell
python -m pip install \
  ./netbox_ssot_contracts-<version>-py3-none-any.whl \
  ./netbox_ssot_provider_netbox-<version>-py3-none-any.whl \
  ./netbox_ssot-<version>-py3-none-any.whl
```

Release assets include a `checksums.txt` file covering the Python distributions and agent archives.

## Install from the workspace

For development or evaluation before a tagged release, install the plugin and its local dependencies from a repository
checkout:

```shell
python -m pip install \
  -e packages/contracts \
  -e providers/netbox \
  -e packages/plugin
```

Add the plugin to the NetBox configuration:

```python
PLUGINS = [
    "netbox_ssot",
]
```

Then apply its database migration:

```shell
python manage.py migrate
```

Restart the NetBox application processes after installation or configuration changes.

## Configuration

The plugin works with its safe defaults. Optional policy settings belong under `PLUGINS_CONFIG`:

```python
PLUGINS_CONFIG = {
    "netbox_ssot": {
        "require_separate_reviewer_and_applier": True,
        "pause_scheduled_collections_until_resolved": False,
    },
}
```

`require_separate_reviewer_and_applier` enforces four-eyes approval. When enabled, the person who finalizes a review
cannot apply it. `pause_scheduled_collections_until_resolved` stops automatic collection for a source while its newest
comparison still needs a decision.

Additional settings control signature age, key-rotation grace periods, ingestion limits, background-job timeouts, and
the provider entry-point group. Their defaults are defined in
[`netbox_ssot/__init__.py`](src/netbox_ssot/__init__.py); change them only for a documented operational requirement.

## Permissions

Review and apply are intentionally separate:

- `netbox_ssot.add_comparisonreview` permits record decisions and final approval or rejection.
- `netbox_ssot.add_applyrun` permits starting an application.
- An applier must also hold the relevant add/change permissions for every affected NetBox model.

Source and agent administration should be delegated through the corresponding NetBox model permissions. Avoid giving
reviewers broad target-model permissions unless they are also expected to apply changes.

## Background work

Comparison preparation and application can run through NetBox's background worker. The plugin rechecks the requesting
user's permissions when an apply job starts, and refreshes drift after a successful application. NetBox must therefore
have a functioning task queue and worker when background execution is selected.

## Retention

Collection evidence follows the policy configured on each source. The newest collection, newest successful collection,
and any collection referenced by a comparison, review, or application are protected automatically.

Preview cleanup without deleting anything:

```shell
python manage.py prune_ssot_collections
```

Limit the preview to one source with `--source <uuid-or-exact-name>`. After reviewing the output, add `--apply` to
delete only the eligible runs and their observations.

## Security boundary

- Agents authenticate enrollment, control polling, command results, and ingestion through the plugin API.
- Observation batches require timestamped Ed25519 signatures from an enabled agent assigned to the source.
- The plugin stores agent public keys, not their private signing keys.
- Provider configuration schemas identify secret references; their resolved values never enter NetBox.
- Comparison and review do not mutate target objects.
- Apply reloads the target graph, validates permissions and reviewed digests, and commits supported writes atomically.
- Delete operations are not supported.

The signed ingestion endpoint is `/api/plugins/ssot/ingest/batches/`. It is an agent protocol endpoint, not a general
user-facing import API.

## Package map

- `api/` — enrollment, control, command, and ingestion endpoints
- `ingestion/` — signed immutable observation acceptance
- `planning/` — resource definitions, target projection, DiffSync adapters, and comparison preparation
- `application/` — approved-plan validation and NetBox mutation
- `templates/`, `tables.py`, and `filtersets.py` — native NetBox user interface
- `models.py` — sources, agents, collections, comparisons, review events, and apply receipts
- `jobs.py` — NetBox background jobs

Provider-specific datasets and field coverage live in the
[NetBox provider documentation](../../providers/netbox/README.md). Shared wire and manifest types live in the
[contracts package](../contracts/README.md).

## Development

Use the repository [contributing guide](../../CONTRIBUTING.md) for local validation. Database-backed plugin tests require
NetBox 4.6, PostgreSQL, and Redis; the repository CI workflow supplies those services.
