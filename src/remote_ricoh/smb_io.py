"""Operacje zapisu plikow i logow na udziale SMB."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath

import smbclient

from .config import normalize_unc


@dataclass(slots=True)
class SmbClient:
    """Sesja SMB z uproszczonym API zapisu."""

    remote_unc: str
    username: str
    password: str
    server: str = field(init=False)
    base_unc: str = field(init=False)

    def __post_init__(self) -> None:
        server, unc = normalize_unc(self.remote_unc)
        self.server = server
        self.base_unc = unc.rstrip("\\")

    def __enter__(self) -> SmbClient:
        smbclient.register_session(
            server=self.server,
            username=self.username,
            password=self.password,
            port=445,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        smbclient.reset_connection_cache()

    def write_binary(self, relative_parts: Iterable[str], payload: bytes) -> str:
        """Zapisuje plik binarny na SMB i zwraca finalna sciezke UNC."""
        target = self._join(relative_parts)
        self._ensure_parent(target)
        with smbclient.open_file(target, mode="wb") as handle:
            handle.write(payload)
        return target

    def append_log_line(self, relative_parts: Iterable[str], line: str) -> str:
        """Dopisuje pojedyncza linie logu UTF-8 do pliku na SMB."""
        target = self._join(relative_parts)
        self._ensure_parent(target)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with smbclient.open_file(target, mode="a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{timestamp}] {line}\n")
        return target

    def _join(self, relative_parts: Iterable[str]) -> str:
        path = PureWindowsPath(self.base_unc)
        for part in relative_parts:
            cleaned = PureWindowsPath(part).name
            path = path / cleaned
        return str(path)

    def _ensure_parent(self, target_unc: str) -> None:
        parent = str(PureWindowsPath(target_unc).parent)
        smbclient.makedirs(parent, exist_ok=True)
