"""Automatyzacja przegladarki: ADFS -> Request CSV -> MyHome -> pobranie ZIP."""

from __future__ import annotations

import json
import re
import time
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import unquote_plus

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .device_delete import DeviceDeleteReportRow

REQUEST_CSV_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx"
MY_HOME_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/MyHome.aspx"
SEARCH_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/Search.aspx"
REQUESTED_ID_LENGTH = 17
REQUESTED_ID_RE = re.compile(
    rf"Request(?:ed)?\s*ID\s*[:=]\s*(\d{{{REQUESTED_ID_LENGTH}}})",
    re.IGNORECASE,
)
REQUESTED_ID_VALUE_RE = re.compile(
    r'(?:\\?["\'])?(?:requested[_\-\s]*id|request[_\-\s]*id)(?:\\?["\'])?'
    rf'\s*[:=]\s*(?:\\?["\'])?(\d{{{REQUESTED_ID_LENGTH}}})(?:\\?["\'])?',
    re.IGNORECASE,
)
REQUESTED_ID_TOKEN_RE = re.compile(rf"(?<!\d)(\d{{{REQUESTED_ID_LENGTH}}})(?!\d)")
MYHOME_RECORDS_RE = re.compile(r"(?:var|let|const)\s+records\s*=\s*(\[[\s\S]*?\]);", re.IGNORECASE)


class PortalError(RuntimeError):
    """Blad podczas automatyzacji portalu Ricoh."""


@dataclass(slots=True)
class DownloadResult:
    """Dane wynikowe pobrania ZIP z portalu."""

    requested_id: str
    zip_path: Path


@dataclass(frozen=True, slots=True)
class DeviceSearchMatch:
    """Dane znalezionego rekordu urzadzenia w portalu."""

    device_id: str
    model: str = ""
    customer: str = ""
    requested_status: str = ""
    last_report_time: str = ""
    row_text: str = ""


