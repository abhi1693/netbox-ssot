from __future__ import annotations

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netbox_ssot.models import DiscoverySource


class SourceDatasetUITests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_superuser(
            username=f"dataset-ui-{uuid4()}",
            password="unused",
        )
        cls.source = DiscoverySource.objects.create(
            name="Dataset definition source",
            provider_id="netbox",
            configuration={"base_url": "https://source.example/"},
            datasets=["references", "regions"],
        )

    def setUp(self) -> None:
        self.client.force_login(self.user)

    def test_source_mapping_badges_link_to_dataset_definitions(self) -> None:
        dataset_url = reverse(
            "plugins:netbox_ssot:source_dataset_detail",
            kwargs={"pk": self.source.pk, "dataset_id": "regions"},
        )

        response = self.client.get(
            reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": self.source.pk})
        )

        assert response.status_code == 200
        assert [group.title for group in response.context["dataset_groups"]] == ["Dependencies", "DCIM"]
        self.assertContains(response, f'href="{dataset_url}"')
        self.assertContains(response, "Regions")

    def test_dataset_page_exposes_definition_dependencies_and_mappings(self) -> None:
        supporting_url = reverse(
            "plugins:netbox_ssot:source_dataset_detail",
            kwargs={"pk": self.source.pk, "dataset_id": "references"},
        )

        response = self.client.get(
            reverse(
                "plugins:netbox_ssot:source_dataset_detail",
                kwargs={"pk": self.source.pk, "dataset_id": "regions"},
            )
        )

        assert response.status_code == 200
        self.assertTemplateUsed(response, "generic/_base.html")
        self.assertContains(response, 'class="nav nav-tabs"', count=1)
        self.assertContains(response, 'class="nav-link active"', count=1)
        self.assertContains(response, "Complete Region hierarchy")
        self.assertContains(response, "Included in source")
        self.assertContains(response, "Declared Scope")
        self.assertContains(response, f'href="{supporting_url}"', count=2)
        assert response.content.count(b'<code class="small">dcim.Region</code>') == 2
        self.assertContains(response, "https://source.example/dcim/regions/")
        self.assertContains(response, "Manifest definition")

    def test_supporting_dataset_has_its_own_definition_page(self) -> None:
        response = self.client.get(
            reverse(
                "plugins:netbox_ssot:source_dataset_detail",
                kwargs={"pk": self.source.pk, "dataset_id": "references"},
            )
        )

        assert response.status_code == 200
        self.assertContains(response, "Supporting dataset")
        self.assertContains(response, "Automatically collected tenancy")
        self.assertContains(response, "extras.Tag")
        self.assertContains(response, "ipam.ASN")

    def test_unknown_dataset_returns_not_found(self) -> None:
        response = self.client.get(
            reverse(
                "plugins:netbox_ssot:source_dataset_detail",
                kwargs={"pk": self.source.pk, "dataset_id": "not-a-dataset"},
            )
        )

        assert response.status_code == 404

    def test_dataset_page_uses_the_source_view_permission(self) -> None:
        user = get_user_model().objects.create_user(username=f"dataset-denied-{uuid4()}")
        self.client.force_login(user)

        response = self.client.get(
            reverse(
                "plugins:netbox_ssot:source_dataset_detail",
                kwargs={"pk": self.source.pk, "dataset_id": "regions"},
            )
        )

        assert response.status_code == 403
