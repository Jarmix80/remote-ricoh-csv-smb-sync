"""Punkt startowy CLI dla automatyzacji pobierania CSV Ricoh."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from .config import ConfigError, Settings
from .documaster import DEFAULT_DOCUMASTER_DB
from .lock import AlreadyRunningError, FileLock
from .printradar_cmail import DEFAULT_PRINTRADAR_CMAIL_DB
from .remote_auto import DEFAULT_REMOTE_AUTO_DB, REMOTE_AUTO_PANEL_PORT
from .service import Runner


def build_parser() -> argparse.ArgumentParser:
    """Buduje parser argumentow CLI."""
    parser = argparse.ArgumentParser(description="Automatyczne pobieranie CSV licznikow Ricoh.")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Sciezka do pliku .env z konfiguracja logowania, SMB i Firebirda.",
    )
    parser.add_argument(
        "--lock-file",
        default=".state/remote_ricoh.lock",
        help="Sciezka lockfile zapobiegajacego rownoleglemu uruchomieniu.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Tryb diagnostyczny: sprawdza konfiguracje, SMB i Firebirda bez logowania do Ricoh.",
    )
    mode_group.add_argument(
        "--dplac-csv",
        help="Sciezka do juz pobranego DPLAC CSV; uruchamia tylko etap SMB + Firebird.",
    )
    mode_group.add_argument(
        "--delete-devices",
        help=(
            "Sciezka do TXT/CSV z numerami seryjnymi urzadzen Ricoh. "
            "Domyslnie wykonuje dry-run i zapisuje raport lokalny."
        ),
    )
    mode_group.add_argument(
        "--service-order-snapshot",
        help="Sciezka do TXT/CSV z filtrami zlecen serwisowych do snapshotu Firebird.",
    )
    mode_group.add_argument(
        "--service-order-diff",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="Porownuje dwa snapshoty CSV zlecen serwisowych.",
    )
    mode_group.add_argument(
        "--close-service-orders",
        help=(
            "Sciezka do TXT/CSV z filtrami zlecen serwisowych. "
            "Domyslnie wykonuje dry-run zamkniecia."
        ),
    )
    mode_group.add_argument(
        "--remote-auto-scan",
        action="store_true",
        help="Cykliczny skan nowych zlecen TECHNIK=REMOTE z produkcyjnego Firebirda.",
    )
    mode_group.add_argument(
        "--remote-auto-weekly",
        action="store_true",
        help="Tygodniowa kontrola lokalnej kolejki oczekujacej REMOTE.",
    )
    mode_group.add_argument(
        "--remote-auto-panel",
        action="store_true",
        help="Uruchamia prosty panel WWW z kolejka, runami i raportami REMOTE.",
    )
    mode_group.add_argument(
        "--documaster-scan",
        action="store_true",
        help="Skanuje katalog SMB documaster i przygotowuje import licznikow do CMAIL.",
    )
    mode_group.add_argument(
        "--printradar-cmail-sync",
        action="store_true",
        help="Synchronizuje zakonczone dzienne liczniki PrintRadar z Firebird CMAIL.",
    )
    mode_group.add_argument(
        "--printradar-cmail-weekly-report",
        action="store_true",
        help="Wysyla tygodniowy raport kolejki licznikow skanera PrintRadar.",
    )
    parser.add_argument(
        "--dplac-not-obtained-csv",
        help="Opcjonalna sciezka do DPLAC_Not_obtained CSV dla trybu --dplac-csv.",
    )
    parser.add_argument(
        "--execute-delete",
        action="store_true",
        help="Wykonuje realne usuniecie dla trybu --delete-devices. Bez tej flagi jest dry-run.",
    )
    parser.add_argument(
        "--allow-recent-delete-serials",
        help=(
            "Opcjonalna sciezka do TXT/CSV z numerami seryjnymi, dla ktorych wolno "
            "ominac blokade Last Report Date/Time z ostatnich 3 miesiecy."
        ),
    )
    parser.add_argument(
        "--allow-recent-delete-before",
        help=(
            "Opcjonalny prog Last Report Date/Time dla jawnego obejscia, np. "
            "'2026/06/07 08:26'. Urzadzenia z data rowna albo nowsza sa pomijane."
        ),
    )
    parser.add_argument(
        "--execute-service-orders",
        action="store_true",
        help=(
            "Wykonuje realny zapis dla trybu --close-service-orders. Wymaga tez FB_ALLOW_WRITES=1."
        ),
    )
    parser.add_argument(
        "--service-order-repair-text",
        help=(
            "Opcjonalna tresc dopisywana do WYKONANIE przy --close-service-orders. "
            "Domyslnie: 'Urządzenie usunięte z Remote.'."
        ),
    )
    parser.add_argument(
        "--preserve-service-order-metadata",
        action="store_true",
        help=(
            "Przy --close-service-orders nie zmienia OPERATOR ani DATA_Z; "
            "aktualizuje tylko WYKONANIE i STAN przez ZR do Z."
        ),
    )
    parser.add_argument(
        "--remote-status-report",
        help=(
            "Opcjonalny raport Remote CSV; przy zamykaniu zlecen zamyka tylko seriale "
            "z final_status/status=not_found."
        ),
    )
    parser.add_argument(
        "--remote-auto-db",
        default=str(DEFAULT_REMOTE_AUTO_DB),
        help="Sciezka do lokalnej bazy SQLite workflow REMOTE.",
    )
    parser.add_argument(
        "--execute-remote-auto",
        action="store_true",
        help=(
            "Wykonuje realne usuwanie i zamykanie w trybie remote-auto. "
            "Wymaga FB_ALLOW_WRITES=1 i REMOTE_AUTO_ALLOW_DELETES=1."
        ),
    )
    parser.add_argument(
        "--execute-documaster",
        action="store_true",
        help=(
            "Wykonuje realny import CMAIL i archiwizacje plikow Documaster. "
            "Wymaga DOCUMASTER_ALLOW_WRITES=1."
        ),
    )
    parser.add_argument(
        "--documaster-db",
        default=str(DEFAULT_DOCUMASTER_DB),
        help="Sciezka do lokalnej bazy stanu importu Documaster.",
    )
    parser.add_argument(
        "--execute-printradar-cmail",
        action="store_true",
        help=("Wykonuje realny zapis PrintRadar do CMAIL. Wymaga PRINTRADAR_CMAIL_ALLOW_WRITES=1."),
    )
    parser.add_argument(
        "--printradar-cmail-backfill",
        action="store_true",
        help="Czyta cala dostepna historie PrintRadar zamiast danych od zapisanego kursora.",
    )
    parser.add_argument(
        "--printradar-cmail-serials",
        help="Opcjonalny TXT/CSV z numerami seryjnymi dla kontrolowanego testu lub canary.",
    )
    parser.add_argument(
        "--printradar-cmail-db",
        default=str(DEFAULT_PRINTRADAR_CMAIL_DB),
        help="Sciezka do lokalnej bazy kursora i kolejki skanerow PrintRadar.",
    )
    parser.add_argument(
        "--remote-auto-host",
        default="0.0.0.0",
        help="Host panelu --remote-auto-panel.",
    )
    parser.add_argument(
        "--remote-auto-port",
        type=int,
        default=REMOTE_AUTO_PANEL_PORT,
        help="Port startowy panelu --remote-auto-panel.",
    )
    return parser


def main() -> int:
    """Uruchamia proces i zwraca kod wyjscia."""
    os.umask(0o077)
    args = build_parser().parse_args()

    env_file = Path(args.env_file)
    lock_file = Path(args.lock_file)

    if args.dplac_not_obtained_csv and not args.dplac_csv:
        print("BLAD konfiguracji: --dplac-not-obtained-csv wymaga --dplac-csv.")
        return 2
    if args.execute_delete and not args.delete_devices:
        print("BLAD konfiguracji: --execute-delete wymaga --delete-devices.")
        return 2
    if args.allow_recent_delete_serials and not args.delete_devices:
        print("BLAD konfiguracji: --allow-recent-delete-serials wymaga --delete-devices.")
        return 2
    if args.allow_recent_delete_before and not args.delete_devices:
        print("BLAD konfiguracji: --allow-recent-delete-before wymaga --delete-devices.")
        return 2
    if args.execute_service_orders and not args.close_service_orders:
        print("BLAD konfiguracji: --execute-service-orders wymaga --close-service-orders.")
        return 2
    if args.remote_status_report and not args.close_service_orders:
        print("BLAD konfiguracji: --remote-status-report wymaga --close-service-orders.")
        return 2
    if args.service_order_repair_text and not args.close_service_orders:
        print("BLAD konfiguracji: --service-order-repair-text wymaga --close-service-orders.")
        return 2
    if args.preserve_service_order_metadata and not args.close_service_orders:
        print("BLAD konfiguracji: --preserve-service-order-metadata wymaga --close-service-orders.")
        return 2
    if args.execute_remote_auto and not (args.remote_auto_scan or args.remote_auto_weekly):
        print("BLAD konfiguracji: --execute-remote-auto wymaga trybu remote-auto.")
        return 2
    if args.execute_documaster and not args.documaster_scan:
        print("BLAD konfiguracji: --execute-documaster wymaga --documaster-scan.")
        return 2
    if args.execute_printradar_cmail and not args.printradar_cmail_sync:
        print("BLAD konfiguracji: --execute-printradar-cmail wymaga --printradar-cmail-sync.")
        return 2
    if args.printradar_cmail_backfill and not args.printradar_cmail_sync:
        print("BLAD konfiguracji: --printradar-cmail-backfill wymaga --printradar-cmail-sync.")
        return 2
    if args.printradar_cmail_serials and not args.printradar_cmail_sync:
        print("BLAD konfiguracji: --printradar-cmail-serials wymaga --printradar-cmail-sync.")
        return 2

    try:
        settings = Settings.from_env_file(env_file)
    except ConfigError as exc:
        print(f"BLAD konfiguracji: {exc}")
        return 2

    try:
        allow_recent_delete_before = _parse_datetime_arg(args.allow_recent_delete_before)
    except ValueError as exc:
        print(f"BLAD konfiguracji: {exc}")
        return 2

    runner = Runner(settings)
    if args.remote_auto_panel:
        try:
            return runner.run_remote_auto_panel(
                Path(args.remote_auto_db),
                host=args.remote_auto_host,
                port=args.remote_auto_port,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"BLAD wykonania: {type(exc).__name__}: {exc}")
            return 1

    try:
        with FileLock(lock_file):
            if args.dry_run:
                return runner.run_dry()
            if args.dplac_csv:
                dplac_not_obtained = (
                    Path(args.dplac_not_obtained_csv) if args.dplac_not_obtained_csv else None
                )
                return runner.run_downloaded_csv(Path(args.dplac_csv), dplac_not_obtained)
            if args.delete_devices:
                return runner.run_delete_devices(
                    Path(args.delete_devices),
                    args.execute_delete,
                    (
                        Path(args.allow_recent_delete_serials)
                        if args.allow_recent_delete_serials
                        else None
                    ),
                    allow_recent_delete_before,
                )
            if args.service_order_snapshot:
                return runner.run_service_order_snapshot(Path(args.service_order_snapshot))
            if args.service_order_diff:
                before_path, after_path = args.service_order_diff
                return runner.run_service_order_diff(Path(before_path), Path(after_path))
            if args.close_service_orders:
                close_options = {}
                if args.service_order_repair_text:
                    close_options["repair_text"] = args.service_order_repair_text
                if args.preserve_service_order_metadata:
                    close_options["preserve_metadata"] = True
                return runner.run_close_service_orders(
                    Path(args.close_service_orders),
                    args.execute_service_orders,
                    Path(args.remote_status_report) if args.remote_status_report else None,
                    **close_options,
                )
            if args.remote_auto_scan:
                return runner.run_remote_auto_scan(
                    Path(args.remote_auto_db),
                    execute=args.execute_remote_auto,
                )
            if args.remote_auto_weekly:
                return runner.run_remote_auto_weekly(
                    Path(args.remote_auto_db),
                    execute=args.execute_remote_auto,
                )
            if args.documaster_scan:
                return runner.run_documaster_scan(
                    Path(args.documaster_db),
                    execute=args.execute_documaster,
                )
            if args.printradar_cmail_sync:
                return runner.run_printradar_cmail_sync(
                    Path(args.printradar_cmail_db),
                    execute=args.execute_printradar_cmail,
                    backfill=args.printradar_cmail_backfill,
                    serials_path=(
                        Path(args.printradar_cmail_serials)
                        if args.printradar_cmail_serials
                        else None
                    ),
                )
            if args.printradar_cmail_weekly_report:
                return runner.run_printradar_cmail_weekly_report(Path(args.printradar_cmail_db))
            return runner.run()
    except AlreadyRunningError as exc:
        print(f"INFO: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"BLAD wykonania: {type(exc).__name__}: {exc}")
        return 1


def _parse_datetime_arg(value: str | None) -> datetime | None:
    """Parsuje argument daty/czasu CLI dla progow operacyjnych."""
    if value is None:
        return None
    text = value.strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("Niepoprawny format --allow-recent-delete-before; uzyj 'YYYY/MM/DD HH:MM'.")


if __name__ == "__main__":
    raise SystemExit(main())
