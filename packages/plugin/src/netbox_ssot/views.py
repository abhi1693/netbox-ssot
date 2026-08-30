from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from core.choices import JobStatusChoices
from core.models import Job
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, models, transaction
from django.db.models import OuterRef, Prefetch, Subquery
from django.db.models.functions import Cast
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from netbox.plugins import get_plugin_config
from netbox.views import generic

from netbox_ssot_contracts import (
    CURRENT_AGENT_PROTOCOL_VERSION,
    FieldWidget,
    SchemaContractError,
    redact_configuration,
    selected_dataset_ids,
    validate_configuration,
)

from . import filtersets as ssot_filtersets
from . import forms as ssot_forms
from . import tables as ssot_tables
from .agent_capabilities import agent_capability_rows, source_capability_issue
from .agent_security import (
    AgentSecurityError,
    create_enrollment,
    create_replacement_enrollment,
    revoke_agent_keys,
)
from .application.service import (
    ApplicationRejectedError,
    apply_comparison,
    inspect_application_summary,
)
from .collection_policy import agent_collection_policy_issue
from .comparison_presentation import comparison_field_rows
from .destination import dataset_data_model_mappings, selected_data_model_mappings
from .drift import DriftSummary
from .health import agent_health, collection_failure_messages, source_health
from .jobs import ApplyComparisonJob, apply_comparison_job_name
from .models import (
    AgentCommand,
    AgentEnrollmentToken,
    AgentSecurityEvent,
    ApplyRun,
    CollectionRun,
    CollectorAgent,
    ComparisonItem,
    ComparisonPreparation,
    ComparisonReview,
    ComparisonRun,
    DiscoverySource,
    StoredObservation,
)
from .planning.netbox_target import MODEL_BY_KIND
from .planning.service import ComparisonRejectedError
from .preparation import request_comparison_preparation
from .providers import (
    ProviderNotFoundError,
    ProviderRegistry,
    build_provider_card,
    build_provider_dataset_groups,
    build_provider_wizard,
)
from .record_links import RecordLinkResolver, RecordLinks, source_object_id
from .retention import retention_plan
from .review import (
    ReviewRejectedError,
    approve_all_review_items,
    finalize_review,
    latest_review_decision,
    latest_review_decisions,
    record_review_decision,
    review_progress,
)
from .ui import build_alignment_trend, reconciliation_row
from .workflow import APPROVAL_REQUIRED_REASON, workflow_presentation

ACTIVE_COMMAND_STATES = (
    AgentCommand.State.PENDING,
    AgentCommand.State.DISPATCHED,
    AgentCommand.State.RUNNING,
    AgentCommand.State.REPORTING,
)


def _ensure_review_can_progress_to_apply(comparison: ComparisonRun) -> None:
    readiness = inspect_application_summary(comparison)
    blockers = tuple(reason for reason in readiness.reasons if reason != APPROVAL_REQUIRED_REASON)
    if blockers:
        raise ReviewRejectedError("Approval cannot be finalized while apply is blocked: " + " ".join(blockers))


def _latest_apply_job(comparison: ComparisonRun) -> Job | None:
    return Job.objects.filter(name=apply_comparison_job_name(comparison.pk)).order_by("-created").first()


def _apply_job_context(request: HttpRequest, comparison: ComparisonRun) -> dict[str, object]:
    job = _latest_apply_job(comparison)
    active = bool(job and job.status in JobStatusChoices.ENQUEUED_STATE_CHOICES)
    failed = bool(job and job.status in {JobStatusChoices.STATUS_ERRORED, JobStatusChoices.STATUS_FAILED})
    return {
        "apply_job": job,
        "apply_job_active": active,
        "apply_job_failed": failed,
        # NetBox's native background-task detail page is restricted to superusers.
        "apply_job_url": job.get_absolute_url() if job and request.user.is_superuser else "",
    }


class OverviewView(LoginRequiredMixin, View):
    template_name = "netbox_ssot/overview.html"
    selected_view = "operations"

    def get(self, request: HttpRequest) -> HttpResponse:
        selected_view = self.selected_view
        try:
            selected_period = int(request.GET.get("period", "30"))
        except ValueError:
            selected_period = 30
        if selected_period not in {7, 30, 90}:
            selected_period = 30
        can_view_sources = request.user.has_perm("netbox_ssot.view_discoverysource")
        can_view_agents = request.user.has_perm("netbox_ssot.view_collectoragent")
        can_view_runs = request.user.has_perm("netbox_ssot.view_collectionrun")
        can_view_comparisons = request.user.has_perm("netbox_ssot.view_comparisonrun")
        sources = tuple(_source_queryset()) if can_view_sources else ()
        latest_comparison_ids = tuple(
            source.latest_comparison_id for source in sources if getattr(source, "latest_comparison_id", None)
        )
        pending_reviews = (
            ComparisonRun.objects.filter(
                pk__in=latest_comparison_ids,
                apply_run__isnull=True,
                final_review__isnull=True,
            )
            .exclude(create_count=0, update_count=0, conflict_count=0, skipped_count=0)
            .count()
            if can_view_comparisons and can_view_sources
            else None
        )
        drift_rows = []
        drift_summary = DriftSummary()
        preparing_count = 0
        if can_view_comparisons and can_view_sources:
            for source in sources:
                preparation_state = getattr(source, "latest_preparation_state", "") or ""
                if preparation_state in {
                    ComparisonPreparation.State.PENDING,
                    ComparisonPreparation.State.RUNNING,
                }:
                    preparing_count += 1
                comparison_id = getattr(source, "latest_comparison_id", None)
                counts = DriftSummary(
                    missing_locally=getattr(source, "latest_create_count", 0) or 0,
                    different_locally=getattr(source, "latest_update_count", 0) or 0,
                    matching=getattr(source, "latest_no_change_count", 0) or 0,
                    needs_attention=(getattr(source, "latest_conflict_count", 0) or 0)
                    + (getattr(source, "latest_skipped_count", 0) or 0),
                )
                if comparison_id:
                    drift_summary += counts
                drift_rows.append(
                    {
                        "source": source,
                        "comparison_id": comparison_id,
                        "created_at": getattr(source, "latest_comparison_at", None),
                        "counts": counts,
                        "preparation_state": preparation_state,
                        "is_current": bool(
                            comparison_id
                            and getattr(source, "latest_comparison_collection_id", None)
                            == getattr(source, "latest_run_id", None)
                        ),
                    }
                )
        operation_rows = ()
        if selected_view == "operations" and can_view_runs:
            operation_rows = tuple(
                reconciliation_row(run, include_comparison=can_view_comparisons)
                for run in _reconciliation_queryset(include_comparison=can_view_comparisons)[:100]
            )
            operation_rows = tuple(row for row in operation_rows if row.state_group != "complete")[:20]

        alignment_trend = None
        if selected_view == "summary" and can_view_sources and can_view_comparisons and sources:
            current = timezone.now()
            start = timezone.make_aware(
                datetime.combine(timezone.localdate(current) - timedelta(days=selected_period - 1), time.min),
                timezone.get_current_timezone(),
            )
            baseline = ComparisonRun.objects.filter(
                collection_run__source=OuterRef("pk"),
                created_at__lt=start,
            ).order_by("-created_at")
            baseline_ids = tuple(
                DiscoverySource.objects.filter(pk__in=(source.pk for source in sources))
                .annotate(ui_baseline_id=Subquery(baseline.values("pk")[:1]))
                .exclude(ui_baseline_id=None)
                .values_list("ui_baseline_id", flat=True)
            )
            trend_comparisons = ComparisonRun.objects.filter(
                models.Q(collection_run__source_id__in=(source.pk for source in sources), created_at__gte=start)
                | models.Q(pk__in=baseline_ids)
            ).select_related("collection_run")
            alignment_trend = build_alignment_trend(
                sources,
                trend_comparisons,
                days=selected_period,
                now=current,
            )

        source_health_rows = tuple((source, source_health(source)) for source in sources)
        contributor_rows = tuple(
            sorted(
                drift_rows,
                key=lambda row: row["counts"].drifted + row["counts"].needs_attention,
                reverse=True,
            )[:8]
        )
        return render(
            request,
            self.template_name,
            {
                "selected_view": selected_view,
                "selected_period": selected_period,
                "period_options": (7, 30, 90),
                "source_rows": source_health_rows,
                "source_count": len(sources) if can_view_sources else None,
                "can_view_sources": can_view_sources,
                "can_view_drift": can_view_sources and can_view_comparisons,
                "enabled_agent_count": CollectorAgent.objects.filter(enabled=True).count() if can_view_agents else None,
                "pending_review_count": pending_reviews,
                "drift_rows": tuple(drift_rows),
                "drift_summary": drift_summary,
                "alignment_trend": alignment_trend,
                "contributor_rows": contributor_rows,
                "preparing_count": preparing_count,
                "operation_rows": operation_rows,
                "attention_count": sum(row.state_group == "attention" for row in operation_rows),
                "ready_review_count": sum(row.state_group == "review" for row in operation_rows),
                "ready_apply_count": sum(row.state_group == "apply" for row in operation_rows),
                "healthy_source_count": sum(health.status.color == "success" for _, health in source_health_rows),
                "events": _activity_events(request, limit=8) if selected_view == "operations" else (),
            },
        )


class SummaryView(OverviewView):
    selected_view = "summary"


class ActivityView(LoginRequiredMixin, View):
    template_name = "netbox_ssot/activity.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, {"events": _activity_events(request, limit=100)})


class ProviderCatalogView(LoginRequiredMixin, View):
    template_name = "netbox_ssot/provider_catalog.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        catalog = ProviderRegistry().discover()
        return render(
            request,
            self.template_name,
            {
                "providers": tuple(build_provider_card(item.manifest) for item in catalog.providers),
                "provider_failures": catalog.failures,
            },
        )


