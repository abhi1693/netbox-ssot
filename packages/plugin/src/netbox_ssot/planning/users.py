from __future__ import annotations

from typing import Final

USERS_RESOURCE_KINDS: Final = frozenset({"object_permission", "user_group", "user"})

USERS_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "object_permission": ("name", "description", "enabled", "actions", "constraints"),
    "user_group": ("name", "description"),
    "user": ("username", "first_name", "last_name", "email", "is_active"),
}

USERS_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "object_permission": ("object_types",),
}

USERS_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "object_permission": {},
    "user_group": {},
    "user": {},
}

USERS_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {}

USERS_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "object_permission": frozenset(),
    "user_group": frozenset(),
    "user": frozenset(),
}


def user_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "user_group" and name == "permission":
        return "object_permission"
    if resource_kind == "user" and name == "group":
        return "user_group"
    if resource_kind == "user" and name == "permission":
        return "object_permission"
    return None


def is_user_multi_relationship(resource_kind: str, name: str) -> bool:
    return (
        resource_kind == "user_group" and name == "permission"
    ) or (
        resource_kind == "user" and name in {"group", "permission"}
    )
