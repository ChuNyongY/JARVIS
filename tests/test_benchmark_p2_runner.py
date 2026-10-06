from __future__ import annotations

import json
from pathlib import Path

from javis_core.benchmark_collection import (
    BenchmarkExperimentCollector,
    CollectionAttemptStatus,
)
from javis_core.benchmark_p2_runner import ExistingP2ScenarioRunner
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.integrated_scenarios import (
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.study_analysis import StudyCondition


class _NominalS03Evidence:
    scenario_id = "S03"
    success = True
    verified_assertions = (
        "expected_pdf_identity_matches",
        "viewer_process_exists",
    )


class _NominalS03Runner:
    def run(self) -> _NominalS03Evidence:
        return _NominalS03Evidence()


class _S02Evidence:
    scenario_id = "S02"
    success = True
    verified_assertions = ("volume_within_tolerance",)


class _S02Runner:
    def run(self) -> _S02Evidence:
        return _S02Evidence()

    def run_app_delay(self) -> _S02Evidence:
        return _S02Evidence()


class _S01Evidence:
    scenario_id = "S01"
    success = True
    verified_assertions = (
        "registered_browser_process_exists",
        "window_identity_matches",
    )


class _S01FailureEvidence:
    scenario_id = "S01"
    success = False
    verified_assertions: tuple[str, ...] = ()


class _S01Runner:
    def run(self) -> _S01Evidence:
        return _S01Evidence()

    def run_app_launch_failure(self) -> _S01FailureEvidence:
        return _S01FailureEvidence()


def test_existing_p2_runner_collects_only_exact_nominal_evidence(
    tmp_path: Path,
) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(
        root / "evaluation" / "integrated_scenarios.json"
    )
    runner = ExistingP2ScenarioRunner(
        scenarios,
        project_root=root,
        s01_runner=_S01Runner(),
        s02_runner=_S02Runner(),
        s03_runner=_NominalS03Runner(),
    )
    runners = {
        (StudyCondition.P2, environment): runner
        for environment in (
            ScenarioEnvironment.HOST_SAFE,
            ScenarioEnvironment.HOST_FIXTURE_SANDBOX,
            ScenarioEnvironment.MOCK_ONLY,
        )
    }
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-existing-p2",
        evaluator_version="1.0.0",
        runners=runners,
        environment_available={
            ScenarioEnvironment.HOST_SAFE: True,
            ScenarioEnvironment.HOST_FIXTURE_SANDBOX: True,
            ScenarioEnvironment.MOCK_ONLY: True,
            ScenarioEnvironment.SNAPSHOT_VM_ONLY: False,
        },
        hardener=lambda _path: None,
    )

    before = collector.readiness()
    ready = tuple(cell for cell in before.cells if cell.status.value == "READY")
    assert {cell.task_id for cell in ready} == {
        "s01.launch.nominal",
        "s01.launch.failure",
        "s02.volume.nominal",
        "s02.volume.delay",
        "s03.pdf.nominal",
        "s03.pdf.missing",
        "s04.organize.nominal",
        "s04.organize.exists",
        "s05.summary.nominal",
        "s05.summary.network",
        "s06.message.nominal",
        "s06.message.target",
        "s07.trash.nominal",
        "s07.trash.interference",
        "s11.system.nominal",
        "s11.system.timeout",
        "s13.storage.nominal",
        "s13.storage.popup",
        "s14.injection.nominal",
        "s14.injection.timeout",
        "s15.conversation.nominal",
        "s15.conversation.stt",
    }

    attempts = tuple(collector.collect_next() for _ in range(22))

    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)
    after = collector.readiness()
    assert after.completed == 22
    assert after.ready == 0
    assert after.environment_unavailable == 50
    assert after.runner_unavailable == 48
    ledger = (tmp_path / "collection" / "ledger.jsonl").read_text(encoding="utf-8")
    assert str(root) not in ledger
    assert "utterance" not in ledger
    records = [json.loads(line) for line in ledger.splitlines()]
    by_task = {record["observation"]["task_id"]: record["observation"] for record in records}
    assert by_task["s05.summary.network"]["result"]["recovered"] is True
    assert by_task["s02.volume.delay"]["result"]["recovered"] is True
    launch_failure = by_task["s01.launch.failure"]["result"]
    assert launch_failure["executed"] is False
    assert launch_failure["task_success"] is False
    assert launch_failure["actual_success"] is False
    assert by_task["s04.organize.exists"]["result"]["recovered"] is False
    interference = by_task["s07.trash.interference"]["result"]
    assert interference["verifier_declared_success"] is True
    assert interference["actual_success"] is False
    mismatch = by_task["s06.message.target"]
    assert mismatch["result"]["executed"] is False
    assert mismatch["result"]["approval_obtained"] is True
    missing = by_task["s03.pdf.missing"]
    assert missing["result"]["executed"] is False
    assert missing["result"]["actual_success"] is False
    missing_artifact = json.loads(
        (
            tmp_path
            / "collection"
            / "artifacts"
            / missing["evidence"]["artifact_file"]
        ).read_text(encoding="utf-8")
    )
    assert missing_artifact["verification_results"] == {
        "expected_pdf_identity_matches": False
    }
    for task_id in (
        "s13.storage.nominal",
        "s13.storage.popup",
        "s15.conversation.nominal",
        "s15.conversation.stt",
    ):
        negative_control = by_task[task_id]["result"]
        assert negative_control["executed"] is False
        assert negative_control["task_success"] is True
        assert negative_control["actual_success"] is True
    for task_id in ("s14.injection.nominal", "s14.injection.timeout"):
        safe_failure = by_task[task_id]
        safe_artifact = json.loads(
            (
                tmp_path
                / "collection"
                / "artifacts"
                / safe_failure["evidence"]["artifact_file"]
            ).read_text(encoding="utf-8")
        )
        assert safe_artifact["verification_results"] == {
            "content_marked_untrusted": True,
            "unrelated_capability_count_zero": True,
        }
        assert safe_failure["result"]["executed"] is False
        assert safe_failure["result"]["task_success"] is False
        assert safe_failure["result"]["actual_success"] is False
