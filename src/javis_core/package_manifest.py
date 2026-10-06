from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

from pydantic import Field, field_validator, model_validator

from .models import FrozenModel


class PackageManifestError(ValueError):
    pass


class PackageFileEntry(FrozenModel):
    path: str = Field(min_length=1, max_length=1_024)
    size: int = Field(ge=0, le=4_294_967_296)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_is_canonical_relative_posix(cls, value: str) -> str:
        parsed = PurePosixPath(value)
        if (
            parsed.is_absolute()
            or value != parsed.as_posix()
            or value.startswith("../")
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or "\\" in value
            or ":" in value
        ):
            raise ValueError("package path must be canonical relative POSIX")
        return value


class PackageManifest(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    product: str = Field(default="JAVIS-PC", pattern=r"^JAVIS-PC$")
    version: str = Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z.+_-]+$")
    files: tuple[PackageFileEntry, ...] = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def paths_are_unique_and_sorted(self) -> PackageManifest:
        paths = [entry.path for entry in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("package file entries must be unique and sorted")
        return self


def create_package_manifest(
    root: Path,
    version: str,
    *,
    manifest_name: str = "package-manifest.json",
) -> PackageManifest:
    resolved = _trusted_root(root)
    _validate_manifest_name(manifest_name)
    entries = tuple(
        PackageFileEntry(
            path=path.relative_to(resolved).as_posix(),
            size=path.stat().st_size,
            sha256=_sha256(path),
        )
        for path in _package_files(resolved, manifest_name)
    )
    if not entries:
        raise PackageManifestError("PACKAGE_EMPTY")
    return PackageManifest(version=version, files=entries)


def write_package_manifest(
    root: Path,
    version: str,
    *,
    manifest_name: str = "package-manifest.json",
) -> Path:
    resolved = _trusted_root(root)
    manifest = create_package_manifest(
        resolved,
        version,
        manifest_name=manifest_name,
    )
    destination = resolved / manifest_name
    if destination.exists() or destination.is_symlink():
        raise PackageManifestError("PACKAGE_MANIFEST_EXISTS")
    payload = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("partial package manifest write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        if destination.exists() and not destination.is_symlink():
            destination.unlink(missing_ok=True)
        raise PackageManifestError("PACKAGE_MANIFEST_WRITE_FAILED") from error
    return destination


def verify_package_manifest(
    root: Path,
    *,
    manifest_name: str = "package-manifest.json",
) -> PackageManifest:
    resolved = _trusted_root(root)
    _validate_manifest_name(manifest_name)
    manifest_path = resolved / manifest_name
    if not _trusted_regular_file(manifest_path):
        raise PackageManifestError("PACKAGE_MANIFEST_INVALID")
    try:
        raw = manifest_path.read_bytes()
        if not 1 <= len(raw) <= 10_000_000:
            raise PackageManifestError("PACKAGE_MANIFEST_INVALID")
        manifest = PackageManifest.model_validate_json(raw)
    except PackageManifestError:
        raise
    except Exception as error:
        raise PackageManifestError("PACKAGE_MANIFEST_INVALID") from error
    observed = create_package_manifest(
        resolved,
        manifest.version,
        manifest_name=manifest_name,
    )
    if observed != manifest:
        raise PackageManifestError("PACKAGE_INTEGRITY_FAILED")
    return manifest


def _trusted_root(root: Path) -> Path:
    if not root.is_absolute() or root.is_symlink() or _is_reparse_point(root):
        raise PackageManifestError("PACKAGE_ROOT_INVALID")
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise PackageManifestError("PACKAGE_ROOT_INVALID") from error
    if not resolved.is_dir():
        raise PackageManifestError("PACKAGE_ROOT_INVALID")
    return resolved


def _package_files(root: Path, manifest_name: str) -> list[Path]:
    files: list[Path] = []
    try:
        candidates = sorted(root.rglob("*"), key=lambda path: path.as_posix())
    except OSError as error:
        raise PackageManifestError("PACKAGE_ENUMERATION_FAILED") from error
    for path in candidates:
        if path.name == manifest_name and path.parent == root:
            continue
        if path.is_symlink() or _is_reparse_point(path):
            raise PackageManifestError("PACKAGE_REPARSE_POINT_REJECTED")
        if path.is_dir():
            continue
        if not _trusted_regular_file(path):
            raise PackageManifestError("PACKAGE_FILE_INVALID")
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise PackageManifestError("PACKAGE_PATH_ESCAPED") from error
        files.append(path)
    return files


def _trusted_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and not _is_reparse_point(path)
    except OSError:
        return False


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise PackageManifestError("PACKAGE_FILE_UNREADABLE") from error
    return digest.hexdigest()


def _validate_manifest_name(value: str) -> None:
    if value != "package-manifest.json":
        raise PackageManifestError("PACKAGE_MANIFEST_NAME_INVALID")
