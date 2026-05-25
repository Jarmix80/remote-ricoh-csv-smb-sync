from __future__ import annotations

from pathlib import Path

from remote_ricoh.firebird_cmail import (
    FirebirdCmailImporter,
    parse_counter_datetime,
    parse_dplac_row,
    safe_int,
)


class _FakeCursor:
    def __init__(self) -> None:
        self.fetchone_result = None
        self.inserted_rows: list[tuple] = []
        self.generator_value = 9000

    def execute(self, sql: str, params=None) -> None:  # noqa: ANN001
        compact_sql = " ".join(sql.split())

        if "SELECT COUNT(*) FROM CMAIL" in compact_sql:
            self.fetchone_result = (123,)
            return

        if "SELECT COUNT(*) FROM MASZYNA" in compact_sql:
            self.fetchone_result = (456,)
            return

        if "SELECT GEN_ID(ID_CMAIL_GEN, 0)" in compact_sql:
            self.fetchone_result = (789,)
            return

        if "SELECT ID_MASZYNA, ID_KLIENT, ID_UMOWACPC" in compact_sql:
            serial = params[0]
            if serial == "SERIAL-1":
                self.fetchone_result = (11, 22, 33, "Ricoh", "IM C300")
            elif serial == "SERIAL-2":
                self.fetchone_result = (44, 55, None, "Ricoh", "MP 2555")
            else:
                self.fetchone_result = None
            return

        if "COALESCE(TOTAL_MONO, 0)" in compact_sql and "FROM CMAIL" in compact_sql:
            self.fetchone_result = (1,) if params[0] == "SERIAL-2" else None
            return

        if "COALESCE(TOTAL, 0)" in compact_sql and "FROM CMAIL" in compact_sql:
            self.fetchone_result = None
            return

        if "SELECT GEN_ID(ID_CMAIL_GEN, 1)" in compact_sql:
            self.generator_value += 1
            self.fetchone_result = (self.generator_value,)
            return

        if "INSERT INTO CMAIL" in compact_sql:
            assert len(params) == 23
            self.inserted_rows.append(params)
            self.fetchone_result = None
            return

        raise AssertionError(f"Nieobsluzone SQL w tescie: {compact_sql}")

    def fetchone(self):  # noqa: ANN201
        return self.fetchone_result


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.commit_calls = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.closed = True


class _TestImporter(FirebirdCmailImporter):
    def __init__(self, connection: _FakeConnection) -> None:
        super().__init__(
            mode="network",
            host="127.0.0.1",
            port=3050,
            user="SYSDBA",
            password="masterkey",
            database="BAZAMS_TEST",
        )
        self.connection = connection

    def _connect(self) -> _FakeConnection:
        return self.connection


def test_parse_counter_datetime_keeps_time() -> None:
    out = parse_counter_datetime("05/21/2026", "08:15")
    assert out is not None
    assert out.isoformat() == "2026-05-21T08:15:00"


def test_safe_int_accepts_commas() -> None:
    assert safe_int("12,345") == 12345
    assert safe_int("") is None


def test_parse_dplac_row_accepts_appliance_serial_number() -> None:
    row = {
        "Appliance Serial Number": "ALT-123",
        "Acquisition Date (mm/dd/yyyy)": "05/20/2026",
        "Acquisition Time": "06:45",
        "Vendor Name": "Ricoh",
        "Model Name": "IM C300",
        "B&W Total": "100",
        "Color Total": "40",
        "Copier: Color": "15",
        "Printer: Total": "25",
        "Printer: Color": "10",
        "Scan (Input): Total": "7",
    }

    out = parse_dplac_row(row)

    assert out is not None
    assert out.serial == "ALT-123"
    assert out.counter_date is not None
    assert out.counter_date.isoformat() == "2026-05-20T06:45:00"
    assert out.mono == 100
    assert out.color == 40
    assert out.copier_color == 15
    assert out.printer_total == 25
    assert out.printer_color == 10
    assert out.scan_total == 7


