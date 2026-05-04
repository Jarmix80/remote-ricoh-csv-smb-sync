from __future__ import annotations

from pathlib import Path

import pytest

from remote_ricoh.config import ConfigError, Settings, normalize_unc


def test_normalize_unc_accepts_slashes() -> None:
    server, unc = normalize_unc("//10.0.0.5/share/folder")
    assert server == "10.0.0.5"
    assert unc == r"\\10.0.0.5\share\folder"


def test_settings_from_env_file_requires_all_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("login_ricoh=test\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        Settings.from_env_file(env_file)


def test_settings_from_env_file_success(tmp_path: Path) -> None:
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

    settings = Settings.from_env_file(env_file)
    assert settings.login_ricoh == "user"
    assert settings.sciezka_remote == r"\\server\share\ricoh"
