"""Raporty e-mail dla tygodniowej kontroli kolejki REMOTE."""

from __future__ import annotations

import csv
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from .config import WARSAW_TZ, EmailSettings
from .remote_auto import ORDER_STATUS_READY_DELETE, RemoteAutoRunResult


def send_weekly_success_report(
    settings: EmailSettings,
    result: RemoteAutoRunResult,
    *,
    sent_at: datetime | None = None,
) -> None:
    """Wysyla podsumowanie poprawnego tygodniowego skanu wraz z CSV."""
    now = sent_at or datetime.now(tz=WARSAW_TZ)
    ready_rows = _ready_delete_rows(result.local_report_path)
    message = _build_message(
        settings,
        subject=f"Remote Ricoh - raport tygodniowy {now:%Y-%m-%d}",
        body=_success_body(result, ready_rows, now),
    )
    payload = result.local_report_path.read_bytes()
    message.add_attachment(
        payload,
        maintype="text",
        subtype="csv",
        filename=result.local_report_path.name,
    )
    _send_message(settings, message)


def send_weekly_failure_alert(
    settings: EmailSettings,
    error: Exception,
    *,
    redactions: tuple[str, ...] = (),
    sent_at: datetime | None = None,
) -> None:
    """Wysyla alert o bledzie skanu bez ujawniania sekretow konfiguracji."""
    now = sent_at or datetime.now(tz=WARSAW_TZ)
    description = _safe_error_description(error, redactions)
    message = _build_message(
        settings,
        subject=f"Remote Ricoh - BLAD skanu tygodniowego {now:%Y-%m-%d}",
        body=(
            "Tygodniowy skan kolejki REMOTE nie zakonczyl sie poprawnie.\n\n"
            f"Czas: {now:%Y-%m-%d %H:%M %Z}\n"
            f"Blad: {type(error).__name__}: {description}\n\n"
            "Szczegoly wykonania znajduja sie w logs/remote_auto.log."
        ),
    )
    _send_message(settings, message)


def _success_body(
    result: RemoteAutoRunResult,
    ready_rows: list[dict[str, str]],
    sent_at: datetime,
) -> str:
    lines = [
        "Tygodniowy raport kolejki REMOTE.",
        "",
        f"Czas: {sent_at:%Y-%m-%d %H:%M %Z}",
        f"Tryb wykonawczy: {'tak' if result.execute else 'nie'}",
        f"Sprawdzone zlecenia: {result.scanned_orders}",
        f"Sprawdzone urzadzenia Remote: {result.remote_checked}",
        "",
        "Statusy:",
    ]
    if result.status_counts:
        lines.extend(
            f"- {status}: {count}" for status, count in sorted(result.status_counts.items())
        )
    else:
        lines.append("- brak pozycji do sprawdzenia")

    lines.extend(["", "Urzadzenia gotowe do usuniecia:"])
    if ready_rows:
        lines.extend(
            "- {order} | {serial} | Last Report: {last_report_time}".format(**row)
            for row in ready_rows
        )
    else:
        lines.append("- brak")

    lines.extend(["", "Pelny raport z tego uruchomienia jest w zalaczniku CSV."])
    return "\n".join(lines)


def _ready_delete_rows(report_path: Path) -> list[dict[str, str]]:
    if not report_path.is_file():
        raise FileNotFoundError(f"Brak raportu tygodniowego do wysylki: {report_path}")
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {
                "order": (row.get("order") or "").strip(),
                "serial": (row.get("serial") or "").strip(),
                "last_report_time": (row.get("last_report_time") or "").strip(),
            }
            for row in csv.DictReader(handle)
            if (row.get("status") or "").strip() == ORDER_STATUS_READY_DELETE
        ]


def _build_message(settings: EmailSettings, *, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.sender_name, settings.sender_address))
    message["To"] = ", ".join(settings.weekly_report_recipients)
    message["Subject"] = subject
    message.set_content(body)
    return message


def _send_message(settings: EmailSettings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if settings.use_ssl:
        with smtplib.SMTP_SSL(
            settings.host,
            settings.port,
            context=context,
            timeout=30,
        ) as client:
            client.login(settings.username, settings.password)
            client.send_message(message)
        return

    with smtplib.SMTP(settings.host, settings.port, timeout=30) as client:
        client.ehlo()
        if settings.use_tls:
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.username, settings.password)
        client.send_message(message)


def _safe_error_description(error: Exception, redactions: tuple[str, ...]) -> str:
    text = str(error).strip() or "brak opisu"
    for secret in redactions:
        if secret:
            text = text.replace(secret, "***")
    return text
