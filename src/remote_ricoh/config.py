"""Ladowanie i walidacja konfiguracji dla procesu Ricoh -> SMB -> Firebird."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

WARSAW_TZ = ZoneInfo("Europe/Warsaw")
REQUEST_TIMEOUT_SECONDS = 15 * 60
POLL_INTERVAL_SECONDS = 30
DEFAULT_FB_PORT = "3050"
DEFAULT_FB_USER = "SYSDBA"
DEFAULT_FB_PASSWORD = "masterkey"
DEFAULT_WEEKLY_REPORT_SENDER_NAME = "Remote Ricoh"
PRINTRADAR_ALLOWED_VIEW = "integration.device_counter_readings"


class ConfigError(ValueError):
    """Blad walidacji konfiguracji."""


@dataclass(frozen=True, slots=True)
class EmailSettings:
    """Parametry firmowego SMTP dla raportow automatyzacji."""

    host: str
    port: int
    username: str
    password: str
    sender_address: str
    sender_name: str
    use_ssl: bool
    use_tls: bool
    weekly_report_recipients: tuple[str, ...]
    documaster_report_recipients: tuple[str, ...] = ()
    printradar_report_recipients: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrintRadarSettings:
    """Polaczenie tylko do odczytu z produkcyjnym widokiem PrintRadar."""

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_view: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    ssh_identity_file: Path
    ssh_remote_db_host: str
    ssh_remote_db_port: int
    tunnel_local_host: str
    tunnel_local_port: int


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
    email: EmailSettings | None = None
    email_warning: str | None = None
    documaster_allow_writes: bool = False
    printradar: PrintRadarSettings | None = None
    printradar_warning: str | None = None
    printradar_cmail_allow_writes: bool = False

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
            "email": None,
            "email_warning": None,
            "documaster_allow_writes": _parse_bool(
                "DOCUMASTER_ALLOW_WRITES",
                pick("DOCUMASTER_ALLOW_WRITES", "0"),
            ),
            "printradar": None,
            "printradar_warning": None,
            "printradar_cmail_allow_writes": _parse_bool(
                "PRINTRADAR_CMAIL_ALLOW_WRITES",
                pick("PRINTRADAR_CMAIL_ALLOW_WRITES", "0"),
            ),
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

        raw_printradar = {
            "db_host": pick("PRINTRADAR_DB_HOST"),
            "db_port": pick("PRINTRADAR_DB_PORT"),
            "db_name": pick("PRINTRADAR_DB_NAME"),
            "db_user": pick("PRINTRADAR_DB_USER"),
            "db_password": pick("PRINTRADAR_DB_PASSWORD"),
            "db_view": pick("PRINTRADAR_DB_VIEW"),
            "ssh_host": pick("PRINTRADAR_SSH_HOST"),
            "ssh_port": pick("PRINTRADAR_SSH_PORT"),
            "ssh_user": pick("PRINTRADAR_SSH_USER"),
            "ssh_identity_file": pick("PRINTRADAR_SSH_IDENTITY_FILE"),
            "ssh_remote_db_host": pick("PRINTRADAR_SSH_REMOTE_DB_HOST"),
            "ssh_remote_db_port": pick("PRINTRADAR_SSH_REMOTE_DB_PORT"),
            "tunnel_local_host": pick("PRINTRADAR_TUNNEL_LOCAL_HOST"),
            "tunnel_local_port": pick("PRINTRADAR_TUNNEL_LOCAL_PORT"),
        }
        if any(raw_printradar.values()):
            try:
                data["printradar"] = _build_printradar_settings(raw_printradar)
            except ConfigError as exc:
                data["printradar_warning"] = f"Konfiguracja PrintRadar pominieta: {exc}"

        raw_email = {
            "host": pick("EMAIL_HOST"),
            "port": pick("EMAIL_PORT"),
            "username": pick("EMAIL_USERNAME"),
            "password": pick("EMAIL_PASSWORD"),
            "sender_address": pick("EMAIL_SENDER_ADDRESS"),
            "sender_name": pick("EMAIL_SENDER_NAME"),
            "use_ssl": pick("EMAIL_USE_SSL"),
            "use_tls": pick("EMAIL_USE_TLS"),
            "weekly_report_to": pick("EMAIL_WEEKLY_REPORT_TO"),
            "documaster_report_to": pick("EMAIL_DOCUMASTER_REPORT_TO"),
            "printradar_report_to": pick("EMAIL_PRINTRADAR_REPORT_TO"),
        }
        if any(raw_email.values()):
            try:
                data["email"] = _build_email_settings(raw_email)
            except ConfigError as exc:
                data["email_warning"] = f"Konfiguracja e-mail pominieta: {exc}"

        return cls(**data)


def _build_email_settings(raw: dict[str, str]) -> EmailSettings:
    required_fields = [
        "host",
        "port",
        "username",
        "password",
        "sender_address",
        "weekly_report_to",
    ]
    missing = [name for name in required_fields if not raw[name]]
    if missing:
        raise ConfigError(f"brak wymaganych zmiennych EMAIL: {', '.join(missing)}")
    if "@" in raw["host"]:
        raise ConfigError("EMAIL_HOST musi byc nazwa serwera SMTP, nie adresem e-mail")

    try:
        port = int(raw["port"])
    except ValueError as exc:
        raise ConfigError("EMAIL_PORT musi byc liczba calkowita") from exc
    if port < 1 or port > 65535:
        raise ConfigError("EMAIL_PORT musi miescic sie w zakresie 1-65535")

    use_ssl = _parse_bool("EMAIL_USE_SSL", raw["use_ssl"])
    use_tls = _parse_bool("EMAIL_USE_TLS", raw["use_tls"])
    if use_ssl == use_tls:
        raise ConfigError(
            "dokladnie jedna z EMAIL_USE_SSL lub EMAIL_USE_TLS musi miec wartosc true"
        )

    sender_address = _validate_email_address("EMAIL_SENDER_ADDRESS", raw["sender_address"])
    recipients = tuple(
        _validate_email_address("EMAIL_WEEKLY_REPORT_TO", value)
        for value in raw["weekly_report_to"].split(",")
        if value.strip()
    )
    if not recipients:
        raise ConfigError("EMAIL_WEEKLY_REPORT_TO nie zawiera poprawnego adresata")
    documaster_recipients = tuple(
        _validate_email_address("EMAIL_DOCUMASTER_REPORT_TO", value)
        for value in (raw["documaster_report_to"] or raw["weekly_report_to"]).split(",")
        if value.strip()
    )
    printradar_recipients = tuple(
        _validate_email_address("EMAIL_PRINTRADAR_REPORT_TO", value)
        for value in (raw["printradar_report_to"] or raw["weekly_report_to"]).split(",")
        if value.strip()
    )

    return EmailSettings(
        host=raw["host"],
        port=port,
        username=raw["username"],
        password=raw["password"],
        sender_address=sender_address,
        sender_name=raw["sender_name"] or DEFAULT_WEEKLY_REPORT_SENDER_NAME,
        use_ssl=use_ssl,
        use_tls=use_tls,
        weekly_report_recipients=recipients,
        documaster_report_recipients=documaster_recipients,
        printradar_report_recipients=printradar_recipients,
    )


def _build_printradar_settings(raw: dict[str, str]) -> PrintRadarSettings:
    required = tuple(raw)
    missing = [name for name in required if not raw[name]]
    if missing:
        raise ConfigError("brak wymaganych zmiennych: " + ", ".join(missing))
    if raw["db_view"] != PRINTRADAR_ALLOWED_VIEW:
        raise ConfigError(f"PRINTRADAR_DB_VIEW musi wskazywac {PRINTRADAR_ALLOWED_VIEW}")
    if raw["db_host"] not in {"127.0.0.1", "localhost"}:
        raise ConfigError("PRINTRADAR_DB_HOST musi wskazywac lokalny tunel")
    if raw["tunnel_local_host"] not in {"127.0.0.1", "localhost"}:
        raise ConfigError("PRINTRADAR_TUNNEL_LOCAL_HOST musi byc lokalny")

    def port(name: str) -> int:
        try:
            value = int(raw[name])
        except ValueError as exc:
            raise ConfigError(f"{name.upper()} musi byc liczba calkowita") from exc
        if not 1 <= value <= 65535:
            raise ConfigError(f"{name.upper()} musi miescic sie w zakresie 1-65535")
        return value

    db_port = port("db_port")
    tunnel_local_port = port("tunnel_local_port")
    if db_port != tunnel_local_port:
        raise ConfigError("PRINTRADAR_DB_PORT i PRINTRADAR_TUNNEL_LOCAL_PORT musza byc identyczne")

    return PrintRadarSettings(
        db_host=raw["db_host"],
        db_port=db_port,
        db_name=raw["db_name"],
        db_user=raw["db_user"],
        db_password=raw["db_password"],
        db_view=raw["db_view"],
        ssh_host=raw["ssh_host"],
        ssh_port=port("ssh_port"),
        ssh_user=raw["ssh_user"],
        ssh_identity_file=Path(raw["ssh_identity_file"]).expanduser(),
        ssh_remote_db_host=raw["ssh_remote_db_host"],
        ssh_remote_db_port=port("ssh_remote_db_port"),
        tunnel_local_host=raw["tunnel_local_host"],
        tunnel_local_port=tunnel_local_port,
    )


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "tak", "yes", "y"}:
        return True
    if normalized in {"0", "false", "nie", "no", "n"}:
        return False
    raise ConfigError(f"{name} musi miec wartosc true albo false")


def _validate_email_address(name: str, value: str) -> str:
    address = value.strip()
    _, parsed = parseaddr(address)
    if not parsed or parsed != address or "@" not in parsed:
        raise ConfigError(f"{name} zawiera niepoprawny adres e-mail")
    return parsed


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
