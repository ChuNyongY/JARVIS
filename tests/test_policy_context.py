from __future__ import annotations

import pytest

from javis_core.intent import GateResult, GateRoute
from javis_core.models import Action, PolicyContext, RiskLevel
from javis_core.policy_context import gate_scoped_policy_context_provider


def action() -> Action:
    return Action(capability_id="system.info.read", purpose="policy confidence test")


def context(confidence: float) -> PolicyContext:
    return PolicyContext(
        addressed_confidence=confidence,
        intent_confidence=confidence,
        target_confidence=confidence,
        requested_risk=RiskLevel.R3,
        privilege_escalation=True,
        external_transmission=True,
        sensitive_data=True,
        suspected_prompt_injection=True,
        target_matches_plan=True,
    )


def test_gate_confidence_caps_provider_without_changing_security_flags() -> None:
    expected = context(1.0)
    scoped = gate_scoped_policy_context_provider(
        lambda _action: expected,
        GateResult(
            route=GateRoute.PLAN,
            accepted=True,
            confidence=0.91,
            reason="COMMAND_ACCEPTED",
        ),
    )

    observed = scoped(action())

    assert observed.addressed_confidence == 0.91
    assert observed.intent_confidence == 0.91
    assert observed.target_confidence == 0.91
    assert observed.requested_risk is RiskLevel.R3
    assert observed.privilege_escalation
    assert observed.external_transmission
    assert observed.sensitive_data
    assert observed.suspected_prompt_injection
    assert observed.target_matches_plan


def test_lower_provider_confidence_is_never_elevated() -> None:
    scoped = gate_scoped_policy_context_provider(
        lambda _action: context(0.82),
        GateResult(
            route=GateRoute.PLAN,
            accepted=True,
            confidence=0.95,
            reason="COMMAND_ACCEPTED",
        ),
    )

    observed = scoped(action())

    assert observed.addressed_confidence == 0.82
    assert observed.intent_confidence == 0.82
    assert observed.target_confidence == 0.82


@pytest.mark.parametrize(
    "gate",
    [
        None,
        GateResult(
            route=GateRoute.PLAN,
            accepted=False,
            confidence=0.95,
            reason="REJECTED",
        ),
        GateResult(
            route=GateRoute.RESPOND,
            accepted=True,
            confidence=0.95,
            reason="RESPONSE_ACCEPTED",
        ),
    ],
)
def test_missing_or_non_plan_gate_fails_closed(gate: GateResult | None) -> None:
    with pytest.raises(ValueError, match="accepted PLAN gate"):
        gate_scoped_policy_context_provider(lambda _action: context(1.0), gate)
