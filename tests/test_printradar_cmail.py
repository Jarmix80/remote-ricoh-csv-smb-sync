from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

from remote_ricoh.config import WARSAW_TZ
from remote_ricoh.firebird_cmail import DeviceMatch
from remote_ricoh.printradar_cmail import (
    PRINTRADAR_COMMENT_PREFIX,
    PRINTRADAR_MAILFROM,
    LatestCmail,
    PrintRadarCmailStore,
    PrintRadarReading,
    _sync_selected,
    select_daily_readings,
    write_scanner_queue_report,
)


def _reading(
    *,
    sample_id: str = "sample-1",
    serial: str = "ABC123",
    collected_at: dt.datetime | None = None,
    total_bw: int = 100,
    total_color: int | None = 20,
    machine_total: int = 120,
    scan_total: int | None = 30,
) -> PrintRadarReading:
    return PrintRadarReading(
        sample_id=sample_id,
        device_id="device-1",
        serial=serial,
        device_name="Ricoh IM",
        site_id="site-1",
        device_host="10.0.0.2",
        collected_at=collected_at or dt.datetime(2026, 7, 27, 8, tzinfo=dt.UTC),
        canonical_counters={"print": {"total": machine_total}},
        total_bw=total_bw,
        total_color=total_color,
        machine_total=machine_total,
        scan_total=scan_total,
    )


def test_select_daily_readings_uses_latest_valid_sample() -> None:
    older = _reading(
        sample_id="older",
        collected_at=dt.datetime(2026, 7, 27, 7, tzinfo=dt.UTC),
        total_bw=90,
        total_color=20,
        machine_total=110,
    )
    latest = _reading(
        sample_id="latest",
        collected_at=dt.datetime(2026, 7, 27, 9, tzinfo=dt.UTC),
    )

    selected, invalid = select_daily_readings(
        [latest, older],
        now=dt.datetime(2026, 7, 28, 10, tzinfo=WARSAW_TZ),
    )

    assert selected == [latest]
    assert invalid == []


def test_select_daily_readings_blocks_invalid_sum_and_regression() -> None:
    invalid_sum = _reading(sample_id="bad-sum", machine_total=999)
    before = _reading(
        sample_id="before",
        serial="REGRESS",
        collected_at=dt.datetime(2026, 7, 26, 6, tzinfo=dt.UTC),
        total_bw=200,
        total_color=20,
        machine_total=220,
    )
    after = _reading(
        sample_id="after",
        serial="REGRESS",
        collected_at=dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC),
        total_bw=190,
        total_color=20,
        machine_total=210,
    )

    selected, invalid = select_daily_readings(
        [invalid_sum, before, after],
        now=dt.datetime(2026, 7, 28, 10, tzinfo=WARSAW_TZ),
    )

    assert selected == []
    assert {row.status for row in invalid} == {
        "skipped_invalid",
        "blocked_source_regression",
    }


def test_select_daily_readings_blocks_regression_between_days() -> None:
    before = _reading(
        sample_id="day-1",
        collected_at=dt.datetime(2026, 7, 25, 8, tzinfo=dt.UTC),
        total_bw=200,
        total_color=20,
        machine_total=220,
    )
    after = _reading(
        sample_id="day-2",
        collected_at=dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC),
        total_bw=190,
        total_color=20,
        machine_total=210,
    )

    selected, invalid = select_daily_readings(
        [before, after],
        now=dt.datetime(2026, 7, 28, 10, tzinfo=WARSAW_TZ),
    )

    assert selected == []
    assert len(invalid) == 1
    assert invalid[0].status == "blocked_source_regression"
    assert "miedzy dniami" in invalid[0].message


