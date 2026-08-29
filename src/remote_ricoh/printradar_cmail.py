"""Bezpieczna synchronizacja dziennych licznikow PrintRadar do Firebird CMAIL."""

from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
import socket
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PRINTRADAR_ALLOWED_VIEW, WARSAW_TZ, PrintRadarSettings
from .firebird_cmail import CounterRecord, DeviceMatch, FirebirdCmailImporter

DEFAULT_PRINTRADAR_CMAIL_DB = Path("local/printradar_cmail/sync.sqlite")
DEFAULT_PRINTRADAR_REPORT_DIR = Path("local/printradar_cmail/reports")
PRINTRADAR_MAILFROM = "[Import] - PrintRadar"
PRINTRADAR_COMMENT_PREFIX = "printradar:"
MIN_CURSOR = "1970-01-01T00:00:00+00:00"
MAX_FIREBIRD_INTEGER = 2_147_483_647


@dataclass(frozen=True, slots=True)
class PrintRadarReading:
    """Odczyt udostepniony przez widok integracyjny PrintRadar."""

    sample_id: str
    device_id: str
    serial: str
    device_name: str
    site_id: str
    device_host: str
    collected_at: dt.datetime
    canonical_counters: dict[str, Any]
    total_bw: int | None
    total_color: int | None
    machine_total: int | None
    scan_total: int | None

    @property
    def local_day(self) -> dt.date:
        return self.collected_at.astimezone(WARSAW_TZ).date()


@dataclass(frozen=True, slots=True)
class LatestCmail:
    """Najnowszy zapis docelowy dla numeru seryjnego."""

    cmail_id: int
    counter_date: dt.date | None
    total: int | None
    mono: int | None
    color: int | None
    scanner_total: int | None


@dataclass(frozen=True, slots=True)
class PrintRadarSyncRow:
    """Wynik przetworzenia jednego dziennego odczytu."""

    serial: str
    sample_id: str
    collected_at: str
    counter_day: str
    machine_total: int | None
    total_bw: int | None
    total_color: int | None
    scan_total: int | None
    status: str
    message: str = ""
    cmail_id: str = ""
    latest_cmail_date: str = ""
    latest_cmail_total: str = ""
    latest_cmail_mono: str = ""
    latest_cmail_color: str = ""

    def as_csv_row(self) -> dict[str, object]:
        return {
            "serial": self.serial,
            "sample_id": self.sample_id,
            "collected_at": self.collected_at,
            "counter_day": self.counter_day,
            "machine_total": self.machine_total,
            "total_bw": self.total_bw,
            "total_color": self.total_color,
            "scan_total_not_imported": self.scan_total,
            "status": self.status,
            "cmail_id": self.cmail_id,
            "latest_cmail_date": self.latest_cmail_date,
            "latest_cmail_total": self.latest_cmail_total,
            "latest_cmail_mono": self.latest_cmail_mono,
            "latest_cmail_color": self.latest_cmail_color,
            "message": self.message,
        }


@dataclass(slots=True)
class PrintRadarSyncResult:
    """Podsumowanie jednego przebiegu synchronizacji."""

    run_id: int
    execute: bool
    backfill: bool
    fetched: int
    selected: int
    rows: list[PrintRadarSyncRow]
    report_path: Path
    cursor_before: str
    cursor_after: str

    @property
    def status_counts(self) -> Counter[str]:
        return Counter(row.status for row in self.rows)

    @property
    def has_warning(self) -> bool:
        return any(
            status.startswith(("blocked_", "skipped_invalid", "failed_"))
            for status in self.status_counts
        )

    def as_log_message(self) -> str:
        statuses = ", ".join(f"{key}={value}" for key, value in sorted(self.status_counts.items()))
        return (
            f"PrintRadar CMAIL: execute={self.execute}, backfill={self.backfill}, "
            f"fetched={self.fetched}, selected={self.selected}, "
            f"statuses=({statuses or 'brak'}), cursor={self.cursor_before}->{self.cursor_after}."
        )


