from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from remote_ricoh.config import Settings
from remote_ricoh.device_delete import DeviceDeleteReportRow
from remote_ricoh.remote_auto import ORDER_STATUS_WAITING_RECENT, RemoteAutoStore
from remote_ricoh.service import Runner
from remote_ricoh.service_orders import (
    ServiceOrderActionRow,
    ServiceOrderDiffRow,
    ServiceOrderRow,
)


def _build_settings() -> Settings:
    return Settings(
        login_ricoh="user",
        pass_ricoh="pass",
        sciezka_remote=r"\\server\share\ricoh",
        user_smb="smbuser",
        pass_smb="smbpass",
        fb_mode="network",
        fb_host="127.0.0.1",
        fb_port=3050,
        fb_user="SYSDBA",
        fb_password="masterkey",
        fb_database="BAZAMS_TEST",
        fb_charset="WIN1250",
        fb_role=None,
        fb_local_copy_path=None,
    )


@dataclass
class _PortalResult:
    requested_id: str
    zip_path: Path


def test_runner_run_imports_firebird_after_smb(monkeypatch, tmp_path: Path) -> None:
    events: list[tuple[str, str]] = []

    class FakeSmbClient:
        def __init__(self, remote_unc: str, username: str, password: str) -> None:
            self.remote_unc = remote_unc
            self.username = username
            self.password = password

        def __enter__(self) -> FakeSmbClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def write_binary(self, path_parts: list[str], payload: bytes) -> str:
            events.append(("smb", path_parts[0]))
            return f"UNC::{path_parts[0]}"

        def append_log_line(self, path_parts: list[str], message: str) -> None:
            return None

    class FakePortalClient:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        def request_and_download_zip(
            self, download_dir: Path, log
        ) -> _PortalResult:  # noqa: ANN001
            zip_path = download_dir / "payload.zip"
            download_dir.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(b"zip")
            return _PortalResult(requested_id="REQ-1", zip_path=zip_path)

    class FakeImporter:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        def import_dplac(self, csv_path: Path):  # noqa: ANN201
            events.append(("firebird", csv_path.name))

            class _Stats:
                def as_log_message(self) -> str:
                    return "Import Firebird CMAIL: rows=1"

            return _Stats()

    def fake_extract(zip_path: Path, output_dir: Path, date_suffix: str):  # noqa: ANN001, ANN202
        output_dir.mkdir(parents=True, exist_ok=True)
        dplac_path = output_dir / "DPLAC_22-05-2026.csv"
        dplac_no_path = output_dir / "DPLAC_Not_obtained_22-05-2026.csv"
        dplac_path.write_bytes(b"dplac")
        dplac_no_path.write_bytes(b"dplac_no")

        class _Extracted:
            def __init__(self) -> None:
                self.dplac_path = dplac_path
                self.dplac_not_obtained_path = dplac_no_path

        return _Extracted()

    monkeypatch.setattr("remote_ricoh.service.SmbClient", FakeSmbClient)
    monkeypatch.setattr("remote_ricoh.service.RicohPortalClient", FakePortalClient)
    monkeypatch.setattr("remote_ricoh.service.FirebirdCmailImporter", FakeImporter)
    monkeypatch.setattr("remote_ricoh.service.extract_meter_csvs", fake_extract)
    monkeypatch.setattr("remote_ricoh.service.today_suffix", lambda: "22-05-2026")
    monkeypatch.setattr(
        "remote_ricoh.service.log_file_name_for_today", lambda: "ricoh_2026-05-22.log"
    )

    code = Runner(_build_settings()).run()

    assert code == 0
    assert events == [
        ("smb", "DPLAC_22-05-2026.csv"),
        ("smb", "DPLAC_Not_obtained_22-05-2026.csv"),
        ("firebird", "DPLAC_22-05-2026.csv"),
    ]


