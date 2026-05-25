"""Orkiestracja procesu: portal Ricoh -> ZIP -> CSV -> SMB -> Firebird + log."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import (
    POLL_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    Settings,
    log_file_name_for_today,
    today_suffix,
)
from .firebird_cmail import FirebirdCmailImporter
from .portal import RicohPortalClient
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
