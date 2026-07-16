from __future__ import annotations

from pathlib import Path

from remote_ricoh import run


def test_main_returns_config_error_for_missing_env(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["run", "--env-file", str(env_file), "--lock-file", str(lock_file)]
    )

    code = run.main()
    assert code == 2


def test_main_dry_run_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_MODE=network",
                "FB_HOST=127.0.0.1",
                "FB_PORT=3050",
                "FB_USER=SYSDBA",
                "FB_PASSWORD=masterkey",
                "FB_DATABASE=BAZAMS_TEST",
                "FB_CHARSET=WIN1250",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_dry(self) -> int:
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        ["run", "--env-file", str(env_file), "--lock-file", str(lock_file), "--dry-run"],
    )

    code = run.main()
    assert code == 0


def test_main_allows_missing_firebird_config(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_dry(self) -> int:
            return 99

        def run(self) -> int:
            return 0

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        ["run", "--env-file", str(env_file), "--lock-file", str(lock_file)],
    )

    code = run.main()
    assert code == 0


def test_main_downloaded_csv_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_MODE=network",
                "FB_HOST=127.0.0.1",
                "FB_PORT=3050",
                "FB_USER=SYSDBA",
                "FB_PASSWORD=masterkey",
                "FB_DATABASE=BAZAMS_TEST",
                "FB_CHARSET=WIN1250",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    dplac_csv = tmp_path / "DPLAC.csv"
    dplac_csv.write_text("x", encoding="utf-8")
    captured: dict[str, Path | None] = {"dplac": None, "dplac_no": None}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_dry(self) -> int:
            return 99

        def run_downloaded_csv(self, dplac_csv: Path, dplac_no_csv: Path | None) -> int:
            captured["dplac"] = dplac_csv
            captured["dplac_no"] = dplac_no_csv
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--dplac-csv",
            str(dplac_csv),
        ],
    )

    code = run.main()
    assert code == 0
    assert captured == {"dplac": dplac_csv, "dplac_no": None}


