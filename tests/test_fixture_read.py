from pathlib import Path

import pytest

from javis_core import (
    Action,
    CancellationToken,
    FixtureReadError,
    FixtureReadExecutor,
    FixtureReadVerifier,
    FixtureSourceCatalog,
    Postcondition,
)
from javis_core.runtime_models import ExecutionResult


def catalog(tmp_path: Path) -> FixtureSourceCatalog:
    (tmp_path / "a.html").write_text("<p>trusted fixture</p>", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second source", encoding="utf-8")
    return FixtureSourceCatalog(tmp_path.resolve(), {"study": ("a.html", "b.txt")})


def action() -> Action:
    return Action(
        capability_id="browser.fixture.read",
        arguments={"fixture_id": "study"},
        purpose="read fixed study sources",
        target_identity="browser-fixture:study",
        expected_postconditions=(Postcondition(predicate="fixture.sources.bound"),),
    )


def test_executor_returns_binding_without_source_text_or_paths(tmp_path: Path) -> None:
    result = FixtureReadExecutor(catalog(tmp_path)).execute(action())

    assert result.success
    assert result.observed["source_count"] == 2
    assert result.observed["total_bytes"] > 0
    serialized = result.model_dump_json()
    assert "trusted fixture" not in serialized
    assert "a.html" not in serialized
    assert str(tmp_path) not in serialized


def test_verifier_rereads_sources_and_rejects_forged_binding(tmp_path: Path) -> None:
    source_catalog = catalog(tmp_path)
    executor = FixtureReadExecutor(source_catalog)
    requested = action()
    genuine = executor.execute(requested)
    verifier = FixtureReadVerifier(source_catalog)

    assert verifier.verify(requested, genuine).success
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={
            "fixture_id": "study",
            "source_count": 2,
            "source_set_digest": "0" * 64,
            "total_bytes": genuine.observed["total_bytes"],
        },
        duration_ms=0,
    )
    assert verifier.verify(requested, forged).reason == "FIXTURE_VERIFY_MISMATCH"


def test_verifier_preserves_retry_safety_for_pre_input_execution_failure(
    tmp_path: Path,
) -> None:
    requested = action()
    verifier = FixtureReadVerifier(catalog(tmp_path))
    failed = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=False,
        error_code="NETWORK_DISCONNECTED",
        error_message="Fixture read was not completed",
        retry_safe=True,
        duration_ms=0,
    )

    result = verifier.verify(requested, failed)

    assert not result.success
    assert result.recoverable
    assert result.reason == "NETWORK_DISCONNECTED"


def test_invalid_contract_and_stop_fail_without_reading(tmp_path: Path) -> None:
    executor = FixtureReadExecutor(catalog(tmp_path))
    wrong = action().model_copy(update={"target_identity": "browser-fixture:other"})
    assert executor.execute(wrong).error_code == "INVALID_ARGUMENTS"
    token = CancellationToken()
    token.cancel()
    assert executor.execute(action(), token).error_code == "STOP_REQUESTED"


def test_catalog_rejects_traversal_suffix_symlink_and_oversize(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    traversal = FixtureSourceCatalog(tmp_path.resolve(), {"bad": ("../outside.txt",)})
    with pytest.raises(FixtureReadError):
        traversal.read("bad")
    wrong_suffix = FixtureSourceCatalog(tmp_path.resolve(), {"bad": ("safe.exe",)})
    with pytest.raises(FixtureReadError):
        wrong_suffix.read("bad")
    oversize = FixtureSourceCatalog(
        tmp_path.resolve(),
        {"bad": ("safe.txt",)},
        max_source_bytes=3,
        max_total_bytes=3,
    )
    with pytest.raises(FixtureReadError):
        oversize.read("bad")

    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    symlink = FixtureSourceCatalog(tmp_path.resolve(), {"bad": ("link.txt",)})
    with pytest.raises(FixtureReadError):
        symlink.read("bad")


def test_read_error_is_redacted(tmp_path: Path) -> None:
    source_catalog = FixtureSourceCatalog(tmp_path.resolve(), {"missing": ("secret.txt",)})

    result = FixtureReadExecutor(source_catalog).execute(
        action().model_copy(
            update={
                "arguments": {"fixture_id": "missing"},
                "target_identity": "browser-fixture:missing",
            }
        )
    )

    assert result.error_code == "FIXTURE_READ_FAILED"
    assert str(tmp_path) not in result.model_dump_json()
