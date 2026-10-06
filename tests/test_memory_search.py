from datetime import UTC, datetime
from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    MemorySearchExecutor,
    MemorySearchVerifier,
    MemorySensitivity,
    MemorySource,
    Postcondition,
    SemanticMemoryCandidate,
    SemanticMemoryRecord,
    SqliteMemoryRepository,
)
from javis_core.runtime_models import ExecutionResult


class Acl:
    def harden(self, path: Path) -> None:
        assert path.is_file()


def repository(tmp_path: Path) -> SqliteMemoryRepository:
    return SqliteMemoryRepository(
        (tmp_path / "memory.sqlite3").resolve(),
        acl=Acl(),
    )


def action(**arguments: object) -> Action:
    values = {"query": "정보처리기사 PDF", "k": 3}
    values.update(arguments)
    return Action(
        capability_id="memory.search",
        arguments=values,  # type: ignore[arg-type]
        purpose="find the unique remembered study PDF",
        target_identity="memory-store:fixture",
        expected_postconditions=(Postcondition(predicate="memory.search.matches"),),
    )


def add_record(store: SqliteMemoryRepository) -> None:
    store.remember(
        SemanticMemoryCandidate(
            key="study.certification_pdf",
            value="C:/fixture/정보처리기사.pdf",
            source=MemorySource.VERIFIED_TASK,
            confidence=1.0,
            sensitivity=MemorySensitivity.LOW,
        ),
        now=datetime(2026, 9, 29, tzinfo=UTC),
    )


def test_search_returns_only_count_digest_and_opaque_unique_id(tmp_path: Path) -> None:
    store = repository(tmp_path)
    add_record(store)

    result = MemorySearchExecutor(store, store_id="fixture").execute(action())

    assert result.success
    assert result.observed["match_count"] == 1
    assert isinstance(result.observed["unique_memory_id"], str)
    serialized = result.model_dump_json()
    assert "C:/fixture" not in serialized
    assert "정보처리기사.pdf" not in serialized


def test_invalid_contract_and_cancel_do_not_search(tmp_path: Path) -> None:
    store = repository(tmp_path)
    executor = MemorySearchExecutor(store, store_id="fixture")
    invalid = (
        action(k=0),
        action(query=""),
        action().model_copy(update={"target_identity": "memory-store:other"}),
        action().model_copy(
            update={"expected_postconditions": (Postcondition(predicate="execution.success"),)}
        ),
    )
    for candidate in invalid:
        assert executor.execute(candidate).error_code == "INVALID_ARGUMENTS"

    token = CancellationToken()
    token.cancel()
    assert executor.execute(action(), token).error_code == "STOP_REQUESTED"


def test_verifier_reruns_query_and_rejects_forged_result(tmp_path: Path) -> None:
    store = repository(tmp_path)
    add_record(store)
    executor = MemorySearchExecutor(store, store_id="fixture")
    requested = action()
    genuine = executor.execute(requested)
    verifier = MemorySearchVerifier(executor)

    assert verifier.verify(requested, genuine).success
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={
            "match_count": 0,
            "results_digest": "0" * 64,
            "unique_memory_id": None,
        },
        duration_ms=0,
    )
    assert verifier.verify(requested, forged).reason == "MEMORY_SEARCH_VERIFY_MISMATCH"


def test_backend_error_is_redacted() -> None:
    class Broken:
        def search(
            self,
            query: str,
            *,
            k: int = 10,
        ) -> tuple[SemanticMemoryRecord, ...]:
            raise RuntimeError("private database detail")

    result = MemorySearchExecutor(Broken(), store_id="fixture").execute(action())

    assert result.error_code == "MEMORY_SEARCH_FAILED"
    assert "private database detail" not in result.model_dump_json()
