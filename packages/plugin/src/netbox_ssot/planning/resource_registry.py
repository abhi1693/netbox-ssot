from __future__ import annotations

from typing import Final

from . import circuits, dcim

RESOURCE_KINDS: Final = dcim.DCIM_RESOURCE_KINDS | circuits.CIRCUITS_RESOURCE_KINDS
ATTRIBUTE_FIELDS: Final = {**dcim.ATTRIBUTE_FIELDS, **circuits.CIRCUITS_ATTRIBUTE_FIELDS}
EXTRA_ATTRIBUTE_FIELDS: Final = dict(dcim.EXTRA_ATTRIBUTE_FIELDS)
RELATIONSHIP_FIELDS: Final = {**dcim.RELATIONSHIP_FIELDS, **circuits.CIRCUITS_RELATIONSHIP_FIELDS}
TAGGED_KINDS: Final = dcim.TAGGED_KINDS | circuits.CIRCUITS_TAGGED_KINDS
REQUIRED_RELATIONSHIPS: Final = {**dcim.REQUIRED_RELATIONSHIPS, **circuits.CIRCUITS_REQUIRED_RELATIONSHIPS}
IDENTITY_RELATIONSHIPS: Final = {**dcim.IDENTITY_RELATIONSHIPS, **circuits.CIRCUITS_IDENTITY_RELATIONSHIPS}


def relationship_target(resource_kind: str, name: str) -> str | None:
    return dcim.relationship_target(resource_kind, name) or circuits.circuit_relationship_target(resource_kind, name)


def is_multi_relationship(resource_kind: str, name: str) -> bool:
    return dcim.is_multi_relationship(resource_kind, name) or (resource_kind == "provider" and name == "asn")


def is_identity_relationship(resource_kind: str, name: str) -> bool:
    return dcim.is_identity_relationship(resource_kind, name) or circuits.is_circuit_identity_relationship(
        resource_kind, name
    )
