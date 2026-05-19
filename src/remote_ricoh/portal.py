"""Automatyzacja przegladarki: ADFS -> Request CSV -> MyHome -> pobranie ZIP."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

REQUEST_CSV_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx"
MY_HOME_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/MyHome.aspx"
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


@dataclass(slots=True)
class RicohPortalClient:
    """Klient Playwright do pobrania ZIP z CSV licznikow."""

    login: str
    password: str
    poll_timeout_seconds: int
    poll_interval_seconds: int
    headless: bool = True

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
                raise PortalError("Nie znaleziono przycisku Request na RequestCsv.aspx.")

            request_button.click(timeout=30_000)
            self._confirm_request_modal_if_present(page, log)
            requested_id = self._wait_for_requested_id_feedback(page, dialog_messages, known_ids)
            if requested_id:
                log(f"Wyslano Request CSV. Requested ID: {requested_id}")
                return requested_id

            requested_id = self._wait_for_new_requested_id(page, known_ids, log)
            if not requested_id:
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
    def _confirm_request_modal_if_present(page: Page, log: Callable[[str], None]) -> None:
        """Potwierdza popup 'Are you sure?' po kliknieciu Request (jesli wystapi)."""
        try:
            page.get_by_text("Are you sure?", exact=False).first.wait_for(
                state="visible", timeout=5_000
            )
        except PlaywrightTimeoutError:
            return

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
            return

        ok_button.click(timeout=20_000)
        page.wait_for_timeout(800)
        log("Potwierdzono popup Request CSV przyciskiem OK.")

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
