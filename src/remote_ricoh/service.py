"""Orkiestracja procesu: portal Ricoh -> ZIP -> CSV -> SMB -> Firebird + log."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import (
    POLL_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    Settings,
    log_file_name_for_today,
    today_suffix,
)
from .device_delete import DeviceDeleteReportRow, load_delete_serials, write_delete_report
from .firebird_cmail import FirebirdCmailImporter
from .portal import RicohPortalClient
from .service_orders import (
    FirebirdServiceOrderClient,
    ServiceOrderActionRow,
    ServiceOrderDiffRow,
    ServiceOrderRow,
    diff_service_order_snapshots,
    load_remote_final_statuses,
    load_service_order_filters,
    write_service_order_action_report,
    write_service_order_diff,
    write_service_order_snapshot,
)
from .smb_io import SmbClient
from .zip_processing import extract_meter_csvs


@dataclass(slots=True)
class Runner:
    """Wykonuje kompletne zadanie pobrania i publikacji plikow CSV."""

    settings: Settings

    def run(self) -> int:
        """Zwraca kod wyjscia procesu (0 sukces, >0 blad)."""
        log_name = log_file_name_for_today()

        with SmbClient(
            remote_unc=self.settings.sciezka_remote,
            username=self.settings.user_smb,
            password=self.settings.pass_smb,
        ) as smb:
            logger = _SmbLogger(smb=smb, log_name=log_name)
            logger.info("Start procesu pobierania CSV Ricoh.")
            self._log_firebird_warning(logger)

            with tempfile.TemporaryDirectory(prefix="remote_ricoh_") as tmp_dir:
                tmp_path = Path(tmp_dir)
                download_dir = tmp_path / "download"
                output_dir = tmp_path / "output"

                client = RicohPortalClient(
                    login=self.settings.login_ricoh,
                    password=self.settings.pass_ricoh,
                    poll_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
                    poll_interval_seconds=POLL_INTERVAL_SECONDS,
                    headless=True,
                )

                result = client.request_and_download_zip(download_dir, logger.info)
                logger.info(f"Pobrano archiwum ZIP dla Requested ID: {result.requested_id}")

                extracted = extract_meter_csvs(result.zip_path, output_dir, today_suffix())
                logger.info("Rozpakowano i przygotowano pliki CSV.")

                dplac_payload = extracted.dplac_path.read_bytes()
                dplac_no_payload = extracted.dplac_not_obtained_path.read_bytes()

                dplac_unc = smb.write_binary([extracted.dplac_path.name], dplac_payload)
                dplac_no_unc = smb.write_binary(
                    [extracted.dplac_not_obtained_path.name], dplac_no_payload
                )
                self._log_smb_writes(logger, dplac_unc, dplac_no_unc)
                self._run_firebird_import(logger, extracted.dplac_path)

                # Dodatkowy cleanup jest wykonywany automatycznie przez TemporaryDirectory,
                # ale usuwamy ZIP jawnie, aby w logach bylo jednoznacznie.
                if result.zip_path.exists():
                    result.zip_path.unlink()
                    logger.info("Usunieto lokalny plik ZIP po sukcesie.")
                shutil.rmtree(output_dir, ignore_errors=True)

            logger.info("Proces zakonczony sukcesem.")
        return 0

    def run_downloaded_csv(
        self,
        dplac_csv: Path,
        dplac_not_obtained_csv: Path | None = None,
    ) -> int:
        """Wykonuje etap SMB + Firebird dla juz pobranego pliku CSV."""
        log_name = log_file_name_for_today()
        dplac_csv = dplac_csv.expanduser().resolve()
        dplac_not_obtained_csv = (
            dplac_not_obtained_csv.expanduser().resolve() if dplac_not_obtained_csv else None
        )

        if not dplac_csv.is_file():
            raise FileNotFoundError(f"Brak pliku DPLAC CSV: {dplac_csv}")
        if dplac_not_obtained_csv is not None and not dplac_not_obtained_csv.is_file():
            raise FileNotFoundError(f"Brak pliku DPLAC_Not_obtained CSV: {dplac_not_obtained_csv}")

        with SmbClient(
            remote_unc=self.settings.sciezka_remote,
            username=self.settings.user_smb,
            password=self.settings.pass_smb,
        ) as smb:
            logger = _SmbLogger(smb=smb, log_name=log_name)
            logger.info(f"Start trybu post-download dla CSV: {dplac_csv}")
            self._log_firebird_warning(logger)

            dplac_remote_name = f"DPLAC_{today_suffix()}.csv"
            dplac_unc = smb.write_binary([dplac_remote_name], dplac_csv.read_bytes())
            dplac_no_unc: str | None = None

            if dplac_not_obtained_csv is not None:
                dplac_no_remote_name = f"DPLAC_Not_obtained_{today_suffix()}.csv"
                dplac_no_unc = smb.write_binary(
                    [dplac_no_remote_name],
                    dplac_not_obtained_csv.read_bytes(),
                )

            self._log_smb_writes(logger, dplac_unc, dplac_no_unc)
            self._run_firebird_import(logger, dplac_csv)
            logger.info("Tryb post-download zakonczony sukcesem.")
        return 0

    def run_dry(self) -> int:
        """Wykonuje diagnostyke SMB i Firebirda bez laczenia z portalem Ricoh."""
        log_name = log_file_name_for_today()

        with SmbClient(
            remote_unc=self.settings.sciezka_remote,
            username=self.settings.user_smb,
            password=self.settings.pass_smb,
        ) as smb:
            logger = _SmbLogger(smb=smb, log_name=log_name)
            smb.ensure_directory()
            smb.ensure_directory(["log"])
            logger.info("DRY-RUN: start diagnostyki SMB.")
            self._log_firebird_warning(logger)
            entries = smb.list_directory()
            logger.info(f"DRY-RUN: katalog docelowy dostepny, wpisow: {len(entries)}.")
            self._run_firebird_diagnostics(logger)
            logger.info("DRY-RUN: zakonczono sukcesem.")
        return 0

    def run_delete_devices(
        self,
        serials_path: Path,
        execute_delete: bool,
        allow_recent_delete_serials_path: Path | None = None,
        allow_recent_delete_before: datetime | None = None,
    ) -> int:
        """Wyszukuje i opcjonalnie usuwa urzadzenia Ricoh z listy numerow seryjnych."""
        serials = load_delete_serials(serials_path)
        if not serials:
            raise ValueError(f"Brak numerow seryjnych w pliku: {serials_path}")
        allow_recent_serials = (
            set(load_delete_serials(allow_recent_delete_serials_path))
            if allow_recent_delete_serials_path is not None
            else set()
        )

        logger = _ConsoleLogger()
        mode = "EXECUTE" if execute_delete else "DRY-RUN"
        logger.info(f"Start trybu usuwania urzadzen Ricoh: tryb={mode}, seriale={len(serials)}.")
        if allow_recent_serials:
            logger.info(
                "Jawne obejscie zabezpieczenia Last Report Date/Time: "
                f"seriale={len(allow_recent_serials)}."
            )
            if allow_recent_delete_before is not None:
                logger.info(
                    "Jawne obejscie ograniczone do Last Report Date/Time < "
                    f"{allow_recent_delete_before:%Y/%m/%d %H:%M}."
                )

        client = RicohPortalClient(
            login=self.settings.login_ricoh,
            password=self.settings.pass_ricoh,
            poll_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            poll_interval_seconds=POLL_INTERVAL_SECONDS,
            headless=True,
        )
        rows = client.delete_devices_by_serials(
            serials,
            execute_delete,
            logger.info,
            allow_recent_serials=allow_recent_serials,
            allow_recent_before=allow_recent_delete_before,
        )
        report_path = write_delete_report(rows)
        logger.info(f"Raport usuwania urzadzen Ricoh: {report_path.resolve()}")
        logger.info(_summarize_delete_report(rows))
        return 0

    def run_service_order_snapshot(self, filters_path: Path) -> int:
        """Zapisuje snapshot zlecen serwisowych pasujacych do filtrow."""
        filters = load_service_order_filters(filters_path)
        if not filters:
            raise ValueError(f"Brak filtrow zlecen w pliku: {filters_path}")

        logger = _ConsoleLogger()
        logger.info(f"Start snapshotu zlecen serwisowych: filtry={len(filters)}.")
        client = self._build_service_order_client()
        rows = client.snapshot(filters)
        report_path = write_service_order_snapshot(rows)
        logger.info(f"Snapshot zlecen serwisowych: {report_path.resolve()}")
        logger.info(_summarize_service_order_snapshot(rows))
        return 0

    def run_service_order_diff(self, before_path: Path, after_path: Path) -> int:
        """Porownuje dwa snapshoty zlecen serwisowych."""
        logger = _ConsoleLogger()
        rows = diff_service_order_snapshots(before_path, after_path)
        report_path = write_service_order_diff(rows)
        logger.info(f"Diff snapshotow zlecen serwisowych: {report_path.resolve()}")
        logger.info(_summarize_service_order_diff(rows))
        return 0

    def run_close_service_orders(
        self,
        filters_path: Path,
        execute_service_orders: bool,
        remote_status_report: Path | None = None,
    ) -> int:
        """Dopisuje wykonanie i opcjonalnie zamyka zlecenia serwisowe."""
        filters = load_service_order_filters(filters_path)
        if not filters:
            raise ValueError(f"Brak filtrow zlecen w pliku: {filters_path}")

        remote_statuses = load_remote_final_statuses(remote_status_report)
        logger = _ConsoleLogger()
        mode = "EXECUTE" if execute_service_orders else "DRY-RUN"
        logger.info(
            "Start zamykania zlecen serwisowych: "
            f"tryb={mode}, filtry={len(filters)}, remote_statuses={len(remote_statuses)}."
        )

        client = self._build_service_order_client()
        rows = client.close_orders(
            filters,
            execute=execute_service_orders,
            remote_statuses=remote_statuses,
        )
        report_path = write_service_order_action_report(rows)
        logger.info(f"Raport zamykania zlecen serwisowych: {report_path.resolve()}")
        logger.info(_summarize_service_order_actions(rows))
        return 0

    def _build_firebird_importer(self) -> FirebirdCmailImporter | None:
        if not self.settings.firebird_enabled:
            return None
        return FirebirdCmailImporter(
            mode=self.settings.fb_mode or "network",
            host=self.settings.fb_host or "",
            port=self.settings.fb_port or 3050,
            user=self.settings.fb_user or "",
            password=self.settings.fb_password or "",
            database=self.settings.fb_database or "",
            charset=self.settings.fb_charset or "WIN1250",
            role=self.settings.fb_role,
            local_copy_path=self.settings.fb_local_copy_path,
        )

    def _build_service_order_client(self) -> FirebirdServiceOrderClient:
        if not self.settings.firebird_enabled:
            raise ValueError("Brak aktywnej konfiguracji Firebird dla zlecen serwisowych.")
        return FirebirdServiceOrderClient(
            mode=self.settings.fb_mode or "network",
            host=self.settings.fb_host or "",
            port=self.settings.fb_port or 3050,
            user=self.settings.fb_user or "",
            password=self.settings.fb_password or "",
            database=self.settings.fb_database or "",
            charset=self.settings.fb_charset or "WIN1250",
            role=self.settings.fb_role,
            local_copy_path=self.settings.fb_local_copy_path,
        )

    def _run_firebird_import(self, logger: _SmbLogger, dplac_csv: Path) -> None:
        importer = self._build_firebird_importer()
        if importer is None:
            logger.info("Import Firebird CMAIL pominiety: brak aktywnej konfiguracji FB.")
            return

        logger.info("Start importu DPLAC do Firebird CMAIL.")
        try:
            stats = importer.import_dplac(dplac_csv)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "OSTRZEZENIE: import Firebird CMAIL nie powiodl sie, ale zapis CSV na SMB "
                f"zostal wykonany. {type(exc).__name__}: {exc}"
            )
            return
        logger.info(stats.as_log_message())

    def _run_firebird_diagnostics(self, logger: _SmbLogger) -> None:
        importer = self._build_firebird_importer()
        if importer is None:
            logger.info("DRY-RUN: Firebird pominiety, brak aktywnej konfiguracji FB.")
            return

        try:
            diagnostics = importer.diagnose()
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "DRY-RUN: Firebird niedostepny, ale SMB jest sprawne. "
                f"{type(exc).__name__}: {exc}"
            )
            return
        logger.info(
            "DRY-RUN: Firebird OK, "
            f"CMAIL={diagnostics.cmail_rows}, "
            f"MASZYNA={diagnostics.maszyna_rows}, "
            f"ID_CMAIL_GEN={diagnostics.id_cmail_generator}."
        )

    @staticmethod
    def _log_smb_writes(logger: _SmbLogger, dplac_unc: str, dplac_no_unc: str | None) -> None:
        logger.info(f"Zapisano na SMB: {dplac_unc}")
        if dplac_no_unc is not None:
            logger.info(f"Zapisano na SMB: {dplac_no_unc}")

    def _log_firebird_warning(self, logger: _SmbLogger) -> None:
        if self.settings.firebird_warning:
            logger.info(f"OSTRZEZENIE: {self.settings.firebird_warning}")


@dataclass(slots=True)
class _SmbLogger:
    """Prosty logger zapisujacy wpisy do dziennego pliku na SMB i stdout."""

    smb: SmbClient
    log_name: str

    def info(self, message: str) -> None:
        print(message)
        self.smb.append_log_line(["log", self.log_name], message)


@dataclass(slots=True)
class _ConsoleLogger:
    """Logger tylko na stdout dla trybow bez SMB."""

    def info(self, message: str) -> None:
        print(message)


def _summarize_delete_report(rows: list[DeviceDeleteReportRow]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    parts = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    return f"Podsumowanie usuwania urzadzen Ricoh: {parts or 'brak wynikow'}."


def _summarize_service_order_snapshot(rows: list[ServiceOrderRow]) -> str:
    return f"Podsumowanie snapshotu zlecen serwisowych: rows={len(rows)}."


def _summarize_service_order_actions(rows: list[ServiceOrderActionRow]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    parts = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    return f"Podsumowanie zamykania zlecen serwisowych: {parts or 'brak wynikow'}."


def _summarize_service_order_diff(rows: list[ServiceOrderDiffRow]) -> str:
    return f"Podsumowanie diffu zlecen serwisowych: changes={len(rows)}."
