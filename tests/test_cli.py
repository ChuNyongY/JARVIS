import getpass
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from pytest import CaptureFixture, MonkeyPatch

from javis_core import (
    AlwaysOnState,
    AutostartStatus,
    BenchmarkRunOutcome,
    ForegroundSnapshot,
    ProductHostState,
    ProductHostStatusSnapshot,
    RiskLevel,
    RuntimeStatusSnapshot,
    TrialResult,
)
from javis_core import cli as cli_module
from javis_core.cli import build_parser, main
from javis_core.credential_store import CredentialStatus
from javis_core.package_manifest import write_package_manifest


def test_product_service_commands_require_explicit_paths() -> None:
    parser = build_parser()
    checked = parser.parse_args(
        ("check-product-service", "C:/project", "C:/project/config.json", "C:/key")
    )
    started = parser.parse_args(
        ("run-product-host", "C:/project", "C:/project/config.json", "C:/key")
    )
    tray = parser.parse_args(
        ("run-product-tray", "C:/project", "C:/project/config.json", "C:/key")
    )
    autostart = parser.parse_args(
        ("install-autostart", "C:/project", "C:/project/config.json", "C:/key")
    )

    assert checked.command == "check-product-service"
    assert started.command == "run-product-host"
    assert tray.command == "run-product-tray"
    assert autostart.command == "install-autostart"


def test_credential_product_commands_do_not_accept_a_secret_path() -> None:
    parser = build_parser()

    for command in (
        "preflight-product-host-credential",
        "check-product-service-credential",
        "run-product-host-credential",
        "run-product-tray-credential",
        "install-autostart-credential",
    ):
        parsed = parser.parse_args((command, "C:/project", "C:/project/config.json"))
        assert parsed.command == command
        assert not hasattr(parsed, "nim_secret")


