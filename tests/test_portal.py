from remote_ricoh.portal import RicohPortalClient


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
