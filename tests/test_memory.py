from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from javis_core import (
    EpisodeCandidate,
    EpisodeResult,
    MemoryError,
    MemorySensitivity,
    MemorySource,
    SemanticMemoryCandidate,
    SqliteMemoryRepository,
)


class Acl:
    def harden(self, path: Path) -> None:
        assert path.is_file()


def repository(tmp_path: Path, **kwargs: object) -> SqliteMemoryRepository:
    return SqliteMemoryRepository(
        (tmp_path / "memory.sqlite3").resolve(),
        acl=Acl(),
        **kwargs,  # type: ignore[arg-type]
    )


def candidate(**updates: object) -> SemanticMemoryCandidate:
    values: dict[str, object] = {
        "key": "preferred.report_directory",
        "value": "D:/University/Reports",
        "source": MemorySource.EXPLICIT_USER_INSTRUCTION,
        "confidence": 1.0,
        "sensitivity": MemorySensitivity.LOW,
    }
    values.update(updates)
    return SemanticMemoryCandidate(**values)  # type: ignore[arg-type]


def test_semantic_round_trip_search_update_and_delete(tmp_path: Path) -> None:
    store = repository(tmp_path)
    first = store.remember(candidate())
    exact = store.get("preferred.report_directory")
    assert exact is not None and exact.value == "D:/University/Reports"
    assert store.search("University Reports")[0].memory_id == first.memory_id
    updated = store.remember(candidate(value="D:/Reports"))
    assert updated.memory_id == first.memory_id
    assert store.search("University") == ()
    assert store.delete(first.memory_id)


def test_query_syntax_is_tokenized_not_executed(tmp_path: Path) -> None:
    store = repository(tmp_path)
    store.remember(candidate())
    assert store.search('" OR * NEAR(password)') == ()


@pytest.mark.parametrize(
    "key,value",
    [
        ("account.password", "ordinary"),
        ("preference.note", "api_key=secret-value"),
        ("preference.note", "Bearer abcdefghijklmnop"),
        ("preference.note", "-----BEGIN PRIVATE KEY-----"),
    ],
)
def test_credentials_are_rejected(key: str, value: str) -> None:
    with pytest.raises(ValueError, match="credential"):
        candidate(key=key, value=value)


def test_expiry_purge_episode_retention_and_row_limit(tmp_path: Path) -> None:
    now = datetime(2026, 9, 22, tzinfo=UTC)
    store = repository(tmp_path)
    record = store.remember(candidate(expires_at=now + timedelta(seconds=1)), now=now)
    assert store.get(record.key, now=now + timedelta(seconds=2)) is None
    assert store.purge_expired(now=now + timedelta(seconds=2)) == 1
    episode = store.record_episode(
        EpisodeCandidate(
            task_id=uuid4(),
            summary="등록된 Edge 창의 구조를 확인했다.",
            result=EpisodeResult.SUCCESS,
            applications=("edge",),
            sensitivity=MemorySensitivity.LOW,
        ),
        now=now,
    )
    assert episode.expires_at == now + timedelta(days=30)
    assert store.purge_expired(now=episode.expires_at) == 1

    limited_path = tmp_path / "limited"
    limited_path.mkdir()
    limited = repository(limited_path, max_rows=1)
    limited.remember(candidate())
    with pytest.raises(MemoryError, match="row limit"):
        limited.record_episode(
            EpisodeCandidate(
                task_id=uuid4(),
                summary="bounded",
                result=EpisodeResult.SUCCESS,
                sensitivity=MemorySensitivity.LOW,
            )
        )


def test_relative_path_and_expired_insert_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteMemoryRepository(Path("relative.sqlite3"), acl=Acl())
    now = datetime(2026, 9, 22, tzinfo=UTC)
    with pytest.raises(MemoryError, match="expired"):
        repository(tmp_path).remember(candidate(expires_at=now), now=now)
