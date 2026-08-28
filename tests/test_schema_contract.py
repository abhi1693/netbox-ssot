from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from netbox_ssot_contracts import (
    FieldWidget,
    ProviderManifest,
    SchemaContractError,
    normalize_config_schema,
    redact_configuration,
    validate_config_schema,
    validate_configuration,
)
from netbox_ssot_provider_netbox import provider_definition

MANIFEST = provider_definition().manifest


def test_netbox_manifest_normalizes_to_safe_ui_fields() -> None:
    fields = normalize_config_schema(MANIFEST.config_schema)

    assert tuple(field.name for field in fields) == (
        "base_url",
        "token_ref",
        "verify_tls",
        "page_size",
        "timeout_seconds",
    )
    assert fields[0].widget is FieldWidget.URL
    assert fields[1].widget is FieldWidget.SECRET_REFERENCE
    assert fields[1].secret is True
    assert fields[2].widget is FieldWidget.CHECKBOX
    assert fields[3].widget is FieldWidget.NUMBER


def test_schema_rejects_provider_controlled_html_or_script_extensions() -> None:
    schema = deepcopy(MANIFEST.config_schema)
    schema["properties"]["base_url"]["x-provider-html"] = "<script>alert(1)</script>"

    with pytest.raises(SchemaContractError, match="unsupported keys"):
        validate_config_schema(schema)


def test_schema_rejects_remote_references() -> None:
    schema = deepcopy(MANIFEST.config_schema)
    schema["properties"]["base_url"] = {
        "type": "string",
        "title": "Endpoint",
        "$ref": "https://example.test/provider-schema.json",
    }

    with pytest.raises(SchemaContractError, match="unsupported keys"):
        validate_config_schema(schema)


def test_manifest_secret_declaration_must_match_schema() -> None:
    manifest_data = MANIFEST.model_dump(mode="python")
    manifest_data["secret_fields"] = ()

    with pytest.raises(ValidationError, match="secret_fields must exactly match"):
        ProviderManifest.model_validate(manifest_data)


def test_configuration_is_validated_and_secret_references_are_redacted() -> None:
    configuration = {
        "base_url": "https://netbox.example.test",
        "token_ref": "env://NETBOX_TOKEN",
        "verify_tls": True,
        "page_size": 500,
        "timeout_seconds": 30,
    }

    validate_configuration(MANIFEST.config_schema, configuration)
    redacted = redact_configuration(normalize_config_schema(MANIFEST.config_schema), configuration)

    assert redacted["token_ref"] == "<secret reference configured>"
    assert "NETBOX_TOKEN" not in repr(redacted)


def test_configuration_rejects_unknown_fields() -> None:
    configuration = {
        "base_url": "https://netbox.example.test",
        "token_ref": "env://NETBOX_TOKEN",
        "execute_python": "os.system('true')",
    }

    with pytest.raises(SchemaContractError, match="Additional properties"):
        validate_configuration(MANIFEST.config_schema, configuration)


def test_configuration_allows_source_netbox_to_apply_its_page_size_limit() -> None:
    configuration = {
        "base_url": "https://netbox.example.test",
        "token_ref": "env://NETBOX_TOKEN",
        "page_size": 1000,
    }

    validate_configuration(MANIFEST.config_schema, configuration)


def test_netbox_token_must_be_an_agent_local_reference() -> None:
    configuration = {
        "base_url": "https://netbox.example.test",
        "token_ref": "nbt_literal.secret",
    }

    with pytest.raises(SchemaContractError, match="does not match"):
        validate_configuration(MANIFEST.config_schema, configuration)
