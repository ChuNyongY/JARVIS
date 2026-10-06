from __future__ import annotations

import json
from pathlib import Path

import pytest

from javis_core.integrated_scenarios import (
    ExpectedAuthorization,
    ScenarioEnvironment,
    ScenarioManifestError,
    load_integrated_scenarios,
)


def _bundled_document() -> dict[str, object]:
    return json.loads(Path("evaluation/integrated_scenarios.json").read_text(encoding="utf-8"))


def test_bundled_manifest_preserves_all_original_scenarios_and_boundaries() -> None:
    manifest = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json"))

    assert [scenario.scenario_id for scenario in manifest.scenarios] == [
        f"S{index:02d}" for index in range(1, 16)
    ]
    by_id = {scenario.scenario_id: scenario for scenario in manifest.scenarios}
    assert {by_id[key].environment for key in ("S08", "S09", "S10", "S12")} == {
        ScenarioEnvironment.SNAPSHOT_VM_ONLY
    }
    assert by_id["S06"].environment is ScenarioEnvironment.MOCK_ONLY
    assert by_id["S10"].expected_authorization is ExpectedAuthorization.STRONG_R4
    assert "automatic_commit" in by_id["S10"].forbidden_effects
    assert by_id["S13"].expected_authorization is ExpectedAuthorization.SUGGEST_ONLY
    assert by_id["S14"].expected_authorization is ExpectedAuthorization.NO_ACTION
    assert by_id["S15"].expected_authorization is ExpectedAuthorization.NO_ACTION


def test_manifest_rejects_missing_or_reordered_fixed_scenario(tmp_path: Path) -> None:
    document = _bundled_document()
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    scenarios[0], scenarios[1] = scenarios[1], scenarios[0]
    path = tmp_path / "reordered.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioManifestError, match="invalid"):
        load_integrated_scenarios(path)


def test_manifest_rejects_red_scenario_on_personal_host(tmp_path: Path) -> None:
    document = _bundled_document()
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[5]
    assert isinstance(scenario, dict)
    scenario["environment"] = "HOST_SAFE"
    path = tmp_path / "unsafe-red.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioManifestError, match="invalid"):
        load_integrated_scenarios(path)


def test_manifest_rejects_vm_scenario_downgrade(tmp_path: Path) -> None:
    document = _bundled_document()
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[7]
    assert isinstance(scenario, dict)
    scenario["environment"] = "MOCK_ONLY"
    path = tmp_path / "downgraded.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioManifestError, match="invalid"):
        load_integrated_scenarios(path)


def test_manifest_rejects_reset_without_automatic_commit_barrier(tmp_path: Path) -> None:
    document = _bundled_document()
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[9]
    assert isinstance(scenario, dict)
    scenario["forbidden_effects"] = ["host_reset"]
    path = tmp_path / "unsafe-reset.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioManifestError, match="invalid"):
        load_integrated_scenarios(path)


def test_manifest_rejects_duplicate_json_key(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"1.0","schema_version":"1.0","scenarios":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ScenarioManifestError, match="invalid"):
        load_integrated_scenarios(path)


def test_manifest_rejects_non_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"schema_version":"1.0","scenarios":NaN}', encoding="utf-8")

    with pytest.raises(ScenarioManifestError, match="invalid"):
        load_integrated_scenarios(path)


def test_manifest_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (128 * 1024 + 1))

    with pytest.raises(ScenarioManifestError, match="size"):
        load_integrated_scenarios(path)