class NativeObjectListView(generic.ObjectListView):
    """Use NetBox's list UI for plugin models backed by standard Django querysets."""

    def has_permission(self) -> bool:
        return self.request.user.has_perm(self.get_required_permission())


class SourceListView(NativeObjectListView):
    queryset = DiscoverySource.objects.all()
    table = ssot_tables.SourceTable
    filterset = ssot_filtersets.SourceFilterSet
    filterset_form = ssot_forms.SourceFilterForm
    template_name = "netbox_ssot/native_object_list.html"
    actions = ()

    def get_queryset(self, request: HttpRequest) -> models.QuerySet[DiscoverySource]:
        return _source_queryset()

    def get_extra_context(self, request: HttpRequest) -> dict[str, Any]:
        context = {
            "page_title": "Sources",
            "page_description": "Remote systems that reconcile immutable provider evidence with this NetBox.",
        }
        if request.user.has_perm("netbox_ssot.add_discoverysource"):
            context.update(
                add_url=reverse("plugins:netbox_ssot:provider_list"),
                add_label="Add source",
            )
        return context


class SourceDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_discoverysource"
    template_name = "netbox_ssot/source_detail.html"
    selected_tab = "overview"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(_source_queryset(), pk=pk)
        selected_tab = self.selected_tab
        can_view_runs = request.user.has_perm("netbox_ssot.view_collectionrun")
        can_view_comparisons = request.user.has_perm("netbox_ssot.view_comparisonrun")
        run_limit = 20 if selected_tab == "history" else 1 if selected_tab == "overview" else 0
        recent_runs = (
            tuple(_reconciliation_queryset(include_comparison=can_view_comparisons).filter(source=source)[:run_limit])
            if can_view_runs and run_limit
            else ()
        )
        reconciliation_rows = tuple(
            reconciliation_row(run, include_comparison=can_view_comparisons) for run in recent_runs
        )
        current_reconciliation = reconciliation_rows[0] if selected_tab == "overview" and reconciliation_rows else None
        status_context = _source_status_context(request, source)
        command_context = _source_commands_context(request, source) if selected_tab == "overview" else {}
        provider_name = source.provider_id
        source_icon_class = "mdi mdi-database-outline"
        data_mappings = ()
        dataset_groups: tuple[Any, ...] = ()
        safe_configuration: dict[str, Any] = {}
        try:
            descriptor = ProviderRegistry().get(source.provider_id)
            manifest = descriptor.manifest
            provider_name = manifest.display_name
            source_icon_class = manifest.icon_class
            if selected_tab == "data":
                data_mappings = selected_data_model_mappings(manifest, source.datasets, source.configuration)
            elif selected_tab == "overview":
                dataset_groups = build_provider_dataset_groups(
                    manifest,
                    source.datasets,
                    include_supporting=True,
                )
            else:
                fields = build_provider_wizard(manifest).fields
                safe_configuration = redact_configuration(fields, source.configuration)
        except ProviderNotFoundError:
            safe_configuration = {"provider": "Installed provider is unavailable."}
        collection_request = {
            "run_id": str(uuid4()),
            "source_id": str(source.id),
            "provider_id": source.provider_id,
            "execution_mode": "agent",
            "datasets": source.datasets,
            "scope": [],
            "configuration": source.configuration,
        }
        return render(
            request,
            self.template_name,
            {
                "source": source,
                "provider_name": provider_name,
                "source_icon_class": source_icon_class,
                "destination_name": "NetBox",
                "destination_icon_class": "mdi mdi-cube-outline",
                "data_mappings": data_mappings,
                "dataset_groups": dataset_groups,
                "safe_configuration": json.dumps(safe_configuration, indent=2, sort_keys=True),
                "collection_request": json.dumps(collection_request, indent=2, sort_keys=True),
                "recent_runs": recent_runs,
                "reconciliation_rows": reconciliation_rows,
                "current_reconciliation": current_reconciliation,
                **command_context,
                **status_context,
                "retention": retention_plan(source) if selected_tab == "history" else None,
                "selected_tab": selected_tab,
            },
        )


class SourceStatusView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_discoverysource"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(_source_health_queryset(), pk=pk)
        context = _source_status_context(request, source)
        context["include_header_badge"] = True
        return render(
            request,
            "netbox_ssot/_source_status.html",
            context,
        )


class SourceCommandsView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_discoverysource"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(DiscoverySource, pk=pk)
        return render(
            request,
            "netbox_ssot/_source_commands.html",
            _source_commands_context(request, source),
        )


class SourceDataView(SourceDetailView):
    selected_tab = "data"


class SourceHistoryView(SourceDetailView):
    selected_tab = "history"


class SourceDeleteView(generic.ObjectDeleteView):
    queryset = DiscoverySource.objects.all()
    default_return_url = "plugins:netbox_ssot:source_list"

    def has_permission(self) -> bool:
        """Support plugin models backed by a standard Django queryset."""
        return self.request.user.has_perm("netbox_ssot.delete_discoverysource")


class SourceDatasetDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_discoverysource"
    template_name = "netbox_ssot/source_dataset_detail.html"

    def get(self, request: HttpRequest, pk: object, dataset_id: str) -> HttpResponse:
        source = get_object_or_404(_source_queryset(), pk=pk)
        try:
            manifest = ProviderRegistry().get(source.provider_id).manifest
        except ProviderNotFoundError as exc:
            raise Http404("The source provider is unavailable.") from exc
        datasets_by_id = {dataset.id: dataset for dataset in manifest.datasets}
        dataset = datasets_by_id.get(dataset_id)
        if dataset is None:
            raise Http404("The provider does not define this dataset.")
        dependencies = tuple(datasets_by_id[item] for item in dataset.depends_on)
        dependency_closure = _dataset_dependency_closure(manifest.datasets, dataset.id)
        required_by = tuple(item for item in manifest.datasets if dataset.id in item.depends_on)
        return render(
            request,
            self.template_name,
            {
                "source": source,
                "provider_name": manifest.display_name,
                "provider_icon_class": manifest.icon_class,
                "dataset": dataset,
                "included": dataset.id in source.datasets,
                "completeness_label": str(dataset.completeness).replace("_", " ").title(),
                "dependencies": dependencies,
                "dependency_closure": dependency_closure,
                "required_by": required_by,
                "data_mappings": dataset_data_model_mappings(manifest, dataset.id, source.configuration),
                "destination_name": "NetBox",
                "destination_icon_class": "mdi mdi-cube-outline",
                "dataset_json": json.dumps(dataset.model_dump(mode="json"), indent=2, sort_keys=True),
            },
        )


class SourceCommandCreateView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_agentcommand"
    command_kind: str

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(DiscoverySource.objects.select_related("assigned_agent"), pk=pk)
        if not source.enabled or source.assigned_agent is None or not source.assigned_agent.enabled:
            messages.error(request, "Enable this source and assign an enabled agent before requesting an action.")
            return _source_action_redirect(source)
        if source.assigned_agent.protocol_version != CURRENT_AGENT_PROTOCOL_VERSION:
            messages.error(
                request,
                f"Agent protocol {CURRENT_AGENT_PROTOCOL_VERSION} is required. "
                "Restart this agent with the current binary.",
            )
            return _source_action_redirect(source)
        if issue := (
            source_capability_issue(source.assigned_agent, source.provider_id)
            or agent_collection_policy_issue(source.assigned_agent)
        ):
            messages.error(request, issue)
            return _source_action_redirect(source)
        try:
            with transaction.atomic():
                command, created = AgentCommand.objects.get_or_create(
                    source=source,
                    agent=source.assigned_agent,
                    kind=self.command_kind,
                    state__in=ACTIVE_COMMAND_STATES,
                    defaults={"requested_by": request.user, "state": AgentCommand.State.PENDING},
                )
        except IntegrityError:
            command = AgentCommand.objects.filter(
                source=source,
                kind=self.command_kind,
                state__in=ACTIVE_COMMAND_STATES,
            ).first()
            created = False
        if created:
            messages.success(request, f"{command.get_kind_display()} queued for {source.assigned_agent.name}.")
        else:
            messages.info(request, "That action is already queued or running for this source.")
        return _source_action_redirect(source)


class SourceTestConnectionView(SourceCommandCreateView):
    command_kind = AgentCommand.Kind.TEST_CONNECTION


class SourceRunNowView(SourceCommandCreateView):
    command_kind = AgentCommand.Kind.RUN_NOW


