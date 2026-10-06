from __future__ import annotations

from pathlib import Path

import pytest

from javis_core import (
    HostSafeScenarioRunner,
    ScenarioEnvironment,
    ScenarioEvidenceLevel,
    load_integrated_scenarios,
)


def test_runner_executes_only_non_mutating_s11_s14_s15_contracts() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))

    report = HostSafeScenarioRunner(manifest, project_root=Path.cwd()).run()

    assert report.all_passed
    assert report.passed == 3
    assert tuple(result.scenario_id for result in report.results) == ("S11", "S14", "S15")
    assert report.results[0].evidence_level is ScenarioEvidenceLevel.ACTUAL_READ_ONLY
    assert all(result.error_code is None for result in report.results)
    assert report.results[1].verified_assertions == (
        "unrelated_capability_count_zero",
        "content_marked_untrusted",
    )
    assert report.results[2].verified_assertions == (
        "false_activation_count_zero",
        "plan_count_zero",
    )


def test_report_omits_transcripts_host_values_and_paths() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))

    raw = HostSafeScenarioRunner(manifest, project_root=Path.cwd()).run().model_dump_json()

    assert "초기화할까" not in raw
    assert "untrusted page" not in raw
    assert str(Path.cwd()) not in raw
    assert "hostname" not in raw
    assert '"version":' not in raw


def test_stt_misrecognized_wake_word_creates_no_plan() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))

    result = HostSafeScenarioRunner(
        manifest,
        project_root=Path.cwd(),
    ).run_s15_stt_misrecognition()

    assert result.success
    assert result.verified_assertions == (
        "false_activation_count_zero",
        "plan_count_zero",
    )


def test_s14_benchmark_preserves_safety_without_claiming_summary_success() -> None:
    nominal = HostSafeScenarioRunner.run_s14_benchmark_nominal()
    timeout = HostSafeScenarioRunner.run_s14_llm_timeout()

    for result in (nominal, timeout):
        assert not result.success
        assert result.verified_assertions == (
            "unrelated_capability_count_zero",
            "content_marked_untrusted",
        )
    assert nominal.error_code == "S14_SUMMARY_NOT_COMPLETED"
    assert timeout.error_code == "LLM_TIMEOUT"


def test_runner_rejects_s11_environment_downgrade() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))
    scenarios = list(manifest.scenarios)
    scenarios[10] = scenarios[10].model_copy(
        update={"environment": ScenarioEnvironment.MOCK_ONLY}
    )
    unsafe_manifest = manifest.model_copy(update={"scenarios": tuple(scenarios)})

    with pytest.raises(ValueError, match="S11"):
        HostSafeScenarioRunner(unsafe_manifest, project_root=Path.cwd())
