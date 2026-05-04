"""Rozpakowanie i mapowanie plikow CSV z archiwum ZIP Ricoh."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ZipContentError(RuntimeError):
    """Brak wymaganych plikow CSV lub niepoprawny ZIP."""


@dataclass(frozen=True, slots=True)
class ExtractResult:
    """Wynik ekstrakcji CSV z archiwum."""

    dplac_path: Path
    dplac_not_obtained_path: Path


def _classify_csv_name(file_name: str) -> str | None:
    lower = file_name.lower()
    if not lower.endswith(".csv"):
        return None
    if lower.startswith("dplac_not_obtained"):
        return "dplac_not_obtained"
    if lower.startswith("dplac"):
        return "dplac"
    return None


def extract_meter_csvs(zip_path: Path, output_dir: Path, date_suffix: str) -> ExtractResult:
    """Wyciaga wymagane CSV i zapisuje z nazwa zakonczona data dd-mm-rrrr."""
    output_dir.mkdir(parents=True, exist_ok=True)

    selected: dict[str, zipfile.ZipInfo] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            file_name = PurePosixPath(info.filename).name
            kind = _classify_csv_name(file_name)
            if kind is None:
                continue
            # Gdy wystapi duplikat prefiksu, bierzemy pierwszy wpis.
            selected.setdefault(kind, info)

        missing = [kind for kind in ("dplac", "dplac_not_obtained") if kind not in selected]
        if missing:
            raise ZipContentError(f"Brak wymaganych plikow CSV w ZIP: {', '.join(missing)}")

        dplac_raw = archive.read(selected["dplac"])
        dplac_no_raw = archive.read(selected["dplac_not_obtained"])

    dplac_name = f"DPLAC_{date_suffix}.csv"
    dplac_no_name = f"DPLAC_Not_obtained_{date_suffix}.csv"

    dplac_out = output_dir / dplac_name
    dplac_no_out = output_dir / dplac_no_name

    dplac_out.write_bytes(dplac_raw)
    dplac_no_out.write_bytes(dplac_no_raw)

    return ExtractResult(dplac_path=dplac_out, dplac_not_obtained_path=dplac_no_out)