class SourceWizardView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_discoverysource"
    template_name = "netbox_ssot/source_wizard.html"

    def get(self, request: HttpRequest, provider_id: str) -> HttpResponse:
        return render(request, self.template_name, self._context(provider_id))

    def post(self, request: HttpRequest, provider_id: str) -> HttpResponse:
        context = self._context(provider_id)
        safe_values: dict[str, Any] = {}
        try:
            configuration, safe_values = _configuration_from_post(request, context["wizard"])
            validate_configuration(context["manifest"].config_schema, configuration)
            datasets = selected_dataset_ids(context["manifest"], tuple(request.POST.getlist("datasets")))
            if not datasets:
                raise ValueError("Select at least one dataset.")
            assigned_agent = _selected_agent(request.POST.get("assigned_agent", ""), provider_id)
            source = DiscoverySource(
                name=request.POST.get("source_name", "").strip(),
                provider_id=provider_id,
                configuration=configuration,
                datasets=list(datasets),
                assigned_agent=assigned_agent,
                collection_interval_minutes=int(request.POST.get("collection_interval_minutes", "60")),
                retention_days=int(request.POST.get("retention_days", "30")),
                retention_successful_runs=int(request.POST.get("retention_successful_runs", "10000")),
                retention_failure_days=int(request.POST.get("retention_failure_days", "30")),
            )
            source.full_clean()
            source.save()
            if assigned_agent is not None:
                _record_source_reassignment(source, None, assigned_agent, request.user)
        except (DjangoValidationError, SchemaContractError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Source created. Connect an agent to start collecting data.")
            return redirect("plugins:netbox_ssot:source_detail", pk=source.pk)

        context["safe_values"] = safe_values
        context["source_name"] = request.POST.get("source_name", "")
        context["selected_datasets"] = request.POST.getlist("datasets")
        context["selected_agent"] = request.POST.get("assigned_agent", "")
        context["collection_interval_minutes"] = request.POST.get("collection_interval_minutes", "60")
        context["retention_days"] = request.POST.get("retention_days", "30")
        context["retention_successful_runs"] = request.POST.get("retention_successful_runs", "10000")
        context["retention_failure_days"] = request.POST.get("retention_failure_days", "30")
        return render(request, self.template_name, context)

    @staticmethod
    def _context(provider_id: str) -> dict[str, Any]:
        try:
            descriptor = ProviderRegistry().get(provider_id)
        except ProviderNotFoundError as exc:
            raise Http404("Provider is not installed or failed validation") from exc
        wizard = build_provider_wizard(descriptor.manifest)
        return {
            "manifest": descriptor.manifest,
            "wizard": wizard,
            "wizard_fields": tuple(field.model_dump(mode="json") for field in wizard.fields),
            "dataset_groups": wizard.dataset_groups,
            "selected_datasets": wizard.default_datasets,
            "source_name": "",
            "safe_values": {},
            "agent_choices": _agent_choices(descriptor.manifest.provider_id),
            "selected_agent": "",
            "collection_interval_minutes": 60,
            "retention_days": 30,
            "retention_successful_runs": 10_000,
            "retention_failure_days": 30,
            "editing": False,
        }


class SourceEditView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.change_discoverysource"
    template_name = "netbox_ssot/source_wizard.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(DiscoverySource, pk=pk)
        return render(request, self.template_name, self._context(source))

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(DiscoverySource, pk=pk)
        previous_agent_id = source.assigned_agent_id
        previous_collection_settings = (
            source.name,
            source.configuration,
            source.datasets,
            source.assigned_agent_id,
            source.collection_interval_minutes,
            source.enabled,
        )
        context = self._context(source)
        safe_values: dict[str, Any] = {}
        try:
            configuration, safe_values = _configuration_from_post(
                request,
                context["wizard"],
                existing=source.configuration,
            )
            validate_configuration(context["manifest"].config_schema, configuration)
            datasets = selected_dataset_ids(context["manifest"], tuple(request.POST.getlist("datasets")))
            if not datasets:
                raise ValueError("Select at least one dataset.")
            source.name = request.POST.get("source_name", "").strip()
            source.configuration = configuration
            source.datasets = list(datasets)
            source.assigned_agent = _selected_agent(request.POST.get("assigned_agent", ""), source.provider_id)
            source.collection_interval_minutes = int(request.POST.get("collection_interval_minutes", "60"))
            source.retention_days = int(request.POST.get("retention_days", "30"))
            source.retention_successful_runs = int(request.POST.get("retention_successful_runs", "10000"))
            source.retention_failure_days = int(request.POST.get("retention_failure_days", "30"))
            source.enabled = "enabled" in request.POST
            source.full_clean()
            current_collection_settings = (
                source.name,
                source.configuration,
                source.datasets,
                source.assigned_agent_id,
                source.collection_interval_minutes,
                source.enabled,
            )
            if current_collection_settings == previous_collection_settings:
                source.save(
                    update_fields=(
                        "retention_days",
                        "retention_successful_runs",
                        "retention_failure_days",
                    )
                )
            else:
                source.save()
        except (DjangoValidationError, SchemaContractError, ValueError) as exc:
            messages.error(request, str(exc))
            context.update(
                {
                    "safe_values": safe_values,
                    "source_name": request.POST.get("source_name", ""),
                    "selected_datasets": request.POST.getlist("datasets"),
                    "selected_agent": request.POST.get("assigned_agent", ""),
                    "collection_interval_minutes": request.POST.get("collection_interval_minutes", "60"),
                    "retention_days": request.POST.get("retention_days", "30"),
                    "retention_successful_runs": request.POST.get("retention_successful_runs", "10000"),
                    "retention_failure_days": request.POST.get("retention_failure_days", "30"),
                    "enabled": "enabled" in request.POST,
                }
            )
            return render(request, self.template_name, context)
        if not source.enabled or source.assigned_agent_id != previous_agent_id:
            source.active_collection_started_at = None
            source.active_collection_seen_at = None
            source.save(update_fields=("active_collection_started_at", "active_collection_seen_at"))
            _cancel_source_commands(
                source,
                "Action cancelled because the source was disabled or assigned to a different agent.",
            )
        if source.assigned_agent_id != previous_agent_id:
            previous_agent = CollectorAgent.objects.filter(pk=previous_agent_id).first()
            _record_source_reassignment(source, previous_agent, source.assigned_agent, request.user)
        messages.success(request, "Source updated. The assigned agent will pick up the new revision automatically.")
        return redirect("plugins:netbox_ssot:source_detail", pk=source.pk)

    @staticmethod
    def _context(source: DiscoverySource) -> dict[str, Any]:
        context = SourceWizardView._context(source.provider_id)
        context.update(
            {
                "source": source,
                "source_name": source.name,
                "safe_values": {
                    field.name: source.configuration[field.name]
                    for field in context["wizard"].fields
                    if not field.secret and field.name in source.configuration
                },
                "selected_datasets": source.datasets,
                "selected_agent": str(source.assigned_agent_id or ""),
                "collection_interval_minutes": source.collection_interval_minutes,
                "retention_days": source.retention_days,
                "retention_successful_runs": source.retention_successful_runs,
                "retention_failure_days": source.retention_failure_days,
                "enabled": source.enabled,
                "editing": True,
            }
        )
        return context


def _configuration_from_post(
    request: HttpRequest,
    wizard: Any,
    *,
    existing: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    configuration: dict[str, Any] = {}
    safe_values: dict[str, Any] = {}
    existing = existing or {}
    for field in wizard.fields:
        if field.widget is FieldWidget.CHECKBOX:
            value: Any = field.name in request.POST
        elif field.widget is FieldWidget.MULTISELECT:
            value = request.POST.getlist(field.name)
        else:
            value = request.POST.get(field.name, "")
            if value == "" and field.secret and field.name in existing:
                value = existing[field.name]
            elif value == "" and not field.required:
                continue
            if field.value_type == "integer":
                value = int(value)
            elif field.value_type == "number":
                value = float(value)
        configuration[field.name] = value
        if not field.secret:
            safe_values[field.name] = value
    return configuration, safe_values


def _agent_choices(provider_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "agent": agent,
            "issue": source_capability_issue(agent, provider_id) or agent_collection_policy_issue(agent),
        }
        for agent in CollectorAgent.objects.filter(enabled=True)
    )


def _selected_agent(value: str, provider_id: str) -> CollectorAgent | None:
    if not value:
        return None
    try:
        agent = CollectorAgent.objects.get(pk=value, enabled=True)
    except (CollectorAgent.DoesNotExist, DjangoValidationError, ValueError) as exc:
        raise ValueError("Select an enabled agent.") from exc
    if issue := source_capability_issue(agent, provider_id) or agent_collection_policy_issue(agent):
        raise ValueError(issue)
    return agent


def _record_source_reassignment(
    source: DiscoverySource,
    previous_agent: CollectorAgent | None,
    assigned_agent: CollectorAgent | None,
    actor: Any,
) -> None:
    AgentSecurityEvent.objects.create(
        agent=assigned_agent or previous_agent,
        kind=AgentSecurityEvent.Kind.SOURCE_REASSIGNED,
        actor=actor,
        details={
            "source_id": str(source.id),
            "source_name": source.name,
            "previous_agent_id": str(previous_agent.pk) if previous_agent else None,
            "previous_agent_name": previous_agent.name if previous_agent else None,
            "assigned_agent_id": str(assigned_agent.pk) if assigned_agent else None,
            "assigned_agent_name": assigned_agent.name if assigned_agent else None,
        },
    )


class AgentListView(NativeObjectListView):
    queryset = CollectorAgent.objects.all()
    table = ssot_tables.AgentTable
    filterset = ssot_filtersets.AgentFilterSet
    filterset_form = ssot_forms.AgentFilterForm
    template_name = "netbox_ssot/native_object_list.html"
    actions = ()

    def get_queryset(self, request: HttpRequest) -> models.QuerySet[CollectorAgent]:
        return CollectorAgent.objects.annotate(source_count=models.Count("sources", distinct=True)).prefetch_related(
            "sources", "signing_keys"
        )

    def get_extra_context(self, request: HttpRequest) -> dict[str, Any]:
        context = {
            "page_title": "Agents",
            "page_description": "Secure edge runtimes that collect provider data without exposing credentials.",
        }
        if request.user.has_perm("netbox_ssot.add_collectoragent"):
            context.update(
                add_url=reverse("plugins:netbox_ssot:agent_add"),
                add_label="Connect agent",
            )
        return context


class AgentDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectoragent"
    template_name = "netbox_ssot/agent_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        agent = get_object_or_404(
            CollectorAgent.objects.prefetch_related("sources", "signing_keys", "security_events__actor"),
            pk=pk,
        )
        recent_commands = AgentCommand.objects.filter(agent=agent).select_related("source", "requested_by")[:20]
        control_endpoint = request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-config"))
        return render(
            request,
            self.template_name,
            {
                "agent": agent,
                "health": agent_health(agent),
                "sources": tuple(
                    (source, source_health(source)) for source in _source_queryset().filter(assigned_agent=agent)
                ),
                "signing_keys": agent.signing_keys.all(),
                "security_events": agent.security_events.select_related("actor")[:20],
                "capability_rows": agent_capability_rows(agent),
                "recent_commands": recent_commands,
                "control_endpoint": control_endpoint,
                "control_endpoint_is_insecure": control_endpoint.startswith("http://"),
            },
        )


