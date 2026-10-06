from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    ExecutionBroker,
    ExecutionResult,
    NativeReadOnlyExecutor,
    default_registry,
)


def test_file_list_is_limited_to_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "b.txt").write_text("not read", encoding="utf-8")
    (allowed / "a.txt").write_text("not read", encoding="utf-8")
    executor = NativeReadOnlyExecutor((allowed,))
    action = Action(
        capability_id="file.list",
        arguments={"path": str(allowed)},
        purpose="list test files",
        target_identity=str(allowed),
    )

    result = executor.execute(action)

    assert result.success
    assert result.observed["entries"] == ["a.txt", "b.txt"]
    assert result.observed["relative_path"] == "."


def test_file_list_rejects_outside_path(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    executor = NativeReadOnlyExecutor((allowed,))
    action = Action(
        capability_id="file.list",
        arguments={"path": str(outside)},
        purpose="must be rejected",
    )

    result = executor.execute(action)

    assert not result.success
    assert result.error_code == "PATH_OUTSIDE_ALLOWED_ROOTS"
    assert str(allowed) not in (result.error_message or "")


def test_file_list_truncates_deterministically(tmp_path: Path) -> None:
    for name in ("d", "b", "a", "c"):
        (tmp_path / name).mkdir()
    executor = NativeReadOnlyExecutor((tmp_path,), max_entries=2)
    action = Action(
        capability_id="file.list",
        arguments={"path": str(tmp_path)},
        purpose="bounded list",
        target_identity=str(tmp_path),
    )

    result = executor.execute(action)

    assert result.success
    assert result.observed["entries"] == ["a", "b"]
    assert result.observed["truncated"] is True


def test_system_info_read_is_supported(tmp_path: Path) -> None:
    executor = NativeReadOnlyExecutor((tmp_path,))
    action = Action(capability_id="system.info.read", purpose="read system info")

    result = executor.execute(action)

    assert result.success
    assert result.executor == "native"
    assert isinstance(result.observed["system"], str)


def test_cancelled_native_action_fails_before_work(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    executor = NativeReadOnlyExecutor((tmp_path,))
    action = Action(capability_id="system.info.read", purpose="cancelled")

    result = executor.execute(action, token)

    assert not result.success
    assert result.error_code == "STOP_REQUESTED"


class FixedExecutor:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        return self.result.model_copy(update={"action_id": action.action_id})


def test_broker_falls_back_only_for_pre_execution_safe_error() -> None:
    action = Action(capability_id="file.list", purpose="fallback")
    native = FixedExecutor(
        ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code="UNSUPPORTED_CAPABILITY",
            duration_ms=0,
        )
    )
    powershell = FixedExecutor(
        ExecutionResult(
            action_id=action.action_id,
            executor="powershell",
            success=True,
            observed={"fallback": True},
            duration_ms=0,
        )
    )
    broker = ExecutionBroker(
        default_registry(),
        {"native": native, "powershell": powershell},
    )

    result = broker.execute(action)

    assert result.success
    assert result.executor == "powershell"
    assert native.calls == powershell.calls == 1


def test_broker_does_not_fallback_after_ambiguous_failure() -> None:
    action = Action(capability_id="file.list", purpose="no duplicate side effect")
    native = FixedExecutor(
        ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code="PATH_ACCESS_DENIED",
            duration_ms=0,
        )
    )
    powershell = FixedExecutor(
        ExecutionResult(
            action_id=action.action_id,
            executor="powershell",
            success=True,
            duration_ms=0,
        )
    )
    broker = ExecutionBroker(
        default_registry(),
        {"native": native, "powershell": powershell},
    )

    result = broker.execute(action)

    assert not result.success
    assert result.error_code == "PATH_ACCESS_DENIED"
    assert native.calls == 1
    assert powershell.calls == 0
