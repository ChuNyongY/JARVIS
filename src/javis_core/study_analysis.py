from __future__ import annotations

import json
import math
from collections import defaultdict
from enum import StrEnum
from fractions import Fraction
from pathlib import Path

from pydantic import Field, TypeAdapter

from .models import FrozenModel, RiskLevel


class StudyDataError(ValueError):
    pass


class StudyCondition(StrEnum):
    B0 = "B0"
    B1 = "B1"
    P1 = "P1"
    P2 = "P2"


class StudyMetric(StrEnum):
    UER = "UER"
    UNSAFE_EXECUTION = "UNSAFE_EXECUTION"
    TSR = "TSR"


class StudyObservation(FrozenModel):
    participant_id: str = Field(min_length=1, max_length=128, repr=False)
    scenario_id: str = Field(min_length=1, max_length=128)
    condition: StudyCondition
    intended_command: bool
    ground_truth_risk: RiskLevel
    executed: bool
    approval_obtained: bool
    task_success: bool
    confirmations: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(ge=0)


class RateEstimate(FrozenModel):
    condition: StudyCondition
    events: int = Field(ge=0)
    eligible: int = Field(ge=0)
    rate: float | None
    ci95_low: float | None
    ci95_high: float | None


class PairedComparison(FrozenModel):
    baseline: StudyCondition
    metric: StudyMetric
    eligible_pairs: int = Field(ge=0)
    baseline_events: int = Field(ge=0)
    p2_events: int = Field(ge=0)
    baseline_zero_p2_one: int = Field(ge=0)
    baseline_one_p2_zero: int = Field(ge=0)
    effect_delta: float | None
    raw_p_value: float | None
    holm_adjusted_p_value: float | None


class ConditionBurden(FrozenModel):
    condition: StudyCondition
    observations: int = Field(ge=0)
    mean_confirmations: float | None
    mean_total_latency_ms: float | None


class PairedStudyReport(FrozenModel):
    block_count: int = Field(ge=1)
    rates: dict[StudyMetric, tuple[RateEstimate, ...]]
    comparisons: dict[StudyMetric, tuple[PairedComparison, ...]]
    burden: tuple[ConditionBurden, ...]

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


Block = dict[StudyCondition, StudyObservation]


def analyze_paired_study(
    observations: tuple[StudyObservation, ...],
) -> PairedStudyReport:
    blocks = _validated_blocks(observations)
    rates: dict[StudyMetric, tuple[RateEstimate, ...]] = {}
    comparisons: dict[StudyMetric, tuple[PairedComparison, ...]] = {}
    for metric in StudyMetric:
        eligible = tuple(block for block in blocks if _eligible(metric, block[StudyCondition.B0]))
        rates[metric] = tuple(
            _rate_estimate(
                condition,
                tuple(_event(metric, block[condition]) for block in eligible),
            )
            for condition in StudyCondition
        )
        preliminary = tuple(
            _paired_comparison(metric, baseline, eligible)
            for baseline in (StudyCondition.B0, StudyCondition.B1, StudyCondition.P1)
        )
        adjusted = holm_adjust(
            tuple(item.raw_p_value for item in preliminary if item.raw_p_value is not None)
        )
        adjusted_iterator = iter(adjusted)
        comparisons[metric] = tuple(
            item.model_copy(
                update={
                    "holm_adjusted_p_value": (
                        next(adjusted_iterator) if item.raw_p_value is not None else None
                    )
                }
            )
            for item in preliminary
        )
    return PairedStudyReport(
        block_count=len(blocks),
        rates=rates,
        comparisons=comparisons,
        burden=_burden(observations),
    )


def load_study_observations(
    path: Path,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    max_observations: int = 100_000,
) -> tuple[StudyObservation, ...]:
    if min(max_bytes, max_observations) < 1:
        raise ValueError("loader limits must be positive")
    if path.is_symlink() or not path.is_file():
        raise StudyDataError("study data must be a regular non-symlink file")
    try:
        if path.stat().st_size > max_bytes:
            raise StudyDataError("study data exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise StudyDataError("study data exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(document, list) or len(document) > max_observations:
            raise StudyDataError("invalid study observation count")
        observations = tuple(TypeAdapter(list[StudyObservation]).validate_python(document))
        _validated_blocks(observations)
        return observations
    except StudyDataError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise StudyDataError("invalid study data") from exc


def exact_mcnemar_p(discordant_a: int, discordant_b: int) -> float:
    if discordant_a < 0 or discordant_b < 0:
        raise ValueError("discordant counts must be nonnegative")
    total = discordant_a + discordant_b
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, index) for index in range(min(discordant_a, discordant_b) + 1))
    return min(1.0, float(Fraction(2 * tail, 2**total)))


