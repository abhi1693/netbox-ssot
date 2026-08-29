from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver
from utilities.api import get_serializer_for_model

from netbox_ssot.planning.netbox_target import MODEL_BY_KIND
from netbox_ssot.planning.resource_registry import CUSTOM_FIELD_KINDS

SUPPORTED_APPS = frozenset(
    {"core", "extras", "users", "tenancy", "ipam", "dcim", "circuits", "virtualization", "vpn", "wireless"}
)
INTENTIONALLY_EXCLUDED_WRITABLE_MODELS = frozenset(
    {
        "extras.bookmark",
        "extras.imageattachment",
        "extras.journalentry",
        "extras.notification",
        "extras.scriptmodule",
        "extras.subscription",
        "users.token",
    }
)


def _url_patterns(patterns: Iterable[URLPattern | URLResolver]) -> Iterable[URLPattern]:
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _url_patterns(pattern.url_patterns)
        else:
            yield pattern


class PublicModelCoverageTests(SimpleTestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_writable_model_is_supported_or_explicitly_excluded(self) -> None:
        writable_models: set[str] = set()
        for pattern in _url_patterns(get_resolver().url_patterns):
            callback = pattern.callback
            if getattr(callback, "actions", {}).get("post") != "create":
                continue
            queryset = getattr(getattr(callback, "cls", None), "queryset", None)
            if queryset is not None and queryset.model._meta.app_label in SUPPORTED_APPS:
                writable_models.add(queryset.model._meta.label_lower)

        supported_models = {model._meta.label_lower for model in MODEL_BY_KIND.values()}
        assert len(writable_models) == 126
        assert len(supported_models) == 119
        assert supported_models.isdisjoint(INTENTIONALLY_EXCLUDED_WRITABLE_MODELS)
        assert writable_models == supported_models | INTENTIONALLY_EXCLUDED_WRITABLE_MODELS

    def test_every_custom_field_capable_model_carries_custom_field_values(self) -> None:
        custom_field_models: set[str] = set()
        api_exposed_custom_field_models: set[str] = set()
        for resource_kind, model in MODEL_BY_KIND.items():
            try:
                model._meta.get_field("custom_field_data")
            except FieldDoesNotExist:
                continue
            custom_field_models.add(resource_kind)
            if "custom_fields" in get_serializer_for_model(model)().fields:
                api_exposed_custom_field_models.add(resource_kind)

        assert custom_field_models - api_exposed_custom_field_models == {
            "circuit_group_assignment",
            "config_context_profile",
            "vlan_translation_policy",
            "vlan_translation_rule",
        }
        assert len(api_exposed_custom_field_models) == 89
        assert api_exposed_custom_field_models == CUSTOM_FIELD_KINDS
