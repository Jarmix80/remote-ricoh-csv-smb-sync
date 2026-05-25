"""Import licznikow z DPLAC CSV do tabeli Firebird CMAIL."""

from __future__ import annotations

import csv
import datetime as dt
import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CSV_DATE_FMT = "%m/%d/%Y"


@dataclass(frozen=True, slots=True)
class DeviceMatch:
    """Powiazanie wpisu CSV z urzadzeniem w tabeli MASZYNA."""

    id_maszyna: int
    id_klient: int
    id_umowacpc: int | None
    brand: str
    model: str


@dataclass(frozen=True, slots=True)
class CounterRecord:
    """Uproszczony rekord licznika odczytany z DPLAC CSV."""

    serial: str
    counter_date: dt.datetime | None
    brand: str
    model: str
    total: int | None
    mono: int | None
    color: int | None
    copier_total: int | None
    copier_mono: int | None
    copier_color: int | None
    printer_total: int | None
    printer_mono: int | None
    printer_color: int | None
    scan_total: int | None


@dataclass(frozen=True, slots=True)
class FirebirdDiagnostics:
    """Wynik prostego testu dostepu do Firebirda."""

    cmail_rows: int
    maszyna_rows: int
    id_cmail_generator: int


@dataclass(slots=True)
class CmailImportStats:
    """Statystyki importu CSV do CMAIL."""

    rows: int = 0
    inserted: int = 0
    duplicates: int = 0
    parse_errors: int = 0
    device_matched: int = 0
    device_unmatched: int = 0

    def as_log_message(self) -> str:
        """Zwarta forma statystyk do logu procesu."""
        return (
            "Import Firebird CMAIL: "
            f"rows={self.rows}, inserted={self.inserted}, duplicates={self.duplicates}, "
            f"parse_errors={self.parse_errors}, device_matched={self.device_matched}, "
            f"device_unmatched={self.device_unmatched}"
        )


def parse_counter_datetime(date_text: str, time_text: str | None) -> dt.datetime | None:
    """Konwertuje pola daty/czasu z DPLAC do datetime."""
    if not date_text.strip():
        return None

    try:
        parsed = dt.datetime.strptime(date_text.strip(), CSV_DATE_FMT)
    except ValueError:
        return None

    if not time_text:
        return parsed

    parts = [part.strip() for part in time_text.strip().split(":")]
    if len(parts) < 2:
        return parsed

    try:
        return parsed.replace(hour=int(parts[0]), minute=int(parts[1]))
    except ValueError:
        return parsed


