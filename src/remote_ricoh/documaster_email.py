"""Alerty e-mail dla automatycznego importu Documaster."""

from __future__ import annotations

from datetime import datetime

from .config import WARSAW_TZ, EmailSettings
from .documaster import DocumasterRunResult
from .weekly_email import _build_message, _safe_error_description, _send_message


def send_documaster_warning(
    settings: EmailSettings,
    result: DocumasterRunResult,
    *,
    sent_at: datetime | None = None,
) -> None:
    """Wysyla raport tylko wtedy, gdy skan zakonczyl sie ostrzezeniem."""
    now = sent_at or datetime.now(tz=WARSAW_TZ)
    lines = [
        "Import Documaster zakonczyl sie z ostrzezeniami.",
        "",
        f"Czas: {now:%Y-%m-%d %H:%M %Z}",
        f"Tryb wykonawczy: {'tak' if result.execute else 'nie'}",
        f"Pliki: {len(result.files)}",
        "",
        "Statusy wierszy:",
    ]
    lines.extend(f"- {status}: {count}" for status, count in sorted(result.status_counts.items()))
    lines.extend(["", "Pliki wymagajace uwagi:"])
    for item in result.files:
        if item.has_warning:
            lines.append(f"- {item.source_name}: {item.status} {item.message}".rstrip())
    lines.extend(["", "Szczegoly znajduja sie w zalaczonym raporcie CSV."])

    message = _build_message(
        settings,
        subject=f"Documaster - ostrzezenie importu {now:%Y-%m-%d %H:%M}",
        body="\n".join(lines),
        recipients=settings.documaster_report_recipients,
    )
    message.add_attachment(
        result.report_path.read_bytes(),
        maintype="text",
        subtype="csv",
        filename=result.report_path.name,
    )
    _send_message(settings, message)


def send_documaster_failure(
    settings: EmailSettings,
    error: Exception,
    *,
    redactions: tuple[str, ...] = (),
    sent_at: datetime | None = None,
) -> None:
    """Wysyla alert o bledzie uniemozliwiajacym zakonczenie skanu."""
    now = sent_at or datetime.now(tz=WARSAW_TZ)
    description = _safe_error_description(error, redactions)
    message = _build_message(
        settings,
        subject=f"Documaster - BLAD importu {now:%Y-%m-%d %H:%M}",
        body=(
            "Skan katalogu Documaster nie zakonczyl sie poprawnie.\n\n"
            f"Czas: {now:%Y-%m-%d %H:%M %Z}\n"
            f"Blad: {type(error).__name__}: {description}\n\n"
            "Szczegoly znajduja sie w logs/documaster.log."
        ),
        recipients=settings.documaster_report_recipients,
    )
    _send_message(settings, message)
