from __future__ import annotations

from collections import deque
from collections.abc import Callable
from enum import StrEnum
from threading import Lock, Thread
from typing import Protocol
from uuid import UUID

from pydantic import Field

from .agent_session import (
    OrchestratorFactory,
    PlannerContextProvider,
    PolicyContextProvider,
)
from .cancellation import CancellationToken
from .intent import LocalStopDetector
from .models import ActionStatus, FrozenModel, Plan
from .policy_context import gate_scoped_policy_context_provider
from .runtime_models import TaskOutcome
from .voice import CaptureStatus, VoiceCaptureResult
from .voice_command import VoiceCommandCoordinator, VoiceCommandStatus
from .voice_feedback import VoiceFeedbackPresenter


class AlwaysOnState(StrEnum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    CLOSED = "CLOSED"


class RuntimeEventType(StrEnum):
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_ABORTED = "TASK_ABORTED"
    STOP_ACCEPTED = "STOP_ACCEPTED"
    BUSY_IGNORED = "BUSY_IGNORED"
    ROUTED = "ROUTED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


class RuntimeEvent(FrozenModel):
    event_type: RuntimeEventType
    reason: str = Field(
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    task_id: UUID | None = None
    completed_actions: int = Field(default=0, ge=0)
    feedback_delivered: bool | None = None


class RuntimeStatusSnapshot(FrozenModel):
    state: AlwaysOnState
    last_event_type: RuntimeEventType | None = None
    last_reason: str = Field(
        default="RUNTIME_READY",
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    completed_actions: int = Field(default=0, ge=0)
    feedback_delivered: bool | None = None
    sequence: int = Field(default=0, ge=0)


class RuntimeCoordinatorFactory(Protocol):
    def __call__(self, cancellation: CancellationToken) -> VoiceCommandCoordinator: ...


class AlwaysOnAgentRuntime:
    """Coordinate sequential tasks while accepting an exact local STOP concurrently."""

    def __init__(
        self,
        *,
        coordinator_factory: RuntimeCoordinatorFactory,
        orchestrator_factory: OrchestratorFactory,
        policy_context_provider: PolicyContextProvider,
        feedback: VoiceFeedbackPresenter | None = None,
        stop_detector: LocalStopDetector | None = None,
        planner_context_provider: PlannerContextProvider | None = None,
        thread_factory: Callable[..., Thread] = Thread,
    ) -> None:
        self._coordinator_factory = coordinator_factory
        self._orchestrator_factory = orchestrator_factory
        self._policy_context_provider = policy_context_provider
        self._feedback = feedback
        self._stop_detector = stop_detector or LocalStopDetector()
        self._planner_context_provider = planner_context_provider
        self._thread_factory = thread_factory
        self._lock = Lock()
        self._state = AlwaysOnState.IDLE
        self._task_token: CancellationToken | None = None
        self._worker: Thread | None = None
        self._events: deque[RuntimeEvent] = deque(maxlen=100)
        self._status = RuntimeStatusSnapshot(state=AlwaysOnState.IDLE)

    @property
    def state(self) -> AlwaysOnState:
        with self._lock:
            return self._state

    def status_snapshot(self) -> RuntimeStatusSnapshot:
        """Return a privacy-minimized status without consuming the event queue."""
        with self._lock:
            return self._status.model_copy()

    def submit_capture(
        self,
        capture: VoiceCaptureResult,
        *,
        trusted_context: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        with self._lock:
            if self._state is AlwaysOnState.CLOSED:
                event = RuntimeEvent(
                    event_type=RuntimeEventType.CLOSED,
                    reason="RUNTIME_CLOSED",
                )
                self._record_status_locked(event)
                return event
            if self._state in {AlwaysOnState.PLANNING, AlwaysOnState.EXECUTING}:
                return self._handle_busy_capture_locked(capture)
            token = CancellationToken()
            self._task_token = token
            self._state = AlwaysOnState.PLANNING
            try:
                worker = self._thread_factory(
                    target=self._plan_and_run,
                    args=(
                        capture,
                        None if trusted_context is None else dict(trusted_context),
                        token,
                    ),
                    name="javis-task-worker",
                    daemon=True,
                )
                self._worker = worker
                worker.start()
            except Exception:
                self._clear_task_locked(token)
                event = RuntimeEvent(
                    event_type=RuntimeEventType.FAILED,
                    reason="TASK_WORKER_START_FAILED",
                )
                self._record_status_locked(event)
                return event
            event = RuntimeEvent(
                event_type=RuntimeEventType.TASK_STARTED,
                reason="PLANNING_STARTED",
            )
            self._record_status_locked(event)
        return event

    def poll_event(self) -> RuntimeEvent | None:
        with self._lock:
            return self._events.popleft() if self._events else None

    def wait_for_idle(self, *, timeout_seconds: float = 5.0) -> RuntimeEvent | None:
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be within 0..30")
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout_seconds)
            if worker.is_alive():
                return None
        return self.poll_event()

    def close(self, *, timeout_seconds: float = 5.0) -> RuntimeEvent:
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be within 0..30")
        with self._lock:
            if self._state is AlwaysOnState.CLOSED:
                event = RuntimeEvent(
                    event_type=RuntimeEventType.CLOSED,
                    reason="RUNTIME_CLOSED",
                )
                self._record_status_locked(event)
                return event
            token = self._task_token
            worker = self._worker
            self._state = AlwaysOnState.CLOSED
            if token is not None:
                token.cancel("RUNTIME_SHUTDOWN")
        if worker is not None:
            worker.join(timeout=timeout_seconds)
            if worker.is_alive():
                event = RuntimeEvent(
                    event_type=RuntimeEventType.FAILED,
                    reason="RUNTIME_STOP_TIMEOUT",
                )
                with self._lock:
                    self._record_status_locked(event)
                return event
        event = RuntimeEvent(
            event_type=RuntimeEventType.CLOSED,
            reason="RUNTIME_CLOSED",
        )
        with self._lock:
            self._record_status_locked(event)
        return event

    def _handle_busy_capture_locked(self, capture: VoiceCaptureResult) -> RuntimeEvent:
        transcript = capture.transcript
        if (
            capture.status is CaptureStatus.TRANSCRIBED
            and transcript is not None
            and self._stop_detector.matches(transcript)
            and self._task_token is not None
        ):
            self._task_token.cancel("LOCAL_STOP")
            event = RuntimeEvent(
                event_type=RuntimeEventType.STOP_ACCEPTED,
                reason="LOCAL_STOP_ACCEPTED",
            )
            self._record_status_locked(event)
            return event
        event = RuntimeEvent(
            event_type=RuntimeEventType.BUSY_IGNORED,
            reason="ACTIVE_TASK_ACCEPTS_STOP_ONLY",
        )
        self._record_status_locked(event)
        return event

    def _plan_and_run(
        self,
        capture: VoiceCaptureResult,
        trusted_context: dict[str, object] | None,
        token: CancellationToken,
    ) -> None:
        if trusted_context is None:
            try:
                trusted_context = (
                    dict(self._planner_context_provider())
                    if self._planner_context_provider is not None
                    else {}
                )
            except Exception:
                self._finish_worker(token, self._failed("CONTEXT_COLLECTION_FAILED"))
                return
        if token.is_cancelled:
            self._finish_worker(
                token,
                RuntimeEvent(
                    event_type=RuntimeEventType.ROUTED,
                    reason="PLANNING_CANCELLED",
                ),
            )
            return
        try:
            decision = self._coordinator_factory(token).process(
                capture,
                task_active=False,
                trusted_context=trusted_context,
            )
        except Exception:
            self._finish_worker(token, self._failed("COMMAND_COORDINATION_FAILED"))
            return

        with self._lock:
            if self._state is AlwaysOnState.CLOSED:
                return
            if token.is_cancelled:
                self._finish_worker_locked(
                    token,
                    RuntimeEvent(
                        event_type=RuntimeEventType.ROUTED,
                        reason="PLANNING_CANCELLED",
                    ),
                )
                return
            if decision.status is not VoiceCommandStatus.PLAN_READY:
                event_type = (
                    RuntimeEventType.FAILED
                    if decision.status
                    in {VoiceCommandStatus.FAILED, VoiceCommandStatus.CAPTURE_ABORTED}
                    else RuntimeEventType.ROUTED
                )
                self._finish_worker_locked(
                    token,
                    self._safe_event(event_type, decision.reason),
                )
                return
            if decision.plan is None:
                self._finish_worker_locked(
                    token,
                    self._failed("PLAN_READY_WITHOUT_PLAN"),
                )
                return
            plan = decision.plan
            try:
                policy_context_provider = gate_scoped_policy_context_provider(
                    self._policy_context_provider,
                    decision.gate,
                )
            except Exception:
                self._finish_worker_locked(
                    token,
                    self._failed("COMMAND_DECISION_INVALID"),
                )
                return
            self._state = AlwaysOnState.EXECUTING
            self._status = self._status.model_copy(
                update={
                    "state": AlwaysOnState.EXECUTING,
                    "last_reason": "EXECUTION_STARTED",
                    "sequence": self._status.sequence + 1,
                }
            )

        self._run_task(plan, token, policy_context_provider)

    def _run_task(
        self,
        plan: Plan,
        token: CancellationToken,
        policy_context_provider: PolicyContextProvider,
    ) -> None:
        try:
            outcome = self._orchestrator_factory(token).run(
                plan,
                policy_context_provider,
            )
            feedback_delivered = self._deliver_feedback(outcome, token)
            event = RuntimeEvent(
                event_type=(
                    RuntimeEventType.TASK_COMPLETED
                    if outcome.status is ActionStatus.SUCCEEDED
                    else RuntimeEventType.TASK_ABORTED
                ),
                reason=outcome.reason,
                task_id=outcome.task_id,
                completed_actions=outcome.completed_actions,
                feedback_delivered=feedback_delivered,
            )
        except Exception:
            event = RuntimeEvent(
                event_type=RuntimeEventType.FAILED,
                reason="ORCHESTRATION_FAILED",
                task_id=plan.task_id,
            )
        self._finish_worker(token, event)

    def _deliver_feedback(
        self,
        outcome: TaskOutcome,
        token: CancellationToken,
    ) -> bool | None:
        if self._feedback is None or token.is_cancelled:
            return None
        try:
            self._feedback.present_outcome(outcome, cancellation=token)
        except Exception:
            return False
        return True

    def _finish_worker(self, token: CancellationToken, event: RuntimeEvent) -> None:
        with self._lock:
            self._finish_worker_locked(token, event)

    def _finish_worker_locked(
        self,
        token: CancellationToken,
        event: RuntimeEvent,
    ) -> None:
        self._events.append(event)
        if self._state is not AlwaysOnState.CLOSED and self._task_token is token:
            self._clear_task_locked(token)
        self._record_status_locked(event)

    def _clear_task_locked(self, token: CancellationToken) -> None:
        if self._task_token is token:
            self._task_token = None
            self._worker = None
            if self._state is not AlwaysOnState.CLOSED:
                self._state = AlwaysOnState.IDLE

    def _record_status_locked(self, event: RuntimeEvent) -> None:
        self._status = RuntimeStatusSnapshot(
            state=self._state,
            last_event_type=event.event_type,
            last_reason=event.reason,
            completed_actions=event.completed_actions,
            feedback_delivered=event.feedback_delivered,
            sequence=self._status.sequence + 1,
        )

    @staticmethod
    def _safe_event(event_type: RuntimeEventType, reason: str) -> RuntimeEvent:
        try:
            return RuntimeEvent(event_type=event_type, reason=reason)
        except ValueError:
            return RuntimeEvent(
                event_type=RuntimeEventType.FAILED,
                reason="COMMAND_DECISION_INVALID",
            )

    @staticmethod
    def _failed(reason: str) -> RuntimeEvent:
        return RuntimeEvent(event_type=RuntimeEventType.FAILED, reason=reason)
