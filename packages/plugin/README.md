# NetBox SSoT plugin

This package contains the NetBox integration, persistence, review UI, planning gateway, and guarded apply service. The
current alpha exposes the provider catalog, schema-driven source creation, public-key agent enrollment, signed batch
ingestion, immutable collection-run inspection, durable read-only DiffSync comparison previews, and a separately
permissioned review and atomic typed-adapter apply service with immutable decisions, approvals, receipts, and
source-object bindings. Apply recalculates the reviewed delta and executes that exact object through
`source.sync_to(target)`; reverse direction remains disabled until a provider advertises remote write capability.

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

The selectable NetBox target boundary covers portable Core and Extras configuration, every public writable NetBox 4.6
DCIM and Circuits resource, and the portable Users access-control graph through dependency-closed datasets: Data
Sources, Users, Groups, Object Permissions, Extras customization/templates/views/contexts/automation, geography;
device, module, rack, and circuit catalogs; component templates; racks and
reservations; devices and their installed components; inventory and MAC addresses; power; physical and virtual
circuits; circuit groups; and cabling. Its automatic support graph also includes Tags, Owner Groups, Owners, Tenant
Groups, Tenants, RIRs, and ASNs. Internal aggregate/helper rows such as Cable Terminations, Cable Paths, Port Template
Mappings, and Port Mappings are represented through their owning object rather than exposed as standalone resources.

Config Templates are first-class DCIM dependencies. ASN Roles remain resolve-only, and Rack Reservation users must
match a unique local username. Passwords, superuser state, API Tokens, login activity, built-in Django permissions,
private UserConfig preferences, Owner membership bridges, Data Source credentials, unknown backend parameters,
generated files and synchronization/job state, Device IP/cluster assignments, Interface IPAM and wireless policy
fields, VM-interface MAC assignments, wireless cable endpoints, contacts, and images are outside this provider-owned
boundary. Extras scripts, personal UI state, generated notifications/history, binary attachments, subscriptions, and
explicit webhook credentials are also excluded. Only the Git branch parameter is portable; destination-only Data
Source parameters are preserved. New users receive unusable passwords. Circuit Terminations are supported as cable endpoints. Records that
depend on an unsupported relationship fail closed; the review page explains why Apply is unavailable instead of
constructing a partial object.