@dataclass(slots=True)
class PrintRadarCmailStore:
    """Lokalny stan kursora, uruchomien i kolejki skanerow."""

    db_path: Path = DEFAULT_PRINTRADAR_CMAIL_DB

    def initialize(self) -> None:
        self.db_path = self.db_path.expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    execute_mode INTEGER NOT NULL,
                    backfill_mode INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    cursor_before TEXT NOT NULL,
                    cursor_after TEXT NOT NULL DEFAULT '',
                    fetched_count INTEGER NOT NULL DEFAULT 0,
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    report_path TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS row_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    serial TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    counter_day TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    cmail_id TEXT NOT NULL DEFAULT '',
                    UNIQUE(run_id, serial, sample_id)
                );
                CREATE TABLE IF NOT EXISTS scanner_queue (
                    serial TEXT PRIMARY KEY,
                    sample_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    site_id TEXT NOT NULL,
                    device_host TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    scan_total INTEGER,
                    canonical_counters_json TEXT NOT NULL,
                    cmail_scanner_total INTEGER,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = 'interrupted',
                    message = CASE
                        WHEN message = '' THEN 'Poprzedni proces nie zakonczyl przebiegu.'
                        ELSE message
                    END
                WHERE status = 'running'
                  AND started_at < ?
                """,
                (_utc_now(), _utc_before(hours=6)),
            )

    def get_cursor(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'last_collected_at'"
            ).fetchone()
        return str(row[0]) if row else None

    def start_run(self, *, execute: bool, backfill: bool, cursor_before: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (
                    started_at, execute_mode, backfill_mode, status, cursor_before
                ) VALUES (?, ?, ?, 'running', ?)
                """,
                (_utc_now(), int(execute), int(backfill), cursor_before),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        cursor_after: str,
        fetched: int,
        selected: int,
        report_path: Path,
        rows: Sequence[PrintRadarSyncRow],
        advance_cursor: bool,
        message: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO row_results (
                    run_id, serial, sample_id, collected_at, counter_day,
                    status, message, cmail_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        row.serial,
                        row.sample_id,
                        row.collected_at,
                        row.counter_day,
                        row.status,
                        row.message,
                        row.cmail_id,
                    )
                    for row in rows
                ],
            )
            connection.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = ?, cursor_after = ?, fetched_count = ?,
                    selected_count = ?, report_path = ?, message = ?
                WHERE id = ?
                """,
                (
                    _utc_now(),
                    status,
                    cursor_after,
                    fetched,
                    selected,
                    str(report_path),
                    message,
                    run_id,
                ),
            )
            if advance_cursor:
                connection.execute(
                    """
                    INSERT INTO settings (key, value) VALUES ('last_collected_at', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (cursor_after,),
                )

    def fail_run(self, run_id: int, error: Exception) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ?, status = 'failed', message = ? WHERE id = ?",
                (_utc_now(), f"{type(error).__name__}: {error}", run_id),
            )

    def upsert_scanner(
        self,
        reading: PrintRadarReading,
        *,
        cmail_scanner_total: int | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scanner_queue (
                    serial, sample_id, device_name, site_id, device_host, collected_at,
                    scan_total, canonical_counters_json, cmail_scanner_total, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_mapping', ?)
                ON CONFLICT(serial) DO UPDATE SET
                    sample_id = excluded.sample_id,
                    device_name = excluded.device_name,
                    site_id = excluded.site_id,
                    device_host = excluded.device_host,
                    collected_at = excluded.collected_at,
                    scan_total = excluded.scan_total,
                    canonical_counters_json = excluded.canonical_counters_json,
                    cmail_scanner_total = excluded.cmail_scanner_total,
                    status = 'pending_mapping',
                    updated_at = excluded.updated_at
                """,
                (
                    reading.serial,
                    reading.sample_id,
                    reading.device_name,
                    reading.site_id,
                    reading.device_host,
                    reading.collected_at.isoformat(),
                    reading.scan_total,
                    json.dumps(reading.canonical_counters, ensure_ascii=False, sort_keys=True),
                    cmail_scanner_total,
                    _utc_now(),
                ),
            )

    def scanner_rows(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM scanner_queue ORDER BY serial").fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class SshTunnel(AbstractContextManager["SshTunnel"]):
    """Krotkotrwaly lokalny tunel do PostgreSQL PrintRadar."""

    def __init__(self, settings: PrintRadarSettings, *, timeout: float = 15.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> SshTunnel:
        identity = self.settings.ssh_identity_file
        if not identity.is_file():
            raise FileNotFoundError(f"Brak klucza SSH PrintRadar: {identity}")
        if _port_is_open(self.settings.tunnel_local_host, self.settings.tunnel_local_port):
            raise RuntimeError(
                "Lokalny port tunelu PrintRadar jest juz zajety; "
                "automat nie polaczy sie przez niezidentyfikowany tunel."
            )
        forward = (
            f"{self.settings.tunnel_local_host}:{self.settings.tunnel_local_port}:"
            f"{self.settings.ssh_remote_db_host}:{self.settings.ssh_remote_db_port}"
        )
        self.process = subprocess.Popen(
            [
                "ssh",
                "-i",
                str(identity),
                "-p",
                str(self.settings.ssh_port),
                "-N",
                "-L",
                forward,
                "-o",
                "BatchMode=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ServerAliveCountMax=3",
                f"{self.settings.ssh_user}@{self.settings.ssh_host}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                error = (self.process.stderr.read() if self.process.stderr else "").strip()
                raise RuntimeError(f"Nie mozna uruchomic tunelu PrintRadar: {error[:300]}")
            if _port_is_open(
                self.settings.tunnel_local_host,
                self.settings.tunnel_local_port,
            ):
                return self
            time.sleep(0.2)
        self.close()
        raise TimeoutError("Tunel PrintRadar nie otworzyl lokalnego portu w wymaganym czasie.")

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


class PrintRadarSource:
    """Klient ograniczony do zatwierdzonego widoku integracyjnego."""

    def __init__(self, settings: PrintRadarSettings) -> None:
        if settings.db_view != PRINTRADAR_ALLOWED_VIEW:
            raise ValueError("Niedozwolony widok PrintRadar.")
        self.settings = settings

    def fetch(
        self,
        *,
        collected_after: str,
        collected_before: str,
    ) -> list[PrintRadarReading]:
        psycopg = importlib.import_module("psycopg")
        connection = psycopg.connect(
            host=self.settings.db_host,
            port=self.settings.db_port,
            dbname=self.settings.db_name,
            user=self.settings.db_user,
            password=self.settings.db_password,
            options="-c default_transaction_read_only=on",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT device_id, serial_number, device_name, site_id, device_host,
                           collected_at, canonical_counters, total_bw, total_color,
                           machine_total, scan_total, sample_id
                    FROM {PRINTRADAR_ALLOWED_VIEW}
                    WHERE collected_at > %s AND collected_at < %s
                    ORDER BY collected_at ASC, sample_id ASC
                    """,
                    (collected_after, collected_before),
                )
                columns = [item.name for item in cursor.description]
                rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            connection.rollback()
        finally:
            connection.close()
        return [_parse_source_row(row) for row in rows]


def run_printradar_cmail_sync(
    *,
    settings: PrintRadarSettings,
    importer: FirebirdCmailImporter,
    store: PrintRadarCmailStore,
    execute: bool,
    backfill: bool,
    serial_filter: set[str] | None = None,
    report_dir: Path = DEFAULT_PRINTRADAR_REPORT_DIR,
    now: dt.datetime | None = None,
) -> PrintRadarSyncResult:
    """Pobiera zakonczone dni, waliduje i opcjonalnie zapisuje je do CMAIL."""
    actual_now = (now or dt.datetime.now(tz=WARSAW_TZ)).astimezone(WARSAW_TZ)
    completed_before = dt.datetime.combine(
        actual_now.date(),
        dt.time.min,
        tzinfo=WARSAW_TZ,
    ).astimezone(dt.UTC)
    completed_before_text = completed_before.isoformat()
    store.initialize()
    stored_cursor = store.get_cursor()
    cursor_before = MIN_CURSOR if backfill else (stored_cursor or completed_before_text)
    run_id = store.start_run(
        execute=execute,
        backfill=backfill,
        cursor_before=cursor_before,
    )

    try:
        with SshTunnel(settings):
            readings = PrintRadarSource(settings).fetch(
                collected_after=cursor_before,
                collected_before=completed_before_text,
            )
        if serial_filter:
            allowed = {_normalize_serial(value) for value in serial_filter}
            readings = [reading for reading in readings if reading.serial in allowed]
        selected, invalid_rows = select_daily_readings(readings, now=actual_now)
        sync_rows = _sync_selected(
            selected,
            importer=importer,
            store=store,
            execute=execute,
        )
        rows = [*invalid_rows, *sync_rows]
        cursor_after = max(
            (reading.collected_at.isoformat() for reading in readings),
            default=cursor_before,
        )
        report_path = write_sync_report(
            run_id,
            execute=execute,
            rows=rows,
            report_dir=report_dir,
            now=actual_now,
        )
        store.finish_run(
            run_id,
            status="completed_warning" if _rows_have_warning(rows) else "completed",
            cursor_after=cursor_after,
            fetched=len(readings),
            selected=len(selected),
            report_path=report_path,
            rows=rows,
            advance_cursor=execute and serial_filter is None,
        )
        return PrintRadarSyncResult(
            run_id=run_id,
            execute=execute,
            backfill=backfill,
            fetched=len(readings),
            selected=len(selected),
            rows=rows,
            report_path=report_path,
            cursor_before=cursor_before,
            cursor_after=cursor_after,
        )
    except Exception as exc:
        store.fail_run(run_id, exc)
        raise


def select_daily_readings(
    readings: Iterable[PrintRadarReading],
    *,
    now: dt.datetime | None = None,
) -> tuple[list[PrintRadarReading], list[PrintRadarSyncRow]]:
    """Wybiera najnowszy poprawny odczyt per serial i zakonczony dzien."""
    actual_now = (now or dt.datetime.now(tz=WARSAW_TZ)).astimezone(WARSAW_TZ)
    groups: dict[tuple[str, dt.date], list[PrintRadarReading]] = defaultdict(list)
    invalid: list[PrintRadarSyncRow] = []
    for reading in readings:
        error = _validate_reading(reading, actual_now=actual_now)
        if error:
            invalid.append(_result_row(reading, "skipped_invalid", error))
            continue
        groups[(reading.serial, reading.local_day)].append(reading)

    selected: list[PrintRadarReading] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda row: (row.collected_at, row.sample_id))
        regression = _find_print_regression(ordered)
        if regression:
            invalid.append(_result_row(ordered[-1], "blocked_source_regression", regression))
            continue
        selected.append(ordered[-1])

    selected_by_serial: dict[str, list[PrintRadarReading]] = defaultdict(list)
    for reading in selected:
        selected_by_serial[reading.serial].append(reading)
    blocked_serials: set[str] = set()
    for serial, serial_readings in selected_by_serial.items():
        ordered = sorted(serial_readings, key=lambda row: (row.collected_at, row.sample_id))
        regression = _find_print_regression(ordered)
        if regression:
            blocked_serials.add(serial)
            invalid.append(
                _result_row(
                    ordered[-1],
                    "blocked_source_regression",
                    f"Regresja miedzy dniami: {regression}",
                )
            )
    if blocked_serials:
        selected = [reading for reading in selected if reading.serial not in blocked_serials]

    selected.sort(key=lambda row: (row.collected_at, row.sample_id))
    return selected, invalid


