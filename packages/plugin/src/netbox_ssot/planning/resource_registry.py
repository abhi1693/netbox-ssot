from __future__ import annotations

from typing import Final

from . import circuits, core, dcim, extras, ipam, tenancy, users, virtualization, vpn

RESOURCE_KINDS: Final = (
    dcim.DCIM_RESOURCE_KINDS
    | circuits.CIRCUITS_RESOURCE_KINDS
    | users.USERS_RESOURCE_KINDS
    | core.CORE_RESOURCE_KINDS
    | extras.EXTRAS_RESOURCE_KINDS
    | ipam.IPAM_RESOURCE_KINDS
    | tenancy.TENANCY_RESOURCE_KINDS
    | virtualization.VIRTUALIZATION_RESOURCE_KINDS
    | vpn.VPN_RESOURCE_KINDS
)
ATTRIBUTE_FIELDS: Final = {
    **dcim.ATTRIBUTE_FIELDS,
    **circuits.CIRCUITS_ATTRIBUTE_FIELDS,
    **users.USERS_ATTRIBUTE_FIELDS,
    **core.CORE_ATTRIBUTE_FIELDS,
    **extras.EXTRAS_ATTRIBUTE_FIELDS,
    **ipam.IPAM_ATTRIBUTE_FIELDS,
    **tenancy.TENANCY_ATTRIBUTE_FIELDS,
    **virtualization.VIRTUALIZATION_ATTRIBUTE_FIELDS,
    **vpn.VPN_ATTRIBUTE_FIELDS,
}
EXTRA_ATTRIBUTE_FIELDS: Final = {
    **dcim.EXTRA_ATTRIBUTE_FIELDS,
    **users.USERS_EXTRA_ATTRIBUTE_FIELDS,
    **core.CORE_EXTRA_ATTRIBUTE_FIELDS,
    **extras.EXTRAS_EXTRA_ATTRIBUTE_FIELDS,
    **ipam.IPAM_EXTRA_ATTRIBUTE_FIELDS,
    **tenancy.TENANCY_EXTRA_ATTRIBUTE_FIELDS,
    **virtualization.VIRTUALIZATION_EXTRA_ATTRIBUTE_FIELDS,
    **vpn.VPN_EXTRA_ATTRIBUTE_FIELDS,
}
RELATIONSHIP_FIELDS: Final = {
    **dcim.RELATIONSHIP_FIELDS,
    **circuits.CIRCUITS_RELATIONSHIP_FIELDS,
    **users.USERS_RELATIONSHIP_FIELDS,
    **core.CORE_RELATIONSHIP_FIELDS,
    **extras.EXTRAS_RELATIONSHIP_FIELDS,
    **ipam.IPAM_RELATIONSHIP_FIELDS,
    **tenancy.TENANCY_RELATIONSHIP_FIELDS,
    **virtualization.VIRTUALIZATION_RELATIONSHIP_FIELDS,
    **vpn.VPN_RELATIONSHIP_FIELDS,
}
TAGGED_KINDS: Final = (
    dcim.TAGGED_KINDS
    | circuits.CIRCUITS_TAGGED_KINDS
    | extras.EXTRAS_TAGGED_KINDS
    | ipam.IPAM_TAGGED_KINDS
    | tenancy.TENANCY_TAGGED_KINDS
    | virtualization.VIRTUALIZATION_TAGGED_KINDS
    | vpn.VPN_TAGGED_KINDS
)
REQUIRED_RELATIONSHIPS: Final = {
    **dcim.REQUIRED_RELATIONSHIPS,
    **circuits.CIRCUITS_REQUIRED_RELATIONSHIPS,
    **users.USERS_REQUIRED_RELATIONSHIPS,
    **core.CORE_REQUIRED_RELATIONSHIPS,
    **extras.EXTRAS_REQUIRED_RELATIONSHIPS,
    **ipam.IPAM_REQUIRED_RELATIONSHIPS,
    **tenancy.TENANCY_REQUIRED_RELATIONSHIPS,
    **virtualization.VIRTUALIZATION_REQUIRED_RELATIONSHIPS,
    **vpn.VPN_REQUIRED_RELATIONSHIPS,
}
IDENTITY_RELATIONSHIPS: Final = {
    **dcim.IDENTITY_RELATIONSHIPS,
    **circuits.CIRCUITS_IDENTITY_RELATIONSHIPS,
    **users.USERS_IDENTITY_RELATIONSHIPS,
    **core.CORE_IDENTITY_RELATIONSHIPS,
    **extras.EXTRAS_IDENTITY_RELATIONSHIPS,
    **ipam.IPAM_IDENTITY_RELATIONSHIPS,
    **tenancy.TENANCY_IDENTITY_RELATIONSHIPS,
    **virtualization.VIRTUALIZATION_IDENTITY_RELATIONSHIPS,
    **vpn.VPN_IDENTITY_RELATIONSHIPS,
}


def relationship_target(resource_kind: str, name: str) -> str | None:
    return (
        dcim.relationship_target(resource_kind, name)
        or circuits.circuit_relationship_target(resource_kind, name)
        or users.user_relationship_target(resource_kind, name)
        or extras.extras_relationship_target(resource_kind, name)
        or ipam.ipam_relationship_target(resource_kind, name)
        or tenancy.tenancy_relationship_target(resource_kind, name)
        or virtualization.virtualization_relationship_target(resource_kind, name)
        or vpn.vpn_relationship_target(resource_kind, name)
    )


def is_multi_relationship(resource_kind: str, name: str) -> bool:
    return (
        dcim.is_multi_relationship(resource_kind, name)
        or (resource_kind == "provider" and name == "asn")
        or users.is_user_multi_relationship(resource_kind, name)
        or extras.is_extras_multi_relationship(resource_kind, name)
        or ipam.is_ipam_multi_relationship(resource_kind, name)
        or tenancy.is_tenancy_multi_relationship(resource_kind, name)
        or virtualization.is_virtualization_multi_relationship(resource_kind, name)
        or vpn.is_vpn_multi_relationship(resource_kind, name)
    )


def is_identity_relationship(resource_kind: str, name: str) -> bool:
    return (
        dcim.is_identity_relationship(resource_kind, name)
        or circuits.is_circuit_identity_relationship(resource_kind, name)
        or ipam.is_ipam_identity_relationship(resource_kind, name)
        or tenancy.is_tenancy_identity_relationship(resource_kind, name)
        or virtualization.is_virtualization_identity_relationship(resource_kind, name)
        or vpn.is_vpn_identity_relationship(resource_kind, name)
    )
