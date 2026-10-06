from pathlib import Path

import pytest

from javis_core import (
    Action,
    FaultInjectingExecutor,
    FaultPhase,
    FaultSpec,
    MockExecutor,
    write_fault_report,
)


def action() -> Action:
    return Action(capability_id="system.info.read", purpose="test fault")


def test_before_call_fault_can_be_explicitly_retry_safe() -> None:
    delegate = MockExecutor({"system.info.read": lambda action: {"ok": True}})
    wrapper = FaultInjectingExecutor(
        delegate,
        (
            FaultSpec(
                name="adapter unavailable",
                phase=FaultPhase.BEFORE_CALL,
                on_call=1,
                error_code="ADAPTER_UNAVAILABLE",
                retry_safe=True,
            ),
        ),
    )

    result = wrapper.execute(action())

    assert not result.success
    assert result.retry_safe
    assert delegate.calls == ()
    assert not wrapper.observations[0].side_effect_possible


def test_after_call_fault_is_ambiguous_and_never_retry_safe() -> None:
    delegate = MockExecutor({"system.info.read": lambda action: {"ok": True}})
    wrapper = FaultInjectingExecutor(
        delegate,
        (
            FaultSpec(
                name="response lost",
                phase=FaultPhase.AFTER_CALL,
                on_call=1,
                error_code="RESULT_LOST",
            ),
        ),
    )

    result = wrapper.execute(action())

    assert not result.success
    assert not result.retry_safe
    assert len(delegate.calls) == 1
    assert wrapper.observations[0].side_effect_possible


def test_after_call_fault_cannot_claim_retry_safety() -> None:
    with pytest.raises(ValueError, match="after-call"):
        FaultSpec(
            name="unsafe",
            phase=FaultPhase.AFTER_CALL,
            on_call=1,
            error_code="AMBIGUOUS",
            retry_safe=True,
        )


def test_duplicate_fault_slot_is_rejected() -> None:
    fault = FaultSpec(
        name="one",
        phase=FaultPhase.BEFORE_CALL,
        on_call=1,
        error_code="FAIL",
    )
    with pytest.raises(ValueError, match="one fault"):
        FaultInjectingExecutor(MockExecutor(), (fault, fault.model_copy(update={"name": "two"})))


def test_fault_report_is_jsonl_without_action_arguments(tmp_path: Path) -> None:
    wrapper = FaultInjectingExecutor(
        MockExecutor(),
        (
            FaultSpec(
                name="offline",
                phase=FaultPhase.BEFORE_CALL,
                on_call=1,
                error_code="OFFLINE",
            ),
        ),
    )
    wrapper.execute(action())
    report = tmp_path / "reports" / "faults.jsonl"

    write_fault_report(report, wrapper.observations)

    content = report.read_text(encoding="utf-8")
    assert '"fault_name":"offline"' in content
    assert "arguments" not in content