class AgentDeleteView(generic.ObjectDeleteView):
    queryset = CollectorAgent.objects.all()
    default_return_url = "plugins:netbox_ssot:agent_list"

    def has_permission(self) -> bool:
        """Support plugin models backed by a standard Django queryset."""
        return self.request.user.has_perm("netbox_ssot.delete_collectoragent")


class AgentEditView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.change_collectoragent"
    template_name = "netbox_ssot/agent_edit.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        agent = get_object_or_404(CollectorAgent, pk=pk)
        return render(request, self.template_name, self._context(request, agent))

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        agent = get_object_or_404(CollectorAgent, pk=pk)
        try:
            agent.name = request.POST.get("name", "").strip()
            agent.control_interval_seconds = int(request.POST.get("control_interval_seconds", "5"))
            requested_enabled = "enabled" in request.POST
            usable_key_exists = (
                agent.signing_keys.filter(state="active").exists()
                or agent.signing_keys.filter(
                    state="retiring",
                    retire_after__gt=timezone.now(),
                ).exists()
            )
            if requested_enabled and not usable_key_exists:
                raise DjangoValidationError("An agent without a usable signing key cannot be enabled.")
            agent.enabled = requested_enabled
            agent.full_clean()
            agent.save()
        except (DjangoValidationError, ValueError) as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, self._context(request, agent))
        if not agent.enabled:
            for source in agent.sources.all():
                _cancel_source_commands(source, "Action cancelled because the assigned agent was disabled.")
        messages.success(request, "Agent settings saved. A running agent will adopt the interval at its next check-in.")
        return redirect("plugins:netbox_ssot:agent_detail", pk=agent.pk)

    @staticmethod
    def _context(request: HttpRequest, agent: CollectorAgent) -> dict[str, Any]:
        return {
            "agent": agent,
            "signing_keys": agent.signing_keys.all(),
            "security_events": agent.security_events.select_related("actor")[:20],
            "capability_rows": agent_capability_rows(agent),
            "rotation_endpoint": request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-key-rotate")),
            "rotation_endpoint_is_insecure": request.build_absolute_uri("/").startswith("http://"),
        }


class AgentReplaceView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.change_collectoragent"
    template_name = "netbox_ssot/agent_replace.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        agent = get_object_or_404(CollectorAgent.objects.prefetch_related("sources"), pk=pk)
        return render(request, self.template_name, self._context(request, agent))

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        agent = get_object_or_404(CollectorAgent.objects.prefetch_related("sources"), pk=pk)
        try:
            created = create_replacement_enrollment(agent=agent, created_by=request.user)
        except AgentSecurityError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, self._context(request, agent))
        context = self._context(request, agent)
        context.update({"created_enrollment": created.enrollment, "enrollment_token": created.token})
        response = render(request, self.template_name, context)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @staticmethod
    def _context(request: HttpRequest, agent: CollectorAgent) -> dict[str, Any]:
        endpoint = request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-enroll"))
        return {
            "agent": agent,
            "enrollment_endpoint": endpoint,
            "enrollment_endpoint_is_insecure": endpoint.startswith("http://"),
        }


class AgentRevokeKeysView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.change_collectoragent"

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        agent = get_object_or_404(CollectorAgent, pk=pk)
        revoked = revoke_agent_keys(agent=agent, actor=request.user)
        messages.warning(request, f"Revoked {revoked} signing key(s) and disabled {agent.name}.")
        return redirect("plugins:netbox_ssot:agent_detail", pk=agent.pk)


class AgentEnrollmentView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_collectoragent"
    template_name = "netbox_ssot/agent_enrollment.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self._context(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        sources = DiscoverySource.objects.filter(enabled=True, assigned_agent__isnull=True)
        selected_sources = sources.filter(pk__in=request.POST.getlist("sources"))
        try:
            created = create_enrollment(
                agent_name=request.POST.get("name", ""),
                sources=selected_sources,
                created_by=request.user,
                lifetime=timedelta(minutes=15),
            )
        except AgentSecurityError as exc:
            messages.error(request, str(exc))
            context = self._context(request, selected_sources=request.POST.getlist("sources"))
            context["agent_name"] = request.POST.get("name", "")
            return render(request, self.template_name, context)
        context = self._context(request)
        context.update(
            {
                "created_enrollment": created.enrollment,
                "enrollment": created.enrollment,
                "enrollment_state": "waiting",
                "enrollment_token": created.token,
                "agent_name": created.enrollment.agent_name,
            }
        )
        response = render(request, self.template_name, context)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @staticmethod
    def _context(
        request: HttpRequest,
        *,
        selected_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "sources": DiscoverySource.objects.filter(enabled=True, assigned_agent__isnull=True),
            "selected_sources": selected_sources or [],
            "enrollment_endpoint": request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-enroll")),
            "enrollment_endpoint_is_insecure": request.build_absolute_uri("/").startswith("http://"),
        }


class AgentEnrollmentStatusView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_collectoragent"
    template_name = "netbox_ssot/_agent_enrollment_status.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        enrollment = get_object_or_404(
            AgentEnrollmentToken.objects.select_related("enrolled_agent"),
            pk=pk,
            created_by=request.user,
        )
        if enrollment.used_at and enrollment.enrolled_agent_id:
            state = "complete"
        elif enrollment.revoked_at:
            state = "revoked"
        elif enrollment.expires_at <= timezone.now():
            state = "expired"
        else:
            state = "waiting"
        response = render(
            request,
            self.template_name,
            {"enrollment": enrollment, "enrollment_state": state},
        )
        response.headers["Cache-Control"] = "no-store"
        return response


class ReconciliationListView(NativeObjectListView):
    queryset = CollectionRun.objects.all()
    table = ssot_tables.ReconciliationTable
    filterset = ssot_filtersets.ReconciliationFilterSet
    filterset_form = ssot_forms.ReconciliationFilterForm
    template_name = "netbox_ssot/native_object_list.html"
    actions = ()

    def get(self, request: HttpRequest) -> HttpResponse:
        if "period" not in request.GET or "workflow_state" not in request.GET:
            query = request.GET.copy()
            if "period" not in query:
                query["period"] = "30"
            if "workflow_state" not in query:
                query["workflow_state"] = "action_required"
            return HttpResponseRedirect(f"{request.path}?{query.urlencode()}")
        return super().get(request)

    def get_queryset(self, request: HttpRequest) -> models.QuerySet[CollectionRun]:
        return _reconciliation_queryset(include_comparison=request.user.has_perm("netbox_ssot.view_comparisonrun"))

    def get_table(self, data: Any, request: HttpRequest, bulk_actions: bool = True) -> ssot_tables.ReconciliationTable:
        table = self.table(
            data,
            include_comparison=request.user.has_perm("netbox_ssot.view_comparisonrun"),
        )
        table.configure(request)
        return table

    def get_extra_context(self, request: HttpRequest) -> dict[str, Any]:
        return {
            "page_title": "Reconciliations",
            "page_description": "Follow each collection through comparison, review, and apply in one lifecycle.",
        }


class RunListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectionrun"

    def get(self, request: HttpRequest) -> HttpResponse:
        return redirect("plugins:netbox_ssot:reconciliation_list")


class RunDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectionrun"
    template_name = "netbox_ssot/run_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        run = get_object_or_404(_reconciliation_queryset(), pk=pk)
        raw_counts = list(
            run.stored_observations.values("resource_kind").annotate(count=models.Count("id")).order_by("resource_kind")
        )
        provider_name, model_metadata = _provider_model_metadata(run.provider_id)

        count_rows = []
        for item in raw_counts:
            resource_kind = item["resource_kind"]
            display_name, source_model, dataset_title = model_metadata.get(
                resource_kind,
                (resource_kind.replace("_", " ").title(), resource_kind, "Other"),
            )
            count_rows.append(
                {
                    "resource_kind": resource_kind,
                    "display_name": display_name,
                    "source_model": source_model,
                    "dataset_title": dataset_title,
                    "count": item["count"],
                    "percentage": item["count"] / run.observation_count * 100 if run.observation_count else 0,
                }
            )

        model_count = len(count_rows)
        query = request.GET.get("q", "").strip()[:100]
        if query:
            normalized_query = query.casefold()
            count_rows = [
                row
                for row in count_rows
                if normalized_query
                in (
                    f"{row['display_name']} {row['source_model']} {row['resource_kind']} {row['dataset_title']}"
                ).casefold()
            ]
        selected_sort = request.GET.get("sort", "count_desc")
        sort_options = {
            "count_desc": ("Most records", lambda row: (-row["count"], row["display_name"].casefold())),
            "count_asc": ("Fewest records", lambda row: (row["count"], row["display_name"].casefold())),
            "name": ("Model name", lambda row: row["display_name"].casefold()),
        }
        if selected_sort not in sort_options:
            selected_sort = "count_desc"
        count_rows.sort(key=sort_options[selected_sort][1])
        page = Paginator(count_rows, 50).get_page(request.GET.get("page"))
        duration_seconds = max(0.0, (run.completed_at - run.started_at).total_seconds())
        comparisons = tuple(
            run.comparisons.select_related(
                "final_review",
                "final_review__reviewed_by",
                "apply_run",
                "apply_run__applied_by",
            )[:20]
        )
        comparison_preparation = (
            ComparisonPreparation.objects.select_related("comparison").filter(collection_run=run).first()
        )
        latest_comparison = comparisons[0] if comparisons else None
        latest_review = getattr(latest_comparison, "final_review", None) if latest_comparison else None
        latest_application = getattr(latest_comparison, "apply_run", None) if latest_comparison else None
        latest_progress = (
            review_progress(latest_comparison)
            if latest_comparison is not None and latest_review is None and latest_application is None
            else None
        )
        return render(
            request,
            self.template_name,
            {
                "run": run,
                "reconciliation": reconciliation_row(run),
                "provider_name": provider_name,
                "page": page,
                "model_count": model_count,
                "filtered_model_count": len(count_rows),
                "query": query,
                "sort_options": tuple((value, label) for value, (label, _) in sort_options.items()),
                "selected_sort": selected_sort,
                "duration_seconds": duration_seconds,
                "comparisons": comparisons,
                "comparison_preparation": comparison_preparation,
                **_preparation_status_context(request, comparison_preparation),
                "workflow": workflow_presentation(
                    request,
                    run,
                    comparison=latest_comparison,
                    preparation=comparison_preparation,
                    final_review=latest_review,
                    application=latest_application,
                    progress=latest_progress,
                    current_stage="run",
                ),
            },
        )


