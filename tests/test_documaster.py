from __future__ import annotations

import csv
import datetime as dt
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from remote_ricoh.documaster import (
    DocumasterFileResult,
    DocumasterFirebirdImporter,
    DocumasterParseError,
    DocumasterRecord,
    DocumasterRowResult,
    DocumasterStore,
    ParsedDocumasterFile,
    parse_documaster_file,
    write_documaster_report,
)
from remote_ricoh.firebird_cmail import DeviceMatch

HEADERS = [
    "Lp.",
    "Nazwa",
    "Adres sieciowy",
    "Numer seryjny",
    "Model",
    "Położenie",
    "Data ostatniego odczytu",
    "Licznik główny",
    "Licznik główny (zmiana)",
    "Suma Cz/B",
    "Suma Cz/B (zmiana)",
    "Suma Kolor",
    "Suma Kolor (zmiana)",
    "Kopiarka Cz/B",
    "Kopiarka Cz/B (zmiana)",
    "Kopiarka Kolor",
    "Kopiarka Kolor (zmiana)",
    "Drukarka Cz/B",
    "Drukarka Cz/B (zmiana)",
    "Drukarka Kolor",
    "Drukarka Kolor (zmiana)",
    "Skaner Cz/B",
    "Skaner Cz/B (zmiana)",
    "Skaner Kolor",
    "Skaner Kolor (zmiana)",
]


def _source_row(serial: str = "ABC123") -> list[object]:
    return [
        1,
        "drukarka",
        "10.0.0.1",
        serial,
        "RICOH IM C3000 1.00",
        "biuro",
        "2026.07.10 12:30:45",
        150,
        5,
        100,
        3,
        50,
        2,
        20,
        1,
        10,
        1,
        80,
        2,
        40,
        1,
        7,
        0,
        3,
        0,
    ]


def _title() -> str:
    return (
        "Zestawienie liczników drukarek dla: Klient Testowy "
        "od 2026.07.01 00:00:00 do 2026.07.31 23:59:59 - Klient Testowy"
    )


def test_parse_documaster_csv_maps_counter_fields() -> None:
    with BytesIO() as raw:
        wrapper = __import__("io").TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        writer = csv.writer(wrapper)
        writer.writerow([_title()])
        writer.writerow(HEADERS)
        writer.writerow(_source_row())
        wrapper.flush()
        payload = raw.getvalue()

    parsed = parse_documaster_file("report.csv", payload)

    assert parsed.report_customer == "Klient Testowy"
    assert len(parsed.records) == 1
    record = parsed.records[0]
    assert record.serial == "ABC123"
    assert record.counter_datetime == dt.datetime(2026, 7, 10, 12, 30, 45)
    assert (record.total, record.mono, record.color) == (150, 100, 50)
    assert (record.copier_total, record.printer_total, record.scanner_total) == (30, 120, 10)


def test_parse_documaster_xlsx_accepts_integral_floats() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append([_title()])
    worksheet.append(HEADERS)
    worksheet.append([float(value) if isinstance(value, int) else value for value in _source_row()])
    payload = BytesIO()
    workbook.save(payload)

    parsed = parse_documaster_file("report.xlsx", payload.getvalue())

    assert parsed.records[0].total == 150
    assert parsed.records[0].scanner_total == 10


def test_parse_documaster_rejects_missing_required_header() -> None:
    payload = f"{_title()}\nNumer seryjny,Model\nABC123,RICOH\n".encode()

    with pytest.raises(DocumasterParseError, match="Brak wymaganych kolumn"):
        parse_documaster_file("report.csv", payload)


class _FakeCursor:
    def __init__(self, devices: dict[str, list[DeviceMatch]]) -> None:
        self.devices = devices
        self.current_sql = ""
        self.current_params = ()

    def execute(self, sql: str, params=()) -> None:  # noqa: ANN001
        self.current_sql = " ".join(sql.split())
        self.current_params = params

    def fetchall(self):  # noqa: ANN201
        if "FROM MASZYNA" not in self.current_sql:
            raise AssertionError(self.current_sql)
        return [
            (
                device.id_maszyna,
                device.id_klient,
                device.id_umowacpc,
                device.brand,
                device.model,
            )
            for device in self.devices.get(self.current_params[0], [])
        ]

    def fetchone(self):  # noqa: ANN201
        if "SELECT FIRST 1 COUNTER_DATE" in self.current_sql:
            serial = self.current_params[0]
            if serial == "REGRESSION":
                return (dt.date(2026, 7, 1), 200, 150, 50)
            return None
        raise AssertionError(self.current_sql)


