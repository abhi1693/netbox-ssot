from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from netbox.jobs import JobRunner

from .application.service import apply_comparison
from .models import ComparisonRun
from .preparation import prepare_comparison, request_comparison_preparation


class PrepareComparisonJob(JobRunner):
    class Meta:
        name = "Prepare SSoT comparison"

    def run(self, preparation_id: str, *args: object, **kwargs: object) -> None:
        prepare_comparison(preparation_id, logger=self.logger)


def apply_comparison_job_name(comparison_id: object) -> str:
    """Return the stable NetBox job name used to find and de-duplicate an apply."""
    return f"Apply SSoT comparison {comparison_id}"


class ApplyComparisonJob(JobRunner):
    class Meta:
        name = "Apply SSoT comparison"

    def run(
        self,
        comparison_id: str,
        applied_by_id: str,
        required_permissions: list[str],
        *args: object,
        **kwargs: object,
    ) -> None:
        comparison = ComparisonRun.objects.select_related("collection_run").get(pk=comparison_id)
        applied_by = get_user_model().objects.get(pk=applied_by_id)
        permissions = ("netbox_ssot.add_applyrun", *required_permissions)
        if not applied_by.has_perms(permissions):
            raise PermissionDenied(
                "The requesting user no longer has every permission required to apply this comparison."
            )

        self.logger.info("Applying approved comparison %s", comparison.pk)
        outcome = apply_comparison(comparison, applied_by)
        self.logger.info("Application receipt %s is complete", outcome.apply_run.pk)
        if outcome.created:
            refresh = request_comparison_preparation(comparison.collection_run, force=True)
            if refresh.queued:
                self.logger.info("Queued a refreshed drift snapshot after application")


__all__ = ["ApplyComparisonJob", "PrepareComparisonJob", "apply_comparison_job_name"]