def test_collect_existing_p2_command_records_and_resumes_supported_cells(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    class S03Evidence:
        scenario_id = "S03"
        success = True
        verified_assertions = (
            "expected_pdf_identity_matches",
            "viewer_process_exists",
        )

    class S03Runner:
        def run(self) -> S03Evidence:
            return S03Evidence()

    class S02Evidence:
        scenario_id = "S02"
        success = True
        verified_assertions = ("volume_within_tolerance",)

    class S02Runner:
        def run(self) -> S02Evidence:
            return S02Evidence()

        def run_app_delay(self) -> S02Evidence:
            return S02Evidence()

    class S01Evidence:
        scenario_id = "S01"
        success = True
        verified_assertions = (
            "registered_browser_process_exists",
            "window_identity_matches",
        )

    class S01FailureEvidence:
        scenario_id = "S01"
        success = False
        verified_assertions: tuple[str, ...] = ()

    class S01Runner:
        def run(self) -> S01Evidence:
            return S01Evidence()

        def run_app_launch_failure(self) -> S01FailureEvidence:
            return S01FailureEvidence()

    original_runner = cli_module.ExistingP2ScenarioRunner
    monkeypatch.setattr(
        cli_module,
        "ExistingP2ScenarioRunner",
        lambda manifest, *, project_root: original_runner(
            manifest,
            project_root=project_root,
            s01_runner=S01Runner(),
            s02_runner=S02Runner(),
            s03_runner=S03Runner(),
        ),
    )

    class FakeVolume:
        def __init__(self) -> None:
            self.value = 55.0

        def read_percent(self) -> float:
            return self.value

        def set_percent(self, percent: float) -> None:
            self.value = percent

    original_host_runner = cli_module.NonVerifyingHostConditionRunner
    monkeypatch.setattr(
        cli_module,
        "NonVerifyingHostConditionRunner",
        lambda manifest, *, project_root: original_host_runner(
            manifest,
            project_root=project_root,
            volume_backend_factory=FakeVolume,
        ),
    )

    class FakeProcessRunner:
        def supports(self, cell, task) -> bool:
            return cell.condition.value in {"B1", "P1"} and task.scenario_id == "S01"

        def run(self, cell, task) -> BenchmarkRunOutcome:
            success = task.fault_profile.value == "NONE"
            confirmed = cell.condition.value == "B1"
            return BenchmarkRunOutcome(
                result=TrialResult(
                    trial_id=cell.run_id,
                    intended_command=task.intended_command,
                    ground_truth_risk=task.ground_truth_risk,
                    predicted_risk=(task.ground_truth_risk if not confirmed else RiskLevel.R0),
                    executed=success,
                    approval_required=confirmed,
                    approval_obtained=confirmed,
                    task_success=success,
                    verifier_declared_success=False,
                    actual_success=success,
                    confirmations=1 if confirmed else 0,
                    latency_ms={"total": 1.0},
                ),
                assertion_results={
                    assertion: success for assertion in task.expected_assertions
                },
            )

    monkeypatch.setattr(
        cli_module,
        "NonVerifyingProcessConditionRunner",
        lambda _manifest, *, project_root: FakeProcessRunner(),
    )

    class FakeMemoryPdfRunner:
        def supports(self, cell, task) -> bool:
            return cell.condition.value in {"B1", "P1"} and task.scenario_id == "S03"

        def run(self, cell, task) -> BenchmarkRunOutcome:
            success = task.fault_profile.value == "NONE"
            confirmed = cell.condition.value == "B1"
            return BenchmarkRunOutcome(
                result=TrialResult(
                    trial_id=cell.run_id,
                    intended_command=task.intended_command,
                    ground_truth_risk=task.ground_truth_risk,
                    predicted_risk=(task.ground_truth_risk if not confirmed else RiskLevel.R0),
                    executed=success,
                    approval_required=confirmed,
                    approval_obtained=confirmed,
                    task_success=success,
                    verifier_declared_success=False,
                    actual_success=success,
                    confirmations=1 if confirmed else 0,
                    latency_ms={"total": 1.0},
                ),
                assertion_results={
                    assertion: success for assertion in task.expected_assertions
                },
            )

    monkeypatch.setattr(
        cli_module,
        "NonVerifyingMemoryPdfConditionRunner",
        lambda _manifest, *, project_root: FakeMemoryPdfRunner(),
    )
    project = Path.cwd().resolve()
    collection = (tmp_path / "collection").resolve()
    arguments = (
        "collect-existing-p2-benchmark",
        str(project / "evaluation" / "benchmark_tasks.json"),
        str(project / "evaluation" / "integrated_scenarios.json"),
        str(project),
        str(collection),
    )

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first == {
        "status": "COLLECTED",
        "collected_this_run": 22,
        "completed": 22,
        "ready": 0,
        "environment_unavailable": 50,
        "runner_unavailable": 48,
        "complete": False,
    }

    assert main(arguments) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["collected_this_run"] == 0
    assert resumed["completed"] == 22

    available_collection = (tmp_path / "available-collection").resolve()
    available_arguments = (
        "collect-available-benchmark",
        str(project / "evaluation" / "benchmark_tasks.json"),
        str(project / "evaluation" / "integrated_scenarios.json"),
        str(project),
        str(available_collection),
    )
    assert main(available_arguments) == 0
    available = json.loads(capsys.readouterr().out)
    assert available == {
        "status": "COLLECTED",
        "collected_this_run": 70,
        "completed": 70,
        "ready": 0,
        "environment_unavailable": 50,
        "runner_unavailable": 0,
        "complete": False,
    }

    assert main(available_arguments) == 0
    available_resumed = json.loads(capsys.readouterr().out)
    assert available_resumed["collected_this_run"] == 0
    assert available_resumed["completed"] == 70


def test_autostart_status_command_is_read_only(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    calls: list[str] = []

    class Registration:
        def status(self) -> AutostartStatus:
            calls.append("status")
            return AutostartStatus.ABSENT

    monkeypatch.setattr(
        cli_module,
        "_product_autostart_registration",
        lambda *_paths: Registration(),
    )

    result = main(("autostart-status", "C:/project", "C:/config", "C:/secret"))

    assert result == 0
    assert calls == ["status"]
    assert json.loads(capsys.readouterr().out) == {"status": "ABSENT"}


def test_frozen_autostart_targets_windowless_tray_executable(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    console = root / "JAVIS.exe"
    tray = root / "JAVIS-Tray.exe"
    config = root / "config.json"
    secret = root / "NIM.txt"
    for path in (console, tray, config, secret):
        path.write_bytes(b"fixture")
    observed: dict[str, object] = {}

    class Registration:
        def __init__(
            self,
            target: Path,
            arguments: tuple[str, ...],
            working_directory: Path,
        ) -> None:
            observed.update(
                target=target,
                arguments=arguments,
                working_directory=working_directory,
            )

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(console))
    monkeypatch.setattr(cli_module, "StartupShortcutRegistrar", Registration)

    cli_module._product_autostart_registration(root, config, secret)

    assert observed["target"] == tray.resolve()
    assert observed["arguments"] == (
        "run-product-tray",
        str(root),
        str(config),
        str(secret),
    )


def test_frozen_credential_autostart_contains_no_secret_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    console = root / "JAVIS.exe"
    tray = root / "JAVIS-Tray.exe"
    config = root / "config.json"
    for path in (console, tray, config):
        path.write_bytes(b"fixture")
    observed: dict[str, object] = {}

    class Registration:
        def __init__(
            self,
            target: Path,
            arguments: tuple[str, ...],
            working_directory: Path,
        ) -> None:
            observed.update(
                target=target,
                arguments=arguments,
                working_directory=working_directory,
            )

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(console))
    monkeypatch.setattr(cli_module, "StartupShortcutRegistrar", Registration)

    cli_module._product_credential_autostart_registration(root, config)

    assert observed["target"] == tray.resolve()
    assert observed["arguments"] == (
        "run-product-tray-credential",
        str(root),
        str(config),
    )
    assert "NIM.txt" not in " ".join(observed["arguments"])  # type: ignore[arg-type]


def test_product_tray_coordinates_status_stop_exit_and_final_cleanup(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    stops: list[str] = []
    shown: list[RuntimeStatusSnapshot] = []
    runtime_snapshot = RuntimeStatusSnapshot(state=AlwaysOnState.IDLE)

    class Lifecycle:
        def start(self) -> ProductHostStatusSnapshot:
            return ProductHostStatusSnapshot(
                state=ProductHostState.RUNNING,
                reason="CAPTURE_STARTED",
                sequence=1,
            )

        def stop(self) -> ProductHostStatusSnapshot:
            stops.append("stop")
            return ProductHostStatusSnapshot(
                state=ProductHostState.STOPPED,
                reason="HOST_STOPPED",
                sequence=len(stops) + 1,
            )

    service = SimpleNamespace(
        lifecycle=Lifecycle(),
        runtime=SimpleNamespace(
            runtime=SimpleNamespace(status_snapshot=lambda: runtime_snapshot)
        ),
    )

    class Tray:
        def __init__(self, **callbacks: object) -> None:
            self._callbacks = callbacks

        def run(self) -> None:
            for name in ("show_status", "stop_agent", "exit_agent"):
                callback = self._callbacks[name]
                assert callable(callback)
                callback()

    monkeypatch.setattr(cli_module, "build_windows_product_service", lambda *_: service)
    monkeypatch.setattr(cli_module, "WindowsTrayController", Tray)
    monkeypatch.setattr(cli_module, "show_runtime_status_message", shown.append)

    result = main(("run-product-tray", "C:/project", "C:/config", "C:/secret"))

    assert result == 0
    assert shown == [runtime_snapshot]
    assert stops == ["stop", "stop", "stop"]
    assert json.loads(capsys.readouterr().out)["state"] == "STOPPED"


def test_provision_secret_command_does_not_echo_key(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "nvapi-private-key")
    monkeypatch.setattr(
        cli_module,
        "provision_nim_secret",
        lambda _root, key: observed.append(key.get_secret_value()),
    )

    result = main(("provision-nim-secret", str(tmp_path.resolve())))

    raw = capsys.readouterr().out
    assert result == 0
    assert observed == ["nvapi-private-key"]
    assert "private" not in raw
    assert json.loads(raw) == {"status": "PROVISIONED"}


def test_credential_management_commands_never_echo_key(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "nvapi-private-key")
    monkeypatch.setattr(
        cli_module,
        "provision_nim_credential",
        lambda key: observed.append(key.get_secret_value()),
    )
    monkeypatch.setattr(
        cli_module,
        "nim_credential_status",
        lambda: CredentialStatus.PRESENT,
    )
    monkeypatch.setattr(cli_module, "remove_nim_credential", lambda: True)

    assert main(("provision-nim-credential",)) == 0
    provisioned = capsys.readouterr().out
    assert json.loads(provisioned) == {"status": "PROVISIONED"}
    assert "private" not in provisioned
    assert observed == ["nvapi-private-key"]

    assert main(("nim-credential-status",)) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "PRESENT"}
    assert main(("remove-nim-credential",)) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "REMOVED"}


