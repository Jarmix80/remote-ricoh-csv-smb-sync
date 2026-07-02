"""Wejscie i raportowanie dla usuwania urzadzen Ricoh po numerach seryjnych."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SERIAL_HEADER_NAMES = {
    "serial",
    "device_sn",
    "device serial",
    "device serial number",
    "nr seryjny",
    "numer seryjny",
}


@dataclass(frozen=True, slots=True)
class DeviceDeleteReportRow:
    """Pojedynczy wynik dry-run/usuwania urzadzenia."""

    serial: str
    status: str
    matched_count: int
    device_id: str = ""
    model: str = ""
    customer: str = ""
    requested_status: str = ""
    last_report_time: str = ""
    message: str = ""

    def as_csv_row(self) -> dict[str, str | int]:
        """Zwraca wiersz raportu CSV."""
        return {
            "serial": self.serial,
            "status": self.status,
            "matched_count": self.matched_count,
            "device_id": self.device_id,
            "model": self.model,
            "customer": self.customer,
            "requested_status": self.requested_status,
            "last_report_time": self.last_report_time,
            "message": self.message,
        }


def load_delete_serials(path: Path) -> list[str]:
    """Wczytuje unikalne numery seryjne z TXT albo CSV."""
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Brak pliku z numerami seryjnymi: {source}")

    if source.suffix.casefold() == ".csv":
        serials = _load_csv_serials(source)
    else:
        serials = _load_txt_serials(source)

    return _deduplicate(serials)


def write_delete_report(
    rows: list[DeviceDeleteReportRow],
    report_dir: Path = Path(".debug/ricoh_device_delete"),
) -> Path:
    """Zapisuje lokalny raport CSV i zwraca sciezke pliku."""
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"delete_report_{timestamp}.csv"

    fieldnames = [
        "serial",
        "status",
        "matched_count",
        "device_id",
        "model",
        "customer",
        "requested_status",
        "last_report_time",
        "message",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())

    return report_path


def _load_txt_serials(path: Path) -> list[str]:
    serials: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        serial = line.strip()
        if not serial or serial.startswith("#"):
            continue
        serials.append(serial)
    return serials


def _load_csv_serials(path: Path) -> list[str]:
    raw_lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not raw_lines:
        return []

    rows = list(csv.reader(raw_lines))
    if not rows:
        return []

    header = [item.strip().casefold() for item in rows[0]]
    serial_column = next(
        (idx for idx, name in enumerate(header) if name in SERIAL_HEADER_NAMES), None
    )
    first_data_row = 1 if serial_column is not None else 0
    if serial_column is None:
        serial_column = 0

    serials: list[str] = []
    for row in rows[first_data_row:]:
        if serial_column >= len(row):
            continue
        serial = row[serial_column].strip()
        if serial:
            serials.append(serial)
    return serials


def _deduplicate(serials: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for serial in serials:
        key = serial.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(serial)
    return result
