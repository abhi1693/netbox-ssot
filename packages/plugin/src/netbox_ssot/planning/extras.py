from __future__ import annotations

from typing import Final

EXTRAS_RESOURCE_KINDS: Final = frozenset(
    {
        "custom_field_choice_set",
        "custom_field",
        "custom_link",
        "export_template",
        "saved_filter",
        "table_config",
        "config_context_profile",
        "config_context",
        "config_template",
        "webhook",
        "notification_group",
        "event_rule",
    }
)

EXTRAS_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "custom_field_choice_set": (
        "name",
        "description",
        "base_choices",
        "extra_choices",
        "choice_colors",
        "order_alphabetically",
    ),
    "custom_field": (
        "type",
        "name",
        "label",
        "group_name",
        "description",
        "required",
        "unique",
        "search_weight",
        "filter_logic",
        "ui_visible",
        "ui_editable",
        "is_cloneable",
        "default",
        "related_object_filter",
        "weight",
        "validation_minimum",
        "validation_maximum",
        "validation_regex",
        "validation_schema",
        "comments",
    ),
    "custom_link": (
        "name",
        "enabled",
        "link_text",
        "link_url",
        "weight",
        "group_name",
        "button_class",
        "new_window",
    ),
    "export_template": (
        "name",
        "description",
        "environment_params",
        "template_code",
        "mime_type",
        "file_name",
        "file_extension",
        "as_attachment",
    ),
    "saved_filter": (
        "name",
        "slug",
        "description",
        "weight",
        "enabled",
        "shared",
        "parameters",
    ),
    "table_config": (
        "table",
        "name",
        "description",
        "weight",
        "enabled",
        "shared",
        "columns",
        "ordering",
    ),
    "config_context_profile": ("name", "description", "schema", "comments"),
    "config_context": ("name", "weight", "description", "is_active", "data"),
    "config_template": (
        "name",
        "description",
        "environment_params",
        "template_code",
        "mime_type",
        "file_name",
        "file_extension",
        "as_attachment",
        "debug",
    ),
    # Webhook credentials, additional headers, and destination CA paths are
    # deliberately local. They are neither collected nor overwritten.
    "webhook": (
        "name",
        "description",
        "payload_url",
        "http_method",
        "http_content_type",
        "body_template",
        "ssl_verification",
    ),
    "notification_group": ("name", "description"),
    # action_data and comments are not exposed by NetBox's EventRule API.
    "event_rule": ("name", "enabled", "event_types", "conditions", "action_type", "description"),
}

EXTRAS_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "custom_field": ("object_types", "related_object_type"),
    "custom_link": ("object_types",),
    "export_template": ("object_types",),
    "saved_filter": ("object_types",),
    "table_config": ("object_type",),
    "config_context": ("unsupported_assignment_types",),
    "event_rule": ("object_types",),
}

EXTRAS_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "custom_field_choice_set": {"owner": ("owner", "owner")},
    "custom_field": {
        "choice_set": ("custom_field_choice_set", "choice_set"),
        "owner": ("owner", "owner"),
    },
    "custom_link": {"owner": ("owner", "owner")},
    "export_template": {"owner": ("owner", "owner")},
    "saved_filter": {"user": ("user", "user"), "owner": ("owner", "owner")},
    "table_config": {"user": ("user", "user")},
    "config_context_profile": {"owner": ("owner", "owner")},
    "config_context": {
        "profile": ("config_context_profile", "profile"),
        "owner": ("owner", "owner"),
    },
    "config_template": {"owner": ("owner", "owner")},
    "webhook": {"owner": ("owner", "owner")},
    "notification_group": {},
    "event_rule": {"owner": ("owner", "owner")},
}

EXTRAS_TAGGED_KINDS: Final = frozenset({"config_context_profile", "config_template", "webhook", "event_rule"})

EXTRAS_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {}

EXTRAS_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {kind: frozenset() for kind in EXTRAS_RESOURCE_KINDS}
EXTRAS_IDENTITY_RELATIONSHIPS["table_config"] = frozenset({"user"})

CONFIG_CONTEXT_MULTI_RELATIONSHIPS: Final[dict[str, str]] = {
    "region": "region",
    "site_group": "site_group",
    "site": "site",
    "location": "location",
    "device_type": "device_type",
    "role": "device_role",
    "platform": "platform",
    "tenant_group": "tenant_group",
    "tenant": "tenant",
}

CONTENT_TYPE_LIST_KINDS: Final = frozenset(
    {"custom_field", "custom_link", "export_template", "saved_filter", "event_rule"}
)


def extras_relationship_target(resource_kind: str, name: str) -> str | None:
    configured = EXTRAS_RELATIONSHIP_FIELDS.get(resource_kind, {}).get(name)
    if configured:
        return configured[0]
    if name == "tag" and resource_kind in EXTRAS_TAGGED_KINDS:
        return "tag"
    if resource_kind == "config_context":
        return CONFIG_CONTEXT_MULTI_RELATIONSHIPS.get(name)
    if resource_kind == "notification_group":
        return {"group": "user_group", "user": "user"}.get(name)
    if resource_kind == "event_rule" and name.startswith("action_"):
        target = name.removeprefix("action_")
        return target if target in {"webhook", "notification_group"} else None
    return None


def is_extras_multi_relationship(resource_kind: str, name: str) -> bool:
    return (
        (name == "tag" and resource_kind in EXTRAS_TAGGED_KINDS)
        or (resource_kind == "config_context" and name in CONFIG_CONTEXT_MULTI_RELATIONSHIPS)
        or (resource_kind == "notification_group" and name in {"group", "user"})
    )
