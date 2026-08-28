from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dcim.models import Location, Region, Site, SiteGroup
from extras.models import Tag
from ipam.models import ASN, RIR
from tenancy.models import Tenant, TenantGroup
from users.models import Owner, OwnerGroup

from .comparison import CanonicalRecord, natural_identity, normalize_value

MODEL_BY_KIND = {
    "tag": Tag,
    "owner_group": OwnerGroup,
    "owner": Owner,
    "tenant_group": TenantGroup,
    "tenant": Tenant,
    "site_group": SiteGroup,
    "rir": RIR,
    "asn": ASN,
    "region": Region,
    "site": Site,
    "location": Location,
}


def load_netbox_target_records() -> list[CanonicalRecord]:
    records: list[CanonicalRecord] = []
    records.extend(_records("owner_group", OwnerGroup.objects.all()))
    records.extend(_records("owner", Owner.objects.select_related("group")))
    records.extend(_records("tag", Tag.objects.select_related("owner").prefetch_related("object_types")))
    records.extend(
        _records(
            "tenant_group",
            TenantGroup.objects.select_related("parent", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "tenant",
            Tenant.objects.select_related("group", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "site_group",
            SiteGroup.objects.select_related("parent", "owner").prefetch_related("tags"),
        )
    )
    records.extend(_records("rir", RIR.objects.select_related("owner").prefetch_related("tags")))
    records.extend(
        _records(
            "asn",
            ASN.objects.select_related("rir", "role", "tenant", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "region",
            Region.objects.select_related("parent", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "site",
            Site.objects.select_related("region", "group", "tenant", "owner").prefetch_related("asns", "tags"),
        )
    )
    records.extend(
        _records(
            "location",
            Location.objects.select_related("site", "parent", "tenant", "owner").prefetch_related("tags"),
        )
    )
    return records


def _records(resource_kind: str, objects: Iterable[Any]) -> list[CanonicalRecord]:
    records: list[CanonicalRecord] = []
    for obj in objects:
        attributes = _attributes(resource_kind, obj)
        relationships = _relationships(resource_kind, obj)
        identity_key = natural_identity(resource_kind, attributes, _identity_relationships(relationships))
        records.append(
            CanonicalRecord(
                resource_kind=resource_kind,
                identity_key=identity_key,
                display_name=_display_name(attributes, str(obj)),
                external_id="",
                attributes=attributes,
                relationships=relationships,
                target_object_type=obj._meta.label_lower,
                target_object_id=str(obj.pk),
            )
        )
    return records


def _attributes(resource_kind: str, obj: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {}

    def add(path: str, value: Any) -> None:
        normalized = normalize_value(value)
        if normalized is not None and normalized != "":
            attributes[path] = normalized

    if resource_kind == "tag":
        for path in ("name", "slug", "color", "description", "weight"):
            add(f"/{path}", getattr(obj, path))
        object_types = sorted(f"{item.app_label}.{item.model}" for item in obj.object_types.all())
        if object_types:
            add("/object_types", object_types)
    elif resource_kind in {"owner_group", "owner"}:
        for path in ("name", "description"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind in {"tenant_group", "tenant", "site_group"}:
        for path in ("name", "slug", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "rir":
        for path in ("name", "slug", "is_private", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "asn":
        for path in ("asn", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
        add("/role", obj.role.slug if obj.role else None)
    elif resource_kind == "region":
        for path in ("name", "slug", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "site":
        for path in (
            "name",
            "slug",
            "status",
            "facility",
            "time_zone",
            "description",
            "physical_address",
            "shipping_address",
            "latitude",
            "longitude",
            "comments",
        ):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "location":
        for path in ("name", "slug", "status", "facility", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    return attributes


def _relationships(resource_kind: str, obj: Any) -> dict[str, Any]:
    relationships: dict[str, Any] = {}

    def add(name: str, target_kind: str, target: Any) -> None:
        if target is not None:
            relationships[name] = _target_identity(target_kind, target)

    def add_many(name: str, target_kind: str, targets: Iterable[Any]) -> None:
        identities = sorted(_target_identity(target_kind, target) for target in targets)
        if identities:
            relationships[name] = identities

    if resource_kind == "tag":
        add("owner", "owner", obj.owner)
    elif resource_kind == "owner":
        add("group", "owner_group", obj.group)
    elif resource_kind == "tenant_group":
        add("parent", "tenant_group", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "tenant":
        add("group", "tenant_group", obj.group)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "site_group":
        add("parent", "site_group", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "rir":
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "asn":
        add("rir", "rir", obj.rir)
        add("tenant", "tenant", obj.tenant)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "region":
        add("parent", "region", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "site":
        add("region", "region", obj.region)
        add("group", "site_group", obj.group)
        add("tenant", "tenant", obj.tenant)
        add("owner", "owner", obj.owner)
        add_many("asn", "asn", obj.asns.all())
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "location":
        add("site", "site", obj.site)
        add("parent", "location", obj.parent)
        add("tenant", "tenant", obj.tenant)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    return relationships


def _target_identity(resource_kind: str, obj: Any) -> str:
    attributes: dict[str, Any] = {}
    relationships: dict[str, str] = {}
    slug_kinds = {"tag", "tenant_group", "tenant", "site_group", "rir", "region", "site", "location"}
    if resource_kind in slug_kinds:
        attributes["/slug"] = obj.slug
    elif resource_kind in {"owner_group", "owner"}:
        attributes["/name"] = obj.name
    elif resource_kind == "asn":
        attributes["/asn"] = obj.asn

    if resource_kind in {"region", "site_group", "tenant_group"} and obj.parent:
        relationships["parent"] = _target_identity(resource_kind, obj.parent)
    elif resource_kind == "tenant" and obj.group:
        relationships["group"] = _target_identity("tenant_group", obj.group)
    elif resource_kind == "location":
        relationships["site"] = _target_identity("site", obj.site)
        if obj.parent:
            relationships["parent"] = _target_identity("location", obj.parent)
    return natural_identity(resource_kind, attributes, relationships)


def _identity_relationships(relationships: dict[str, Any]) -> dict[str, str]:
    return {name: value for name, value in relationships.items() if isinstance(value, str)}


def _display_name(attributes: dict[str, Any], fallback: str) -> str:
    return str(attributes.get("/name") or attributes.get("/asn") or attributes.get("/slug") or fallback)
