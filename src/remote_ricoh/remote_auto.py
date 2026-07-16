"""Cykliczny workflow zlecen REMOTE: stan lokalny, raporty i panel."""

from __future__ import annotations

import csv
import html
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .device_delete import DeviceDeleteReportRow
from .portal import RicohPortalClient
from .service_orders import ServiceOrderRow

DEFAULT_REMOTE_AUTO_DB = Path("local/remote_auto/remote_auto.sqlite")
DEFAULT_REMOTE_AUTO_REPORT_DIR = Path("local/remote_auto/reports")
REMOTE_AUTO_DELETE_GUARD = "REMOTE_AUTO_ALLOW_DELETES"
REMOTE_AUTO_FIREBIRD_GUARD = "FB_ALLOW_WRITES"
REMOTE_AUTO_WAIT_DAYS = 7
REMOTE_AUTO_FRESHNESS_MONTHS = 1
REMOTE_AUTO_PANEL_PORT = 8099

ORDER_STATUS_WAITING_RECENT = "waiting_recent"
ORDER_STATUS_READY_DELETE = "ready_delete"
ORDER_STATUS_DELETE_PENDING = "delete_pending"
ORDER_STATUS_REMOTE_NOT_FOUND = "remote_not_found"
ORDER_STATUS_CLOSED = "closed"
ORDER_STATUS_SKIPPED = "skipped"
ORDER_STATUS_FAILED = "failed"

QUEUE_STATUSES = {
    ORDER_STATUS_WAITING_RECENT,
    ORDER_STATUS_READY_DELETE,
    ORDER_STATUS_DELETE_PENDING,
}


@dataclass(frozen=True, slots=True)
class RemoteAutoDecision:
    """Decyzja automatu dla pojedynczego wyniku Remote."""

    order_status: str
    event_type: str
    reason: str
    next_check_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RemoteAutoRunResult:
    """Podsumowanie pojedynczego uruchomienia workflow."""

    run_id: int
    mode: str
    execute: bool
    scanned_orders: int
    remote_checked: int
    status_counts: dict[str, int]
    remote_report_path: Path | None
    action_report_paths: list[Path]

    def as_log_message(self) -> str:
        parts = ", ".join(f"{key}={value}" for key, value in sorted(self.status_counts.items()))
        return (
            "Remote auto: "
            f"mode={self.mode}, execute={self.execute}, scanned_orders={self.scanned_orders}, "
            f"remote_checked={self.remote_checked}, {parts or 'brak statusow'}"
        )


def assert_remote_auto_execute_allowed() -> None:
    """Wymaga dwoch jawnych zmiennych przed realnym workflow automatu."""
    if os.getenv(REMOTE_AUTO_FIREBIRD_GUARD, "").strip() != "1":
        raise PermissionError(f"Realny workflow wymaga {REMOTE_AUTO_FIREBIRD_GUARD}=1.")
    if os.getenv(REMOTE_AUTO_DELETE_GUARD, "").strip() != "1":
        raise PermissionError(f"Realny workflow wymaga {REMOTE_AUTO_DELETE_GUARD}=1.")


def subtract_months(value: datetime, months: int) -> datetime:
    """Odejmuje miesiace kalendarzowe, zachowujac dzien gdy to mozliwe."""
    return RicohPortalClient._subtract_months(value, months)  # noqa: SLF001


def parse_remote_last_report_time(value: str) -> datetime | None:
    """Parsuje format Last Report Date/Time z portalu Remote."""
    return RicohPortalClient._parse_last_report_time(value)  # noqa: SLF001