def _sync_selected(
    readings: Sequence[PrintRadarReading],
    *,
    importer: FirebirdCmailImporter,
    store: PrintRadarCmailStore,
    execute: bool,
) -> list[PrintRadarSyncRow]:
    connection = importer._connect()
    rows: list[PrintRadarSyncRow] = []
    try:
        cursor = connection.cursor()
        serials = sorted({reading.serial for reading in readings})
        latest_by_serial = _fetch_latest_cmail_batch(cursor, serials)
        devices_by_serial = _fetch_unique_devices_batch(cursor, serials)
        existing_markers = _fetch_printradar_markers(cursor)

        scanner_latest: dict[str, PrintRadarReading] = {}
        for reading in readings:
            if reading.scan_total is None:
                continue
            current = scanner_latest.get(reading.serial)
            if current is None or (reading.collected_at, reading.sample_id) > (
                current.collected_at,
                current.sample_id,
            ):
                scanner_latest[reading.serial] = reading
        for reading in scanner_latest.values():
            latest = latest_by_serial.get(reading.serial)
            store.upsert_scanner(
                reading,
                cmail_scanner_total=latest.scanner_total if latest else None,
            )

        for reading in readings:
            latest = latest_by_serial.get(reading.serial)
            device = devices_by_serial.get(reading.serial, "blocked_device_not_found")
            if isinstance(device, str):
                rows.append(
                    _with_latest(_result_row(reading, device, _status_message(device)), latest)
                )
                continue
            marker = PRINTRADAR_COMMENT_PREFIX + reading.sample_id
            result = _evaluate_target(
                reading,
                latest,
                marker_exists=marker in existing_markers,
            )
            if result is not None:
                rows.append(result)
                continue
            record = CounterRecord(
                serial=reading.serial,
                counter_date=reading.collected_at,
                brand="",
                model="",
                total=reading.machine_total,
                mono=reading.total_bw,
                color=reading.total_color,
                copier_total=None,
                copier_mono=None,
                copier_color=None,
                printer_total=None,
                printer_mono=None,
                printer_color=None,
                scan_total=None,
            )
            if execute:
                cmail_id = importer._insert_cmail(
                    cursor,
                    record,
                    device,
                    mailfrom=PRINTRADAR_MAILFROM,
                    comments=marker,
                )
                rows.append(
                    _with_latest(
                        _result_row(reading, "inserted", cmail_id=str(cmail_id)),
                        latest,
                    )
                )
                latest_by_serial[reading.serial] = LatestCmail(
                    cmail_id=cmail_id,
                    counter_date=reading.local_day,
                    total=reading.machine_total,
                    mono=reading.total_bw,
                    color=reading.total_color,
                    scanner_total=latest.scanner_total if latest else None,
                )
                existing_markers.add(marker)
            else:
                rows.append(_with_latest(_result_row(reading, "would_insert"), latest))
        if execute:
            connection.commit()
        else:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return rows


