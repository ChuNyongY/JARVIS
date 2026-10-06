from pathlib import Path

from javis_core import (
    audit_scenario_capabilities,
    default_registry,
    load_integrated_scenarios,
)
from javis_core.cli import main

MANIFEST = Path("evaluation/integrated_scenarios.json").resolve()


def test_all_manifest_requirements_are_cataloged_without_turning_gates_into_actions() -> None:
    report = audit_scenario_capabilities(
        load_integrated_scenarios(MANIFEST),
        default_registry(),
    )

    assert report.catalog_complete
    assert report.required_count == 23
    assert report.missing_capabilities == ()
    assert report.local_control_capabilities == (
        "intent.addressedness_gate",
        "intent.untrusted_content_gate",
    )
    assert "package.install" in report.registered_action_capabilities
    assert not set(report.local_control_capabilities) & set(
        report.registered_action_capabilities
    )


def test_scenario_capability_audit_cli(capsys: object) -> None:
    assert main(["audit-scenario-capabilities", str(MANIFEST)]) == 0