def test_runner_run_ignores_firebird_error_after_smb(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    class FakeSmbClient:
        def __init__(self, remote_unc: str, username: str, password: str) -> None:
            return None

        def __enter__(self) -> FakeSmbClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def write_binary(self, path_parts: list[str], payload: bytes) -> str:
            events.append(("smb", path_parts[0]))
            return f"UNC::{path_parts[0]}"

        def append_log_line(self, path_parts: list[str], message: str) -> None:
            return None

    class FakePortalClient:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def request_and_download_zip(
            self, download_dir: Path, log
        ) -> _PortalResult:  # noqa: ANN001
            zip_path = download_dir / "payload.zip"
            download_dir.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(b"zip")
            return _PortalResult(requested_id="REQ-2", zip_path=zip_path)

    class FakeImporter:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def import_dplac(self, csv_path: Path):  # noqa: ANN201
            events.append(("firebird", csv_path.name))
            raise RuntimeError("boom")

    def fake_extract(zip_path: Path, output_dir: Path, date_suffix: str):  # noqa: ANN001, ANN202
        output_dir.mkdir(parents=True, exist_ok=True)
        dplac_path = output_dir / "DPLAC_22-05-2026.csv"
        dplac_no_path = output_dir / "DPLAC_Not_obtained_22-05-2026.csv"
        dplac_path.write_bytes(b"dplac")
        dplac_no_path.write_bytes(b"dplac_no")

        class _Extracted:
            def __init__(self) -> None:
                self.dplac_path = dplac_path
                self.dplac_not_obtained_path = dplac_no_path

        return _Extracted()

    monkeypatch.setattr("remote_ricoh.service.SmbClient", FakeSmbClient)
    monkeypatch.setattr("remote_ricoh.service.RicohPortalClient", FakePortalClient)
    monkeypatch.setattr("remote_ricoh.service.FirebirdCmailImporter", FakeImporter)
    monkeypatch.setattr("remote_ricoh.service.extract_meter_csvs", fake_extract)
    monkeypatch.setattr("remote_ricoh.service.today_suffix", lambda: "22-05-2026")

    code = Runner(_build_settings()).run()

    assert code == 0
    assert events == [
        ("smb", "DPLAC_22-05-2026.csv"),
        ("smb", "DPLAC_Not_obtained_22-05-2026.csv"),
        ("firebird", "DPLAC_22-05-2026.csv"),
    ]


def test_runner_run_dry_checks_firebird(monkeypatch) -> None:
    class FakeSmbClient:
        def __init__(self, remote_unc: str, username: str, password: str) -> None:
            self.ensure_calls: list[tuple[str, ...]] = []

        def __enter__(self) -> FakeSmbClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def ensure_directory(self, path_parts: list[str] | None = None) -> None:
            self.ensure_calls.append(tuple(path_parts or []))

        def list_directory(self) -> list[str]:
            return ["a", "b"]

        def append_log_line(self, path_parts: list[str], message: str) -> None:
            return None

    class FakeImporter:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def diagnose(self):  # noqa: ANN201
            class _Diagnostics:
                cmail_rows = 10
                maszyna_rows = 20
                id_cmail_generator = 30

            return _Diagnostics()

    monkeypatch.setattr("remote_ricoh.service.SmbClient", FakeSmbClient)
    monkeypatch.setattr("remote_ricoh.service.FirebirdCmailImporter", FakeImporter)

    code = Runner(_build_settings()).run_dry()

    assert code == 0


def test_runner_run_skips_firebird_when_not_configured(monkeypatch, tmp_path: Path) -> None:
    events: list[tuple[str, str]] = []

    class FakeSmbClient:
        def __init__(self, remote_unc: str, username: str, password: str) -> None:
            return None

        def __enter__(self) -> FakeSmbClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def write_binary(self, path_parts: list[str], payload: bytes) -> str:
            events.append(("smb", path_parts[0]))
            return f"UNC::{path_parts[0]}"

        def append_log_line(self, path_parts: list[str], message: str) -> None:
            return None

    class FakePortalClient:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def request_and_download_zip(
            self, download_dir: Path, log
        ) -> _PortalResult:  # noqa: ANN001
            zip_path = download_dir / "payload.zip"
            download_dir.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(b"zip")
            return _PortalResult(requested_id="REQ-3", zip_path=zip_path)

    def fake_extract(zip_path: Path, output_dir: Path, date_suffix: str):  # noqa: ANN001, ANN202
        output_dir.mkdir(parents=True, exist_ok=True)
        dplac_path = output_dir / "DPLAC_22-05-2026.csv"
        dplac_no_path = output_dir / "DPLAC_Not_obtained_22-05-2026.csv"
        dplac_path.write_bytes(b"dplac")
        dplac_no_path.write_bytes(b"dplac_no")

        class _Extracted:
            def __init__(self) -> None:
                self.dplac_path = dplac_path
                self.dplac_not_obtained_path = dplac_no_path

        return _Extracted()

    settings = Settings(
        login_ricoh="user",
        pass_ricoh="pass",
        sciezka_remote=r"\\server\share\ricoh",
        user_smb="smbuser",
        pass_smb="smbpass",
        fb_mode=None,
        fb_host=None,
        fb_port=None,
        fb_user=None,
        fb_password=None,
        fb_database=None,
        fb_charset=None,
        fb_role=None,
        fb_local_copy_path=None,
    )

    monkeypatch.setattr("remote_ricoh.service.SmbClient", FakeSmbClient)
    monkeypatch.setattr("remote_ricoh.service.RicohPortalClient", FakePortalClient)
    monkeypatch.setattr("remote_ricoh.service.extract_meter_csvs", fake_extract)
    monkeypatch.setattr("remote_ricoh.service.today_suffix", lambda: "22-05-2026")

    code = Runner(settings).run()

    assert code == 0
    assert events == [
        ("smb", "DPLAC_22-05-2026.csv"),
        ("smb", "DPLAC_Not_obtained_22-05-2026.csv"),
    ]


def test_runner_run_downloaded_csv_writes_smb_and_imports_firebird(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str]] = []
    dplac_csv = tmp_path / "manual_DPLAC.csv"
    dplac_no_csv = tmp_path / "manual_DPLAC_Not_obtained.csv"
    dplac_csv.write_text("dplac", encoding="utf-8")
    dplac_no_csv.write_text("dplac_no", encoding="utf-8")

    class FakeSmbClient:
        def __init__(self, remote_unc: str, username: str, password: str) -> None:
            return None

        def __enter__(self) -> FakeSmbClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def write_binary(self, path_parts: list[str], payload: bytes) -> str:
            events.append(("smb", path_parts[0]))
            return f"UNC::{path_parts[0]}"

        def append_log_line(self, path_parts: list[str], message: str) -> None:
            return None

    class FakeImporter:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def import_dplac(self, csv_path: Path):  # noqa: ANN201
            events.append(("firebird", csv_path.name))

            class _Stats:
                def as_log_message(self) -> str:
                    return "Import Firebird CMAIL: rows=1"

            return _Stats()

    monkeypatch.setattr("remote_ricoh.service.SmbClient", FakeSmbClient)
    monkeypatch.setattr("remote_ricoh.service.FirebirdCmailImporter", FakeImporter)
    monkeypatch.setattr("remote_ricoh.service.today_suffix", lambda: "22-05-2026")
    monkeypatch.setattr(
        "remote_ricoh.service.log_file_name_for_today", lambda: "ricoh_2026-05-22.log"
    )

    code = Runner(_build_settings()).run_downloaded_csv(dplac_csv, dplac_no_csv)

    assert code == 0
    assert events == [
        ("smb", "DPLAC_22-05-2026.csv"),
        ("smb", "DPLAC_Not_obtained_22-05-2026.csv"),
        ("firebird", "manual_DPLAC.csv"),
    ]


