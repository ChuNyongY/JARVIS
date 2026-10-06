from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    FileAbsentPredicate,
    FileTrashExecutor,
    Postcondition,
)


class Backend:
    def __init__(self, *, fail: bool = False, remove: bool = True) -> None:
        self.fail = fail
        self.remove = remove
        self.calls: list[Path] = []

    def trash(self, path: Path) -> None:
        self.calls.append(path)
        if self.fail:
            raise OSError("injected")
        if self.remove:
            path.unlink()


def action(source: Path) -> Action:
    return Action(
        capability_id="file.delete.trash",
        arguments={"path": str(source)},
        purpose="move fixture to trash",
        target_identity=str(source.resolve()),
        expected_postconditions=(Postcondition(predicate="file.absent"),),
    )


def test_file_is_removed_and_absence_is_independently_verified(tmp_path: Path) -> None:
    source = tmp_path / "trash-me.txt"
    source.write_text("fixture", encoding="utf-8")
    backend = Backend()

    result = FileTrashExecutor((tmp_path,), backend).execute(action(source))

    assert result.success
    assert not source.exists()
    assert FileAbsentPredicate()(Postcondition(predicate="file.absent"), result)


def test_post_call_failure_is_never_retry_safe(tmp_path: Path) -> None:
    for backend in (Backend(fail=True), Backend(remove=False)):
        source = tmp_path / f"case-{id(backend)}.txt"
        source.write_text("fixture", encoding="utf-8")
        result = FileTrashExecutor((tmp_path,), backend).execute(action(source))
        assert not result.success
        assert not result.retry_safe


def test_cancel_prevents_backend_call(tmp_path: Path) -> None:
    source = tmp_path / "keep.txt"
    source.write_text("fixture", encoding="utf-8")
    backend = Backend()
    cancellation = CancellationToken()
    cancellation.cancel()

    result = FileTrashExecutor((tmp_path,), backend).execute(action(source), cancellation)

    assert result.error_code == "STOP_REQUESTED"
    assert backend.calls == []
    assert source.exists()


def test_outside_root_and_target_mismatch_are_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("fixture", encoding="utf-8")
    executor = FileTrashExecutor((allowed,), Backend())

    assert executor.execute(action(outside)).error_code == "PATH_OUTSIDE_ALLOWED_ROOTS"
    inside = allowed / "inside.txt"
    inside.write_text("fixture", encoding="utf-8")
    mismatched = action(inside).model_copy(update={"target_identity": "other"})
    assert executor.execute(mismatched).error_code == "TARGET_IDENTITY_MISMATCH"
