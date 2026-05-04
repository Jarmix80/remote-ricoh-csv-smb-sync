"""Automatyzacja przegladarki: ADFS -> Request CSV -> MyHome -> pobranie ZIP."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ADFS_START_URL = "https://adfs.jp.ricoh.com/adfs/ls/"
REQUEST_CSV_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx"
MY_HOME_URL = "https://nslep.osp.ricoh.co.jp/atremotecenter/MyHome.aspx"
REQUESTED_ID_RE = re.compile(r"Requested\s*ID\s*:\s*(\d+)", re.IGNORECASE)


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
                requested_id = self._create_csv_request(page, log)
                zip_path = self._poll_and_download(page, output_dir, requested_id, log)
                return DownloadResult(requested_id=requested_id, zip_path=zip_path)
            finally:
                context.close()
                browser.close()

    def _open_adfs_and_login(self, page: Page, log: Callable[[str], None]) -> None:
        log(f"Otwieram ADFS: {ADFS_START_URL}")
        page.goto(ADFS_START_URL, wait_until="domcontentloaded", timeout=60_000)

        partner = page.get_by_text("Partner", exact=False).first
        partner.click(timeout=30_000)
        log("Kliknieto opcje Partner.")

        user_input = self._first_locator(
            page,
            [
                "input[type='email']",
                "input[name='UserName']",
                "input[name='username']",
                "input[id*='user']",
                "input[name*='user']",
            ],
        )
        pass_input = self._first_locator(
            page,
            [
                "input[type='password']",
                "input[name='Password']",
                "input[name='password']",
                "input[id*='pass']",
            ],
        )

        if user_input is None or pass_input is None:
            raise PortalError("Nie znaleziono pol logowania na stronie ADFS.")

        user_input.fill(self.login)
        pass_input.fill(self.password)

        submit = self._first_locator(
            page,
            [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Sign in')",
                "button:has-text('Log in')",
            ],
        )
        if submit is None:
            raise PortalError("Nie znaleziono przycisku submit logowania.")

        submit.click()
        page.wait_for_load_state("domcontentloaded", timeout=60_000)
        log("Logowanie zakonczone, przechodze do strony Request CSV.")

    def _create_csv_request(self, page: Page, log: Callable[[str], None]) -> str:
        page.goto(REQUEST_CSV_URL, wait_until="domcontentloaded", timeout=60_000)

        dialog_messages: list[str] = []

        def handle_dialog(dialog) -> None:  # noqa: ANN001
            dialog_messages.append(dialog.message)
            dialog.accept()

        page.on("dialog", handle_dialog)

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
        if not requested_id:
            raise PortalError("Nie udalo sie odczytac Requested ID po wyslaniu zadania CSV.")

        log(f"Wyslano Request CSV. Requested ID: {requested_id}")
        return requested_id

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

            page.goto(MY_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
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

    @staticmethod
    def _extract_requested_id(text: str) -> str | None:
        match = REQUESTED_ID_RE.search(text)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _first_locator(page: Page, selectors: list[str]) -> Locator | None:
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() > 0:
                return locator
        return None
