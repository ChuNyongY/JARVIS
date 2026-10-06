from pathlib import Path

from javis_core import MockIntegratedScenarioRunner, load_integrated_scenarios


def test_s06_and_s13_run_without_external_effect_or_mutation() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))

    report = MockIntegratedScenarioRunner(manifest).run()
    message, storage = report.results

    assert report.all_passed
    assert report.passed == 2
    assert message.success
    assert message.scenario_id == "S06"
    assert message.evidence_level == "MOCK_CONTRACT"
    assert message.external_effect_count == 0
    assert message.verified_assertions == (
        "recipient_matches",
        "message_content_matches",
        "approval_bound_to_payload",
    )
    assert storage.success
    assert storage.scenario_id == "S13"
    assert storage.verified_assertions == (
        "suggestion_emitted",
        "no_mutation_observed",
    )
    assert storage.external_effect_count == 0
    raw = report.model_dump_json()
    assert "철수" not in raw
    assert "게임하자" not in raw


def test_s13_popup_interference_remains_suggestion_only() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))

    result = MockIntegratedScenarioRunner(manifest).run_s13_popup()

    assert result.success
    assert result.verified_assertions == (
        "suggestion_emitted",
        "no_mutation_observed",
    )
    assert result.external_effect_count == 0
