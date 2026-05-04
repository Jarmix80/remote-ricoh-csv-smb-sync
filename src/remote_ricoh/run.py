"""Punkt startowy CLI dla automatyzacji pobierania CSV Ricoh."""

from __future__ import annotations

import argparse
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
        help="Sciezka do pliku .env z konfiguracja logowania i SMB.",
    )
    parser.add_argument(
        "--lock-file",
        default=".state/remote_ricoh.lock",
        help="Sciezka lockfile zapobiegajacego rownoleglemu uruchomieniu.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tryb diagnostyczny: sprawdza konfiguracje i SMB bez logowania do Ricoh.",
    )
    return parser


def main() -> int:
    """Uruchamia proces i zwraca kod wyjscia."""
    args = build_parser().parse_args()

    env_file = Path(args.env_file)
    lock_file = Path(args.lock_file)

    try:
        settings = Settings.from_env_file(env_file)
    except ConfigError as exc:
        print(f"BLAD konfiguracji: {exc}")
        return 2

    try:
        with FileLock(lock_file):
            runner = Runner(settings)
            if args.dry_run:
                return runner.run_dry()
            return runner.run()
    except AlreadyRunningError as exc:
        print(f"INFO: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"BLAD wykonania: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
