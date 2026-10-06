from __future__ import annotations

from pathlib import Path

import pytest

from javis_core.package_manifest import PackageManifestError
from tools.build_windows_installer import (
    InstallerBuildError,
    create_payload_archive,
    render_install_script,
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
