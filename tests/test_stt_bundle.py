import hashlib
import json
from pathlib import Path

import pytest

from javis_core.stt_bundle import SttBundleError, load_stt_bundle

NAMES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def write_bundle(root: Path) -> Path:
    entries: dict[str, object] = {}
    for name in NAMES:
        raw = name.encode()
        (root / name).write_bytes(raw)
        entries[name] = {
            "path": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    document = {
        "schema_version": "1.0",
        "source_repository": "Systran/faster-whisper-tiny",
        "source_revision": "d" * 40,
        "files": entries,
    }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def test_stt_bundle_loads_and_reverifies(tmp_path: Path) -> None:
    bundle = load_stt_bundle(write_bundle(tmp_path))

    assert bundle.verify()
    assert bundle.source_revision == "d" * 40


def test_stt_bundle_detects_change_after_load(tmp_path: Path) -> None:
    bundle = load_stt_bundle(write_bundle(tmp_path))
    (bundle.root / "model.bin").write_bytes(b"changed")

    assert not bundle.verify()


def test_stt_bundle_rejects_wrong_filename_size_and_hash(tmp_path: Path) -> None:
    manifest = write_bundle(tmp_path)
    document = json.loads(manifest.read_text())
    document["files"]["model.bin"]["path"] = "other.bin"
    manifest.write_text(json.dumps(document))
    with pytest.raises(SttBundleError, match="filename"):
        load_stt_bundle(manifest)

    document["files"]["model.bin"]["path"] = "model.bin"
    document["files"]["model.bin"]["size"] = 1
    manifest.write_text(json.dumps(document))
    with pytest.raises(SttBundleError, match="size"):
        load_stt_bundle(manifest)

    document["files"]["model.bin"]["size"] = len("model.bin")
    document["files"]["model.bin"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(document))
    with pytest.raises(SttBundleError, match="hash"):
        load_stt_bundle(manifest)


def test_stt_bundle_rejects_duplicate_json_key(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":"1.0","schema_version":"1.0"}')

    with pytest.raises(SttBundleError, match="duplicate"):
        load_stt_bundle(manifest)
