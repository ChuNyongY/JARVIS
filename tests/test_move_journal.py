from pathlib import Path
from uuid import uuid4

import pytest

from javis_core import (
    DurableMoveJournal,
    MoveJournalError,
    MoveJournalRecord,
    MoveRecoveryAnalyzer,
    MoveState,
)


def prepared(source: Path, destination: Path, source_hash: str) -> MoveJournalRecord:
    return MoveJournalRecord(
        transaction_id=uuid4(),
        source=source,
        destination=destination,
        source_hash=source_hash,
        state=MoveState.PREPARED,
        reason="READY",
    )


def test_append_and_reload_round_trip(tmp_path: Path) -> None:
    journal = DurableMoveJournal(tmp_path / "state" / "moves.jsonl")
    record = prepared(tmp_path / "a", tmp_path / "b", "abc")

    journal.append(record)

    assert journal.load() == (record,)


def test_missing_journal_loads_as_empty(tmp_path: Path) -> None:
    assert DurableMoveJournal(tmp_path / "moves.jsonl").load() == ()


def test_directory_cannot_be_used_as_journal(tmp_path: Path) -> None:
    with pytest.raises(MoveJournalError, match="directory"):
        DurableMoveJournal(tmp_path)


def test_final_truncated_record_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "moves.jsonl"
    journal = DurableMoveJournal(path)
    record = prepared(tmp_path / "a", tmp_path / "b", "abc")
    journal.append(record)
    with path.open("ab") as handle:
        handle.write(b'{"transaction_id":')

    assert journal.load() == (record,)


def test_corruption_before_final_line_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "moves.jsonl"
    path.write_bytes(b"not-json\ntruncated")

    with pytest.raises(MoveJournalError, match="line 1"):
        DurableMoveJournal(path).load()


def test_recovery_classifies_unchanged_move_as_rolled_back(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("payload", encoding="utf-8")
    journal = DurableMoveJournal(tmp_path / "moves.jsonl")
    record = prepared(
        source,
        destination,
        "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5",
    )
    journal.append(record)

    recovered = MoveRecoveryAnalyzer(journal).recover()

    assert recovered[0].state is MoveState.ROLLED_BACK
    assert source.read_text(encoding="utf-8") == "payload"
    assert not destination.exists()
    assert MoveRecoveryAnalyzer(journal).recover() == ()


def test_recovery_classifies_completed_move_as_committed(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    destination.write_text("payload", encoding="utf-8")
    journal = DurableMoveJournal(tmp_path / "moves.jsonl")
    record = prepared(
        source,
        destination,
        "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5",
    )
    journal.append(record)

    recovered = MoveRecoveryAnalyzer(journal).recover()

    assert recovered[0].state is MoveState.COMMITTED
    assert destination.read_text(encoding="utf-8") == "payload"
    assert not source.exists()


@pytest.mark.parametrize("layout", ["both", "neither", "changed"])
def test_recovery_marks_uncertain_layout_ambiguous(tmp_path: Path, layout: str) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    if layout in {"both", "changed"}:
        source.write_text("changed" if layout == "changed" else "payload", encoding="utf-8")
    if layout == "both":
        destination.write_text("payload", encoding="utf-8")
    journal = DurableMoveJournal(tmp_path / "moves.jsonl")
    journal.append(
        prepared(
            source,
            destination,
            "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5",
        )
    )

    recovered = MoveRecoveryAnalyzer(journal).recover()

    assert recovered[0].state is MoveState.AMBIGUOUS
