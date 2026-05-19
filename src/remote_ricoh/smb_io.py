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
        unique_target = self._resolve_unique_target(target)
        self._ensure_parent(unique_target)
        with smbclient.open_file(unique_target, mode="wb") as handle:
            handle.write(payload)
        return unique_target

    def append_log_line(self, relative_parts: Iterable[str], line: str) -> str:
        """Dopisuje pojedyncza linie logu UTF-8 do pliku na SMB."""
        target = self._join(relative_parts)
        self._ensure_parent(target)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with smbclient.open_file(target, mode="a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{timestamp}] {line}\n")
        return target

    def list_directory(self, relative_parts: Iterable[str] = ()) -> list[str]:
        """Listuje zawartosc katalogu i zwraca posortowane nazwy wpisow."""
        target = self._join(relative_parts)
        entries = smbclient.listdir(target)
        return sorted(str(item) for item in entries)

    def ensure_directory(self, relative_parts: Iterable[str] = ()) -> str:
        """Zapewnia istnienie katalogu i zwraca jego sciezke UNC."""
        target = self._join(relative_parts)
        smbclient.makedirs(target, exist_ok=True)
        return target

    def _join(self, relative_parts: Iterable[str]) -> str:
        path = PureWindowsPath(self.base_unc)
        for part in relative_parts:
            cleaned = PureWindowsPath(part).name
            path = path / cleaned
        return str(path)

    def _resolve_unique_target(self, target_unc: str) -> str:
        """Dla istniejacego pliku wybiera nazwe z kolejnym sufiksem (n)."""
        if not smbclient.path.exists(target_unc):
            return target_unc

        target_path = PureWindowsPath(target_unc)
        stem = target_path.stem
        suffix = target_path.suffix
        parent = target_path.parent

        counter = 1
        while True:
            candidate = str(parent / f"{stem}({counter}){suffix}")
            if not smbclient.path.exists(candidate):
                return candidate
            counter += 1

    def _ensure_parent(self, target_unc: str) -> None:
        parent = str(PureWindowsPath(target_unc).parent)
        smbclient.makedirs(parent, exist_ok=True)
