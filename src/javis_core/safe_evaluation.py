from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from .fixture_scenarios import FixtureIntegratedScenarioRunner
from .host_safe_scenarios import HostSafeScenarioRunner
from .integrated_scenarios import IntegratedScenarioManifest
from .mock_scenarios import MockIntegratedScenarioRunner
from .models import FrozenModel
from .registry import CapabilityRegistry
from .scenario_capabilities import audit_scenario_capabilities


class SafeEvaluationStatus(StrEnum):
    PASS = "PASS"
    NOT_RUN = "NOT_RUN"


class SafeEvaluationResult(FrozenModel):
    scenario_id: str = Field(pattern=r"^S(?:0[1-9]|1[0-5])$")
    status: SafeEvaluationStatus
    evidence_level: str = Field(min_length=2, max_length=64)
    reason_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=128,
        pattern=r"^[A-Z0-9_]+$",
    )

    @model_validator(mode="after")
    def consistent_status(self) -> SafeEvaluationResult:
        if (self.status is SafeEvaluationStatus.PASS) != (self.reason_code is None):
            raise ValueError("safe evaluation status and reason are inconsistent")
        return self


class SafeEvaluationReport(FrozenModel):
    schema_version: str = "1.0"
    catalog_complete: bool
    results: tuple[SafeEvaluationResult, ...] = Field(min_length=15, max_length=15)
    passed: int = Field(ge=0, le=15)
    not_run: int = Field(ge=0, le=15)
    all_required_evidence_complete: bool

    @model_validator(mode="after")
    def consistent_summary(self) -> SafeEvaluationReport:
        expected_ids = tuple(f"S{number:02d}" for number in range(1, 16))
        if tuple(item.scenario_id for item in self.results) != expected_ids:
            raise ValueError("safe evaluation must contain ordered S01-S15")
        actual_passed = sum(item.status is SafeEvaluationStatus.PASS for item in self.results)
        actual_not_run = 15 - actual_passed
        if self.passed != actual_passed or self.not_run != actual_not_run:
            raise ValueError("safe evaluation counts are inconsistent")
        if self.all_required_evidence_complete != (self.catalog_complete and actual_not_run == 0):
            raise ValueError("safe evaluation completion flag is inconsistent")
        return self


class SafeIntegratedEvaluationRunner:
    """Aggregate only evidence that can be produced without dangerous host effects."""

    _NOT_RUN_REASONS = {
        "S01": "INTERACTIVE_APP_LAUNCH_NOT_RUN",
        "S02": "EXACT_VOLUME_MUTATION_NOT_RUN_BY_THIS_COMMAND",
        "S03": "VIEWER_PROCESS_E2E_NOT_RUN",
        "S08": "SNAPSHOT_VM_PROVIDER_UNAVAILABLE",
        "S09": "SNAPSHOT_VM_PROVIDER_UNAVAILABLE",
        "S10": "SNAPSHOT_VM_PROVIDER_UNAVAILABLE",
        "S12": "SNAPSHOT_VM_PROVIDER_UNAVAILABLE",
    }

    def __init__(
        self,
        manifest: IntegratedScenarioManifest,
        registry: CapabilityRegistry,
        *,
        project_root: Path,
    ) -> None:
        self._manifest = manifest
        self._registry = registry
        self._project_root = project_root

    def run(self) -> SafeEvaluationReport:
        host = HostSafeScenarioRunner(
            self._manifest,
            project_root=self._project_root,
        ).run()
        fixtures = FixtureIntegratedScenarioRunner(self._manifest).run()
        mocks = MockIntegratedScenarioRunner(self._manifest).run()
        passed_levels = {
            item.scenario_id: item.evidence_level.value
            for item in host.results
            if item.success
        }
        passed_levels.update(
            {
                item.scenario_id: item.evidence_level
                for item in fixtures.results
                if item.success
            }
        )
        passed_levels.update(
            {
                item.scenario_id: item.evidence_level
                for item in mocks.results
                if item.success
            }
        )
        results: list[SafeEvaluationResult] = []
        for number in range(1, 16):
            scenario_id = f"S{number:02d}"
            evidence_level = passed_levels.get(scenario_id)
            if evidence_level is not None:
                results.append(
                    SafeEvaluationResult(
                        scenario_id=scenario_id,
                        status=SafeEvaluationStatus.PASS,
                        evidence_level=evidence_level,
                    )
                )
            else:
                results.append(
                    SafeEvaluationResult(
                        scenario_id=scenario_id,
                        status=SafeEvaluationStatus.NOT_RUN,
                        evidence_level="NONE",
                        reason_code=self._NOT_RUN_REASONS[scenario_id],
                    )
                )
        catalog = audit_scenario_capabilities(self._manifest, self._registry)
        passed = len(passed_levels)
        return SafeEvaluationReport(
            catalog_complete=catalog.catalog_complete,
            results=tuple(results),
            passed=passed,
            not_run=15 - passed,
            all_required_evidence_complete=catalog.catalog_complete and passed == 15,
        )
