from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from remote_ricoh.device_delete import DeviceDeleteReportRow
from remote_ricoh.remote_auto import (
    ORDER_STATUS_DELETE_PENDING,
    ORDER_STATUS_READY_DELETE,
    ORDER_STATUS_REMOTE_NOT_FOUND,
    ORDER_STATUS_WAITING_RECENT,
    RemoteAutoStore,
    assert_remote_auto_execute_allowed,
    decide_remote_auto_status,
    read_allowed_report,
    render_remote_auto_dashboard,
)
from remote_ricoh.service_orders import ServiceOrderRow


def _order(table_id: int = 10, serial: str = "ABC123") -> ServiceOrderRow:
    return ServiceOrderRow(
        filter_key="remote_auto",
        id_zlecenie_table=table_id,
        id_zlecenie=14331,
        rok=2026,
        stan="O",
        serial=serial,
        problem="odpiąć REMOTE",
    )


def test_decide_remote_auto_statuses() -> None:
    now = datetime(2026, 7, 2, 10, 0)

    assert (
        decide_remote_auto_status(
            DeviceDeleteReportRow("ABC", "not_found", 0),
            now=now,
        ).order_status
        == ORDER_STATUS_REMOTE_NOT_FOUND
    )
    assert (
        decide_remote_auto_status(
            DeviceDeleteReportRow("ABC", "would_delete_recent_override", 1),
            now=now,
        ).order_status
        == ORDER_STATUS_READY_DELETE
    )
    assert (
        decide_remote_auto_status(
            DeviceDeleteReportRow("ABC", "delete_pending", 1),
            now=now,
        ).order_status
        == ORDER_STATUS_DELETE_PENDING
    )
    waiting = decide_remote_auto_status(
        DeviceDeleteReportRow("ABC", "skipped_recent_report", 1),
        now=now,
    )
    assert waiting.order_status == ORDER_STATUS_WAITING_RECENT
    assert waiting.next_check_at == datetime(2026, 7, 9, 10, 0)


def test_remote_auto_store_queue_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "remote_auto.sqlite"
    store = RemoteAutoStore(db_path)
    store.initialize()
    now = datetime(2026, 7, 2, 10, 0)
    row = _order()

    store.upsert_order(row, now)
    previous = store.set_order_status(
        row,
        status=ORDER_STATUS_WAITING_RECENT,
        last_report_time="2026/07/01 08:00",
        reason="swiezy odczyt",
        next_check_at=datetime(2026, 7, 9, 10, 0),
        now=now,
    )
    store.record_event(
        row,
        event_type="waiting_recent",
        status_from=previous,
        status_to=ORDER_STATUS_WAITING_RECENT,
        message="swiezy odczyt",
        now=now,
    )

    assert previous == "new"
    assert store.should_skip_daily(row, datetime(2026, 7, 3, 10, 0)) is True
    assert store.due_order_ids(datetime(2026, 7, 10, 10, 0)) == [10]
    dashboard = store.dashboard()
    assert dashboard["counts"] == {ORDER_STATUS_WAITING_RECENT: 1}
    assert len(dashboard["events"]) == 1


def test_render_dashboard_contains_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "remote_auto.sqlite"
    store = RemoteAutoStore(db_path)
    store.initialize()
    now = datetime(2026, 7, 2, 10, 0)
    row = _order()
    store.upsert_order(row, now)
    store.set_order_status(row, status=ORDER_STATUS_READY_DELETE, reason="stary odczyt", now=now)

    html = render_remote_auto_dashboard(db_path)

    assert "ABC123" in html
    assert ORDER_STATUS_READY_DELETE in html


def test_read_allowed_report_blocks_path_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "local" / "remote_auto" / "reports" / "x.csv"
    report.parent.mkdir(parents=True)
    report.write_text("ok", encoding="utf-8")

    content_type, payload = read_allowed_report("local/remote_auto/reports/x.csv")

    assert content_type.startswith("text/csv")
    assert payload == b"ok"
    with pytest.raises(PermissionError):
        read_allowed_report("/etc/passwd")


def test_remote_auto_execute_requires_two_guards(monkeypatch) -> None:
    monkeypatch.setenv("FB_ALLOW_WRITES", "1")
    monkeypatch.delenv("REMOTE_AUTO_ALLOW_DELETES", raising=False)

    with pytest.raises(PermissionError):
        assert_remote_auto_execute_allowed()

    monkeypatch.setenv("REMOTE_AUTO_ALLOW_DELETES", "1")
    assert_remote_auto_execute_allowed()