def _evaluate_target(
    reading: PrintRadarReading,
    latest: LatestCmail | None,
    *,
    marker_exists: bool,
) -> PrintRadarSyncRow | None:
    if marker_exists:
        return _with_latest(_result_row(reading, "duplicate_marker"), latest)
    if latest is None:
        return None
    if latest.counter_date and latest.counter_date > dt.datetime.now(tz=WARSAW_TZ).date():
        return _with_latest(
            _result_row(reading, "blocked_target_future_date", "CMAIL ma date przyszla."),
            latest,
        )
    if latest.counter_date and latest.counter_date > reading.local_day:
        return _with_latest(
            _result_row(reading, "blocked_source_older_date", "CMAIL ma nowsza date licznika."),
            latest,
        )
    source_values = (reading.machine_total, reading.total_bw, reading.total_color)
    target_values = (_normalized_total(latest), latest.mono, latest.color)
    if _counter_tuple_equal(source_values, target_values):
        return _with_latest(_result_row(reading, "duplicate_counters"), latest)
    for name, source, target in zip(
        ("TOTAL", "TOTAL_MONO", "TOTAL_COLOR"),
        source_values,
        target_values,
        strict=True,
    ):
        if source is not None and target is not None and source < target:
            return _with_latest(
                _result_row(
                    reading,
                    "blocked_target_counter_ahead",
                    f"{name}: PrintRadar={source}, CMAIL={target}.",
                ),
                latest,
            )
    return None


