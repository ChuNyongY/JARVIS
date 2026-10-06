import json
from pathlib import Path

import pytest

from javis_core import RiskLevel, TrialResult, evaluate_trials, load_trials


def trial(trial_id: str, **overrides: object) -> TrialResult:
    values: dict[str, object] = {
        "trial_id": trial_id,
        "intended_command": True,
        "ground_truth_risk": RiskLevel.R1,
        "predicted_risk": RiskLevel.R1,
        "executed": True,
        "approval_required": False,
        "approval_obtained": False,
        "task_success": True,
        "verifier_declared_success": True,
        "actual_success": True,
        "confirmations": 0,
        "latency_ms": {"total": 100.0},
    }
    values.update(overrides)
    return TrialResult.model_validate(values)


def test_metrics_match_definitions() -> None:
    trials = (
        trial("safe-success"),
        trial(
            "background-false-activation",
            intended_command=False,
            ground_truth_risk=RiskLevel.R0,
            predicted_risk=RiskLevel.R1,
            executed=True,
            task_success=False,
            verifier_declared_success=False,
            actual_success=False,
            latency_ms={"total": 200.0},
        ),
        trial(
            "unsafe-dangerous",
            ground_truth_risk=RiskLevel.R4,
            predicted_risk=RiskLevel.R1,
            approval_required=True,
            approval_obtained=False,
            executed=True,
            task_success=False,
            verifier_declared_success=True,
            actual_success=False,
            confirmations=1,
            latency_ms={"total": 300.0, "planning": 30.0},
        ),
        trial(
            "recovered",
            recoverable_failure_occurred=True,
            recovered=True,
            confirmations=1,
            latency_ms={"total": 400.0, "planning": 50.0},
        ),
    )

    report = evaluate_trials(trials)

    assert report.unintended_execution_rate == 1.0
    assert report.unsafe_execution_rate == 1.0
    assert report.severe_underclassification_rate == 1.0
    assert report.task_success_rate == pytest.approx(2 / 3)
    assert report.recovery_rate == 1.0
    assert report.verification_precision == pytest.approx(2 / 3)
    assert report.confirmation_burden == 1.0
    assert report.mean_latency_ms == {"planning": 40.0, "total": 250.0}


def test_zero_denominators_are_reported_as_none() -> None:
    report = evaluate_trials(())
    assert report.trial_count == 0
    assert report.unintended_execution_rate is None
    assert report.task_success_rate is None
    assert report.confirmation_burden is None


def test_load_trials_rejects_duplicate_ids(tmp_path: Path) -> None:
    payload = [
        trial("duplicate").model_dump(mode="json"),
        trial("duplicate").model_dump(mode="json"),
    ]
    path = tmp_path / "trials.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        load_trials(path)


def test_inconsistent_recovery_is_rejected() -> None:
    with pytest.raises(ValueError, match="recoverable failure"):
        trial("bad", recovered=True)
