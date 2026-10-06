from __future__ import annotations

from pathlib import Path

from javis_core.package_manifest import write_package_manifest
from javis_core.release_audit import audit_windows_release


class FakePublisherVerifier:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = allowed

    def verify(self, executable: Path, expected_subject: str) -> bool:
        return expected_subject == "CN=Trusted" and executable.name in self.allowed


def _release_fixture(tmp_path: Path) -> tuple[Path, Path]:
    package = tmp_path / "package"
    package.mkdir()
    (package / "JAVIS.exe").write_bytes(b"console")
    (package / "JAVIS-Tray.exe").write_bytes(b"tray")
    write_package_manifest(package.resolve(), "0.1.0")
    setup = tmp_path / "JAVIS-PC-0.1.0-Setup.exe"
    setup.write_bytes(b"setup")
    from hashlib import sha256

    setup.with_suffix(".exe.sha256").write_text(
        f"{sha256(b'setup').hexdigest()}  {setup.name}\n",
        encoding="ascii",
    )
    return package.resolve(), setup.resolve()


def test_release_audit_requires_all_three_exact_signatures(tmp_path: Path) -> None:
    package, setup = _release_fixture(tmp_path)

    report = audit_windows_release(
        package,
        setup,
        "CN=Trusted",
        publisher_verifier=FakePublisherVerifier(
            {"JAVIS.exe", "JAVIS-Tray.exe", setup.name}
        ),
    )

    assert report.ready
    assert all(item.passed for item in report.items)


def test_release_audit_fails_closed_on_missing_signature_or_tampered_setup(
    tmp_path: Path,
) -> None:
    package, setup = _release_fixture(tmp_path)
    setup.write_bytes(b"tampered")

    report = audit_windows_release(
        package,
        setup,
        "CN=Trusted",
        publisher_verifier=FakePublisherVerifier({"JAVIS.exe"}),
    )

    assert not report.ready
    assert {item.component for item in report.items if not item.passed} == {
        "package.tray-signature",
        "setup.hash",
        "setup.signature",
    }
