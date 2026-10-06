import json
from pathlib import Path

import pytest

from javis_core import RiskLevel
from javis_core.study_analysis import (
    StudyCondition,
    StudyDataError,
    StudyMetric,
    StudyObservation,
    analyze_paired_study,
    exact_mcnemar_p,
    holm_adjust,
    load_study_observations,
)


def block(
    participant: str,
    scenario: str,
    *,
    intended: bool,
    risk: RiskLevel,
    events: dict[StudyCondition, bool],
) -> tuple[StudyObservation, ...]:
    return tuple(
        StudyObservation(
            participant_id=participant,
            scenario_id=scenario,
            condition=condition,
            intended_command=intended,
            ground_truth_risk=risk,
            executed=event,
            approval_obtained=False,
            task_success=event if intended else False,
            confirmations=condition is not StudyCondition.B0,
            total_latency_ms=10 + list(StudyCondition).index(condition),
        )
        for condition, event in events.items()
    )


def all_events(**updates: bool) -> dict[StudyCondition, bool]:
    values = {condition: False for condition in StudyCondition}
    values.update({StudyCondition(key): value for key, value in updates.items()})
    return values


def test_analyzer_uses_paired_eligible_blocks_and_redacts_participants() -> None:
    observations = (
        *block(
            "person-secret",
            "noise-1",
            intended=False,
            risk=RiskLevel.R1,
            events=all_events(B0=True),
        ),
        *block(
            "person-secret",
            "danger-1",
            intended=True,
            risk=RiskLevel.R4,
            events=all_events(B0=True, P2=True),
        ),
    )

    report = analyze_paired_study(observations)

    uer_rates = {item.condition: item for item in report.rates[StudyMetric.UER]}
    assert uer_rates[StudyCondition.B0].rate == 1
    assert uer_rates[StudyCondition.P2].rate == 0
    unsafe = report.comparisons[StudyMetric.UNSAFE_EXECUTION][0]
    assert unsafe.eligible_pairs == 1
    assert unsafe.effect_delta == 0
    assert "person-secret" not in report.canonical_json()


def test_incomplete_duplicate_and_inconsistent_blocks_are_rejected() -> None:
    complete = block(
        "p1",
        "s1",
        intended=True,
        risk=RiskLevel.R1,
        events=all_events(),
    )
    with pytest.raises(StudyDataError, match="B0, B1, P1, and P2"):
        analyze_paired_study(complete[:-1])
    with pytest.raises(StudyDataError, match="duplicate"):
        analyze_paired_study((*complete, complete[0]))
    changed = complete[-1].model_copy(update={"ground_truth_risk": RiskLevel.R4})
    with pytest.raises(StudyDataError, match="consistent"):
        analyze_paired_study((*complete[:-1], changed))


def test_exact_mcnemar_and_holm_known_values() -> None:
    assert exact_mcnemar_p(0, 5) == 0.0625
    assert exact_mcnemar_p(0, 0) == 1
    assert holm_adjust((0.01, 0.03, 0.04)) == pytest.approx((0.03, 0.06, 0.06))


def test_no_eligible_blocks_produce_null_rates_and_p_values() -> None:
    observations = block(
        "p1",
        "intended-low-risk",
        intended=True,
        risk=RiskLevel.R1,
        events=all_events(P2=True),
    )

    report = analyze_paired_study(observations)

    assert report.rates[StudyMetric.UER][0].rate is None
    assert report.comparisons[StudyMetric.UNSAFE_EXECUTION][0].raw_p_value is None


def test_loader_rejects_duplicate_json_and_round_trips(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('[{"participant_id":"a","participant_id":"b"}]')
    with pytest.raises(StudyDataError, match="duplicate"):
        load_study_observations(duplicate)

    observations = block(
        "p1",
        "s1",
        intended=False,
        risk=RiskLevel.R0,
        events=all_events(),
    )
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps([item.model_dump(mode="json") for item in observations]),
        encoding="utf-8",
    )
    assert load_study_observations(valid) == observations
