from __future__ import annotations

from javis_core import (
    Action,
    CancellationToken,
    CapabilityRegistry,
    CapabilitySpec,
    CapturePurpose,
    ExecutionBroker,
    ExecutionResult,
    RiskLevel,
    SelectiveCaptureGate,
)

PID = 4242


def action(**updates: object) -> Action:
    values: dict[str, object] = {
        "capability_id": "demo.visual.toggle",
        "arguments": {"process_id": PID},
        "purpose": "toggle a visual demo control",
        "target_identity": f"process:{PID}",
    }
    values.update(updates)
    return Action(**values)  # type: ignore[arg-type]


def uia_failure(current: Action, **updates: object) -> ExecutionResult:
    values: dict[str, object] = {
        "action_id": current.action_id,
        "executor": "uia",
        "success": False,
        "error_code": "UIA_GROUNDING_REQUIRED",
        "retry_safe": True,
        "duration_ms": 0,
    }
    values.update(updates)
    return ExecutionResult(**values)  # type: ignore[arg-type]


class FixedExecutor:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0

    def execute(
        self,
        current: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        del cancellation
        self.calls += 1
        return self.result.model_copy(update={"action_id": current.action_id})


class GatedVisionExecutor:
    def __init__(self, gate: SelectiveCaptureGate) -> None:
        self.gate = gate
        self.calls = 0

    def execute(
        self,
        current: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        del cancellation
        self.calls += 1
        process_id = current.arguments["process_id"]
        assert type(process_id) is int
        assert self.gate.consume(current.action_id, process_id, CapturePurpose.GROUNDING)
        assert self.gate.consume(current.action_id, process_id, CapturePurpose.PRE_INPUT)
        return ExecutionResult(
            action_id=current.action_id,
            executor="vision",
            success=True,
            duration_ms=0,
        )


def registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        (
            CapabilitySpec(
                capability_id="demo.visual.toggle",
                description="toggle a visual demo control",
                risk_floor=RiskLevel.R2,
                allowed_executors=("uia", "vision"),
                reversible=True,
                external_effect=False,
                sensitive=False,
            ),
        )
    )


def test_broker_authorizes_three_one_shot_captures_after_bound_uia_failure() -> None:
    current = action()
    gate = SelectiveCaptureGate(lambda pid: pid == PID)
    uia = FixedExecutor(uia_failure(current))
    vision = GatedVisionExecutor(gate)
    broker = ExecutionBroker(
        registry(),
        {"uia": uia, "vision": vision},
        vision_capture_gate=gate,
    )

    result = broker.execute(current)

    assert result.success
    assert uia.calls == vision.calls == 1
    assert gate.consume(current.action_id, PID, CapturePurpose.VERIFICATION)
    assert not gate.consume(current.action_id, PID, CapturePurpose.VERIFICATION)
    gate.close(current.action_id)
    assert not gate.consume(current.action_id, PID, CapturePurpose.GROUNDING)


def test_broker_never_calls_vision_without_gate_or_preceding_uia_evidence() -> None:
    current = action()
    uia = FixedExecutor(uia_failure(current))
    vision = FixedExecutor(
        ExecutionResult(
            action_id=current.action_id,
            executor="vision",
            success=True,
            duration_ms=0,
        )
    )

    result = ExecutionBroker(registry(), {"uia": uia, "vision": vision}).execute(current)

    assert result.error_code == "VISION_FALLBACK_NOT_AUTHORIZED"
    assert vision.calls == 0


def test_gate_rejects_unbound_ambiguous_or_post_input_evidence() -> None:
    current = action()
    gate = SelectiveCaptureGate(lambda _pid: True)

    mismatched = current.model_copy(update={"target_identity": "process:999"})
    wrong_target = gate.authorize_uia_fallback(mismatched, uia_failure(mismatched))
    ambiguous = gate.authorize_uia_fallback(
        current,
        uia_failure(current, error_code="UIA_OBSERVATION_FAILED"),
    )
    post_input = gate.authorize_uia_fallback(
        current,
        uia_failure(current, retry_safe=False),
    )

    assert not wrong_target
    assert not ambiguous
    assert not post_input


def test_gate_expires_and_revalidates_process_for_each_capture() -> None:
    now = [10.0]
    authorized = [True]
    current = action()
    gate = SelectiveCaptureGate(
        lambda _pid: authorized[0],
        authorization_seconds=1.0,
        clock=lambda: now[0],
    )
    assert gate.authorize_uia_fallback(current, uia_failure(current))
    now[0] = 9.0
    assert not gate.consume(current.action_id, PID, CapturePurpose.GROUNDING)
    now[0] = 10.0
    authorized[0] = False
    assert not gate.consume(current.action_id, PID, CapturePurpose.GROUNDING)
    authorized[0] = True
    now[0] = 11.0
    assert not gate.consume(current.action_id, PID, CapturePurpose.GROUNDING)


def test_gate_capacity_and_dependency_errors_fail_closed() -> None:
    first = action()
    second = action()
    gate = SelectiveCaptureGate(lambda _pid: True, max_active_actions=1)
    assert gate.authorize_uia_fallback(first, uia_failure(first))
    assert not gate.authorize_uia_fallback(second, uia_failure(second))

    failing = SelectiveCaptureGate(
        lambda _pid: (_ for _ in ()).throw(RuntimeError("secret")),
        clock=lambda: float("nan"),
    )
    assert not failing.authorize_uia_fallback(first, uia_failure(first))


def test_broker_closes_unused_capture_authorization_after_vision_failure() -> None:
    current = action()
    gate = SelectiveCaptureGate(lambda _pid: True)
    uia = FixedExecutor(uia_failure(current))
    vision = FixedExecutor(
        ExecutionResult(
            action_id=current.action_id,
            executor="vision",
            success=False,
            error_code="VISION_GROUNDING_FAILED",
            retry_safe=True,
            duration_ms=0,
        )
    )
    broker = ExecutionBroker(
        registry(),
        {"uia": uia, "vision": vision},
        vision_capture_gate=gate,
    )

    result = broker.execute(current)

    assert result.error_code == "VISION_GROUNDING_FAILED"
    assert not gate.consume(current.action_id, PID, CapturePurpose.GROUNDING)
