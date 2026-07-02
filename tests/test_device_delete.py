from __future__ import annotations

import csv
from pathlib import Path

from remote_ricoh.device_delete import (
    DeviceDeleteReportRow,
    load_delete_serials,
    write_delete_report,
)


def test_load_delete_serials_from_txt_ignores_comments_and_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "serials.txt"
    source.write_text(
        "\n".join(
            [
                "# komentarz",
                "T575H403598",
                "",
                "t575h403598",
                "ABC123",
            ]
        ),
        encoding="utf-8",
    )

    assert load_delete_serials(source) == ["T575H403598", "ABC123"]


def test_load_delete_serials_from_csv_serial_column(tmp_path: Path) -> None:
    source = tmp_path / "serials.csv"
    source.write_text(
        "comment,serial\npierwszy,T575H403598\ndrugi,ABC123\n",
        encoding="utf-8",
    )

    assert load_delete_serials(source) == ["T575H403598", "ABC123"]


def test_load_delete_serials_from_single_column_csv_without_header(tmp_path: Path) -> None:
    source = tmp_path / "serials.csv"
    source.write_text("T575H403598\nABC123\n", encoding="utf-8")

    assert load_delete_serials(source) == ["T575H403598", "ABC123"]


def test_write_delete_report(tmp_path: Path) -> None:
    report_path = write_delete_report(
        [
            DeviceDeleteReportRow(
                serial="T575H403598",
                status="would_delete",
                matched_count=1,
                device_id="T575H403598",
                model="RICOH SP 4510DN",
                customer="1447",
                requested_status="Removing",
                last_report_time="2025/12/29 21:29",
                message="dry-run",
            )
        ],
        tmp_path,
    )

    with report_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "serial": "T575H403598",
            "status": "would_delete",
            "matched_count": "1",
            "device_id": "T575H403598",
            "model": "RICOH SP 4510DN",
            "customer": "1447",
            "requested_status": "Removing",
            "last_report_time": "2025/12/29 21:29",
            "message": "dry-run",
        }
    ]