def decide_remote_auto_status(
    row: DeviceDeleteReportRow,
    *,
    now: datetime,
) -> RemoteAutoDecision:
    """Mapuje status raportu Remote na stan kolejki automatu."""
    status = row.status
    if status in {"not_found", "deleted"}:
        return RemoteAutoDecision(
            order_status=ORDER_STATUS_REMOTE_NOT_FOUND,
            event_type="remote_not_found",
            reason="Remote potwierdza brak urzadzenia.",
        )
    if status == "delete_pending":
        return RemoteAutoDecision(
            order_status=ORDER_STATUS_DELETE_PENDING,
            event_type="delete_pending",
            reason="Remote przyjal usuniecie; Requested Status=Removing.",
            next_check_at=now,
        )
    if status in {"would_delete", "would_delete_recent_override"}:
        return RemoteAutoDecision(
            order_status=ORDER_STATUS_READY_DELETE,
            event_type="ready_delete",
            reason="Urzadzenie kwalifikuje sie do usuniecia w realnym trybie.",
            next_check_at=now,
        )
    if status == "skipped_recent_report":
        return RemoteAutoDecision(
            order_status=ORDER_STATUS_WAITING_RECENT,
            event_type="waiting_recent",
            reason="Last Report Date/Time jest nowszy niz prog jednego miesiaca.",
            next_check_at=now + timedelta(days=REMOTE_AUTO_WAIT_DAYS),
        )
    if status == "ambiguous":
        return RemoteAutoDecision(
            order_status=ORDER_STATUS_SKIPPED,
            event_type="ambiguous_remote_match",
            reason="Remote zwrocil wiecej niz jedno dopasowanie.",
        )
    if status == "skipped_missing_last_report":
        return RemoteAutoDecision(
            order_status=ORDER_STATUS_SKIPPED,
            event_type="missing_last_report",
            reason="Brak poprawnej daty Last Report Date/Time.",
        )
    return RemoteAutoDecision(
        order_status=ORDER_STATUS_FAILED if status == "failed" else ORDER_STATUS_SKIPPED,
        event_type=status or "unknown_remote_status",
        reason=row.message or "Nieobslugiwany status Remote.",
    )


def format_order_event_note(event_time: datetime, reason: str, last_report_time: str) -> str:
    """Buduje krotki wpis do WYKONANIE dla odlozenia albo bledu."""
    last_report = last_report_time or "brak"
    return (
        f"{event_time:%Y-%m-%d %H:%M} - Remote: nie usunieto; "
        f"powod: {reason}; Last Report Date/Time: {last_report}."
    )


