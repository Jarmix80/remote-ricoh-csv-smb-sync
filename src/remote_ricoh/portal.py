"""Automatyzacja przegladarki: ADFS -> Request CSV -> MyHome -> pobranie ZIP."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

REQUEST_CSV_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx"
MY_HOME_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/MyHome.aspx"
REQUESTED_ID_RE = re.compile(r"Requested\s*ID\s*:\s*(\d+)", re.IGNORECASE)
REQUESTED_ID_VALUE_RE = re.compile(
    r'(?:\\?")?RequestedId(?:\\?")?\s*:\s*(?:\\?")?(\d{10,})(?:\\?")?'
)
MYHOME_RECORDS_RE = re.compile(r"var\s+records\s*=\s*(\[[\s\S]*?\]);", re.IGNORECASE)


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
        self._set_date_range_to_today(page, log)

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
            page.wait_for_timeout(1_500)

            requested_id = self._extract_requested_id("\n".join(dialog_messages + [page.content()]))
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
            if record is not None:
                status = record.get("Status", "")
                file_name = record.get("FileName", "")
                log(
                    f"Requested ID {requested_id} status: {status or 'brak'} file: {file_name or 'brak'}"
                )

            if record is not None and record.get("Status", "").lower() != "completed":
                log(
                    f"Requested ID {requested_id} jeszcze nie jest gotowy do pobrania, ponawiam za {self.poll_interval_seconds}s."
                )
                page.wait_for_timeout(self.poll_interval_seconds * 1_000)
                continue

            id_cell = page.locator(f"text={requested_id}").first
            if id_cell.count() > 0:
                try:
                    id_cell.wait_for(state="visible", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass

                log(f"Requested ID {requested_id} widoczny w MyHome, uruchamiam pobranie ZIP.")
                with page.expect_download(timeout=60_000) as dl_info:
                    id_cell.dblclick(timeout=30_000)
                download = dl_info.value
                target = output_dir / download.suggested_filename
                download.save_as(str(target))
                log(f"Pobrano ZIP: {target.name}")
                return target

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
        fallback_id = self._pick_latest_requested_id(last_ids)
        if fallback_id:
            log("Brak nowego Requested ID; uzywam najnowszego ID z tabeli MyHome jako fallback.")
            return fallback_id
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
    def _extract_requested_id(text: str) -> str | None:
        match = REQUESTED_ID_RE.search(text)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_requested_ids_from_html(html: str) -> set[str]:
        records = RicohPortalClient._extract_records_from_html(html)
        if records:
            return {
                str(item.get("RequestedId", "")).strip()
                for item in records
                if str(item.get("RequestedId", "")).strip()
            }
        return set(REQUESTED_ID_VALUE_RE.findall(html))

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
            if str(record.get("RequestedId", "")).strip() == requested_id:
                return record
        return None

    @staticmethod
    def _set_date_range_to_today(page: Page, log: Callable[[str], None]) -> None:
        """Ustawia zakres dat (od/do) na dzisiejsza date w formacie MM/DD/YYYY."""
        today = datetime.now().strftime("%m/%d/%Y")
        start_selector = "input[id$='wtInput_TargetMonthStartCoIm']"
        end_selector = "input[id$='wtInput_TargetMonthEndCoIm']"

        page.wait_for_selector(start_selector, state="visible", timeout=30_000)
        page.wait_for_selector(end_selector, state="visible", timeout=30_000)

        start = page.locator(start_selector).first
        end = page.locator(end_selector).first

        start.fill(today)
        end.fill(today)
        start.dispatch_event("change")
        end.dispatch_event("change")
        log(f"Ustawiono zakres dat Request CSV: {today} - {today}.")

    @staticmethod
    def _pick_latest_requested_id(ids: set[str]) -> str | None:
        numeric_ids = [value for value in ids if value.isdigit()]
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
