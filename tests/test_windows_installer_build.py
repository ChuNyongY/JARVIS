from __future__ import annotations

from pathlib import Path

import pytest

from javis_core.package_manifest import PackageManifestError
from tools.build_windows_installer import (
    InstallerBuildError,
    create_payload_archive,
    render_install_script,
    resolve_installer_paths,
)


def test_install_template_never_enables_startup_or_embeds_secret(tmp_path: Path) -> None:
    template = Path("packaging/install_package.cmd")
    destination = tmp_path / "install.cmd"

    render_install_script(template, destination, "a" * 64)

    rendered = destination.read_text(encoding="utf-8")
    assert "__VERSION__" not in rendered
    assert "__PAYLOAD_SHA256__" not in rendered
    assert "install-autostart" not in rendered
    assert "nvapi-" not in rendered
    assert "powershell" not in rendered.casefold()
    assert "certutil.exe" in rendered
    assert "tar.exe" in rendered
    assert ".installing-" in rendered


def test_payload_archive_requires_a_verified_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "JAVIS.exe").write_bytes(b"not-manifested")

    with pytest.raises(PackageManifestError):
        create_payload_archive(package, tmp_path / "payload.zip")

    assert not (tmp_path / "payload.zip").exists()


def test_install_template_rejects_invalid_hash(tmp_path: Path) -> None:
    with pytest.raises(InstallerBuildError, match="HASH_INVALID"):
        render_install_script(
            Path("packaging/install_package.cmd"),
            tmp_path / "install.cmd",
            "not-a-hash",
        )


def test_installer_paths_accept_verified_custom_package_and_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    package = tmp_path / "package"
    output = tmp_path / "output"
    for directory in (repository, package, output):
        directory.mkdir()
    observed: list[Path] = []
    monkeypatch.setattr(
        "tools.build_windows_installer.verify_package_manifest",
        lambda path: observed.append(path),
    )

    resolved_package, setup, checksum = resolve_installer_paths(
        repository,
        package=package,
        destination=output / "JAVIS-Candidate.exe",
    )

    assert resolved_package == package.resolve()
    assert setup == output.resolve() / "JAVIS-Candidate.exe"
    assert checksum == output.resolve() / "JAVIS-Candidate.exe.sha256"
    assert observed == [package.resolve()]


@pytest.mark.parametrize("existing_name", ["JAVIS.exe", "JAVIS.exe.sha256"])
def test_installer_paths_never_overwrite_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_name: str,
) -> None:
    repository = tmp_path / "repository"
    package = tmp_path / "package"
    output = tmp_path / "output"
    for directory in (repository, package, output):
        directory.mkdir()
    (output / existing_name).write_bytes(b"existing")
    monkeypatch.setattr(
        "tools.build_windows_installer.verify_package_manifest",
        lambda _path: None,
    )

    with pytest.raises(InstallerBuildError, match="DESTINATION_EXISTS"):
        resolve_installer_paths(
            repository,
            package=package,
            destination=output / "JAVIS.exe",
        )


def test_installer_paths_reject_invalid_custom_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    package = tmp_path / "package"
    repository.mkdir()
    package.mkdir()
    monkeypatch.setattr(
        "tools.build_windows_installer.verify_package_manifest",
        lambda _path: None,
    )

    with pytest.raises(InstallerBuildError, match="DESTINATION_INVALID"):
        resolve_installer_paths(
            repository,
            package=package,
            destination=tmp_path / "missing" / "JAVIS.zip",
        )
