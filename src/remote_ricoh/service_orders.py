"""Snapshoty i zamykanie zlecen serwisowych w Firebird Menadzera Serwisu."""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .firebird_cmail import FirebirdCmailImporter

DEFAULT_REPAIR_TEXT = "Urządzenie usunięte z Remote."
DEFAULT_CLOSE_OPERATOR = "Marcin"
DEFAULT_REPORT_DIR = Path(".debug/ricoh_service_orders")
ORDER_RE = re.compile(r"^\s*(?P<number>\d+)\s*/\s*(?P<year>\d{4})\s*$")
TRUE_VALUES = {"1", "true", "t", "tak", "yes", "y"}


@dataclass(frozen=True, slots=True)
class ServiceOrderFilter:
    """Pojedynczy filtr wyboru zlecen serwisowych."""

    order_number: int | None = None
    year: int | None = None
    serial: str = ""
    problem_contains: str = ""
    allow_multiple: bool = False

    @property
    def key(self) -> str:
        parts: list[str] = []
        if self.order_number is not None and self.year is not None:
            parts.append(f"{self.order_number}/{self.year}")
        if self.serial:
            parts.append(f"serial:{self.serial}")
        if self.problem_contains:
            parts.append(f"problem:{self.problem_contains}")
        if self.allow_multiple:
            parts.append("allow_multiple:true")
        return ";".join(parts) or "<empty>"

    def normalized_key(self) -> tuple[int | None, int | None, str, str, bool]:
        """Zwraca stabilny klucz do deduplikacji filtrow."""
        return (
            self.order_number,
            self.year,
            self.serial.casefold(),
            self.problem_contains.casefold(),
            self.allow_multiple,
        )


@dataclass(frozen=True, slots=True)
class ServiceOrderRow:
    """Snapshot wybranego rekordu ZLECENIE."""

    filter_key: str
    id_zlecenie_table: int
    id_zlecenie: int
    rok: int
    stan: str = ""
    data: str = ""
    data_z: str = ""
    serial: str = ""
    problem: str = ""
    wykonanie: str = ""
    operator: str = ""
    technik: str = ""
    editcnt: str = ""
    editdate: str = ""
    edittime: str = ""
    editsource: str = ""
    id_klient: str = ""
    id_maszyna: str = ""
    id_faktura: str = ""
    faktura: str = ""

    @property
    def order_label(self) -> str:
        return f"{self.id_zlecenie}/{self.rok}"

    def as_csv_row(self) -> dict[str, str | int]:
        return {
            "filter_key": self.filter_key,
            "id_zlecenie_table": self.id_zlecenie_table,
            "id_zlecenie": self.id_zlecenie,
            "rok": self.rok,
            "stan": self.stan,
            "data": self.data,
            "data_z": self.data_z,
            "serial": self.serial,
            "problem": self.problem,
            "wykonanie": self.wykonanie,
            "operator": self.operator,
            "technik": self.technik,
            "editcnt": self.editcnt,
            "editdate": self.editdate,
            "edittime": self.edittime,
            "editsource": self.editsource,
            "id_klient": self.id_klient,
            "id_maszyna": self.id_maszyna,
            "id_faktura": self.id_faktura,
            "faktura": self.faktura,
        }


