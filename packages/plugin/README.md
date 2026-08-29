# NetBox SSoT plugin

This package contains the NetBox integration, persistence, review UI, planning gateway, and guarded apply service. The
current alpha exposes the provider catalog, schema-driven source creation, public-key agent enrollment, signed batch
ingestion, immutable collection-run inspection, durable read-only DiffSync comparison previews, and a separately
permissioned review and atomic apply service with immutable decisions, approvals, receipts, and source-object bindings.

Grant `netbox_ssot.add_comparisonreview` to operators who may record decisions and finalize approvals or rejections. Keep
`netbox_ssot.add_applyrun` and the relevant NetBox model permissions limited to operators who may mutate the target.
Set `require_separate_reviewer_and_applier` in `PLUGINS_CONFIG` when those roles must be held by different users.

Add `netbox_ssot` to NetBox's `PLUGINS` list after installing the workspace package.

Run NetBox migrations after installation:

```shell
python manage.py migrate
```

The ingest endpoint is `/api/plugins/ssot/ingest/batches/`. It accepts only timestamped Ed25519 signatures
from enabled agents assigned to the batch's registered source.

The selectable NetBox target boundary is Regions, Sites, and Locations. Their automatic support bundle includes Tags,
Owner Groups, Owners, Tenant Groups, Tenants, Site Groups, RIRs, and ASNs. Other resource kinds from historical batches
are retained as evidence but are marked unsupported in new comparisons.
