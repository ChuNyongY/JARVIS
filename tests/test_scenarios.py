from pathlib import Path

from javis_core import load_intent_scenarios, run_intent_scenarios


def test_bundled_intent_contract_scenarios_pass() -> None:
    scenarios = load_intent_scenarios(Path("evaluation/intent_scenarios.json"))
    report = run_intent_scenarios(scenarios)

    assert report.total == 15
    assert report.passed == 15
    assert report.accuracy == 1.0
    assert report.failures == ()
