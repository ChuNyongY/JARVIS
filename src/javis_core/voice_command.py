from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .cancellation import CancellationToken
from .intent import GateResult, GateRoute, InputSource, IntentEvidence, IntentGate
from .models import Plan
from .voice import CaptureStatus, VoiceCaptureResult


class VoiceCommandStatus(StrEnum):
    CAPTURE_ABORTED = "CAPTURE_ABORTED"
    ROUTED = "ROUTED"
    PLAN_READY = "PLAN_READY"
    FAILED = "FAILED"


@dataclass(frozen=True, repr=False)
class VoiceCommandDecision:
    status: VoiceCommandStatus
    reason: str
    gate: GateResult | None = None
    plan: Plan | None = None


class EvidenceClassifier(Protocol):
    def classify(
        self,
        transcript: str,
        *,
        wake_confidence: float,
        explicit_wake_word: bool,
        source: InputSource = "MIC",
    ) -> IntentEvidence: ...


class CommandPlanner(Protocol):
    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan: ...


class VoiceCommandCoordinator:
    """Route a completed voice capture without acquiring execution authority."""

    def __init__(
        self,
        classifier: EvidenceClassifier,
        planner: CommandPlanner,
        cancellation: CancellationToken,
        *,
        gate: IntentGate | None = None,
    ) -> None:
        self._classifier = classifier
        self._planner = planner
        self._cancellation = cancellation
        self._gate = gate or IntentGate()

    def process(
        self,
        capture: VoiceCaptureResult,
        *,
        task_active: bool,
        trusted_context: Mapping[str, object] | None = None,
    ) -> VoiceCommandDecision:
        if capture.status is CaptureStatus.ABORTED:
            return VoiceCommandDecision(
                status=VoiceCommandStatus.CAPTURE_ABORTED,
                reason=capture.reason,
            )
        if capture.status is not CaptureStatus.TRANSCRIBED or not capture.transcript:
            return VoiceCommandDecision(
                status=VoiceCommandStatus.FAILED,
                reason="INVALID_VOICE_CAPTURE",
            )
        try:
            evidence = self._classifier.classify(
                capture.transcript,
                wake_confidence=capture.wake_confidence,
                explicit_wake_word=True,
                source="MIC",
            )
            gate_result = self._gate.evaluate(evidence, task_active=task_active)
        except Exception:
            return VoiceCommandDecision(
                status=VoiceCommandStatus.FAILED,
                reason="INTENT_CLASSIFICATION_FAILED",
            )
        if gate_result.route is GateRoute.CANCEL and gate_result.accepted:
            self._cancellation.cancel("LOCAL_STOP")
            return VoiceCommandDecision(
                status=VoiceCommandStatus.ROUTED,
                reason=gate_result.reason,
                gate=gate_result,
            )
        if gate_result.route is not GateRoute.PLAN or not gate_result.accepted:
            return VoiceCommandDecision(
                status=VoiceCommandStatus.ROUTED,
                reason=gate_result.reason,
                gate=gate_result,
            )
        try:
            plan = self._planner.plan(
                evidence.transcript,
                trusted_context=trusted_context or {},
            )
        except Exception:
            return VoiceCommandDecision(
                status=VoiceCommandStatus.FAILED,
                reason="PLANNING_FAILED",
                gate=gate_result,
            )
        return VoiceCommandDecision(
            status=VoiceCommandStatus.PLAN_READY,
            reason="TYPED_PLAN_READY",
            gate=gate_result,
            plan=plan,
        )
