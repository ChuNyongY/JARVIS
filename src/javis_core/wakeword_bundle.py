from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, field_validator

from .models import FrozenModel


class WakeWordBundleError(ValueError):
    pass


class WakeWordModelFile(FrozenModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_must_be_local_relative_onnx(cls, value: str) -> str:
        if "\\" in value or ":" in value:
            raise ValueError("model path must be a relative POSIX path")
        parsed = PurePosixPath(value)
        if (
            parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or parsed.suffix.lower() != ".onnx"
        ):
            raise ValueError("model path must be a non-traversing relative ONNX path")
        return value


class WakeWordBundleManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    wakeword_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    wakeword: WakeWordModelFile
    melspectrogram: WakeWordModelFile
    embedding: WakeWordModelFile


class LoadedWakeWordBundle(FrozenModel):
    wakeword_key: str
    wakeword_path: Path
    wakeword_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    melspectrogram_path: Path
    melspectrogram_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_path: Path
    embedding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def verify(self) -> bool:
        return all(
            _matches(path, expected)
            for path, expected in (
                (self.wakeword_path, self.wakeword_sha256),
                (self.melspectrogram_path, self.melspectrogram_sha256),
                (self.embedding_path, self.embedding_sha256),
            )
        )


def load_wakeword_bundle(
    manifest_path: Path,
    *,
    max_manifest_bytes: int = 16 * 1024,
    max_model_bytes: int = 10 * 1024 * 1024,
    max_total_model_bytes: int = 30 * 1024 * 1024,
) -> LoadedWakeWordBundle:
    if min(max_manifest_bytes, max_model_bytes, max_total_model_bytes) < 1:
        raise ValueError("bundle limits must be positive")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WakeWordBundleError("bundle manifest must be a regular non-symlink file")
    root = manifest_path.parent
    if root.is_symlink() or not root.is_dir():
        raise WakeWordBundleError("bundle root must be a non-symlink directory")
    root = root.resolve(strict=True)
    try:
        if manifest_path.stat().st_size > max_manifest_bytes:
            raise WakeWordBundleError("bundle manifest exceeded size limit")
        raw = manifest_path.read_bytes()
        if len(raw) > max_manifest_bytes:
            raise WakeWordBundleError("bundle manifest exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        manifest = WakeWordBundleManifest.model_validate(document)
    except WakeWordBundleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WakeWordBundleError("invalid wake-word bundle manifest") from exc

    loaded: dict[str, Path] = {}
    total_bytes = 0
    for role, specification in (
        ("wakeword", manifest.wakeword),
        ("melspectrogram", manifest.melspectrogram),
        ("embedding", manifest.embedding),
    ):
        candidate = root.joinpath(*PurePosixPath(specification.path).parts)
        _reject_symlink_components(root, candidate)
        try:
            resolved = candidate.resolve(strict=True)
            size = resolved.stat().st_size
        except OSError as exc:
            raise WakeWordBundleError("bundle model is unavailable") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise WakeWordBundleError("bundle model escaped its root")
        if size > max_model_bytes:
            raise WakeWordBundleError("bundle model exceeded file size limit")
        total_bytes += size
        if total_bytes > max_total_model_bytes:
            raise WakeWordBundleError("bundle exceeded total model size limit")
        if not _matches(resolved, specification.sha256):
            raise WakeWordBundleError("bundle model hash mismatch")
        loaded[role] = resolved
    return LoadedWakeWordBundle(
        wakeword_key=manifest.wakeword_key,
        wakeword_path=loaded["wakeword"],
        wakeword_sha256=manifest.wakeword.sha256,
        melspectrogram_path=loaded["melspectrogram"],
        melspectrogram_sha256=manifest.melspectrogram.sha256,
        embedding_path=loaded["embedding"],
        embedding_sha256=manifest.embedding.sha256,
    )


def _matches(path: Path, expected_sha256: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256
    except OSError:
        return False


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise WakeWordBundleError("bundle model path must not contain symlinks")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WakeWordBundleError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise WakeWordBundleError(f"non-finite JSON number: {value}")
