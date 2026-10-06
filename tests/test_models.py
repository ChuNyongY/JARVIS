from uuid import uuid4

import pytest
from pydantic import ValidationError

from javis_core.models import (
    Action,
    ActionStatus,
    Plan,
    VerificationResult,
    canonical_arguments_hash,
)
from javis_core.runtime_models import ExecutionResult, TaskOutcome


def test_argument_hash_is_canonical() -> None:
    left = canonical_arguments_hash({"b": [2, 1], "a": "한글"})
    right = canonical_arguments_hash({"a": "한글", "b": [2, 1]})
    assert left == right
    assert len(left) == 64


def test_argument_hash_rejects_non_json_value() -> None:
    with pytest.raises(ValueError):
        canonical_arguments_hash({"bad": {1, 2}})  # type: ignore[dict-item]


def test_plan_requires_unique_actions() -> None:
    action_id = uuid4()
    first = Action(
        action_id=action_id,
        capability_id="file.list",
        purpose="first",
    )
    second = Action(
        action_id=action_id,
        capability_id="system.info.read",
        purpose="second",
    )
    with pytest.raises(ValidationError, match="unique"):
        Plan(goal="invalid", steps=(first, second))


def test_runtime_status_codes_reject_free_form_or_unbounded_text() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(
            action_id=uuid4(),
            executor="native",
            success=False,
            error_code="private path or secret",
            duration_ms=0,
        )
    with pytest.raises(ValidationError):
        VerificationResult(success=False, reason="private\nerror")
    with pytest.raises(ValidationError):
        TaskOutcome(
            task_id=uuid4(),
            status=ActionStatus.ABORTED,
            completed_actions=0,
            reason="X" * 257,
        )
