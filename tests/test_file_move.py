from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    ExecutionResult,
    FileMovedPredicate,
    FileMoveExecutor,
    InMemoryMoveJournal,
    MoveState,
    Postcondition,
)


def move_action(source: Path, destination: Path) -> Action:
    return Action(
        capability_id="file.move",
        arguments={"source": str(source), "destination": str(destination)},
        purpose="move fixture",
        target_identity=str(source.resolve()),
        expected_postconditions=(Postcondition(predicate="file.moved"),),
    )


def test_file_move_commits_after_state_verification(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("payload", encoding="utf-8")
    journal = InMemoryMoveJournal()
    executor = FileMoveExecutor((tmp_path,), journal)

    result = executor.execute(move_action(source, destination))

    assert result.success
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "payload"
    assert [record.state for record in journal.records] == [
        MoveState.PREPARED,
        MoveState.COMMITTED,
    ]
    assert FileMovedPredicate()(Postcondition(predicate="file.moved"), result)


def test_cancel_before_move_leaves_source_in_place(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("payload", encoding="utf-8")
    journal = InMemoryMoveJournal()
    cancellation = CancellationToken()
    cancellation.cancel()

    result = FileMoveExecutor((tmp_path,), journal).execute(
        move_action(source, destination),
        cancellation,
    )

    assert not result.success
    assert result.error_code == "STOP_REQUESTED"
    assert source.exists()
    assert not destination.exists()
    assert [record.state for record in journal.records] == [
        MoveState.PREPARED,
        MoveState.ROLLED_BACK,
    ]


def test_existing_destination_is_rejected_before_journal(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")
    journal = InMemoryMoveJournal()

    result = FileMoveExecutor((tmp_path,), journal).execute(
        move_action(source, destination)
    )

    assert not result.success
    assert result.error_code == "SOURCE_OR_DESTINATION_INVALID"
    assert result.retry_safe
    assert journal.records == ()


def test_target_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    action = move_action(source, destination).model_copy(
        update={"target_identity": "different-file"}
    )

    result = FileMoveExecutor((tmp_path,), InMemoryMoveJournal()).execute(action)

    assert not result.success
    assert result.error_code == "TARGET_IDENTITY_MISMATCH"


def test_file_move_predicate_rejects_unverified_observation(tmp_path: Path) -> None:
    result = ExecutionResult(
        action_id="00000000-0000-0000-0000-000000000001",
        executor="native",
        success=True,
        observed={"source": str(tmp_path / "missing")},
        duration_ms=0,
    )
    assert not FileMovedPredicate()(Postcondition(predicate="file.moved"), result)