class RemoteAutoStore:
    """Lokalny magazyn SQLite dla kolejki REMOTE i historii zdarzen."""

    def __init__(self, db_path: Path = DEFAULT_REMOTE_AUTO_DB) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_orders (
                    id_zlecenie_table INTEGER PRIMARY KEY,
                    order_label TEXT NOT NULL,
                    serial TEXT NOT NULL,
                    stan TEXT NOT NULL DEFAULT '',
                    problem TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    remote_status TEXT NOT NULL DEFAULT '',
                    last_report_time TEXT NOT NULL DEFAULT '',
                    requested_status TEXT NOT NULL DEFAULT '',
                    last_reason TEXT NOT NULL DEFAULT '',
                    next_check_at TEXT NOT NULL DEFAULT '',
                    remote_report_path TEXT NOT NULL DEFAULT '',
                    close_report_path TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TEXT NOT NULL,
                    id_zlecenie_table INTEGER,
                    order_label TEXT NOT NULL DEFAULT '',
                    serial TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    status_from TEXT NOT NULL DEFAULT '',
                    status_to TEXT NOT NULL DEFAULT '',
                    remote_status TEXT NOT NULL DEFAULT '',
                    last_report_time TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    report_path TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS remote_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL,
                    execute INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    message TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def start_run(self, mode: str, execute: bool, now: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO remote_runs (started_at, mode, execute, status)
                VALUES (?, ?, ?, ?)
                """,
                (_dt(now), mode, int(execute), "running"),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        message: str,
        summary: dict[str, Any],
        now: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE remote_runs
                SET finished_at = ?, status = ?, message = ?, summary_json = ?
                WHERE id = ?
                """,
                (_dt(now), status, message, json.dumps(summary, ensure_ascii=False), run_id),
            )

    def upsert_order(self, row: ServiceOrderRow, now: datetime) -> None:
        current = self.get_order(row.id_zlecenie_table)
        status = current["status"] if current else "new"
        first_seen = current["first_seen_at"] if current else _dt(now)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO remote_orders (
                    id_zlecenie_table, order_label, serial, stan, problem, first_seen_at,
                    last_seen_at, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id_zlecenie_table) DO UPDATE SET
                    order_label = excluded.order_label,
                    serial = excluded.serial,
                    stan = excluded.stan,
                    problem = excluded.problem,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    row.id_zlecenie_table,
                    row.order_label,
                    row.serial,
                    row.stan,
                    row.problem,
                    first_seen,
                    _dt(now),
                    status,
                    _dt(now),
                ),
            )

    def get_order(self, id_zlecenie_table: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM remote_orders WHERE id_zlecenie_table = ?",
                (id_zlecenie_table,),
            ).fetchone()

    def due_order_ids(self, now: datetime) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id_zlecenie_table
                FROM remote_orders
                WHERE status IN (?, ?, ?)
                  AND (
                    status IN (?, ?)
                    OR next_check_at = ''
                    OR next_check_at <= ?
                  )
                ORDER BY next_check_at, id_zlecenie_table
                """,
                (
                    ORDER_STATUS_WAITING_RECENT,
                    ORDER_STATUS_READY_DELETE,
                    ORDER_STATUS_DELETE_PENDING,
                    ORDER_STATUS_READY_DELETE,
                    ORDER_STATUS_DELETE_PENDING,
                    _dt(now),
                ),
            ).fetchall()
            return [int(row["id_zlecenie_table"]) for row in rows]

    def should_skip_daily(self, row: ServiceOrderRow, now: datetime) -> bool:
        current = self.get_order(row.id_zlecenie_table)
        if current is None:
            return False
        if current["status"] == ORDER_STATUS_CLOSED:
            return True
        if current["status"] != ORDER_STATUS_WAITING_RECENT:
            return False
        next_check_at = current["next_check_at"]
        return bool(next_check_at and next_check_at > _dt(now))

    def set_order_status(
        self,
        row: ServiceOrderRow,
        *,
        status: str,
        remote_status: str = "",
        last_report_time: str = "",
        requested_status: str = "",
        reason: str = "",
        next_check_at: datetime | None = None,
        remote_report_path: Path | None = None,
        close_report_path: Path | None = None,
        now: datetime,
    ) -> str:
        current = self.get_order(row.id_zlecenie_table)
        previous = current["status"] if current else ""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE remote_orders
                SET status = ?, remote_status = ?, last_report_time = ?,
                    requested_status = ?, last_reason = ?, next_check_at = ?,
                    remote_report_path = COALESCE(NULLIF(?, ''), remote_report_path),
                    close_report_path = COALESCE(NULLIF(?, ''), close_report_path),
                    updated_at = ?
                WHERE id_zlecenie_table = ?
                """,
                (
                    status,
                    remote_status,
                    last_report_time,
                    requested_status,
                    reason,
                    _dt(next_check_at) if next_check_at else "",
                    str(remote_report_path or ""),
                    str(close_report_path or ""),
                    _dt(now),
                    row.id_zlecenie_table,
                ),
            )
        return previous

    def record_event(
        self,
        row: ServiceOrderRow | None,
        *,
        event_type: str,
        status_from: str = "",
        status_to: str = "",
        remote_status: str = "",
        last_report_time: str = "",
        message: str = "",
        report_path: Path | None = None,
        now: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO remote_events (
                    event_time, id_zlecenie_table, order_label, serial, event_type,
                    status_from, status_to, remote_status, last_report_time, message, report_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _dt(now),
                    row.id_zlecenie_table if row else None,
                    row.order_label if row else "",
                    row.serial if row else "",
                    event_type,
                    status_from,
                    status_to,
                    remote_status,
                    last_report_time,
                    message,
                    str(report_path or ""),
                ),
            )

    def dashboard(self) -> dict[str, Any]:
        with self._connect() as connection:
            counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM remote_orders GROUP BY status"
                ).fetchall()
            }
            orders = connection.execute(
                """
                SELECT *
                FROM remote_orders
                ORDER BY
                    CASE status
                        WHEN 'waiting_recent' THEN 1
                        WHEN 'ready_delete' THEN 2
                        WHEN 'delete_pending' THEN 3
                        WHEN 'failed' THEN 4
                        ELSE 9
                    END,
                    next_check_at,
                    order_label
                LIMIT 300
                """
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM remote_events ORDER BY id DESC LIMIT 200"
            ).fetchall()
            runs = connection.execute(
                "SELECT * FROM remote_runs ORDER BY id DESC LIMIT 100"
            ).fetchall()
            return {"counts": counts, "orders": orders, "events": events, "runs": runs}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def write_remote_auto_csv_report(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    report_dir: Path = DEFAULT_REMOTE_AUTO_REPORT_DIR,
) -> Path:
    """Zapisuje lokalny raport workflow do CSV."""
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"remote_auto_{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    fieldnames = [
        "order",
        "serial",
        "status",
        "remote_status",
        "last_report_time",
        "requested_status",
        "reason",
        "next_check_at",
        "remote_report_path",
        "close_report_path",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    return report_path


