from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from remote_ricoh.portal import PortalError, RicohPortalClient


def test_extract_requested_id_from_feedback_text() -> None:
    text = "Request accepted. Requested ID : 20260506132047396"

    out = RicohPortalClient._extract_requested_id(text)

    assert out == "20260506132047396"


def test_extract_requested_id_from_js_key_value() -> None:
    text = '{"requested_id":"20260506132047397"}'

    out = RicohPortalClient._extract_requested_id(text)

    assert out == "20260506132047397"


def test_extract_requested_id_rejects_short_numeric_value() -> None:
    text = "Requested ID: 1636031505"

    out = RicohPortalClient._extract_requested_id(text)

    assert out is None


def test_extract_requested_ids_from_records_with_key_variants() -> None:
    html = (
        "<script>let records = "
        '[{"requestedId":"20260506132047398"}, {"RequestedID": "20260506132047399"}]'
        ";</script>"
    )

    out = RicohPortalClient._extract_requested_ids_from_html(html)

    assert out == {"20260506132047398", "20260506132047399"}


def test_extract_requested_ids_from_context_without_records_json() -> None:
    html = (
        "<table><thead><tr><th>Requested ID</th></tr></thead>"
        "<tbody><tr><td>20260506132047400</td></tr></tbody></table>"
    )

    out = RicohPortalClient._extract_requested_ids_from_html(html)

    assert out == {"20260506132047400"}


def test_find_record_by_requested_id_with_key_variants() -> None:
    html = (
        "<script>const records = "
        '[{"RequestID":"20260506132047401","status":"Completed","fileName":"x.zip"}]'
        ";</script>"
    )

    record = RicohPortalClient._find_record_by_requested_id(html, "20260506132047401")

    assert record is not None
    assert RicohPortalClient._extract_status_from_record(record) == "Completed"
    assert RicohPortalClient._extract_file_name_from_record(record) == "x.zip"


def test_confirm_request_modal_returns_false_when_missing() -> None:
    logs: list[str] = []

    class MissingDialog:
        @property
        def first(self) -> MissingDialog:
            return self

        def wait_for(self, **kwargs) -> None:  # noqa: ANN003
            raise PlaywrightTimeoutError("missing")

    class FakePage:
        def get_by_text(self, *args, **kwargs) -> MissingDialog:  # noqa: ANN002, ANN003
            return MissingDialog()

    assert RicohPortalClient._confirm_request_modal_if_present(FakePage(), logs.append) is False
    assert logs == []


def test_capture_debug_snapshot_writes_html_metadata_and_screenshot(tmp_path: Path) -> None:
    logs: list[str] = []
    client = RicohPortalClient(
        login="user",
        password="pass",
        poll_timeout_seconds=1,
        poll_interval_seconds=1,
        debug_dir=tmp_path,
    )

    class FakePage:
        url = "https://example.test/MyHome.aspx"

        def content(self) -> str:
            return "<html><body>snapshot</body></html>"

        def screenshot(self, path: str, full_page: bool) -> None:
            assert full_page is True
            Path(path).write_bytes(b"png")

    out = client._capture_debug_snapshot(
        FakePage(),
        "requested id missing",
        logs.append,
        {"known_ids_count": 3},
    )

    assert out is not None
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["reason"] == "requested id missing"
    assert metadata["url"] == "https://example.test/MyHome.aspx"
    assert metadata["known_ids_count"] == 3
    assert (out / "page.html").read_text(encoding="utf-8") == "<html><body>snapshot</body></html>"
    assert (out / "screenshot.png").read_bytes() == b"png"
    assert logs[-1].startswith("Zapisano diagnostyke portalu Ricoh:")


def test_create_csv_request_captures_debug_when_requested_id_missing(monkeypatch) -> None:
    logs: list[str] = []
    clicked: list[int] = []
    snapshots: list[tuple[str, dict[str, object] | None]] = []
    client = RicohPortalClient(
        login="user",
        password="pass",
        poll_timeout_seconds=1,
        poll_interval_seconds=1,
    )

    class FakeRequestButton:
        def click(self, timeout: int) -> None:
            clicked.append(timeout)

    class FakePage:
        def goto(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def on(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def remove_listener(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    def fake_snapshot(
        self: RicohPortalClient,
        page: FakePage,
        reason: str,
        log,
        metadata: dict[str, object] | None = None,
    ) -> None:
        snapshots.append((reason, metadata))

    monkeypatch.setattr(
        RicohPortalClient,
        "_set_date_range_from_yesterday_to_today",
        staticmethod(lambda page, log: log("dates set")),
    )
    monkeypatch.setattr(
        RicohPortalClient,
        "_first_locator",
        staticmethod(lambda page, selectors: FakeRequestButton()),
    )
    monkeypatch.setattr(
        RicohPortalClient,
        "_confirm_request_modal_if_present",
        staticmethod(lambda page, log: False),
    )
    monkeypatch.setattr(
        RicohPortalClient,
        "_wait_for_requested_id_feedback",
        staticmethod(lambda page, dialog_messages, known_ids: None),
    )
    monkeypatch.setattr(
        RicohPortalClient,
        "_wait_for_new_requested_id",
        lambda self, page, known_ids, log: None,
    )
    monkeypatch.setattr(RicohPortalClient, "_capture_debug_snapshot", fake_snapshot)

    with pytest.raises(PortalError, match="Requested ID"):
        client._create_csv_request(FakePage(), logs.append, {"20260506132047401"})

    assert clicked == [30_000]
    assert snapshots == [
        (
            "requested_id_missing",
            {
                "known_ids_count": 1,
                "dialog_messages": [],
                "request_dom_popup_confirmed": False,
                "request_popup_confirmed": False,
            },
        )
    ]
    assert "Kliknieto przycisk Request CSV." in logs
    assert "Nie wykryto popupu potwierdzenia Request CSV po kliknieciu Request." in logs


def test_extract_device_records_from_encoded_hidden_json() -> None:
    html = (
        '<input type="hidden" value="%5B%7B%22DeviceId%22%3A%22T575H403598%22%2C'
        "%22ModelName%22%3A%22RICOH+SP+4510DN%22%2C%22Customer%22%3A%221447%22%2C"
        "%22RequestStatus%22%3A%22Removing%22%2C"
        '%22LastReportTime%22%3A%222025%2F12%2F29+21%3A29%22%7D%5D">'
    )

    matches = RicohPortalClient._extract_device_records_from_html(html, "T575H403598")

    assert len(matches) == 1
    assert matches[0].device_id == "T575H403598"
    assert matches[0].model == "RICOH SP 4510DN"
    assert matches[0].customer == "1447"
    assert matches[0].requested_status == "Removing"
    assert matches[0].last_report_time == "2025/12/29 21:29"


def test_parse_last_report_time_accepts_portal_format() -> None:
    out = RicohPortalClient._parse_last_report_time("2025/12/29 21:29")

    assert out == datetime(2025, 12, 29, 21, 29)


def test_subtract_months_clamps_day() -> None:
    out = RicohPortalClient._subtract_months(datetime(2026, 5, 31, 12, 0), 3)

    assert out == datetime(2026, 2, 28, 12, 0)
