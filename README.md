# remote_ricoh

[![Sponsorzy](https://img.shields.io/github/sponsors/Jarmix80?style=for-the-badge)](https://github.com/sponsors/Jarmix80)

## PL
Automatyczne pobieranie licznikow CSV z portalu Ricoh, zapis na udziale SMB oraz import `DPLAC` do Firebird `CMAIL`.

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
8. Import `DPLAC_dd-mm-rrrr.csv` do tabeli Firebird `CMAIL`.
9. Zapis logu dziennego na SMB: `log/ricoh_YYYY-MM-DD.log`.

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
- Biblioteka klienta Firebird dostepna w systemie (`libfbclient`/zgodny klient legacy)

### Konfiguracja
Utworz lokalny `.env` na podstawie `.env.example`:

```env
login_ricoh=
pass_ricoh=
sciezka_remote=//serwer/udzial/katalog
user_smb=
pass_smb=
FB_HOST=127.0.0.1
FB_PORT=3050
FB_USER=SYSDBA
FB_PASSWORD=masterkey
FB_DATABASE=BAZAMS_TEST
FB_CHARSET=WIN1250
FB_ROLE=
FB_LOCAL_COPY_PATH=
```

Przelaczenie na inna baze:
- test/local alias na serwerze Firebird: zostaw `FB_MODE=network` i zmien `FB_HOST` / `FB_DATABASE`
- produkcja na Windows Server 2022: zostaw `FB_MODE=network`, ustaw host produkcyjny, port i alias lub pelna sciezke `.FDB`
- lokalna kopia pliku `.FDB`: ustaw `FB_MODE=local` i wpisz sciezke do pliku w `FB_LOCAL_COPY_PATH`
- sekcja `FB_*` jest opcjonalna: gdy jej brakuje albo Firebird jest niedostepny, CSV nadal zostana zapisane na SMB, a import do `CMAIL` zostanie tylko pominiety z ostrzezeniem w logu

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

Diagnostyka SMB + Firebird (bez logowania Ricoh):
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dry-run
```

Tryb post-download dla juz pobranego `DPLAC.csv`:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dplac-csv /sciezka/do/DPLAC.csv
```

Tryb post-download z opcjonalnym `DPLAC_Not_obtained.csv`:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env \
  --dplac-csv /sciezka/do/DPLAC.csv \
  --dplac-not-obtained-csv /sciezka/do/DPLAC_Not_obtained.csv
```

Kody wyjscia:
- `0` sukces
- `1` blad wykonania
- `2` blad konfiguracji
- `3` aktywny lockfile

### Cron
Instalacja wpisow cron:
- codziennie o `06:00`
- oraz po restarcie serwera (`@reboot`, start po 180s)
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
Automatic download of Ricoh meter CSV files, saving them to an SMB share, and importing `DPLAC` into Firebird `CMAIL`.

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
8. Imports `DPLAC_dd-mm-yyyy.csv` into the Firebird `CMAIL` table.
9. Writes daily log on SMB: `log/ricoh_YYYY-MM-DD.log`.

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
- Firebird client library available on the host (`libfbclient` or compatible legacy client)

### Configuration
Create local `.env` from `.env.example`:

```env
login_ricoh=
pass_ricoh=
sciezka_remote=//server/share/folder
user_smb=
pass_smb=
FB_HOST=127.0.0.1
FB_PORT=3050
FB_USER=SYSDBA
FB_PASSWORD=masterkey
FB_DATABASE=BAZAMS_TEST
FB_CHARSET=WIN1250
FB_ROLE=
FB_LOCAL_COPY_PATH=
```

Switching to another database:
- test/local alias on a Firebird server: keep `FB_MODE=network` and change `FB_HOST` / `FB_DATABASE`
- production on Windows Server 2022: keep `FB_MODE=network`, set the production host, port, and alias or full `.FDB` path
- local `.FDB` copy: set `FB_MODE=local` and point `FB_LOCAL_COPY_PATH` to the file
- the `FB_*` section is optional: if it is missing or Firebird is unavailable, CSV files are still written to SMB and only the `CMAIL` import is skipped with a warning in the log

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

SMB + Firebird diagnostics only (no Ricoh login):
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dry-run
```

Post-download mode for an already downloaded `DPLAC.csv`:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dplac-csv /path/to/DPLAC.csv
```

Post-download mode with optional `DPLAC_Not_obtained.csv`:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env \
  --dplac-csv /path/to/DPLAC.csv \
  --dplac-not-obtained-csv /path/to/DPLAC_Not_obtained.csv
```

Exit codes:
- `0` success
- `1` runtime error
- `2` config error
- `3` lockfile active

### Cron
Install cron entries:
- daily at `06:00`
- and after server restart (`@reboot`, starts after 180s)
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
