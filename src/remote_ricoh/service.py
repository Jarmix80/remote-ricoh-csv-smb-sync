"""Orkiestracja procesu: portal Ricoh -> ZIP -> CSV -> SMB -> Firebird + log."""

from __future__ import annotations

import shutil
import tempfile
from collections import Counter, defaultdict
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
from .remote_auto import (
    DEFAULT_REMOTE_AUTO_DB,
    ORDER_STATUS_CLOSED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_REMOTE_NOT_FOUND,
    ORDER_STATUS_SKIPPED,
    ORDER_STATUS_WAITING_RECENT,
    REMOTE_AUTO_FRESHNESS_MONTHS,
    RemoteAutoRunResult,
    RemoteAutoStore,
    assert_remote_auto_execute_allowed,
    decide_remote_auto_status,
    format_order_event_note,
    serve_remote_auto_panel,
    subtract_months,
    write_remote_auto_csv_report,
)
from .service_orders import (
    FirebirdServiceOrderClient,
    ServiceOrderActionRow,
    ServiceOrderDiffRow,
    ServiceOrderFilter,
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
        *,
        repair_text: str | None = None,
        preserve_metadata: bool = False,
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
        close_options = {}
        if repair_text is not None:
            close_options["repair_text"] = repair_text
        if preserve_metadata:
            close_options["preserve_metadata"] = True
        rows = client.close_orders(
            filters,
            execute=execute_service_orders,
            remote_statuses=remote_statuses,
            **close_options,
        )
        report_path = write_service_order_action_report(rows)
        logger.info(f"Raport zamykania zlecen serwisowych: {report_path.resolve()}")
        logger.info(_summarize_service_order_actions(rows))
        return 0

    def run_remote_auto_scan(
        self,
        db_path: Path = DEFAULT_REMOTE_AUTO_DB,
        *,
        execute: bool = False,
    ) -> int:
        """Wykonuje codzienny cykl obslugi nowych zlecen REMOTE."""
        result = self._run_remote_auto(mode="scan", db_path=db_path, execute=execute)
        print(result.as_log_message())
        return 0

    def run_remote_auto_weekly(
        self,
        db_path: Path = DEFAULT_REMOTE_AUTO_DB,
        *,
        execute: bool = False,
    ) -> int:
        """Wykonuje tygodniowy cykl kolejki oczekujacej REMOTE."""
        result = self._run_remote_auto(mode="weekly", db_path=db_path, execute=execute)
        print(result.as_log_message())
        return 0

    def run_remote_auto_panel(
        self,
        db_path: Path = DEFAULT_REMOTE_AUTO_DB,
        *,
        host: str,
        port: int,
    ) -> int:
        """Uruchamia prosty panel read-only z kolejka i raportami."""
        return serve_remote_auto_panel(db_path, host=host, port=port)

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

    def _run_remote_auto(
        self,
        *,
        mode: str,
        db_path: Path,
        execute: bool,
    ) -> RemoteAutoRunResult:
        if execute:
            assert_remote_auto_execute_allowed()

        logger = _ConsoleLogger()
        store = RemoteAutoStore(db_path)
        store.initialize()
        now = datetime.now()
        run_id = store.start_run(mode, execute, now)
        status_counts: Counter[str] = Counter()
        action_report_paths: list[Path] = []
        remote_report_path: Path | None = None
        scanned_orders = 0

        try:
            service_client = self._build_service_order_client()
            orders = self._remote_auto_source_orders(service_client, store, mode, now)
            scanned_orders = len(orders)
            processable = self._remote_auto_filter_orders(orders, store, mode, now, logger)
            serial_groups: dict[str, list[ServiceOrderRow]] = defaultdict(list)
            for row in processable:
                serial_groups[row.serial.casefold()].append(row)

            serials: list[str] = []
            serial_to_order: dict[str, ServiceOrderRow] = {}
            for serial_key, rows in serial_groups.items():
                if len(rows) > 1:
                    for row in rows:
                        previous = store.set_order_status(
                            row,
                            status=ORDER_STATUS_SKIPPED,
                            reason="Ten sam numer seryjny wystepuje w wielu otwartych zleceniach.",
                            now=now,
                        )
                        store.record_event(
                            row,
                            event_type="duplicate_serial",
                            status_from=previous,
                            status_to=ORDER_STATUS_SKIPPED,
                            message="Pominieto: duplikat numeru seryjnego w otwartych zleceniach.",
                            now=now,
                        )
                        status_counts[ORDER_STATUS_SKIPPED] += 1
                    continue
                row = rows[0]
                serials.append(row.serial)
                serial_to_order[serial_key] = row

            if serials:
                portal = self._build_portal_client()
                cutoff = subtract_months(now, REMOTE_AUTO_FRESHNESS_MONTHS)
                remote_rows = portal.delete_devices_by_serials(
                    serials,
                    execute,
                    logger.info,
                    allow_recent_serials=set(serials),
                    allow_recent_before=cutoff,
                )
                remote_report_path = write_delete_report(remote_rows)
                logger.info(f"Remote auto: raport Remote {remote_report_path.resolve()}")
            else:
                remote_rows = []

            report_rows: list[dict[str, object]] = []
            for remote_row in remote_rows:
                order = serial_to_order.get(remote_row.serial.casefold())
                if order is None:
                    continue
                decision = decide_remote_auto_status(remote_row, now=now)
                close_report_path = self._remote_auto_maybe_close_order(
                    service_client,
                    store,
                    order,
                    decision.order_status,
                    execute,
                    now,
                    action_report_paths,
                )
                final_status = (
                    ORDER_STATUS_CLOSED if close_report_path and execute else decision.order_status
                )
                previous = store.set_order_status(
                    order,
                    status=final_status,
                    remote_status=remote_row.status,
                    last_report_time=remote_row.last_report_time,
                    requested_status=remote_row.requested_status,
                    reason=decision.reason,
                    next_check_at=decision.next_check_at,
                    remote_report_path=remote_report_path,
                    close_report_path=close_report_path,
                    now=now,
                )
                store.record_event(
                    order,
                    event_type=decision.event_type,
                    status_from=previous,
                    status_to=final_status,
                    remote_status=remote_row.status,
                    last_report_time=remote_row.last_report_time,
                    message=decision.reason,
                    report_path=remote_report_path,
                    now=now,
                )
                self._remote_auto_maybe_append_note(
                    service_client,
                    store,
                    order,
                    previous=previous,
                    current=final_status,
                    reason=decision.reason,
                    last_report_time=remote_row.last_report_time,
                    execute=execute,
                    now=now,
                )
                status_counts[final_status] += 1
                report_rows.append(
                    {
                        "order": order.order_label,
                        "serial": order.serial,
                        "status": final_status,
                        "remote_status": remote_row.status,
                        "last_report_time": remote_row.last_report_time,
                        "requested_status": remote_row.requested_status,
                        "reason": decision.reason,
                        "next_check_at": (
                            decision.next_check_at.isoformat(sep=" ")
                            if decision.next_check_at
                            else ""
                        ),
                        "remote_report_path": str(remote_report_path or ""),
                        "close_report_path": str(close_report_path or ""),
                    }
                )

            local_report_path = write_remote_auto_csv_report(report_rows, mode=mode)
            logger.info(f"Remote auto: raport lokalny {local_report_path.resolve()}")
            summary = {
                "scanned_orders": scanned_orders,
                "remote_checked": len(remote_rows),
                "status_counts": dict(status_counts),
                "remote_report_path": str(remote_report_path or ""),
                "local_report_path": str(local_report_path),
                "action_report_paths": [str(path) for path in action_report_paths],
            }
            store.finish_run(
                run_id,
                status="success",
                message="OK",
                summary=summary,
                now=datetime.now(),
            )
            return RemoteAutoRunResult(
                run_id=run_id,
                mode=mode,
                execute=execute,
                scanned_orders=scanned_orders,
                remote_checked=len(remote_rows),
                status_counts=dict(status_counts),
                remote_report_path=remote_report_path,
                action_report_paths=action_report_paths,
            )
        except Exception as exc:
            store.finish_run(
                run_id,
                status="failed",
                message=f"{type(exc).__name__}: {exc}",
                summary={"scanned_orders": scanned_orders, "status_counts": dict(status_counts)},
                now=datetime.now(),
            )
            raise

    def _remote_auto_source_orders(
        self,
        service_client: FirebirdServiceOrderClient,
        store: RemoteAutoStore,
        mode: str,
        now: datetime,
    ) -> list[ServiceOrderRow]:
        if mode == "weekly":
            return service_client.fetch_by_table_ids(store.due_order_ids(now))
        return service_client.fetch_remote_open_orders()

    def _remote_auto_filter_orders(
        self,
        orders: list[ServiceOrderRow],
        store: RemoteAutoStore,
        mode: str,
        now: datetime,
        logger: _ConsoleLogger,
    ) -> list[ServiceOrderRow]:
        processable: list[ServiceOrderRow] = []
        for row in orders:
            store.upsert_order(row, now)
            if row.stan == "Z":
                previous = store.set_order_status(
                    row,
                    status=ORDER_STATUS_CLOSED,
                    reason="Zlecenie juz zamkniete w Firebird.",
                    now=now,
                )
                store.record_event(
                    row,
                    event_type="already_closed",
                    status_from=previous,
                    status_to=ORDER_STATUS_CLOSED,
                    message="Zlecenie juz zamkniete w Firebird.",
                    now=now,
                )
                continue
            if not row.serial:
                previous = store.set_order_status(
                    row,
                    status=ORDER_STATUS_SKIPPED,
                    reason="Brak numeru seryjnego w zleceniu.",
                    now=now,
                )
                store.record_event(
                    row,
                    event_type="missing_serial",
                    status_from=previous,
                    status_to=ORDER_STATUS_SKIPPED,
                    message="Pominieto: brak numeru seryjnego w zleceniu.",
                    now=now,
                )
                continue
            if mode == "scan" and store.should_skip_daily(row, now):
                logger.info(
                    "Remote auto: pomijam codzienny skan kolejki oczekujacej "
                    f"{row.order_label} {row.serial}."
                )
                continue
            processable.append(row)
        return processable

    def _remote_auto_maybe_close_order(
        self,
        service_client: FirebirdServiceOrderClient,
        store: RemoteAutoStore,
        order: ServiceOrderRow,
        order_status: str,
        execute: bool,
        now: datetime,
        action_report_paths: list[Path],
    ) -> Path | None:
        if order_status != ORDER_STATUS_REMOTE_NOT_FOUND or not execute:
            return None
        filters = [ServiceOrderFilter(order_number=order.id_zlecenie, year=order.rok)]
        actions = service_client.close_orders(
            filters,
            execute=True,
            remote_statuses={order.serial.casefold(): "not_found"},
        )
        report_path = write_service_order_action_report(actions)
        action_report_paths.append(report_path)
        for action in actions:
            store.record_event(
                order,
                event_type=f"service_order_{action.status}",
                status_to=ORDER_STATUS_CLOSED,
                remote_status="not_found",
                message=action.message,
                report_path=report_path,
                now=now,
            )
        if all(action.status in {"closed", "already_closed"} for action in actions):
            return report_path
        return None

    def _remote_auto_maybe_append_note(
        self,
        service_client: FirebirdServiceOrderClient,
        store: RemoteAutoStore,
        order: ServiceOrderRow,
        *,
        previous: str,
        current: str,
        reason: str,
        last_report_time: str,
        execute: bool,
        now: datetime,
    ) -> None:
        if (
            not execute
            or previous == current
            or current in {ORDER_STATUS_CLOSED, ORDER_STATUS_REMOTE_NOT_FOUND}
        ):
            return
        if current not in {ORDER_STATUS_WAITING_RECENT, ORDER_STATUS_SKIPPED, ORDER_STATUS_FAILED}:
            return
        note = format_order_event_note(now, reason, last_report_time)
        action = service_client.append_order_event(order, note, execute=True)
        report_path = write_service_order_action_report([action])
        store.record_event(
            order,
            event_type=f"service_order_{action.status}",
            status_from=previous,
            status_to=current,
            message=action.message,
            report_path=report_path,
            now=now,
        )
        if action.status == "event_appended":
            store.set_order_status(
                order,
                status=current,
                reason=reason,
                last_report_time=last_report_time,
                close_report_path=report_path,
                now=now,
            )

    def _build_portal_client(self) -> RicohPortalClient:
        return RicohPortalClient(
            login=self.settings.login_ricoh,
            password=self.settings.pass_ricoh,
            poll_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            poll_interval_seconds=POLL_INTERVAL_SECONDS,
            headless=True,
        )


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
