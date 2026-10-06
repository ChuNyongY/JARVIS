from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    DocxDocumentExecutor,
    DocxDocumentVerifier,
    Postcondition,
    document_content_sha256,
    document_target_identity,
)
from javis_core.runtime_models import ExecutionResult

SOURCE_DIGEST = "a" * 64


def action(path: Path, *, body: str = "첫 문단\n둘째 문단") -> Action:
    title = "자료 요약"
    digest = document_content_sha256(title, body, SOURCE_DIGEST)
    return Action(
        capability_id="document.write",
        arguments={
            "path": str(path),
            "title": title,
            "body": body,
            "source_set_digest": SOURCE_DIGEST,
        },
        purpose="write a verified fixture summary",
        target_identity=document_target_identity(path),
        expected_postconditions=(
            Postcondition(
                predicate="document.content.sha256",
                arguments={"sha256": digest},
            ),
        ),
    )


def test_create_new_docx_and_independently_verify_content(tmp_path: Path) -> None:
    output = tmp_path / "summary.docx"
    executor = DocxDocumentExecutor((tmp_path.resolve(),))
    requested = action(output)

    result = executor.execute(requested)
    verification = DocxDocumentVerifier(executor).verify(requested, result)

    assert result.success
    assert verification.success
    assert output.is_file()
    serialized = result.model_dump_json()
    assert "첫 문단" not in serialized
    assert str(tmp_path) not in serialized


def test_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "summary.docx"
    output.write_bytes(b"existing")

    result = DocxDocumentExecutor((tmp_path.resolve(),)).execute(action(output))

    assert result.error_code == "DOCUMENT_ALREADY_EXISTS"
    assert result.retry_safe
    assert output.read_bytes() == b"existing"


def test_invalid_target_digest_suffix_and_postcondition_are_rejected(tmp_path: Path) -> None:
    executor = DocxDocumentExecutor((tmp_path.resolve(),))
    output = tmp_path / "summary.docx"
    candidates = (
        action(output).model_copy(update={"target_identity": "document-path-sha256:" + "0" * 64}),
        action(tmp_path / "summary.txt"),
        action(output).model_copy(
            update={"expected_postconditions": (Postcondition(predicate="execution.success"),)}
        ),
    )
    for candidate in candidates:
        assert executor.execute(candidate).error_code == "INVALID_ARGUMENTS"
    assert not output.exists()


def test_stop_before_write_creates_nothing(tmp_path: Path) -> None:
    output = tmp_path / "summary.docx"
    token = CancellationToken()
    token.cancel()

    result = DocxDocumentExecutor((tmp_path.resolve(),)).execute(action(output), token)

    assert result.error_code == "STOP_REQUESTED"
    assert not output.exists()


def test_verifier_rejects_forged_receipt_and_tampered_package(tmp_path: Path) -> None:
    output = tmp_path / "summary.docx"
    executor = DocxDocumentExecutor((tmp_path.resolve(),))
    requested = action(output)
    genuine = executor.execute(requested)
    verifier = DocxDocumentVerifier(executor)
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={**genuine.observed, "file_sha256": "0" * 64},
        duration_ms=0,
    )

    assert verifier.verify(requested, forged).reason == "DOCUMENT_RECEIPT_MISMATCH"
    output.write_bytes(b"not a docx")
    assert verifier.verify(requested, genuine).reason == "DOCUMENT_VERIFY_FAILED"


def test_outside_root_and_symlink_parent_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    executor = DocxDocumentExecutor((root.resolve(),))
    assert executor.execute(action(tmp_path / "outside.docx")).error_code == "INVALID_ARGUMENTS"

    real = root / "real"
    real.mkdir()
    link = root / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        return
    assert executor.execute(action(link / "linked.docx")).error_code == "INVALID_ARGUMENTS"