def _fetch_unique_devices_batch(
    cursor: Any,
    serials: Sequence[str],
) -> dict[str, DeviceMatch | str]:
    if not serials:
        return {}
    placeholders = ", ".join("?" for _ in serials)
    cursor.execute(
        f"""
        SELECT ID_MASZYNA, ID_KLIENT, ID_UMOWACPC,
               TRIM(COALESCE(MARKA, '')), TRIM(COALESCE(MODEL, '')),
               UPPER(TRIM(COALESCE(SERIAL, ''))),
               UPPER(TRIM(COALESCE(SERIAL2, '')))
        FROM MASZYNA
        WHERE UPPER(TRIM(COALESCE(SERIAL, ''))) IN ({placeholders})
           OR UPPER(TRIM(COALESCE(SERIAL2, ''))) IN ({placeholders})
        """,
        (*serials, *serials),
    )
    rows = cursor.fetchall()
    requested = set(serials)
    matches: dict[str, list[DeviceMatch]] = defaultdict(list)
    for row in rows:
        device = DeviceMatch(
            id_maszyna=int(row[0]),
            id_klient=int(row[1]),
            id_umowacpc=int(row[2]) if row[2] is not None else None,
            brand=str(row[3] or ""),
            model=str(row[4] or ""),
        )
        for matched_serial in {str(row[5] or ""), str(row[6] or "")} & requested:
            matches[matched_serial].append(device)

    result: dict[str, DeviceMatch | str] = {}
    for serial in serials:
        devices = matches.get(serial, [])
        if len(devices) == 1:
            result[serial] = devices[0]
        elif len(devices) > 1:
            result[serial] = "blocked_device_ambiguous"
    return result


