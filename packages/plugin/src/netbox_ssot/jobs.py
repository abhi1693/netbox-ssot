from __future__ import annotations

from netbox.jobs import JobRunner

from .preparation import prepare_comparison


class PrepareComparisonJob(JobRunner):
    class Meta:
        name = "Prepare SSoT comparison"

    def run(self, preparation_id: str, *args: object, **kwargs: object) -> None:
        prepare_comparison(preparation_id, logger=self.logger)


__all__ = ["PrepareComparisonJob"]
