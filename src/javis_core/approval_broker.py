from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, SecretStr

from .approval import ApprovalTokenService
from .models import (
    Action,
    FrozenModel,
    Plan,
    PolicyDecision,
    RiskLevel,
    canonical_arguments_hash,
)
from .runtime_models import ApprovalArtifact


class ApprovalChannel(StrEnum):
    VOICE = "VOICE"
    LOCAL_UI = "LOCAL_UI"


class ApprovalRequest(FrozenModel):
    request_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    action_id: UUID
    capability_id: str
    arguments: dict[str, JsonValue]
    arguments_hash: str
    target_identity: str | None
    risk: RiskLevel
    purpose: str
    strong_auth_required: bool


class ApprovalResponse(FrozenModel):
    request_id: UUID
    approved: bool
    channel: ApprovalChannel
    authentication_proof: SecretStr | None = Field(default=None, repr=False)


class ApprovalPresenter(Protocol):
    def request(self, approval: ApprovalRequest) -> ApprovalResponse: ...


class StrongAuthenticator(Protocol):
    def verify(
        self,
        approval: ApprovalRequest,
        response: ApprovalResponse,
    ) -> bool: ...


class DenyStrongAuthenticator:
    def verify(
        self,
        approval: ApprovalRequest,
        response: ApprovalResponse,
    ) -> bool:
        return False


class ApprovalCoordinator:
    """ApprovalBroker implementation that keeps UI/authentication outside the planner."""

    def __init__(
        self,
        presenter: ApprovalPresenter,
        token_service: ApprovalTokenService,
        authenticator: StrongAuthenticator | None = None,
    ) -> None:
        self._presenter = presenter
        self._tokens = token_service
        self._authenticator = authenticator or DenyStrongAuthenticator()

    def approve_yellow(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> bool:
        request = self._build_request(plan, action, decision)
        response = self._presenter.request(request)
        return response.request_id == request.request_id and response.approved

    def approve_red(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalArtifact | None:
        request = self._build_request(plan, action, decision)
        response = self._presenter.request(request)
        if (
            response.request_id != request.request_id
            or not response.approved
            or response.channel is not ApprovalChannel.LOCAL_UI
        ):
            return None
        strong_auth_completed = False
        if decision.requires_strong_auth:
            strong_auth_completed = self._authenticator.verify(request, response)
            if not strong_auth_completed:
                return None
        return ApprovalArtifact(
            token=self._tokens.issue(plan, action, decision),
            strong_auth_completed=strong_auth_completed,
        )

    @staticmethod
    def _build_request(
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalRequest:
        return ApprovalRequest(
            task_id=plan.task_id,
            action_id=action.action_id,
            capability_id=action.capability_id,
            arguments=action.arguments,
            arguments_hash=canonical_arguments_hash(action.arguments),
            target_identity=action.target_identity,
            risk=decision.risk,
            purpose=action.purpose,
            strong_auth_required=decision.requires_strong_auth,
        )