class _FakeConnection:
    def __init__(self, devices: dict[str, list[DeviceMatch]]) -> None:
        self.cursor_obj = _FakeCursor(devices)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class _FakeImporter:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.inserted: list[str] = []

    def _connect(self) -> _FakeConnection:
        return self.connection

    @staticmethod
    def _has_duplicate(cursor, record) -> bool:  # noqa: ANN001
        return record.serial.startswith("DUP")

    def _insert_cmail(self, cursor, record, device, **kwargs) -> int:  # noqa: ANN001, ANN003
        self.inserted.append(record.serial)
        return 9000 + len(self.inserted)


def _record(serial: str, total: int = 100, mono: int = 80, color: int = 20) -> DocumasterRecord:
    return DocumasterRecord(
        row_number=3,
        serial=serial,
        counter_datetime=dt.datetime(2026, 7, 10, 12, 0),
        source_model="source",
        total=total,
        mono=mono,
        color=color,
        copier_total=30,
        copier_mono=20,
        copier_color=10,
        printer_total=70,
        printer_mono=60,
        printer_color=10,
        scanner_total=5,
    )


def _device(serial: str, customer: int) -> DeviceMatch:
    return DeviceMatch(
        id_maszyna=100 + len(serial),
        id_klient=customer,
        id_umowacpc=700,
        brand="RICOH",
        model="IM C3000",
    )


def test_firebird_planner_handles_duplicates_mismatch_and_regression() -> None:
    records = [
        _record("READY"),
        _record("DUP-EXPECTED"),
        _record("EXPECTED-2"),
        _record("REGRESSION", total=190, mono=140, color=50),
        _record("MISMATCH"),
    ]
    devices = {
        record.serial: [_device(record.serial, 674 if record.serial == "MISMATCH" else 26)]
        for record in records
    }
    connection = _FakeConnection(devices)
    importer = _FakeImporter(connection)

    rows = DocumasterFirebirdImporter(importer).process(
        ParsedDocumasterFile("report.xlsx", "Ikano", records),
        execute=False,
        file_hash="a" * 64,
    )

    assert [row.status for row in rows] == [
        "would_insert",
        "duplicate",
        "would_insert",
        "skipped_counter_regression",
        "skipped_customer_mismatch",
    ]
    assert importer.inserted == []
    assert connection.commits == 0
    assert connection.closed is True


def test_firebird_import_commits_once_per_file() -> None:
    records = [_record("READY"), _record("DUP")]
    devices = {record.serial: [_device(record.serial, 26)] for record in records}
    connection = _FakeConnection(devices)
    importer = _FakeImporter(connection)

    rows = DocumasterFirebirdImporter(importer).process(
        ParsedDocumasterFile("report.xlsx", "Ikano", records),
        execute=True,
        file_hash="b" * 64,
    )

    assert [row.status for row in rows] == ["inserted", "duplicate"]
    assert importer.inserted == ["READY"]
    assert connection.commits == 1


def test_documaster_store_records_hash_and_report(tmp_path: Path) -> None:
    store = DocumasterStore(tmp_path / "state.sqlite")
    store.initialize()
    now = dt.datetime(2026, 7, 28, 8, 0)
    run_id = store.start_run(True, now)
    result = DocumasterFileResult(
        source_name="report.csv",
        file_hash="c" * 64,
        file_size=100,
        source_mtime=123.0,
        report_customer="Argenta",
        status="processed",
        archive_path=r"documaster\argenta\report.csv",
        rows=[
            DocumasterRowResult(
                row_number=3,
                serial="ABC123",
                counter_datetime="2026-07-10 12:00:00",
                total=100,
                mono=80,
                color=20,
                status="inserted",
            )
        ],
    )
    report_path = write_documaster_report([result], now, tmp_path / "reports")

    store.record_file(result, report_path, now)
    store.finish_run(
        run_id,
        status="success",
        report_path=report_path,
        message="ok",
        now=now,
    )

    stored = store.get_file("c" * 64)
    assert stored is not None
    assert stored["status"] == "processed"
    assert stored["inserted_count"] == 1
