"""Raporty e-mail synchronizacji PrintRadar -> CMAIL."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import WARSAW_TZ, EmailSettings
from .weekly_email import _build_message, _safe_error_description, _send_message


def send_printradar_scanner_report(
    settings: EmailSettings,
    report_path: Path,
    *,
    pending_count: int,
    sent_at: datetime | None = None,
) -> None:
    """Wysyla cotygodniowa kolejke licznikow skanera wymagajacych mapowania."""
    now = sent_at or datetime.now(tz=WARSAW_TZ)
    message = _build_message(
        settings,
        subject=f"PrintRadar - kolejka licznikow skanera {now:%Y-%m-%d}",
        body=(
            "Liczniki wydrukow sa synchronizowane z CMAIL.\n"
            "SCANNER_TOTAL pozostaje wstrzymany do walidacji mapowania per model.\n\n"
            f"Pozycje oczekujace: {pending_count}\n"
            "Szczegoly znajduja sie w zalaczonym raporcie CSV."
        ),
        recipients=settings.printradar_report_recipients,
    )
    message.add_attachment(
        report_path.read_bytes(),
        maintype="text",
        subtype="csv",
        filename=report_path.name,
    )
    _send_message(settings, message)


def send_printradar_failure(
    settings: EmailSettings,
    error: Exception,
    *,
    redactions: tuple[str, ...] = (),
    sent_at: datetime | None = None,
) -> None:
    """Wysyla alert o bledzie synchronizacji bez ujawniania sekretow."""
    now = sent_at or datetime.now(tz=WARSAW_TZ)
    description = _safe_error_description(error, redactions)
    message = _build_message(
        settings,
        subject=f"PrintRadar CMAIL - BLAD {now:%Y-%m-%d %H:%M}",
        body=(
            "Synchronizacja licznikow PrintRadar z CMAIL nie zakonczyla sie poprawnie.\n\n"
            f"Czas: {now:%Y-%m-%d %H:%M %Z}\n"
            f"Blad: {type(error).__name__}: {description}\n\n"
            "Szczegoly znajduja sie w logs/printradar_cmail.log."
        ),
        recipients=settings.printradar_report_recipients,
    )
    _send_message(settings, message)
