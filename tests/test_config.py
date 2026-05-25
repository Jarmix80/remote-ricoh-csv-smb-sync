from __future__ import annotations

from pathlib import Path

import pytest

from remote_ricoh.config import ConfigError, Settings, normalize_unc


def test_normalize_unc_accepts_slashes() -> None:
    server, unc = normalize_unc("//10.0.0.5/share/folder")
    assert server == "10.0.0.5"
    assert unc == r"\\10.0.0.5\share\folder"


def test_settings_from_env_file_requires_base_values(tmp_path: Path) -> None:
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

    settings = Settings.from_env_file(env_file)
    assert settings.login_ricoh == "user"
    assert settings.sciezka_remote == r"\\server\share\ricoh"
    assert settings.fb_database == "BAZAMS_TEST"
    assert settings.fb_mode == "network"
    assert settings.fb_port == 3050
    assert settings.firebird_enabled is True


def test_settings_from_env_file_allows_missing_firebird_config(tmp_path: Path) -> None:
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

    assert settings.sciezka_remote == r"\\server\share\ricoh"
    assert settings.firebird_enabled is False
    assert settings.fb_host is None
    assert settings.firebird_warning is None


def test_settings_from_env_file_disables_invalid_firebird_config(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_HOST=127.0.0.1",
                "FB_PORT=abc",
                "FB_USER=SYSDBA",
                "FB_PASSWORD=masterkey",
                "FB_DATABASE=BAZAMS_TEST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.firebird_enabled is False
    assert settings.fb_port is None
    assert settings.firebird_warning is not None
    assert "nieprawidlowy port Firebird" in settings.firebird_warning


def test_settings_from_env_file_uses_default_firebird_credentials_when_empty(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "FB_HOST=192.168.0.9",
                "FB_PORT=",
                "FB_USER=",
                "FB_PASSWORD=",
                "FB_DATABASE=BAZAMS_TEST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.firebird_enabled is True
    assert settings.fb_host == "192.168.0.9"
    assert settings.fb_port == 3050
    assert settings.fb_user == "SYSDBA"
    assert settings.fb_password == "masterkey"
    assert settings.fb_database == "BAZAMS_TEST"
    assert settings.firebird_warning is None