def serve_remote_auto_panel(
    db_path: Path,
    *,
    host: str = "0.0.0.0",
    port: int = REMOTE_AUTO_PANEL_PORT,
    log=print,  # noqa: ANN001
) -> int:
    """Uruchamia prosty panel read-only dla lokalnego SQLite."""
    store = RemoteAutoStore(db_path)
    store.initialize()
    handler = _build_panel_handler(db_path)
    last_error: OSError | None = None
    for candidate_port in range(port, port + 21):
        try:
            server = ThreadingHTTPServer((host, candidate_port), handler)
        except OSError as exc:
            last_error = exc
            continue
        url_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
        log(f"Panel Remote auto: http://{url_host}:{candidate_port}/")
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return 0
    raise RuntimeError(
        f"Nie udalo sie uruchomic panelu na portach {port}-{port + 20}: {last_error}"
    )


def render_remote_auto_dashboard(db_path: Path) -> str:
    """Renderuje HTML panelu na podstawie lokalnej bazy."""
    store = RemoteAutoStore(db_path)
    store.initialize()
    data = store.dashboard()
    counts = data["counts"]
    order_rows = "\n".join(_render_order_row(row) for row in data["orders"])
    event_rows = "\n".join(_render_event_row(row) for row in data["events"])
    run_rows = "\n".join(_render_run_row(row) for row in data["runs"])
    count_cards = "\n".join(
        f"<div class='metric'><strong>{_e(status)}</strong><span>{count}</span></div>"
        for status, count in sorted(counts.items())
    )
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Remote auto</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; color: #172026; background: #f5f7f8; }}
    header {{ background: #1f3a44; color: white; padding: 16px 24px; }}
    main {{ padding: 18px 24px 32px; }}
    h1 {{ margin: 0; font-size: 22px; }}
    h2 {{ margin: 24px 0 10px; font-size: 18px; }}
    .metrics {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .metric {{ background: white; border: 1px solid #d9e1e5; padding: 10px 12px; border-radius: 6px; min-width: 140px; }}
    .metric span {{ display: block; font-size: 24px; margin-top: 4px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; border: 1px solid #d9e1e5; }}
    th, td {{ padding: 7px 8px; border-bottom: 1px solid #e6ecef; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: #eaf0f2; position: sticky; top: 0; }}
    a {{ color: #0f5d7a; }}
    .empty {{ padding: 16px; background: white; border: 1px solid #d9e1e5; }}
  </style>
</head>
<body>
<header><h1>Remote auto</h1></header>
<main>
  <section class="metrics">{count_cards or "<div class='metric'><strong>brak</strong><span>0</span></div>"}</section>
  <h2>Kolejka i zlecenia</h2>
  {_table(["Zlecenie", "Serial", "Status", "Last Report", "Nast. kontrola", "Powod", "Raport"], order_rows)}
  <h2>Ostatnie uruchomienia</h2>
  {_table(["ID", "Start", "Koniec", "Tryb", "Execute", "Status", "Podsumowanie"], run_rows)}
  <h2>Zdarzenia</h2>
  {_table(["Czas", "Zlecenie", "Serial", "Zdarzenie", "Zmiana", "Komunikat", "Raport"], event_rows)}
</main>
</body>
</html>"""


def read_allowed_report(path_text: str, *, cwd: Path | None = None) -> tuple[str, bytes]:
    """Czyta raport tylko z dozwolonych katalogow lokalnych."""
    root = (cwd or Path.cwd()).resolve()
    requested = (
        (root / path_text).resolve()
        if not Path(path_text).is_absolute()
        else Path(path_text).resolve()
    )
    allowed_roots = [(root / "local" / "remote_auto").resolve(), (root / ".debug").resolve()]
    if not any(requested == item or requested.is_relative_to(item) for item in allowed_roots):
        raise PermissionError("Raport poza dozwolonym katalogiem.")
    if not requested.is_file():
        raise FileNotFoundError(f"Brak raportu: {requested}")
    content_type = (
        "text/csv; charset=utf-8" if requested.suffix == ".csv" else "text/plain; charset=utf-8"
    )
    return content_type, requested.read_bytes()


def _build_panel_handler(db_path: Path) -> type[BaseHTTPRequestHandler]:
    class RemoteAutoPanelHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = render_remote_auto_dashboard(db_path).encode("utf-8")
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", body)
                return
            if parsed.path == "/report":
                params = parse_qs(parsed.query)
                report_path = (params.get("path") or [""])[0]
                try:
                    content_type, body = read_allowed_report(report_path)
                except (FileNotFoundError, PermissionError) as exc:
                    self._send(
                        HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", str(exc).encode("utf-8")
                    )
                    return
                self._send(HTTPStatus.OK, content_type, body)
                return
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found")

        def log_message(self, format: str, *args: object) -> None:
            return None

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return RemoteAutoPanelHandler


def _table(headers: list[str], rows: str) -> str:
    if not rows:
        return "<div class='empty'>Brak danych.</div>"
    header = "".join(f"<th>{_e(item)}</th>" for item in headers)
    return f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"


def _render_order_row(row: sqlite3.Row) -> str:
    report = _report_link(row["remote_report_path"])
    return (
        "<tr>"
        f"<td>{_e(row['order_label'])}</td>"
        f"<td>{_e(row['serial'])}</td>"
        f"<td>{_e(row['status'])}</td>"
        f"<td>{_e(row['last_report_time'])}</td>"
        f"<td>{_e(row['next_check_at'])}</td>"
        f"<td>{_e(row['last_reason'])}</td>"
        f"<td>{report}</td>"
        "</tr>"
    )


def _render_event_row(row: sqlite3.Row) -> str:
    report = _report_link(row["report_path"])
    change = f"{row['status_from']} -> {row['status_to']}".strip()
    return (
        "<tr>"
        f"<td>{_e(row['event_time'])}</td>"
        f"<td>{_e(row['order_label'])}</td>"
        f"<td>{_e(row['serial'])}</td>"
        f"<td>{_e(row['event_type'])}</td>"
        f"<td>{_e(change)}</td>"
        f"<td>{_e(row['message'])}</td>"
        f"<td>{report}</td>"
        "</tr>"
    )


def _render_run_row(row: sqlite3.Row) -> str:
    return (
        "<tr>"
        f"<td>{row['id']}</td>"
        f"<td>{_e(row['started_at'])}</td>"
        f"<td>{_e(row['finished_at'])}</td>"
        f"<td>{_e(row['mode'])}</td>"
        f"<td>{'tak' if row['execute'] else 'nie'}</td>"
        f"<td>{_e(row['status'])}</td>"
        f"<td><code>{_e(row['summary_json'])}</code></td>"
        "</tr>"
    )


def _report_link(path_text: str) -> str:
    if not path_text:
        return ""
    escaped = _e(path_text)
    return f"<a href='/report?path={escaped}'>{escaped}</a>"


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _dt(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    return value.isoformat()
