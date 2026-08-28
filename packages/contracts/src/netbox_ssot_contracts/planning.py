from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from .base import AttributeValue, ContractModel, Identifier, JsonPointer
from .manifest import ResourceKind
from .observation import Evidence


class MatchKind(StrEnum):
    SOURCE_BINDING = "source_binding"
    EXACT_IDENTITY = "exact_identity"
    MANUAL = "manual"
    FUZZY_SUGGESTION = "fuzzy_suggestion"


class ChangeAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLYING = "applying"
    APPLIED = "applied"
    STALE = "stale"
    FAILED = "failed"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class SafetyPolicy(ContractModel):
    skip_unmatched_destination: bool = True
    allow_hard_delete: bool = False
    minimum_complete_snapshots_for_absence: int = Field(default=2, ge=2)
    absence_grace_period: timedelta = Field(default=timedelta(days=7), gt=timedelta(0))
    require_current_target_fingerprint: bool = True
    allow_automatic_fuzzy_match: bool = False

    @model_validator(mode="after")
    def enforce_baseline(self) -> SafetyPolicy:
        if not self.skip_unmatched_destination:
            raise ValueError("destination-only records must be skipped in the v1 safety contract")
        if self.allow_hard_delete:
            raise ValueError("hard deletion is not supported by the v1 safety contract")
        if self.allow_automatic_fuzzy_match:
            raise ValueError("automatic fuzzy identity is not supported by the v1 safety contract")
        return self


class MatchEvidence(ContractModel):
    kind: MatchKind
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def fuzzy_is_never_authoritative(self) -> MatchEvidence:
        if self.kind is MatchKind.FUZZY_SUGGESTION and self.confidence >= 1:
            raise ValueError("fuzzy suggestions cannot have authoritative confidence")
        return self


class FieldChange(ContractModel):
    path: JsonPointer
    before: AttributeValue = None
    after: AttributeValue = None
    owner: Identifier

    @model_validator(mode="after")
    def must_change(self) -> FieldChange:
        if self.before == self.after:
            raise ValueError("field change before and after values must differ")
        return self


class ChangeProposal(ContractModel):
    change_id: UUID
    action: ChangeAction
    resource_kind: ResourceKind
    external_id: str = Field(min_length=1, max_length=512)
    target_object_id: str | None = Field(default=None, max_length=128)
    target_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    match: MatchEvidence | None = None
    fields: tuple[FieldChange, ...] = ()
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    depends_on: tuple[UUID, ...] = ()
    destructive: bool = False
    automatic: bool = False

    @model_validator(mode="after")
    def enforce_action_shape(self) -> ChangeProposal:
        if self.action is ChangeAction.CREATE and self.target_object_id is not None:
            raise ValueError("create proposals cannot identify an existing target object")
        if self.action in {ChangeAction.UPDATE, ChangeAction.DELETE} and self.target_object_id is None:
            raise ValueError("update and delete proposals require a target object")
        if self.action is ChangeAction.UPDATE and not self.fields:
            raise ValueError("update proposals require at least one field change")
        if self.action is ChangeAction.DELETE and not self.destructive:
            raise ValueError("delete proposals must be explicitly marked destructive")
        if self.match and self.match.kind is MatchKind.FUZZY_SUGGESTION and self.automatic:
            raise ValueError("fuzzy suggestions cannot produce automatic changes")
        return self


class ReconciliationPlan(ContractModel):
    plan_id: UUID
    source_id: UUID
    run_ids: tuple[UUID, ...] = Field(min_length=1)
    created_at: datetime
    status: PlanStatus = PlanStatus.DRAFT
    target_snapshot_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: SafetyPolicy = Field(default_factory=SafetyPolicy)
    changes: tuple[ChangeProposal, ...]

    @model_validator(mode="after")
    def validate_plan(self) -> ReconciliationPlan:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        change_ids = {change.change_id for change in self.changes}
        if len(change_ids) != len(self.changes):
            raise ValueError("change IDs must be unique")
        for change in self.changes:
            unknown = set(change.depends_on) - change_ids
            if unknown:
                raise ValueError(f"change {change.change_id} has unknown dependencies")
            if change.change_id in change.depends_on:
                raise ValueError(f"change {change.change_id} cannot depend on itself")
            if change.action is ChangeAction.DELETE:
                raise ValueError("v1 plans cannot contain hard-delete proposals")
        return self


class PlanDecision(ContractModel):
    plan_id: UUID
    change_id: UUID
    decision: DecisionKind
    decided_by: str = Field(min_length=1, max_length=150)
    decided_at: datetime
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def require_timezone(self) -> PlanDecision:
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("decided_at must include a timezone")
        return self
