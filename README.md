# remote_ricoh

Automatyczne pobieranie licznikow CSV z portalu Ricoh i zapis na udziale SMB.

## Zakres

Proces wykonuje:
1. wejscie na `https://adfs.jp.ricoh.com/adfs/ls/`,
2. klikniecie opcji `Partner (Dealer / Supplier)`,
3. logowanie danymi z `.env`,
4. przejscie do `RequestCsv.aspx` i klik `Request`,
5. odczyt `Requested ID`,
6. polling `MyHome.aspx` do 15 minut,
7. pobranie ZIP,
8. rozpakowanie plikow `DPLAC` i `DPLAC_Not_obtained`,
9. zapis na SMB jako:
   - `DPLAC_dd-mm-rrrr.csv`
   - `DPLAC_Not_obtained_dd-mm-rrrr.csv`
10. zapis logu dziennego na SMB: `log/ricoh_YYYY-MM-DD.log`.

## Wymagania

- Python 3.12+
- Przegladarka Chromium dla Playwright
- Dostep SMB do sciezki z `.env`

## Konfiguracja

Plik `.env` (nie commitowac):

```env
login_ricoh=
pass_ricoh=
sciezka_remote=//serwer/udzial/katalog
user_smb=
pass_smb=
```

## Instalacja

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Uruchomienie reczne

```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env
```

Tryb diagnostyczny (bez logowania do Ricoh, tylko walidacja SMB + log):

```bash
source .venv/bin/activate
python -m remote_ricoh.run --env-file .env --dry-run
```

Kody wyjscia:
- `0` sukces,
- `1` blad wykonania,
- `2` blad konfiguracji,
- `3` aktywny lockfile (proces juz dziala).

## Cron (06:00 codziennie)

```bash
./scripts/install_cron.sh
```

Skrypt dopisuje wpis:

```cron
0 6 * * * cd /home/marcin/projects/remote_ricoh && /home/marcin/projects/remote_ricoh/.venv/bin/python -m remote_ricoh.run --env-file /home/marcin/projects/remote_ricoh/.env >> /home/marcin/projects/remote_ricoh/logs/cron.log 2>&1
```

## Testy i jakosc

```bash
source .venv/bin/activate
ruff check .
black --check .
pytest
```
