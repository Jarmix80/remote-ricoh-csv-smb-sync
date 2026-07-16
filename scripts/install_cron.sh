#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_CMD="${REPO_DIR}/.venv/bin/python -m remote_ricoh.run --env-file ${REPO_DIR}/.env"
DAILY_CRON_LINE="0 6 * * * cd ${REPO_DIR} && ${RUN_CMD} >> ${REPO_DIR}/logs/cron.log 2>&1 # remote_ricoh_daily"
REMOTE_AUTO_SCAN_CRON_LINE="30 6 * * * cd ${REPO_DIR} && ${RUN_CMD} --remote-auto-scan >> ${REPO_DIR}/logs/remote_auto.log 2>&1 # remote_ricoh_auto_scan"
REMOTE_AUTO_WEEKLY_CRON_LINE="15 7 * * 1 cd ${REPO_DIR} && ${RUN_CMD} --remote-auto-weekly >> ${REPO_DIR}/logs/remote_auto.log 2>&1 # remote_ricoh_auto_weekly"
REBOOT_CRON_LINE="@reboot /bin/bash -lc 'sleep 180; cd ${REPO_DIR} && ${RUN_CMD} --dry-run >> ${REPO_DIR}/logs/cron.log 2>&1' # remote_ricoh_reboot"

mkdir -p "${REPO_DIR}/logs"

current_cron="$(crontab -l 2>/dev/null || true)"
cleaned_cron="$(printf "%s\n" "${current_cron}" | awk -v run_cmd="${RUN_CMD}" '
  index($0, run_cmd) == 0 &&
  index($0, "remote_ricoh_daily") == 0 &&
  index($0, "remote_ricoh_auto_scan") == 0 &&
  index($0, "remote_ricoh_auto_weekly") == 0 &&
  index($0, "remote_ricoh_reboot") == 0 { print }
')"

{
  printf "%s\n" "${cleaned_cron}"
  printf "%s\n" "${DAILY_CRON_LINE}"
  printf "%s\n" "${REMOTE_AUTO_SCAN_CRON_LINE}"
  printf "%s\n" "${REMOTE_AUTO_WEEKLY_CRON_LINE}"
  printf "%s\n" "${REBOOT_CRON_LINE}"
} | crontab -

echo "Skonfigurowano cron:"
echo " - ${DAILY_CRON_LINE}"
echo " - ${REMOTE_AUTO_SCAN_CRON_LINE}"
echo " - ${REMOTE_AUTO_WEEKLY_CRON_LINE}"
echo " - ${REBOOT_CRON_LINE}"
