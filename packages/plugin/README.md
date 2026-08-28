# NetBox SSoT plugin

This package contains the NetBox integration, persistence, review UI, planning gateway, and guarded apply service. The
current alpha exposes the provider catalog, schema-driven source creation, public-key agent enrollment, signed batch
ingestion, immutable collection-run inspection, durable read-only DiffSync comparison previews, and a separately
permissioned atomic apply service with immutable receipts and source-object bindings.

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