class _FakeCursor:
    def __init__(
        self,
        *,
        latest: LatestCmail | None = None,
        marker_exists: bool = False,
        devices: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.latest = latest
        self.marker_exists = marker_exists
        self.devices = devices or [(11, 22, 33, "RICOH", "IM C3000", "ABC123", "")]
        self._one: tuple[Any, ...] | None = None
        self._many: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        normalized = " ".join(sql.split()).upper()
        if "FROM CMAIL" in normalized and "SCANNER_TOTAL" in normalized:
            latest = self.latest
            self._many = (
                []
                if latest is None
                else [
                    (
                        "ABC123",
                        latest.cmail_id,
                        latest.counter_date,
                        latest.total,
                        latest.mono,
                        latest.color,
                        latest.scanner_total,
                    )
                ]
            )
        elif "FROM CMAIL WHERE COMMENTS STARTING WITH" in normalized:
            self._many = [("printradar:sample-1",)] if self.marker_exists else []
        elif "FROM MASZYNA" in normalized:
            self._many = self.devices
        else:
            raise AssertionError(f"Nieobslugiwane SQL testowe: {normalized}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._many


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_obj = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _FakeImporter:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.inserted: list[dict[str, Any]] = []

    def _connect(self) -> _FakeConnection:
        return self.connection

    def _insert_cmail(
        self,
        cursor: _FakeCursor,
        record: Any,
        device: DeviceMatch,
        **kwargs: Any,
    ) -> int:
        self.inserted.append(
            {
                "record": record,
                "device": device,
                **kwargs,
            }
        )
        return 501


def test_sync_execute_inserts_print_only_with_printradar_marker(tmp_path: Path) -> None:
    reading = _reading()
    connection = _FakeConnection(_FakeCursor())
    importer = _FakeImporter(connection)
    store = PrintRadarCmailStore(tmp_path / "state.sqlite")
    store.initialize()

    rows = _sync_selected([reading], importer=importer, store=store, execute=True)

    assert rows[0].status == "inserted"
    assert rows[0].cmail_id == "501"
    assert connection.committed is True
    assert importer.inserted[0]["mailfrom"] == PRINTRADAR_MAILFROM
    assert importer.inserted[0]["comments"] == PRINTRADAR_COMMENT_PREFIX + reading.sample_id
    assert importer.inserted[0]["record"].scan_total is None
    scanner = store.scanner_rows()[0]
    assert scanner["serial"] == reading.serial
    assert scanner["scan_total"] == 30


def test_sync_refreshes_scanner_queue_outside_daily_cursor(tmp_path: Path) -> None:
    daily_reading = _reading(sample_id="daily", scan_total=2)
    latest_scanner = _reading(
        sample_id="latest-scanner",
        collected_at=dt.datetime(2026, 7, 27, 12, tzinfo=dt.UTC),
        scan_total=205_752,
    )
    connection = _FakeConnection(_FakeCursor())
    importer = _FakeImporter(connection)
    store = PrintRadarCmailStore(tmp_path / "state.sqlite")
    store.initialize()

    _sync_selected(
        [daily_reading],
        importer=importer,
        store=store,
        execute=False,
        scanner_readings=[latest_scanner],
    )

    scanner = store.scanner_rows()[0]
    assert scanner["sample_id"] == "latest-scanner"
    assert scanner["scan_total"] == 205_752


def test_sync_dry_run_blocks_target_ahead_and_preserves_zero_in_report(
    tmp_path: Path,
) -> None:
    latest = LatestCmail(
        cmail_id=7,
        counter_date=dt.date(2026, 7, 26),
        total=150,
        mono=150,
        color=0,
        scanner_total=40,
    )
    connection = _FakeConnection(_FakeCursor(latest=latest))
    importer = _FakeImporter(connection)
    store = PrintRadarCmailStore(tmp_path / "state.sqlite")
    store.initialize()

    rows = _sync_selected([_reading()], importer=importer, store=store, execute=False)

    assert rows[0].status == "blocked_target_counter_ahead"
    assert rows[0].latest_cmail_color == "0"
    assert importer.inserted == []
    assert connection.rolled_back is True


def test_scanner_queue_report_is_persistent_csv(tmp_path: Path) -> None:
    store = PrintRadarCmailStore(tmp_path / "state.sqlite")
    store.initialize()
    store.upsert_scanner(_reading(), cmail_scanner_total=25)

    report = write_scanner_queue_report(store, report_dir=tmp_path / "reports")

    with report.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["serial"] == "ABC123"
    assert rows[0]["scan_total"] == "30"
    assert rows[0]["cmail_scanner_total"] == "25"


def test_store_marks_abandoned_run_as_interrupted(tmp_path: Path) -> None:
    store = PrintRadarCmailStore(tmp_path / "state.sqlite")
    store.initialize()
    run_id = store.start_run(
        execute=False,
        backfill=True,
        cursor_before="1970-01-01T00:00:00+00:00",
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET started_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (run_id,),
        )

    store.initialize()

    with store._connect() as connection:
        status = connection.execute(
            "SELECT status FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0]
    assert status == "interrupted"
