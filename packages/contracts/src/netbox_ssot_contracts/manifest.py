from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, HttpUrl, model_validator

from .base import ContractModel, Identifier, JsonPointer
from .schema import normalize_config_schema, validate_config_schema

ContractVersion = Literal["1.0"]
CURRENT_CONTRACT_VERSION: ContractVersion = "1.0"


class ExecutionMode(StrEnum):
    AGENT = "agent"


class ProviderCapability(StrEnum):
    SOURCE_READ = "source_read"


class ResourceKind(StrEnum):
    TAG = "tag"
    OWNER_GROUP = "owner_group"
    OWNER = "owner"
    TENANT_GROUP = "tenant_group"
    REGION = "region"
    SITE_GROUP = "site_group"
    SITE = "site"
    LOCATION = "location"
    TENANT = "tenant"
    RIR = "rir"
    ASN = "asn"
    MANUFACTURER = "manufacturer"
    DEVICE_ROLE = "device_role"
    PLATFORM = "platform"
    DEVICE_TYPE = "device_type"
    RACK_GROUP = "rack_group"
    RACK_ROLE = "rack_role"
    RACK_TYPE = "rack_type"
    RACK = "rack"
    RACK_RESERVATION = "rack_reservation"
    MODULE_TYPE_PROFILE = "module_type_profile"
    MODULE_TYPE = "module_type"
    CONSOLE_PORT_TEMPLATE = "console_port_template"
    CONSOLE_SERVER_PORT_TEMPLATE = "console_server_port_template"
    POWER_PORT_TEMPLATE = "power_port_template"
    POWER_OUTLET_TEMPLATE = "power_outlet_template"
    INTERFACE_TEMPLATE = "interface_template"
    FRONT_PORT_TEMPLATE = "front_port_template"
    REAR_PORT_TEMPLATE = "rear_port_template"
    MODULE_BAY_TEMPLATE = "module_bay_template"
    DEVICE_BAY_TEMPLATE = "device_bay_template"
    INVENTORY_ITEM_TEMPLATE = "inventory_item_template"
    INVENTORY_ITEM_ROLE = "inventory_item_role"
    VIRTUAL_CHASSIS = "virtual_chassis"
    DEVICE = "device"
    VIRTUAL_DEVICE_CONTEXT = "virtual_device_context"
    MODULE = "module"
    CONSOLE_PORT = "console_port"
    CONSOLE_SERVER_PORT = "console_server_port"
    POWER_PORT = "power_port"
    POWER_OUTLET = "power_outlet"
    INTERFACE = "interface"
    FRONT_PORT = "front_port"
    REAR_PORT = "rear_port"
    MODULE_BAY = "module_bay"
    DEVICE_BAY = "device_bay"
    INVENTORY_ITEM = "inventory_item"
    POWER_PANEL = "power_panel"
    POWER_FEED = "power_feed"
    CABLE_BUNDLE = "cable_bundle"
    CABLE = "cable"
    PROVIDER = "provider"
    PROVIDER_ACCOUNT = "provider_account"
    PROVIDER_NETWORK = "provider_network"
    CIRCUIT_TYPE = "circuit_type"
    CIRCUIT_GROUP = "circuit_group"
    CIRCUIT = "circuit"
    CIRCUIT_TERMINATION = "circuit_termination"
    VIRTUAL_CIRCUIT_TYPE = "virtual_circuit_type"
    VIRTUAL_CIRCUIT = "virtual_circuit"
    VIRTUAL_CIRCUIT_TERMINATION = "virtual_circuit_termination"
    CIRCUIT_GROUP_ASSIGNMENT = "circuit_group_assignment"
    OBJECT_PERMISSION = "object_permission"
    USER_GROUP = "user_group"
    USER = "user"
    DATA_SOURCE = "data_source"
    CUSTOM_FIELD_CHOICE_SET = "custom_field_choice_set"
    CUSTOM_FIELD = "custom_field"
    CUSTOM_LINK = "custom_link"
    EXPORT_TEMPLATE = "export_template"
    SAVED_FILTER = "saved_filter"
    TABLE_CONFIG = "table_config"
    CONFIG_CONTEXT_PROFILE = "config_context_profile"
    CONFIG_CONTEXT = "config_context"
    CONFIG_TEMPLATE = "config_template"
    WEBHOOK = "webhook"
    NOTIFICATION_GROUP = "notification_group"
    EVENT_RULE = "event_rule"
    VLAN = "vlan"
    PREFIX = "prefix"
    IP_ADDRESS = "ip_address"
    MAC_ADDRESS = "mac_address"
    WLAN = "wlan"
    DNS_RECORD = "dns_record"
    LINK_CANDIDATE = "link_candidate"


