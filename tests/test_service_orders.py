from __future__ import annotations

import csv
from pathlib import Path

import pytest

from remote_ricoh.service_orders import (
    DEFAULT_CLOSE_OPERATOR,
    DEFAULT_REPAIR_TEXT,
    FirebirdServiceOrderClient,
    ServiceOrderFilter,
    ServiceOrderRow,
    append_close_operator,
    append_repair_text,
    assert_service_order_writes_allowed,
    diff_service_order_snapshots,
    load_remote_final_statuses,
    load_service_order_filters,
    write_service_order_snapshot,
)


def _db_row(
    *,
    table_id: int = 79331,
    order_number: int = 14331,
    year: int = 2025,
    stan: str = "O",
    serial: str = "G696M313134",
    problem: str = "odpiąć REMOTE",
    wykonanie: str | None = None,
    operator: str = "Utworzył: JoannaG",
    technik: str = "Marcin Jarmuszkiewicz",
) -> dict[str, object]:
    return {
        "ID_ZLECENIE_TABLE": table_id,
        "ID_ZLECENIE": order_number,
        "ROK": year,
        "STAN": stan,
        "DATA": "2025-12-30",
        "DATA_Z": None,
        "SERIAL": serial,
        "PROBLEM": problem,
        "WYKONANIE": wykonanie,
        "OPERATOR": operator,
        "TECHNIK": technik,
        "EDITCNT": 27,
        "EDITDATE": "2026-05-21",
        "EDITTIME": "10:25:07",
        "EDITSOURCE": None,
        "ID_KLIENT": 134,
        "ID_MASZYNA": 6116,
        "ID_FAKTURA": None,
        "FAKTURA": "",
    }


class _FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.fetchall_result: list[tuple[object, ...]] = []
        self.fetchone_result: tuple[object, ...] | None = None
        self.executed_sql: list[str] = []

    def execute(self, sql: str, params=()) -> None:  # noqa: ANN001
        compact_sql = " ".join(sql.split())
        self.executed_sql.append(compact_sql)
        params = tuple(params or ())

        if compact_sql.startswith("SELECT") and "WHERE ID_ZLECENIE_TABLE = ?" in compact_sql:
            row = next((item for item in self.rows if item["ID_ZLECENIE_TABLE"] == params[0]), None)
            self.fetchone_result = _tuple_from_row(row) if row else None
            return

        if compact_sql.startswith("SELECT") and "FROM ZLECENIE" in compact_sql:
            matched = list(self.rows)
            idx = 0
            if "ID_ZLECENIE = ?" in compact_sql:
                value = params[idx]
                idx += 1
                matched = [row for row in matched if row["ID_ZLECENIE"] == value]
            if "ROK = ?" in compact_sql:
                value = params[idx]
                idx += 1
                matched = [row for row in matched if row["ROK"] == value]
            if "TRIM(COALESCE(SERIAL, '')) = ?" in compact_sql:
                value = params[idx]
                idx += 1
                matched = [row for row in matched if row["SERIAL"] == value]
            if "PROBLEM CONTAINING ?" in compact_sql:
                value = str(params[idx]).casefold()
                matched = [row for row in matched if value in str(row["PROBLEM"]).casefold()]
            self.fetchall_result = [_tuple_from_row(row) for row in matched]
            return

        if compact_sql.startswith("UPDATE ZLECENIE SET WYKONANIE = ?, OPERATOR = ?"):
            new_wykonanie, new_operator, table_id = params
            for row in self.rows:
                if row["ID_ZLECENIE_TABLE"] == table_id and row["STAN"] in {"O", "ZR"}:
                    row["WYKONANIE"] = new_wykonanie
                    row["OPERATOR"] = new_operator
                    row["STAN"] = "ZR"
            return

        if compact_sql.startswith("UPDATE ZLECENIE SET STAN = 'Z'"):
            table_id = params[0]
            for row in self.rows:
                if row["ID_ZLECENIE_TABLE"] == table_id and row["STAN"] == "ZR":
                    row["STAN"] = "Z"
                    row["DATA_Z"] = "2026-07-02"
            return

        raise AssertionError(f"Nieobsluzone SQL: {compact_sql}")

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.fetchall_result

    def fetchone(self) -> tuple[object, ...] | None:
        return self.fetchone_result


