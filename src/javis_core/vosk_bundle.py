from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from .models import FrozenModel


class VoskBundleError(ValueError):
    pass


_REQUIRED_FILES = frozenset(
    {
        "README",
        "am/final.mdl",
        "conf/mfcc.conf",
        "conf/model.conf",
        "graph/Gr.fst",
        "graph/HCLr.fst",
        "graph/disambig_tid.int",
        "graph/phones.txt",
        "graph/phones/word_boundary.int",
        "ivector/final.dubm",
        "ivector/final.ie",
        "ivector/final.mat",
        "ivector/global_cmvn.stats",
        "ivector/online_cmvn.conf",
        "ivector/splice.conf",
    }
)


class VoskModelFile(FrozenModel):
    path: str = Field(min_length=1, max_length=256)
    size: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def path_is_portable(self) -> VoskModelFile:
        if "\\" in self.path or ":" in self.path or "\0" in self.path:
            raise ValueError("Vosk file path must use portable relative syntax")
        parsed = PurePosixPath(self.path)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            raise ValueError("Vosk file path must not traverse directories")
        return self


class VoskBundleManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    model_name: Literal["vosk-model-small-ko-0.22"]
    locale: Literal["ko-KR"] = "ko-KR"
    license: Literal["Apache-2.0"] = "Apache-2.0"
    source_url: Literal[
        "https://alphacephei.com/vosk/models/vosk-model-small-ko-0.22.zip"
    ]
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: tuple[VoskModelFile, ...] = Field(min_length=15, max_length=32)

    @model_validator(mode="after")
    def inventory_is_exact(self) -> VoskBundleManifest:
        paths = tuple(item.path for item in self.files)
        if len(paths) != len(set(paths)) or set(paths) != _REQUIRED_FILES:
            raise ValueError("Vosk model inventory is not the pinned Korean model layout")
        return self


class LoadedVoskBundle(FrozenModel):
    root: Path
    manifest: VoskBundleManifest

    def verify(self) -> bool:
        if self.root.is_symlink() or not self.root.is_dir():
            return False
        actual = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path != self.root / "manifest.json"
        }
        if actual != _REQUIRED_FILES:
            return False
        return all(_matches(self.root, item) for item in self.manifest.files)


def load_vosk_bundle(
    manifest_path: Path,
    *,
    max_manifest_bytes: int = 32 * 1024,
    max_file_bytes: int = 200 * 1024 * 1024,
    max_total_bytes: int = 320 * 1024 * 1024,
) -> LoadedVoskBundle:
    if min(max_manifest_bytes, max_file_bytes, max_total_bytes) < 1:
        raise ValueError("bundle limits must be positive")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise VoskBundleError("Vosk manifest must be a regular non-symlink file")
    root = manifest_path.parent
    if root.is_symlink() or not root.is_dir():
        raise VoskBundleError("Vosk bundle root must be a non-symlink directory")
    root = root.resolve(strict=True)
    try:
        if manifest_path.stat().st_size > max_manifest_bytes:
            raise VoskBundleError("Vosk manifest exceeded size limit")
        raw = manifest_path.read_bytes()
        if len(raw) > max_manifest_bytes:
            raise VoskBundleError("Vosk manifest exceeded size limit")
        manifest = VoskBundleManifest.model_validate(
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        )
    except VoskBundleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise VoskBundleError("invalid Vosk bundle manifest") from error

    total = 0
    for item in manifest.files:
        candidate = root.joinpath(*PurePosixPath(item.path).parts)
        _reject_symlink_components(root, candidate)
        try:
            resolved = candidate.resolve(strict=True)
            size = resolved.stat().st_size
        except OSError as error:
            raise VoskBundleError("Vosk model file is unavailable") from error
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise VoskBundleError("Vosk model file escaped its root")
        if size != item.size or size > max_file_bytes:
            raise VoskBundleError("Vosk model file size mismatch")
        total += size
        if total > max_total_bytes:
            raise VoskBundleError("Vosk bundle exceeded total size limit")
        if not _matches(root, item):
            raise VoskBundleError("Vosk model file hash mismatch")
    loaded = LoadedVoskBundle(root=root, manifest=manifest)
    if not loaded.verify():
        raise VoskBundleError("Vosk model inventory contains missing or extra files")
    return loaded


def _matches(root: Path, item: VoskModelFile) -> bool:
    path = root.joinpath(*PurePosixPath(item.path).parts)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size != item.size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == item.sha256
    except OSError:
        return False


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise VoskBundleError("Vosk model path must not contain symlinks")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VoskBundleError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise VoskBundleError(f"non-finite JSON number: {value}")