def test_runner_run_delete_devices_writes_report(monkeypatch, tmp_path: Path) -> None:
    serials_file = tmp_path / "serials.txt"
    serials_file.write_text("T575H403598\nABC123\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakePortalClient:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["client_kwargs"] = kwargs

        def delete_devices_by_serials(  # noqa: ANN001
            self,
            serials,
            execute_delete,
            log,
            allow_recent_serials=None,
            allow_recent_before=None,
        ):
            captured["serials"] = serials
            captured["execute_delete"] = execute_delete
            captured["allow_recent_serials"] = allow_recent_serials
            captured["allow_recent_before"] = allow_recent_before
            log("fake delete")
            return [
                DeviceDeleteReportRow(
                    serial=serials[0],
                    status="would_delete",
                    matched_count=1,
                    device_id=serials[0],
                ),
                DeviceDeleteReportRow(
                    serial=serials[1],
                    status="not_found",
                    matched_count=0,
                ),
            ]

    def fake_write_report(rows):  # noqa: ANN001, ANN202
        captured["rows"] = rows
        return tmp_path / "report.csv"

    monkeypatch.setattr("remote_ricoh.service.RicohPortalClient", FakePortalClient)
    monkeypatch.setattr("remote_ricoh.service.write_delete_report", fake_write_report)

    code = Runner(_build_settings()).run_delete_devices(serials_file, execute_delete=False)

    assert code == 0
    assert captured["serials"] == ["T575H403598", "ABC123"]
    assert captured["execute_delete"] is False
    assert captured["allow_recent_serials"] == set()
    assert captured["allow_recent_before"] is None
    assert [row.status for row in captured["rows"]] == ["would_delete", "not_found"]


def test_runner_remote_auto_scan_records_waiting_recent(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "remote_auto.sqlite"
    remote_order = ServiceOrderRow(
        filter_key="remote_auto",
        id_zlecenie_table=70001,
        id_zlecenie=17001,
        rok=2026,
        stan="O",
        serial="ABC123",
        problem="odpiąć REMOTE",
    )

    class FakeServiceOrderClient:
        def fetch_remote_open_orders(self):  # noqa: ANN201
            return [remote_order]

    class FakePortalClient:
        def delete_devices_by_serials(  # noqa: ANN001
            self,
            serials,
            execute_delete,
            log,
            allow_recent_serials=None,
            allow_recent_before=None,
        ):
            assert serials == ["ABC123"]
            assert execute_delete is False
            assert allow_recent_serials == {"ABC123"}
            assert allow_recent_before is not None
            return [
                DeviceDeleteReportRow(
                    serial="ABC123",
                    status="skipped_recent_report",
                    matched_count=1,
                    last_report_time="2026/07/01 08:00",
                    message="swiezy odczyt",
                )
            ]

    monkeypatch.setattr(
        Runner, "_build_service_order_client", lambda self: FakeServiceOrderClient()
    )
    monkeypatch.setattr(Runner, "_build_portal_client", lambda self: FakePortalClient())
    monkeypatch.setattr(
        "remote_ricoh.service.write_delete_report", lambda rows: tmp_path / "remote.csv"
    )
    monkeypatch.setattr(
        "remote_ricoh.service.write_remote_auto_csv_report",
        lambda rows, mode: tmp_path / f"{mode}.csv",
    )

    code = Runner(_build_settings()).run_remote_auto_scan(db_path, execute=False)

    assert code == 0
    dashboard = RemoteAutoStore(db_path).dashboard()
    assert dashboard["counts"] == {ORDER_STATUS_WAITING_RECENT: 1}
    assert dashboard["orders"][0]["last_report_time"] == "2026/07/01 08:00"


def test_runner_service_order_snapshot_writes_report(monkeypatch, tmp_path: Path) -> None:
    filters_file = tmp_path / "orders.txt"
    filters_file.write_text("14331/2025\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeClient:
        def snapshot(self, filters):  # noqa: ANN001, ANN201
            captured["filters"] = filters
            return [
                ServiceOrderRow(
                    filter_key=filters[0].key,
                    id_zlecenie_table=79331,
                    id_zlecenie=14331,
                    rok=2025,
                    stan="O",
                )
            ]

    def fake_build_client(self):  # noqa: ANN001, ANN202
        return FakeClient()

    def fake_write_snapshot(rows):  # noqa: ANN001, ANN202
        captured["rows"] = rows
        return tmp_path / "snapshot.csv"

    monkeypatch.setattr(Runner, "_build_service_order_client", fake_build_client)
    monkeypatch.setattr("remote_ricoh.service.write_service_order_snapshot", fake_write_snapshot)

    code = Runner(_build_settings()).run_service_order_snapshot(filters_file)

    assert code == 0
    assert [item.key for item in captured["filters"]] == ["14331/2025"]
    assert [row.order_label for row in captured["rows"]] == ["14331/2025"]


def test_runner_close_service_orders_passes_remote_statuses(monkeypatch, tmp_path: Path) -> None:
    filters_file = tmp_path / "orders.txt"
    filters_file.write_text("14331/2025\n", encoding="utf-8")
    remote_report = tmp_path / "remote.csv"
    remote_report.write_text("serial,final_status\nG696M313134,not_found\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeClient:
        def close_orders(self, filters, *, execute, remote_statuses):  # noqa: ANN001, ANN201
            captured["filters"] = filters
            captured["execute"] = execute
            captured["remote_statuses"] = remote_statuses
            return [
                ServiceOrderActionRow(
                    filter_key=filters[0].key,
                    status="would_close",
                    matched_count=1,
                    order="14331/2025",
                    serial="G696M313134",
                )
            ]

    def fake_build_client(self):  # noqa: ANN001, ANN202
        return FakeClient()

    def fake_write_report(rows):  # noqa: ANN001, ANN202
        captured["rows"] = rows
        return tmp_path / "close_report.csv"

    monkeypatch.setattr(Runner, "_build_service_order_client", fake_build_client)
    monkeypatch.setattr(
        "remote_ricoh.service.write_service_order_action_report",
        fake_write_report,
    )

    code = Runner(_build_settings()).run_close_service_orders(
        filters_file,
        execute_service_orders=False,
        remote_status_report=remote_report,
    )

    assert code == 0
    assert captured["execute"] is False
    assert captured["remote_statuses"] == {"g696m313134": "not_found"}
    assert [row.status for row in captured["rows"]] == ["would_close"]


def test_runner_service_order_diff_writes_report(monkeypatch, tmp_path: Path) -> None:
    before = tmp_path / "before.csv"
    after = tmp_path / "after.csv"
    before.write_text("x", encoding="utf-8")
    after.write_text("x", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_diff(before_path: Path, after_path: Path):  # noqa: ANN202
        captured["before_path"] = before_path
        captured["after_path"] = after_path
        return [
            ServiceOrderDiffRow(
                id_zlecenie_table="79331",
                order="14331/2025",
                serial="G696M313134",
                field="stan",
                before="O",
                after="Z",
            )
        ]

    def fake_write_diff(rows):  # noqa: ANN001, ANN202
        captured["rows"] = rows
        return tmp_path / "diff.csv"

    monkeypatch.setattr("remote_ricoh.service.diff_service_order_snapshots", fake_diff)
    monkeypatch.setattr("remote_ricoh.service.write_service_order_diff", fake_write_diff)

    code = Runner(_build_settings()).run_service_order_diff(before, after)

    assert code == 0
    assert captured["before_path"] == before
    assert captured["after_path"] == after
    assert [row.field for row in captured["rows"]] == ["stan"]