def _fetch_latest_cmail_batch(
    cursor: Any,
    serials: Sequence[str],
) -> dict[str, LatestCmail]:
    if not serials:
        return {}
    placeholders = ", ".join("?" for _ in serials)
    cursor.execute(
        f"""
        SELECT UPPER(TRIM(COALESCE(SERIAL, ''))), ID_CMAIL, COUNTER_DATE,
               TOTAL, TOTAL_MONO, TOTAL_COLOR, SCANNER_TOTAL
        FROM CMAIL
        WHERE UPPER(TRIM(COALESCE(SERIAL, ''))) IN ({placeholders})
        """,
        tuple(serials),
    )
    result: dict[str, LatestCmail] = {}
    for row in cursor.fetchall():
        serial = str(row[0] or "")
        counter_date = row[2]
        if isinstance(counter_date, dt.datetime):
            counter_date = counter_date.date()
        candidate = LatestCmail(
            cmail_id=int(row[1]),
            counter_date=counter_date,
            total=row[3],
            mono=row[4],
            color=row[5],
            scanner_total=row[6],
        )
        current = result.get(serial)
        candidate_key = (candidate.counter_date or dt.date.min, candidate.cmail_id)
        current_key = (
            (current.counter_date or dt.date.min, current.cmail_id)
            if current
            else (dt.date.min, -1)
        )
        if candidate_key > current_key:
            result[serial] = candidate
    return result


def _fetch_printradar_markers(cursor: Any) -> set[str]:
    cursor.execute(
        "SELECT COMMENTS FROM CMAIL WHERE COMMENTS STARTING WITH ?",
        (PRINTRADAR_COMMENT_PREFIX,),
    )
    return {str(row[0]) for row in cursor.fetchall() if row[0]}


def write_sync_report(
    run_id: int,
    *,
    execute: bool,
    rows: Sequence[PrintRadarSyncRow],
    report_dir: Path = DEFAULT_PRINTRADAR_REPORT_DIR,
    now: dt.datetime | None = None,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now(tz=WARSAW_TZ)).strftime("%Y%m%d_%H%M%S")
    path = (
        report_dir / f"printradar_cmail_{stamp}_{run_id}_{'execute' if execute else 'dry_run'}.csv"
    )
    fields = list(PrintRadarSyncRow("", "", "", "", None, None, None, None, "").as_csv_row())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())
    return path


def write_scanner_queue_report(
    store: PrintRadarCmailStore,
    *,
    report_dir: Path = DEFAULT_PRINTRADAR_REPORT_DIR,
    now: dt.datetime | None = None,
) -> Path:
    store.initialize()
    rows = store.scanner_rows()
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now(tz=WARSAW_TZ)).strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"printradar_scanner_queue_{stamp}.csv"
    fields = [
        "serial",
        "sample_id",
        "device_name",
        "site_id",
        "device_host",
        "collected_at",
        "scan_total",
        "cmail_scanner_total",
        "status",
        "canonical_counters_json",
        "updated_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def load_serial_filter(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    values: set[str] = set()
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            first = line.strip().split(",", 1)[0].split(";", 1)[0].strip()
            if first and first.casefold() not in {"serial", "serial_number", "sn"}:
                values.add(_normalize_serial(first))
    return values


def _parse_source_row(row: dict[str, Any]) -> PrintRadarReading:
    canonical = row.get("canonical_counters")
    if isinstance(canonical, str):
        try:
            canonical = json.loads(canonical)
        except json.JSONDecodeError:
            canonical = {}
    collected_at = dt.datetime.fromisoformat(
        str(row.get("collected_at") or "").replace("Z", "+00:00")
    )
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=dt.UTC)
    return PrintRadarReading(
        sample_id=str(row.get("sample_id") or "").strip(),
        device_id=str(row.get("device_id") or "").strip(),
        serial=_normalize_serial(row.get("serial_number")),
        device_name=str(row.get("device_name") or "").strip(),
        site_id=str(row.get("site_id") or "").strip(),
        device_host=str(row.get("device_host") or "").strip(),
        collected_at=collected_at,
        canonical_counters=canonical if isinstance(canonical, dict) else {},
        total_bw=_optional_int(row.get("total_bw")),
        total_color=_optional_int(row.get("total_color")),
        machine_total=_optional_int(row.get("machine_total")),
        scan_total=_optional_int(row.get("scan_total")),
    )


