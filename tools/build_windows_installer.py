from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

from javis_core.package_manifest import verify_package_manifest
from javis_core.release_signing import ReleaseSigner, WindowsAuthenticodeSigner

VERSION = "0.1.0"
PAYLOAD_NAME = "JAVIS-PC-payload.zip"


class InstallerBuildError(RuntimeError):
    pass


def create_payload_archive(package: Path, destination: Path) -> str:
    root = package.resolve(strict=True)
    verify_package_manifest(root)
    if destination.exists() or destination.is_symlink():
        raise InstallerBuildError("PAYLOAD_DESTINATION_INVALID")
    digest = hashlib.sha256()
    try:
        with zipfile.ZipFile(
            destination,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            for source in sorted(root.rglob("*"), key=lambda path: path.as_posix()):
                if source.is_dir():
                    continue
                if source.is_symlink() or _is_reparse_point(source) or not source.is_file():
                    raise InstallerBuildError("PAYLOAD_FILE_INVALID")
                relative = source.relative_to(root).as_posix()
                information = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
                information.compress_type = zipfile.ZIP_DEFLATED
                information.create_system = 0
                with source.open("rb") as handle:
                    archive.writestr(information, handle.read(), compresslevel=6)
        with destination.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def render_install_script(template: Path, destination: Path, payload_hash: str) -> None:
    if len(payload_hash) != 64 or any(
        character not in "0123456789abcdef" for character in payload_hash
    ):
        raise InstallerBuildError("PAYLOAD_HASH_INVALID")
    raw = template.read_text(encoding="utf-8")
    if raw.count("__VERSION__") != 1 or raw.count("__PAYLOAD_SHA256__") != 1:
        raise InstallerBuildError("INSTALL_TEMPLATE_INVALID")
    rendered = raw.replace("__VERSION__", VERSION).replace(
        "__PAYLOAD_SHA256__",
        payload_hash,
    )
    destination.write_text(rendered, encoding="utf-8", newline="\r\n")


def build_installer(
    repository: Path,
    *,
    signer: ReleaseSigner | None = None,
    package: Path | None = None,
    destination: Path | None = None,
) -> tuple[Path, str]:
    root = repository.resolve(strict=True)
    package_root, setup, checksum = resolve_installer_paths(
        root,
        package=package,
        destination=destination,
    )
    build_parent = root / "build" / "windows-installer"
    build_parent.mkdir(parents=True, exist_ok=True)
    temporary = build_parent / f"build-{uuid4().hex}"
    temporary.mkdir()
    try:
        payload = temporary / PAYLOAD_NAME
        payload_hash = create_payload_archive(package_root, payload)
        install_script = temporary / "install.cmd"
        render_install_script(
            root / "packaging" / "install_package.cmd",
            install_script,
            payload_hash,
        )
        sed = temporary / "installer.sed"
        _write_sed(sed, temporary, setup)
        iexpress = (
            Path(Path(sys.executable).anchor)
            / "Windows"
            / "System32"
            / "iexpress.exe"
        )
        if not iexpress.is_file():
            raise InstallerBuildError("IEXPRESS_UNAVAILABLE")
        result = subprocess.run(
            [str(iexpress), "/N", str(sed)],
            cwd=temporary,
            check=False,
            timeout=900,
        )
        if result.returncode != 0 or not setup.is_file():
            setup.unlink(missing_ok=True)
            raise InstallerBuildError("IEXPRESS_BUILD_FAILED")
        if signer is not None:
            signer.sign(setup.resolve(strict=True))
        setup_hash = _sha256(setup)
        checksum.write_text(f"{setup_hash}  {setup.name}\n", encoding="ascii")
        return setup, setup_hash
    finally:
        if temporary.exists():
            _remove_owned_temporary(temporary, build_parent)


def resolve_installer_paths(
    repository: Path,
    *,
    package: Path | None = None,
    destination: Path | None = None,
) -> tuple[Path, Path, Path]:
    root = repository.resolve(strict=True)
    package_candidate = package or root / "dist" / f"JAVIS-PC-{VERSION}"
    if package_candidate.is_symlink() or not package_candidate.is_dir():
        raise InstallerBuildError("PACKAGE_INVALID")
    package_root = package_candidate.resolve(strict=True)
    verify_package_manifest(package_root)

    setup_candidate = destination or root / "dist" / f"JAVIS-PC-{VERSION}-Setup.exe"
    if setup_candidate.is_symlink() or setup_candidate.suffix.lower() != ".exe":
        raise InstallerBuildError("INSTALLER_DESTINATION_INVALID")
    try:
        setup_parent = setup_candidate.parent.resolve(strict=True)
    except OSError as error:
        raise InstallerBuildError("INSTALLER_DESTINATION_INVALID") from error
    if not setup_parent.is_dir():
        raise InstallerBuildError("INSTALLER_DESTINATION_INVALID")
    setup = setup_parent / setup_candidate.name
    checksum = setup.with_suffix(".exe.sha256")
    if (
        setup.exists()
        or setup.is_symlink()
        or checksum.exists()
        or checksum.is_symlink()
    ):
        raise InstallerBuildError("INSTALLER_DESTINATION_EXISTS")
    return package_root, setup, checksum


def _write_sed(path: Path, source: Path, setup: Path) -> None:
    source_value = str(source).rstrip("\\") + "\\"
    document = f"""[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[SourceFiles]
SourceFiles0=%SourceDirectory%
[SourceFiles0]
%FILE0%=
%FILE1%=
[Strings]
InstallPrompt=
FinishMessage=
TargetName="{setup}"
FriendlyName="JAVIS-PC {VERSION} per-user installer"
AppLaunched="cmd.exe /d /c call install.cmd"
SourceDirectory="{source_value}"
FILE0="{PAYLOAD_NAME}"
FILE1="install.cmd"
"""
    path.write_text(document, encoding="utf-8", newline="\r\n")


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_owned_temporary(path: Path, parent: Path) -> None:
    resolved_parent = parent.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if resolved_path.parent != resolved_parent or not resolved_path.name.startswith("build-"):
        raise InstallerBuildError("TEMPORARY_DIRECTORY_INVALID")
    shutil.rmtree(resolved_path)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--certificate-thumbprint")
    parser.add_argument("--expected-publisher")
    parser.add_argument("--timestamp-url")
    arguments = parser.parse_args()
    signing_values = (
        arguments.certificate_thumbprint,
        arguments.expected_publisher,
        arguments.timestamp_url,
    )
    if any(signing_values) != all(signing_values):
        raise InstallerBuildError("SIGNING_ARGUMENTS_INCOMPLETE")
    signer = (
        WindowsAuthenticodeSigner(*signing_values)
        if all(signing_values)
        else None
    )
    repository = Path(__file__).resolve().parent.parent
    setup, digest = build_installer(
        repository,
        signer=signer,
        package=arguments.package,
        destination=arguments.destination,
    )
    print(f"installer={setup}")
    print(f"sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