class CompletenessMode(StrEnum):
    NONE = "none"
    DECLARED_SCOPE = "declared_scope"


class DataModelMapping(ContractModel):
    source_name: str = Field(min_length=1, max_length=80)
    source_model: str = Field(min_length=1, max_length=120)
    source_path: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_/-]*/$")
    destination_kind: ResourceKind


class DatasetDefinition(ContractModel):
    id: Identifier
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    resource_kinds: tuple[ResourceKind, ...] = Field(min_length=1)
    default_enabled: bool = True
    selectable: bool = True
    completeness: CompletenessMode = CompletenessMode.DECLARED_SCOPE
    depends_on: tuple[Identifier, ...] = ()
    data_mappings: tuple[DataModelMapping, ...] = ()

    @model_validator(mode="after")
    def validate_data_mappings(self) -> DatasetDefinition:
        if not self.data_mappings:
            return self
        pairs = [(mapping.source_model, mapping.destination_kind) for mapping in self.data_mappings]
        if len(set(pairs)) != len(pairs):
            raise ValueError("dataset data mappings must be unique")
        mapped_kinds = {mapping.destination_kind for mapping in self.data_mappings}
        if mapped_kinds != set(self.resource_kinds):
            raise ValueError("dataset data mappings must cover every resource kind exactly")
        return self


class AgentCompatibility(ContractModel):
    protocol_version: Literal["1.0"] = "1.0"
    minimum_agent_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
    collector_id: Identifier


class ProviderManifest(ContractModel):
    provider_id: Identifier
    display_name: str = Field(min_length=1, max_length=80)
    icon_class: str = Field(default="mdi mdi-database-outline", pattern=r"^mdi mdi-[a-z0-9-]+$")
    description: str = Field(min_length=1, max_length=500)
    instance_url_field: Identifier | None = None
    implementation_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
    contract_version: ContractVersion = CURRENT_CONTRACT_VERSION
    documentation_url: HttpUrl
    execution_modes: tuple[ExecutionMode, ...] = Field(min_length=1)
    capabilities: tuple[ProviderCapability, ...] = Field(min_length=1)
    agent_compatibility: AgentCompatibility
    config_schema: dict[str, Any]
    secret_fields: tuple[JsonPointer, ...] = ()
    datasets: tuple[DatasetDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> ProviderManifest:
        validate_config_schema(self.config_schema)

        if self.instance_url_field is not None:
            url_definition = self.config_schema.get("properties", {}).get(self.instance_url_field)
            if not isinstance(url_definition, dict):
                raise ValueError("instance_url_field must identify a configuration property")
            if url_definition.get("type") != "string" or url_definition.get("format") != "uri":
                raise ValueError("instance_url_field must identify a string URI property")
            if f"/{self.instance_url_field}" in self.secret_fields:
                raise ValueError("instance_url_field cannot identify a secret property")

        if len(set(self.execution_modes)) != len(self.execution_modes):
            raise ValueError("execution_modes must be unique")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        if self.agent_compatibility.collector_id != self.provider_id:
            raise ValueError("agent collector ID must equal the provider ID")

        dataset_ids = [dataset.id for dataset in self.datasets]
        if len(set(dataset_ids)) != len(dataset_ids):
            raise ValueError("dataset IDs must be unique")

        known_ids = set(dataset_ids)
        for dataset in self.datasets:
            unknown = set(dataset.depends_on) - known_ids
            if unknown:
                raise ValueError(f"dataset {dataset.id!r} depends on unknown datasets: {sorted(unknown)}")
            if dataset.id in dataset.depends_on:
                raise ValueError(f"dataset {dataset.id!r} cannot depend on itself")

        schema_secret_fields = {field.pointer for field in normalize_config_schema(self.config_schema) if field.secret}
        declared_secret_fields = set(self.secret_fields)
        if schema_secret_fields != declared_secret_fields:
            raise ValueError(
                "secret_fields must exactly match schema properties marked writeOnly or x-netbox-ssot-secret"
            )
        return self
