from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .ingestion.signing import SignatureError, decode_public_key, public_key_fingerprint


class SynchronizationDirection(models.TextChoices):
    SOURCE_TO_TARGET = "source_to_target", "Source to target"
    TARGET_TO_SOURCE = "target_to_source", "Target to source"


class DiscoverySource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    provider_id = models.SlugField(max_length=128)
    configuration = models.JSONField(default=dict)
    datasets = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    assigned_agent = models.ForeignKey(
        "CollectorAgent",
        on_delete=models.SET_NULL,
        related_name="sources",
        null=True,
        blank=True,
    )
    collection_interval_minutes = models.PositiveIntegerField(
        default=60,
        validators=(MinValueValidator(1), MaxValueValidator(43_200)),
    )
    retention_days = models.PositiveIntegerField(
        default=30,
        validators=(MinValueValidator(1), MaxValueValidator(3_650)),
    )
    retention_successful_runs = models.PositiveIntegerField(
        default=10_000,
        validators=(MinValueValidator(1), MaxValueValidator(100_000)),
    )
    retention_failure_days = models.PositiveIntegerField(
        default=30,
        validators=(MinValueValidator(1), MaxValueValidator(3_650)),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class CollectorAgent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    public_key = models.CharField(max_length=43, unique=True)
    enabled = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    agent_version = models.CharField(max_length=64, blank=True)
    protocol_version = models.CharField(max_length=16, blank=True)
    provider_capabilities = models.JSONField(default=list)
    capabilities_reported_at = models.DateTimeField(null=True, blank=True)
    control_interval_seconds = models.PositiveSmallIntegerField(
        default=5,
        validators=(MinValueValidator(2), MaxValueValidator(30)),
    )
    reported_control_interval_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        try:
            decode_public_key(self.public_key)
        except SignatureError as exc:
            raise ValidationError({"public_key": "Enter a valid unpadded base64url Ed25519 public key."}) from exc


class AgentSigningKey(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRING = "retiring", "Retiring"
        RETIRED = "retired", "Retired"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    agent = models.ForeignKey(CollectorAgent, on_delete=models.PROTECT, related_name="signing_keys")
    public_key = models.CharField(max_length=43, unique=True)
    fingerprint = models.CharField(max_length=64, unique=True, editable=False)
    state = models.CharField(max_length=16, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    retire_after = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("agent",),
                condition=models.Q(state="active"),
                name="ssot_agent_one_active_key_uniq",
            ),
        )
        indexes = (models.Index(fields=("agent", "state"), name="ssot_agent_key_state_idx"),)

    def clean(self) -> None:
        super().clean()
        try:
            fingerprint = public_key_fingerprint(self.public_key)
        except SignatureError as exc:
            raise ValidationError({"public_key": "Enter a valid unpadded base64url Ed25519 public key."}) from exc
        if self.fingerprint and self.fingerprint != fingerprint:
            raise ValidationError({"public_key": "The signing public key is immutable."})
        self.fingerprint = fingerprint

    def save(self, *args: object, **kwargs: object) -> None:
        if not self.fingerprint:
            self.fingerprint = public_key_fingerprint(self.public_key)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.agent}: {self.fingerprint[:16]} ({self.state})"


class AgentEnrollmentToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(max_length=20, editable=False)
    agent_name = models.CharField(max_length=100)
    sources = models.ManyToManyField(DiscoverySource, related_name="agent_enrollment_tokens", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    enrolled_agent = models.ForeignKey(
        CollectorAgent,
        on_delete=models.PROTECT,
        related_name="enrollment_tokens",
        null=True,
        blank=True,
    )
    target_agent = models.ForeignKey(
        CollectorAgent,
        on_delete=models.PROTECT,
        related_name="replacement_enrollment_tokens",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = (models.Index(fields=("token_hash", "expires_at"), name="ssot_enroll_token_idx"),)
        constraints = (
            models.UniqueConstraint(
                fields=("target_agent",),
                condition=models.Q(
                    target_agent__isnull=False,
                    used_at__isnull=True,
                    revoked_at__isnull=True,
                ),
                name="ssot_agent_one_replacement_uniq",
            ),
        )

    def __str__(self) -> str:
        return f"{self.agent_name}: {self.token_prefix}"


class AgentCommand(models.Model):
    class Kind(models.TextChoices):
        TEST_CONNECTION = "test_connection", "Test connection"
        RUN_NOW = "run_now", "Run now"

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"
        RUNNING = "running", "Running"
        REPORTING = "reporting", "Reporting"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    agent = models.ForeignKey(CollectorAgent, on_delete=models.PROTECT, related_name="commands")
    source = models.ForeignKey(DiscoverySource, on_delete=models.PROTECT, related_name="commands")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    reporting_at = models.DateTimeField(null=True, blank=True)
    last_progress_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict)

    class Meta:
        ordering = ("-requested_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("source", "kind"),
                condition=models.Q(state__in=("pending", "dispatched", "running", "reporting")),
                name="ssot_command_source_kind_active_uniq",
            ),
        )
        indexes = (models.Index(fields=("agent", "state", "requested_at"), name="ssot_command_agent_state_idx"),)

    def __str__(self) -> str:
        return f"{self.source}: {self.get_kind_display()} ({self.state})"


class AppendOnlyModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args: object, **kwargs: object) -> None:
        if not self._state.adding:
            raise ValidationError("Persisted discovery evidence is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]:
        raise ValidationError("Persisted discovery evidence is append-only.")


class AgentSecurityEvent(AppendOnlyModel):
    class Kind(models.TextChoices):
        ENROLLMENT_CREATED = "enrollment_created", "Enrollment created"
        ENROLLED = "enrolled", "Enrolled"
        KEY_ROTATED = "key_rotated", "Key rotated"
        KEYS_REVOKED = "keys_revoked", "Keys revoked"
        REPLACEMENT_CREATED = "replacement_created", "Replacement created"
        IDENTITY_REPLACED = "identity_replaced", "Identity replaced"
        SOURCE_REASSIGNED = "source_reassigned", "Source reassigned"
        CAPABILITIES_UPDATED = "capabilities_updated", "Capabilities updated"

    id = models.BigAutoField(primary_key=True)
    agent = models.ForeignKey(
        CollectorAgent,
        on_delete=models.PROTECT,
        related_name="security_events",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    occurred_at = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = (models.Index(fields=("agent", "-occurred_at"), name="ssot_agent_security_idx"),)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} at {self.occurred_at}"


class CollectionRun(AppendOnlyModel):
    run_id = models.UUIDField(primary_key=True, editable=False)
    source = models.ForeignKey(DiscoverySource, on_delete=models.PROTECT, related_name="runs")
    agent = models.ForeignKey(CollectorAgent, on_delete=models.PROTECT, related_name="runs")
    provider_id = models.SlugField(max_length=128)
    provider_version = models.CharField(max_length=64)
    contract_version = models.CharField(max_length=32)
    state = models.CharField(max_length=16)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    datasets = models.JSONField(default=list)
    scope = models.JSONField(default=list)
    messages = models.JSONField(default=list)
    completeness_token = models.CharField(max_length=512, blank=True)
    payload_digest = models.CharField(max_length=64)
    observation_count = models.PositiveIntegerField()

    class Meta:
        ordering = ("-received_at",)
        indexes = (
            models.Index(fields=("source", "-completed_at"), name="ssot_run_source_done_idx"),
            models.Index(fields=("provider_id", "state"), name="ssot_run_provider_state_idx"),
        )

    def __str__(self) -> str:
        return str(self.run_id)


class StoredObservation(AppendOnlyModel):
    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey(CollectionRun, on_delete=models.PROTECT, related_name="stored_observations")
    source = models.ForeignKey(DiscoverySource, on_delete=models.PROTECT, related_name="observations")
    sequence = models.PositiveIntegerField()
    resource_kind = models.CharField(max_length=64)
    external_id = models.CharField(max_length=512)
    collected_at = models.DateTimeField()
    scope = models.JSONField(default=list)
    attributes = models.JSONField(default=list)
    relationships = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        ordering = ("run_id", "sequence")
        constraints = (
            models.UniqueConstraint(fields=("run", "sequence"), name="ssot_observation_run_sequence_uniq"),
            models.UniqueConstraint(
                fields=("run", "resource_kind", "external_id"),
                name="ssot_observation_identity_uniq",
            ),
        )
        indexes = (
            models.Index(fields=("source", "resource_kind", "external_id"), name="ssot_obs_source_identity_idx"),
        )

    def __str__(self) -> str:
        return f"{self.resource_kind}: {self.external_id}"


class ComparisonRun(AppendOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    collection_run = models.ForeignKey(CollectionRun, on_delete=models.PROTECT, related_name="comparisons")
    source_payload_digest = models.CharField(max_length=64)
    target_snapshot_digest = models.CharField(max_length=64)
    engine_version = models.CharField(max_length=32)
    direction = models.CharField(
        max_length=32,
        choices=SynchronizationDirection.choices,
        default=SynchronizationDirection.SOURCE_TO_TARGET,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    create_count = models.PositiveIntegerField(default=0)
    update_count = models.PositiveIntegerField(default=0)
    no_change_count = models.PositiveIntegerField(default=0)
    conflict_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("collection_run", "target_snapshot_digest", "engine_version", "direction"),
                name="ssot_comparison_run_target_uniq",
            ),
        )
        indexes = (models.Index(fields=("collection_run", "-created_at"), name="ssot_cmp_collection_idx"),)

    @property
    def item_count(self) -> int:
        return self.create_count + self.update_count + self.no_change_count + self.conflict_count + self.skipped_count

    def __str__(self) -> str:
        return str(self.id)


class ComparisonItem(AppendOnlyModel):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        NO_CHANGE = "no_change", "No change"
        CONFLICT = "conflict", "Conflict"
        SKIPPED = "skipped", "Skipped"

    id = models.BigAutoField(primary_key=True)
    comparison = models.ForeignKey(ComparisonRun, on_delete=models.PROTECT, related_name="items")
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=Action.choices)
    resource_kind = models.CharField(max_length=64)
    identity_key = models.CharField(max_length=1024)
    display_name = models.CharField(max_length=512)
    source_external_id = models.CharField(max_length=512)
    target_object_type = models.CharField(max_length=128, blank=True)
    target_object_id = models.CharField(max_length=128, blank=True)
    match_basis = models.CharField(max_length=64)
    reason = models.TextField(blank=True)
    source_data = models.JSONField(default=dict)
    target_data = models.JSONField(default=dict)
    changes = models.JSONField(default=list)

    class Meta:
        ordering = ("comparison_id", "sequence")
        constraints = (
            models.UniqueConstraint(fields=("comparison", "sequence"), name="ssot_comparison_item_sequence_uniq"),
        )
        indexes = (
            models.Index(fields=("comparison", "action"), name="ssot_cmp_item_action_idx"),
            models.Index(fields=("comparison", "resource_kind"), name="ssot_cmp_item_kind_idx"),
        )

    def __str__(self) -> str:
        return f"{self.resource_kind}: {self.display_name} ({self.action})"


class ReviewDecision(AppendOnlyModel):
    class Decision(models.TextChoices):
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"

    id = models.BigAutoField(primary_key=True)
    comparison = models.ForeignKey(ComparisonRun, on_delete=models.PROTECT, related_name="review_decisions")
    comparison_item = models.ForeignKey(ComparisonItem, on_delete=models.PROTECT, related_name="review_decisions")
    decision = models.CharField(max_length=16, choices=Decision.choices)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, max_length=1000)

    class Meta:
        ordering = ("comparison_id", "comparison_item_id", "decided_at", "id")
        indexes = (
            models.Index(
                fields=("comparison", "comparison_item", "-decided_at", "-id"),
                name="ssot_review_latest_idx",
            ),
        )

    def clean(self) -> None:
        super().clean()
        if self.comparison_item_id and self.comparison_id != self.comparison_item.comparison_id:
            raise ValidationError({"comparison_item": "The review item must belong to the selected comparison."})
        if self.decision == self.Decision.REJECT and not self.reason.strip():
            raise ValidationError({"reason": "A rejected record requires a reason."})

    def __str__(self) -> str:
        return f"{self.comparison_item}: {self.get_decision_display()}"


