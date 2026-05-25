"""Ladowanie i walidacja konfiguracji dla procesu Ricoh -> SMB -> Firebird."""

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
DEFAULT_FB_PORT = "3050"
DEFAULT_FB_USER = "SYSDBA"
DEFAULT_FB_PASSWORD = "masterkey"


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
    fb_mode: str | None
    fb_host: str | None
    fb_port: int | None
    fb_user: str | None
    fb_password: str | None
    fb_database: str | None
    fb_charset: str | None
    fb_role: str | None
    fb_local_copy_path: str | None
    firebird_warning: str | None = None

    @property
    def firebird_enabled(self) -> bool:
        """Zwraca True, gdy konfiguracja Firebirda jest kompletna i aktywna."""
        return self.fb_port is not None

    @classmethod
    def from_env_file(cls, env_file: Path) -> Settings:
        """Buduje konfiguracje z .env i nadpisujacych zmiennych procesu."""
        env_data = {k: str(v) for k, v in dotenv_values(env_file).items() if v is not None}

        def pick(key: str, default: str = "") -> str:
            value = os.getenv(key)
            if value is None:
                value = env_data.get(key, default)
            return value.strip()

        data: dict[str, object] = {
            "login_ricoh": pick("login_ricoh"),
            "pass_ricoh": pick("pass_ricoh"),
            "sciezka_remote": pick("sciezka_remote"),
            "user_smb": pick("user_smb"),
            "pass_smb": pick("pass_smb"),
            "fb_mode": None,
            "fb_host": None,
            "fb_port": None,
            "fb_user": None,
            "fb_password": None,
            "fb_database": None,
            "fb_charset": None,
            "fb_role": None,
            "fb_local_copy_path": None,
            "firebird_warning": None,
        }

        missing = [
            name
            for name in ("login_ricoh", "pass_ricoh", "sciezka_remote", "user_smb", "pass_smb")
            if not data[name]  # type: ignore[index]
        ]
        if missing:
            raise ConfigError(f"Brak wymaganych zmiennych: {', '.join(missing)}")

        # Wymagamy UNC, np. \\host\share\folder
        _, normalized_unc = normalize_unc(str(data["sciezka_remote"]))
        data["sciezka_remote"] = normalized_unc

        raw_fb_input = {
            "fb_mode": pick("FB_MODE"),
            "fb_host": pick("FB_HOST"),
            "fb_port": pick("FB_PORT"),
            "fb_user": pick("FB_USER"),
            "fb_password": pick("FB_PASSWORD"),
            "fb_database": pick("FB_DATABASE"),
            "fb_charset": pick("FB_CHARSET"),
            "fb_role": pick("FB_ROLE"),
            "fb_local_copy_path": pick("FB_LOCAL_COPY_PATH"),
        }
        fb_config_present = any(raw_fb_input.values())

        raw_fb = {
            **raw_fb_input,
            "fb_port": raw_fb_input["fb_port"] or DEFAULT_FB_PORT,
            "fb_user": raw_fb_input["fb_user"] or DEFAULT_FB_USER,
            "fb_password": raw_fb_input["fb_password"] or DEFAULT_FB_PASSWORD,
        }

        if fb_config_present:
            fb_mode = (raw_fb["fb_mode"] or "network").casefold()
            if fb_mode not in {"network", "local"}:
                data["firebird_warning"] = (
                    "Konfiguracja Firebird pominieta: FB_MODE musi miec wartosc "
                    "'network' albo 'local'."
                )
            else:
                required_fields = ["fb_port", "fb_user", "fb_password", "fb_database"]
                if fb_mode == "network":
                    required_fields.insert(0, "fb_host")

                missing_fb = [name for name in required_fields if not raw_fb[name]]
                if missing_fb:
                    data["firebird_warning"] = (
                        "Konfiguracja Firebird pominieta: brak wymaganych zmiennych: "
                        + ", ".join(missing_fb)
                    )
                else:
                    try:
                        fb_port = int(raw_fb["fb_port"])
                    except ValueError:
                        data["firebird_warning"] = (
                            "Konfiguracja Firebird pominieta: nieprawidlowy port Firebird: "
                            f"{raw_fb['fb_port']}"
                        )
                    else:
                        if fb_port < 1 or fb_port > 65535:
                            data["firebird_warning"] = (
                                "Konfiguracja Firebird pominieta: FB_PORT musi miescic sie "
                                "w zakresie 1-65535."
                            )
                        else:
                            data["fb_mode"] = fb_mode
                            data["fb_host"] = raw_fb["fb_host"] or None
                            data["fb_port"] = fb_port
                            data["fb_user"] = raw_fb["fb_user"] or None
                            data["fb_password"] = raw_fb["fb_password"] or None
                            data["fb_database"] = raw_fb["fb_database"] or None
                            data["fb_charset"] = raw_fb["fb_charset"] or "WIN1250"
                            data["fb_role"] = raw_fb["fb_role"] or None
                            data["fb_local_copy_path"] = raw_fb["fb_local_copy_path"] or None

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
