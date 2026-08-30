from typing import ClassVar

__version__ = "0.0.1"

try:
    from netbox.plugins import PluginConfig
except ModuleNotFoundError as exc:
    if exc.name not in {"django", "netbox"}:
        raise
    config = None
else:

    class NetBoxSSOTConfig(PluginConfig):
        name = "netbox_ssot"
        verbose_name = "NetBox SSoT"
        description = "Provider-driven discovery with durable review and safe reconciliation"
        version = __version__
        base_url = "ssot"
        min_version = "4.6.0"
        max_version = "4.6.99"
        author = "Abhishek Haris"
        default_settings: ClassVar[dict[str, str | int | bool]] = {
            "agent_signature_max_age_seconds": 300,
            "agent_key_rotation_grace_seconds": 600,
            "maximum_batch_bytes": 67_108_864,
            "maximum_observations_per_batch": 100_000,
            "apply_job_timeout_seconds": 86_400,
            "comparison_job_timeout_seconds": 86_400,
            "provider_entry_point_group": "netbox_ssot.providers",
            "pause_scheduled_collections_until_resolved": False,
            "require_separate_reviewer_and_applier": False,
        }

    config = NetBoxSSOTConfig

__all__ = ["__version__", "config"]
