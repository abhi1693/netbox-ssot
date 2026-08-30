from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "netbox_ssot"

urlpatterns = (
    path(
        "",
        RedirectView.as_view(pattern_name="plugins:netbox_ssot:overview", permanent=False),
        name="root",
    ),
    path("overview/", views.OverviewView.as_view(), name="overview"),
    path("overview/summary/", views.SummaryView.as_view(), name="summary"),
    path("activity/", views.ActivityView.as_view(), name="activity"),
    path("providers/", views.ProviderCatalogView.as_view(), name="provider_list"),
    path("sources/", views.SourceListView.as_view(), name="source_list"),
    path("sources/add/<str:provider_id>/", views.SourceWizardView.as_view(), name="source_wizard"),
    path("sources/<uuid:pk>/", views.SourceDetailView.as_view(), name="source_detail"),
    path("sources/<uuid:pk>/status/", views.SourceStatusView.as_view(), name="source_status"),
    path("sources/<uuid:pk>/commands/", views.SourceCommandsView.as_view(), name="source_commands"),
    path("sources/<uuid:pk>/data/", views.SourceDataView.as_view(), name="source_data"),
    path("sources/<uuid:pk>/history/", views.SourceHistoryView.as_view(), name="source_history"),
    path("sources/<uuid:pk>/delete/", views.SourceDeleteView.as_view(), name="source_delete"),
    path(
        "sources/<uuid:pk>/datasets/<slug:dataset_id>/",
        views.SourceDatasetDetailView.as_view(),
        name="source_dataset_detail",
    ),
    path("sources/<uuid:pk>/edit/", views.SourceEditView.as_view(), name="source_edit"),
    path(
        "sources/<uuid:pk>/test-connection/",
        views.SourceTestConnectionView.as_view(),
        name="source_test_connection",
    ),
    path("sources/<uuid:pk>/run-now/", views.SourceRunNowView.as_view(), name="source_run_now"),
    path("agents/", views.AgentListView.as_view(), name="agent_list"),
    path("agents/add/", views.AgentEnrollmentView.as_view(), name="agent_add"),
    path(
        "agents/enrollments/<uuid:pk>/status/",
        views.AgentEnrollmentStatusView.as_view(),
        name="agent_enrollment_status",
    ),
    path("agents/<uuid:pk>/", views.AgentDetailView.as_view(), name="agent_detail"),
    path("agents/<uuid:pk>/edit/", views.AgentEditView.as_view(), name="agent_edit"),
    path("agents/<uuid:pk>/delete/", views.AgentDeleteView.as_view(), name="agent_delete"),
    path("agents/<uuid:pk>/replace/", views.AgentReplaceView.as_view(), name="agent_replace"),
    path("agents/<uuid:pk>/revoke-keys/", views.AgentRevokeKeysView.as_view(), name="agent_revoke_keys"),
    path("reconciliations/", views.ReconciliationListView.as_view(), name="reconciliation_list"),
    path("runs/", views.RunListView.as_view(), name="run_list"),
    path("runs/<uuid:pk>/", views.RunDetailView.as_view(), name="run_detail"),
    path("runs/<uuid:pk>/status/", views.RunStatusView.as_view(), name="run_status"),
    path(
        "runs/<uuid:pk>/models/<slug:resource_kind>/",
        views.ObservationListView.as_view(),
        name="observation_list",
    ),
    path(
        "runs/<uuid:run_pk>/observations/<int:pk>/",
        views.ObservationDetailView.as_view(),
        name="observation_detail",
    ),
    path("runs/<uuid:pk>/compare/", views.ComparisonCreateView.as_view(), name="comparison_add"),
    path("comparisons/", views.ComparisonListView.as_view(), name="comparison_list"),
    path("comparisons/<uuid:pk>/", views.ComparisonDetailView.as_view(), name="comparison_detail"),
    path("comparisons/<uuid:pk>/review/", views.ComparisonReviewActionView.as_view(), name="comparison_review"),
    path("comparisons/<uuid:pk>/apply/", views.ApplyCreateView.as_view(), name="apply_add"),
    path(
        "comparisons/<uuid:pk>/apply-status/",
        views.ApplyStatusView.as_view(),
        name="apply_status",
    ),
    path(
        "comparisons/<uuid:comparison_pk>/items/<int:pk>/",
        views.ComparisonItemDetailView.as_view(),
        name="comparison_item_detail",
    ),
    path(
        "comparisons/<uuid:comparison_pk>/items/<int:pk>/decide/",
        views.ComparisonItemDecisionView.as_view(),
        name="comparison_item_decide",
    ),
    path("applications/", views.ApplyListView.as_view(), name="apply_list"),
    path("applications/<uuid:pk>/", views.ApplyDetailView.as_view(), name="apply_detail"),
)