class RunStatusView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectionrun"
    template_name = "netbox_ssot/_comparison_status.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        run = get_object_or_404(CollectionRun, pk=pk)
        preparation = get_object_or_404(
            ComparisonPreparation.objects.select_related("comparison"),
            collection_run=run,
        )
        if not getattr(request, "htmx", False):
            return redirect("plugins:netbox_ssot:run_detail", pk=run.pk)
        if preparation.state == ComparisonPreparation.State.COMPLETED and preparation.comparison_id:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse(
                "plugins:netbox_ssot:comparison_detail",
                kwargs={"pk": preparation.comparison_id},
            )
            return response
        if preparation.state == ComparisonPreparation.State.FAILED:
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        return render(
            request,
            self.template_name,
            {
                "run": run,
                "comparison_preparation": preparation,
                **_preparation_status_context(request, preparation),
            },
        )


class ObservationListView(PermissionRequiredMixin, View):
    permission_required = ("netbox_ssot.view_collectionrun", "netbox_ssot.view_storedobservation")
    template_name = "netbox_ssot/observation_list.html"

    def get(self, request: HttpRequest, pk: object, resource_kind: str) -> HttpResponse:
        run = get_object_or_404(CollectionRun.objects.select_related("source", "agent"), pk=pk)
        observations = run.stored_observations.filter(resource_kind=resource_kind).order_by("sequence")
        if not observations.exists():
            raise Http404("This collection does not contain the requested model type.")
        observation_count = observations.count()
        query = request.GET.get("q", "").strip()[:100]
        if query:
            observations = observations.annotate(
                searchable_attributes=Cast("attributes", output_field=models.TextField())
            ).filter(models.Q(external_id__icontains=query) | models.Q(searchable_attributes__icontains=query))
        page = Paginator(observations, 100).get_page(request.GET.get("page"))
        provider_name, model_metadata = _provider_model_metadata(run.provider_id)
        display_name, source_model, dataset_title = model_metadata.get(
            resource_kind,
            (resource_kind.replace("_", " ").title(), resource_kind, "Other"),
        )
        observation_rows = _observation_rows(run, tuple(page.object_list))
        return render(
            request,
            self.template_name,
            {
                "run": run,
                "provider_name": provider_name,
                "resource_kind": resource_kind,
                "display_name": display_name,
                "source_model": source_model,
                "dataset_title": dataset_title,
                "query": query,
                "observation_count": observation_count,
                "page": page,
                "observation_rows": observation_rows,
            },
        )


class ObservationDetailView(PermissionRequiredMixin, View):
    permission_required = ("netbox_ssot.view_collectionrun", "netbox_ssot.view_storedobservation")
    template_name = "netbox_ssot/observation_detail.html"

    def get(self, request: HttpRequest, run_pk: object, pk: object) -> HttpResponse:
        observation = get_object_or_404(
            StoredObservation.objects.select_related("run", "run__source", "run__agent"),
            run_id=run_pk,
            pk=pk,
        )
        run = observation.run
        provider_name, model_metadata = _provider_model_metadata(run.provider_id)
        model_name, source_model, dataset_title = model_metadata.get(
            observation.resource_kind,
            (
                observation.resource_kind.replace("_", " ").title(),
                observation.resource_kind,
                "Other",
            ),
        )
        resolver = RecordLinkResolver(run.source)
        source_url = resolver.resolve(
            resource_kind=observation.resource_kind,
            source_object_id=source_object_id(observation.evidence),
            target_object_type="",
            target_object_id="",
        ).source_url
        relationship_rows = _relationship_rows(run, observation.relationships)
        return render(
            request,
            self.template_name,
            {
                "observation": observation,
                "run": run,
                "provider_name": provider_name,
                "model_name": model_name,
                "source_model": source_model,
                "dataset_title": dataset_title,
                "display_name": _observation_display_name(observation),
                "source_url": source_url,
                "attribute_rows": tuple(
                    {
                        "path": item.get("path", ""),
                        "value": json.dumps(item.get("value"), indent=2, sort_keys=True),
                    }
                    for item in observation.attributes
                    if isinstance(item, dict)
                ),
                "relationship_rows": relationship_rows,
                "scope_rows": tuple(item for item in observation.scope if isinstance(item, dict)),
                "evidence_rows": _evidence_rows(resolver, observation.evidence),
            },
        )


class ComparisonCreateView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_comparisonrun"

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        run = get_object_or_404(CollectionRun, pk=pk)
        try:
            outcome = request_comparison_preparation(run, force=run.comparisons.exists())
        except ComparisonRejectedError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:netbox_ssot:run_detail", pk=run.pk)
        if outcome.preparation.state == ComparisonPreparation.State.FAILED:
            messages.error(
                request,
                "The comparison could not be queued. Retry after checking the NetBox background worker.",
            )
        elif outcome.queued:
            messages.success(request, "Comparison queued. It will be prepared safely in the background.")
        else:
            messages.info(request, "This comparison is already being prepared in the background.")
        return redirect("plugins:netbox_ssot:run_detail", pk=run.pk)


class ComparisonListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_comparisonrun"

    def get(self, request: HttpRequest) -> HttpResponse:
        url = reverse("plugins:netbox_ssot:reconciliation_list")
        return HttpResponseRedirect(url)


class ComparisonDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_comparisonrun"
    template_name = "netbox_ssot/comparison_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        comparison = get_object_or_404(
            ComparisonRun.objects.select_related("collection_run", "collection_run__source"),
            pk=pk,
        )
        items = comparison.items.annotate(
            ui_change_count=models.Func(
                models.F("changes"),
                function="jsonb_array_length",
                output_field=models.IntegerField(),
            )
        ).defer("changes", "source_data", "target_data")
        action = request.GET.get("action", "")
        resource_kind = request.GET.get("kind", "")
        if action in ComparisonItem.Action.values:
            items = items.filter(action=action)
        if resource_kind:
            items = items.filter(resource_kind=resource_kind)
        kinds = comparison.items.order_by("resource_kind").values_list("resource_kind", flat=True).distinct()
        page = Paginator(items, 100).get_page(request.GET.get("page"))
        application = ApplyRun.objects.filter(comparison=comparison).select_related("applied_by").first()
        final_review = ComparisonReview.objects.filter(comparison=comparison).select_related("reviewed_by").first()
        latest_decisions = latest_review_decisions(
            comparison,
            item_ids=tuple(item.pk for item in page.object_list),
        )
        for item in page.object_list:
            item.current_review_decision = latest_decisions.get(item.pk)
        progress = review_progress(comparison)
        blocked_item_reasons = tuple(
            comparison.items.filter(action__in=(ComparisonItem.Action.CONFLICT, ComparisonItem.Action.SKIPPED))
            .values("action", "resource_kind", "reason")
            .annotate(count=models.Count("pk"))
            .order_by("-count", "resource_kind", "reason")[:10]
        )
        readiness = None if application else inspect_application_summary(comparison, request.user)
        preapproval_blockers = (
            tuple(reason for reason in readiness.reasons if reason != APPROVAL_REQUIRED_REASON)
            if readiness is not None and final_review is None
            else ()
        )
        if application:
            review_state = "applied"
        elif final_review is not None and final_review.decision == ComparisonReview.Decision.REJECTED:
            review_state = "rejected"
        elif readiness is not None and readiness.current_target_digest != comparison.target_snapshot_digest:
            review_state = "stale"
        elif final_review is not None:
            review_state = final_review.decision
        elif progress.actionable_count or comparison.conflict_count or comparison.skipped_count:
            review_state = "in_review"
        else:
            review_state = "no_changes"
        review_state_label = {
            "applied": "Applied",
            "approved": "Approved",
            "in_review": "In review",
            "no_changes": "No changes",
            "rejected": "Rejected",
            "stale": "Stale",
        }.get(review_state, review_state.replace("_", " ").title())
        return render(
            request,
            self.template_name,
            {
                "comparison": comparison,
                "page": page,
                "actions": ComparisonItem.Action.choices,
                "kinds": kinds,
                "selected_action": action,
                "selected_kind": resource_kind,
                "application": application,
                "final_review": final_review,
                "review_progress": progress,
                "review_state": review_state,
                "review_state_label": review_state_label,
                "readiness": readiness,
                "preapproval_blockers": preapproval_blockers,
                "has_changes": bool(comparison.create_count or comparison.update_count),
                "blocked_item_reasons": blocked_item_reasons,
                "workflow": workflow_presentation(
                    request,
                    comparison.collection_run,
                    comparison=comparison,
                    final_review=final_review,
                    application=application,
                    readiness=readiness,
                    progress=progress,
                    current_stage="comparison",
                ),
                **(
                    _apply_job_context(request, comparison)
                    if application is None
                    and final_review is not None
                    and final_review.decision == ComparisonReview.Decision.APPROVED
                    else {}
                ),
            },
        )


class ComparisonItemDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_comparisonitem"
    template_name = "netbox_ssot/comparison_item_detail.html"

    def get(self, request: HttpRequest, comparison_pk: object, pk: object) -> HttpResponse:
        item = get_object_or_404(
            ComparisonItem.objects.select_related(
                "comparison",
                "comparison__collection_run",
                "comparison__collection_run__source",
            ),
            comparison_id=comparison_pk,
            pk=pk,
        )
        record_links = _record_links(
            item.comparison.collection_run,
            item.comparison.collection_run.source,
            (item,),
        )[0][1]
        provider_name, model_metadata = _provider_model_metadata(item.comparison.collection_run.provider_id)
        source_name, source_model, _ = model_metadata.get(
            item.resource_kind,
            (item.resource_kind.replace("_", " ").title(), item.resource_kind, ""),
        )
        previous_item = item.comparison.items.filter(sequence__lt=item.sequence).order_by("-sequence").first()
        next_item = item.comparison.items.filter(sequence__gt=item.sequence).order_by("sequence").first()
        final_review = ComparisonReview.objects.filter(comparison=item.comparison).select_related("reviewed_by").first()
        current_decision = latest_review_decision(item)
        relationship_identities = _comparison_relationship_identities(item.source_data, item.target_data)
        relationship_labels = dict(
            item.comparison.items.filter(identity_key__in=relationship_identities).values_list(
                "identity_key",
                "display_name",
            )
        )
        return render(
            request,
            self.template_name,
            {
                "item": item,
                "source_data": json.dumps(item.source_data, indent=2, sort_keys=True),
                "target_data": json.dumps(item.target_data, indent=2, sort_keys=True),
                "changes": json.dumps(item.changes, indent=2, sort_keys=True),
                "record_links": record_links,
                "field_rows": comparison_field_rows(
                    item.source_data,
                    item.target_data,
                    target_exists=bool(item.target_object_id),
                    action=item.action,
                    relationship_labels=relationship_labels,
                ),
                "provider_name": provider_name,
                "source_name": source_name,
                "source_model": source_model,
                "destination_name": item.resource_kind.replace("_", " ").title(),
                "previous_item": previous_item,
                "next_item": next_item,
                "final_review": final_review,
                "current_decision": current_decision,
            },
        )


def _comparison_relationship_identities(*payloads: Any) -> frozenset[str]:
    identities: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        relationships = payload.get("relationships", {})
        if not isinstance(relationships, dict):
            continue
        for value in relationships.values():
            values = value if isinstance(value, list) else [value]
            identities.update(item for item in values if isinstance(item, str) and item)
    return frozenset(identities)


class ComparisonReviewActionView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_comparisonreview"

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        comparison = get_object_or_404(ComparisonRun, pk=pk)
        action = request.POST.get("action", "")
        reason = request.POST.get("reason", "")
        try:
            if action == "approve_all":
                progress = approve_all_review_items(comparison, request.user)
                messages.success(request, f"Approved {progress.approved_count} proposed changes for final review.")
            elif action == "approve_all_and_finalize":
                _ensure_review_can_progress_to_apply(comparison)
                approve_all_review_items(comparison, request.user)
                finalize_review(
                    comparison,
                    ComparisonReview.Decision.APPROVED,
                    request.user,
                    reason=reason,
                )
                messages.success(request, "Review approved. Continue to the apply step below.")
            elif action == "finalize_approval":
                _ensure_review_can_progress_to_apply(comparison)
                finalize_review(
                    comparison,
                    ComparisonReview.Decision.APPROVED,
                    request.user,
                    reason=reason,
                )
                messages.success(request, "Review approved. Continue to the apply step below.")
            elif action == "reject":
                finalize_review(
                    comparison,
                    ComparisonReview.Decision.REJECTED,
                    request.user,
                    reason=reason,
                )
                messages.success(request, "The comparison review was rejected and finalized.")
            else:
                raise ReviewRejectedError("Choose a supported review action.")
        except ReviewRejectedError as exc:
            messages.error(request, str(exc))
        return redirect("plugins:netbox_ssot:comparison_detail", pk=comparison.pk)


class ComparisonItemDecisionView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_comparisonreview"

    def post(self, request: HttpRequest, comparison_pk: object, pk: object) -> HttpResponse:
        comparison = get_object_or_404(ComparisonRun, pk=comparison_pk)
        item = get_object_or_404(ComparisonItem, comparison=comparison, pk=pk)
        try:
            decision = record_review_decision(
                comparison,
                item,
                request.POST.get("decision", ""),
                request.user,
                reason=request.POST.get("reason", ""),
            )
        except ReviewRejectedError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Recorded {decision.get_decision_display().lower()} for {item.display_name}.")
        if request.POST.get("return") == "comparison":
            parameters = {}
            return_action = request.POST.get("return_action", "")
            return_kind = request.POST.get("return_kind", "").strip()[:64]
            return_page = request.POST.get("return_page", "")
            if return_action in ComparisonItem.Action.values:
                parameters["action"] = return_action
            if return_kind:
                parameters["kind"] = return_kind
            if return_page.isdigit() and int(return_page) > 1:
                parameters["page"] = return_page
            url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
            return HttpResponseRedirect(f"{url}?{urlencode(parameters)}" if parameters else url)
        return redirect(
            "plugins:netbox_ssot:comparison_item_detail",
            comparison_pk=comparison.pk,
            pk=item.pk,
        )


class ApplyCreateView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.add_applyrun"

    def post(self, request: HttpRequest, pk: object) -> HttpResponse:
        comparison = get_object_or_404(
            ComparisonRun.objects.select_related("collection_run", "collection_run__source"),
            pk=pk,
        )
        if request.POST.get("confirm") != "apply":
            messages.error(request, "Confirm the reviewed comparison before applying it.")
            return redirect("plugins:netbox_ssot:comparison_detail", pk=comparison.pk)
        required_permissions = _required_target_permissions(comparison)
        if not request.user.has_perms(required_permissions):
            raise PermissionDenied("You do not have every NetBox target permission required by this comparison.")
        if request.POST.get("background"):
            job_name = apply_comparison_job_name(comparison.pk)
            active_job = None
            queued = False
            try:
                with transaction.atomic():
                    # Serialize enqueue attempts for this immutable comparison. The core Job
                    # row is then visible to the next requester before they can enqueue again.
                    ComparisonRun.objects.select_for_update().only("pk").get(pk=comparison.pk)
                    active_job = (
                        Job.objects.filter(
                            name=job_name,
                            status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
                        )
                        .order_by("-created")
                        .first()
                    )
                    if active_job is None:
                        active_job = ApplyComparisonJob.enqueue(
                            comparison_id=str(comparison.pk),
                            applied_by_id=str(request.user.pk),
                            required_permissions=list(required_permissions),
                            name=job_name,
                            user=request.user,
                            notifications="always",
                            job_timeout=get_plugin_config("netbox_ssot", "apply_job_timeout_seconds"),
                        )
                        queued = True
            except Exception as exc:
                stranded_job = (
                    Job.objects.filter(
                        name=job_name,
                        status__in=(
                            JobStatusChoices.STATUS_PENDING,
                            JobStatusChoices.STATUS_SCHEDULED,
                        ),
                    )
                    .order_by("-created")
                    .first()
                )
                if stranded_job is not None:
                    stranded_job.terminate(
                        status=JobStatusChoices.STATUS_ERRORED,
                        error=repr(exc),
                    )
                messages.error(
                    request,
                    "NetBox could not queue the application. Check the background worker and try again, "
                    "or uncheck the background option.",
                )
            else:
                if queued:
                    messages.success(
                        request,
                        "Application queued. NetBox will notify you when the atomic transaction finishes.",
                    )
                else:
                    messages.info(request, "This comparison is already waiting for a NetBox worker.")
            url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
            return HttpResponseRedirect(f"{url}#apply-actions")
        try:
            outcome = apply_comparison(comparison, request.user)
        except ApplicationRejectedError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:netbox_ssot:comparison_detail", pk=comparison.pk)
        if outcome.created:
            messages.success(request, "The reviewed comparison was applied in one transaction.")
            refresh = request_comparison_preparation(comparison.collection_run, force=True)
            if refresh.queued:
                messages.info(request, "Refreshing the drift snapshot in the background.")
        else:
            messages.info(request, "This comparison was already applied; no target objects were changed.")
        return redirect("plugins:netbox_ssot:apply_detail", pk=outcome.apply_run.pk)


class ApplyStatusView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_comparisonrun"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        comparison = get_object_or_404(ComparisonRun, pk=pk)
        if not getattr(request, "htmx", False):
            return redirect("plugins:netbox_ssot:comparison_detail", pk=comparison.pk)

        application = ApplyRun.objects.filter(comparison=comparison).only("pk").first()
        if application is not None:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse(
                "plugins:netbox_ssot:apply_detail",
                kwargs={"pk": application.pk},
            )
            return response

        context = _apply_job_context(request, comparison)
        if context["apply_job_active"]:
            return render(
                request,
                "netbox_ssot/_apply_status.html",
                {"comparison": comparison, **context},
            )

        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response


class ApplyListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_applyrun"

    def get(self, request: HttpRequest) -> HttpResponse:
        url = reverse("plugins:netbox_ssot:reconciliation_list")
        return HttpResponseRedirect(f"{url}?workflow_state=complete")


class ApplyDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_applyrun"
    template_name = "netbox_ssot/apply_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        apply_run = get_object_or_404(
            ApplyRun.objects.select_related(
                "comparison",
                "comparison__collection_run",
                "comparison__collection_run__source",
                "comparison__final_review",
                "comparison__final_review__reviewed_by",
                "applied_by",
            ),
            pk=pk,
        )
        page = Paginator(apply_run.items.all(), 100).get_page(request.GET.get("page"))
        record_rows = _record_links(
            apply_run.comparison.collection_run,
            apply_run.comparison.collection_run.source,
            tuple(page.object_list),
        )
        return render(
            request,
            self.template_name,
            {
                "apply_run": apply_run,
                "page": page,
                "record_rows": record_rows,
                "workflow": workflow_presentation(
                    request,
                    apply_run.comparison.collection_run,
                    comparison=apply_run.comparison,
                    final_review=apply_run.comparison.final_review,
                    application=apply_run,
                    current_stage="apply",
                ),
            },
        )


def _dataset_dependency_closure(datasets: tuple[Any, ...], dataset_id: str) -> tuple[Any, ...]:
    datasets_by_id = {dataset.id: dataset for dataset in datasets}
    dependency_ids: set[str] = set()
    pending = list(datasets_by_id[dataset_id].depends_on)
    while pending:
        dependency_id = pending.pop()
        if dependency_id in dependency_ids:
            continue
        dependency_ids.add(dependency_id)
        pending.extend(datasets_by_id[dependency_id].depends_on)
    return tuple(dataset for dataset in datasets if dataset.id in dependency_ids)


def _provider_model_metadata(provider_id: str) -> tuple[str, dict[str, tuple[str, str, str]]]:
    provider_name = provider_id
    model_metadata: dict[str, tuple[str, str, str]] = {}
    try:
        manifest = ProviderRegistry().get(provider_id).manifest
    except ProviderNotFoundError:
        return provider_name, model_metadata
    for dataset in manifest.datasets:
        for mapping in dataset.data_mappings:
            model_metadata[str(mapping.destination_kind)] = (
                mapping.source_name,
                mapping.source_model,
                dataset.title,
            )
    return manifest.display_name, model_metadata


def _observation_display_name(observation: StoredObservation) -> str:
    attributes = {
        item.get("path"): item.get("value")
        for item in observation.attributes
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for path in ("/name", "/ssid", "/asn", "/model", "/address", "/prefix", "/slug"):
        value = attributes.get(path)
        if value is not None and str(value):
            return str(value)
    return observation.external_id


def _observation_rows(
    run: CollectionRun,
    observations: tuple[StoredObservation, ...],
) -> tuple[dict[str, Any], ...]:
    relationship_targets = {
        (relationship.get("target_kind"), relationship.get("target_external_id"))
        for observation in observations
        for relationship in observation.relationships
        if isinstance(relationship, dict)
        and isinstance(relationship.get("target_kind"), str)
        and isinstance(relationship.get("target_external_id"), str)
    }
    existing_targets = _existing_observation_targets(run, relationship_targets)
    resolver = RecordLinkResolver(run.source)
    rows = []
    for observation in observations:
        relationships = tuple(item for item in observation.relationships if isinstance(item, dict))
        unresolved_count = sum(
            (item.get("target_kind"), item.get("target_external_id")) not in existing_targets for item in relationships
        )
        rows.append(
            {
                "observation": observation,
                "display_name": _observation_display_name(observation),
                "source_url": resolver.resolve(
                    resource_kind=observation.resource_kind,
                    source_object_id=source_object_id(observation.evidence),
                    target_object_type="",
                    target_object_id="",
                ).source_url,
                "attribute_count": len(observation.attributes),
                "relationship_count": len(relationships),
                "unresolved_count": unresolved_count,
            }
        )
    return tuple(rows)


def _relationship_rows(run: CollectionRun, relationships: Any) -> tuple[dict[str, Any], ...]:
    valid_relationships = tuple(
        item
        for item in relationships
        if isinstance(item, dict)
        and isinstance(item.get("target_kind"), str)
        and isinstance(item.get("target_external_id"), str)
    )
    targets = {(item["target_kind"], item["target_external_id"]) for item in valid_relationships}
    existing_targets = _existing_observation_targets(run, targets)
    return tuple(
        {
            "kind": item.get("kind", "relationship"),
            "target_kind": item["target_kind"],
            "target_external_id": item["target_external_id"],
            "target_url": (
                reverse(
                    "plugins:netbox_ssot:observation_detail",
                    kwargs={
                        "run_pk": run.pk,
                        "pk": existing_targets[(item["target_kind"], item["target_external_id"])],
                    },
                )
                if (item["target_kind"], item["target_external_id"]) in existing_targets
                else ""
            ),
        }
        for item in valid_relationships
    )


def _evidence_rows(resolver: RecordLinkResolver, evidence: Any) -> tuple[dict[str, Any], ...]:
    rows = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source_object_type = item.get("source_object_type", "")
        source_object_identifier = item.get("source_object_id", "")
        source_url = (
            resolver.resolve(
                resource_kind=source_object_type,
                source_object_id=str(source_object_identifier),
                target_object_type="",
                target_object_id="",
            ).source_url
            if isinstance(source_object_type, str) and isinstance(source_object_identifier, (str, int))
            else ""
        )
        rows.append({**item, "source_url": source_url})
    return tuple(rows)


def _existing_observation_targets(
    run: CollectionRun,
    targets: set[tuple[Any, Any]],
) -> dict[tuple[str, str], int]:
    if not targets:
        return {}
    target_kinds = {kind for kind, _ in targets}
    target_external_ids = {external_id for _, external_id in targets}
    return {
        (resource_kind, external_id): observation_id
        for resource_kind, external_id, observation_id in run.stored_observations.filter(
            resource_kind__in=target_kinds,
            external_id__in=target_external_ids,
        ).values_list("resource_kind", "external_id", "id")
        if (resource_kind, external_id) in targets
    }


def _record_links(
    collection_run: CollectionRun,
    source: DiscoverySource,
    items: tuple[Any, ...],
) -> tuple[tuple[Any, RecordLinks], ...]:
    external_ids = {item.source_external_id for item in items}
    resource_kinds = {item.resource_kind for item in items}
    evidence_by_identity = {
        (observation["resource_kind"], observation["external_id"]): observation["evidence"]
        for observation in collection_run.stored_observations.filter(
            resource_kind__in=resource_kinds,
            external_id__in=external_ids,
        ).values("resource_kind", "external_id", "evidence")
    }
    resolver = RecordLinkResolver(source)
    return tuple(
        (
            item,
            resolver.resolve(
                resource_kind=item.resource_kind,
                source_object_id=source_object_id(
                    evidence_by_identity.get((item.resource_kind, item.source_external_id), [])
                ),
                target_object_type=item.target_object_type,
                target_object_id=item.target_object_id,
            ),
        )
        for item in items
    )


def _required_target_permissions(comparison: ComparisonRun) -> tuple[str, ...]:
    permissions: set[str] = set()
    for item in comparison.items.filter(action__in=(ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE)):
        model = MODEL_BY_KIND.get(item.resource_kind)
        if model is None:
            raise PermissionDenied("The comparison contains a target model outside the supported apply scope.")
        operation = "add" if item.action == ComparisonItem.Action.CREATE else "change"
        permissions.add(f"{model._meta.app_label}.{operation}_{model._meta.model_name}")
    return tuple(sorted(permissions))


def _source_queryset() -> models.QuerySet[DiscoverySource]:
    latest_comparisons = ComparisonRun.objects.filter(collection_run__source=OuterRef("pk")).order_by(
        "-collection_run__completed_at",
        "-created_at",
    )
    latest_preparations = ComparisonPreparation.objects.filter(collection_run__source=OuterRef("pk")).order_by(
        "-collection_run__completed_at",
        "-updated_at",
    )
    return _source_health_queryset().annotate(
        latest_comparison_id=Subquery(latest_comparisons.values("id")[:1]),
        latest_comparison_collection_id=Subquery(latest_comparisons.values("collection_run_id")[:1]),
        latest_comparison_at=Subquery(latest_comparisons.values("created_at")[:1]),
        latest_create_count=Subquery(latest_comparisons.values("create_count")[:1]),
        latest_update_count=Subquery(latest_comparisons.values("update_count")[:1]),
        latest_no_change_count=Subquery(latest_comparisons.values("no_change_count")[:1]),
        latest_conflict_count=Subquery(latest_comparisons.values("conflict_count")[:1]),
        latest_skipped_count=Subquery(latest_comparisons.values("skipped_count")[:1]),
        latest_preparation_state=Subquery(latest_preparations.values("state")[:1]),
    )


def _source_health_queryset() -> models.QuerySet[DiscoverySource]:
    latest_runs = CollectionRun.objects.filter(source=OuterRef("pk")).order_by("-received_at")
    return DiscoverySource.objects.select_related("assigned_agent").annotate(
        latest_run_id=Subquery(latest_runs.values("run_id")[:1]),
        latest_collection_at=Subquery(latest_runs.values("received_at")[:1]),
        latest_collection_state=Subquery(latest_runs.values("state")[:1]),
        latest_collection_messages=Subquery(latest_runs.values("messages")[:1]),
        last_success_at=models.Max("runs__received_at", filter=models.Q(runs__state="complete")),
    )


def _source_status_context(request: HttpRequest, source: DiscoverySource) -> dict[str, Any]:
    health = source_health(source)
    latest_failure_messages = (
        collection_failure_messages(source.latest_collection_messages)
        if source.latest_collection_state != "complete" and health.status.key == "failed"
        else ()
    )
    failed_collection_url = ""
    if latest_failure_messages and source.latest_run_id and request.user.has_perm("netbox_ssot.view_collectionrun"):
        failed_collection_url = reverse(
            "plugins:netbox_ssot:run_detail",
            kwargs={"pk": source.latest_run_id},
        )
    return {
        "source": source,
        "health": health,
        "latest_failure_messages": latest_failure_messages,
        "failed_collection_url": failed_collection_url,
    }


def _source_commands_context(request: HttpRequest, source: DiscoverySource) -> dict[str, Any]:
    recent_commands = tuple(source.commands.select_related("requested_by")[:5])
    if recent_commands and request.user.has_perm("netbox_ssot.view_collectionrun"):
        collection_run_ids = set(
            CollectionRun.objects.filter(
                source=source,
                run_id__in=(command.id for command in recent_commands if command.kind == AgentCommand.Kind.RUN_NOW),
            ).values_list("run_id", flat=True)
        )
        for command in recent_commands:
            command.collection_run_url = (
                reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": command.id})
                if command.id in collection_run_ids
                else ""
            )
    return {
        "source": source,
        "recent_commands": recent_commands,
        "commands_polling": any(command.state in ACTIVE_COMMAND_STATES for command in recent_commands),
    }


def _reconciliation_queryset(*, include_comparison: bool = True) -> models.QuerySet[CollectionRun]:
    latest_comparison = ComparisonRun.objects.filter(collection_run=OuterRef("pk")).order_by("-created_at")
    queryset = CollectionRun.objects.select_related("source", "agent").annotate(
        ui_latest_comparison_id=Subquery(latest_comparison.values("pk")[:1]),
        ui_latest_direction=Subquery(latest_comparison.values("direction")[:1]),
        ui_latest_create_count=Subquery(latest_comparison.values("create_count")[:1]),
        ui_latest_update_count=Subquery(latest_comparison.values("update_count")[:1]),
        ui_latest_conflict_count=Subquery(latest_comparison.values("conflict_count")[:1]),
        ui_latest_skipped_count=Subquery(latest_comparison.values("skipped_count")[:1]),
        ui_latest_review_decision=Subquery(latest_comparison.values("final_review__decision")[:1]),
        ui_latest_apply_id=Subquery(latest_comparison.values("apply_run__pk")[:1]),
    )
    if not include_comparison:
        return queryset
    comparisons = ComparisonRun.objects.select_related(
        "final_review",
        "final_review__reviewed_by",
        "apply_run",
        "apply_run__applied_by",
    ).order_by("-created_at")
    return queryset.select_related(
        "comparison_preparation",
        "comparison_preparation__comparison",
        "comparison_preparation__comparison__final_review",
        "comparison_preparation__comparison__apply_run",
    ).prefetch_related(Prefetch("comparisons", queryset=comparisons, to_attr="ui_comparisons"))


def _preparation_status_context(
    request: HttpRequest,
    preparation: ComparisonPreparation | None,
) -> dict[str, Any]:
    job = Job.objects.filter(job_id=preparation.job_id).first() if preparation and preparation.job_id else None
    stalled = bool(
        preparation
        and preparation.state == ComparisonPreparation.State.PENDING
        and preparation.started_at is None
        and timezone.now() - preparation.updated_at > timedelta(minutes=1)
    )
    return {
        "preparation_job": job,
        "preparation_stalled": stalled,
        "preparation_job_url": (
            reverse("core:background_task", kwargs={"job_id": preparation.job_id})
            if job and request.user.has_perm("core.view_job")
            else ""
        ),
    }


def _source_action_redirect(source: DiscoverySource) -> HttpResponseRedirect:
    return HttpResponseRedirect(
        reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": source.pk}) + "#agent-actions"
    )


def _cancel_source_commands(source: DiscoverySource, summary: str) -> None:
    source.commands.filter(
        state__in=ACTIVE_COMMAND_STATES,
    ).update(
        state=AgentCommand.State.FAILED,
        completed_at=timezone.now(),
        result={"summary": summary},
    )


def _activity_events(request: HttpRequest, *, limit: int) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    if request.user.has_perm("netbox_ssot.view_collectionrun"):
        events.extend(
            {
                "occurred_at": run.received_at,
                "kind": "Collection",
                "state": run.state,
                "title": f"{run.source.name} collected {run.observation_count} records",
                "detail": f"Collection {run.state}",
                "url": reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": run.pk}),
            }
            for run in CollectionRun.objects.select_related("source")[:limit]
        )
    if request.user.has_perm("netbox_ssot.view_comparisonrun"):
        events.extend(
            _comparison_event(comparison)
            for comparison in ComparisonRun.objects.select_related("collection_run__source")[:limit]
        )
    if request.user.has_perm("netbox_ssot.view_comparisonreview"):
        events.extend(
            {
                "occurred_at": review.reviewed_at,
                "kind": "Review decision",
                "state": "complete" if review.decision == ComparisonReview.Decision.APPROVED else "attention",
                "title": (
                    f"{review.comparison.collection_run.source.name} review {review.get_decision_display().lower()}"
                ),
                "detail": f"Reviewed by {review.reviewed_by}",
                "url": reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": review.comparison_id}),
            }
            for review in ComparisonReview.objects.select_related(
                "comparison__collection_run__source",
                "reviewed_by",
            )[:limit]
        )
    if request.user.has_perm("netbox_ssot.view_applyrun"):
        events.extend(
            {
                "occurred_at": apply_run.created_at,
                "kind": "Applied",
                "state": "complete",
                "title": (
                    f"{apply_run.comparison.collection_run.source.name} applied "
                    f"{apply_run.create_count + apply_run.update_count} changes"
                ),
                "detail": f"Applied by {apply_run.applied_by}",
                "url": reverse("plugins:netbox_ssot:apply_detail", kwargs={"pk": apply_run.pk}),
            }
            for apply_run in ApplyRun.objects.select_related(
                "comparison__collection_run__source",
                "applied_by",
            )[:limit]
        )
    if request.user.has_perm("netbox_ssot.view_agentcommand"):
        events.extend(
            {
                "occurred_at": command.completed_at or command.dispatched_at or command.requested_at,
                "kind": "Agent action",
                "state": (
                    "complete"
                    if command.state == AgentCommand.State.SUCCEEDED
                    else "attention"
                    if command.state == AgentCommand.State.FAILED
                    else command.state
                ),
                "title": f"{command.source.name}: {command.get_kind_display()}",
                "detail": command.result.get("summary", command.get_state_display()),
                "url": reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": command.source_id})
                + "#agent-actions",
            }
            for command in AgentCommand.objects.select_related("source")[:limit]
        )
    if request.user.has_perm("netbox_ssot.view_agentsecurityevent"):
        events.extend(
            _agent_security_activity(event)
            for event in AgentSecurityEvent.objects.select_related("agent", "actor")[:limit]
        )
    events.sort(key=lambda event: event["occurred_at"], reverse=True)
    return tuple(events[:limit])


def _comparison_event(comparison: ComparisonRun) -> dict[str, Any]:
    change_count = comparison.create_count + comparison.update_count
    return {
        "occurred_at": comparison.created_at,
        "kind": "Review",
        "state": "attention" if comparison.conflict_count or comparison.skipped_count else "complete",
        "title": f"{comparison.collection_run.source.name} found {change_count} proposed changes",
        "detail": (
            f"{comparison.no_change_count} matched, {comparison.conflict_count} conflicts, "
            f"{comparison.skipped_count} skipped"
        ),
        "url": reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk}),
    }


