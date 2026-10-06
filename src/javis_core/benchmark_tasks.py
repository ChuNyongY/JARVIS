from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from pydantic import Field, StrictBool, field_validator, model_validator

from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .models import FrozenModel, RiskLevel
from .registry import CapabilityRegistry


class BenchmarkTaskError(ValueError):
    pass


class FaultProfile(StrEnum):
    NONE = "NONE"
    APP_LAUNCH_FAILURE = "APP_LAUNCH_FAILURE"
    UI_ELEMENT_MISSING = "UI_ELEMENT_MISSING"
    POPUP = "POPUP"
    WINDOW_MOVED = "WINDOW_MOVED"
    APP_DELAY = "APP_DELAY"
    USER_INTERFERENCE = "USER_INTERFERENCE"
    FILE_EXISTS = "FILE_EXISTS"
    FILE_MISSING = "FILE_MISSING"
    NETWORK_DISCONNECT = "NETWORK_DISCONNECT"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    STT_MISRECOGNITION = "STT_MISRECOGNITION"
    FOREGROUND_CHANGE = "FOREGROUND_CHANGE"
    POLICY_ERROR = "POLICY_ERROR"
    VISION_MISMATCH = "VISION_MISMATCH"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"


class BenchmarkTask(FrozenModel):
    task_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    scenario_id: str = Field(pattern=r"^S(?:0[1-9]|1[0-5])$")
    utterance: str = Field(min_length=1, max_length=500)
    intended_command: StrictBool
    ground_truth_risk: RiskLevel
    environment: ScenarioEnvironment
    required_capabilities: tuple[str, ...] = Field(min_length=1, max_length=8)
    expected_assertions: tuple[str, ...] = Field(min_length=1, max_length=12)
    fault_profile: FaultProfile = FaultProfile.NONE

    @field_validator("ground_truth_risk", mode="before")
    @classmethod
    def risk_must_be_an_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("ground_truth_risk must be an integer")
        return value

    @model_validator(mode="after")
    def validate_tokens(self) -> BenchmarkTask:
        token = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
        if (
            len(set(self.required_capabilities)) != len(self.required_capabilities)
            or any(token.fullmatch(item) is None for item in self.required_capabilities)
            or len(set(self.expected_assertions)) != len(self.expected_assertions)
            or any(token.fullmatch(item) is None for item in self.expected_assertions)
        ):
            raise ValueError("benchmark capability and assertion tokens must be unique")
        return self


class BenchmarkTaskManifest(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    tasks: tuple[BenchmarkTask, ...] = Field(min_length=30, max_length=50)

    @model_validator(mode="after")
    def validate_coverage(self) -> BenchmarkTaskManifest:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("benchmark task_id values must be unique")
        expected = {f"S{number:02d}" for number in range(1, 16)}
        counts = {scenario_id: 0 for scenario_id in expected}
        for task in self.tasks:
            counts[task.scenario_id] += 1
        if any(count < 2 for count in counts.values()):
            raise ValueError("every S01-S15 scenario must have at least two benchmark tasks")
        if not any(task.fault_profile is FaultProfile.NONE for task in self.tasks):
            raise ValueError("benchmark must include nominal tasks")
        if sum(task.fault_profile is not FaultProfile.NONE for task in self.tasks) < 10:
            raise ValueError("benchmark must include at least ten fault tasks")
        return self


def load_benchmark_tasks(path: Path, *, max_bytes: int = 512 * 1024) -> BenchmarkTaskManifest:
    if max_bytes < 2 or path.is_symlink() or not path.is_file():
        raise BenchmarkTaskError("invalid benchmark manifest file")
    try:
        if path.stat().st_size > max_bytes:
            raise BenchmarkTaskError("benchmark manifest exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise BenchmarkTaskError("benchmark manifest exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        return BenchmarkTaskManifest.model_validate(document)
    except BenchmarkTaskError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BenchmarkTaskError("invalid benchmark task manifest") from error


def validate_benchmark_contract(
    benchmark: BenchmarkTaskManifest,
    scenarios: IntegratedScenarioManifest,
    registry: CapabilityRegistry,
) -> None:
    authoritative = {scenario.scenario_id: scenario for scenario in scenarios.scenarios}
    for task in benchmark.tasks:
        scenario = authoritative[task.scenario_id]
        if task.environment is not scenario.environment:
            raise BenchmarkTaskError("benchmark environment drifted from scenario manifest")
        if set(task.required_capabilities) != set(scenario.required_capabilities):
            raise BenchmarkTaskError("benchmark capabilities drifted from scenario manifest")
        if not set(task.expected_assertions).issubset(scenario.verification_assertions):
            raise BenchmarkTaskError("benchmark assertions drifted from scenario manifest")
        capability_risks = tuple(
            capability.risk_floor
            for capability_id in task.required_capabilities
            if (capability := registry.get(capability_id)) is not None
        )
        if capability_risks and task.ground_truth_risk < max(capability_risks):
            raise BenchmarkTaskError("benchmark risk is below a capability floor")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkTaskError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise BenchmarkTaskError(f"non-finite JSON number: {value}")
