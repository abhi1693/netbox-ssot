from __future__ import annotations

from typing import Any, Final

CORE_RESOURCE_KINDS: Final = frozenset({"data_source"})

CORE_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "data_source": (
        "name",
        "type",
        "source_url",
        "enabled",
        "sync_interval",
        "ignore_rules",
        "description",
        "comments",
    ),
}

# Backend parameters can contain credentials. Only explicitly portable keys belong
# to the canonical graph; all other keys remain local to the destination.
CORE_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "data_source": ("parameters",),
}

CORE_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "data_source": {"owner": ("owner", "owner")},
}

CORE_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {}

CORE_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "data_source": frozenset(),
}

PORTABLE_DATA_SOURCE_PARAMETER_KEYS: Final[dict[str, frozenset[str]]] = {
    "git": frozenset({"branch"}),
}


def portable_data_source_parameters(backend_type: str, parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        return {}
    allowed = PORTABLE_DATA_SOURCE_PARAMETER_KEYS.get(backend_type, frozenset())
    return {
        key: value
        for key, value in sorted(parameters.items())
        if key in allowed and isinstance(value, str) and value
    }
