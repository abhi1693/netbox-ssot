from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ...models import DiscoverySource
from ...retention import prune_collections


class Command(BaseCommand):
    help = "Preview or apply collection retention policies without deleting reviewed or applied evidence."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--source", help="Limit cleanup to one source UUID or exact source name.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete eligible collection runs. Without this flag the command is a dry run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        sources = DiscoverySource.objects.order_by("name")
        source_value = options.get("source")
        if source_value:
            try:
                source_id = UUID(source_value)
            except ValueError:
                sources = sources.filter(name=source_value)
            else:
                sources = sources.filter(pk=source_id)
        if not sources.exists():
            raise CommandError("No matching discovery source was found.")

        apply = bool(options["apply"])
        total_runs = 0
        total_observations = 0
        for source in sources:
            result = prune_collections(source, apply=apply)
            plan = result.plan
            total_runs += result.deleted_runs if apply else plan.eligible_runs
            total_observations += result.deleted_observations if apply else plan.eligible_observations
            action = "deleted" if apply else "eligible"
            self.stdout.write(
                f"{source.name}: {plan.total_runs} stored runs, {plan.total_observations} records; "
                f"{result.deleted_runs if apply else plan.eligible_runs} runs and "
                f"{result.deleted_observations if apply else plan.eligible_observations} records {action}; "
                f"{plan.review_protected_runs} review-protected."
            )

        if apply:
            self.stdout.write(self.style.SUCCESS(f"Deleted {total_runs} runs and {total_observations} observations."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {total_runs} runs and {total_observations} observations are eligible. "
                    "Re-run with --apply to delete them."
                )
            )
