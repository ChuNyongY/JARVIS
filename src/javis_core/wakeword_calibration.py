from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictInt, model_validator

from .models import FrozenModel
from .wakeword_evaluation import WakeWordCorpusReport


class WakeWordCalibrationStatus(StrEnum):
    SELECTED = "SELECTED"
    NO_FEASIBLE_THRESHOLD = "NO_FEASIBLE_THRESHOLD"


class WakeWordThresholdCandidate(FrozenModel):
    threshold: float = Field(ge=0.5, le=0.99)
    positive_count: StrictInt = Field(ge=1)
    negative_count: StrictInt = Field(ge=1)
    false_positives: StrictInt = Field(ge=0)
    false_negatives: StrictInt = Field(ge=0)
    inference_failures: StrictInt = Field(ge=0)
    false_acceptance_rate: float = Field(ge=0.0, le=1.0)
    false_rejection_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def rates_are_consistent(self) -> WakeWordThresholdCandidate:
        if self.false_positives > self.negative_count:
            raise ValueError("calibration false positives exceed negative count")
        if self.false_negatives > self.positive_count:
            raise ValueError("calibration false negatives exceed positive count")
        if self.inference_failures > self.positive_count + self.negative_count:
            raise ValueError("calibration failures exceed sample count")
        if self.false_acceptance_rate != self.false_positives / self.negative_count:
            raise ValueError("calibration FAR is inconsistent")
        if self.false_rejection_rate != self.false_negatives / self.positive_count:
            raise ValueError("calibration FRR is inconsistent")
        return self


class WakeWordCalibrationReport(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    corpus_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    far_ceiling: float = Field(ge=0.0, le=1.0)
    status: WakeWordCalibrationStatus
    selected_threshold: float | None = Field(default=None, ge=0.5, le=0.99)
    candidates: tuple[WakeWordThresholdCandidate, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def selection_is_consistent(self) -> WakeWordCalibrationReport:
        thresholds = tuple(candidate.threshold for candidate in self.candidates)
        if thresholds != tuple(sorted(set(thresholds))):
            raise ValueError("calibration thresholds must be unique and sorted")
        feasible = tuple(
            candidate
            for candidate in self.candidates
            if candidate.false_acceptance_rate <= self.far_ceiling
        )
        expected = (
            min(
                feasible,
                key=lambda candidate: (
                    candidate.false_rejection_rate,
                    candidate.false_acceptance_rate,
                    -candidate.threshold,
                ),
            ).threshold
            if feasible
            else None
        )
        if self.selected_threshold != expected:
            raise ValueError("calibration selected threshold is not optimal")
        if (self.status is WakeWordCalibrationStatus.SELECTED) != (expected is not None):
            raise ValueError("calibration status is inconsistent")
        return self


DEFAULT_WAKEWORD_THRESHOLDS = tuple(index / 100 for index in range(50, 100))


def calibrate_wakeword_threshold(
    evaluation: WakeWordCorpusReport,
    *,
    far_ceiling: float = 0.05,
    thresholds: tuple[float, ...] = DEFAULT_WAKEWORD_THRESHOLDS,
) -> WakeWordCalibrationReport:
    if type(far_ceiling) is not float or not 0.0 <= far_ceiling <= 1.0:
        raise ValueError("FAR ceiling must be a float between 0 and 1")
    if (
        not thresholds
        or len(thresholds) > 50
        or any(type(threshold) is not float for threshold in thresholds)
        or any(not 0.5 <= threshold <= 0.99 for threshold in thresholds)
        or thresholds != tuple(sorted(set(thresholds)))
    ):
        raise ValueError("calibration thresholds must be unique sorted floats")
    candidates = tuple(
        _candidate(evaluation, threshold)
        for threshold in thresholds
    )
    feasible = tuple(
        candidate
        for candidate in candidates
        if candidate.false_acceptance_rate <= far_ceiling
    )
    selected = (
        min(
            feasible,
            key=lambda candidate: (
                candidate.false_rejection_rate,
                candidate.false_acceptance_rate,
                -candidate.threshold,
            ),
        ).threshold
        if feasible
        else None
    )
    return WakeWordCalibrationReport(
        corpus_id=evaluation.corpus_id,
        far_ceiling=far_ceiling,
        status=(
            WakeWordCalibrationStatus.SELECTED
            if selected is not None
            else WakeWordCalibrationStatus.NO_FEASIBLE_THRESHOLD
        ),
        selected_threshold=selected,
        candidates=candidates,
    )


def _candidate(
    evaluation: WakeWordCorpusReport,
    threshold: float,
) -> WakeWordThresholdCandidate:
    false_positives = sum(
        not sample.target_present
        and (sample.max_score is None or sample.max_score >= threshold)
        for sample in evaluation.samples
    )
    false_negatives = sum(
        sample.target_present
        and (sample.max_score is None or sample.max_score < threshold)
        for sample in evaluation.samples
    )
    return WakeWordThresholdCandidate(
        threshold=threshold,
        positive_count=evaluation.positive_count,
        negative_count=evaluation.negative_count,
        false_positives=false_positives,
        false_negatives=false_negatives,
        inference_failures=evaluation.inference_failures,
        false_acceptance_rate=false_positives / evaluation.negative_count,
        false_rejection_rate=false_negatives / evaluation.positive_count,
    )
