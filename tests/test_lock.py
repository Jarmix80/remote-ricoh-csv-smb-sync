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