def test_verify_package_command_reports_only_summary(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    root = tmp_path.resolve()
    (root / "JAVIS.exe").write_bytes(b"fixture")
    write_package_manifest(root, "0.1.0")

    result = main(("verify-package", str(root)))

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "VERIFIED",
        "version": "0.1.0",
        "file_count": 1,
    }


def test_evaluate_command_outputs_report(capsys: CaptureFixture[str]) -> None:
    path = Path("evaluation/example_trials.json")

    result = main(("evaluate", str(path)))

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 0
    assert report["trial_count"] == 3
    assert report["task_success_rate"] == 0.5


def test_evaluate_wakeword_corpus_command_uses_local_bundle_and_report(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    bundle = object()
    manifest = object()
    detector = object()
    observed: dict[str, object] = {}

    monkeypatch.setattr(cli_module, "load_wakeword_bundle", lambda path: bundle)
    monkeypatch.setattr(
        cli_module.OpenWakeWordDetector,
        "from_local_bundle",
        lambda loaded: detector if loaded is bundle else None,
    )
    monkeypatch.setattr(
        cli_module,
        "load_wakeword_corpus_manifest",
        lambda path: manifest,
    )

    def evaluate(loaded, root, loaded_detector, *, threshold):  # type: ignore[no-untyped-def]
        observed.update(
            manifest=loaded,
            root=root,
            detector=loaded_detector,
            threshold=threshold,
        )
        return SimpleNamespace(
            model_dump_json=lambda **_options: '{"false_acceptance_rate":0.0}'
        )

    monkeypatch.setattr(cli_module, "evaluate_wakeword_corpus", evaluate)

    result = main(
        (
            "evaluate-wakeword-corpus",
            "C:/corpus/manifest.json",
            "C:/corpus",
            "C:/models/manifest.json",
            "--threshold",
            "0.6",
        )
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {"false_acceptance_rate": 0.0}
    assert observed == {
        "manifest": manifest,
        "root": Path("C:/corpus"),
        "detector": detector,
        "threshold": 0.6,
    }


def test_smoke_system_info_runs_full_read_only_loop(capsys: CaptureFixture[str]) -> None:
    result = main(("smoke-system-info",))

    captured = capsys.readouterr()
    outcome = json.loads(captured.out)
    assert result == 0
    assert outcome["status"] == "SUCCEEDED"
    assert outcome["completed_actions"] == 1


def test_validate_integrated_scenarios_outputs_only_fixed_ids(
    capsys: CaptureFixture[str],
) -> None:
    result = main(
        ("validate-integrated-scenarios", "evaluation/integrated_scenarios.json")
    )

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report == {
        "schema_version": "1.0",
        "scenario_count": 15,
        "scenario_ids": [f"S{index:02d}" for index in range(1, 16)],
        "valid": True,
    }


def test_run_host_safe_scenarios_outputs_privacy_minimized_report(
    capsys: CaptureFixture[str],
) -> None:
    result = main(
        (
            "run-host-safe-scenarios",
            "evaluation/integrated_scenarios.json",
            str(Path.cwd()),
        )
    )

    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert result == 0
    assert report["all_passed"]
    assert report["passed"] == 3
    assert [item["scenario_id"] for item in report["results"]] == ["S11", "S14", "S15"]
    assert "hostname" not in raw
    assert str(Path.cwd()) not in raw


def test_run_mock_scenarios_never_reports_message_content(
    capsys: CaptureFixture[str],
) -> None:
    result = main(("run-mock-scenarios", "evaluation/integrated_scenarios.json"))

    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert result == 0
    assert report["all_passed"]
    assert [item["scenario_id"] for item in report["results"]] == ["S06", "S13"]
    assert all(item["external_effect_count"] == 0 for item in report["results"])
    assert "철수" not in raw
    assert "게임하자" not in raw


def test_validate_app_registry_outputs_verified_entries(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    registry = tmp_path / "apps.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "apps": [
                    {
                        "app_id": "safe",
                        "executable": str(executable.resolve()),
                        "sha256": hashlib.sha256(b"fixture").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(("validate-app-registry", str(registry))) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["safe"]["executable"] == str(executable.resolve())
    assert not report["safe"]["publisher_verified"]


def test_analyze_study_command_outputs_privacy_minimized_report(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    rows = [
        {
            "participant_id": "private-participant",
            "scenario_id": "noise-1",
            "condition": condition,
            "intended_command": False,
            "ground_truth_risk": 0,
            "executed": condition == "B0",
            "approval_obtained": False,
            "task_success": False,
            "confirmations": 0,
            "total_latency_ms": 10,
        }
        for condition in ("B0", "B1", "P1", "P2")
    ]
    path = tmp_path / "study.json"
    path.write_text(json.dumps(rows), encoding="utf-8")

    assert main(("analyze-study", str(path))) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["block_count"] == 1
    assert "private-participant" not in raw


def test_foreground_context_command_omits_content_and_paths(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    registry = tmp_path / "apps.json"
    registry.write_text('{"schema_version":"1.0","apps":[]}', encoding="utf-8")

    class Collector:
        def __init__(self, apps: object) -> None:
            assert apps == {}

        def capture(self) -> ForegroundSnapshot:
            from datetime import UTC, datetime

            return ForegroundSnapshot(
                hwnd=10,
                pid=20,
                app_id=None,
                identity_verified=False,
                captured_at=datetime(2026, 9, 22, tzinfo=UTC),
            )

    monkeypatch.setattr(cli_module, "ForegroundContextCollector", Collector)
    assert main(("foreground-context", str(registry))) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["pid"] == 20
    assert "title" not in raw
    assert "executable" not in raw
    assert str(tmp_path) not in raw
