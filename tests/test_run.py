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
