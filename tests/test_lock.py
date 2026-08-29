from __future__ import annotations

from pathlib import Path

import pytest

from remote_ricoh.lock import AlreadyRunningError, FileLock


def test_file_lock_blocks_second_instance(tmp_path: Path) -> None:
    lock_path = tmp_path / "job.lock"

    with FileLock(lock_path):
        with pytest.raises(AlreadyRunningError):
            with FileLock(lock_path):
                pass

    assert not lock_path.exists()


def test_file_lock_recovers_stale_pid(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / "job.lock"
    lock_path.write_text("pid=987654\n", encoding="utf-8")
    monkeypatch.setattr("remote_ricoh.lock._pid_is_alive", lambda pid: False)

    with FileLock(lock_path):
        assert "token=" in lock_path.read_text(encoding="utf-8")
        assert lock_path.stat().st_mode & 0o777 == 0o600

    assert not lock_path.exists()


def test_file_lock_keeps_malformed_existing_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "job.lock"
    lock_path.write_text("unknown owner\n", encoding="utf-8")

    with pytest.raises(AlreadyRunningError):
        with FileLock(lock_path):
            pass

    assert lock_path.read_text(encoding="utf-8") == "unknown owner\n"


def test_file_lock_does_not_remove_replaced_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "job.lock"

    with FileLock(lock_path):
        lock_path.write_text("pid=1\ntoken=replaced\n", encoding="utf-8")

    assert lock_path.read_text(encoding="utf-8") == "pid=1\ntoken=replaced\n"
