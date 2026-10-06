import hashlib
from pathlib import Path

import pytest

from javis_core import (
    Action,
    MemoryPdfResolutionError,
    MemoryPdfResolver,
    MemorySearchExecutor,
    MemorySearchVerifier,
    MemorySensitivity,
    MemorySource,
    Postcondition,
    SemanticMemoryCandidate,
    SqliteMemoryRepository,
)


class Acl:
    def harden(self, path: Path) -> None:
        assert path.is_file()


def search_action() -> Action:
    return Action(
        capability_id="memory.search",
        arguments={"query": "정보처리기사 PDF", "k": 3},
        purpose="find unique remembered PDF",
        target_identity="memory-store:fixture",
        expected_postconditions=(Postcondition(predicate="memory.search.matches"),),
    )


def setup(tmp_path: Path, value: str) -> tuple[SqliteMemoryRepository, MemorySearchExecutor]:
    store = SqliteMemoryRepository(
        (tmp_path / "memory.sqlite3").resolve(),
        acl=Acl(),
    )
    store.remember(
        SemanticMemoryCandidate(
            key="study.certification_pdf",
            value=value,
            source=MemorySource.VERIFIED_TASK,
            confidence=1.0,
            sensitivity=MemorySensitivity.LOW,
        )
    )
    return store, MemorySearchExecutor(store, store_id="fixture")


def test_unique_verified_memory_becomes_exact_hash_pinned_file_open_action(tmp_path: Path) -> None:
    document = tmp_path / "정보처리기사 PDF.pdf"
    document.write_bytes(b"%PDF-1.4\nfixture\n%%EOF\n")
    store, executor = setup(tmp_path, str(document.resolve()))
    requested = search_action()
    result = executor.execute(requested)

    action = MemoryPdfResolver(
        store,
        MemorySearchVerifier(executor),
        (tmp_path.resolve(),),
        viewer_app_id="chrome",
    ).resolve(requested, result)

    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    assert action.capability_id == "file.open"
    assert action.arguments == {
        "path": str(document.resolve()),
        "sha256": digest,
        "viewer_app_id": "chrome",
    }
    assert action.target_identity == f"file-sha256:{digest}"


def test_forged_search_result_and_outside_or_non_pdf_memory_are_rejected(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    outside_pdf = outside / "정보처리기사 PDF.pdf"
    outside_pdf.write_bytes(b"pdf")
    store, executor = setup(inside, str(outside_pdf.resolve()))
    requested = search_action()
    result = executor.execute(requested)
    resolver = MemoryPdfResolver(
        store,
        MemorySearchVerifier(executor),
        (inside.resolve(),),
        viewer_app_id="chrome",
    )

    forged = result.model_copy(update={"observed": {**result.observed, "match_count": 0}})
    with pytest.raises(MemoryPdfResolutionError, match="not verified"):
        resolver.resolve(requested, forged)
    with pytest.raises(MemoryPdfResolutionError, match="outside"):
        resolver.resolve(requested, result)

    text_root = tmp_path / "text"
    text_root.mkdir()
    text = text_root / "정보처리기사 PDF.txt"
    text.write_text("not pdf", encoding="utf-8")
    text_store, text_executor = setup(text_root, str(text.resolve()))
    text_request = search_action()
    with pytest.raises(MemoryPdfResolutionError, match="not allowed"):
        MemoryPdfResolver(
            text_store,
            MemorySearchVerifier(text_executor),
            (text_root.resolve(),),
            viewer_app_id="chrome",
        ).resolve(text_request, text_executor.execute(text_request))


def test_multiple_matches_and_symlink_pdf_are_rejected(tmp_path: Path) -> None:
    first = tmp_path / "정보처리기사 PDF one.pdf"
    second = tmp_path / "정보처리기사 PDF two.pdf"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    store, executor = setup(tmp_path, str(first.resolve()))
    store.remember(
        SemanticMemoryCandidate(
            key="study.certification_pdf_backup",
            value=str(second.resolve()),
            source=MemorySource.VERIFIED_TASK,
            confidence=1.0,
            sensitivity=MemorySensitivity.LOW,
        )
    )
    requested = search_action()
    resolver = MemoryPdfResolver(
        store,
        MemorySearchVerifier(executor),
        (tmp_path.resolve(),),
        viewer_app_id="chrome",
    )
    with pytest.raises(MemoryPdfResolutionError, match="exactly one"):
        resolver.resolve(requested, executor.execute(requested))

    symlink_root = tmp_path / "symlink-case"
    symlink_root.mkdir()
    target = symlink_root / "정보처리기사 PDF target.pdf"
    target.write_bytes(b"target")
    link = symlink_root / "정보처리기사 PDF link.pdf"
    try:
        link.symlink_to(target)
    except OSError:
        return
    link_store, link_executor = setup(symlink_root, str(link.absolute()))
    link_request = search_action()
    with pytest.raises(MemoryPdfResolutionError, match="not allowed"):
        MemoryPdfResolver(
            link_store,
            MemorySearchVerifier(link_executor),
            (symlink_root.resolve(),),
            viewer_app_id="chrome",
        ).resolve(link_request, link_executor.execute(link_request))