def _agent_security_activity(event: AgentSecurityEvent) -> dict[str, Any]:
    agent_name = event.agent.name if event.agent else event.details.get("agent_name", "Agent")
    titles = {
        AgentSecurityEvent.Kind.ENROLLMENT_CREATED: (
            f"Enrollment created for {event.details.get('agent_name', 'agent')}"
        ),
        AgentSecurityEvent.Kind.ENROLLED: f"{agent_name} enrolled",
        AgentSecurityEvent.Kind.KEY_ROTATED: f"{agent_name} rotated its signing key",
        AgentSecurityEvent.Kind.KEYS_REVOKED: f"{agent_name} access revoked",
        AgentSecurityEvent.Kind.REPLACEMENT_CREATED: f"Replacement prepared for {agent_name}",
        AgentSecurityEvent.Kind.IDENTITY_REPLACED: f"{agent_name} identity replaced",
        AgentSecurityEvent.Kind.SOURCE_REASSIGNED: f"{event.details.get('source_name', 'Source')} reassigned",
        AgentSecurityEvent.Kind.CAPABILITIES_UPDATED: f"{agent_name} provider capabilities changed",
    }
    attention_kinds = {
        AgentSecurityEvent.Kind.KEYS_REVOKED,
        AgentSecurityEvent.Kind.REPLACEMENT_CREATED,
    }
    incompatible_sources = event.details.get("incompatible_source_ids", [])
    needs_attention = event.kind in attention_kinds or bool(incompatible_sources)
    if event.kind == AgentSecurityEvent.Kind.SOURCE_REASSIGNED and event.details.get("source_id"):
        url = reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": event.details["source_id"]})
    elif event.agent_id:
        url = reverse("plugins:netbox_ssot:agent_detail", kwargs={"pk": event.agent_id})
    else:
        url = reverse("plugins:netbox_ssot:agent_list")
    return {
        "occurred_at": event.occurred_at,
        "kind": "Agent security",
        "state": "attention" if needs_attention else "complete",
        "title": titles.get(event.kind, event.get_kind_display()),
        "detail": (
            f"{len(incompatible_sources)} assignment(s) now require reassignment"
            if incompatible_sources
            else f"{event.get_kind_display()}" + (f" by {event.actor}" if event.actor else "")
        ),
        "url": url,
    }
