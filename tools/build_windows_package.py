from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from javis_core.package_manifest import (
    verify_package_manifest,
    write_package_manifest,
)
from javis_core.release_signing import ReleaseSigner, WindowsAuthenticodeSigner

VERSION = "0.1.0"
ASSET_DIRECTORIES = (
    Path("config"),
    Path("models"),
    Path("native/WindowsHelloHelper/publish-fdd"),
)


class PackageBuildError(RuntimeError):
    pass


def build_package(
    repository: Path,
    destination: Path,
    *,
    signer: ReleaseSigner | None = None,
) -> Path:
    root = _trusted_directory(repository, "REPOSITORY_INVALID")
    destination = _new_absolute_destination(destination)
    build_parent = root / "build" / "windows-package"
    build_parent.mkdir(parents=True, exist_ok=True)
    temporary = build_parent / f"build-{uuid4().hex}"
    temporary.mkdir()
    staging = temporary / "staging"
    try:
        _run_pyinstaller(root, temporary)
        frozen = temporary / "dist" / "JAVIS"
        if not (frozen / "JAVIS.exe").is_file() or not (
            frozen / "JAVIS-Tray.exe"
        ).is_file():
            raise PackageBuildError("FROZEN_EXECUTABLE_MISSING")
        frozen.replace(staging)
        for relative in ASSET_DIRECTORIES:
            source = root / relative
            _copy_trusted_tree(source, staging / relative)
        readme = root / "docs" / "PACKAGE_README.ko.txt"
        _copy_trusted_file(readme, staging / readme.name)
        if signer is not None:
            signer.sign((staging / "JAVIS.exe").resolve(strict=True))
            signer.sign((staging / "JAVIS-Tray.exe").resolve(strict=True))
        write_package_manifest(staging.resolve(strict=True), VERSION)
        verified = verify_package_manifest(staging.resolve(strict=True))
        if not any(entry.path == "JAVIS.exe" for entry in verified.files):
            raise PackageBuildError("PACKAGE_EXECUTABLE_UNMANIFESTED")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
        return destination
    except Exception:
        if destination.exists():
            raise
        raise
    finally:
        if temporary.exists():
            _remove_owned_temporary(temporary, build_parent)


def _run_pyinstaller(root: Path, temporary: Path) -> None:
    specification = root / "tools" / "javis_windows.spec"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(temporary / "dist"),
        "--workpath",
        str(temporary / "work"),
        str(specification),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            timeout=1_800,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PackageBuildError("PYINSTALLER_FAILED") from error
    if result.returncode != 0:
        raise PackageBuildError("PYINSTALLER_FAILED")


def _copy_trusted_tree(source: Path, destination: Path) -> None:
    root = _trusted_directory(source, "ASSET_DIRECTORY_INVALID")
    destination.mkdir(parents=True, exist_ok=False)
    for candidate in sorted(root.rglob("*"), key=lambda path: path.as_posix()):
        relative = candidate.relative_to(root)
        if candidate.is_symlink() or _is_reparse_point(candidate):
            raise PackageBuildError("ASSET_REPARSE_POINT_REJECTED")
        target = destination / relative
        if candidate.is_dir():
            target.mkdir(exist_ok=False)
        elif candidate.is_file():
            _copy_trusted_file(candidate, target)
        else:
            raise PackageBuildError("ASSET_FILE_INVALID")


def _copy_trusted_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or _is_reparse_point(source):
        raise PackageBuildError("ASSET_REPARSE_POINT_REJECTED")
    try:
        resolved = source.resolve(strict=True)
    except OSError as error:
        raise PackageBuildError("ASSET_FILE_INVALID") from error
    if not resolved.is_file() or destination.exists():
        raise PackageBuildError("ASSET_FILE_INVALID")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved, destination, follow_symlinks=False)


def _trusted_directory(path: Path, code: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or _is_reparse_point(path):
        raise PackageBuildError(code)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PackageBuildError(code) from error
    if not resolved.is_dir():
        raise PackageBuildError(code)
    return resolved


def _new_absolute_destination(path: Path) -> Path:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise PackageBuildError("DESTINATION_INVALID")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_symlink() or _is_reparse_point(parent):
        raise PackageBuildError("DESTINATION_INVALID")
    return parent / path.name


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _remove_owned_temporary(path: Path, parent: Path) -> None:
    resolved_parent = parent.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if resolved_path.parent != resolved_parent or not resolved_path.name.startswith("build-"):
        raise PackageBuildError("TEMPORARY_DIRECTORY_INVALID")
    shutil.rmtree(resolved_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("dist") / f"JAVIS-PC-{VERSION}",
    )
    parser.add_argument("--certificate-thumbprint")
    parser.add_argument("--expected-publisher")
    parser.add_argument("--timestamp-url")
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parent.parent
    destination = arguments.destination
    if not destination.is_absolute():
        destination = repository / destination
    signing_values = (
        arguments.certificate_thumbprint,
        arguments.expected_publisher,
        arguments.timestamp_url,
    )
    if any(signing_values) != all(signing_values):
        raise PackageBuildError("SIGNING_ARGUMENTS_INCOMPLETE")
    signer = (
        WindowsAuthenticodeSigner(*signing_values)
        if all(signing_values)
        else None
    )
    built = build_package(repository, destination, signer=signer)
    manifest = verify_package_manifest(built)
    print(f"built={built}")
    print(f"files={len(manifest.files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
