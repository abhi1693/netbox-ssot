from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ComparisonFieldRow:
    category: str
    field: str
    label: str
    provider_value: str
    local_value: str
    provider_present: bool
    local_present: bool
    changed: bool
    status: str
    status_color: str


def comparison_field_rows(
    source_data: Any,
    target_data: Any,
    *,
    target_exists: bool,
    action: str = "",
    relationship_labels: dict[str, str] | None = None,
) -> tuple[ComparisonFieldRow, ...]:
    source = source_data if isinstance(source_data, dict) else {}
    target = target_data if isinstance(target_data, dict) else {}
    rows: list[ComparisonFieldRow] = []
    for category in ("attributes", "relationships"):
        source_fields = source.get(category, {})
        target_fields = target.get(category, {})
        if not isinstance(source_fields, dict):
            source_fields = {}
        if not isinstance(target_fields, dict):
            target_fields = {}
        for field in sorted(set(source_fields) | set(target_fields)):
            provider_present = field in source_fields
            local_present = field in target_fields
            provider_value = source_fields.get(field)
            local_value = target_fields.get(field)
            changed = provider_present != local_present or provider_value != local_value
            status, status_color = _field_status(
                target_exists=target_exists,
                action=action,
                provider_present=provider_present,
                local_present=local_present,
                changed=changed,
            )
            rows.append(
                ComparisonFieldRow(
                    category=category,
                    field=field,
                    label=field_label(field),
                    provider_value=format_comparison_value(
                        provider_value,
                        relationship=category == "relationships",
                        relationship_labels=relationship_labels,
                    ),
                    local_value=format_comparison_value(
                        local_value,
                        relationship=category == "relationships",
                        relationship_labels=relationship_labels,
                    ),
                    provider_present=provider_present,
                    local_present=local_present,
                    changed=changed,
                    status=status,
                    status_color=status_color,
                )
            )
    return tuple(rows)


def field_label(field: str) -> str:
    normalized = field.removeprefix("/").replace("_", " ").strip()
    labels = {
        "asn": "ASN",
        "rir": "RIR",
        "url": "URL",
    }
    return labels.get(normalized.casefold(), normalized.capitalize() or "Value")


def format_comparison_value(
    value: Any,
    *,
    relationship: bool = False,
    relationship_labels: dict[str, str] | None = None,
) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if relationship:
        return format_relationship_value(value, relationship_labels)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(format_comparison_value(item) for item in value) or "None"
    if isinstance(value, dict):
        return "; ".join(f"{field_label(str(key))}: {format_comparison_value(item)}" for key, item in value.items())
    return str(value)


def _field_status(
    *,
    target_exists: bool,
    action: str,
    provider_present: bool,
    local_present: bool,
    changed: bool,
) -> tuple[str, str]:
    if not target_exists:
        return ("New", "success") if action == "create" else ("Observed", "secondary")
    if not changed:
        return "Matches", "success"
    if provider_present and not local_present:
        return "Add", "info"
    if local_present and not provider_present:
        return "Remove", "danger"
    return "Change", "warning"


def format_relationship_value(value: Any, relationship_labels: dict[str, str] | None = None) -> str:
    labels = relationship_labels or {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
        if isinstance(decoded, list):
            label = labels.get(value)
            if label and not label.startswith("netbox:"):
                return f"{field_label(str(decoded[0]))} · {label}"
            return _format_identity(decoded)
        return str(decoded)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(format_relationship_value(item, labels) for item in value) or "None"
    return format_comparison_value(value)


def _format_identity(identity: list[Any]) -> str:
    if not identity:
        return "None"
    kind = field_label(str(identity[0]))
    ignored = {"account", "address", "asn", "model", "name", "prefix", "root", "slug", "username"}
    values = [value for value in _identity_values(identity[1:]) if value.casefold() not in ignored]
    return f"{kind} · {' / '.join(values)}" if values else kind


def _identity_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        values: list[str] = []
        for item in value:
            values.extend(_identity_values(item))
        return values
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(decoded, list):
            return _identity_values(decoded[1:])
    if value is None:
        return []
    return [str(value)]


__all__ = [
    "ComparisonFieldRow",
    "comparison_field_rows",
    "field_label",
    "format_comparison_value",
    "format_relationship_value",
]
