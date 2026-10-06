from __future__ import annotations

import pytest

from javis_core.models import Action, VerificationResult
from javis_core.runtime_models import ExecutionResult
from javis_core.verifier_router import CapabilityVerifierRouter


class RecordingVerifier:
    def __init__(self, response: VerificationResult) -> None:
        self.response = response
        self.calls: list[tuple[Action, ExecutionResult]] = []

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        self.calls.append((action, result))
        return self.response


class RaisingVerifier:
    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        raise RuntimeError("private delegate failure")


class InvalidResultVerifier:
    def verify(self, action: Action, result: ExecutionResult) -> object:
        return object()


def action(capability_id: str) -> Action:
    return Action(
        capability_id=capability_id,
        arguments={"private": "must-not-leak"},
        purpose="route verification",
    )


def result(for_action: Action) -> ExecutionResult:
    return ExecutionResult(
        action_id=for_action.action_id,
        executor="native",
        success=True,
        observed={},
        duration_ms=1,
    )


def test_routes_only_to_exact_capability() -> None:
    expected = VerificationResult(success=True, reason="VERIFIED")
    first = RecordingVerifier(expected)
    second = RecordingVerifier(expected)
    router = CapabilityVerifierRouter({"cap.first": first, "cap.second": second})
    selected = action("cap.second")
    execution = result(selected)

    assert router.verify(selected, execution) == expected
    assert first.calls == []
    assert second.calls == [(selected, execution)]


def test_unsupported_capability_fails_closed_without_argument_leak() -> None:
    router = CapabilityVerifierRouter(
        {"cap.known": RecordingVerifier(VerificationResult(success=True, reason="OK"))}
    )
    unknown = action("cap.unknown")

    verification = router.verify(unknown, result(unknown))

    assert verification == VerificationResult(
        success=False,
        observed={},
        reason="UNSUPPORTED_CAPABILITY",
        recoverable=False,
    )
    assert "private" not in verification.reason
    assert "must-not-leak" not in verification.reason


@pytest.mark.parametrize(
    "routes",
    [
        {},
        {"": RecordingVerifier(VerificationResult(success=True, reason="OK"))},
        {"bad route": RecordingVerifier(VerificationResult(success=True, reason="OK"))},
        {"x" * 129: RecordingVerifier(VerificationResult(success=True, reason="OK"))},
    ],
)
def test_invalid_route_maps_are_rejected(routes: object) -> None:
    with pytest.raises(ValueError):
        CapabilityVerifierRouter(routes)  # type: ignore[arg-type]


def test_route_mapping_is_copied_and_invalid_delegate_is_rejected() -> None:
    route = RecordingVerifier(VerificationResult(success=True, reason="OK"))
    routes = {"cap.safe": route}
    router = CapabilityVerifierRouter(routes)
    routes.clear()
    selected = action("cap.safe")

    assert router.verify(selected, result(selected)).success
    with pytest.raises(TypeError):
        CapabilityVerifierRouter({"cap.bad": object()})  # type: ignore[dict-item]


@pytest.mark.parametrize("delegate", [InvalidResultVerifier(), RaisingVerifier()])
def test_delegate_contract_failure_is_redacted_and_fail_closed(delegate: object) -> None:
    router = CapabilityVerifierRouter({"cap.safe": delegate})  # type: ignore[dict-item]
    selected = action("cap.safe")

    verification = router.verify(selected, result(selected))

    assert not verification.success
    assert not verification.recoverable
    assert verification.observed == {}
    assert verification.reason in {"INVALID_VERIFIER_RESULT", "VERIFIER_FAILED"}
