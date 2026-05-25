from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from remote_ricoh.config import Settings
from remote_ricoh.service import Runner


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
