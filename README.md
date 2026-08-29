# remote_ricoh

[![Sponsorzy](https://img.shields.io/github/sponsors/Jarmix80?style=for-the-badge)](https://github.com/sponsors/Jarmix80)

## PL
Automatyczne pobieranie licznikow CSV z portalu Ricoh, zapis na udziale SMB oraz import `DPLAC` do Firebird `CMAIL`.

### Co robi proces
1. Start z `https://nslep.osp.ricoh.co.jp/atremotecenter/RequestCsv.aspx`.
2. Przejscie przez logowanie ADFS (krok `Partner` + formularz logowania).
3. Ustawienie zakresu dat od wczoraj do dzis (`MM/DD/YYYY` od-do).
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
- sekcja `FB_*` jest opcjonalna: przy braku konfiguracji import do `CMAIL` zostanie pominiety; gdy skonfigurowany Firebird jest niedostepny, CSV pozostana zapisane na SMB, ale proces zakonczy sie bledem i wysle alert e-mail

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

Dry-run usuwania urzadzen Ricoh po numerach seryjnych:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --delete-devices seriale.txt
```

Realne usuniecie wymaga jawnej flagi:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --delete-devices seriale.txt --execute-delete
```

Plik `seriale.txt` zawiera jeden numer seryjny w linii. Obslugiwany jest tez CSV
z jedna kolumna albo kolumna `serial`. Raport zapisywany jest lokalnie w
`.debug/ricoh_device_delete/` i zawiera kolumny `requested_status` oraz
`last_report_time`. Automat nie usuwa urzadzenia, jesli `Last Report Date/Time`
jest z ostatnich 3 miesiecy albo nie da sie poprawnie odczytac tej daty. Po
przyjeciu zlecenia usuniecia portal przechodzi przez `Requested Status = Removing`;
automat monitoruje ten stan do znikniecia urzadzenia albo zapisuje `delete_pending`
po timeoutcie.

Snapshot zlecen serwisowych Firebird przed reczna weryfikacja:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --service-order-snapshot zlecenia.txt
```

Porownanie snapshotow po recznym zamknieciu przykladowego zlecenia:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env \
  --service-order-diff before.csv after.csv
```

Dry-run dopisania wykonania i zamkniecia zlecen:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env \
  --close-service-orders zlecenia.txt \
  --remote-status-report .debug/ricoh_device_delete/final_batch_report_YYYYMMDD_HHMMSS.csv
```

Realny zapis wymaga flagi oraz jawnej zmiennej ochronnej:
```bash
source .venv/bin/activate
FB_ALLOW_WRITES=1 python -m remote_ricoh.run --env-file .env \
  --close-service-orders zlecenia.txt \
  --remote-status-report .debug/ricoh_device_delete/final_batch_report_YYYYMMDD_HHMMSS.csv \
  --execute-service-orders
```

`zlecenia.txt` moze zawierac `14331/2025`, `serial:G696M313134` albo
`problem:odpiąć REMOTE`. CSV obsluguje kolumny `order`, `serial`,
`problem_contains` i `allow_multiple`. Zlecenia sa identyfikowane po
`ZLECENIE.ID_ZLECENIE + ROK`, a raporty zapisywane sa w
`.debug/ricoh_service_orders/`. Automat dopisuje do `WYKONANIE` tekst
`Urządzenie usunięte z Remote.`, przechodzi przez `STAN='ZR'`, finalnie ustawia
`STAN='Z'` i `DATA_Z=CURRENT_DATE`, a do `OPERATOR` dopisuje
`Edytował: Marcin,Zamknął :Marcin`; nie zmienia `TECHNIK`.

Dla zlecen zamykanych po pomocy zdalnej mozna podac wlasny wpis i zachowac
metadane zlecenia bez zmian:
```bash
FB_ALLOW_WRITES=1 python -m remote_ricoh.run --env-file .env \
  --close-service-orders zlecenia.txt \
  --service-order-repair-text "pomoc zdalna" \
  --preserve-service-order-metadata \
  --execute-service-orders
```
W tym wariancie aktualizowane sa wylacznie `WYKONANIE` oraz `STAN` przez `ZR`
do `Z`; `OPERATOR`, `TECHNIK` i `DATA_Z` sa zachowane. Firebird moze niezaleznie
zaktualizowac techniczne pola audytowe `EDITCNT`, `EDITDATE` i `EDITTIME`.

### Cykliczny automat REMOTE
Tryb testowy codziennego skanu nowych zlecen `TECHNIK=REMOTE`:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --remote-auto-scan
```

Tryb testowy tygodniowej kolejki oczekujacej:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --remote-auto-weekly
```

Po poprawnym skanie tygodniowym automat wysyla na `EMAIL_WEEKLY_REPORT_TO`
podsumowanie oraz zalacza lokalny raport CSV. Przy bledzie skanu wysyla alert.
Wymagane ustawienia SMTP w `.env`:
```env
EMAIL_HOST=ksero-partner.com.pl
EMAIL_PORT=587
EMAIL_USERNAME=system@ksero-partner.com.pl
EMAIL_PASSWORD=
EMAIL_SENDER_ADDRESS=system@ksero-partner.com.pl
EMAIL_SENDER_NAME=Remote Ricoh
EMAIL_USE_SSL=false
EMAIL_USE_TLS=true
EMAIL_WEEKLY_REPORT_TO=marcin@ksero-partner.com.pl
```

Domyslna lokalna baza kolejki to `local/remote_auto/remote_auto.sqlite`.
Automat porownuje `Last Report Date/Time` z progiem jednego miesiaca. Swieze
odczyty trafiaja do `waiting_recent` i sa sprawdzane raz w tygodniu; starsze
odczyty przechodza do `ready_delete`. W trybie testowym nie ma usuwania w Remote
ani zapisu do Firebird.

Realny tryb po okresie testowym wymaga flagi oraz dwoch zmiennych ochronnych:
```bash
source .venv/bin/activate
FB_ALLOW_WRITES=1 REMOTE_AUTO_ALLOW_DELETES=1 \
python -m remote_ricoh.run --env-file .env --remote-auto-scan --execute-remote-auto
```

Panel LAN bez hasla:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --remote-auto-panel --remote-auto-port 8099
```

Panel pokazuje kolejke, ostatnie uruchomienia, zdarzenia i linki do raportow.
Jesli port jest zajety, automat probuje kolejne porty do `+20`.

Kody wyjscia:
- `0` sukces
- `1` blad wykonania
- `2` blad konfiguracji
- `3` aktywny lockfile

### Cron
Instalacja wpisow cron:
- codziennie o `06:00` pelny proces pobrania i importu
- codziennie o `06:30` testowy skan `--remote-auto-scan`
- w poniedzialki o `07:15` testowa kolejka `--remote-auto-weekly`
- codziennie o `06:15` synchronizacja PrintRadar -> CMAIL
- w poniedzialki o `07:30` raport kolejki skanerow PrintRadar
- po restarcie serwera (`@reboot`, start po 180s) diagnostyka `--dry-run` bez logowania Ricoh
```bash
./scripts/install_cron.sh
```

### Automatyczny import Documaster

Pliki `.csv` i `.xlsx` umieszczone bezposrednio w katalogu SMB `documaster`
mozna sprawdzic bez zapisu:

```bash
python -m remote_ricoh.run --env-file .env \
  --lock-file .state/documaster.lock \
  --documaster-scan
```

Realny import do `CMAIL` oraz archiwizacja plikow wymagaja dwoch zabezpieczen:

```bash
# .env
DOCUMASTER_ALLOW_WRITES=1

python -m remote_ricoh.run --env-file .env \
  --lock-file .state/documaster.lock \
  --documaster-scan --execute-documaster
```

Automat obsluguje raporty Documaster CSV/XLSX, pomija duplikaty i wiersze z
niezgodnym klientem, a pozostale odczyty zapisuje transakcyjnie do `CMAIL`.
Stan plikow znajduje sie w `local/documaster/documaster.sqlite`, a raporty w
`local/documaster/reports/`. Po sukcesie plik jest przenoszony do podkatalogu
klienta. Instalator dodaje skan od poniedzialku do piatku o kazdej pelnej
godzinie od 08:00 do 16:00. Alerty o bledach i pominietych wierszach korzystaja
z `EMAIL_DOCUMASTER_REPORT_TO` albo, gdy ta zmienna jest pusta, z
`EMAIL_WEEKLY_REPORT_TO`.

### Synchronizacja PrintRadar do CMAIL

Dry-run calej dostepnej historii nie zapisuje nic w Firebird ani kursora:

```bash
python -m remote_ricoh.run --env-file .env \
  --lock-file .state/printradar_cmail.lock \
  --printradar-cmail-sync --printradar-cmail-backfill
```

Kontrolowany zapis jednego lub kilku numerow seryjnych wymaga pliku TXT/CSV,
flagi wykonania oraz `PRINTRADAR_CMAIL_ALLOW_WRITES=1`:

```bash
python -m remote_ricoh.run --env-file .env \
  --lock-file .state/printradar_cmail.lock \
  --printradar-cmail-sync --printradar-cmail-backfill \
  --printradar-cmail-serials local/canary_serials.txt \
  --execute-printradar-cmail
```

Synchronizacja wybiera ostatni poprawny odczyt na numer seryjny i zakonczony
dzien. Nie nadpisuje licznikow, blokuje spadki i niejednoznaczne dopasowania
`MASZYNA`. Kazdy rekord ma `MAILFROM=[Import] - PrintRadar` i
`COMMENTS=printradar:<sample_id>`. `SCANNER_TOTAL` nie jest zapisywane; dane
skanera trafiaja do kolejki w `local/printradar_cmail/sync.sqlite` i do
tygodniowego raportu e-mail. Instalator crona dodaje zadanie zapisujace dopiero
przy `PRINTRADAR_CMAIL_ALLOW_WRITES=1`.

Przy bledzie portalu przed odczytem `Requested ID` lokalne snapshoty diagnostyczne sa
zapisywane w `.debug/ricoh_portal/`.

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
3. Sets date range from yesterday to today (`MM/DD/YYYY`, from-to).
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
- the `FB_*` section is optional: without configuration, the `CMAIL` import is skipped; if a configured Firebird instance is unavailable, CSV files remain saved on SMB, but the process fails and sends an email alert

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

Dry-run Ricoh device deletion by serial numbers:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --delete-devices serials.txt
```

Actual deletion requires an explicit flag:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --delete-devices serials.txt --execute-delete
```

`serials.txt` contains one serial number per line. CSV is also supported with a single
column or a `serial` column. The local report is written to
`.debug/ricoh_device_delete/` and includes the `requested_status` and
`last_report_time` columns. The automation does not delete a device if
`Last Report Date/Time` is within the last 3 months or cannot be parsed. After the
delete request is accepted, the portal goes through `Requested Status = Removing`;
the automation monitors this state until the device disappears or writes
`delete_pending` after timeout.

Firebird service order snapshot before manual verification:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --service-order-snapshot orders.txt
```

Compare snapshots after manually closing an example order:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env \
  --service-order-diff before.csv after.csv
```

Dry-run adding repair text and closing service orders:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env \
  --close-service-orders orders.txt \
  --remote-status-report .debug/ricoh_device_delete/final_batch_report_YYYYMMDD_HHMMSS.csv
```

Actual writes require an explicit flag and guard environment variable:
```bash
source .venv/bin/activate
FB_ALLOW_WRITES=1 python -m remote_ricoh.run --env-file .env \
  --close-service-orders orders.txt \
  --remote-status-report .debug/ricoh_device_delete/final_batch_report_YYYYMMDD_HHMMSS.csv \
  --execute-service-orders
```

`orders.txt` may contain `14331/2025`, `serial:G696M313134`, or
`problem:odpiąć REMOTE`. CSV supports `order`, `serial`, `problem_contains`, and
`allow_multiple` columns. Orders are identified by `ZLECENIE.ID_ZLECENIE + ROK`,
and local reports are written to `.debug/ricoh_service_orders/`. The automation
appends `Urządzenie usunięte z Remote.` to `WYKONANIE`, transitions through
`STAN='ZR'`, then sets `STAN='Z'` and `DATA_Z=CURRENT_DATE`, and appends
`Edytował: Marcin,Zamknął :Marcin` to `OPERATOR`; it does not change `TECHNIK`.

For remote-support closures, a custom completion note can be used while keeping
the order metadata unchanged:
```bash
FB_ALLOW_WRITES=1 python -m remote_ricoh.run --env-file .env \
  --close-service-orders orders.txt \
  --service-order-repair-text "pomoc zdalna" \
  --preserve-service-order-metadata \
  --execute-service-orders
```
This mode updates only `WYKONANIE` and transitions `STAN` through `ZR` to `Z`.
It preserves `OPERATOR`, `TECHNIK`, and `DATA_Z`; Firebird may independently
update the technical audit fields `EDITCNT`, `EDITDATE`, and `EDITTIME`.

### Cyclic REMOTE automation
Daily test-mode scan for new `TECHNIK=REMOTE` service orders:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --remote-auto-scan
```

Weekly test-mode check of the waiting queue:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --remote-auto-weekly
```

After a successful weekly scan, the automation sends a summary and the local CSV
report to `EMAIL_WEEKLY_REPORT_TO`. A scan failure sends an alert. Configure the
same `EMAIL_*` SMTP variables shown in the Polish section above.

The default local queue database is `local/remote_auto/remote_auto.sqlite`.
The automation compares `Last Report Date/Time` with a one-month threshold.
Recent readings go to `waiting_recent` and are checked weekly; older readings
go to `ready_delete`. Test mode does not delete in Remote and does not write to
Firebird.

Actual execution after the test period requires a flag and two guard variables:
```bash
source .venv/bin/activate
FB_ALLOW_WRITES=1 REMOTE_AUTO_ALLOW_DELETES=1 \
python -m remote_ricoh.run --env-file .env --remote-auto-scan --execute-remote-auto
```

Unauthenticated LAN panel:
```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --remote-auto-panel --remote-auto-port 8099
```

The panel shows the queue, recent runs, events, and report links. If the port is
busy, the automation tries the next ports up to `+20`.

Exit codes:
- `0` success
- `1` runtime error
- `2` config error
- `3` lockfile active

### Cron
Install cron entries:
- daily at `06:00` for the full download and import process
- daily at `06:30` for test-mode `--remote-auto-scan`
- Mondays at `07:15` for test-mode `--remote-auto-weekly`
- daily at `06:15` for PrintRadar -> CMAIL synchronization
- Mondays at `07:30` for the PrintRadar scanner queue report
- after server restart (`@reboot`, starts after 180s) for `--dry-run` diagnostics without Ricoh login
```bash
./scripts/install_cron.sh
```

### Automatic Documaster import

Use dry-run to inspect `.csv` and `.xlsx` files placed directly in the SMB
`documaster` directory:

```bash
python -m remote_ricoh.run --env-file .env \
  --lock-file .state/documaster.lock \
  --documaster-scan
```

Real `CMAIL` writes and file archiving require both safeguards:

```bash
# .env
DOCUMASTER_ALLOW_WRITES=1

python -m remote_ricoh.run --env-file .env \
  --lock-file .state/documaster.lock \
  --documaster-scan --execute-documaster
```

The importer supports Documaster CSV/XLSX reports, skips duplicates and
customer mismatches, and writes all other readings to `CMAIL` in one
transaction per file. File state is stored in
`local/documaster/documaster.sqlite`, while detailed reports are written to
`local/documaster/reports/`. Successful files are moved to their customer
subdirectory. The cron installer schedules scans every hour from 08:00 through
16:00, Monday through Friday. Warning recipients come from
`EMAIL_DOCUMASTER_REPORT_TO`, falling back to `EMAIL_WEEKLY_REPORT_TO`.

### PrintRadar to CMAIL synchronization

Run a full-history dry-run without Firebird writes or cursor advancement:

```bash
python -m remote_ricoh.run --env-file .env \
  --lock-file .state/printradar_cmail.lock \
  --printradar-cmail-sync --printradar-cmail-backfill
```

Real writes require `PRINTRADAR_CMAIL_ALLOW_WRITES=1`,
`--execute-printradar-cmail`, and can be limited to serials from a TXT/CSV file.
Inserted rows use `MAILFROM=[Import] - PrintRadar` and
`COMMENTS=printradar:<sample_id>`. Scanner counters are not written yet; they
remain in the local SQLite queue and the weekly email report.

When the portal fails before a `Requested ID` is read, local diagnostic snapshots are
written to `.debug/ricoh_portal/`.

### Tests and quality
```bash
source .venv/bin/activate
ruff check .
black --check .
pytest
```
