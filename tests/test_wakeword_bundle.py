import hashlib
import json
from pathlib import Path

import pytest

from javis_core.wakeword_bundle import WakeWordBundleError, load_wakeword_bundle


def write_bundle(root: Path) -> Path:
    files = {
        "wakeword": "hey_jarvis.onnx",
        "melspectrogram": "melspectrogram.onnx",
        "embedding": "embedding_model.onnx",
    }
    document: dict[str, object] = {
        "schema_version": "1.0",
        "wakeword_key": "hey_jarvis",
    }
    for role, name in files.items():
        raw = role.encode()
        (root / name).write_bytes(raw)
        document[role] = {
            "path": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def test_bundle_loader_validates_all_hashes(tmp_path: Path) -> None:
    bundle = load_wakeword_bundle(write_bundle(tmp_path))

    assert bundle.verify()
    assert bundle.wakeword_key == "hey_jarvis"
    assert bundle.wakeword_path == (tmp_path / "hey_jarvis.onnx").resolve()


def test_bundle_detects_change_after_load(tmp_path: Path) -> None:
    bundle = load_wakeword_bundle(write_bundle(tmp_path))
    bundle.wakeword_path.write_bytes(b"changed")

    assert not bundle.verify()


def test_bundle_rejects_hash_mismatch_and_traversal(tmp_path: Path) -> None:
    manifest = write_bundle(tmp_path)
    document = json.loads(manifest.read_text())
    document["wakeword"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(document))
    with pytest.raises(WakeWordBundleError, match="hash"):
        load_wakeword_bundle(manifest)

    document["wakeword"]["path"] = "../outside.onnx"
    manifest.write_text(json.dumps(document))
    with pytest.raises(WakeWordBundleError, match="invalid"):
        load_wakeword_bundle(manifest)


def test_bundle_rejects_duplicate_json_key(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":"1.0","schema_version":"1.0"}')

    with pytest.raises(WakeWordBundleError, match="duplicate"):
        load_wakeword_bundle(manifest)