class _FakeConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.cursor_obj = _FakeCursor(rows)
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.closed = True


class _TestServiceOrderClient(FirebirdServiceOrderClient):
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.repair_text = DEFAULT_REPAIR_TEXT

    def _connect(self) -> _FakeConnection:
        return self.connection


def _tuple_from_row(row: dict[str, object] | None) -> tuple[object, ...]:
    assert row is not None
    return (
        row["ID_ZLECENIE_TABLE"],
        row["ID_ZLECENIE"],
        row["ROK"],
        row["STAN"],
        row["DATA"],
        row["DATA_Z"],
        row["SERIAL"],
        row["PROBLEM"],
        row["WYKONANIE"],
        row["OPERATOR"],
        row["TECHNIK"],
        row["EDITCNT"],
        row["EDITDATE"],
        row["EDITTIME"],
        row["EDITSOURCE"],
        row["ID_KLIENT"],
        row["ID_MASZYNA"],
        row["ID_FAKTURA"],
        row["FAKTURA"],
    )


def test_load_service_order_filters_from_txt(tmp_path: Path) -> None:
    source = tmp_path / "service_orders_filters.txt"
    source.write_text(
        "\n".join(
            [
                "# komentarz",
                "14331/2025",
                "serial:G696M313134",
                "problem:odpiąć REMOTE",
                "14331/2025",
            ]
        ),
        encoding="utf-8",
    )

    filters = load_service_order_filters(source)

    assert [item.key for item in filters] == [
        "14331/2025",
        "serial:G696M313134",
        "problem:odpiąć REMOTE",
    ]


def test_load_service_order_filters_from_csv(tmp_path: Path) -> None:
    source = tmp_path / "orders.csv"
    source.write_text(
        "order,serial,problem_contains,allow_multiple\n"
        "14331/2025,G696M313134,odpiąć REMOTE,tak\n",
        encoding="utf-8",
    )

    filters = load_service_order_filters(source)

    assert filters == [
        ServiceOrderFilter(
            order_number=14331,
            year=2025,
            serial="G696M313134",
            problem_contains="odpiąć REMOTE",
            allow_multiple=True,
        )
    ]


def test_load_remote_final_statuses_supports_final_status(tmp_path: Path) -> None:
    source = tmp_path / "remote.csv"
    source.write_text(
        "serial,final_status\nG696M313134,not_found\nE154MB32964,skipped_recent_report\n",
        encoding="utf-8",
    )

    assert load_remote_final_statuses(source) == {
        "g696m313134": "not_found",
        "e154mb32964": "skipped_recent_report",
    }


def test_append_repair_text_without_duplicate() -> None:
    assert append_repair_text("") == DEFAULT_REPAIR_TEXT
    assert append_repair_text("Diagnoza") == f"Diagnoza\n{DEFAULT_REPAIR_TEXT}"
    assert append_repair_text(DEFAULT_REPAIR_TEXT) == DEFAULT_REPAIR_TEXT


def test_append_close_operator_without_duplicate() -> None:
    assert (
        append_close_operator("Utworzył: JoannaG")
        == f"Utworzył: JoannaG Edytował: {DEFAULT_CLOSE_OPERATOR},"
        f"Zamknął :{DEFAULT_CLOSE_OPERATOR}"
    )
    value = (
        f"Utworzył: JoannaG Edytował: {DEFAULT_CLOSE_OPERATOR},Zamknął :{DEFAULT_CLOSE_OPERATOR}"
    )
    assert append_close_operator(value) == value


