#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_SCRIPT="${PROJECT_ROOT}/scripts/run_daily_email.sh"
LOG_FILE="${PROJECT_ROOT}/logs/cron.log"
MARKER="# alarmbot-daily-email"

if [[ ! -x "${RUN_SCRIPT}" ]]; then
  chmod +x "${RUN_SCRIPT}"
fi
mkdir -p "${PROJECT_ROOT}/logs"

CRON_EXPR="${1:-0 9 * * *}"
CRON_ENV_FILE="${2:-${PROJECT_ROOT}/.env}"

ENTRY="${CRON_EXPR} JOB_ENV_FILE='${CRON_ENV_FILE}' bash ${RUN_SCRIPT} >> ${LOG_FILE} 2>&1 ${MARKER}"

CURRENT="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf "%s\n" "${CURRENT}" | grep -v "${MARKER}" || true)"
NEW_CRON="$(printf "%s\n%s\n" "${FILTERED}" "${ENTRY}" | sed '/^[[:space:]]*$/d')"

printf "%s\n" "${NEW_CRON}" | crontab -

echo "Installed cron entry:"
echo "${ENTRY}"
echo
echo "Current crontab:"
crontab -l
