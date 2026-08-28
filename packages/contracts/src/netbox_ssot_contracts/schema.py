from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import Field

from .base import AttributeValue, ContractModel, JsonPointer

ROOT_KEYS = {
    "$schema",
    "additionalProperties",
    "description",
    "properties",
    "required",
    "title",
    "type",
    "x-netbox-ssot-order",
}
PROPERTY_KEYS = {
    "default",
    "description",
    "enum",
    "examples",
    "format",
    "items",
    "maximum",
    "maxLength",
    "minimum",
    "minLength",
    "pattern",
    "title",
    "type",
    "writeOnly",
    "x-netbox-ssot-placeholder",
    "x-netbox-ssot-secret",
    "x-netbox-ssot-widget",
}
SUPPORTED_TYPES = {"string", "integer", "number", "boolean", "array"}


class SchemaContractError(ValueError):
    """A provider schema is valid JSON Schema but outside the supported safe subset."""


class FieldWidget(StrEnum):
    TEXT = "text"
    URL = "url"
    NUMBER = "number"
    CHECKBOX = "checkbox"
    SELECT = "select"
    MULTISELECT = "multiselect"
    SECRET_REFERENCE = "secret-reference"


class SchemaField(ContractModel):
    name: str = Field(min_length=1, max_length=128)
    pointer: JsonPointer
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    value_type: str
    widget: FieldWidget
    required: bool = False
    secret: bool = False
    default: AttributeValue = None
    choices: tuple[AttributeValue, ...] = ()
    placeholder: str = Field(default="", max_length=200)


def validate_config_schema(schema: Mapping[str, Any]) -> None:
    """Validate a provider configuration schema and the UI-safe v1 subset."""

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaContractError(f"invalid JSON Schema: {exc.message}") from exc

    unknown_root_keys = set(schema) - ROOT_KEYS
    if unknown_root_keys:
        raise SchemaContractError(f"unsupported root schema keys: {sorted(unknown_root_keys)}")
    if schema.get("type") != "object":
        raise SchemaContractError("configuration schema root must have type 'object'")
    if schema.get("additionalProperties") is not False:
        raise SchemaContractError("configuration schema must set additionalProperties to false")

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise SchemaContractError("configuration schema properties must be an object")

    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise SchemaContractError("required must be an array of property names")
    unknown_required = set(required) - set(properties)
    if unknown_required:
        raise SchemaContractError(f"required contains unknown properties: {sorted(unknown_required)}")

    order = schema.get("x-netbox-ssot-order", list(properties))
    if not isinstance(order, list) or set(order) != set(properties) or len(order) != len(properties):
        raise SchemaContractError("x-netbox-ssot-order must list every property exactly once")

    for name, definition in properties.items():
        _validate_property(name, definition)


def _validate_property(name: str, definition: Any) -> None:
    if not isinstance(definition, dict):
        raise SchemaContractError(f"property {name!r} must be an object")
    unknown_keys = set(definition) - PROPERTY_KEYS
    if unknown_keys:
        raise SchemaContractError(f"property {name!r} uses unsupported keys: {sorted(unknown_keys)}")

    value_type = definition.get("type")
    if value_type not in SUPPORTED_TYPES:
        raise SchemaContractError(f"property {name!r} has unsupported type {value_type!r}")
    if not isinstance(definition.get("title"), str) or not definition["title"].strip():
        raise SchemaContractError(f"property {name!r} must have a title")

    if value_type == "array":
        items = definition.get("items")
        if not isinstance(items, dict) or set(items) - {"enum", "type"}:
            raise SchemaContractError(f"array property {name!r} must use scalar enum items")
        if items.get("type") != "string" or not isinstance(items.get("enum"), list):
            raise SchemaContractError(f"array property {name!r} must use string enum items")
    elif "items" in definition:
        raise SchemaContractError(f"non-array property {name!r} cannot declare items")

    widget = definition.get("x-netbox-ssot-widget")
    if widget is not None:
        try:
            selected_widget = FieldWidget(widget)
        except ValueError as exc:
            raise SchemaContractError(f"property {name!r} requests unsupported widget {widget!r}") from exc
        if selected_widget is FieldWidget.SECRET_REFERENCE and not _is_secret(definition):
            raise SchemaContractError(f"property {name!r} uses secret-reference without being marked secret")

    if _is_secret(definition) and value_type != "string":
        raise SchemaContractError(f"secret property {name!r} must be a string reference")


def normalize_config_schema(schema: Mapping[str, Any]) -> tuple[SchemaField, ...]:
    validate_config_schema(schema)
    properties: dict[str, dict[str, Any]] = schema["properties"]
    required = set(schema.get("required", []))
    order: list[str] = schema.get("x-netbox-ssot-order", list(properties))

    fields: list[SchemaField] = []
    for name in order:
        definition = properties[name]
        secret = _is_secret(definition)
        choices = _choices(definition)
        widget = _widget_for(definition, secret=secret, has_choices=bool(choices))
        fields.append(
            SchemaField(
                name=name,
                pointer=f"/{_escape_pointer(name)}",
                title=definition["title"],
                description=definition.get("description", ""),
                value_type=definition["type"],
                widget=widget,
                required=name in required,
                secret=secret,
                default=definition.get("default"),
                choices=choices,
                placeholder=definition.get("x-netbox-ssot-placeholder", ""),
            )
        )
    return tuple(fields)


def validate_configuration(schema: Mapping[str, Any], configuration: Mapping[str, Any]) -> None:
    validate_config_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(configuration), key=lambda error: tuple(str(part) for part in error.path))
    if errors:
        first_error: ValidationError = errors[0]
        path = "/".join(str(part) for part in first_error.absolute_path)
        location = f" at {path}" if path else ""
        raise SchemaContractError(f"configuration is invalid{location}: {first_error.message}")


def redact_configuration(fields: tuple[SchemaField, ...], configuration: Mapping[str, Any]) -> dict[str, Any]:
    secret_names = {field.name for field in fields if field.secret}
    return {
        key: "<secret reference configured>" if key in secret_names and value else value
        for key, value in configuration.items()
    }


def _is_secret(definition: Mapping[str, Any]) -> bool:
    return definition.get("writeOnly") is True or definition.get("x-netbox-ssot-secret") is True


def _choices(definition: Mapping[str, Any]) -> tuple[AttributeValue, ...]:
    if definition["type"] == "array":
        return tuple(definition["items"]["enum"])
    return tuple(definition.get("enum", ()))


def _widget_for(definition: Mapping[str, Any], *, secret: bool, has_choices: bool) -> FieldWidget:
    requested = definition.get("x-netbox-ssot-widget")
    if requested is not None:
        return FieldWidget(requested)
    if secret:
        return FieldWidget.SECRET_REFERENCE
    if definition["type"] == "boolean":
        return FieldWidget.CHECKBOX
    if definition["type"] in {"integer", "number"}:
        return FieldWidget.NUMBER
    if definition["type"] == "array":
        return FieldWidget.MULTISELECT
    if has_choices:
        return FieldWidget.SELECT
    if definition.get("format") in {"uri", "uri-reference"}:
        return FieldWidget.URL
    return FieldWidget.TEXT


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
