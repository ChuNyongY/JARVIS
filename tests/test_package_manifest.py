from __future__ import annotations

import json
from pathlib import Path

import pytest

from javis_core.package_manifest import (
    PackageManifestError,
    create_package_manifest,
    verify_package_manifest,
    write_package_manifest,
)


def test_package_manifest_round_trip_and_sorted_paths(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "nested").mkdir()
    (root / "z.txt").write_bytes(b"z")
    (root / "nested" / "a.bin").write_bytes(b"abc")

    destination = write_package_manifest(root, "0.1.0")
    verified = verify_package_manifest(root)

    assert destination == root / "package-manifest.json"
    assert [entry.path for entry in verified.files] == ["nested/a.bin", "z.txt"]
    assert verified.files[0].size == 3


def test_package_manifest_detects_tampering_and_extra_files(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    payload = root / "JAVIS.exe"
    payload.write_bytes(b"original")
    write_package_manifest(root, "0.1.0")
    payload.write_bytes(b"tampered")

    with pytest.raises(PackageManifestError, match="INTEGRITY"):
        verify_package_manifest(root)

    payload.write_bytes(b"original")
    (root / "unexpected.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="INTEGRITY"):
        verify_package_manifest(root)


def test_package_manifest_rejects_symlink_when_supported(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(PackageManifestError, match="REPARSE"):
        create_package_manifest(root, "0.1.0")


def test_package_manifest_rejects_duplicate_or_unsorted_document(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "payload").write_text("x", encoding="utf-8")
    document = {
        "schema_version": "1.0",
        "product": "JAVIS-PC",
        "version": "0.1.0",
        "files": [
            {"path": "z", "size": 1, "sha256": "0" * 64},
            {"path": "a", "size": 1, "sha256": "0" * 64},
        ],
    }
    (root / "package-manifest.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(PackageManifestError, match="MANIFEST_INVALID"):
        verify_package_manifest(root)
