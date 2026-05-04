#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_LINE="0 6 * * * cd ${REPO_DIR} && ${REPO_DIR}/.venv/bin/python -m remote_ricoh.run --env-file ${REPO_DIR}/.env >> ${REPO_DIR}/logs/cron.log 2>&1"

mkdir -p "${REPO_DIR}/logs"

current_cron="$(crontab -l 2>/dev/null || true)"
if grep -Fq "remote_ricoh.run" <<<"${current_cron}"; then
  echo "Wpis cron dla remote_ricoh juz istnieje."
  exit 0
fi

{
  printf "%s\n" "${current_cron}"
  printf "%s\n" "${CRON_LINE}"
} | crontab -

echo "Dodano cron: ${CRON_LINE}"
