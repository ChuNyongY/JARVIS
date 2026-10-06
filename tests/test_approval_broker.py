from uuid import uuid4

from pydantic import SecretStr

from javis_core import (
    Action,
    ApprovalChannel,
    ApprovalCoordinator,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalTokenService,
    Plan,
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    default_registry,
)


class Presenter:
    def __init__(
        self,
        channel: ApprovalChannel,
        *,
        approved: bool = True,
        wrong_request: bool = False,
    ) -> None:
        self.channel = channel
        self.approved = approved
        self.wrong_request = wrong_request
        self.requests: list[ApprovalRequest] = []

    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(approval)
        return ApprovalResponse(
            request_id=uuid4() if self.wrong_request else approval.request_id,
            approved=self.approved,
            channel=self.channel,
            authentication_proof=SecretStr("proof"),
        )


class Authenticator:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self.calls = 0

    def verify(
        self,
        approval: ApprovalRequest,
        response: ApprovalResponse,
    ) -> bool:
        self.calls += 1
        return self.accepted


def decision(action: Action) -> PolicyDecision:
    return PolicyEngine(default_registry()).evaluate(
        action,
        PolicyContext(
            addressed_confidence=1,
            intent_confidence=1,
            target_confidence=1,
            target_matches_plan=True,
        ),
    )


def test_yellow_can_be_approved_by_explicit_voice_response() -> None:
    action = Action(
        capability_id="file.move",
        arguments={"source": "a", "destination": "b"},
        purpose="move",
        target_identity="a",
    )
    plan = Plan(goal="move", steps=(action,))
    presenter = Presenter(ApprovalChannel.VOICE)
    coordinator = ApprovalCoordinator(presenter, ApprovalTokenService(b"i" * 32))

    assert coordinator.approve_yellow(plan, action, decision(action))


def test_red_rejects_voice_only_approval() -> None:
    action = Action(
        capability_id="message.send",
        purpose="send",
        target_identity="recipient",
    )
    plan = Plan(goal="send", steps=(action,))
    coordinator = ApprovalCoordinator(
        Presenter(ApprovalChannel.VOICE),
        ApprovalTokenService(b"j" * 32),
    )

    assert coordinator.approve_red(plan, action, decision(action)) is None


def test_r4_requires_successful_strong_authentication() -> None:
    action = Action(
        capability_id="system.reset.commit",
        purpose="reset",
        target_identity="local-machine",
    )
    plan = Plan(goal="reset", steps=(action,))
    denied_auth = Authenticator(False)
    denied = ApprovalCoordinator(
        Presenter(ApprovalChannel.LOCAL_UI),
        ApprovalTokenService(b"k" * 32),
        denied_auth,
    )
    assert denied.approve_red(plan, action, decision(action)) is None
    assert denied_auth.calls == 1

    accepted_auth = Authenticator(True)
    accepted = ApprovalCoordinator(
        Presenter(ApprovalChannel.LOCAL_UI),
        ApprovalTokenService(b"l" * 32),
        accepted_auth,
    )
    artifact = accepted.approve_red(plan, action, decision(action))
    assert artifact is not None
    assert artifact.strong_auth_completed


def test_response_is_bound_to_request_id() -> None:
    action = Action(capability_id="file.move", purpose="move")
    plan = Plan(goal="move", steps=(action,))
    coordinator = ApprovalCoordinator(
        Presenter(ApprovalChannel.LOCAL_UI, wrong_request=True),
        ApprovalTokenService(b"m" * 32),
    )

    assert not coordinator.approve_yellow(plan, action, decision(action))