def _validate_reading(reading: PrintRadarReading, *, actual_now: dt.datetime) -> str:
    if not reading.serial:
        return "Brak numeru seryjnego."
    if not reading.sample_id:
        return "Brak sample_id."
    if reading.local_day >= actual_now.date():
        return "Dzien odczytu nie jest jeszcze zakonczony."
    values = (reading.machine_total, reading.total_bw, reading.total_color, reading.scan_total)
    if reading.machine_total is None or reading.total_bw is None:
        return "Brak machine_total albo total_bw."
    if any(value is not None and not 0 <= value <= MAX_FIREBIRD_INTEGER for value in values):
        return "Licznik jest poza zakresem Firebird INTEGER."
    expected = reading.total_bw + (reading.total_color or 0)
    if reading.machine_total != expected:
        return (
            f"Niespojna suma: machine_total={reading.machine_total}, "
            f"total_bw+total_color={expected}."
        )
    if reading.collected_at > actual_now.astimezone(dt.UTC) + dt.timedelta(minutes=5):
        return "Data odczytu jest przyszla."
    return ""


def _find_print_regression(readings: Sequence[PrintRadarReading]) -> str:
    previous: tuple[int | None, int | None, int | None] | None = None
    for reading in readings:
        current = (reading.machine_total, reading.total_bw, reading.total_color)
        if previous is not None:
            for name, before, after in zip(
                ("machine_total", "total_bw", "total_color"),
                previous,
                current,
                strict=True,
            ):
                if before is not None and after is not None and after < before:
                    return f"Spadek {name}: {before}->{after}."
        previous = current
    return ""


def _normalized_total(latest: LatestCmail) -> int | None:
    if latest.total is not None:
        return latest.total
    if latest.mono is None and latest.color is None:
        return None
    return (latest.mono or 0) + (latest.color or 0)


def _counter_tuple_equal(
    source: tuple[int | None, int | None, int | None],
    target: tuple[int | None, int | None, int | None],
) -> bool:
    return all(
        left == right or {left, right} <= {None, 0}
        for left, right in zip(source, target, strict=True)
    )


def _result_row(
    reading: PrintRadarReading,
    status: str,
    message: str = "",
    *,
    cmail_id: str = "",
) -> PrintRadarSyncRow:
    return PrintRadarSyncRow(
        serial=reading.serial,
        sample_id=reading.sample_id,
        collected_at=reading.collected_at.isoformat(),
        counter_day=reading.local_day.isoformat(),
        machine_total=reading.machine_total,
        total_bw=reading.total_bw,
        total_color=reading.total_color,
        scan_total=reading.scan_total,
        status=status,
        message=message,
        cmail_id=cmail_id,
    )


def _with_latest(row: PrintRadarSyncRow, latest: LatestCmail | None) -> PrintRadarSyncRow:
    if latest is None:
        return row
    return PrintRadarSyncRow(
        serial=row.serial,
        sample_id=row.sample_id,
        collected_at=row.collected_at,
        counter_day=row.counter_day,
        machine_total=row.machine_total,
        total_bw=row.total_bw,
        total_color=row.total_color,
        scan_total=row.scan_total,
        status=row.status,
        message=row.message,
        cmail_id=row.cmail_id,
        latest_cmail_date=latest.counter_date.isoformat() if latest.counter_date else "",
        latest_cmail_total=_optional_text(_normalized_total(latest)),
        latest_cmail_mono=_optional_text(latest.mono),
        latest_cmail_color=_optional_text(latest.color),
    )


def _status_message(status: str) -> str:
    return {
        "blocked_device_not_found": "Brak urzadzenia w MASZYNA.",
        "blocked_device_ambiguous": "Numer seryjny pasuje do wielu rekordow MASZYNA.",
    }.get(status, "")


def _rows_have_warning(rows: Sequence[PrintRadarSyncRow]) -> bool:
    return any(row.status.startswith(("blocked_", "skipped_invalid", "failed_")) for row in rows)


def _normalize_serial(value: Any) -> str:
    return str(value or "").strip().upper()


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_text(value: Any) -> str:
    return "" if value is None else str(value)


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _utc_now() -> str:
    return dt.datetime.now(tz=dt.UTC).isoformat()


def _utc_before(*, hours: int) -> str:
    return (dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=hours)).isoformat()
