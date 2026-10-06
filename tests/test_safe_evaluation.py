from pathlib import Path

from javis_core import (
    SafeEvaluationStatus,
    SafeIntegratedEvaluationRunner,
    default_registry,
    load_integrated_scenarios,
)
from javis_core.cli import main

MANIFEST = Path("evaluation/integrated_scenarios.json").resolve()
ROOT = Path.cwd().resolve()


def test_safe_evaluation_reports_passes_and_unexecuted_gaps_without_overclaiming() -> None:
    report = SafeIntegratedEvaluationRunner(
        load_integrated_scenarios(MANIFEST),
        default_registry(),
        project_root=ROOT,
    ).run()

    assert report.catalog_complete
    assert report.passed == 8
    assert report.not_run == 7
    assert not report.all_required_evidence_complete
    passed = {
        item.scenario_id
        for item in report.results
        if item.status is SafeEvaluationStatus.PASS
    }
    assert passed == {"S04", "S05", "S06", "S07", "S11", "S13", "S14", "S15"}
    reasons = {
        item.scenario_id: item.reason_code
        for item in report.results
        if item.status is SafeEvaluationStatus.NOT_RUN
    }
    assert reasons["S03"] == "VIEWER_PROCESS_E2E_NOT_RUN"
    assert reasons["S02"] == "EXACT_VOLUME_MUTATION_NOT_RUN_BY_THIS_COMMAND"
    assert reasons["S12"] == "SNAPSHOT_VM_PROVIDER_UNAVAILABLE"


def test_safe_evaluation_cli(capsys: object) -> None:
    assert main(["run-safe-evaluation", str(MANIFEST), str(ROOT)]) == 0