@dataclass(frozen=True, slots=True)
class ServiceOrderActionRow:
    """Wynik dry-run albo realnej proby zamkniecia zlecenia."""

    filter_key: str
    status: str
    matched_count: int = 0
    id_zlecenie_table: str = ""
    order: str = ""
    serial: str = ""
    remote_final_status: str = ""
    before_stan: str = ""
    after_stan: str = ""
    before_data_z: str = ""
    after_data_z: str = ""
    before_wykonanie: str = ""
    after_wykonanie: str = ""
    message: str = ""

    def as_csv_row(self) -> dict[str, str | int]:
        return {
            "filter_key": self.filter_key,
            "status": self.status,
            "matched_count": self.matched_count,
            "id_zlecenie_table": self.id_zlecenie_table,
            "order": self.order,
            "serial": self.serial,
            "remote_final_status": self.remote_final_status,
            "before_stan": self.before_stan,
            "after_stan": self.after_stan,
            "before_data_z": self.before_data_z,
            "after_data_z": self.after_data_z,
            "before_wykonanie": self.before_wykonanie,
            "after_wykonanie": self.after_wykonanie,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ServiceOrderDiffRow:
    """Roznica pomiedzy dwoma snapshotami tego samego zlecenia."""

    id_zlecenie_table: str
    order: str
    serial: str
    field: str
    before: str
    after: str

    def as_csv_row(self) -> dict[str, str]:
        return {
            "id_zlecenie_table": self.id_zlecenie_table,
            "order": self.order,
            "serial": self.serial,
            "field": self.field,
            "before": self.before,
            "after": self.after,
        }


def load_service_order_filters(path: Path) -> list[ServiceOrderFilter]:
    """Wczytuje filtry z TXT albo CSV."""
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Brak pliku z filtrami zlecen: {source}")

    if source.suffix.casefold() == ".csv":
        filters = _load_csv_filters(source)
    else:
        filters = _load_txt_filters(source)
    return _deduplicate_filters(filters)


def load_remote_final_statuses(path: Path | None) -> dict[str, str]:
    """Wczytuje finalny status Remote z raportu usuwania urzadzen."""
    if path is None:
        return {}

    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Brak raportu Remote: {source}")

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fields = {name.casefold(): name for name in reader.fieldnames}
        serial_field = fields.get("serial")
        status_field = fields.get("final_status") or fields.get("status")
        if serial_field is None or status_field is None:
            raise ValueError("Raport Remote musi zawierac kolumny serial oraz final_status/status.")

        result: dict[str, str] = {}
        for row in reader:
            serial = (row.get(serial_field) or "").strip()
            status = (row.get(status_field) or "").strip()
            if serial and status:
                result[serial.casefold()] = status
        return result


def assert_service_order_writes_allowed() -> None:
    """Wymaga jawnej zmiennej srodowiskowej przed zapisem do Firebirda."""
    if os.getenv("FB_ALLOW_WRITES", "").strip() != "1":
        raise PermissionError("Realny zapis wymaga ustawienia FB_ALLOW_WRITES=1.")


def append_repair_text(existing: str, addition: str = DEFAULT_REPAIR_TEXT) -> str:
    """Dopisuje opis wykonania bez duplikowania tej samej informacji."""
    current = (existing or "").strip()
    text = addition.strip()
    if not text:
        return current
    if text.casefold() in current.casefold():
        return current
    if not current:
        return text
    return f"{current}\n{text}"


def append_close_operator(existing: str, operator_name: str = DEFAULT_CLOSE_OPERATOR) -> str:
    """Dopisuje slad operatora zgodny z Menadzerem Serwisu."""
    current = (existing or "").strip()
    marker = f"Zamknął :{operator_name}"
    if marker.casefold() in current.casefold():
        return current
    addition = f"Edytował: {operator_name},{marker}"
    if not current:
        return addition
    return f"{current} {addition}"


def append_edit_operator(existing: str, operator_name: str = DEFAULT_CLOSE_OPERATOR) -> str:
    """Dopisuje slad edycji operatora bez znacznika zamkniecia."""
    current = (existing or "").strip()
    marker = f"Edytował: {operator_name}"
    if marker.casefold() in current.casefold():
        return current
    if not current:
        return marker
    return f"{current} {marker}"


def write_service_order_snapshot(
    rows: list[ServiceOrderRow],
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """Zapisuje snapshot zlecen do CSV."""
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"service_order_snapshot_{_timestamp()}.csv"
    _write_dict_rows(report_path, SERVICE_ORDER_SNAPSHOT_FIELDS, [row.as_csv_row() for row in rows])
    return report_path


def write_service_order_action_report(
    rows: list[ServiceOrderActionRow],
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """Zapisuje raport dry-run/apply zamykania zlecen."""
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"service_order_close_report_{_timestamp()}.csv"
    _write_dict_rows(report_path, SERVICE_ORDER_ACTION_FIELDS, [row.as_csv_row() for row in rows])
    return report_path


def write_service_order_diff(
    rows: list[ServiceOrderDiffRow],
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """Zapisuje roznice snapshotow do CSV."""
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"service_order_diff_{_timestamp()}.csv"
    _write_dict_rows(report_path, SERVICE_ORDER_DIFF_FIELDS, [row.as_csv_row() for row in rows])
    return report_path


def diff_service_order_snapshots(before_path: Path, after_path: Path) -> list[ServiceOrderDiffRow]:
    """Porownuje dwa snapshoty CSV po ID_ZLECENIE_TABLE."""
    before_rows = _read_snapshot_rows(before_path)
    after_rows = _read_snapshot_rows(after_path)
    before_by_id = {row["id_zlecenie_table"]: row for row in before_rows}
    after_by_id = {row["id_zlecenie_table"]: row for row in after_rows}

    diff_rows: list[ServiceOrderDiffRow] = []
    for row_id in sorted(before_by_id.keys() | after_by_id.keys(), key=_sort_snapshot_id):
        before = before_by_id.get(row_id)
        after = after_by_id.get(row_id)
        if before is None:
            diff_rows.append(
                ServiceOrderDiffRow(
                    id_zlecenie_table=row_id,
                    order=_order_from_snapshot(after),
                    serial=after.get("serial", "") if after else "",
                    field="<row>",
                    before="",
                    after="added",
                )
            )
            continue
        if after is None:
            diff_rows.append(
                ServiceOrderDiffRow(
                    id_zlecenie_table=row_id,
                    order=_order_from_snapshot(before),
                    serial=before.get("serial", ""),
                    field="<row>",
                    before="present",
                    after="",
                )
            )
            continue

        for field in SERVICE_ORDER_SNAPSHOT_FIELDS:
            if field in {"filter_key"}:
                continue
            before_value = before.get(field, "")
            after_value = after.get(field, "")
            if before_value != after_value:
                diff_rows.append(
                    ServiceOrderDiffRow(
                        id_zlecenie_table=row_id,
                        order=_order_from_snapshot(after),
                        serial=after.get("serial", ""),
                        field=field,
                        before=before_value,
                        after=after_value,
                    )
                )
    return diff_rows


class FirebirdServiceOrderClient:
    """Klient Firebird do odczytu i aktualizacji tabeli ZLECENIE."""

    def __init__(
        self,
        *,
        mode: str,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        charset: str = "WIN1250",
        role: str | None = None,
        local_copy_path: str | None = None,
        repair_text: str = DEFAULT_REPAIR_TEXT,
    ) -> None:
        self.repair_text = repair_text
        self._importer = FirebirdCmailImporter(
            mode=mode,
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset=charset,
            role=role,
            local_copy_path=local_copy_path,
        )

    def snapshot(self, filters: list[ServiceOrderFilter]) -> list[ServiceOrderRow]:
        """Zwraca snapshot zlecen pasujacych do filtrow."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            rows: list[ServiceOrderRow] = []
            for item in filters:
                rows.extend(self._fetch_filter_rows(cursor, item))
            return rows
        finally:
            connection.close()

    def fetch_remote_open_orders(self) -> list[ServiceOrderRow]:
        """Zwraca otwarte zlecenia przypisane do technika REMOTE."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    ID_ZLECENIE_TABLE, ID_ZLECENIE, ROK, STAN, DATA, DATA_Z,
                    SERIAL, PROBLEM, WYKONANIE, OPERATOR, TECHNIK, EDITCNT,
                    EDITDATE, EDITTIME, EDITSOURCE, ID_KLIENT, ID_MASZYNA,
                    ID_FAKTURA, FAKTURA
                FROM ZLECENIE
                WHERE STAN IN ('O', 'ZR')
                  AND TRIM(UPPER(COALESCE(TECHNIK, ''))) = 'REMOTE'
                ORDER BY DATA, ROK, ID_ZLECENIE, ID_ZLECENIE_TABLE
                """
            )
            return [_row_from_db("remote_auto", row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def fetch_by_table_ids(self, table_ids: list[int]) -> list[ServiceOrderRow]:
        """Zwraca zlecenia po ID_ZLECENIE_TABLE z zachowaniem kolejnosci wejscia."""
        if not table_ids:
            return []
        connection = self._connect()
        try:
            cursor = connection.cursor()
            rows: list[ServiceOrderRow] = []
            for table_id in table_ids:
                row = self._fetch_by_table_id(cursor, table_id, f"id_table:{table_id}")
                if row is not None:
                    rows.append(row)
            return rows
        finally:
            connection.close()

    def close_orders(
        self,
        filters: list[ServiceOrderFilter],
        *,
        execute: bool,
        remote_statuses: dict[str, str] | None = None,
        repair_text: str | None = None,
        preserve_metadata: bool = False,
    ) -> list[ServiceOrderActionRow]:
        """Planowo albo realnie zamyka zlecenia wskazane filtrami."""
        if execute:
            assert_service_order_writes_allowed()

        connection = self._connect()
        try:
            cursor = connection.cursor()
            results: list[ServiceOrderActionRow] = []
            for item in filters:
                matches = self._fetch_filter_rows(cursor, item)
                if not matches:
                    results.append(
                        ServiceOrderActionRow(
                            filter_key=item.key,
                            status="not_found",
                            matched_count=0,
                            message="Nie znaleziono zlecenia dla filtra.",
                        )
                    )
                    continue
                if len(matches) > 1 and not item.allow_multiple:
                    results.append(
                        ServiceOrderActionRow(
                            filter_key=item.key,
                            status="ambiguous",
                            matched_count=len(matches),
                            message=(
                                "Filtr zwrocil wiele zlecen; dodaj allow_multiple=true "
                                "albo podaj dokladniejszy filtr."
                            ),
                        )
                    )
                    continue

                for row in matches:
                    results.append(
                        self._process_order_row(
                            cursor,
                            row,
                            execute=execute,
                            remote_statuses=remote_statuses or {},
                            repair_text=repair_text or self.repair_text,
                            preserve_metadata=preserve_metadata,
                        )
                    )
            if execute:
                connection.commit()
            return results
        except Exception:
            if execute and hasattr(connection, "rollback"):
                connection.rollback()
            raise
        finally:
            connection.close()

    def append_order_event(
        self,
        row: ServiceOrderRow,
        note: str,
        *,
        execute: bool,
    ) -> ServiceOrderActionRow:
        """Dopisuje zdarzenie do WYKONANIE bez zamykania zlecenia."""
        if execute:
            assert_service_order_writes_allowed()

        if row.stan == "Z":
            return _action_for_row(row, "already_closed", message="Zlecenie jest juz zamkniete.")
        if row.stan not in {"O", "ZR"}:
            return _action_for_row(
                row,
                "skipped_status",
                message=f"Pominieto: nieobslugiwany status zlecenia {row.stan!r}.",
            )

        new_wykonanie = append_repair_text(row.wykonanie, note)
        if new_wykonanie == row.wykonanie:
            return _action_for_row(
                row,
                "already_noted",
                after_wykonanie=new_wykonanie,
                message="Zdarzenie bylo juz dopisane.",
            )
        new_operator = append_edit_operator(row.operator)
        if not execute:
            return _action_for_row(
                row,
                "would_append_event",
                after_wykonanie=new_wykonanie,
                message="Dry-run: zdarzenie zostaloby dopisane do WYKONANIE.",
            )

        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET WYKONANIE = ?, OPERATOR = ?
                WHERE ID_ZLECENIE_TABLE = ? AND STAN IN ('O', 'ZR')
                """,
                (new_wykonanie, new_operator, row.id_zlecenie_table),
            )
            connection.commit()
            after = self._fetch_by_table_id(cursor, row.id_zlecenie_table, row.filter_key)
            return _action_for_row(
                row,
                "event_appended",
                after_wykonanie=after.wykonanie if after else new_wykonanie,
                message="Zdarzenie dopisane do WYKONANIE.",
            )
        except Exception:
            if hasattr(connection, "rollback"):
                connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> Any:
        return self._importer._connect()  # noqa: SLF001

    def _fetch_filter_rows(self, cursor: Any, item: ServiceOrderFilter) -> list[ServiceOrderRow]:
        where: list[str] = []
        params: list[Any] = []
        if item.order_number is not None and item.year is not None:
            where.append("ID_ZLECENIE = ?")
            params.append(item.order_number)
            where.append("ROK = ?")
            params.append(item.year)
        if item.serial:
            where.append("TRIM(COALESCE(SERIAL, '')) = ?")
            params.append(item.serial)
        if item.problem_contains:
            where.append("PROBLEM CONTAINING ?")
            params.append(item.problem_contains)
        if not where:
            raise ValueError(f"Pusty filtr zlecenia: {item.key}")

        cursor.execute(
            f"""
            SELECT
                ID_ZLECENIE_TABLE, ID_ZLECENIE, ROK, STAN, DATA, DATA_Z,
                SERIAL, PROBLEM, WYKONANIE, OPERATOR, TECHNIK, EDITCNT,
                EDITDATE, EDITTIME, EDITSOURCE, ID_KLIENT, ID_MASZYNA,
                ID_FAKTURA, FAKTURA
            FROM ZLECENIE
            WHERE {" AND ".join(where)}
            ORDER BY ROK, ID_ZLECENIE, ID_ZLECENIE_TABLE
            """,
            tuple(params),
        )
        return [_row_from_db(item.key, row) for row in cursor.fetchall()]

    def _process_order_row(
        self,
        cursor: Any,
        row: ServiceOrderRow,
        *,
        execute: bool,
        remote_statuses: dict[str, str],
        repair_text: str,
        preserve_metadata: bool,
    ) -> ServiceOrderActionRow:
        remote_status = remote_statuses.get(row.serial.casefold(), "") if row.serial else ""
        if remote_statuses and remote_status != "not_found":
            return _action_for_row(
                row,
                "skipped_remote_status",
                remote_status=remote_status,
                message="Pominieto: raport Remote nie potwierdza final_status=not_found.",
            )

        if row.stan == "Z":
            return _action_for_row(row, "already_closed", remote_status=remote_status)
        if row.stan not in {"O", "ZR"}:
            return _action_for_row(
                row,
                "skipped_status",
                remote_status=remote_status,
                message=f"Pominieto: nieobslugiwany status zlecenia {row.stan!r}.",
            )

        new_wykonanie = append_repair_text(row.wykonanie, repair_text)
        new_operator = row.operator if preserve_metadata else append_close_operator(row.operator)
        if not execute:
            return _action_for_row(
                row,
                "would_close",
                remote_status=remote_status,
                after_stan="Z",
                after_data_z=row.data_z if preserve_metadata else date.today().isoformat(),
                after_wykonanie=new_wykonanie,
                message="Dry-run: zlecenie kwalifikuje sie do zamkniecia.",
            )

        if preserve_metadata:
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET WYKONANIE = ?, STAN = 'ZR'
                WHERE ID_ZLECENIE_TABLE = ? AND STAN IN ('O', 'ZR')
                """,
                (new_wykonanie, row.id_zlecenie_table),
            )
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ? AND STAN = 'ZR'
                """,
                (row.id_zlecenie_table,),
            )
        else:
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET WYKONANIE = ?, OPERATOR = ?, STAN = 'ZR'
                WHERE ID_ZLECENIE_TABLE = ? AND STAN IN ('O', 'ZR')
                """,
                (new_wykonanie, new_operator, row.id_zlecenie_table),
            )
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET STAN = 'Z', DATA_Z = CURRENT_DATE
                WHERE ID_ZLECENIE_TABLE = ? AND STAN = 'ZR'
                """,
                (row.id_zlecenie_table,),
            )
        after = self._fetch_by_table_id(cursor, row.id_zlecenie_table, row.filter_key)
        return _action_for_row(
            row,
            "closed",
            remote_status=remote_status,
            after_stan=after.stan if after else "Z",
            after_data_z=(
                (after.data_z if after else row.data_z)
                if preserve_metadata
                else (after.data_z if after else date.today().isoformat())
            ),
            after_wykonanie=after.wykonanie if after else new_wykonanie,
            message="Zlecenie zamkniete.",
        )

    def _fetch_by_table_id(
        self, cursor: Any, id_zlecenie_table: int, filter_key: str
    ) -> ServiceOrderRow | None:
        cursor.execute(
            """
            SELECT
                ID_ZLECENIE_TABLE, ID_ZLECENIE, ROK, STAN, DATA, DATA_Z,
                SERIAL, PROBLEM, WYKONANIE, OPERATOR, TECHNIK, EDITCNT,
                EDITDATE, EDITTIME, EDITSOURCE, ID_KLIENT, ID_MASZYNA,
                ID_FAKTURA, FAKTURA
            FROM ZLECENIE
            WHERE ID_ZLECENIE_TABLE = ?
            """,
            (id_zlecenie_table,),
        )
        row = cursor.fetchone()
        return _row_from_db(filter_key, row) if row else None


SERVICE_ORDER_SNAPSHOT_FIELDS = [
    "filter_key",
    "id_zlecenie_table",
    "id_zlecenie",
    "rok",
    "stan",
    "data",
    "data_z",
    "serial",
    "problem",
    "wykonanie",
    "operator",
    "technik",
    "editcnt",
    "editdate",
    "edittime",
    "editsource",
    "id_klient",
    "id_maszyna",
    "id_faktura",
    "faktura",
]

SERVICE_ORDER_ACTION_FIELDS = [
    "filter_key",
    "status",
    "matched_count",
    "id_zlecenie_table",
    "order",
    "serial",
    "remote_final_status",
    "before_stan",
    "after_stan",
    "before_data_z",
    "after_data_z",
    "before_wykonanie",
    "after_wykonanie",
    "message",
]

SERVICE_ORDER_DIFF_FIELDS = [
    "id_zlecenie_table",
    "order",
    "serial",
    "field",
    "before",
    "after",
]


def _load_txt_filters(path: Path) -> list[ServiceOrderFilter]:
    filters: list[ServiceOrderFilter] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.casefold()
        if lower.startswith("serial:"):
            filters.append(ServiceOrderFilter(serial=line.split(":", 1)[1].strip()))
            continue
        if lower.startswith("problem:"):
            filters.append(ServiceOrderFilter(problem_contains=line.split(":", 1)[1].strip()))
            continue
        if lower.startswith("opis:"):
            filters.append(ServiceOrderFilter(problem_contains=line.split(":", 1)[1].strip()))
            continue
        filters.append(_parse_order_filter(line))
    return filters


def _load_csv_filters(path: Path) -> list[ServiceOrderFilter]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        result: list[ServiceOrderFilter] = []
        for row in reader:
            normalized = {
                key.strip().casefold(): (value or "").strip() for key, value in row.items()
            }
            order_text = (
                normalized.get("order")
                or normalized.get("zlecenie")
                or normalized.get("nr_zlecenia")
                or normalized.get("numer_zlecenia")
                or ""
            )
            order_number, year = _parse_order_parts(order_text) if order_text else (None, None)
            result.append(
                ServiceOrderFilter(
                    order_number=order_number,
                    year=year,
                    serial=normalized.get("serial") or normalized.get("nr_seryjny") or "",
                    problem_contains=(
                        normalized.get("problem_contains")
                        or normalized.get("problem")
                        or normalized.get("opis")
                        or normalized.get("opis_usterki")
                        or ""
                    ),
                    allow_multiple=(normalized.get("allow_multiple", "").casefold() in TRUE_VALUES),
                )
            )
        return [item for item in result if item.key != "<empty>"]


def _parse_order_filter(text: str) -> ServiceOrderFilter:
    order_number, year = _parse_order_parts(text)
    return ServiceOrderFilter(order_number=order_number, year=year)


def _parse_order_parts(text: str) -> tuple[int, int]:
    match = ORDER_RE.match(text)
    if match is None:
        raise ValueError(f"Nieprawidlowy filtr zlecenia: {text!r}")
    return int(match.group("number")), int(match.group("year"))


def _deduplicate_filters(filters: list[ServiceOrderFilter]) -> list[ServiceOrderFilter]:
    result: list[ServiceOrderFilter] = []
    seen: set[tuple[int | None, int | None, str, str, bool]] = set()
    for item in filters:
        key = item.normalized_key()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _row_from_db(filter_key: str, row: Any) -> ServiceOrderRow:
    return ServiceOrderRow(
        filter_key=filter_key,
        id_zlecenie_table=int(row[0]),
        id_zlecenie=int(row[1]),
        rok=int(row[2]),
        stan=_to_text(row[3]),
        data=_to_text(row[4]),
        data_z=_to_text(row[5]),
        serial=_to_text(row[6]),
        problem=_to_text(row[7]),
        wykonanie=_to_text(row[8]),
        operator=_to_text(row[9]),
        technik=_to_text(row[10]),
        editcnt=_to_text(row[11]),
        editdate=_to_text(row[12]),
        edittime=_to_text(row[13]),
        editsource=_to_text(row[14]),
        id_klient=_to_text(row[15]),
        id_maszyna=_to_text(row[16]),
        id_faktura=_to_text(row[17]),
        faktura=_to_text(row[18]),
    )


def _action_for_row(
    row: ServiceOrderRow,
    status: str,
    *,
    remote_status: str = "",
    after_stan: str = "",
    after_data_z: str = "",
    after_wykonanie: str = "",
    message: str = "",
) -> ServiceOrderActionRow:
    return ServiceOrderActionRow(
        filter_key=row.filter_key,
        status=status,
        matched_count=1,
        id_zlecenie_table=str(row.id_zlecenie_table),
        order=row.order_label,
        serial=row.serial,
        remote_final_status=remote_status,
        before_stan=row.stan,
        after_stan=after_stan or row.stan,
        before_data_z=row.data_z,
        after_data_z=after_data_z or row.data_z,
        before_wykonanie=row.wykonanie,
        after_wykonanie=after_wykonanie or row.wykonanie,
        message=message,
    )


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value).strip()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _write_dict_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_snapshot_rows(path: Path) -> list[dict[str, str]]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Brak snapshotu zlecen: {source}")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sort_snapshot_id(value: str) -> tuple[int, str]:
    try:
        return (int(value), value)
    except ValueError:
        return (0, value)


def _order_from_snapshot(row: dict[str, str] | None) -> str:
    if not row:
        return ""
    number = row.get("id_zlecenie", "")
    year = row.get("rok", "")
    return f"{number}/{year}" if number and year else ""
