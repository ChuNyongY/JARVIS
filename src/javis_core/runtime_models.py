from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, JsonValue, model_validator

from .models import ActionStatus, FrozenModel

ExecutorName = Literal["native", "powershell", "uia", "vision", "mock"]
AuditEventType = Literal[
    "POLICY_DECISION",
    "APPROVAL_VERIFIED",
    "EXECUTION_STARTED",
    "EXECUTION_FINISHED",
    "VERIFICATION_FINISHED",
    "RETRY_SCHEDULED",
    "REPLAN_SCHEDULED",
    "TASK_FINISHED",
    "TASK_ABORTED",
]


class ExecutionResult(FrozenModel):
    action_id: UUID
    executor: ExecutorName
    success: bool
    observed: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    error_message: str | None = Field(default=None, max_length=512, repr=False)
    retry_safe: bool = False
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_error(self) -> ExecutionResult:
        if self.success and self.error_code is not None:
            raise ValueError("successful execution cannot have error_code")
        if not self.success and not self.error_code:
            raise ValueError("failed execution must have error_code")
        return self


class AuditEvent(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    task_id: UUID
    action_id: UUID | None = None
    event_type: AuditEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamp(self) -> AuditEvent:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return self


class RetryBudget(FrozenModel):
    max_attempts: int = Field(default=2, ge=1, le=5)
    max_replans: int = Field(default=1, ge=0, le=3)


class TaskOutcome(FrozenModel):
    task_id: UUID
    status: ActionStatus
    completed_actions: int = Field(ge=0)
    reason: str = Field(
        min_length=2,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )


class ApprovalArtifact(FrozenModel):
    token: str = Field(min_length=1, repr=False)
    strong_auth_completed: bool = False
