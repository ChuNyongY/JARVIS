from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from .app_registry import AppRegistryLoader
from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink, JsonlAuditSink
from .autostart import StartupShortcutRegistrar
from .benchmark_b0_runner import DirectB0MockScenarioRunner
from .benchmark_b1_safety_runner import B1SafetyConditionRunner
from .benchmark_collection import (
    BenchmarkExperimentCollector,
    CollectionAttemptStatus,
)
from .benchmark_nonverifying_fixture_runner import NonVerifyingFixtureConditionRunner
from .benchmark_nonverifying_host_runner import NonVerifyingHostConditionRunner
from .benchmark_nonverifying_memory_pdf_runner import NonVerifyingMemoryPdfConditionRunner
from .benchmark_nonverifying_process_runner import NonVerifyingProcessConditionRunner
from .benchmark_nonverifying_runner import NonVerifyingMockConditionRunner
from .benchmark_p1_safety_runner import P1SafetyConditionRunner
from .benchmark_p2_runner import ExistingP2ScenarioRunner
from .benchmark_results import (
    evaluate_benchmark_observations,
    load_benchmark_observations,
)
from .benchmark_runner_chain import BenchmarkConditionRunnerChain
from .benchmark_snapshot_runner import (
    SnapshotBenchmarkConditionRunner,
    build_snapshot_benchmark_requests,
    write_snapshot_benchmark_requests,
)
from .benchmark_tasks import load_benchmark_tasks, validate_benchmark_contract
from .credential_store import (
    load_nim_credential,
    nim_credential_status,
    provision_nim_credential,
    remove_nim_credential,
)
from .doctor import run_doctor
from .evaluation import evaluate_trials, load_trials
from .execution_broker import ExecutionBroker
from .fixture_scenarios import FixtureIntegratedScenarioRunner
from .foreground_context import ForegroundContextCollector
from .host_safe_scenarios import HostSafeScenarioRunner
from .integrated_scenarios import ScenarioEnvironment, load_integrated_scenarios
from .microphone_probe import probe_microphones
from .mock_scenarios import MockIntegratedScenarioRunner
from .models import Action, Plan, PolicyContext, Postcondition
from .native_readonly import NativeReadOnlyExecutor
from .orchestrator import Orchestrator
from .package_manifest import verify_package_manifest
from .policy import PolicyEngine
from .product_host import (
    load_product_host_config,
    run_product_preflight,
    run_product_preflight_with_secret_loader,
)
from .product_service import (
    build_windows_product_service,
    build_windows_product_service_with_secret_loader,
)
from .registry import default_registry
from .release_audit import audit_windows_release
from .safe_evaluation import SafeIntegratedEvaluationRunner
from .scenario_capabilities import audit_scenario_capabilities
from .scenarios import load_intent_scenarios, run_intent_scenarios
from .secret_provisioning import provision_nim_secret
from .skill_runtime import LocalSkillCatalog, SkillOutcome, SkillProposal
from .snapshot_batch import (
    SNAPSHOT_SCENARIO_IDS,
    HmacSha256SnapshotVerifier,
    SnapshotEvidenceBatchEvaluator,
    load_snapshot_hmac_key,
)
from .snapshot_vm import SnapshotEvidenceError, SnapshotEvidenceGate, load_snapshot_manifests
from .status_surface import run_tk_status_hud
from .study_analysis import StudyCondition, analyze_paired_study, load_study_observations
from .user_validation import (
    ConsoleConsentProvider,
    UserValidationSink,
    UserValidationWorkflow,
)
from .verifier import PredicateVerifier
from .voice_adapters import (
    OpenWakeWordDetector,
    RepCnnWakeWordDetector,
    SoundDeviceFrameSource,
)
from .vosk_bundle import load_vosk_bundle
from .vosk_wakeword import VoskKoreanWakeWordDetector
from .wakeword_bundle import load_wakeword_bundle
from .wakeword_calibration import (
    WakeWordCalibrationStatus,
    calibrate_wakeword_threshold,
)
from .wakeword_corpus_capture import (
    ConsoleWakeWordCaptureConsent,
    ConsoleWakeWordCaptureReadiness,
    WakeWordCaptureError,
    capture_wakeword_corpus,
    load_wakeword_capture_plan,
)
from .wakeword_evaluation import (
    ResettableWakeWordDetector,
    evaluate_wakeword_corpus,
    load_wakeword_corpus_manifest,
)
from .windows_tray import WindowsTrayController, show_runtime_status_message
from .windows_user_validation import ConsoleHumanAssessment, WindowsUserValidationRunners
from .worker_process import EXPLORER_WORKER_TOKEN, TTS_WORKER_TOKEN, UIA_WORKER_TOKEN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="javis")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="Aggregate experiment JSON")
    evaluate.add_argument("path", type=Path)

    intent = subparsers.add_parser("intent-scenarios", help="Run Intent Gate scenarios")
    intent.add_argument("path", type=Path)

    integrated = subparsers.add_parser(
        "validate-integrated-scenarios",
        help="Validate the fixed S01-S15 execution and isolation contract",
    )
    integrated.add_argument("path", type=Path)
    benchmark = subparsers.add_parser(
        "validate-benchmark-tasks",
        help="Validate the fixed 30-50 task benchmark against S01-S15",
    )
    benchmark.add_argument("tasks", type=Path)
    benchmark.add_argument("scenarios", type=Path)
    benchmark_results = subparsers.add_parser(
        "analyze-benchmark-results",
        help="Validate the complete B0/B1/P1/P2 grid and evaluate P2 targets",
    )
    benchmark_results.add_argument("tasks", type=Path)
    benchmark_results.add_argument("scenarios", type=Path)
    benchmark_results.add_argument("observations", type=Path)
    benchmark_results.add_argument("evidence_directory", type=Path)
    collect_p2 = subparsers.add_parser(
        "collect-existing-p2-benchmark",
        help="Collect supported exact P2 scenario observations with resumable evidence",
    )
    collect_p2.add_argument("tasks", type=Path)
    collect_p2.add_argument("scenarios", type=Path)
    collect_p2.add_argument("project_root", type=Path)
    collect_p2.add_argument("collection_root", type=Path)
    collect_available = subparsers.add_parser(
        "collect-available-benchmark",
        help="Collect genuine B0 mock and P2 observations available on this host",
    )
    collect_available.add_argument("tasks", type=Path)
    collect_available.add_argument("scenarios", type=Path)
    collect_available.add_argument("project_root", type=Path)
    collect_available.add_argument("collection_root", type=Path)
    export_snapshot_requests = subparsers.add_parser(
        "export-snapshot-benchmark-requests",
        help="Export the exact VM-only benchmark cells for an external snapshot harness",
    )
    export_snapshot_requests.add_argument("tasks", type=Path)
    export_snapshot_requests.add_argument("scenarios", type=Path)
    export_snapshot_requests.add_argument("destination", type=Path)
    export_snapshot_requests.add_argument("--repetitions", type=int, default=1)
    collect_snapshot = subparsers.add_parser(
        "collect-snapshot-benchmark",
        help="Verify signed snapshot-VM evidence and merge it into the exact benchmark ledger",
    )
    collect_snapshot.add_argument("tasks", type=Path)
    collect_snapshot.add_argument("scenarios", type=Path)
    collect_snapshot.add_argument("collection_root", type=Path)
    collect_snapshot.add_argument("evidence_directory", type=Path)
    collect_snapshot.add_argument("key_file", type=Path)
    collect_snapshot.add_argument("key_id")
    collect_snapshot.add_argument("vm_provider")
    collect_snapshot.add_argument("base_snapshot_id")
    collect_snapshot.add_argument("guest_machine_id")
    collect_snapshot.add_argument("--repetitions", type=int, default=1)
    host_safe = subparsers.add_parser(
        "run-host-safe-scenarios",
        help="Run the non-mutating S11/S14/S15 contract subset",
    )
    host_safe.add_argument("path", type=Path)
    host_safe.add_argument("project_root", type=Path)
    mock_scenarios = subparsers.add_parser(
        "run-mock-scenarios",
        help="Run external-effect scenarios against privacy-minimized mock sinks",
    )
    mock_scenarios.add_argument("path", type=Path)
    fixture_scenarios = subparsers.add_parser(
        "run-fixture-scenarios",
        help="Run S05 inside a disposable local fixture sandbox",
    )
    fixture_scenarios.add_argument("path", type=Path)
    capability_audit = subparsers.add_parser(
        "audit-scenario-capabilities",
        help="Audit S01-S15 action capability and local control catalog coverage",
    )
    capability_audit.add_argument("path", type=Path)
    safe_evaluation = subparsers.add_parser(
        "run-safe-evaluation",
        help="Run all currently safe evidence and report explicit S01-S15 gaps",
    )
    safe_evaluation.add_argument("path", type=Path)
    safe_evaluation.add_argument("project_root", type=Path)
    snapshot_batch = subparsers.add_parser(
        "verify-snapshot-batch",
        help="Verify signed S08/S09/S10/S12 evidence without controlling a VM",
    )
    snapshot_batch.add_argument("manifests", type=Path)
    snapshot_batch.add_argument("evidence_directory", type=Path)
    snapshot_batch.add_argument("key_file", type=Path)
    snapshot_batch.add_argument("key_id")

    study = subparsers.add_parser(
        "analyze-study",
        help="Analyze paired B0/B1/P1/P2 study observations",
    )
    study.add_argument("path", type=Path)

    wakeword_evaluation = subparsers.add_parser(
        "evaluate-wakeword-corpus",
        help="Evaluate local wake-word FAR/FRR without opening a microphone",
    )
    wakeword_evaluation.add_argument("manifest", type=Path)
    wakeword_evaluation.add_argument("corpus_root", type=Path)
    wakeword_evaluation.add_argument("wakeword_manifest", type=Path)
    wakeword_evaluation.add_argument("--threshold", type=float, default=0.5)

    repcnn_evaluation = subparsers.add_parser(
        "evaluate-repcnn-wakeword-corpus",
        help="Evaluate a pinned local RepCNN wake-word model without a microphone",
    )
    repcnn_evaluation.add_argument("manifest", type=Path)
    repcnn_evaluation.add_argument("corpus_root", type=Path)
    repcnn_evaluation.add_argument("model_path", type=Path)
    repcnn_evaluation.add_argument("model_sha256")
    repcnn_evaluation.add_argument("--threshold", type=float, default=0.7)

    vosk_evaluation = subparsers.add_parser(
        "evaluate-vosk-wakeword-corpus",
        help="Evaluate the experimental pinned Korean Vosk model without a microphone",
    )
    vosk_evaluation.add_argument("manifest", type=Path)
    vosk_evaluation.add_argument("corpus_root", type=Path)
    vosk_evaluation.add_argument("vosk_manifest", type=Path)

    wakeword_calibration = subparsers.add_parser(
        "calibrate-wakeword-threshold",
        help="Select an openWakeWord threshold on a separate calibration corpus",
    )
    wakeword_calibration.add_argument("manifest", type=Path)
    wakeword_calibration.add_argument("corpus_root", type=Path)
    wakeword_calibration.add_argument("wakeword_manifest", type=Path)
    wakeword_calibration.add_argument("--far-ceiling", type=float, default=0.05)

    repcnn_calibration = subparsers.add_parser(
        "calibrate-repcnn-wakeword-threshold",
        help="Select a pinned RepCNN threshold on a separate calibration corpus",
    )
    repcnn_calibration.add_argument("manifest", type=Path)
    repcnn_calibration.add_argument("corpus_root", type=Path)
    repcnn_calibration.add_argument("model_path", type=Path)
    repcnn_calibration.add_argument("model_sha256")
    repcnn_calibration.add_argument("--far-ceiling", type=float, default=0.05)

    wakeword_capture = subparsers.add_parser(
        "capture-wakeword-corpus",
        help="Record a consent-bound local wake-word corpus interactively",
    )
    wakeword_capture.add_argument("plan", type=Path)
    wakeword_capture.add_argument("destination", type=Path)

    smoke = subparsers.add_parser(
        "smoke-system-info",
        help="Run a read-only system-info action through the full safety loop",
    )
    smoke.add_argument("--audit-path", type=Path)

    skill_create = subparsers.add_parser(
        "skill-create",
        help="Create a local skill draft; drafts cannot be used until approved",
    )
    skill_create.add_argument("catalog", type=Path)
    skill_create.add_argument("key")
    skill_create.add_argument("title")
    skill_create.add_argument("description")
    skill_create.add_argument("--trigger", action="append", required=True)
    skill_create.add_argument("--step", action="append", required=True)
    skill_create.add_argument("--capability", action="append", required=True)

    skill_approve = subparsers.add_parser(
        "skill-approve",
        help="Promote one local skill draft to a runnable verified skill",
    )
    skill_approve.add_argument("catalog", type=Path)
    skill_approve.add_argument("skill_id")

    skill_search = subparsers.add_parser(
        "skill-search",
        help="Search verified local skills for a user request",
    )
    skill_search.add_argument("catalog", type=Path)
    skill_search.add_argument("query")

    skill_revise = subparsers.add_parser(
        "skill-revise",
        help="Create a new draft version of a verified skill",
    )
    skill_revise.add_argument("catalog", type=Path)
    skill_revise.add_argument("skill_id")
    skill_revise.add_argument("key")
    skill_revise.add_argument("title")
    skill_revise.add_argument("description")
    skill_revise.add_argument("--trigger", action="append", required=True)
    skill_revise.add_argument("--step", action="append", required=True)
    skill_revise.add_argument("--capability", action="append", required=True)

    skill_use = subparsers.add_parser(
        "skill-record-use",
        help="Record an independently verified result for a skill version",
    )
    skill_use.add_argument("catalog", type=Path)
    skill_use.add_argument("skill_id")
    skill_use.add_argument("outcome", choices=[item.value for item in SkillOutcome])
    skill_use.add_argument("detail_code")

    validate_apps = subparsers.add_parser(
        "validate-app-registry",
        help="Validate paths, hashes, and optional Authenticode publishers",
    )
    validate_apps.add_argument("path", type=Path)
    validate_apps.add_argument("--require-publisher", action="store_true")

    subparsers.add_parser(
        "probe-microphones",
        help="Read microphone capability metadata without recording audio",
    )
    doctor = subparsers.add_parser(
        "doctor",
        help="Validate local runtime integrity without launching apps or models",
    )
    doctor.add_argument("project_root", type=Path)
    foreground = subparsers.add_parser(
        "foreground-context",
        help="Report privacy-minimized foreground window identity",
    )
    foreground.add_argument("registry", type=Path)
    foreground.add_argument("--require-publisher", action="store_true")
    product_preflight = subparsers.add_parser(
        "preflight-product-host",
        help="Validate product config, protected NIM secret, and doctor before capture",
    )
    product_preflight.add_argument("project_root", type=Path)
    product_preflight.add_argument("config", type=Path)
    product_preflight.add_argument("nim_secret", type=Path)
    product_check = subparsers.add_parser(
        "check-product-service",
        help="Build the complete product service without opening the microphone or HUD",
    )
    product_check.add_argument("project_root", type=Path)
    product_check.add_argument("config", type=Path)
    product_check.add_argument("nim_secret", type=Path)
    product_run = subparsers.add_parser(
        "run-product-host",
        help="Start the microphone capture service and local status HUD",
    )
    product_run.add_argument("project_root", type=Path)
    product_run.add_argument("config", type=Path)
    product_run.add_argument("nim_secret", type=Path)
    product_tray = subparsers.add_parser(
        "run-product-tray",
        help="Start the microphone capture service with a Windows notification icon",
    )
    product_tray.add_argument("project_root", type=Path)
    product_tray.add_argument("config", type=Path)
    product_tray.add_argument("nim_secret", type=Path)
    for command, help_text in (
        (
            "preflight-product-host-credential",
            "Validate the product using the current user's Windows Credential Manager key",
        ),
        (
            "check-product-service-credential",
            "Build the product service using Windows Credential Manager",
        ),
        (
            "run-product-host-credential",
            "Start the product host using Windows Credential Manager",
        ),
        (
            "run-product-tray-credential",
            "Start the tray host using Windows Credential Manager",
        ),
    ):
        credential_product = subparsers.add_parser(command, help=help_text)
        credential_product.add_argument("project_root", type=Path)
        credential_product.add_argument("config", type=Path)
    for command, help_text in (
        ("autostart-status", "Report current-user JAVIS Startup registration status"),
        ("install-autostart", "Register the JAVIS tray host for current-user Startup"),
        ("remove-autostart", "Remove the owned current-user JAVIS Startup shortcut"),
    ):
        autostart = subparsers.add_parser(command, help=help_text)
        autostart.add_argument("project_root", type=Path)
        autostart.add_argument("config", type=Path)
        autostart.add_argument("nim_secret", type=Path)
    for command, help_text in (
        (
            "autostart-status-credential",
            "Report Credential Manager tray Startup registration status",
        ),
        (
            "install-autostart-credential",
            "Register the Credential Manager tray host for current-user Startup",
        ),
        (
            "remove-autostart-credential",
            "Remove the owned Credential Manager tray Startup shortcut",
        ),
    ):
        credential_autostart = subparsers.add_parser(command, help=help_text)
        credential_autostart.add_argument("project_root", type=Path)
        credential_autostart.add_argument("config", type=Path)
    provision_secret = subparsers.add_parser(
        "provision-nim-secret",
        help="Prompt without echo and create a protected NIM.txt in a product root",
    )
    provision_secret.add_argument("project_root", type=Path)
    subparsers.add_parser(
        "provision-nim-credential",
        help="Prompt without echo and store the NIM key in Windows Credential Manager",
    )
    subparsers.add_parser(
        "nim-credential-status",
        help="Report NIM Credential Manager presence without reading it to stdout",
    )
    subparsers.add_parser(
        "remove-nim-credential",
        help="Delete the fixed NIM key from Windows Credential Manager",
    )
    verify_package = subparsers.add_parser(
        "verify-package",
        help="Verify the exact files and SHA-256 package manifest",
    )
    verify_package.add_argument("package_root", type=Path)
    release_audit = subparsers.add_parser(
        "audit-windows-release",
        help="Require exact package integrity, Setup hash, and trusted publisher signatures",
    )
    release_audit.add_argument("package_root", type=Path)
    release_audit.add_argument("setup", type=Path)
    release_audit.add_argument("expected_publisher")
    user_validation = subparsers.add_parser(
        "run-user-validation",
        help="Run separately consented microphone, speaker, Hello, and STOP checks",
    )
    user_validation.add_argument("project_root", type=Path)
    user_validation.add_argument("config", type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.command == "evaluate":
        evaluation_report = evaluate_trials(load_trials(args.path))
        print(evaluation_report.model_dump_json(indent=2))
        return 0
    if args.command == "evaluate-wakeword-corpus":
        detector = OpenWakeWordDetector.from_local_bundle(
            load_wakeword_bundle(args.wakeword_manifest)
        )
        wakeword_report = evaluate_wakeword_corpus(
            load_wakeword_corpus_manifest(args.manifest),
            args.corpus_root,
            detector,
            threshold=args.threshold,
        )
        print(wakeword_report.model_dump_json(indent=2))
        return 0
    if args.command == "evaluate-repcnn-wakeword-corpus":
        repcnn_detector = RepCnnWakeWordDetector.from_local_model(
            args.model_path,
            args.model_sha256,
        )
        wakeword_report = evaluate_wakeword_corpus(
            load_wakeword_corpus_manifest(args.manifest),
            args.corpus_root,
            repcnn_detector,
            threshold=args.threshold,
        )
        print(wakeword_report.model_dump_json(indent=2))
        return 0
    if args.command == "evaluate-vosk-wakeword-corpus":
        vosk_detector = VoskKoreanWakeWordDetector.from_local_bundle(
            load_vosk_bundle(args.vosk_manifest)
        )
        wakeword_report = evaluate_wakeword_corpus(
            load_wakeword_corpus_manifest(args.manifest),
            args.corpus_root,
            vosk_detector,
            threshold=0.5,
        )
        print(wakeword_report.model_dump_json(indent=2))
        return 0
    if args.command == "capture-wakeword-corpus":
        capture_plan = load_wakeword_capture_plan(args.plan)
        consent_id = ConsoleWakeWordCaptureConsent().request(capture_plan)
        if consent_id is None:
            print(json.dumps({"status": "SKIPPED", "code": "CONSENT_NOT_GIVEN"}))
            return 1
        try:
            captured_manifest = capture_wakeword_corpus(
                capture_plan,
                args.destination,
                consent_id,
                SoundDeviceFrameSource,
                ConsoleWakeWordCaptureReadiness(),
            )
        except WakeWordCaptureError:
            print(json.dumps({"status": "ERROR", "code": "CAPTURE_FAILED"}))
            return 1
        print(
            json.dumps(
                {
                    "status": "CAPTURED",
                    "corpus_id": captured_manifest.corpus_id,
                    "sample_count": len(captured_manifest.samples),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command in {
        "calibrate-wakeword-threshold",
        "calibrate-repcnn-wakeword-threshold",
    }:
        calibration_detector: ResettableWakeWordDetector = (
            OpenWakeWordDetector.from_local_bundle(
                load_wakeword_bundle(args.wakeword_manifest)
            )
            if args.command == "calibrate-wakeword-threshold"
            else RepCnnWakeWordDetector.from_local_model(
                args.model_path,
                args.model_sha256,
            )
        )
        calibration = calibrate_wakeword_threshold(
            evaluate_wakeword_corpus(
                load_wakeword_corpus_manifest(args.manifest),
                args.corpus_root,
                calibration_detector,
                threshold=0.5,
            ),
            far_ceiling=args.far_ceiling,
        )
        print(calibration.model_dump_json(indent=2))
        return (
            0
            if calibration.status is WakeWordCalibrationStatus.SELECTED
            else 1
        )
    if args.command == "smoke-system-info":
        return _smoke_system_info(args.audit_path)
    if args.command == "skill-create":
        catalog = _skill_catalog(args.catalog)
        record = catalog.propose(_skill_proposal(args))
        print(record.model_dump_json(indent=2))
        return 0
    if args.command == "skill-approve":
        record = _skill_catalog(args.catalog).approve(UUID(args.skill_id))
        print(record.model_dump_json(indent=2))
        return 0
    if args.command == "skill-search":
        records = _skill_catalog(args.catalog).search(args.query)
        print(
            json.dumps(
                {
                    "query": args.query,
                    "matches": [item.model_dump(mode="json") for item in records],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "skill-revise":
        catalog = _skill_catalog(args.catalog)
        record = catalog.propose_revision(UUID(args.skill_id), _skill_proposal(args))
        print(record.model_dump_json(indent=2))
        return 0
    if args.command == "skill-record-use":
        catalog = _skill_catalog(args.catalog)
        use_record = catalog.record_use(
            catalog.require(UUID(args.skill_id)),
            SkillOutcome(args.outcome),
            args.detail_code,
        )
        print(use_record.model_dump_json(indent=2))
        return 0
    if args.command == "intent-scenarios":
        scenario_report = run_intent_scenarios(load_intent_scenarios(args.path))
        print(scenario_report.model_dump_json(indent=2))
        return 0 if not scenario_report.failures else 1
    if args.command == "validate-integrated-scenarios":
        manifest = load_integrated_scenarios(args.path)
        integrated_report = {
            "schema_version": manifest.schema_version,
            "scenario_count": len(manifest.scenarios),
            "scenario_ids": [scenario.scenario_id for scenario in manifest.scenarios],
            "valid": True,
        }
        print(json.dumps(integrated_report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate-benchmark-tasks":
        benchmark_manifest = load_benchmark_tasks(args.tasks)
        validate_benchmark_contract(
            benchmark_manifest,
            load_integrated_scenarios(args.scenarios),
            default_registry(),
        )
        benchmark_report = {
            "schema_version": benchmark_manifest.schema_version,
            "task_count": len(benchmark_manifest.tasks),
            "scenario_count": len({task.scenario_id for task in benchmark_manifest.tasks}),
            "fault_task_count": sum(
                task.fault_profile.value != "NONE" for task in benchmark_manifest.tasks
            ),
            "valid": True,
        }
        print(json.dumps(benchmark_report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze-benchmark-results":
        benchmark_manifest = load_benchmark_tasks(args.tasks)
        validate_benchmark_contract(
            benchmark_manifest,
            load_integrated_scenarios(args.scenarios),
            default_registry(),
        )
        benchmark_evaluation = evaluate_benchmark_observations(
            benchmark_manifest,
            load_integrated_scenarios(args.scenarios),
            load_benchmark_observations(args.observations),
            args.evidence_directory,
        )
        print(benchmark_evaluation.model_dump_json(indent=2))
        return 0 if benchmark_evaluation.all_p2_targets_met else 1
    if args.command == "collect-existing-p2-benchmark":
        project_root = _resolve_cli_path(args.project_root, directory=True)
        tasks = load_benchmark_tasks(args.tasks)
        scenarios = load_integrated_scenarios(args.scenarios)
        runner = ExistingP2ScenarioRunner(scenarios, project_root=project_root)
        collector = BenchmarkExperimentCollector(
            tasks,
            scenarios,
            args.collection_root,
            repetitions_per_condition=1,
            evaluator_id="javis-existing-p2",
            evaluator_version="2.0.0",
            runners={
                (StudyCondition.P2, environment): runner
                for environment in (
                    ScenarioEnvironment.HOST_SAFE,
                    ScenarioEnvironment.HOST_FIXTURE_SANDBOX,
                    ScenarioEnvironment.MOCK_ONLY,
                )
            },
            environment_available={
                ScenarioEnvironment.HOST_SAFE: True,
                ScenarioEnvironment.HOST_FIXTURE_SANDBOX: True,
                ScenarioEnvironment.MOCK_ONLY: True,
                ScenarioEnvironment.SNAPSHOT_VM_ONLY: False,
            },
        )
        collected = 0
        for _ in collector.schedule:
            if collector.readiness().ready == 0:
                break
            attempt = collector.collect_next()
            if attempt.status is not CollectionAttemptStatus.RECORDED:
                print(
                    json.dumps(
                        {"status": attempt.status.value, "collected_this_run": collected},
                        indent=2,
                    )
                )
                return 1
            collected += 1
        readiness = collector.readiness()
        print(
            json.dumps(
                {
                    "status": "COLLECTED",
                    "collected_this_run": collected,
                    "completed": readiness.completed,
                    "ready": readiness.ready,
                    "environment_unavailable": readiness.environment_unavailable,
                    "runner_unavailable": readiness.runner_unavailable,
                    "complete": readiness.complete,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "collect-available-benchmark":
        project_root = _resolve_cli_path(args.project_root, directory=True)
        tasks = load_benchmark_tasks(args.tasks)
        scenarios = load_integrated_scenarios(args.scenarios)
        p2_runner = ExistingP2ScenarioRunner(scenarios, project_root=project_root)
        b0_runner = DirectB0MockScenarioRunner(scenarios)
        nonverifying_runner = NonVerifyingMockConditionRunner(scenarios)
        nonverifying_host_runner = NonVerifyingHostConditionRunner(
            scenarios,
            project_root=project_root,
        )
        nonverifying_fixture_runner = NonVerifyingFixtureConditionRunner(scenarios)
        nonverifying_memory_pdf_runner = NonVerifyingMemoryPdfConditionRunner(
            scenarios,
            project_root=project_root,
        )
        nonverifying_process_runner = NonVerifyingProcessConditionRunner(
            scenarios,
            project_root=project_root,
        )
        b1_safety_runner = B1SafetyConditionRunner(scenarios)
        p1_safety_runner = P1SafetyConditionRunner(scenarios)
        nonverifying_host_chain = BenchmarkConditionRunnerChain(
            (
                nonverifying_process_runner,
                nonverifying_host_runner,
                b1_safety_runner,
                p1_safety_runner,
            )
        )
        nonverifying_b1_fixture_chain = BenchmarkConditionRunnerChain(
            (
                nonverifying_fixture_runner,
                nonverifying_memory_pdf_runner,
                b1_safety_runner,
            )
        )
        nonverifying_p1_fixture_chain = BenchmarkConditionRunnerChain(
            (
                nonverifying_fixture_runner,
                nonverifying_memory_pdf_runner,
                p1_safety_runner,
            )
        )
        collector = BenchmarkExperimentCollector(
            tasks,
            scenarios,
            args.collection_root,
            repetitions_per_condition=1,
            evaluator_id="javis-available-conditions",
            evaluator_version="1.0.0",
            runners={
                (StudyCondition.B0, ScenarioEnvironment.MOCK_ONLY): b0_runner,
                (StudyCondition.B1, ScenarioEnvironment.MOCK_ONLY): nonverifying_runner,
                (StudyCondition.P1, ScenarioEnvironment.MOCK_ONLY): nonverifying_runner,
                (StudyCondition.B1, ScenarioEnvironment.HOST_SAFE): nonverifying_host_chain,
                (StudyCondition.P1, ScenarioEnvironment.HOST_SAFE): nonverifying_host_chain,
                (
                    StudyCondition.B1,
                    ScenarioEnvironment.HOST_FIXTURE_SANDBOX,
                ): nonverifying_b1_fixture_chain,
                (
                    StudyCondition.P1,
                    ScenarioEnvironment.HOST_FIXTURE_SANDBOX,
                ): nonverifying_p1_fixture_chain,
                **{
                    (StudyCondition.P2, environment): p2_runner
                    for environment in (
                        ScenarioEnvironment.HOST_SAFE,
                        ScenarioEnvironment.HOST_FIXTURE_SANDBOX,
                        ScenarioEnvironment.MOCK_ONLY,
                    )
                },
            },
            environment_available={
                ScenarioEnvironment.HOST_SAFE: True,
                ScenarioEnvironment.HOST_FIXTURE_SANDBOX: True,
                ScenarioEnvironment.MOCK_ONLY: True,
                ScenarioEnvironment.SNAPSHOT_VM_ONLY: False,
            },
        )
        collected = 0
        for _ in collector.schedule:
            if collector.readiness().ready == 0:
                break
            attempt = collector.collect_next()
            if attempt.status is not CollectionAttemptStatus.RECORDED:
                print(
                    json.dumps(
                        {"status": attempt.status.value, "collected_this_run": collected},
                        indent=2,
                    )
                )
                return 1
            collected += 1
        readiness = collector.readiness()
        print(
            json.dumps(
                {
                    "status": "COLLECTED",
                    "collected_this_run": collected,
                    "completed": readiness.completed,
                    "ready": readiness.ready,
                    "environment_unavailable": readiness.environment_unavailable,
                    "runner_unavailable": readiness.runner_unavailable,
                    "complete": readiness.complete,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "export-snapshot-benchmark-requests":
        requests = build_snapshot_benchmark_requests(
            load_benchmark_tasks(args.tasks),
            load_integrated_scenarios(args.scenarios),
            repetitions_per_condition=args.repetitions,
        )
        write_snapshot_benchmark_requests(args.destination, requests)
        print(
            json.dumps(
                {
                    "status": "EXPORTED",
                    "request_count": len(requests.requests),
                    "repetitions_per_condition": requests.repetitions_per_condition,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "collect-snapshot-benchmark":
        tasks = load_benchmark_tasks(args.tasks)
        scenarios = load_integrated_scenarios(args.scenarios)
        snapshot_runner = SnapshotBenchmarkConditionRunner(
            _resolve_cli_path(args.evidence_directory, directory=True),
            HmacSha256SnapshotVerifier(
                args.key_id,
                load_snapshot_hmac_key(_resolve_cli_path(args.key_file, directory=False)),
            ),
            vm_provider=args.vm_provider,
            base_snapshot_id=args.base_snapshot_id,
            guest_machine_id=args.guest_machine_id,
        )
        collector = BenchmarkExperimentCollector(
            tasks,
            scenarios,
            args.collection_root,
            repetitions_per_condition=args.repetitions,
            evaluator_id="javis-available-conditions",
            evaluator_version="1.0.0",
            runners={
                (condition, ScenarioEnvironment.SNAPSHOT_VM_ONLY): snapshot_runner
                for condition in StudyCondition
            },
            environment_available={
                ScenarioEnvironment.HOST_SAFE: False,
                ScenarioEnvironment.HOST_FIXTURE_SANDBOX: False,
                ScenarioEnvironment.MOCK_ONLY: False,
                ScenarioEnvironment.SNAPSHOT_VM_ONLY: True,
            },
        )
        collected = 0
        for _ in collector.schedule:
            if collector.readiness().ready == 0:
                break
            attempt = collector.collect_next()
            if attempt.status is not CollectionAttemptStatus.RECORDED:
                print(
                    json.dumps(
                        {"status": attempt.status.value, "collected_this_run": collected},
                        indent=2,
                    )
                )
                return 1
            collected += 1
        readiness = collector.readiness()
        print(
            json.dumps(
                {
                    "status": "COLLECTED",
                    "collected_this_run": collected,
                    "completed": readiness.completed,
                    "ready": readiness.ready,
                    "environment_unavailable": readiness.environment_unavailable,
                    "runner_unavailable": readiness.runner_unavailable,
                    "complete": readiness.complete,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "run-host-safe-scenarios":
        manifest = load_integrated_scenarios(args.path)
        host_safe_report = HostSafeScenarioRunner(
            manifest,
            project_root=args.project_root,
        ).run()
        print(host_safe_report.model_dump_json(indent=2))
        return 0 if host_safe_report.all_passed else 1
    if args.command == "run-mock-scenarios":
        manifest = load_integrated_scenarios(args.path)
        mock_report = MockIntegratedScenarioRunner(manifest).run()
        print(mock_report.model_dump_json(indent=2))
        return 0 if mock_report.all_passed else 1
    if args.command == "run-fixture-scenarios":
        manifest = load_integrated_scenarios(args.path)
        fixture_report = FixtureIntegratedScenarioRunner(manifest).run()
        print(fixture_report.model_dump_json(indent=2))
        return 0 if fixture_report.all_passed else 1
    if args.command == "audit-scenario-capabilities":
        manifest = load_integrated_scenarios(args.path)
        coverage = audit_scenario_capabilities(manifest, default_registry())
        print(coverage.model_dump_json(indent=2))
        return 0 if coverage.catalog_complete else 1
    if args.command == "run-safe-evaluation":
        manifest = load_integrated_scenarios(args.path)
        safe_report = SafeIntegratedEvaluationRunner(
            manifest,
            default_registry(),
            project_root=args.project_root,
        ).run()
        print(safe_report.model_dump_json(indent=2))
        return 0 if safe_report.catalog_complete else 1
    if args.command == "verify-snapshot-batch":
        evidence_directory = args.evidence_directory
        if (
            not evidence_directory.is_absolute()
            or evidence_directory.is_symlink()
            or not evidence_directory.is_dir()
        ):
            raise SnapshotEvidenceError("invalid snapshot evidence directory")
        verifier = HmacSha256SnapshotVerifier(
            args.key_id,
            load_snapshot_hmac_key(args.key_file),
        )
        snapshot_report = SnapshotEvidenceBatchEvaluator(
            load_snapshot_manifests(args.manifests),
            SnapshotEvidenceGate(verifier),
        ).evaluate_paths(
            {
                scenario_id: evidence_directory / f"{scenario_id}.json"
                for scenario_id in SNAPSHOT_SCENARIO_IDS
            }
        )
        print(snapshot_report.model_dump_json(indent=2))
        return 0 if snapshot_report.all_accepted else 1
    if args.command == "analyze-study":
        study_report = analyze_paired_study(load_study_observations(args.path))
        print(study_report.canonical_json())
        return 0
    if args.command == "validate-app-registry":
        apps = AppRegistryLoader(require_publisher=args.require_publisher).load(args.path)
        app_report = {
            app_id: {
                "executable": str(app.executable),
                "publisher_verified": app.publisher is not None,
            }
            for app_id, app in sorted(apps.items())
        }
        print(json.dumps(app_report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "probe-microphones":
        print(probe_microphones().model_dump_json(indent=2))
        return 0
    if args.command == "doctor":
        report = run_doctor(args.project_root)
        print(report.model_dump_json(indent=2))
        return 0 if report.passed else 1
    if args.command == "foreground-context":
        apps = AppRegistryLoader(require_publisher=args.require_publisher).load(args.registry)
        snapshot = ForegroundContextCollector(apps).capture()
        print(snapshot.model_dump_json(indent=2))
        return 0
    if args.command == "preflight-product-host":
        preflight = run_product_preflight(
            args.project_root,
            args.config,
            args.nim_secret,
        )
        print(preflight.model_dump_json(indent=2))
        return 0 if preflight.ready else 1
    if args.command == "preflight-product-host-credential":
        preflight = run_product_preflight_with_secret_loader(
            args.project_root,
            args.config,
            load_nim_credential,
        )
        print(preflight.model_dump_json(indent=2))
        return 0 if preflight.ready else 1
    if args.command == "check-product-service":
        service = build_windows_product_service(
            args.project_root,
            args.config,
            args.nim_secret,
        )
        product_report = {
            "ready": service.preflight.ready,
            "capability_count": len(service.capabilities.registry.list()),
            "capture_running": service.runtime.capture_pump.is_running,
            "host_state": service.lifecycle.status_snapshot().state.value,
        }
        print(json.dumps(product_report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "check-product-service-credential":
        service = build_windows_product_service_with_secret_loader(
            args.project_root,
            args.config,
            load_nim_credential,
        )
        product_report = {
            "ready": service.preflight.ready,
            "capability_count": len(service.capabilities.registry.list()),
            "capture_running": service.runtime.capture_pump.is_running,
            "host_state": service.lifecycle.status_snapshot().state.value,
        }
        print(json.dumps(product_report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-product-host":
        service = build_windows_product_service(
            args.project_root,
            args.config,
            args.nim_secret,
        )
        started = service.lifecycle.start()
        if started.state.value != "RUNNING":
            print(started.model_dump_json(indent=2))
            return 1
        try:
            run_tk_status_hud(
                status_provider=service.runtime.runtime.status_snapshot,
                request_capture_stop=service.runtime.capture_pump.request_stop,
                request_runtime_stop=service.lifecycle.stop,
                poll_interval_ms=service.configuration.settings.hud_poll_interval_ms,
            )
        finally:
            stopped = service.lifecycle.stop()
        print(stopped.model_dump_json(indent=2))
        return 0 if stopped.state.value == "STOPPED" else 1
    if args.command == "run-product-host-credential":
        service = build_windows_product_service_with_secret_loader(
            args.project_root,
            args.config,
            load_nim_credential,
        )
        started = service.lifecycle.start()
        if started.state.value != "RUNNING":
            print(started.model_dump_json(indent=2))
            return 1
        try:
            run_tk_status_hud(
                status_provider=service.runtime.runtime.status_snapshot,
                request_capture_stop=service.runtime.capture_pump.request_stop,
                request_runtime_stop=service.lifecycle.stop,
                poll_interval_ms=service.configuration.settings.hud_poll_interval_ms,
            )
        finally:
            stopped = service.lifecycle.stop()
        print(stopped.model_dump_json(indent=2))
        return 0 if stopped.state.value == "STOPPED" else 1
    if args.command == "run-product-tray":
        service = build_windows_product_service(
            args.project_root,
            args.config,
            args.nim_secret,
        )
        started = service.lifecycle.start()
        if started.state.value != "RUNNING":
            print(started.model_dump_json(indent=2))
            return 1
        tray = WindowsTrayController(
            show_status=lambda: show_runtime_status_message(
                service.runtime.runtime.status_snapshot()
            ),
            stop_agent=service.lifecycle.stop,
            exit_agent=service.lifecycle.stop,
        )
        try:
            tray.run()
        finally:
            stopped = service.lifecycle.stop()
        print(stopped.model_dump_json(indent=2))
        return 0 if stopped.state.value == "STOPPED" else 1
    if args.command == "run-product-tray-credential":
        service = build_windows_product_service_with_secret_loader(
            args.project_root,
            args.config,
            load_nim_credential,
        )
        started = service.lifecycle.start()
        if started.state.value != "RUNNING":
            print(started.model_dump_json(indent=2))
            return 1
        tray = WindowsTrayController(
            show_status=lambda: show_runtime_status_message(
                service.runtime.runtime.status_snapshot()
            ),
            stop_agent=service.lifecycle.stop,
            exit_agent=service.lifecycle.stop,
        )
        try:
            tray.run()
        finally:
            stopped = service.lifecycle.stop()
        print(stopped.model_dump_json(indent=2))
        return 0 if stopped.state.value == "STOPPED" else 1
    if args.command in {"autostart-status", "install-autostart", "remove-autostart"}:
        registration = _product_autostart_registration(
            args.project_root,
            args.config,
            args.nim_secret,
        )
        if args.command == "autostart-status":
            status = registration.status()
        elif args.command == "install-autostart":
            preflight = run_product_preflight(
                args.project_root,
                args.config,
                args.nim_secret,
            )
            if not preflight.ready:
                print(json.dumps({"status": "PREFLIGHT_FAILED"}, indent=2))
                return 1
            status = registration.register()
        else:
            status = registration.unregister()
        print(json.dumps({"status": status.value}, indent=2))
        return 0
    if args.command in {
        "autostart-status-credential",
        "install-autostart-credential",
        "remove-autostart-credential",
    }:
        registration = _product_credential_autostart_registration(
            args.project_root,
            args.config,
        )
        if args.command == "autostart-status-credential":
            status = registration.status()
        elif args.command == "install-autostart-credential":
            preflight = run_product_preflight_with_secret_loader(
                args.project_root,
                args.config,
                load_nim_credential,
            )
            if not preflight.ready:
                print(json.dumps({"status": "PREFLIGHT_FAILED"}, indent=2))
                return 1
            status = registration.register()
        else:
            status = registration.unregister()
        print(json.dumps({"status": status.value}, indent=2))
        return 0
    if args.command == "provision-nim-secret":
        root = _resolve_cli_path(args.project_root, directory=True)
        value = SecretStr(getpass.getpass("NVIDIA NIM API key: "))
        provision_nim_secret(root, value)
        print(json.dumps({"status": "PROVISIONED"}, indent=2))
        return 0
    if args.command == "provision-nim-credential":
        value = SecretStr(getpass.getpass("NVIDIA NIM API key: "))
        provision_nim_credential(value)
        print(json.dumps({"status": "PROVISIONED"}, indent=2))
        return 0
    if args.command == "nim-credential-status":
        print(json.dumps({"status": nim_credential_status().value}, indent=2))
        return 0
    if args.command == "remove-nim-credential":
        removed = remove_nim_credential()
        print(json.dumps({"status": "REMOVED" if removed else "ABSENT"}, indent=2))
        return 0
    if args.command == "verify-package":
        package = verify_package_manifest(args.package_root)
        print(
            json.dumps(
                {
                    "status": "VERIFIED",
                    "version": package.version,
                    "file_count": len(package.files),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "audit-windows-release":
        release_report = audit_windows_release(
            args.package_root,
            args.setup,
            args.expected_publisher,
        )
        print(release_report.model_dump_json(indent=2))
        return 0 if release_report.ready else 1
    if args.command == "run-user-validation":
        root = _resolve_cli_path(args.project_root, directory=True)
        config = load_product_host_config(
            _resolve_cli_path(args.config, directory=False),
            root,
        )
        assessment = ConsoleHumanAssessment()
        validation_report = UserValidationWorkflow(
            ConsoleConsentProvider(),
            WindowsUserValidationRunners(config, root, assessment).build(),
        ).run()
        UserValidationSink(
            (root / "state" / "user-validation.jsonl").resolve(strict=False),
            root,
        ).append(validation_report)
        print(validation_report.model_dump_json(indent=2))
        return 0 if validation_report.complete else 1
    return 2


def _product_autostart_registration(
    project_root: Path,
    config_path: Path,
    secret_path: Path,
) -> StartupShortcutRegistrar:
    root = _resolve_cli_path(project_root, directory=True)
    config = _resolve_cli_path(config_path, directory=False)
    secret = _resolve_cli_path(secret_path, directory=False)
    executable = Path(sys.executable).resolve(strict=True)
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        tray_executable = executable.with_name("JAVIS-Tray.exe")
        if tray_executable.is_symlink() or not tray_executable.is_file():
            raise ValueError("frozen tray executable is unavailable")
        executable = tray_executable.resolve(strict=True)
    elif executable.name.casefold() == "python.exe":
        windowed = executable.with_name("pythonw.exe")
        if windowed.is_file() and not windowed.is_symlink():
            executable = windowed.resolve(strict=True)
    arguments: tuple[str, ...]
    if frozen:
        arguments = (
            "run-product-tray",
            str(root),
            str(config),
            str(secret),
        )
    else:
        arguments = (
            "-m",
            "javis_core.cli",
            "run-product-tray",
            str(root),
            str(config),
            str(secret),
        )
    return StartupShortcutRegistrar(executable, arguments, root)


def _product_credential_autostart_registration(
    project_root: Path,
    config_path: Path,
) -> StartupShortcutRegistrar:
    root = _resolve_cli_path(project_root, directory=True)
    config = _resolve_cli_path(config_path, directory=False)
    executable = Path(sys.executable).resolve(strict=True)
    frozen = bool(getattr(sys, "frozen", False))
    arguments: tuple[str, ...]
    if frozen:
        tray_executable = executable.with_name("JAVIS-Tray.exe")
        if tray_executable.is_symlink() or not tray_executable.is_file():
            raise ValueError("frozen tray executable is unavailable")
        executable = tray_executable.resolve(strict=True)
        arguments = (
            "run-product-tray-credential",
            str(root),
            str(config),
        )
    else:
        if executable.name.casefold() == "python.exe":
            windowed = executable.with_name("pythonw.exe")
            if windowed.is_file() and not windowed.is_symlink():
                executable = windowed.resolve(strict=True)
        arguments = (
            "-m",
            "javis_core.cli",
            "run-product-tray-credential",
            str(root),
            str(config),
        )
    return StartupShortcutRegistrar(executable, arguments, root)


def _resolve_cli_path(path: Path, *, directory: bool) -> Path:
    if path.is_symlink():
        raise ValueError("product path must not be a symbolic link")
    resolved = path.resolve(strict=True)
    if directory and not resolved.is_dir():
        raise ValueError("product root must be a directory")
    if not directory and not resolved.is_file():
        raise ValueError("product input must be a file")
    return resolved


def _skill_catalog(path: Path) -> LocalSkillCatalog:
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError(
            "skill catalog must be an absolute non-symlink path in an existing directory"
        )
    return LocalSkillCatalog(path.resolve(strict=False), default_registry())


def _skill_proposal(args: argparse.Namespace) -> SkillProposal:
    return SkillProposal(
        key=args.key,
        title=args.title,
        description=args.description,
        triggers=tuple(args.trigger),
        procedure=tuple(args.step),
        allowed_capabilities=tuple(args.capability),
    )


def _smoke_system_info(audit_path: Path | None) -> int:
    registry = default_registry()
    native = NativeReadOnlyExecutor((Path.cwd(),))
    executor = ExecutionBroker(registry, {"native": native})
    audit = JsonlAuditSink(audit_path) if audit_path else InMemoryAuditSink()
    action = Action(
        capability_id="system.info.read",
        purpose="read-only smoke test",
        target_identity="local-machine",
        expected_postconditions=(Postcondition(predicate="execution.success"),),
    )
    plan = Plan(goal="Verify the read-only JAVIS pipeline", steps=(action,))
    context = PolicyContext(
        addressed_confidence=1.0,
        intent_confidence=1.0,
        target_confidence=1.0,
        target_matches_plan=True,
    )
    orchestrator = Orchestrator(
        policy=PolicyEngine(registry),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=audit,
        approvals=ApprovalTokenService(secrets.token_bytes(32)),
    )
    outcome = orchestrator.run(plan, lambda _action: context)
    print(json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if outcome.status.value == "SUCCEEDED" else 1


def entrypoint() -> None:
    if getattr(sys, "frozen", False) and sys.argv[1:] == [TTS_WORKER_TOKEN]:
        from .tts_worker import main as tts_worker_main

        raise SystemExit(tts_worker_main())
    if getattr(sys, "frozen", False) and sys.argv[1:] == [UIA_WORKER_TOKEN]:
        from .uia_worker import main as uia_worker_main

        raise SystemExit(uia_worker_main())
    if getattr(sys, "frozen", False) and sys.argv[1:] == [EXPLORER_WORKER_TOKEN]:
        from .explorer_folder_worker import main as explorer_worker_main

        raise SystemExit(explorer_worker_main())
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
