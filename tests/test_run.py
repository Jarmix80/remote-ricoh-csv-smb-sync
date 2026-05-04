from __future__ import annotations

from pathlib import Path

from remote_ricoh import run


def test_main_returns_config_error_for_missing_env(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["run", "--env-file", str(env_file)])

    code = run.main()
    assert code == 2


def test_main_dry_run_path(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
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
            return 0

        def run(self) -> int:
            return 99

    monkeypatch.setattr(run, "Runner", FakeRunner)
    monkeypatch.setattr("sys.argv", ["run", "--env-file", str(env_file), "--dry-run"])

    code = run.main()
    assert code == 0