def test_main_delete_devices_defaults_to_dry_run(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    serials = tmp_path / "serials.txt"
    serials.write_text("T575H403598\n", encoding="utf-8")
    captured: dict[str, Path | bool | None] = {"path": None, "execute": None}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_dry(self) -> int:
            return 99

        def run_delete_devices(
            self,
            serials_path: Path,
            execute_delete: bool,
            allow_recent_delete_serials_path: Path | None = None,
            allow_recent_delete_before=None,  # noqa: ANN001
        ) -> int:
            captured["path"] = serials_path
            captured["execute"] = execute_delete
            captured["allow_recent"] = allow_recent_delete_serials_path
            captured["allow_recent_before"] = allow_recent_delete_before
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--delete-devices",
            str(serials),
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {
        "path": serials,
        "execute": False,
        "allow_recent": None,
        "allow_recent_before": None,
    }


def test_main_delete_devices_execute_flag(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    serials = tmp_path / "serials.txt"
    serials.write_text("T575H403598\n", encoding="utf-8")
    captured: dict[str, bool | None] = {"execute": None}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_delete_devices(
            self,
            serials_path: Path,
            execute_delete: bool,
            allow_recent_delete_serials_path: Path | None = None,
            allow_recent_delete_before=None,  # noqa: ANN001
        ) -> int:
            captured["execute"] = execute_delete
            captured["allow_recent"] = allow_recent_delete_serials_path
            captured["allow_recent_before"] = allow_recent_delete_before
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--delete-devices",
            str(serials),
            "--execute-delete",
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {"execute": True, "allow_recent": None, "allow_recent_before": None}


def test_main_delete_devices_recent_override_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    serials = tmp_path / "serials.txt"
    serials.write_text("T575H403598\n", encoding="utf-8")
    allow_recent = tmp_path / "allow_recent.txt"
    allow_recent.write_text("T575H403598\n", encoding="utf-8")
    captured: dict[str, Path | bool | None] = {
        "path": None,
        "execute": None,
        "allow_recent": None,
        "allow_recent_before": None,
    }

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_delete_devices(
            self,
            serials_path: Path,
            execute_delete: bool,
            allow_recent_delete_serials_path: Path | None = None,
            allow_recent_delete_before=None,  # noqa: ANN001
        ) -> int:
            captured["path"] = serials_path
            captured["execute"] = execute_delete
            captured["allow_recent"] = allow_recent_delete_serials_path
            captured["allow_recent_before"] = allow_recent_delete_before
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--delete-devices",
            str(serials),
            "--allow-recent-delete-serials",
            str(allow_recent),
            "--allow-recent-delete-before",
            "2026/06/07 08:26",
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {
        "path": serials,
        "execute": False,
        "allow_recent": allow_recent,
        "allow_recent_before": run.datetime(2026, 6, 7, 8, 26),
    }


def test_main_requires_delete_devices_for_execute_delete(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["run", "--env-file", str(env_file), "--lock-file", str(lock_file), "--execute-delete"],
    )

    code = run.main()

    assert code == 2


def test_main_requires_dplac_csv_for_not_obtained_option(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text("", encoding="utf-8")
    dplac_no_csv = tmp_path / "DPLAC_Not_obtained.csv"
    dplac_no_csv.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--dplac-not-obtained-csv",
            str(dplac_no_csv),
        ],
    )

    code = run.main()
    assert code == 2


def test_main_service_order_snapshot_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_MODE=network",
                "FB_HOST=127.0.0.1",
                "FB_DATABASE=BAZAMS_TEST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    filters = tmp_path / "orders.txt"
    filters.write_text("14331/2025\n", encoding="utf-8")
    captured: dict[str, Path | None] = {"path": None}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_service_order_snapshot(self, filters_path: Path) -> int:
            captured["path"] = filters_path
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--service-order-snapshot",
            str(filters),
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {"path": filters}


def test_main_close_service_orders_execute_and_remote_report(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_MODE=network",
                "FB_HOST=127.0.0.1",
                "FB_DATABASE=BAZAMS_TEST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    filters = tmp_path / "orders.txt"
    filters.write_text("14331/2025\n", encoding="utf-8")
    remote_report = tmp_path / "remote.csv"
    remote_report.write_text("serial,final_status\nG696M313134,not_found\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_close_service_orders(
            self,
            filters_path: Path,
            execute_service_orders: bool,
            remote_status_report: Path | None,
        ) -> int:
            captured["filters_path"] = filters_path
            captured["execute_service_orders"] = execute_service_orders
            captured["remote_status_report"] = remote_status_report
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--close-service-orders",
            str(filters),
            "--execute-service-orders",
            "--remote-status-report",
            str(remote_report),
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {
        "filters_path": filters,
        "execute_service_orders": True,
        "remote_status_report": remote_report,
    }


def test_main_service_order_diff_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    before = tmp_path / "before.csv"
    after = tmp_path / "after.csv"
    before.write_text("x", encoding="utf-8")
    after.write_text("x", encoding="utf-8")
    captured: dict[str, Path | None] = {"before": None, "after": None}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_service_order_diff(self, before_path: Path, after_path: Path) -> int:
            captured["before"] = before_path
            captured["after"] = after_path
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--service-order-diff",
            str(before),
            str(after),
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {"before": before, "after": after}


def test_main_requires_close_service_orders_for_execute_service_orders(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--execute-service-orders",
        ],
    )

    code = run.main()

    assert code == 2


def test_main_requires_close_service_orders_for_remote_status_report(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    remote_report = tmp_path / "remote.csv"
    env_file.write_text("", encoding="utf-8")
    remote_report.write_text("serial,final_status\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--remote-status-report",
            str(remote_report),
        ],
    )

    code = run.main()

    assert code == 2


def test_main_remote_auto_scan_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_MODE=network",
                "FB_HOST=127.0.0.1",
                "FB_DATABASE=BAZAMS_TEST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "remote_auto.sqlite"
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_remote_auto_scan(self, db_path: Path, *, execute: bool = False) -> int:
            captured["db_path"] = db_path
            captured["execute"] = execute
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--remote-auto-scan",
            "--remote-auto-db",
            str(db_path),
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {"db_path": db_path, "execute": False}


def test_main_remote_auto_weekly_execute_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_MODE=network",
                "FB_HOST=127.0.0.1",
                "FB_DATABASE=BAZAMS_TEST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "remote_auto.sqlite"
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_remote_auto_weekly(self, db_path: Path, *, execute: bool = False) -> int:
            captured["db_path"] = db_path
            captured["execute"] = execute
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--remote-auto-weekly",
            "--remote-auto-db",
            str(db_path),
            "--execute-remote-auto",
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {"db_path": db_path, "execute": True}


def test_main_remote_auto_panel_without_lock(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    lock_file.write_text("pid=1\n", encoding="utf-8")
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "remote_auto.sqlite"
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.settings = settings

        def run_remote_auto_panel(self, db_path: Path, *, host: str, port: int) -> int:
            captured["db_path"] = db_path
            captured["host"] = host
            captured["port"] = port
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--remote-auto-panel",
            "--remote-auto-db",
            str(db_path),
            "--remote-auto-host",
            "0.0.0.0",
            "--remote-auto-port",
            "8105",
        ],
    )

    code = run.main()

    assert code == 0
    assert captured == {"db_path": db_path, "host": "0.0.0.0", "port": 8105}


def test_main_requires_remote_auto_mode_for_execute_remote_auto(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    lock_file = tmp_path / "remote_ricoh.lock"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--env-file",
            str(env_file),
            "--lock-file",
            str(lock_file),
            "--execute-remote-auto",
        ],
    )

    code = run.main()

    assert code == 2
