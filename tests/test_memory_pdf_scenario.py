from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

import javis_core.memory_pdf_scenario as scenario_module
from javis_core import Action, ExecutionResult, Postcondition
from javis_core.memory_pdf_scenario import (
    MemoryPdfScenarioResult,
    S03MemoryPdfScenarioRunner,
    _write_minimal_pdf,
)


def test_memory_pdf_scenario_result_preserves_only_coarse_evidence() -> None:
    result = MemoryPdfScenarioResult(
        success=True,
        verified_assertions=(
            "expected_pdf_identity_matches",
            "viewer_process_exists",
        ),
    )

    assert result.model_dump() == {
        "scenario_id": "S03",
        "success": True,
        "evidence_level": "ACTUAL_REVERSIBLE_FIXTURE",
        "verified_assertions": (
            "expected_pdf_identity_matches",
            "viewer_process_exists",
        ),
        "error_code": None,
    }
    assert "path" not in result.model_dump_json()
    assert "pid" not in result.model_dump_json()
    assert "hwnd" not in result.model_dump_json()


def test_memory_pdf_scenario_result_rejects_inconsistent_error() -> None:
    with pytest.raises(ValidationError):
        MemoryPdfScenarioResult(
            success=False,
            verified_assertions=(),
            error_code=None,
        )


def test_minimal_pdf_writer_produces_a_bounded_pdf(tmp_path: Path) -> None:
    destination = tmp_path / "fixture.pdf"

    _write_minimal_pdf(destination)

    payload = destination.read_bytes()
    assert payload.startswith(b"%PDF-1.4")
    assert payload.endswith(b"%%EOF\n")
    assert len(payload) < 4_096


def test_memory_pdf_runner_requires_absolute_project_root() -> None:
    with pytest.raises(ValueError, match="absolute non-symlink"):
        S03MemoryPdfScenarioRunner(Path("relative"))


def test_memory_pdf_runner_success_path_is_privacy_minimized(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "apps.json").write_text("{}", encoding="utf-8")
    app = SimpleNamespace(app_id="edge")

    class RegistryLoader:
        def __init__(self, *, require_publisher: bool) -> None:
            assert require_publisher

        def load(self, _path: Path) -> dict[str, object]:
            return {"edge": app}

    class Store:
        def __init__(self, _path: Path) -> None:
            self.remembered = False

        def remember(self, _candidate: object) -> None:
            self.remembered = True

    class SearchExecutor:
        def __init__(self, store: Store, *, store_id: str) -> None:
            assert store.remembered
            assert store_id == "s03-fixture"

    class SearchVerifier:
        def __init__(self, _executor: SearchExecutor) -> None:
            pass

    class Resolver:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def resolve(self, _action: Action, _result: ExecutionResult) -> Action:
            return Action(
                capability_id="file.open",
                arguments={
                    "path": "internal-only",
                    "sha256": "0" * 64,
                    "viewer_app_id": "edge",
                },
                purpose="open fixture",
                target_identity=f"file-sha256:{'0' * 64}",
                expected_postconditions=(Postcondition(predicate="file.opened"),),
            )

    class WindowResolver:
        def snapshot(self, _app: object) -> frozenset[tuple[int, int]]:
            return frozenset()

        def matching_windows(self, _app: object) -> set[tuple[int, int]]:
            return set()

    class PdfExecutor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class PdfVerifier:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    def run_one(
        action: Action,
        executor: object,
        _verifier: object,
    ) -> bool:
        observed = (
            {"pid": 101, "hwnd": 202}
            if action.capability_id == "file.open"
            else {"match_count": 1}
        )
        executor.result = ExecutionResult(  # type: ignore[attr-defined]
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=observed,
            duration_ms=0,
        )
        return True

    monkeypatch.setattr(scenario_module, "AppRegistryLoader", RegistryLoader)
    monkeypatch.setattr(scenario_module, "SqliteMemoryRepository", Store)
    monkeypatch.setattr(scenario_module, "MemorySearchExecutor", SearchExecutor)
    monkeypatch.setattr(scenario_module, "MemorySearchVerifier", SearchVerifier)
    monkeypatch.setattr(scenario_module, "MemoryPdfResolver", Resolver)
    monkeypatch.setattr(scenario_module, "WindowsRuntimeWindowResolver", WindowResolver)
    monkeypatch.setattr(scenario_module, "RegisteredPdfOpenExecutor", PdfExecutor)
    monkeypatch.setattr(scenario_module, "PdfOpenedVerifier", PdfVerifier)
    monkeypatch.setattr(scenario_module, "_run_one", run_one)
    monkeypatch.setattr(scenario_module, "_close_observed_window", lambda *_args: True)

    result = S03MemoryPdfScenarioRunner(tmp_path.resolve()).run()

    assert result.success
    assert result.verified_assertions == (
        "expected_pdf_identity_matches",
        "viewer_process_exists",
    )
    serialized = result.model_dump_json()
    assert "internal-only" not in serialized
    assert "101" not in serialized
    assert "202" not in serialized
