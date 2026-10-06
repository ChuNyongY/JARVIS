from pathlib import Path

from javis_core import FixtureIntegratedScenarioRunner, load_integrated_scenarios
from javis_core.cli import main

MANIFEST = Path("evaluation/integrated_scenarios.json").resolve()


def test_s04_s05_s07_run_in_disposable_fixture_sandboxes() -> None:
    report = FixtureIntegratedScenarioRunner(load_integrated_scenarios(MANIFEST)).run()

    assert report.all_passed
    assert report.passed == 3
    assert tuple(item.scenario_id for item in report.results) == ("S04", "S05", "S07")
    assert all(item.evidence_level == "FIXTURE_SANDBOX" for item in report.results)
    assert all(item.external_effect_count == 0 for item in report.results)
    assert report.results[1].verified_assertions == (
        "source_set_matches",
        "document_content_hash_matches",
    )


def test_fixture_scenario_cli(capsys: object) -> None:
    assert main(["run-fixture-scenarios", str(MANIFEST)]) == 0


def test_s03_missing_pdf_fails_closed_without_external_effect() -> None:
    result = FixtureIntegratedScenarioRunner(
        load_integrated_scenarios(MANIFEST)
    ).run_s03_file_missing()

    assert result.scenario_id == "S03"
    assert result.success is False
    assert result.error_code == "S03_FILE_MISSING"
    assert result.verified_assertions == ()
    assert result.external_effect_count == 0
    assert "remembered-missing.pdf" not in result.model_dump_json()
