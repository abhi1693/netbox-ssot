from django.urls import path

from .views import (
    AgentCommandResultView,
    AgentCommandStatusView,
    AgentConfigurationView,
    AgentEnrollmentAPIView,
    AgentKeyRotationAPIView,
    BatchIngestView,
)

app_name = "netbox_ssot"

urlpatterns = (
    path("agent/enroll/", AgentEnrollmentAPIView.as_view(), name="agent-enroll"),
    path("agent/keys/rotate/", AgentKeyRotationAPIView.as_view(), name="agent-key-rotate"),
    path("agent/config/", AgentConfigurationView.as_view(), name="agent-config"),
    path("agent/commands/results/", AgentCommandResultView.as_view(), name="agent-command-result"),
    path("agent/commands/status/", AgentCommandStatusView.as_view(), name="agent-command-status"),
    path("ingest/batches/", BatchIngestView.as_view(), name="batch-ingest"),
)
