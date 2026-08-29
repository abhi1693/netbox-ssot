from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, models, transaction
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Cast
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from netbox_ssot_contracts import (
    CURRENT_AGENT_PROTOCOL_VERSION,
    FieldWidget,
    SchemaContractError,
    redact_configuration,
    selected_dataset_ids,
    validate_configuration,
)

from .agent_capabilities import agent_capability_rows, source_capability_issue
from .agent_security import (
    AgentSecurityError,
    create_enrollment,
    create_replacement_enrollment,
    revoke_agent_keys,
)
from .application.service import ApplicationRejectedError, apply_comparison, inspect_application
from .collection_policy import agent_collection_policy_issue
from .comparison_presentation import comparison_field_rows
from .destination import selected_data_model_mappings
from .health import agent_health, source_health
from .models import (
    AgentCommand,
    AgentSecurityEvent,
    ApplyRun,
    CollectionRun,
    CollectorAgent,
    ComparisonItem,
    ComparisonReview,
    ComparisonRun,
    DiscoverySource,
    StoredObservation,
)
from .planning.service import ComparisonRejectedError, create_comparison
from .providers import ProviderNotFoundError, ProviderRegistry, build_provider_card, build_provider_wizard
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

ACTIVE_COMMAND_STATES = (
    AgentCommand.State.PENDING,
    AgentCommand.State.DISPATCHED,
    AgentCommand.State.RUNNING,
    AgentCommand.State.REPORTING,
)


class OverviewView(LoginRequiredMixin, View):
    template_name = "netbox_ssot/overview.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        can_view_sources = request.user.has_perm("netbox_ssot.view_discoverysource")
        can_view_agents = request.user.has_perm("netbox_ssot.view_collectoragent")
        can_view_comparisons = request.user.has_perm("netbox_ssot.view_comparisonrun")
        sources = _source_queryset() if can_view_sources else DiscoverySource.objects.none()
        pending_reviews = (
            ComparisonRun.objects.filter(apply_run__isnull=True, final_review__isnull=True)
            .exclude(create_count=0, update_count=0, conflict_count=0, skipped_count=0)
            .count()
            if can_view_comparisons
            else None
        )
        return render(
            request,
            self.template_name,
            {
                "source_rows": tuple((source, source_health(source)) for source in sources),
                "source_count": sources.count() if can_view_sources else None,
                "can_view_sources": can_view_sources,
                "enabled_agent_count": CollectorAgent.objects.filter(enabled=True).count() if can_view_agents else None,
                "pending_review_count": pending_reviews,
                "events": _activity_events(request, limit=8),
            },
        )


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


class SourceListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_discoverysource"
    template_name = "netbox_ssot/source_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        sources = _source_queryset()
        return render(
            request,
            self.template_name,
            {"source_rows": tuple((source, source_health(source)) for source in sources)},
        )


class SourceDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_discoverysource"
    template_name = "netbox_ssot/source_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        source = get_object_or_404(_source_queryset(), pk=pk)
        recent_runs = CollectionRun.objects.filter(source=source).select_related("agent")[:10]
        recent_commands = tuple(source.commands.select_related("requested_by")[:10])
        if request.user.has_perm("netbox_ssot.view_collectionrun"):
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
        provider_name = source.provider_id
        source_icon_class = "mdi mdi-database-outline"
        data_mappings = ()
        try:
            descriptor = ProviderRegistry().get(source.provider_id)
            manifest = descriptor.manifest
            fields = build_provider_wizard(manifest).fields
            safe_configuration = redact_configuration(fields, source.configuration)
            provider_name = manifest.display_name
            source_icon_class = manifest.icon_class
            data_mappings = selected_data_model_mappings(manifest, source.datasets, source.configuration)
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
                "safe_configuration": json.dumps(safe_configuration, indent=2, sort_keys=True),
                "collection_request": json.dumps(collection_request, indent=2, sort_keys=True),
                "recent_runs": recent_runs,
                "recent_commands": recent_commands,
                "health": source_health(source),
                "retention": retention_plan(source),
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
            "dataset_fields": tuple(
                dataset.model_dump(mode="json") for dataset in descriptor.manifest.datasets if dataset.selectable
            ),
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


class AgentListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectoragent"
    template_name = "netbox_ssot/agent_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        control_endpoint = request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-config"))
        return render(
            request,
            self.template_name,
            {
                "agent_rows": tuple(
                    (agent, agent_health(agent))
                    for agent in CollectorAgent.objects.prefetch_related("sources", "signing_keys")
                ),
                "control_endpoint": control_endpoint,
                "control_endpoint_is_insecure": control_endpoint.startswith("http://"),
            },
        )


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
        return redirect("plugins:netbox_ssot:agent_list")

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
        return redirect("plugins:netbox_ssot:agent_edit", pk=agent.pk)


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


class RunListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectionrun"
    template_name = "netbox_ssot/run_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        runs = CollectionRun.objects.select_related("source", "agent")[:200]
        return render(request, self.template_name, {"runs": runs})


class RunDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_collectionrun"
    template_name = "netbox_ssot/run_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        run = get_object_or_404(CollectionRun.objects.select_related("source", "agent"), pk=pk)
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
        return render(
            request,
            self.template_name,
            {
                "run": run,
                "provider_name": provider_name,
                "page": page,
                "model_count": model_count,
                "filtered_model_count": len(count_rows),
                "query": query,
                "sort_options": tuple((value, label) for value, (label, _) in sort_options.items()),
                "selected_sort": selected_sort,
                "duration_seconds": duration_seconds,
                "comparisons": run.comparisons.all()[:20],
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
            outcome = create_comparison(run)
        except ComparisonRejectedError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:netbox_ssot:run_detail", pk=run.pk)
        if outcome.created:
            messages.success(request, "Review ready. No NetBox records were changed.")
        else:
            messages.info(request, "This collection has already been reviewed against the current local data.")
        return redirect("plugins:netbox_ssot:comparison_detail", pk=outcome.comparison.pk)


class ComparisonListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_comparisonrun"
    template_name = "netbox_ssot/comparison_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        comparisons = ComparisonRun.objects.select_related(
            "collection_run",
            "collection_run__source",
            "final_review",
            "apply_run",
        )[:200]
        return render(request, self.template_name, {"comparisons": comparisons})


class ComparisonDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_comparisonrun"
    template_name = "netbox_ssot/comparison_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        comparison = get_object_or_404(
            ComparisonRun.objects.select_related("collection_run", "collection_run__source"),
            pk=pk,
        )
        items = comparison.items.all()
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
        readiness = None if application else inspect_application(comparison, request.user)
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
                "readiness": readiness,
                "has_changes": bool(comparison.create_count or comparison.update_count),
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
                approve_all_review_items(comparison, request.user)
                finalize_review(comparison, ComparisonReview.Decision.APPROVED, request.user)
                messages.success(request, "The comparison review was approved and finalized.")
            elif action == "finalize_approval":
                finalize_review(comparison, ComparisonReview.Decision.APPROVED, request.user)
                messages.success(request, "The comparison review was approved and finalized.")
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
        try:
            outcome = apply_comparison(comparison, request.user)
        except ApplicationRejectedError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:netbox_ssot:comparison_detail", pk=comparison.pk)
        if outcome.created:
            messages.success(request, "The reviewed comparison was applied in one transaction.")
        else:
            messages.info(request, "This comparison was already applied; no target objects were changed.")
        return redirect("plugins:netbox_ssot:apply_detail", pk=outcome.apply_run.pk)


class ApplyListView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_applyrun"
    template_name = "netbox_ssot/apply_list.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        apply_runs = ApplyRun.objects.select_related(
            "comparison",
            "comparison__collection_run",
            "comparison__collection_run__source",
            "applied_by",
        )[:200]
        return render(request, self.template_name, {"apply_runs": apply_runs})


class ApplyDetailView(PermissionRequiredMixin, View):
    permission_required = "netbox_ssot.view_applyrun"
    template_name = "netbox_ssot/apply_detail.html"

    def get(self, request: HttpRequest, pk: object) -> HttpResponse:
        apply_run = get_object_or_404(
            ApplyRun.objects.select_related(
                "comparison",
                "comparison__collection_run",
                "comparison__collection_run__source",
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
            {"apply_run": apply_run, "page": page, "record_rows": record_rows},
        )


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
    for path in ("/name", "/asn", "/model", "/address", "/prefix", "/slug"):
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
    model_permissions = {
        "tag": ("extras", "tag"),
        "owner_group": ("users", "ownergroup"),
        "owner": ("users", "owner"),
        "tenant_group": ("tenancy", "tenantgroup"),
        "tenant": ("tenancy", "tenant"),
        "site_group": ("dcim", "sitegroup"),
        "rir": ("ipam", "rir"),
        "asn": ("ipam", "asn"),
        "region": ("dcim", "region"),
        "site": ("dcim", "site"),
        "location": ("dcim", "location"),
    }
    for item in comparison.items.filter(action__in=(ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE)):
        permission_target = model_permissions.get(item.resource_kind)
        if permission_target is None:
            raise PermissionDenied("The comparison contains a target model outside the supported apply scope.")
        app_label, model_name = permission_target
        operation = "add" if item.action == ComparisonItem.Action.CREATE else "change"
        permissions.add(f"{app_label}.{operation}_{model_name}")
    return tuple(sorted(permissions))


def _source_queryset() -> models.QuerySet[DiscoverySource]:
    latest_runs = CollectionRun.objects.filter(source=OuterRef("pk")).order_by("-received_at")
    return DiscoverySource.objects.select_related("assigned_agent").annotate(
        latest_collection_at=Subquery(latest_runs.values("received_at")[:1]),
        latest_collection_state=Subquery(latest_runs.values("state")[:1]),
        last_success_at=models.Max("runs__received_at", filter=models.Q(runs__state="complete")),
    )


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
                    f"{review.comparison.collection_run.source.name} review "
                    f"{review.get_decision_display().lower()}"
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
        url = reverse("plugins:netbox_ssot:agent_edit", kwargs={"pk": event.agent_id})
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
