from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from time import perf_counter_ns
from xml.etree import ElementTree

from pydantic import JsonValue

from .cancellation import CancellationToken
from .models import Action, VerificationResult
from .runtime_models import ExecutionResult

_SHA256 = re.compile(r"[0-9a-f]{64}")
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PARTS = frozenset({"[Content_Types].xml", "_rels/.rels", "word/document.xml"})


class DocumentWriteError(RuntimeError):
    pass


def document_target_identity(path: Path) -> str:
    canonical = path.as_posix().casefold().encode("utf-8")
    return f"document-path-sha256:{hashlib.sha256(canonical).hexdigest()}"


def document_content_sha256(title: str, body: str, source_set_digest: str) -> str:
    payload = {"body": body, "source_set_digest": source_set_digest, "title": title}
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


class DocxDocumentExecutor:
    name = "native"

    def __init__(self, allowed_roots: tuple[Path, ...]) -> None:
        if not allowed_roots:
            raise ValueError("at least one document root is required")
        roots: list[Path] = []
        for root in allowed_roots:
            if not root.is_absolute() or not root.is_dir() or root.is_symlink():
                raise ValueError("document roots must be existing absolute directories")
            roots.append(root.resolve(strict=True))
        self._roots = tuple(roots)

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        request = self._validated_request(action)
        if request is None:
            return self._failure(action, started, "INVALID_ARGUMENTS", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)
        path, title, body, source_digest, content_digest = request
        try:
            payload = self._build_docx(title, body, source_digest)
            self._create_exclusive(path, payload)
        except FileExistsError:
            return self._failure(action, started, "DOCUMENT_ALREADY_EXISTS", retry_safe=True)
        except Exception:
            return self._failure(action, started, "DOCUMENT_WRITE_FAILED", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(
                action,
                started,
                "STOP_REQUESTED_AFTER_WRITE",
                retry_safe=False,
            )
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=self._observation(path, content_digest, source_digest),
            duration_ms=self._elapsed_ms(started),
        )

    def _validated_request(
        self,
        action: Action,
    ) -> tuple[Path, str, str, str, str] | None:
        if (
            action.capability_id != "document.write"
            or set(action.arguments) != {"path", "title", "body", "source_set_digest"}
            or len(action.expected_postconditions) != 1
        ):
            return None
        raw_path = action.arguments.get("path")
        title = action.arguments.get("title")
        body = action.arguments.get("body")
        source_digest = action.arguments.get("source_set_digest")
        if (
            not isinstance(raw_path, str)
            or not isinstance(title, str)
            or not isinstance(body, str)
            or not isinstance(source_digest, str)
            or not title.strip()
            or not body.strip()
            or len(title) > 200
            or len(body) > 20_000
            or _SHA256.fullmatch(source_digest) is None
        ):
            return None
        path = self._allowed_new_path(Path(raw_path))
        if path is None or action.target_identity != document_target_identity(path):
            return None
        postcondition = action.expected_postconditions[0]
        content_digest = document_content_sha256(title, body, source_digest)
        if postcondition.predicate != "document.content.sha256" or postcondition.arguments != {
            "sha256": content_digest
        }:
            return None
        return path, title, body, source_digest, content_digest

    def _allowed_new_path(self, path: Path) -> Path | None:
        if not path.is_absolute() or path.suffix.lower() != ".docx" or path.is_symlink():
            return None
        try:
            parent = path.parent.resolve(strict=True)
        except OSError:
            return None
        candidate = parent / path.name
        for root in self._roots:
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            cursor = root
            if any(
                (cursor := cursor / part).is_symlink()
                for part in relative.parent.parts
            ):
                return None
            return candidate
        return None

    @staticmethod
    def _build_docx(title: str, body: str, source_digest: str) -> bytes:
        document = ElementTree.Element(f"{{{_WORD_NS}}}document")
        body_node = ElementTree.SubElement(document, f"{{{_WORD_NS}}}body")
        paragraphs = [title, *body.splitlines(), f"Source set SHA-256: {source_digest}"]
        for value in paragraphs:
            paragraph = ElementTree.SubElement(body_node, f"{{{_WORD_NS}}}p")
            run = ElementTree.SubElement(paragraph, f"{{{_WORD_NS}}}r")
            text = ElementTree.SubElement(run, f"{{{_WORD_NS}}}t")
            text.text = value
        document_xml = ElementTree.tostring(document, encoding="utf-8", xml_declaration=True)
        content_types = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" '
            b'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Default Extension="xml" ContentType="application/xml"/>'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.'
            b'wordprocessingml.document.main+xml"/>'
            b"</Types>"
        )
        relationships = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            b'relationships"><Relationship Id="rId1" Type="http://schemas.'
            b'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            b'Target="word/document.xml"/></Relationships>'
        )
        with tempfile.SpooledTemporaryFile(max_size=1_048_576) as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("[Content_Types].xml", content_types)
                archive.writestr("_rels/.rels", relationships)
                archive.writestr("word/document.xml", document_xml)
            stream.seek(0)
            payload = stream.read()
        if not 0 < len(payload) <= 1_048_576:
            raise DocumentWriteError("generated document exceeds size limit")
        return payload

    @staticmethod
    def _create_exclusive(path: Path, payload: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".javis-docx-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _observation(path: Path, content_digest: str, source_digest: str) -> dict[str, JsonValue]:
        return {
            "content_sha256": content_digest,
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_set_digest": source_digest,
            "created": True,
        }

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(
        cls,
        action: Action,
        started: int,
        code: str,
        *,
        retry_safe: bool,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=code,
            error_message="Document write was not completed",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class DocxDocumentVerifier:
    def __init__(self, executor: DocxDocumentExecutor) -> None:
        self._executor = executor

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        request = self._executor._validated_request(action)  # noqa: SLF001
        if request is None:
            return self._failure("INVALID_DOCUMENT_CONTRACT")
        path, title, body, source_digest, content_digest = request
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_048_576:
                return self._failure("DOCUMENT_FILE_INVALID")
            with zipfile.ZipFile(path, "r") as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or set(names) != _PARTS:
                    return self._failure("DOCUMENT_PACKAGE_INVALID")
                info = archive.getinfo("word/document.xml")
                if info.file_size > 65_536:
                    return self._failure("DOCUMENT_XML_TOO_LARGE")
                xml = archive.read(info)
            root = ElementTree.fromstring(xml)
            observed_text = [node.text or "" for node in root.iter(f"{{{_WORD_NS}}}t")]
        except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError, KeyError):
            return self._failure("DOCUMENT_VERIFY_FAILED")
        expected_text = [title, *body.splitlines(), f"Source set SHA-256: {source_digest}"]
        if observed_text != expected_text:
            return self._failure("DOCUMENT_CONTENT_MISMATCH")
        try:
            expected_observation = self._executor._observation(  # noqa: SLF001
                path,
                content_digest,
                source_digest,
            )
        except OSError:
            return self._failure("DOCUMENT_VERIFY_FAILED")
        if result.observed != expected_observation:
            return self._failure("DOCUMENT_RECEIPT_MISMATCH")
        return VerificationResult(
            success=True,
            observed=expected_observation,
            reason="DOCUMENT_CONTENT_MATCHED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)