def test_import_dplac_inserts_rows_and_skips_duplicates(tmp_path: Path) -> None:
    csv_path = tmp_path / "DPLAC.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Device Serial Number,Acquisition Date (mm/dd/yyyy),Acquisition Time,Vendor Name,Model Name,Total,B&W Total,Color Total,Copier: Total,Copier: B&W,Copier: Color,Printer: Total,Printer: B&W,Printer: Color,Scan (Input): Total",
                "SERIAL-1,05/20/2026,07:30,Ricoh,IM C300,150,100,50,120,80,40,30,20,10,10",
                "SERIAL-2,05/20/2026,09:00,Ricoh,MP 2555,200,200,0,200,200,0,0,0,0,5",
                ",05/20/2026,09:10,Ricoh,Missing,10,10,0,10,10,0,0,0,0,0",
                "SERIAL-3,05/20/2026,,Unknown,,333,,,,,,,,,",
            ]
        )
        + "\n",
        encoding="utf-8-sig",
    )

    connection = _FakeConnection()
    importer = _TestImporter(connection)

    stats = importer.import_dplac(csv_path)

    assert stats.rows == 4
    assert stats.inserted == 2
    assert stats.duplicates == 1
    assert stats.parse_errors == 1
    assert stats.device_matched == 2
    assert stats.device_unmatched == 1
    assert connection.commit_calls == 2
    assert connection.closed is True

    first_insert = connection.cursor_obj.inserted_rows[0]
    assert first_insert[1] == "SERIAL-1"
    assert first_insert[3] == "[impotr] - automate AI Ranonen"
    assert first_insert[5] == "Ricoh IM C300 SERIAL-1"
    assert first_insert[9:12] == (11, 22, 33)
    assert first_insert[16:23] == (120, 80, 40, 30, 20, 10, 10)

    second_insert = connection.cursor_obj.inserted_rows[1]
    assert second_insert[1] == "SERIAL-3"
    assert second_insert[5] == "Unknown SERIAL-3"
    assert second_insert[9:12] == (None, None, None)
    assert second_insert[16:23] == (None, None, None, None, None, None, None)


def test_diagnose_reads_counts_and_generator() -> None:
    connection = _FakeConnection()
    importer = _TestImporter(connection)

    diagnostics = importer.diagnose()

    assert diagnostics.cmail_rows == 123
    assert diagnostics.maszyna_rows == 456
    assert diagnostics.id_cmail_generator == 789
    assert connection.closed is True


def test_connect_falls_back_to_firebirdsql_when_fdb_client_is_missing(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class FakeFirebirdSqlModule:
        @staticmethod
        def connect(**kwargs):  # noqa: ANN003, ANN205
            recorded.update(kwargs)
            return _FakeConnection()

    class FakeFdbModule:
        @staticmethod
        def connect(**kwargs):  # noqa: ANN003, ANN205
            raise Exception("The location of Firebird Client Library could not be determined.")

    monkeypatch.setattr("remote_ricoh.firebird_cmail._load_fdb", lambda: FakeFdbModule())
    monkeypatch.setattr(
        "remote_ricoh.firebird_cmail._load_firebirdsql",
        lambda: FakeFirebirdSqlModule(),
    )

    importer = FirebirdCmailImporter(
        mode="network",
        host="192.168.0.9",
        port=3050,
        user="SYSDBA",
        password="masterkey",
        database="BAZAMS_TEST",
        charset="WIN1250",
        role="MANAGER",
    )

    connection = importer._connect()

    assert isinstance(connection, _FakeConnection)
    assert recorded == {
        "host": "192.168.0.9",
        "port": 3050,
        "database": "BAZAMS_TEST",
        "user": "SYSDBA",
        "password": "masterkey",
        "charset": "WIN1250",
        "role": "MANAGER",
    }


def test_connect_local_mode_resolves_copy_path(monkeypatch, tmp_path: Path) -> None:
    recorded: dict[str, object] = {}
    local_copy = tmp_path / "BAZAMS_TEST.FDB"
    local_copy.write_bytes(b"stub")

    class FakeFirebirdSqlModule:
        @staticmethod
        def connect(**kwargs):  # noqa: ANN003, ANN205
            recorded.update(kwargs)
            return _FakeConnection()

    monkeypatch.setattr(
        "remote_ricoh.firebird_cmail._load_fdb", lambda: (_ for _ in ()).throw(ImportError)
    )
    monkeypatch.setattr(
        "remote_ricoh.firebird_cmail._load_firebirdsql",
        lambda: FakeFirebirdSqlModule(),
    )

    importer = FirebirdCmailImporter(
        mode="local",
        host="127.0.0.1",
        port=3050,
        user="SYSDBA",
        password="masterkey",
        database="IGNORED_ALIAS",
        local_copy_path=str(local_copy),
    )

    connection = importer._connect()

    assert isinstance(connection, _FakeConnection)
    assert recorded["host"] == "127.0.0.1"
    assert recorded["database"] == str(local_copy)
