from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .memory import SemanticMemoryRecord
from .memory_search import MemorySearchVerifier
from .models import Action, Postcondition
from .runtime_models import ExecutionResult


class MemoryRecordLookup(Protocol):
    def get_by_id(self, memory_id: UUID) -> SemanticMemoryRecord | None: ...


class MemoryPdfResolutionError(ValueError):
    """Stable S03 resolution error without memory values or path details."""


class MemoryPdfResolver:
    def __init__(
        self,
        lookup: MemoryRecordLookup,
        search_verifier: MemorySearchVerifier,
        allowed_roots: Iterable[Path],
        *,
        viewer_app_id: str,
        max_file_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        roots = tuple(allowed_roots)
        if (
            not roots
            or any(
                not root.is_absolute() or not root.is_dir() or root.is_symlink()
                for root in roots
            )
            or not viewer_app_id
            or max_file_bytes < 1
        ):
            raise ValueError("invalid memory PDF resolver configuration")
        self._lookup = lookup
        self._search_verifier = search_verifier
        self._roots = tuple(root.resolve(strict=True) for root in roots)
        self._viewer_app_id = viewer_app_id
        self._max_file_bytes = max_file_bytes

    def resolve(self, search_action: Action, search_result: ExecutionResult) -> Action:
        verification = self._search_verifier.verify(search_action, search_result)
        if not verification.success:
            raise MemoryPdfResolutionError("memory search result is not verified")
        if verification.observed.get("match_count") != 1:
            raise MemoryPdfResolutionError("memory search must have exactly one result")
        raw_memory_id = verification.observed.get("unique_memory_id")
        if not isinstance(raw_memory_id, str):
            raise MemoryPdfResolutionError("memory search has no unique result")
        try:
            memory_id = UUID(raw_memory_id)
        except ValueError:
            raise MemoryPdfResolutionError("memory result ID is invalid") from None
        try:
            record = self._lookup.get_by_id(memory_id)
        except Exception:
            raise MemoryPdfResolutionError("memory result could not be resolved") from None
        if record is None or not isinstance(record.value, str):
            raise MemoryPdfResolutionError("memory result is unavailable")
        path = self._validated_pdf(Path(record.value))
        digest = self._hash_file(path)
        return Action(
            capability_id="file.open",
            arguments={
                "path": str(path),
                "sha256": digest,
                "viewer_app_id": self._viewer_app_id,
            },
            purpose="open the uniquely verified remembered PDF",
            target_identity=f"file-sha256:{digest}",
            expected_postconditions=(Postcondition(predicate="file.opened"),),
        )

    def _validated_pdf(self, path: Path) -> Path:
        if not path.is_absolute() or path.is_symlink() or path.suffix.casefold() != ".pdf":
            raise MemoryPdfResolutionError("remembered PDF path is not allowed")
        try:
            resolved = path.resolve(strict=True)
            size = resolved.stat().st_size
        except OSError:
            raise MemoryPdfResolutionError("remembered PDF is unavailable") from None
        if (
            not resolved.is_file()
            or not 1 <= size <= self._max_file_bytes
            or not any(resolved.is_relative_to(root) for root in self._roots)
        ):
            raise MemoryPdfResolutionError("remembered PDF is outside the allowed boundary")
        return resolved

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            raise MemoryPdfResolutionError("remembered PDF could not be hashed") from None
        return digest.hexdigest()
