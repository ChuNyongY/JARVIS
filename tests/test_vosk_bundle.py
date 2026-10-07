from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from javis_core.vosk_bundle import VoskBundleError, load_vosk_bundle

FILES = (
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
)


def make_bundle(root: Path) -> Path:
    entries = []
    for index, relative in enumerate(FILES):
        payload = f"model-{index}".encode()
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        entries.append(
            {
                "path": relative,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "model_name": "vosk-model-small-ko-0.22",
                "locale": "ko-KR",
                "license": "Apache-2.0",
                "source_url": "https://alphacephei.com/vosk/models/vosk-model-small-ko-0.22.zip",
                "archive_sha256": "e" * 64,
                "files": entries,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_loads_and_reverifies_exact_pinned_inventory(tmp_path: Path) -> None:
    loaded = load_vosk_bundle(make_bundle(tmp_path / "model"))

    assert loaded.verify() is True
    (loaded.root / "conf" / "model.conf").write_bytes(b"changed")
    assert loaded.verify() is False


def test_rejects_extra_model_file(tmp_path: Path) -> None:
    manifest = make_bundle(tmp_path / "model")
    (manifest.parent / "extra.bin").write_bytes(b"extra")

    with pytest.raises(VoskBundleError, match="missing or extra"):
        load_vosk_bundle(manifest)


def test_rejects_nested_manifest_named_extra_file(tmp_path: Path) -> None:
    manifest = make_bundle(tmp_path / "model")
    extra = manifest.parent / "extra" / "manifest.json"
    extra.parent.mkdir()
    extra.write_text("{}", encoding="utf-8")

    with pytest.raises(VoskBundleError, match="missing or extra"):
        load_vosk_bundle(manifest)


def test_rejects_hash_mismatch(tmp_path: Path) -> None:
    manifest = make_bundle(tmp_path / "model")
    (manifest.parent / "am" / "final.mdl").write_bytes(b"tampered")

    with pytest.raises(VoskBundleError, match="size mismatch"):
        load_vosk_bundle(manifest)


def test_rejects_duplicate_manifest_key(tmp_path: Path) -> None:
    manifest = make_bundle(tmp_path / "model")
    manifest.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")

    with pytest.raises(VoskBundleError, match="duplicate JSON key"):
        load_vosk_bundle(manifest)
