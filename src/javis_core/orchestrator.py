from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .approval import ApprovalError, ApprovalTokenService
from .audit import AuditSink
from .cancellation import CancellationToken
from .executor import Executor
from .models import (
    Action,
    ActionStatus,
    Decision,
    Plan,
    PolicyContext,
    PolicyDecision,
    VerificationResult,
)
from .policy import PolicyEngine
from .runtime_models import (
    ApprovalArtifact,
    AuditEvent,
    ExecutionResult,
    RetryBudget,
    TaskOutcome,
)
from .verifier import Verifier

ContextProvider = Callable[[Action], PolicyContext]


class ApprovalBroker(Protocol):
    def approve_yellow(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> bool: ...

    def approve_red(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalArtifact | None: ...


class Replanner(Protocol):
    def replan(
        self,
        plan: Plan,
        failed_action: Action,
        execution: ExecutionResult,
        verification: VerificationResult,
        replan_number: int,
    ) -> Action | None: ...


class DenyAllApprovalBroker:
    def approve_yellow(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> bool:
        return False

    def approve_red(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalArtifact | None:
        return None


class Orchestrator:
    def __init__(
        self,
        *,
        policy: PolicyEngine,
        executor: Executor,
        verifier: Verifier,
        audit: AuditSink,
        approvals: ApprovalTokenService,
        approval_broker: ApprovalBroker | None = None,
        retry_budget: RetryBudget | None = None,
        cancellation: CancellationToken | None = None,
        replanner: Replanner | None = None,
    ) -> None:
        self._policy = policy
        self._executor = executor
        self._verifier = verifier
        self._audit = audit
        self._approvals = approvals
        self._approval_broker = approval_broker or DenyAllApprovalBroker()
        self._budget = retry_budget or RetryBudget()
        self._cancellation = cancellation or CancellationToken()
        self._replanner = replanner

    def run(self, plan: Plan, context_provider: ContextProvider) -> TaskOutcome:
        completed = 0
        replans = 0
        actions = list(plan.steps)
        current_plan = plan
        action_index = 0
        while action_index < len(actions):
            action = actions[action_index]
            if self._cancellation.is_cancelled:
                return self._abort(current_plan, action, completed, self._cancellation.reason)
            decision = self._policy.evaluate(action, context_provider(action))
            self._emit(
                current_plan,
                action,
                "POLICY_DECISION",
                {
                    "decision": decision.decision.value,
                    "risk": decision.risk.value,
                    "reasons": list(decision.reasons),
                },
            )
            authorization_error = self._authorize(current_plan, action, decision)
            if authorization_error is not None:
                return self._abort(current_plan, action, completed, authorization_error)

            action_succeeded = False
            last_reason = "NO_ATTEMPT"
            last_result: ExecutionResult | None = None
            last_verification: VerificationResult | None = None
            for attempt in range(1, self._budget.max_attempts + 1):
                if self._cancellation.is_cancelled:
                    return self._abort(
                        current_plan, action, completed, self._cancellation.reason
                    )
                self._emit(
                    current_plan,
                    action,
                    "EXECUTION_STARTED",
                    {"attempt": attempt, "capability_id": action.capability_id},
                )
                result = self._executor.execute(action, self._cancellation)
                last_result = result
                self._emit(
                    current_plan,
                    action,
                    "EXECUTION_FINISHED",
                    {
                        "attempt": attempt,
                        "success": result.success,
                        "error_code": result.error_code,
                        "duration_ms": result.duration_ms,
                        "executor": result.executor,
                    },
                )
                if self._cancellation.is_cancelled:
                    return self._abort(
                        current_plan, action, completed, self._cancellation.reason
                    )
                verification = self._verifier.verify(action, result)
                last_verification = verification
                self._emit(
                    current_plan,
                    action,
                    "VERIFICATION_FINISHED",
                    {
                        "attempt": attempt,
                        "success": verification.success,
                        "reason": verification.reason,
                        "recoverable": verification.recoverable,
                    },
                )
                if verification.success:
                    action_succeeded = True
                    completed += 1
                    break

                last_reason = verification.reason
                if not verification.recoverable or attempt >= self._budget.max_attempts:
                    break
                self._emit(
                    current_plan,
                    action,
                    "RETRY_SCHEDULED",
                    {"next_attempt": attempt + 1, "reason": verification.reason},
                )

            if not action_succeeded:
                if (
                    last_result is None
                    or last_verification is None
                    or not last_verification.recoverable
                    or self._replanner is None
                    or replans >= self._budget.max_replans
                ):
                    return self._abort(current_plan, action, completed, last_reason)
                replacement = self._replanner.replan(
                    current_plan,
                    action,
                    last_result,
                    last_verification,
                    replans + 1,
                )
                if replacement is None:
                    return self._abort(current_plan, action, completed, "REPLAN_DECLINED")
                if replacement.action_id == action.action_id:
                    return self._abort(
                        current_plan,
                        action,
                        completed,
                        "REPLAN_REUSED_ACTION_ID",
                    )
                replans += 1
                actions[action_index] = replacement
                current_plan = current_plan.model_copy(update={"steps": tuple(actions)})
                self._emit(
                    current_plan,
                    action,
                    "REPLAN_SCHEDULED",
                    {
                        "replan_number": replans,
                        "new_action_id": str(replacement.action_id),
                        "reason": last_reason,
                    },
                )
                continue
            action_index += 1

        outcome = TaskOutcome(
            task_id=current_plan.task_id,
            status=ActionStatus.SUCCEEDED,
            completed_actions=completed,
            reason="ALL_ACTIONS_VERIFIED",
        )
        self._emit(
            current_plan,
            None,
            "TASK_FINISHED",
            {"completed_actions": completed, "reason": outcome.reason},
        )
        return outcome

    def _authorize(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> str | None:
        if decision.decision is Decision.DENY:
            return f"POLICY_DENIED:{decision.reasons[0]}"
        if decision.decision is Decision.YELLOW and not self._approval_broker.approve_yellow(
            plan, action, decision
        ):
            return "YELLOW_APPROVAL_REQUIRED"
        if decision.decision is Decision.RED:
            artifact = self._approval_broker.approve_red(plan, action, decision)
            if artifact is None:
                return "RED_APPROVAL_REQUIRED"
            if decision.requires_strong_auth and not artifact.strong_auth_completed:
                return "STRONG_AUTH_REQUIRED"
            try:
                self._approvals.verify(artifact.token, plan, action, decision)
            except ApprovalError:
                return "INVALID_APPROVAL"
            self._emit(
                plan,
                action,
                "APPROVAL_VERIFIED",
                {"risk": decision.risk.value, "strong_auth": artifact.strong_auth_completed},
            )
        return None

    def _abort(
        self,
        plan: Plan,
        action: Action,
        completed: int,
        reason: str,
    ) -> TaskOutcome:
        outcome = TaskOutcome(
            task_id=plan.task_id,
            status=ActionStatus.ABORTED,
            completed_actions=completed,
            reason=reason,
        )
        self._emit(
            plan,
            action,
            "TASK_ABORTED",
            {"completed_actions": completed, "reason": reason},
        )
        return outcome

    def _emit(
        self,
        plan: Plan,
        action: Action | None,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self._audit.append(
            AuditEvent.model_validate(
                {
                    "task_id": plan.task_id,
                    "action_id": action.action_id if action else None,
                    "event_type": event_type,
                    "payload": payload,
                }
            )
        )
