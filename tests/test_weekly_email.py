from __future__ import annotations

from datetime import datetime
from pathlib import Path

from remote_ricoh.config import WARSAW_TZ, EmailSettings
from remote_ricoh.remote_auto import RemoteAutoRunResult
from remote_ricoh.weekly_email import send_weekly_failure_alert, send_weekly_success_report


def _email_settings(*, use_ssl: bool = False, use_tls: bool = True) -> EmailSettings:
    return EmailSettings(
        host="ksero-partner.com.pl",
        port=587,
        username="system@ksero-partner.com.pl",
        password="secret",
        sender_address="system@ksero-partner.com.pl",
        sender_name="Remote Ricoh",
        use_ssl=use_ssl,
        use_tls=use_tls,
        weekly_report_recipients=("marcin@ksero-partner.com.pl",),
    )


class _FakeSmtp:
    instances: list[_FakeSmtp] = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.login_args: tuple[str, str] | None = None
        self.messages = []
        self.__class__.instances.append(self)

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def ehlo(self) -> None:
        self.ehlo_calls += 1

    def starttls(self, *, context) -> None:  # noqa: ANN001
        self.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:  # noqa: ANN001
        self.messages.append(message)


def _result(report_path: Path) -> RemoteAutoRunResult:
    return RemoteAutoRunResult(
        run_id=1,
        mode="weekly",
        execute=False,
        scanned_orders=2,
        remote_checked=2,
        status_counts={"ready_delete": 1, "waiting_recent": 1},
        remote_report_path=None,
        local_report_path=report_path,
        action_report_paths=[],
    )


def test_success_report_uses_tls_and_attaches_csv(monkeypatch, tmp_path: Path) -> None:
    _FakeSmtp.instances.clear()
    report_path = tmp_path / "weekly.csv"
    report_path.write_text(
        "order,serial,status,last_report_time\n"
        "17214/2026,G477M731121,ready_delete,2026/06/07 08:26\n"
        "17503/2026,E205R162408,waiting_recent,2026/06/21 18:06\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("remote_ricoh.weekly_email.smtplib.SMTP", _FakeSmtp)

    send_weekly_success_report(
        _email_settings(),
        _result(report_path),
        sent_at=datetime(2026, 7, 20, 7, 15, tzinfo=WARSAW_TZ),
    )

    client = _FakeSmtp.instances[0]
    assert client.ehlo_calls == 2
    assert client.starttls_calls == 1
    assert client.login_args == ("system@ksero-partner.com.pl", "secret")
    message = client.messages[0]
    assert message["Subject"] == "Remote Ricoh - raport tygodniowy 2026-07-20"
    assert "17214/2026 | G477M731121" in message.get_body().get_content()
    attachment = list(message.iter_attachments())[0]
    assert attachment.get_filename() == "weekly.csv"
    assert attachment.get_content().encode() == report_path.read_bytes()


def test_failure_alert_redacts_password(monkeypatch) -> None:
    _FakeSmtp.instances.clear()
    monkeypatch.setattr("remote_ricoh.weekly_email.smtplib.SMTP", _FakeSmtp)

    send_weekly_failure_alert(
        _email_settings(),
        RuntimeError("SMTP failed for secret"),
        redactions=("secret",),
        sent_at=datetime(2026, 7, 20, 7, 15, tzinfo=WARSAW_TZ),
    )

    message = _FakeSmtp.instances[0].messages[0]
    assert message["Subject"] == "Remote Ricoh - BLAD skanu tygodniowego 2026-07-20"
    body = message.get_content()
    assert "***" in body
    assert "secret" not in body
