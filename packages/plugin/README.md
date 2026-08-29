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

The selectable NetBox target boundary covers every public writable NetBox 4.6 DCIM resource through dependency-closed
datasets: geography; device, module, and rack catalogs; component templates; racks and reservations; devices and their
installed components; inventory and MAC addresses; power; and cabling. Its automatic support graph also includes Tags,
Owner Groups, Owners, Tenant Groups, Tenants, RIRs, and ASNs. Internal aggregate/helper rows such as Cable Terminations,
Cable Paths, Port Template Mappings, and Port Mappings are represented through their owning object rather than exposed
as standalone resources.

Cross-app references are intentionally resolve-only. Config Templates and ASN Roles must resolve uniquely, and Rack
Reservation users must match a unique local username. Device IP/cluster assignments, Interface IPAM and wireless
policy fields, VM-interface MAC assignments, circuit/wireless cable endpoints, custom fields, contacts, and images are
outside this provider-owned DCIM boundary. Records that depend on one of those unsupported relationships fail closed;
the review page explains why Apply is unavailable instead of constructing a partial object.