@dataclass(slots=True)
class RicohPortalClient:
    """Klient Playwright do pobrania ZIP z CSV licznikow."""

    login: str
    password: str
    poll_timeout_seconds: int
    poll_interval_seconds: int
    headless: bool = True
    debug_dir: Path = Path(".debug/ricoh_portal")

    def request_and_download_zip(
        self, output_dir: Path, log: Callable[[str], None]
    ) -> DownloadResult:
        """Uruchamia pelny flow Ricoh i zwraca sciezke pobranego ZIP."""
        output_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            try:
                self._open_adfs_and_login(page, log)
                known_ids = self._collect_requested_ids(page)
                log(f"MyHome przed Request: wykryto {len(known_ids)} Requested ID.")
                requested_id = self._create_csv_request(page, log, known_ids)
                zip_path = self._poll_and_download(page, output_dir, requested_id, log)
                return DownloadResult(requested_id=requested_id, zip_path=zip_path)
            finally:
                context.close()
                browser.close()

    def delete_devices_by_serials(
        self,
        serials: list[str],
        execute_delete: bool,
        log: Callable[[str], None],
        allow_recent_serials: set[str] | None = None,
        allow_recent_before: datetime | None = None,
    ) -> list[DeviceDeleteReportRow]:
        """Wyszukuje i opcjonalnie usuwa urzadzenia po numerach seryjnych."""
        results: list[DeviceDeleteReportRow] = []
        allow_recent_keys = {serial.casefold() for serial in allow_recent_serials or set()}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            try:
                self._open_adfs_and_login(page, log)
                mode = "EXECUTE" if execute_delete else "DRY-RUN"
                log(f"Start usuwania urzadzen Ricoh: tryb={mode}, seriale={len(serials)}.")

                for serial in serials:
                    try:
                        results.append(
                            self._process_device_delete_serial(
                                page,
                                serial,
                                execute_delete,
                                log,
                                allow_recent=serial.casefold() in allow_recent_keys,
                                allow_recent_before=allow_recent_before,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._capture_debug_snapshot(
                            page,
                            "device_delete_failed",
                            log,
                            {"serial": serial, "error": f"{type(exc).__name__}: {exc}"},
                        )
                        results.append(
                            DeviceDeleteReportRow(
                                serial=serial,
                                status="failed",
                                matched_count=0,
                                message=f"{type(exc).__name__}: {exc}",
                            )
                        )
            finally:
                context.close()
                browser.close()

        return results

    def _open_adfs_and_login(self, page: Page, log: Callable[[str], None]) -> None:
        # Wejscie przez RequestCsv daje poprawny redirect SAML do ADFS/HRD.
        log(f"Otwieram strone startowa: {REQUEST_CSV_URL}")
        page.goto(REQUEST_CSV_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_000)

        # Partner jest widoczny tylko na ekranie Home Realm Discovery.
        partner = page.get_by_text("Partner", exact=False).first
        try:
            partner.wait_for(state="visible", timeout=8_000)
            partner.click(timeout=30_000)
            log("Kliknieto opcje Partner.")
        except PlaywrightTimeoutError:
            log("Krok Partner pominiety (strona przeszla od razu do logowania).")

        user_selector = ",".join(
            [
                "input[type='email']",
                "input[name='UserName']",
                "input[name='username']",
                "input[id*='user']",
                "input[name*='user']",
            ]
        )
        pass_selector = ",".join(
            [
                "input[type='password']",
                "input[name='Password']",
                "input[name='password']",
                "input[id*='pass']",
            ]
        )
        page.wait_for_selector(user_selector, state="visible", timeout=60_000)
        page.wait_for_selector(pass_selector, state="visible", timeout=60_000)
        user_input = page.locator(user_selector).first
        pass_input = page.locator(pass_selector).first

        if user_input is None or pass_input is None:
            raise PortalError("Nie znaleziono pol logowania na stronie ADFS.")

        user_input.fill(self.login)
        pass_input.fill(self.password)

        submit_selector = ",".join(
            [
                "#submitButton",
                "span#submitButton",
                "[role='button']:has-text('Sign in')",
                "[role='button']:has-text('Log in')",
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Sign in')",
                "button:has-text('Log in')",
            ]
        )
        page.wait_for_selector(submit_selector, state="visible", timeout=30_000)
        submit = page.locator(submit_selector).first

        submit.click()
        page.wait_for_load_state("domcontentloaded", timeout=60_000)
        log("Logowanie zakonczone, przechodze do strony Request CSV.")

    def _create_csv_request(
        self,
        page: Page,
        log: Callable[[str], None],
        known_ids: set[str],
    ) -> str:
        page.goto(REQUEST_CSV_URL, wait_until="domcontentloaded", timeout=60_000)
        self._set_date_range_from_yesterday_to_today(page, log)

        dialog_messages: list[str] = []

        def handle_dialog(dialog) -> None:  # noqa: ANN001
            dialog_messages.append(dialog.message)
            dialog.accept()

        page.on("dialog", handle_dialog)

        try:
            request_button = self._first_locator(
                page,
                [
                    "button:has-text('Request')",
                    "input[type='submit'][value*='Request']",
                    "input[type='button'][value*='Request']",
                ],
            )
            if request_button is None:
                self._capture_debug_snapshot(page, "request_button_missing", log)
                raise PortalError("Nie znaleziono przycisku Request na RequestCsv.aspx.")

            request_button.click(timeout=30_000)
            log("Kliknieto przycisk Request CSV.")
            request_dom_popup_confirmed = self._confirm_request_modal_if_present(page, log)
            request_popup_confirmed = request_dom_popup_confirmed or bool(dialog_messages)
            if dialog_messages:
                log("Potwierdzono dialog Request CSV przegladarki.")
            if not request_popup_confirmed:
                log("Nie wykryto popupu potwierdzenia Request CSV po kliknieciu Request.")

            requested_id = self._wait_for_requested_id_feedback(page, dialog_messages, known_ids)
            if requested_id:
                log(f"Wyslano Request CSV. Requested ID: {requested_id}")
                return requested_id

            requested_id = self._wait_for_new_requested_id(page, known_ids, log)
            if not requested_id:
                self._capture_debug_snapshot(
                    page,
                    "requested_id_missing",
                    log,
                    {
                        "known_ids_count": len(known_ids),
                        "dialog_messages": dialog_messages,
                        "request_dom_popup_confirmed": request_dom_popup_confirmed,
                        "request_popup_confirmed": request_popup_confirmed,
                    },
                )
                raise PortalError("Nie udalo sie odczytac Requested ID po wyslaniu zadania CSV.")

            log(f"Wyslano Request CSV. Requested ID: {requested_id}")
            return requested_id
        finally:
            try:
                page.remove_listener("dialog", handle_dialog)
            except Exception:
                pass

    def _poll_and_download(
        self,
        page: Page,
        output_dir: Path,
        requested_id: str,
        log: Callable[[str], None],
    ) -> Path:
        deadline = time.monotonic() + self.poll_timeout_seconds

        while True:
            if time.monotonic() > deadline:
                raise PortalError(
                    f"Timeout: Requested ID {requested_id} nie pojawil sie w MyHome w czasie {self.poll_timeout_seconds}s."
                )

            myhome_html = self._load_myhome_records_html(page)
            record = self._find_record_by_requested_id(myhome_html, requested_id)
            status = ""
            if record is not None:
                status = self._extract_status_from_record(record)
                file_name = self._extract_file_name_from_record(record)
                log(
                    f"Requested ID {requested_id} status: {status or 'brak'} file: {file_name or 'brak'}"
                )

            if record is not None and status.lower() != "completed":
                log(
                    f"Requested ID {requested_id} jeszcze nie jest gotowy do pobrania, ponawiam za {self.poll_interval_seconds}s."
                )
                page.wait_for_timeout(self.poll_interval_seconds * 1_000)
                continue

            id_row = page.locator(f"tr:has-text('{requested_id}')").first
            id_cell = page.locator(f"text={requested_id}").first
            if id_row.count() > 0 or id_cell.count() > 0:
                try:
                    if id_row.count() > 0:
                        id_row.wait_for(state="visible", timeout=5_000)
                    else:
                        id_cell.wait_for(state="visible", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass

                row_status = ""
                if id_row.count() > 0:
                    try:
                        row_text = id_row.inner_text(timeout=5_000)
                    except Exception:
                        row_text = ""
                    row_status = self._extract_status_from_row_text(row_text)
                if not row_status and id_cell.count() > 0:
                    row_status = self._extract_status_from_id_row(id_cell)

                if row_status and row_status.lower() != "completed":
                    log(
                        f"Requested ID {requested_id} status z tabeli: {row_status}. "
                        f"Czekam {self.poll_interval_seconds}s."
                    )
                    page.wait_for_timeout(self.poll_interval_seconds * 1_000)
                    continue

                log(f"Requested ID {requested_id} widoczny w MyHome, uruchamiam pobranie ZIP.")

                download_actions: list[tuple[str, Callable[[], None]]] = []
                if id_row.count() > 0:
                    download_actions.append(
                        ("row_dblclick", lambda row=id_row: row.dblclick(timeout=30_000))
                    )
                    download_actions.append(
                        (
                            "row_click_enter",
                            lambda row=id_row: (
                                row.click(timeout=30_000),
                                page.keyboard.press("Enter"),
                            ),
                        )
                    )
                if id_cell.count() > 0:
                    download_actions.append(
                        ("id_cell_dblclick", lambda cell=id_cell: cell.dblclick(timeout=30_000))
                    )

                for action_name, action in download_actions:
                    try:
                        with page.expect_download(timeout=60_000) as dl_info:
                            action()
                        download = dl_info.value
                        target = output_dir / download.suggested_filename
                        download.save_as(str(target))
                        log(f"Pobrano ZIP: {target.name} ({action_name}).")
                        return target
                    except PlaywrightTimeoutError:
                        log(f"Brak eventu download po akcji {action_name}, probuje kolejna akcje.")
                        continue

            log(
                f"Requested ID {requested_id} jeszcze niedostepny, ponawiam za {self.poll_interval_seconds}s."
            )
            page.wait_for_timeout(self.poll_interval_seconds * 1_000)

    def _process_device_delete_serial(
        self,
        page: Page,
        serial: str,
        execute_delete: bool,
        log: Callable[[str], None],
        *,
        allow_recent: bool = False,
        allow_recent_before: datetime | None = None,
    ) -> DeviceDeleteReportRow:
        matches = self._search_device_by_serial(page, serial, log)
        if not matches:
            log(f"DELETE: {serial}: brak dopasowania.")
            return DeviceDeleteReportRow(
                serial=serial,
                status="not_found",
                matched_count=0,
                message="Nie znaleziono urzadzenia dla podanego numeru seryjnego.",
            )

        if len(matches) != 1:
            log(f"DELETE: {serial}: niejednoznaczne dopasowanie ({len(matches)}), pomijam.")
            return DeviceDeleteReportRow(
                serial=serial,
                status="ambiguous",
                matched_count=len(matches),
                message="Znaleziono wiecej niz jedno urzadzenie; pominieto.",
            )

        match = matches[0]
        if self._is_removing_status(match.requested_status):
            log(
                f"DELETE: {serial}: usuniecie juz trwa, "
                f"Requested Status={match.requested_status}."
            )
            return DeviceDeleteReportRow(
                serial=serial,
                status="delete_pending",
                matched_count=1,
                device_id=match.device_id,
                model=match.model,
                customer=match.customer,
                requested_status=match.requested_status,
                last_report_time=match.last_report_time,
                message="Urzadzenie jest juz w stanie Removing.",
            )

        last_report_time = self._parse_last_report_time(match.last_report_time)
        if last_report_time is None:
            log(
                f"DELETE: {serial}: brak poprawnej daty Last Report Date/Time, "
                "pomijam dla bezpieczenstwa."
            )
            return DeviceDeleteReportRow(
                serial=serial,
                status="skipped_missing_last_report",
                matched_count=1,
                device_id=match.device_id,
                model=match.model,
                customer=match.customer,
                requested_status=match.requested_status,
                last_report_time=match.last_report_time,
                message="Pominieto: brak poprawnej daty Last Report Date/Time.",
            )

        cutoff = self._subtract_months(datetime.now(), 3)
        if last_report_time.date() >= cutoff.date():
            allow_recent_by_time = (
                allow_recent_before is None or last_report_time < allow_recent_before
            )
            if not allow_recent or not allow_recent_by_time:
                message = (
                    "Pominieto: Last Report Date/Time jest z ostatnich 3 miesiecy "
                    f"(prog: {cutoff.date().isoformat()})."
                )
                if allow_recent and not allow_recent_by_time:
                    message = (
                        "Pominieto: Last Report Date/Time nie jest wczesniejszy niz "
                        f"jawny prog {allow_recent_before:%Y/%m/%d %H:%M}."
                    )
                log(
                    f"DELETE: {serial}: Last Report Date/Time={match.last_report_time} "
                    f"{message}"
                )
                return DeviceDeleteReportRow(
                    serial=serial,
                    status="skipped_recent_report",
                    matched_count=1,
                    device_id=match.device_id,
                    model=match.model,
                    customer=match.customer,
                    requested_status=match.requested_status,
                    last_report_time=match.last_report_time,
                    message=message,
                )
            log(
                f"DELETE: {serial}: Last Report Date/Time={match.last_report_time} "
                "jest z ostatnich 3 miesiecy, ale serial jest na liscie jawnego obejscia."
            )

        if not execute_delete:
            log(f"DELETE dry-run: {serial}: znaleziono {match.device_id}, bez usuwania.")
            status = "would_delete_recent_override" if allow_recent else "would_delete"
            reason = (
                "Dry-run: urzadzenie kwalifikuje sie do usuniecia przez jawne obejscie "
                "zabezpieczenia Last Report Date/Time."
                if allow_recent
                else (
                    "Dry-run: urzadzenie kwalifikuje sie do usuniecia; "
                    "Last Report Date/Time jest starszy niz 3 miesiace."
                )
            )
            return DeviceDeleteReportRow(
                serial=serial,
                status=status,
                matched_count=1,
                device_id=match.device_id,
                model=match.model,
                customer=match.customer,
                requested_status=match.requested_status,
                last_report_time=match.last_report_time,
                message=reason,
            )

        log(f"DELETE execute: {serial}: usuwam {match.device_id}.")
        removed, delete_requested = self._remove_current_device_match(page, serial, log)
        if not removed:
            return DeviceDeleteReportRow(
                serial=serial,
                status="failed",
                matched_count=1,
                device_id=match.device_id,
                model=match.model,
                customer=match.customer,
                requested_status=match.requested_status,
                last_report_time=match.last_report_time,
                message="Nie udalo sie uruchomic akcji Remove.",
            )

        if delete_requested:
            remaining = self._search_device_by_serial(page, serial, log)
            if not remaining:
                log(f"DELETE execute: {serial}: potwierdzono usuniecie.")
                return DeviceDeleteReportRow(
                    serial=serial,
                    status="deleted",
                    matched_count=1,
                    device_id=match.device_id,
                    model=match.model,
                    customer=match.customer,
                    requested_status="",
                    last_report_time=match.last_report_time,
                    message="Usunieto i potwierdzono ponownym wyszukaniem.",
                )
            if len(remaining) == 1:
                current = remaining[0]
                log(
                    f"DELETE execute: {serial}: zlecenie przyjete, "
                    f"Requested Status={current.requested_status or 'brak'}."
                )
                return DeviceDeleteReportRow(
                    serial=serial,
                    status="delete_pending",
                    matched_count=1,
                    device_id=current.device_id,
                    model=current.model,
                    customer=current.customer,
                    requested_status=current.requested_status,
                    last_report_time=current.last_report_time,
                    message="Portal przyjal zlecenie usuniecia; sprawdzic ponownie w raporcie kontrolnym.",
                )
            log(
                f"DELETE execute: {serial}: po przyjeciu zlecenia widoczne "
                f"{len(remaining)} dopasowania."
            )
            return DeviceDeleteReportRow(
                serial=serial,
                status="ambiguous",
                matched_count=len(remaining),
                device_id=match.device_id,
                model=match.model,
                customer=match.customer,
                requested_status=match.requested_status,
                last_report_time=match.last_report_time,
                message="Po przyjeciu zlecenia usuniecia znaleziono wiecej niz jedno urzadzenie.",
            )

        remaining = self._search_device_by_serial(page, serial, log)
        if not remaining:
            log(f"DELETE execute: {serial}: potwierdzono usuniecie.")
            return DeviceDeleteReportRow(
                serial=serial,
                status="deleted",
                matched_count=1,
                device_id=match.device_id,
                model=match.model,
                customer=match.customer,
                requested_status="",
                last_report_time=match.last_report_time,
                message="Usunieto i potwierdzono ponownym wyszukaniem.",
            )

        log(f"DELETE execute: {serial}: po usunieciu nadal widoczne dopasowania: {len(remaining)}.")
        self._capture_debug_snapshot(
            page,
            "device_delete_still_present",
            log,
            {"serial": serial, "remaining": len(remaining)},
        )
        return DeviceDeleteReportRow(
            serial=serial,
            status="failed",
            matched_count=len(remaining),
            device_id=match.device_id,
            model=match.model,
            customer=match.customer,
            requested_status=remaining[0].requested_status if len(remaining) == 1 else "",
            last_report_time=remaining[0].last_report_time if len(remaining) == 1 else "",
            message="Po akcji Remove urzadzenie nadal jest widoczne w wynikach.",
        )

    def _wait_for_device_delete_completion(
        self,
        page: Page,
        serial: str,
        original_match: DeviceSearchMatch,
        log: Callable[[str], None],
    ) -> DeviceDeleteReportRow:
        deadline = time.monotonic() + self.poll_timeout_seconds
        last_match = original_match

        while True:
            matches = self._search_device_by_serial(page, serial, log)
            if not matches:
                log(f"DELETE execute: {serial}: potwierdzono usuniecie.")
                return DeviceDeleteReportRow(
                    serial=serial,
                    status="deleted",
                    matched_count=1,
                    device_id=original_match.device_id,
                    model=original_match.model,
                    customer=original_match.customer,
                    requested_status="",
                    last_report_time=original_match.last_report_time,
                    message="Usunieto i potwierdzono ponownym wyszukaniem.",
                )

            if len(matches) != 1:
                log(
                    f"DELETE execute: {serial}: podczas monitorowania wykryto "
                    f"{len(matches)} dopasowan."
                )
                return DeviceDeleteReportRow(
                    serial=serial,
                    status="ambiguous",
                    matched_count=len(matches),
                    device_id=original_match.device_id,
                    model=original_match.model,
                    customer=original_match.customer,
                    requested_status=last_match.requested_status,
                    last_report_time=last_match.last_report_time,
                    message="Podczas monitorowania usuwania znaleziono wiecej niz jedno urzadzenie.",
                )

            last_match = matches[0]
            requested_status = last_match.requested_status or "brak"
            if time.monotonic() > deadline:
                log(
                    f"DELETE execute: {serial}: timeout monitorowania, "
                    f"ostatni Requested Status={requested_status}."
                )
                return DeviceDeleteReportRow(
                    serial=serial,
                    status="delete_pending",
                    matched_count=1,
                    device_id=last_match.device_id or original_match.device_id,
                    model=last_match.model or original_match.model,
                    customer=last_match.customer or original_match.customer,
                    requested_status=last_match.requested_status,
                    last_report_time=last_match.last_report_time,
                    message=(
                        "Portal przyjal zlecenie usuniecia, ale rekord nadal jest widoczny "
                        f"po {self.poll_timeout_seconds}s monitorowania."
                    ),
                )

            log(
                f"DELETE execute: {serial}: Requested Status={requested_status}; "
                f"czekam {self.poll_interval_seconds}s."
            )
            page.wait_for_timeout(self.poll_interval_seconds * 1_000)

    def _search_device_by_serial(
        self,
        page: Page,
        serial: str,
        log: Callable[[str], None],
    ) -> list[DeviceSearchMatch]:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1_000)
        self._select_device_search_target(page)
        self._fill_device_serial_search(page, serial)
        search_button = self._first_locator(
            page,
            [
                "input[type='submit'][value*='Search']",
                "input[type='button'][value*='Search']",
                "button:has-text('Search')",
                "a:has-text('Search')",
            ],
        )
        if search_button is None:
            self._capture_debug_snapshot(
                page, "device_search_button_missing", log, {"serial": serial}
            )
            raise PortalError("Nie znaleziono przycisku Search na Search.aspx.")

        search_button.click(timeout=30_000)
        try:
            page.wait_for_url("**/DeviceList.aspx**", timeout=30_000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(2_000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(1_000)

        matches = self._find_device_matches(page, serial)
        log(f"DELETE search: {serial}: znaleziono {len(matches)} dopasowan.")
        return matches

    @staticmethod
    def _select_device_search_target(page: Page) -> None:
        selectors = [
            "input[id$='wtSearchTarget_Device']",
            "input[name$='wtSearchTarget_Device']",
            "input[id*='SearchTarget_Device']",
            "input[name*='SearchTarget_Device']",
        ]
        for selector in selectors:
            target = page.locator(selector).first
            try:
                if target.count() == 0:
                    continue
                try:
                    with page.expect_response(
                        lambda response: "Search.aspx" in response.url
                        and response.request.method == "POST",
                        timeout=5_000,
                    ):
                        target.check(timeout=10_000, force=True)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(1_000)
                return
            except Exception:
                try:
                    target.click(timeout=10_000, force=True)
                    page.wait_for_timeout(1_000)
                    return
                except Exception:
                    continue

        target = RicohPortalClient._first_locator(page, ["label:has-text('Device')"])
        if target is not None:
            try:
                target.click(timeout=10_000)
                page.wait_for_timeout(500)
            except Exception:
                pass

    @staticmethod
    def _fill_device_serial_search(page: Page, serial: str) -> None:
        selectors = [
            "input[id$='wtInput_DeviceSn_Dev']",
            "input[name$='wtInput_DeviceSn_Dev']",
            "input[id*='Input_DeviceSn_Dev']",
            "input[name*='Input_DeviceSn_Dev']",
            "input[id$='wtInput_DeviceSn_App']",
            "input[name$='wtInput_DeviceSn_App']",
            "input[id*='Input_DeviceSn_App']",
            "input[name*='Input_DeviceSn_App']",
        ]
        filled_count = 0
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = locator.count()
            except Exception:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    if not item.is_visible(timeout=1_000):
                        continue
                    item.fill(serial, timeout=10_000)
                    filled_count += 1
                except Exception:
                    continue

        changed_count = page.evaluate(
            """([selectors, value]) => {
                const inputs = new Set();
                for (const selector of selectors) {
                    for (const input of document.querySelectorAll(selector)) {
                        if (typeof input.value !== 'undefined') {
                            inputs.add(input);
                        }
                    }
                }
                for (const input of inputs) {
                    input.focus();
                    input.value = value;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.blur();
                }
                return inputs.size;
            }""",
            [selectors, serial],
        )
        if filled_count == 0 and changed_count == 0:
            raise PortalError("Nie znaleziono pola Device Serial Number na Search.aspx.")

    def _find_device_matches(self, page: Page, serial: str) -> list[DeviceSearchMatch]:
        html_matches = self._extract_device_records_from_html(page.content(), serial)
        if html_matches:
            return html_matches

        row_matches: list[DeviceSearchMatch] = []
        rows = page.locator("tr").filter(has_text=serial)
        try:
            count = rows.count()
        except Exception:
            count = 0
        for index in range(count):
            row = rows.nth(index)
            try:
                if not row.is_visible(timeout=1_000):
                    continue
                row_text = " ".join(row.inner_text(timeout=5_000).split())
            except Exception:
                continue
            if row_text:
                row_matches.append(DeviceSearchMatch(device_id=serial, row_text=row_text[:500]))
        return self._dedupe_device_matches(row_matches)

    @staticmethod
    def _extract_device_records_from_html(html: str, serial: str) -> list[DeviceSearchMatch]:
        normalized = unquote_plus(unescape(html))
        decoder = json.JSONDecoder()
        matches: list[DeviceSearchMatch] = []

        for marker in re.finditer(r"\[\{", normalized):
            start = marker.start()
            window = normalized[start : start + 12_000]
            if '"DeviceId"' not in window and '"ApplianceId"' not in window:
                continue
            try:
                parsed, _ = decoder.raw_decode(normalized[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list):
                continue

            for item in parsed:
                if not isinstance(item, dict):
                    continue
                values = [
                    str(item.get("DeviceId") or ""),
                    str(item.get("ApplianceId") or ""),
                    str(item.get("DeviceSerialCode") or ""),
                    str(item.get("ApplianceSerialCode") or ""),
                ]
                if not any(serial.casefold() in value.casefold() for value in values):
                    continue
                matches.append(
                    DeviceSearchMatch(
                        device_id=str(item.get("DeviceId") or item.get("ApplianceId") or serial),
                        model=str(item.get("ModelName") or ""),
                        customer=str(item.get("Customer") or ""),
                        requested_status=str(item.get("RequestStatus") or ""),
                        last_report_time=str(item.get("LastReportTime") or ""),
                        row_text=json.dumps(item, ensure_ascii=False, sort_keys=True)[:500],
                    )
                )

        return RicohPortalClient._dedupe_device_matches(matches)

    @staticmethod
    def _dedupe_device_matches(matches: list[DeviceSearchMatch]) -> list[DeviceSearchMatch]:
        result: list[DeviceSearchMatch] = []
        seen: set[tuple[str, str, str]] = set()
        for match in matches:
            key = (match.device_id, match.model, match.customer)
            if key in seen:
                continue
            seen.add(key)
            result.append(match)
        return result

    @staticmethod
    def _is_removing_status(requested_status: str) -> bool:
        return requested_status.strip().casefold() == "removing"

    @staticmethod
    def _parse_last_report_time(value: str) -> datetime | None:
        normalized = " ".join(value.strip().split())
        if not normalized:
            return None

        formats = [
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for date_format in formats:
            try:
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue
        return None

    @staticmethod
    def _subtract_months(value: datetime, months: int) -> datetime:
        month_index = value.month - months
        year = value.year + (month_index - 1) // 12
        month = (month_index - 1) % 12 + 1
        day = min(value.day, monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    def _remove_current_device_match(
        self,
        page: Page,
        serial: str,
        log: Callable[[str], None],
    ) -> tuple[bool, bool]:
        if not self._select_current_device_match(page, serial):
            self._capture_debug_snapshot(page, "device_delete_row_missing", log, {"serial": serial})
            return False, False

        dialog_messages: list[str] = []
        delete_requested = False

        def handle_dialog(dialog) -> None:  # noqa: ANN001
            dialog_messages.append(dialog.message)
            dialog.accept()

        page.on("dialog", handle_dialog)
        try:
            for attempt in range(2):
                if not self._select_current_device_match(page, serial):
                    self._capture_debug_snapshot(
                        page,
                        "device_delete_select_failed",
                        log,
                        {"serial": serial, "attempt": attempt + 1},
                    )
                    return False, delete_requested

                remove_button = self._first_locator(
                    page,
                    [
                        "input[type='submit'][value*='Remove']",
                        "input[type='button'][value*='Remove']",
                        "button:has-text('Remove')",
                        "a:has-text('Remove')",
                    ],
                )
                if remove_button is None:
                    if attempt == 0:
                        self._capture_debug_snapshot(
                            page,
                            "device_delete_button_missing",
                            log,
                            {"serial": serial},
                        )
                    return attempt > 0, delete_requested

                remove_button.click(timeout=30_000)
                log(f"DELETE execute: {serial}: kliknieto Remove (proba {attempt + 1}).")
                delete_requested = self._confirm_delete_modal_if_present(page) or delete_requested
                page.wait_for_timeout(2_000)

                if delete_requested or not self._page_has_device_match(page, serial):
                    return True, delete_requested
        finally:
            try:
                page.remove_listener("dialog", handle_dialog)
            except Exception:
                pass

        if dialog_messages:
            log(f"DELETE execute: {serial}: potwierdzono dialog przegladarki.")
        return True, delete_requested

    @staticmethod
    def _select_current_device_match(page: Page, serial: str) -> bool:
        selected_count = page.evaluate(
            """(serial) => {
                const jq = window.jQuery || window.$;
                if (!jq) {
                    return 0;
                }

                const list = jq('#DeviceList_ListView');
                if (!list.length || typeof list.getGrid !== 'function') {
                    return 0;
                }

                const grid = list.getGrid();
                const rowCount = typeof grid.countRows === 'function' ? grid.countRows() : 0;
                let targetRow = -1;
                let targetRecord = null;

                for (let row = 0; row < rowCount; row += 1) {
                    const record = typeof list.getRecord === 'function' ? list.getRecord(row) : null;
                    if (!record) {
                        continue;
                    }
                    const values = [
                        record.DeviceId || '',
                        record.ApplianceId || '',
                        record.DeviceSerialCode || '',
                        record.ApplianceSerialCode || '',
                    ].map((value) => String(value).toLowerCase());
                    const needle = String(serial).toLowerCase();
                    if (values.some((value) => value.includes(needle) || needle.includes(value))) {
                        targetRow = row;
                        targetRecord = record;
                        break;
                    }
                }

                if (targetRow < 0 || !targetRecord) {
                    return 0;
                }

                if (typeof grid.selectCell === 'function') {
                    grid.selectCell(targetRow, 0, targetRow, 0);
                }
                if (typeof list.clearSelected === 'function') {
                    list.clearSelected();
                }
                if (typeof list.toggleSelected === 'function') {
                    list.toggleSelected(targetRow, true);
                }

                const cell = typeof grid.getCell === 'function' ? grid.getCell(targetRow, 0) : null;
                if (cell) {
                    const className = grid.getSettings().selectedClassName || 'ListView_Selected';
                    jq(cell).siblings().addBack().addClass(className);
                }

                let selected = [];
                if (typeof list.getSelected === 'function') {
                    selected = list.getSelected() || [];
                }
                if (!Array.isArray(selected) || selected.length === 0) {
                    selected = [targetRecord];
                }

                jq("input[id$='wtInput_Selected_Records']").val(JSON.stringify(selected));
                return selected.length;
            }""",
            serial,
        )
        return int(selected_count or 0) == 1

    @staticmethod
    def _confirm_delete_modal_if_present(page: Page) -> bool:
        confirmed = RicohPortalClient._click_first_visible(
            page,
            [
                "div.ui-dialog:has-text('The selected Device will be deleted') button:has-text('OK')",
                "div.ui-dialog:has-text('Are you sure') button:has-text('OK')",
                "div.ui-dialog:has-text('Are you sure') button:has-text('Yes')",
            ],
            timeout=10_000,
        )
        if confirmed:
            page.wait_for_timeout(1_000)

        request_received = RicohPortalClient._click_first_visible(
            page,
            [
                "div.ui-dialog:has-text('Request received') button:has-text('OK')",
                "div.ui-dialog:has-text('Request received') input[type='button'][value='OK']",
            ],
            timeout=10_000 if confirmed else 2_000,
        )
        if request_received:
            page.wait_for_timeout(2_000)
        return request_received

    @staticmethod
    def _click_first_visible(page: Page, selectors: list[str], timeout: int) -> bool:
        for selector in selectors:
            button = page.locator(selector).first
            try:
                button.wait_for(state="visible", timeout=timeout)
                button.click(timeout=timeout)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    @staticmethod
    def _first_visible_device_row(page: Page, serial: str) -> Locator | None:
        rows = page.locator("tr").filter(has_text=serial)
        try:
            count = rows.count()
        except Exception:
            return None
        for index in range(count):
            row = rows.nth(index)
            try:
                if row.is_visible(timeout=1_000):
                    return row
            except Exception:
                continue
        return None

    @staticmethod
    def _page_has_visible_device_row(page: Page, serial: str) -> bool:
        return RicohPortalClient._first_visible_device_row(page, serial) is not None

    @staticmethod
    def _page_has_device_match(page: Page, serial: str) -> bool:
        html = page.content()
        return bool(RicohPortalClient._extract_device_records_from_html(html, serial))

    def _collect_requested_ids(self, page: Page) -> set[str]:
        """Pobiera aktualna liste RequestedId z MyHome przed wyslaniem nowego zadania."""
        return self._extract_requested_ids_from_html(self._load_myhome_records_html(page))

    def _wait_for_new_requested_id(
        self,
        page: Page,
        known_ids: set[str],
        log: Callable[[str], None],
    ) -> str | None:
        """Czeka na pojawienie sie nowego RequestedId po kliknieciu Request."""
        deadline = time.monotonic() + 120
        last_ids: set[str] = set()
        while time.monotonic() <= deadline:
            current_ids = self._extract_requested_ids_from_html(
                self._load_myhome_records_html(page)
            )
            last_ids = current_ids
            new_ids = sorted(current_ids - known_ids, reverse=True)
            if new_ids:
                return new_ids[0]
            log("Brak nowego Requested ID w MyHome, ponawiam odczyt.")
            page.wait_for_timeout(2_000)
        if last_ids:
            log("Brak nowego Requested ID po Request; tabela MyHome nie pokazuje nowego wpisu.")
        return None

    def _load_myhome_records_html(self, page: Page) -> str:
        """Laduje MyHome i wykonuje SearchMyRequest, aby odswiezyc dane tabeli."""
        page.goto(MY_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1_000)

        search_button = self._first_locator(
            page,
            [
                "input[id$='wtButton_SearchMyRequest']",
                "input[value*='Search']",
                "button:has-text('Search')",
            ],
        )
        if search_button is not None:
            try:
                search_button.click(timeout=15_000)
                page.wait_for_timeout(1_000)
            except Exception:
                pass

        return page.content()

    @staticmethod
    def _confirm_request_modal_if_present(page: Page, log: Callable[[str], None]) -> bool:
        """Potwierdza popup 'Are you sure?' po kliknieciu Request (jesli wystapi)."""
        try:
            page.get_by_text("Are you sure?", exact=False).first.wait_for(
                state="visible", timeout=5_000
            )
        except PlaywrightTimeoutError:
            return False

        ok_button = RicohPortalClient._first_locator(
            page,
            [
                "button:has-text('OK')",
                "input[type='button'][value='OK']",
                "input[type='submit'][value='OK']",
            ],
        )
        if ok_button is None:
            log("Wykryto popup potwierdzenia Request, ale nie znaleziono przycisku OK.")
            return False

        ok_button.click(timeout=20_000)
        page.wait_for_timeout(800)
        log("Potwierdzono popup Request CSV przyciskiem OK.")
        return True

    def _capture_debug_snapshot(
        self,
        page: Page,
        reason: str,
        log: Callable[[str], None],
        metadata: dict[str, object] | None = None,
    ) -> Path | None:
        """Zapisuje lokalny snapshot strony portalu dla diagnostyki awarii."""
        safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "_", reason).strip("_") or "snapshot"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_dir = self.debug_dir / f"{timestamp}_{safe_reason}"

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": timestamp,
                "reason": reason,
                "url": page.url,
            }
            if metadata:
                payload.update(metadata)

            (target_dir / "metadata.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            try:
                (target_dir / "page.html").write_text(
                    page.content(),
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception as exc:  # noqa: BLE001
                (target_dir / "page_error.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )

            try:
                page.screenshot(path=str(target_dir / "screenshot.png"), full_page=True)
            except Exception as exc:  # noqa: BLE001
                (target_dir / "screenshot_error.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )

            log(f"Zapisano diagnostyke portalu Ricoh: {target_dir.resolve()}")
            return target_dir
        except Exception as exc:  # noqa: BLE001
            log(f"Nie udalo sie zapisac diagnostyki portalu Ricoh: {type(exc).__name__}: {exc}")
            return None

    @staticmethod
    def _wait_for_requested_id_feedback(
        page: Page, dialog_messages: list[str], known_ids: set[str]
    ) -> str | None:
        """Po kliknieciu Request probuje odczytac Requested ID z feedbacku strony/dialogu."""
        deadline = time.monotonic() + 8
        while time.monotonic() <= deadline:
            page_html = page.content()
            text = "\n".join(dialog_messages + [page_html])

            feedback_ids = RicohPortalClient._extract_requested_ids_from_text(text)
            new_feedback_ids = sorted(feedback_ids - known_ids, reverse=True)
            if new_feedback_ids:
                return new_feedback_ids[0]

            page_ids = RicohPortalClient._extract_requested_ids_from_html(page_html)
            new_page_ids = sorted(page_ids - known_ids, reverse=True)
            if new_page_ids:
                return new_page_ids[0]

            page.wait_for_timeout(1_000)
        return None

    @staticmethod
    def _extract_requested_ids_from_text(text: str) -> set[str]:
        ids: set[str] = set()
        ids.update(REQUESTED_ID_RE.findall(text))
        ids.update(REQUESTED_ID_VALUE_RE.findall(text))
        ids.update(REQUESTED_ID_TOKEN_RE.findall(text))
        return {value for value in ids if RicohPortalClient._is_valid_requested_id(value)}

    @staticmethod
    def _extract_requested_id(text: str) -> str | None:
        return RicohPortalClient._pick_latest_requested_id(
            RicohPortalClient._extract_requested_ids_from_text(text)
        )

    @staticmethod
    def _extract_requested_ids_from_html(html: str) -> set[str]:
        ids: set[str] = set()
        records = RicohPortalClient._extract_records_from_html(html)
        if records:
            for item in records:
                requested_id = RicohPortalClient._extract_requested_id_from_record(item)
                if requested_id:
                    ids.add(requested_id)
        ids.update(RicohPortalClient._extract_requested_ids_from_text(html))
        return ids

    @staticmethod
    def _extract_records_from_html(html: str) -> list[dict]:
        match = MYHOME_RECORDS_RE.search(html)
        if match is None:
            return []
        raw_json = match.group(1)
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _find_record_by_requested_id(html: str, requested_id: str) -> dict | None:
        for record in RicohPortalClient._extract_records_from_html(html):
            record_requested_id = RicohPortalClient._extract_requested_id_from_record(record)
            if record_requested_id == requested_id:
                return record
        return None

    @staticmethod
    def _extract_requested_id_from_record(record: dict) -> str | None:
        for key, value in record.items():
            lower_key = str(key).lower()
            if "request" not in lower_key or "id" not in lower_key:
                continue
            match = REQUESTED_ID_TOKEN_RE.search(str(value))
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _extract_status_from_record(record: dict) -> str:
        for key, value in record.items():
            if "status" in str(key).lower():
                return str(value).strip()
        return ""

    @staticmethod
    def _extract_file_name_from_record(record: dict) -> str:
        for key, value in record.items():
            lower_key = str(key).lower()
            if "file" in lower_key and "name" in lower_key:
                return str(value).strip()
        return ""

    @staticmethod
    def _is_valid_requested_id(value: str) -> bool:
        return REQUESTED_ID_TOKEN_RE.fullmatch(value) is not None

    @staticmethod
    def _extract_status_from_id_row(id_cell: Locator) -> str:
        """Czyta status z wiersza tabeli MyHome zawierajacego Requested ID."""
        try:
            row_text = id_cell.locator("xpath=ancestor::tr[1]").inner_text(timeout=5_000)
        except Exception:
            return ""

        return RicohPortalClient._extract_status_from_row_text(row_text)

    @staticmethod
    def _extract_status_from_row_text(row_text: str) -> str:
        normalized = " ".join(row_text.split()).lower()
        status_candidates = [
            "waiting for transfer",
            "processing",
            "pending",
            "running",
            "completed",
            "failed",
            "error",
            "canceled",
            "cancelled",
        ]
        for candidate in status_candidates:
            if candidate in normalized:
                return candidate
        return ""

    @staticmethod
    def _set_date_range_from_yesterday_to_today(page: Page, log: Callable[[str], None]) -> None:
        """Ustawia zakres dat Request CSV: od wczoraj do dzis (MM/DD/YYYY)."""
        today_dt = datetime.now()
        yesterday_dt = today_dt - timedelta(days=1)
        start_day = yesterday_dt.strftime("%m/%d/%Y")
        end_day = today_dt.strftime("%m/%d/%Y")
        start_selector = "input[id$='wtInput_TargetMonthStartCoIm']"
        end_selector = "input[id$='wtInput_TargetMonthEndCoIm']"

        page.wait_for_selector(start_selector, state="visible", timeout=30_000)
        page.wait_for_selector(end_selector, state="visible", timeout=30_000)

        start = page.locator(start_selector).first
        end = page.locator(end_selector).first

        start.fill(start_day)
        end.fill(end_day)
        start.dispatch_event("change")
        end.dispatch_event("change")
        log(f"Ustawiono zakres dat Request CSV: {start_day} - {end_day}.")

    @staticmethod
    def _pick_latest_requested_id(ids: set[str]) -> str | None:
        numeric_ids = [value for value in ids if RicohPortalClient._is_valid_requested_id(value)]
        if not numeric_ids:
            return None
        return max(numeric_ids, key=int)

    @staticmethod
    def _first_locator(page: Page, selectors: list[str]) -> Locator | None:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    return locator
            except Exception:
                # Przejscia miedzy stronami potrafia niszczyc kontekst JS.
                continue
        return None
