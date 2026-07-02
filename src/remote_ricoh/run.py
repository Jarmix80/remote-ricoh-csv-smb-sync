"""Punkt startowy CLI dla automatyzacji pobierania CSV Ricoh."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .config import ConfigError, Settings
from .lock import AlreadyRunningError, FileLock
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
            "Wykonuje realny zapis dla trybu --close-service-orders. "
            "Wymaga tez FB_ALLOW_WRITES=1."
        ),
    )
    parser.add_argument(
        "--remote-status-report",
        help=(
            "Opcjonalny raport Remote CSV; przy zamykaniu zlecen zamyka tylko seriale "
            "z final_status/status=not_found."
        ),
    )
    return parser


def main() -> int:
    """Uruchamia proces i zwraca kod wyjscia."""
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

    try:
        with FileLock(lock_file):
            runner = Runner(settings)
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
                return runner.run_close_service_orders(
                    Path(args.close_service_orders),
                    args.execute_service_orders,
                    Path(args.remote_status_report) if args.remote_status_report else None,
                )
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
