"""Orkiestracja procesu: portal Ricoh -> ZIP -> CSV -> SMB + log."""

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
                    [extracted.dplac_not_obtained_path.name],
                    dplac_no_payload,
                )

                logger.info(f"Zapisano na SMB: {dplac_unc}")
                logger.info(f"Zapisano na SMB: {dplac_no_unc}")

                # Dodatkowy cleanup jest wykonywany automatycznie przez TemporaryDirectory,
                # ale usuwamy ZIP jawnie, aby w logach bylo jednoznacznie.
                if result.zip_path.exists():
                    result.zip_path.unlink()
                    logger.info("Usunieto lokalny plik ZIP po sukcesie.")
                shutil.rmtree(output_dir, ignore_errors=True)

            logger.info("Proces zakonczony sukcesem.")
        return 0


@dataclass(slots=True)
class _SmbLogger:
    """Prosty logger zapisujacy wpisy do dziennego pliku na SMB i stdout."""

    smb: SmbClient
    log_name: str

    def info(self, message: str) -> None:
        print(message)
        self.smb.append_log_line(["log", self.log_name], message)
