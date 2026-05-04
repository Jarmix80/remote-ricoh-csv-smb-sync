from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from remote_ricoh.zip_processing import ZipContentError, extract_meter_csvs


def _build_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def test_extract_meter_csvs_success(tmp_path: Path) -> None:
    zip_path = tmp_path / "payload.zip"
    _build_zip(
        zip_path,
        {
            "a/DPLAC.csv": b"dplac",
            "b/DPLAC_Not_obtained.csv": b"dplac_no",
            "x/other.txt": b"ignore",
        },
    )

    out = extract_meter_csvs(zip_path, tmp_path / "out", "04-05-2026")

    assert out.dplac_path.name == "DPLAC_04-05-2026.csv"
    assert out.dplac_not_obtained_path.name == "DPLAC_Not_obtained_04-05-2026.csv"
    assert out.dplac_path.read_bytes() == b"dplac"
    assert out.dplac_not_obtained_path.read_bytes() == b"dplac_no"


def test_extract_meter_csvs_missing_required_file(tmp_path: Path) -> None:
    zip_path = tmp_path / "payload.zip"
    _build_zip(zip_path, {"DPLAC.csv": b"dplac"})

    with pytest.raises(ZipContentError):
        extract_meter_csvs(zip_path, tmp_path / "out", "04-05-2026")
