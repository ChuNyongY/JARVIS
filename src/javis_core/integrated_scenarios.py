from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import FrozenModel

_MAX_MANIFEST_BYTES = 128 * 1024
_EXPECTED_IDS = tuple(f"S{index:02d}" for index in range(1, 16))
_VM_ONLY_IDS = frozenset({"S08", "S09", "S10", "S12"})


class ScenarioManifestError(RuntimeError):
    """Raised when the fixed integrated-scenario manifest is unsafe or malformed."""


class ScenarioEnvironment(StrEnum):
    HOST_SAFE = "HOST_SAFE"
    HOST_FIXTURE_SANDBOX = "HOST_FIXTURE_SANDBOX"
    SNAPSHOT_VM_ONLY = "SNAPSHOT_VM_ONLY"
    MOCK_ONLY = "MOCK_ONLY"


class ExpectedAuthorization(StrEnum):
    AUTO_GREEN = "AUTO_GREEN"
    EXPLICIT_YELLOW = "EXPLICIT_YELLOW"
    EXPLICIT_RED = "EXPLICIT_RED"
    STRONG_R4 = "STRONG_R4"
    SUGGEST_ONLY = "SUGGEST_ONLY"
    NO_ACTION = "NO_ACTION"


class BaselineIsolation(StrEnum):
    MOCK = "MOCK"
    SNAPSHOT_VM = "SNAPSHOT_VM"


class IntegratedScenario(FrozenModel):
    scenario_id: str = Field(pattern=r"^S(?:0[1-9]|1[0-5])$")
    stimulus: str = Field(min_length=1, max_length=300)
    expected_behavior: str = Field(min_length=1, max_length=500)
    expected_authorization: ExpectedAuthorization
    environment: ScenarioEnvironment
    b0_isolation: BaselineIsolation
    required_capabilities: tuple[str, ...] = Field(min_length=1, max_length=12)
    verification_assertions: tuple[str, ...] = Field(min_length=1, max_length=12)
    forbidden_effects: tuple[str, ...] = Field(default_factory=tuple, max_length=12)

    @field_validator("required_capabilities", "verification_assertions", "forbidden_effects")
    @classmethod
    def bounded_unique_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("scenario token lists must be unique")
        if any(not value or len(value) > 100 or not value.isascii() for value in values):
            raise ValueError("scenario tokens must be bounded non-empty ASCII")
        return values

    @model_validator(mode="after")
    def enforce_execution_boundary(self) -> IntegratedScenario:
        if (
            self.scenario_id in _VM_ONLY_IDS
            and self.environment is not ScenarioEnvironment.SNAPSHOT_VM_ONLY
        ):
            raise ValueError("destructive scenario must be snapshot-VM-only")
        if self.expected_authorization in {
            ExpectedAuthorization.EXPLICIT_RED,
            ExpectedAuthorization.STRONG_R4,
        } and self.environment in {
            ScenarioEnvironment.HOST_SAFE,
            ScenarioEnvironment.HOST_FIXTURE_SANDBOX,
        }:
            raise ValueError("RED/R4 scenario cannot run on the personal host")
        if self.expected_authorization in {
            ExpectedAuthorization.NO_ACTION,
            ExpectedAuthorization.SUGGEST_ONLY,
        } and not self.forbidden_effects:
            raise ValueError("non-execution scenario must declare forbidden effects")
        if self.scenario_id == "S10":
            if self.expected_authorization is not ExpectedAuthorization.STRONG_R4:
                raise ValueError("system reset must require R4 authorization")
            if "automatic_commit" not in self.forbidden_effects:
                raise ValueError("system reset must forbid automatic commit")
        if self.scenario_id == "S13" and (
            self.expected_authorization is not ExpectedAuthorization.SUGGEST_ONLY
            or "filesystem_delete" not in self.forbidden_effects
        ):
            raise ValueError("low-disk scenario must remain suggestion-only")
        if self.scenario_id in {"S14", "S15"} and (
            self.expected_authorization is not ExpectedAuthorization.NO_ACTION
        ):
            raise ValueError("injection and non-addressed speech must not execute")
        return self


class IntegratedScenarioManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    scenarios: tuple[IntegratedScenario, ...] = Field(min_length=15, max_length=15)

    @model_validator(mode="after")
    def exact_fixed_set(self) -> IntegratedScenarioManifest:
        identifiers = tuple(scenario.scenario_id for scenario in self.scenarios)
        if identifiers != _EXPECTED_IDS:
            raise ValueError("manifest must contain ordered scenarios S01 through S15 exactly once")
        return self


def load_integrated_scenarios(path: Path) -> IntegratedScenarioManifest:
    try:
        if path.is_symlink() or not path.is_file():
            raise ScenarioManifestError("scenario manifest must be a regular file")
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ScenarioManifestError("scenario manifest exceeds size limit")
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
        return IntegratedScenarioManifest.model_validate(raw)
    except ScenarioManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScenarioManifestError("invalid integrated scenario manifest") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")
