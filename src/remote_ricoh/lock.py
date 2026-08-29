"""Prosty lockfile gwarantujacy pojedyncze uruchomienie procesu."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


class AlreadyRunningError(RuntimeError):
    """Proces jest juz uruchomiony."""


@dataclass(slots=True)
class FileLock:
    """Lock oparty o atomowe utworzenie pliku."""

    path: Path
    _payload: str = field(init=False, default="", repr=False)

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        for attempt in range(2):
            try:
                fd = os.open(self.path, flags, 0o600)
                break
            except FileExistsError as exc:
                if attempt == 0 and self._remove_stale_lock():
                    continue
                raise AlreadyRunningError(f"Lock juz istnieje: {self.path}") from exc
        else:  # pragma: no cover - petla zawsze konczy sie break albo wyjatkiem
            raise AlreadyRunningError(f"Nie mozna utworzyc locka: {self.path}")

        self._payload = f"pid={os.getpid()}\ntoken={uuid4().hex}\n"
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(self._payload)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        try:
            if self._payload and self.path.read_text(encoding="utf-8") == self._payload:
                self.path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except OSError:
            # Nie przerywamy procesu cleanup przy okazjonalnym bledzie I/O.
            pass

    def _remove_stale_lock(self) -> bool:
        """Usuwa lock tylko wtedy, gdy zapisany PID juz nie istnieje."""
        try:
            payload = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return True
        except OSError:
            return False

        pid_line = next((line for line in payload.splitlines() if line.startswith("pid=")), "")
        try:
            pid = int(pid_line.removeprefix("pid="))
        except ValueError:
            return False
        if pid <= 0 or _pid_is_alive(pid):
            return False

        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
