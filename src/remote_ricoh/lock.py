"""Prosty lockfile gwarantujacy pojedyncze uruchomienie procesu."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    """Proces jest juz uruchomiony."""


@dataclass(slots=True)
class FileLock:
    """Lock oparty o atomowe utworzenie pliku."""

    path: Path

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileExistsError as exc:
            raise AlreadyRunningError(f"Lock juz istnieje: {self.path}") from exc

        payload = f"pid={os.getpid()}\n"
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            # Nie przerywamy procesu cleanup przy okazjonalnym bledzie I/O.
            pass
