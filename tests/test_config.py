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


def test_settings_from_env_file_loads_weekly_email_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "EMAIL_HOST=ksero-partner.com.pl",
                "EMAIL_PORT=587",
                "EMAIL_USERNAME=system@ksero-partner.com.pl",
                "EMAIL_PASSWORD=secret",
                "EMAIL_SENDER_ADDRESS=system@ksero-partner.com.pl",
                "EMAIL_SENDER_NAME=",
                "EMAIL_USE_SSL=false",
                "EMAIL_USE_TLS=true",
                "EMAIL_WEEKLY_REPORT_TO=marcin@ksero-partner.com.pl,biuro@ksero-partner.com.pl",
                "DOCUMASTER_ALLOW_WRITES=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.email is not None
    assert settings.email.host == "ksero-partner.com.pl"
    assert settings.email.port == 587
    assert settings.email.sender_name == "Remote Ricoh"
    assert settings.email.use_tls is True
    assert settings.email.weekly_report_recipients == (
        "marcin@ksero-partner.com.pl",
        "biuro@ksero-partner.com.pl",
    )
    assert settings.email.documaster_report_recipients == (
        "marcin@ksero-partner.com.pl",
        "biuro@ksero-partner.com.pl",
    )
    assert settings.documaster_allow_writes is True


def test_settings_loads_separate_documaster_recipient(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "EMAIL_HOST=ksero-partner.com.pl",
                "EMAIL_PORT=587",
                "EMAIL_USERNAME=system@ksero-partner.com.pl",
                "EMAIL_PASSWORD=secret",
                "EMAIL_SENDER_ADDRESS=system@ksero-partner.com.pl",
                "EMAIL_USE_SSL=false",
                "EMAIL_USE_TLS=true",
                "EMAIL_WEEKLY_REPORT_TO=weekly@example.com",
                "EMAIL_DOCUMASTER_REPORT_TO=documaster@example.com",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.email is not None
    assert settings.email.documaster_report_recipients == ("documaster@example.com",)


def test_settings_loads_printradar_and_separate_report_recipient(tmp_path: Path) -> None:
    identity = tmp_path / "id_ed25519"
    identity.write_text("test", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "PRINTRADAR_CMAIL_ALLOW_WRITES=1",
                "PRINTRADAR_DB_HOST=127.0.0.1",
                "PRINTRADAR_DB_PORT=55432",
                "PRINTRADAR_DB_NAME=printradar",
                "PRINTRADAR_DB_USER=reader",
                "PRINTRADAR_DB_PASSWORD=secret",
                "PRINTRADAR_DB_VIEW=integration.device_counter_readings",
                "PRINTRADAR_SSH_HOST=print.example.com",
                "PRINTRADAR_SSH_PORT=22",
                "PRINTRADAR_SSH_USER=tunnel",
                f"PRINTRADAR_SSH_IDENTITY_FILE={identity}",
                "PRINTRADAR_SSH_REMOTE_DB_HOST=127.0.0.1",
                "PRINTRADAR_SSH_REMOTE_DB_PORT=5432",
                "PRINTRADAR_TUNNEL_LOCAL_HOST=127.0.0.1",
                "PRINTRADAR_TUNNEL_LOCAL_PORT=55432",
                "EMAIL_HOST=mail.example.com",
                "EMAIL_PORT=587",
                "EMAIL_USERNAME=system@example.com",
                "EMAIL_PASSWORD=email-secret",
                "EMAIL_SENDER_ADDRESS=system@example.com",
                "EMAIL_USE_SSL=false",
                "EMAIL_USE_TLS=true",
                "EMAIL_WEEKLY_REPORT_TO=weekly@example.com",
                "EMAIL_PRINTRADAR_REPORT_TO=printradar@example.com",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.printradar is not None
    assert settings.printradar.db_name == "printradar"
    assert settings.printradar.ssh_identity_file == identity
    assert settings.printradar_cmail_allow_writes is True
    assert settings.email is not None
    assert settings.email.printradar_report_recipients == ("printradar@example.com",)


def test_settings_from_env_file_marks_invalid_email_host_as_warning(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "login_ricoh=user",
                "pass_ricoh=pass",
                "sciezka_remote=//server/share/ricoh",
                "user_smb=smbuser",
                "pass_smb=smbpass",
                "EMAIL_HOST=system@ksero-partner.com.pl",
                "EMAIL_PORT=587",
                "EMAIL_USERNAME=system@ksero-partner.com.pl",
                "EMAIL_PASSWORD=secret",
                "EMAIL_SENDER_ADDRESS=system@ksero-partner.com.pl",
                "EMAIL_USE_SSL=false",
                "EMAIL_USE_TLS=true",
                "EMAIL_WEEKLY_REPORT_TO=marcin@ksero-partner.com.pl",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.email is None
    assert settings.email_warning is not None
    assert "EMAIL_HOST" in settings.email_warning