def safe_int(value: Any) -> int | None:
    """Konwertuje wartosc licznika do int lub zwraca None."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def parse_dplac_row(row: Mapping[str, str]) -> CounterRecord | None:
    """Buduje rekord importowy z pojedynczego wiersza DPLAC."""
    serial = (row.get("Device Serial Number") or row.get("Appliance Serial Number") or "").strip()
    if not serial:
        return None

    return CounterRecord(
        serial=serial,
        counter_date=parse_counter_datetime(
            row.get("Acquisition Date (mm/dd/yyyy)", ""),
            row.get("Acquisition Time"),
        ),
        brand=(row.get("Vendor Name") or "").strip(),
        model=(row.get("Model Name") or "").strip(),
        total=safe_int(row.get("Total")),
        mono=safe_int(row.get("B&W Total")),
        color=safe_int(row.get("Color Total")),
        copier_total=safe_int(row.get("Copier: Total")),
        copier_mono=safe_int(row.get("Copier: B&W")),
        copier_color=safe_int(row.get("Copier: Color")),
        printer_total=safe_int(row.get("Printer: Total")),
        printer_mono=safe_int(row.get("Printer: B&W")),
        printer_color=safe_int(row.get("Printer: Color")),
        scan_total=safe_int(row.get("Scan (Input): Total")),
    )


class FirebirdCmailImporter:
    """Importer zapisujacy rekordy DPLAC do Firebird CMAIL."""

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
    ) -> None:
        self.mode = mode
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        self.role = role
        self.local_copy_path = local_copy_path

    def diagnose(self) -> FirebirdDiagnostics:
        """Sprawdza podstawowe odczyty wymagane do importu."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cmail_rows = self._fetch_scalar(cursor, "SELECT COUNT(*) FROM CMAIL")
            maszyna_rows = self._fetch_scalar(cursor, "SELECT COUNT(*) FROM MASZYNA")
            generator_value = self._fetch_scalar(
                cursor,
                "SELECT GEN_ID(ID_CMAIL_GEN, 0) FROM RDB$DATABASE",
            )
            return FirebirdDiagnostics(
                cmail_rows=cmail_rows,
                maszyna_rows=maszyna_rows,
                id_cmail_generator=generator_value,
            )
        finally:
            connection.close()

    def import_dplac(self, csv_path: Path) -> CmailImportStats:
        """Importuje rekordy z pliku DPLAC CSV do CMAIL."""
        connection = self._connect()
        stats = CmailImportStats()

        try:
            cursor = connection.cursor()
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    stats.rows += 1
                    record = parse_dplac_row(row)
                    if record is None:
                        stats.parse_errors += 1
                        continue

                    device = self._fetch_device(cursor, record.serial)
                    if device is None:
                        stats.device_unmatched += 1
                    else:
                        stats.device_matched += 1

                    if self._has_duplicate(cursor, record):
                        stats.duplicates += 1
                        continue

                    self._insert_cmail(cursor, record, device)
                    connection.commit()
                    stats.inserted += 1
        finally:
            connection.close()

        return stats

    def _connect(self) -> Any:
        host, database = self._resolve_target()
        fdb_error: str | None = None

        try:
            return self._connect_with_fdb(host=host, database=database)
        except ImportError as exc:
            fdb_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            fdb_error = str(exc)
            if not _should_try_firebirdsql(fdb_error):
                raise RuntimeError(f"Blad logowania do Firebird: {exc}") from exc

        try:
            return self._connect_with_firebirdsql(host=host, database=database)
        except ImportError as exc:
            if fdb_error is not None:
                raise RuntimeError(
                    "Brak zaleznosci wymaganej do fallbacku firebirdsql po bledzie fdb: "
                    f"{exc}. Pierwotny blad fdb: {fdb_error}"
                ) from exc
            raise
        except Exception as exc:  # noqa: BLE001
            if fdb_error is not None:
                raise RuntimeError(f"Blad fdb: {fdb_error}; fallback firebirdsql: {exc}") from exc
            raise RuntimeError(f"Blad logowania do Firebird: {exc}") from exc

    def _resolve_target(self) -> tuple[str, str]:
        if self.mode == "network":
            return self.host, self.database

        target_path = (self.local_copy_path or self.database).strip()
        if not target_path:
            raise RuntimeError("Brak lokalnej sciezki Firebird dla FB_MODE=local.")

        resolved_path = Path(target_path).expanduser()
        if not resolved_path.is_absolute():
            resolved_path = (Path.cwd() / resolved_path).resolve()
        if not resolved_path.exists():
            raise RuntimeError(f"Brak lokalnej kopii Firebird: {resolved_path}")
        return self.host or "127.0.0.1", str(resolved_path)

    def _connect_with_fdb(self, *, host: str, database: str) -> Any:
        fdb = _load_fdb()
        kwargs = {
            "dsn": f"{host}/{self.port}:{database}",
            "user": self.user,
            "password": self.password,
            "charset": self.charset,
        }
        if self.role:
            kwargs["role"] = self.role
        return fdb.connect(**kwargs)

    def _connect_with_firebirdsql(self, *, host: str, database: str) -> Any:
        firebirdsql = _load_firebirdsql()
        kwargs = {
            "host": host,
            "port": self.port,
            "database": database,
            "user": self.user,
            "password": self.password,
            "charset": self.charset,
        }
        if self.role:
            kwargs["role"] = self.role
        return firebirdsql.connect(**kwargs)

    @staticmethod
    def _fetch_scalar(cursor: Any, sql: str) -> int:
        cursor.execute(sql)
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    @staticmethod
    def _fetch_device(cursor: Any, serial: str) -> DeviceMatch | None:
        cursor.execute(
            """
            SELECT ID_MASZYNA, ID_KLIENT, ID_UMOWACPC, TRIM(COALESCE(MARKA, '')), TRIM(COALESCE(MODEL, ''))
            FROM MASZYNA
            WHERE TRIM(SERIAL) = ? OR TRIM(COALESCE(SERIAL2, '')) = ?
            ROWS 1
            """,
            (serial, serial),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return DeviceMatch(
            id_maszyna=int(row[0]),
            id_klient=int(row[1]),
            id_umowacpc=int(row[2]) if row[2] is not None else None,
            brand=row[3] or "",
            model=row[4] or "",
        )

    @staticmethod
    def _has_duplicate(cursor: Any, record: CounterRecord) -> bool:
        counter_date = record.counter_date.date() if record.counter_date else None
        if record.mono is not None or record.color is not None:
            cursor.execute(
                """
                SELECT 1
                FROM CMAIL
                WHERE TRIM(COALESCE(SERIAL, '')) = ?
                  AND (COUNTER_DATE = ? OR COUNTER_DATE IS NULL)
                  AND COALESCE(TOTAL_MONO, 0) = COALESCE(?, 0)
                  AND COALESCE(TOTAL_COLOR, 0) = COALESCE(?, 0)
                ROWS 1
                """,
                (record.serial, counter_date, record.mono, record.color),
            )
        else:
            cursor.execute(
                """
                SELECT 1
                FROM CMAIL
                WHERE TRIM(COALESCE(SERIAL, '')) = ?
                  AND (COUNTER_DATE = ? OR COUNTER_DATE IS NULL)
                  AND COALESCE(TOTAL, 0) = COALESCE(?, 0)
                ROWS 1
                """,
                (record.serial, counter_date, record.total),
            )
        return cursor.fetchone() is not None

    @staticmethod
    def _next_cmail_id(cursor: Any) -> int:
        try:
            cursor.execute("SELECT GEN_ID(ID_CMAIL_GEN, 1) FROM RDB$DATABASE")
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except Exception:  # noqa: BLE001
            pass

        cursor.execute("SELECT COALESCE(MAX(ID_CMAIL), 0) + 1 FROM CMAIL")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 1

    @classmethod
    def _insert_cmail(cls, cursor: Any, record: CounterRecord, device: DeviceMatch | None) -> None:
        brand = record.brand or (device.brand if device else "")
        model = record.model or (device.model if device else "")
        subject = " ".join(part for part in (brand, model, record.serial) if part).strip()
        comment_value = f"{dt.date.today().isoformat()}_remote_automate"
        counter_date = record.counter_date.date() if record.counter_date else None
        counter_date_string = record.counter_date.isoformat() if record.counter_date else ""

        cursor.execute(
            """
            INSERT INTO CMAIL (
                ID_CMAIL, SERIAL, COUNTER_DATE, MAILFROM, MAILTO, SUBJECT,
                TOTAL, TOTAL_MONO, TOTAL_COLOR, ID_DEVICE, ID_CUSTOMER, ID_CPC, MAILREAD, DATEIN, COMMENTS,
                DEVICE_BRAND, MODEL_NAME, COUNTER_DATE_STRING,
                COPIER_TOTAL, COPIER_MONO, COPIER_COLOR, PRINTER_TOTAL, PRINTER_MONO, PRINTER_COLOR,
                SCANNER_TOTAL
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                cls._next_cmail_id(cursor),
                record.serial,
                counter_date,
                "[impotr] - automate AI Ranonen",
                "",
                subject,
                record.total,
                record.mono,
                record.color,
                device.id_maszyna if device else None,
                device.id_klient if device else None,
                device.id_umowacpc if device else None,
                comment_value,
                brand,
                model,
                counter_date_string,
                record.copier_total,
                record.copier_mono,
                record.copier_color,
                record.printer_total,
                record.printer_mono,
                record.printer_color,
                record.scan_total,
            ),
        )


def _load_fdb() -> Any:
    return importlib.import_module("fdb")


def _load_firebirdsql() -> Any:
    return importlib.import_module("firebirdsql")


def _should_try_firebirdsql(error_message: str) -> bool:
    normalized = error_message.lower()
    return (
        "client library" in normalized
        or "fbclient" in normalized
        or "gds32" in normalized
        or "could not be determined" in normalized
    )
