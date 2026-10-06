from datetime import UTC, datetime, timedelta

import pytest

from javis_core import (
    Action,
    ApprovalError,
    ApprovalTokenService,
    Plan,
    PolicyContext,
    PolicyEngine,
    default_registry,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def red_action() -> tuple[Plan, Action, object]:
    action = Action(
        capability_id="system.reset.commit",
        arguments={"keep_files": True},
        purpose="test reset boundary",
        target_identity="local-machine",
    )
    plan = Plan(goal="test", steps=(action,))
    decision = PolicyEngine(default_registry()).evaluate(
        action,
        PolicyContext(
            addressed_confidence=1.0,
            intent_confidence=1.0,
            target_confidence=1.0,
            target_matches_plan=True,
        ),
    )
    return plan, action, decision


def service(clock: Clock) -> ApprovalTokenService:
    return ApprovalTokenService(b"a" * 32, ttl=timedelta(seconds=30), now=clock)


def test_approval_round_trip_and_replay_block() -> None:
    clock = Clock()
    plan, action, decision = red_action()
    approvals = service(clock)
    token = approvals.issue(plan, action, decision)  # type: ignore[arg-type]
    claims = approvals.verify(token, plan, action, decision)  # type: ignore[arg-type]
    assert claims.action_id == action.action_id
    with pytest.raises(ApprovalError, match="already used"):
        approvals.verify(token, plan, action, decision)  # type: ignore[arg-type]


def test_changed_arguments_invalidate_approval() -> None:
    clock = Clock()
    plan, action, decision = red_action()
    approvals = service(clock)
    token = approvals.issue(plan, action, decision)  # type: ignore[arg-type]
    changed = action.model_copy(update={"arguments": {"keep_files": False}})
    changed_plan = plan.model_copy(update={"steps": (changed,)})
    with pytest.raises(ApprovalError, match="does not match"):
        approvals.verify(token, changed_plan, changed, decision)  # type: ignore[arg-type]


def test_changed_target_invalidate_approval() -> None:
    clock = Clock()
    plan, action, decision = red_action()
    approvals = service(clock)
    token = approvals.issue(plan, action, decision)  # type: ignore[arg-type]
    changed = action.model_copy(update={"target_identity": "other-machine"})
    changed_plan = plan.model_copy(update={"steps": (changed,)})
    with pytest.raises(ApprovalError, match="does not match"):
        approvals.verify(token, changed_plan, changed, decision)  # type: ignore[arg-type]


def test_expired_approval_is_rejected() -> None:
    clock = Clock()
    plan, action, decision = red_action()
    approvals = service(clock)
    token = approvals.issue(plan, action, decision)  # type: ignore[arg-type]
    clock.value += timedelta(seconds=31)
    with pytest.raises(ApprovalError, match="expired"):
        approvals.verify(token, plan, action, decision)  # type: ignore[arg-type]


def test_short_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="32"):
        ApprovalTokenService(b"short")
