from __future__ import annotations

from pathlib import Path

from remote_ricoh import run


def test_main_returns_config_error_for_missing_env(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["run", "--env-file", str(env_file)])

    code = run.main()
    assert code == 2
