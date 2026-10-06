import pytest

from javis_core import (
    Action,
    Decision,
    PolicyContext,
    PolicyEngine,
    RiskLevel,
    default_registry,
)


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine(default_registry())


def context(**overrides: object) -> PolicyContext:
    values: dict[str, object] = {
        "addressed_confidence": 0.99,
        "intent_confidence": 0.99,
        "target_confidence": 0.99,
        "target_matches_plan": True,
    }
    values.update(overrides)
    return PolicyContext.model_validate(values)


def action(capability_id: str) -> Action:
    return Action(capability_id=capability_id, purpose="test", target_identity="target")


def test_unknown_capability_is_denied(engine: PolicyEngine) -> None:
    result = engine.evaluate(action("powershell.raw"), context())
    assert result.decision is Decision.DENY
    assert result.risk is RiskLevel.R4


def test_low_risk_capability_is_green(engine: PolicyEngine) -> None:
    result = engine.evaluate(action("system.info.read"), context())
    assert result.decision is Decision.GREEN
    assert result.risk is RiskLevel.R0


def test_r2_capability_is_yellow(engine: PolicyEngine) -> None:
    result = engine.evaluate(action("file.move"), context())
    assert result.decision is Decision.YELLOW
    assert result.risk is RiskLevel.R2


def test_context_can_only_raise_risk(engine: PolicyEngine) -> None:
    result = engine.evaluate(
        action("system.info.read"),
        context(external_transmission=True, requested_risk=RiskLevel.R1),
    )
    assert result.decision is Decision.RED
    assert result.risk is RiskLevel.R3


def test_uncertain_gate_requires_preview(engine: PolicyEngine) -> None:
    result = engine.evaluate(action("system.info.read"), context(intent_confidence=0.8))
    assert result.decision is Decision.YELLOW
    assert result.risk is RiskLevel.R2


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"addressed_confidence": 0.5}, "LOW_GATE_CONFIDENCE"),
        ({"target_matches_plan": False}, "TARGET_MISMATCH"),
        ({"suspected_prompt_injection": True}, "PROMPT_INJECTION_SUSPECTED"),
    ],
)
def test_hard_denials(
    engine: PolicyEngine,
    override: dict[str, object],
    reason: str,
) -> None:
    result = engine.evaluate(action("system.info.read"), context(**override))
    assert result.decision is Decision.DENY
    assert reason in result.reasons


def test_r4_requires_strong_auth(engine: PolicyEngine) -> None:
    result = engine.evaluate(action("system.reset.commit"), context())
    assert result.decision is Decision.RED
    assert result.risk is RiskLevel.R4
    assert result.requires_strong_auth

