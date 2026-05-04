"""Ladowanie i walidacja konfiguracji dla procesu Ricoh -> SMB."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

WARSAW_TZ = ZoneInfo("Europe/Warsaw")
REQUEST_TIMEOUT_SECONDS = 15 * 60
POLL_INTERVAL_SECONDS = 30


class ConfigError(ValueError):
    """Blad walidacji konfiguracji."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Zestaw wymaganych ustawien procesu."""

    login_ricoh: str
    pass_ricoh: str
    sciezka_remote: str
    user_smb: str
    pass_smb: str

    @classmethod
    def from_env_file(cls, env_file: Path) -> Settings:
        """Buduje konfiguracje z .env i nadpisujacych zmiennych procesu."""
        env_data = {k: str(v) for k, v in dotenv_values(env_file).items() if v is not None}

        def pick(key: str) -> str:
            value = os.getenv(key)
            if value is None:
                value = env_data.get(key, "")
            return value.strip()

        data = {
            "login_ricoh": pick("login_ricoh"),
            "pass_ricoh": pick("pass_ricoh"),
            "sciezka_remote": pick("sciezka_remote"),
            "user_smb": pick("user_smb"),
            "pass_smb": pick("pass_smb"),
        }

        missing = [name for name, value in data.items() if not value]
        if missing:
            raise ConfigError(f"Brak wymaganych zmiennych: {', '.join(missing)}")

        # Wymagamy UNC, np. \\host\share\folder
        _, normalized_unc = normalize_unc(data["sciezka_remote"])
        data["sciezka_remote"] = normalized_unc

        return cls(**data)


def normalize_unc(path_value: str) -> tuple[str, str]:
    """Normalizuje sciezke SMB do postaci UNC i zwraca (server, unc)."""
    text = path_value.replace("/", "\\")
    if not text.startswith("\\\\"):
        text = "\\\\" + text.lstrip("\\")

    match = re.match(r"^\\\\([^\\]+)\\([^\\]+)(.*)$", text)
    if match is None:
        raise ConfigError(f"Nieprawidlowy format UNC: {path_value}")

    server = match.group(1)
    share = match.group(2)
    rest = match.group(3).strip("\\")
    unc = f"\\\\{server}\\{share}"
    if rest:
        unc += "\\" + rest
    return server, unc


def today_suffix() -> str:
    """Zwraca date w formacie dd-mm-rrrr wg strefy Europe/Warsaw."""
    now = datetime.now(tz=WARSAW_TZ)
    return now.strftime("%d-%m-%Y")


def log_file_name_for_today() -> str:
    """Nazwa dziennego pliku logu na SMB."""
    now = datetime.now(tz=WARSAW_TZ)
    return f"ricoh_{now.strftime('%Y-%m-%d')}.log"
