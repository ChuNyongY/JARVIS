from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import JsonValue

from .models import Action, Postcondition, VerificationResult
from .runtime_models import ExecutionResult


class Verifier(Protocol):
    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult: ...


Predicate = Callable[[Postcondition, ExecutionResult], bool]


class PredicateVerifier:
    def __init__(self, predicates: Mapping[str, Predicate] | None = None) -> None:
        builtins: dict[str, Predicate] = {
            "execution.success": lambda _post, result: result.success,
            "observed.contains": self._observed_contains,
            "observed.equals": self._observed_equals,
        }
        builtins.update(predicates or {})
        self._predicates = builtins

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH", recoverable=False)
        if not result.success:
            return self._failure(
                result.error_code or "EXECUTION_FAILED",
                recoverable=result.retry_safe,
            )
        for postcondition in action.expected_postconditions:
            predicate = self._predicates.get(postcondition.predicate)
            if predicate is None:
                return self._failure("UNKNOWN_POSTCONDITION", recoverable=False)
            try:
                matched = predicate(postcondition, result)
            except (KeyError, TypeError, ValueError):
                matched = False
            if not matched:
                return self._failure(
                    f"POSTCONDITION_FAILED:{postcondition.predicate}",
                    recoverable=True,
                )
        return VerificationResult(
            success=True,
            observed=result.observed,
            reason="ALL_POSTCONDITIONS_MET",
        )

    @staticmethod
    def _observed_contains(postcondition: Postcondition, result: ExecutionResult) -> bool:
        expected = postcondition.arguments.get("values")
        if not isinstance(expected, dict):
            raise ValueError("values must be an object")
        return all(result.observed.get(key) == value for key, value in expected.items())

    @staticmethod
    def _observed_equals(postcondition: Postcondition, result: ExecutionResult) -> bool:
        expected: JsonValue = postcondition.arguments.get("value")
        return result.observed == expected

    @staticmethod
    def _failure(reason: str, *, recoverable: bool) -> VerificationResult:
        return VerificationResult(
            success=False,
            reason=reason,
            recoverable=recoverable,
        )
