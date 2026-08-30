from __future__ import annotations

from django import forms
from netbox.forms import NetBoxModelFilterSetForm
from utilities.forms import BOOLEAN_WITH_BLANK_CHOICES
from utilities.forms.rendering import FieldSet

from .models import CollectionRun, CollectorAgent, DiscoverySource, SynchronizationDirection
from .ui import RECONCILIATION_STATES


class SourceFilterForm(NetBoxModelFilterSetForm):
    model = DiscoverySource
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("provider_id", "enabled", "assigned_agent", name="Source"),
    )
    provider_id = forms.CharField(label="Provider", required=False)
    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    assigned_agent = forms.ModelMultipleChoiceField(queryset=CollectorAgent.objects.all(), required=False)


class AgentFilterForm(NetBoxModelFilterSetForm):
    model = CollectorAgent
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("enabled", "agent_version", "protocol_version", name="Runtime"),
    )
    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    agent_version = forms.CharField(required=False)
    protocol_version = forms.CharField(required=False)


class ReconciliationFilterForm(NetBoxModelFilterSetForm):
    model = CollectionRun
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("workflow_state", "source", "period", "direction", name="Workflow"),
        FieldSet("state", "provider_id", "agent", name="Collection evidence"),
    )
    workflow_state = forms.ChoiceField(
        choices=(("", "All workflow states"), *RECONCILIATION_STATES),
        required=False,
    )
    source = forms.ModelMultipleChoiceField(queryset=DiscoverySource.objects.all(), required=False)
    period = forms.ChoiceField(
        choices=(("", "All time"), ("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days")),
        required=False,
    )
    direction = forms.ChoiceField(choices=(("", "---------"), *SynchronizationDirection.choices), required=False)
    state = forms.CharField(label="Collection state", required=False)
    provider_id = forms.CharField(label="Provider", required=False)
    agent = forms.ModelMultipleChoiceField(queryset=CollectorAgent.objects.all(), required=False)


__all__ = ["AgentFilterForm", "ReconciliationFilterForm", "SourceFilterForm"]