def test_close_orders_dry_run_skips_remote_status() -> None:
    connection = _FakeConnection([_db_row(order_number=13721, serial="E154MB32964")])
    client = _TestServiceOrderClient(connection)

    rows = client.close_orders(
        [ServiceOrderFilter(order_number=13721, year=2025)],
        execute=False,
        remote_statuses={"e154mb32964": "skipped_recent_report"},
    )

    assert rows[0].status == "skipped_remote_status"
    assert rows[0].remote_final_status == "skipped_recent_report"
    assert connection.commit_calls == 0
    assert connection.closed is True


def test_close_orders_dry_run_plans_close() -> None:
    connection = _FakeConnection([_db_row(wykonanie="Demontaż")])
    client = _TestServiceOrderClient(connection)

    rows = client.close_orders(
        [ServiceOrderFilter(order_number=14331, year=2025)],
        execute=False,
        remote_statuses={"g696m313134": "not_found"},
    )

    assert rows[0].status == "would_close"
    assert rows[0].before_stan == "O"
    assert rows[0].after_stan == "Z"
    assert rows[0].after_wykonanie == f"Demontaż\n{DEFAULT_REPAIR_TEXT}"
    assert connection.commit_calls == 0


def test_close_orders_execute_requires_env(monkeypatch) -> None:
    monkeypatch.delenv("FB_ALLOW_WRITES", raising=False)

    with pytest.raises(PermissionError):
        assert_service_order_writes_allowed()


def test_close_orders_execute_updates_status_operator_and_keeps_technik(monkeypatch) -> None:
    monkeypatch.setenv("FB_ALLOW_WRITES", "1")
    row = _db_row()
    connection = _FakeConnection([row])
    client = _TestServiceOrderClient(connection)

    rows = client.close_orders(
        [ServiceOrderFilter(order_number=14331, year=2025)],
        execute=True,
        remote_statuses={"g696m313134": "not_found"},
    )

    assert rows[0].status == "closed"
    assert row["STAN"] == "Z"
    assert row["DATA_Z"] == "2026-07-02"
    assert row["WYKONANIE"] == DEFAULT_REPAIR_TEXT
    assert (
        row["OPERATOR"] == "Utworzył: JoannaG "
        f"Edytował: {DEFAULT_CLOSE_OPERATOR},Zamknął :{DEFAULT_CLOSE_OPERATOR}"
    )
    assert row["TECHNIK"] == "Marcin Jarmuszkiewicz"
    assert connection.commit_calls == 1
    assert any(
        "SET WYKONANIE = ?, OPERATOR = ?, STAN = 'ZR'" in sql
        for sql in connection.cursor_obj.executed_sql
    )
    assert any(
        "SET STAN = 'Z', DATA_Z = CURRENT_DATE" in sql for sql in connection.cursor_obj.executed_sql
    )


def test_diff_service_order_snapshots(tmp_path: Path) -> None:
    before = write_service_order_snapshot(
        [
            ServiceOrderRow(
                filter_key="14331/2025",
                id_zlecenie_table=79331,
                id_zlecenie=14331,
                rok=2025,
                stan="O",
                serial="G696M313134",
                wykonanie="",
            )
        ],
        tmp_path,
    )
    after = write_service_order_snapshot(
        [
            ServiceOrderRow(
                filter_key="14331/2025",
                id_zlecenie_table=79331,
                id_zlecenie=14331,
                rok=2025,
                stan="Z",
                serial="G696M313134",
                wykonanie=DEFAULT_REPAIR_TEXT,
            )
        ],
        tmp_path,
    )

    rows = diff_service_order_snapshots(before, after)

    changes = {(row.field, row.before, row.after) for row in rows}
    assert ("stan", "O", "Z") in changes
    assert ("wykonanie", "", DEFAULT_REPAIR_TEXT) in changes


def test_write_service_order_snapshot_has_expected_columns(tmp_path: Path) -> None:
    path = write_service_order_snapshot(
        [
            ServiceOrderRow(
                filter_key="14331/2025",
                id_zlecenie_table=79331,
                id_zlecenie=14331,
                rok=2025,
                stan="O",
            )
        ],
        tmp_path,
    )

    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["filter_key"] == "14331/2025"
    assert rows[0]["id_zlecenie_table"] == "79331"
    assert rows[0]["stan"] == "O"
