from pathlib import Path

import pytest

from javis_core import FileLockTimeout, InterprocessFileLock


def test_interprocess_lock_creates_one_byte_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "state.lock"
    with InterprocessFileLock(path):
        assert path.exists()

    assert path.read_bytes() == b"\0"
    with InterprocessFileLock(path):
        assert path.stat().st_size == 1


def test_invalid_lock_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        InterprocessFileLock(tmp_path / "lock", timeout_seconds=0)


def test_second_file_handle_times_out_while_byte_is_locked(tmp_path: Path) -> None:
    path = tmp_path / "contended.lock"
    with (
        InterprocessFileLock(path),
        pytest.raises(FileLockTimeout),
        InterprocessFileLock(path, timeout_seconds=0.1),
    ):
        pass
