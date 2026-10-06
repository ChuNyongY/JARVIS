from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .models import FrozenModel


class SttBundleError(ValueError):
    pass


class SttModelFile(FrozenModel):
    path: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1)


class SttBundleFiles(FrozenModel):
    config: SttModelFile = Field(alias="config.json")
    model: SttModelFile = Field(alias="model.bin")
    tokenizer: SttModelFile = Field(alias="tokenizer.json")
    vocabulary: SttModelFile = Field(alias="vocabulary.txt")


class SttBundleManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    source_repository: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9._/-]+$",
    )
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    files: SttBundleFiles


class LoadedSttBundle(FrozenModel):
    root: Path
    source_repository: str
    source_revision: str
    files: SttBundleFiles

    def verify(self) -> bool:
        if self.root.is_symlink() or not self.root.is_dir():
            return False
        return all(
            _matches(self.root / name, specification)
            for name, specification in _file_items(self.files)
        )


def load_stt_bundle(
    manifest_path: Path,
    *,
    max_manifest_bytes: int = 16 * 1024,
    max_file_bytes: int = 90 * 1024 * 1024,
    max_total_bytes: int = 100 * 1024 * 1024,
) -> LoadedSttBundle:
    if min(max_manifest_bytes, max_file_bytes, max_total_bytes) < 1:
        raise ValueError("bundle limits must be positive")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SttBundleError("STT manifest must be a regular non-symlink file")
    root = manifest_path.parent
    if root.is_symlink() or not root.is_dir():
        raise SttBundleError("STT bundle root must be a non-symlink directory")
    root = root.resolve(strict=True)
    try:
        if manifest_path.stat().st_size > max_manifest_bytes:
            raise SttBundleError("STT manifest exceeded size limit")
        raw = manifest_path.read_bytes()
        if len(raw) > max_manifest_bytes:
            raise SttBundleError("STT manifest exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        manifest = SttBundleManifest.model_validate(document)
    except SttBundleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SttBundleError("invalid STT bundle manifest") from exc

    total = 0
    for expected_name, specification in _file_items(manifest.files):
        if specification.path != expected_name:
            raise SttBundleError("STT model path must match its required filename")
        candidate = root / expected_name
        if candidate.is_symlink() or not candidate.is_file():
            raise SttBundleError("STT model must be a regular non-symlink file")
        try:
            resolved = candidate.resolve(strict=True)
            actual_size = resolved.stat().st_size
        except OSError as exc:
            raise SttBundleError("STT model is unavailable") from exc
        if not resolved.is_relative_to(root):
            raise SttBundleError("STT model escaped bundle root")
        if actual_size != specification.size:
            raise SttBundleError("STT model size mismatch")
        if actual_size > max_file_bytes:
            raise SttBundleError("STT model exceeded file size limit")
        total += actual_size
        if total > max_total_bytes:
            raise SttBundleError("STT bundle exceeded total size limit")
        if not _matches(resolved, specification):
            raise SttBundleError("STT model hash mismatch")
    return LoadedSttBundle(
        root=root,
        source_repository=manifest.source_repository,
        source_revision=manifest.source_revision,
        files=manifest.files,
    )


def _file_items(files: SttBundleFiles) -> tuple[tuple[str, SttModelFile], ...]:
    return (
        ("config.json", files.config),
        ("model.bin", files.model),
        ("tokenizer.json", files.tokenizer),
        ("vocabulary.txt", files.vocabulary),
    )


def _matches(path: Path, specification: SttModelFile) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size != specification.size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == specification.sha256
    except OSError:
        return False


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SttBundleError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise SttBundleError(f"non-finite JSON number: {value}")
