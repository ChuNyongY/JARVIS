from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class RiskLevel(IntEnum):
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4


class Decision(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    DENY = "DENY"


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Postcondition(FrozenModel):
    predicate: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class Action(FrozenModel):
    action_id: UUID = Field(default_factory=uuid4)
    capability_id: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    purpose: str = Field(min_length=1)
    expected_postconditions: tuple[Postcondition, ...] = Field(default_factory=tuple)
    target_identity: str | None = None


class Plan(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID = Field(default_factory=uuid4)
    goal: str = Field(min_length=1)
    steps: tuple[Action, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_action_ids(self) -> Plan:
        action_ids = [step.action_id for step in self.steps]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action_id must be unique within a plan")
        return self


ExecutorKind = Literal["native", "powershell", "uia", "vision"]


class CapabilitySpec(FrozenModel):
    capability_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    risk_floor: RiskLevel
    allowed_executors: tuple[ExecutorKind, ...] = Field(min_length=1)
    reversible: bool
    external_effect: bool
    sensitive: bool


class PolicyContext(FrozenModel):
    addressed_confidence: float = Field(ge=0.0, le=1.0)
    intent_confidence: float = Field(ge=0.0, le=1.0)
    target_confidence: float = Field(ge=0.0, le=1.0)
    requested_risk: RiskLevel | None = None
    privilege_escalation: bool = False
    external_transmission: bool = False
    sensitive_data: bool = False
    suspected_prompt_injection: bool = False
    target_matches_plan: bool = False


class PolicyDecision(FrozenModel):
    decision: Decision
    risk: RiskLevel
    reasons: tuple[str, ...] = Field(default_factory=tuple)
    requires_strong_auth: bool = False


class ApprovalClaims(FrozenModel):
    task_id: UUID
    action_id: UUID
    capability_id: str
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_identity: str | None
    risk: RiskLevel
    issued_at: datetime
    expires_at: datetime
    nonce: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def validate_times(self) -> ApprovalClaims:
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("approval times must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


class VerificationResult(FrozenModel):
    success: bool
    observed: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = Field(
        min_length=2,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    recoverable: bool = False


def canonical_arguments_hash(arguments: dict[str, JsonValue]) -> str:
    try:
        canonical = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("arguments must contain only JSON values") from exc
    return hashlib.sha256(canonical).hexdigest()
