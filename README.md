# remote_ricoh

[![Sponsorzy](https://img.shields.io/github/sponsors/Jarmix80?style=for-the-badge)](https://github.com/sponsors/Jarmix80)

## PL
Automatyczne pobieranie licznikow CSV z portalu Ricoh i zapis na udziale SMB.

### Co robi proces
1. Start z `https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx`.
2. Przejscie przez logowanie ADFS (krok `Partner` + formularz logowania).
3. Ustawienie zakresu dat na dzien biezacy (`MM/DD/YYYY` od-do).
4. Klikniecie `Request` i utworzenie zadania CSV.
5. Monitoring `MyHome` (odswiezanie `SearchMyRequest`) do statusu `Completed`.
6. Pobranie ZIP i rozpakowanie plikow:
   - `DPLAC`
   - `DPLAC_Not_obtained`
7. Zapis na SMB jako:
   - `DPLAC_dd-mm-rrrr.csv`
   - `DPLAC_Not_obtained_dd-mm-rrrr.csv`
8. Zapis logu dziennego na SMB: `log/ricoh_YYYY-MM-DD.log`.

### Bezpieczenstwo danych
- Sekrety sa trzymane tylko w lokalnym `.env` (plik ignorowany przez Git).
- Do repo trafia wyłącznie `.env.example` bez wartosci wrazliwych.
- Katalogi lokalnych artefaktow (`.codex/`, `.debug/`, `logs/`, `.state/`) sa ignorowane.
- Nie zapisuj hasel/tokenow w kodzie, commitach ani issue/PR.

### Licencja i feedback
- Projekt jest udostepniony na licencji MIT (plik `LICENSE`).
- Zapraszamy do komentarzy, issue i propozycji usprawnien.

### Wsparcie projektu
- GitHub Sponsors: `Jarmix80`
- Ko-fi: `https://ko-fi.com/jarmix80`
- PayPal: `jarmix80`
- Kazda uwaga, komentarz i propozycja rozwoju sa mile widziane.

### Wymagania
- Python 3.12+
- Playwright + Chromium
- Dostep SMB do katalogu docelowego

### Konfiguracja
Utworz lokalny `.env` na podstawie `.env.example`:

```env
login_ricoh=
pass_ricoh=
sciezka_remote=//serwer/udzial/katalog
user_smb=
pass_smb=
```

### Instalacja
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### Uruchomienie
Pelny run:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env
```

Diagnostyka SMB (bez logowania Ricoh):
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dry-run
```

Kody wyjscia:
- `0` sukces
- `1` blad wykonania
- `2` blad konfiguracji
- `3` aktywny lockfile

### Cron
Instalacja wpisu cron (06:00 codziennie):
```bash
./scripts/install_cron.sh
```

### Testy i jakosc
```bash
source .venv/bin/activate
ruff check .
black --check .
pytest
```

---

## EN
Automatic download of Ricoh meter CSV files and saving them to an SMB share.

### What the process does
1. Starts from `https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx`.
2. Goes through ADFS login flow (`Partner` step + login form).
3. Sets date range to current day (`MM/DD/YYYY`, from-to).
4. Clicks `Request` to create CSV job.
5. Monitors `MyHome` (refresh via `SearchMyRequest`) until status is `Completed`.
6. Downloads ZIP and extracts:
   - `DPLAC`
   - `DPLAC_Not_obtained`
7. Saves to SMB as:
   - `DPLAC_dd-mm-yyyy.csv`
   - `DPLAC_Not_obtained_dd-mm-yyyy.csv`
8. Writes daily log on SMB: `log/ricoh_YYYY-MM-DD.log`.

### Sensitive data handling
- Secrets are stored only in local `.env` (ignored by Git).
- Repository includes only `.env.example` without sensitive values.
- Local artifacts (`.codex/`, `.debug/`, `logs/`, `.state/`) are ignored.
- Do not put credentials/tokens in source code, commits, issues, or PRs.

### License and feedback
- The project is released under the MIT License (`LICENSE` file).
- Feedback is welcome: comments, issues, and improvement suggestions.

### Support the project
- GitHub Sponsors: `Jarmix80`
- Ko-fi: `https://ko-fi.com/jarmix80`
- PayPal: `jarmix80`
- Comments, issue reports, and improvement ideas are welcome.

### Requirements
- Python 3.12+
- Playwright + Chromium
- SMB access to target directory

### Configuration
Create local `.env` from `.env.example`:

```env
login_ricoh=
pass_ricoh=
sciezka_remote=//server/share/folder
user_smb=
pass_smb=
```

### Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### Run
Full run:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env
```

SMB diagnostics only (no Ricoh login):
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dry-run
```

Exit codes:
- `0` success
- `1` runtime error
- `2` config error
- `3` lockfile active

### Cron
Install daily cron job (06:00):
```bash
./scripts/install_cron.sh
```

### Tests and quality
```bash
source .venv/bin/activate
ruff check .
black --check .
pytest
```