class ComparisonReview(AppendOnlyModel):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    comparison = models.OneToOneField(ComparisonRun, on_delete=models.PROTECT, related_name="final_review")
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, max_length=1000)
    decision_digest = models.CharField(max_length=64, editable=False)
    approved_count = models.PositiveIntegerField(default=0)
    rejected_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-reviewed_at",)
        indexes = (models.Index(fields=("decision", "-reviewed_at"), name="ssot_review_state_idx"),)

    def clean(self) -> None:
        super().clean()
        if self.decision == self.Decision.REJECTED and not self.reason.strip():
            raise ValidationError({"reason": "A rejected review requires a reason."})

    def __str__(self) -> str:
        return f"{self.comparison}: {self.get_decision_display()}"


class ApplyRun(AppendOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    comparison = models.OneToOneField(ComparisonRun, on_delete=models.PROTECT, related_name="apply_run")
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    direction = models.CharField(
        max_length=32,
        choices=SynchronizationDirection.choices,
        default=SynchronizationDirection.SOURCE_TO_TARGET,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    create_count = models.PositiveIntegerField(default=0)
    update_count = models.PositiveIntegerField(default=0)
    no_change_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-created_at",)

    @property
    def item_count(self) -> int:
        return self.create_count + self.update_count + self.no_change_count

    def __str__(self) -> str:
        return str(self.id)


class ApplyItem(AppendOnlyModel):
    id = models.BigAutoField(primary_key=True)
    apply_run = models.ForeignKey(ApplyRun, on_delete=models.PROTECT, related_name="items")
    comparison_item = models.OneToOneField(ComparisonItem, on_delete=models.PROTECT, related_name="apply_item")
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=ComparisonItem.Action.choices)
    resource_kind = models.CharField(max_length=64)
    source_external_id = models.CharField(max_length=512)
    target_object_type = models.CharField(max_length=128)
    target_object_id = models.CharField(max_length=128)

    class Meta:
        ordering = ("apply_run_id", "sequence")
        constraints = (models.UniqueConstraint(fields=("apply_run", "sequence"), name="ssot_apply_item_sequence_uniq"),)
        indexes = (
            models.Index(fields=("apply_run", "action"), name="ssot_apply_item_action_idx"),
            models.Index(fields=("apply_run", "resource_kind"), name="ssot_apply_item_kind_idx"),
        )

    def __str__(self) -> str:
        return f"{self.resource_kind}: {self.source_external_id} ({self.action})"


class ObjectBinding(models.Model):
    id = models.BigAutoField(primary_key=True)
    source = models.ForeignKey(DiscoverySource, on_delete=models.PROTECT, related_name="object_bindings")
    resource_kind = models.CharField(max_length=64)
    source_external_id = models.CharField(max_length=512)
    identity_key = models.CharField(max_length=1024)
    target_object_type = models.CharField(max_length=128)
    target_object_id = models.CharField(max_length=128)
    source_fingerprint = models.CharField(max_length=64)
    last_applied_run = models.ForeignKey(ApplyRun, on_delete=models.PROTECT, related_name="bindings")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source_id", "resource_kind", "source_external_id")
        constraints = (
            models.UniqueConstraint(
                fields=("source", "resource_kind", "source_external_id"),
                name="ssot_binding_source_object_uniq",
            ),
        )
        indexes = (
            models.Index(
                fields=("source", "target_object_type", "target_object_id"),
                name="ssot_binding_target_idx",
            ),
        )

    def __str__(self) -> str:
        return f"{self.source}: {self.resource_kind}:{self.source_external_id}"