def holm_adjust(p_values: tuple[float, ...]) -> tuple[float, ...]:
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in p_values):
        raise ValueError("p-values must be finite and within 0..1")
    ordered = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    adjusted = [0.0] * len(p_values)
    running = 0.0
    count = len(p_values)
    for rank, (original_index, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[original_index] = running
    return tuple(adjusted)


def _validated_blocks(observations: tuple[StudyObservation, ...]) -> tuple[Block, ...]:
    if not observations:
        raise StudyDataError("study requires at least one observation")
    grouped: dict[tuple[str, str], Block] = defaultdict(dict)
    for observation in observations:
        key = (observation.participant_id, observation.scenario_id)
        if observation.condition in grouped[key]:
            raise StudyDataError("duplicate condition within participant/scenario block")
        grouped[key][observation.condition] = observation
    expected = set(StudyCondition)
    blocks: list[Block] = []
    for block in grouped.values():
        if set(block) != expected:
            raise StudyDataError("each block must contain B0, B1, P1, and P2 exactly once")
        metadata = {
            (item.intended_command, item.ground_truth_risk) for item in block.values()
        }
        if len(metadata) != 1:
            raise StudyDataError("eligibility metadata must be consistent within a block")
        blocks.append(block)
    return tuple(blocks)


def _eligible(metric: StudyMetric, observation: StudyObservation) -> bool:
    if metric is StudyMetric.UER:
        return not observation.intended_command
    if metric is StudyMetric.UNSAFE_EXECUTION:
        return observation.ground_truth_risk >= RiskLevel.R3
    return observation.intended_command


def _event(metric: StudyMetric, observation: StudyObservation) -> bool:
    if metric is StudyMetric.UER:
        return observation.executed
    if metric is StudyMetric.UNSAFE_EXECUTION:
        return observation.executed and not observation.approval_obtained
    return observation.task_success


def _rate_estimate(
    condition: StudyCondition,
    events: tuple[bool, ...],
) -> RateEstimate:
    count = sum(events)
    interval = _wilson_interval(count, len(events))
    return RateEstimate(
        condition=condition,
        events=count,
        eligible=len(events),
        rate=count / len(events) if events else None,
        ci95_low=interval[0] if interval else None,
        ci95_high=interval[1] if interval else None,
    )


def _paired_comparison(
    metric: StudyMetric,
    baseline: StudyCondition,
    blocks: tuple[Block, ...],
) -> PairedComparison:
    pairs = tuple(
        (_event(metric, block[baseline]), _event(metric, block[StudyCondition.P2]))
        for block in blocks
    )
    baseline_events = sum(pair[0] for pair in pairs)
    p2_events = sum(pair[1] for pair in pairs)
    zero_one = sum(not left and right for left, right in pairs)
    one_zero = sum(left and not right for left, right in pairs)
    denominator = len(pairs)
    return PairedComparison(
        baseline=baseline,
        metric=metric,
        eligible_pairs=denominator,
        baseline_events=baseline_events,
        p2_events=p2_events,
        baseline_zero_p2_one=zero_one,
        baseline_one_p2_zero=one_zero,
        effect_delta=(p2_events - baseline_events) / denominator if denominator else None,
        raw_p_value=exact_mcnemar_p(zero_one, one_zero) if denominator else None,
        holm_adjusted_p_value=None,
    )


def _wilson_interval(events: int, total: int) -> tuple[float, float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = events / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _burden(observations: tuple[StudyObservation, ...]) -> tuple[ConditionBurden, ...]:
    return tuple(
        ConditionBurden(
            condition=condition,
            observations=len(items),
            mean_confirmations=(sum(item.confirmations for item in items) / len(items)),
            mean_total_latency_ms=(sum(item.total_latency_ms for item in items) / len(items)),
        )
        for condition in StudyCondition
        if (items := tuple(item for item in observations if item.condition is condition))
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StudyDataError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise StudyDataError(f"non-finite JSON number: {value}")
