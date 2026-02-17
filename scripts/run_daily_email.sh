#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "python not found: ${PYTHON_BIN}" >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/logs" "${PROJECT_ROOT}/out"

NOW_TS="$(date '+%Y%m%d_%H%M%S')"
JSON_PATH="${PROJECT_ROOT}/out/jobs_${NOW_TS}.json"
JSON_LATEST="${PROJECT_ROOT}/out/jobs_latest.json"
MSG_PATH="${PROJECT_ROOT}/out/jobs_message_${NOW_TS}.txt"
MSG_LATEST="${PROJECT_ROOT}/out/jobs_message_latest.txt"

SITES="${JOB_SITES:-saramin,jobkorea,wanted,linkareer,jasoseol,catch}"
KEYWORDS="${JOB_KEYWORDS:-HR,인사,조직문화,문화,교육,HRD}"
LIMIT_PER_SITE="${JOB_LIMIT_PER_SITE:-20}"
FILTER_FILE="${JOB_FILTER_FILE:-filters/new_grad.json}"
MESSAGE_TITLE="${JOB_MESSAGE_TITLE:-채용 공고 요약}"
MESSAGE_MAX_ITEMS="${JOB_MESSAGE_MAX_ITEMS:-0}"
NICKNAME_FILE="${JOB_NICKNAME_FILE:-nicknames.txt}"
DEFAULT_GREETING_TEMPLATE='안녕 {nickname}! 오늘의 채용 요약이야.'
GREETING_TEMPLATE="${JOB_GREETING_TEMPLATE:-$DEFAULT_GREETING_TEMPLATE}"
RECIPIENTS_FILE="${JOB_RECIPIENTS_FILE:-recipients.txt}"
EMAIL_SUBJECT="${JOB_EMAIL_SUBJECT:-[알림] 오늘의 채용 요약}"
EMAIL_SEND_MODE="${JOB_EMAIL_SEND_MODE:-single}"
RENDER_JS="${JOB_RENDER_JS:-0}"
ENABLE_DUMP="${JOB_ENABLE_DUMP:-0}"
EMAIL_DRY_RUN="${JOB_EMAIL_DRY_RUN:-0}"
ENV_FILE="${JOB_ENV_FILE:-${PROJECT_ROOT}/.env}"
INPUT_JSON="${JOB_INPUT_JSON:-}"

resolve_path() {
  local value="$1"
  if [[ -z "${value}" ]]; then
    echo ""
    return
  fi
  if [[ "${value}" = /* ]]; then
    echo "${value}"
  else
    echo "${PROJECT_ROOT}/${value}"
  fi
}

FILTER_PATH="$(resolve_path "${FILTER_FILE}")"
NICKNAME_PATH="$(resolve_path "${NICKNAME_FILE}")"
RECIPIENTS_PATH="$(resolve_path "${RECIPIENTS_FILE}")"
ENV_PATH="$(resolve_path "${ENV_FILE}")"
INPUT_JSON_PATH="$(resolve_path "${INPUT_JSON}")"

if [[ -n "${INPUT_JSON_PATH}" ]]; then
  CRAWL_ARGS=(
    "${PYTHON_BIN}" "${PROJECT_ROOT}/main.py"
    --input "${INPUT_JSON_PATH}"
    --output "${JSON_PATH}"
  )
else
  CRAWL_ARGS=(
    "${PYTHON_BIN}" "${PROJECT_ROOT}/main.py"
    --sites "${SITES}"
    --keywords "${KEYWORDS}"
    --limit-per-site "${LIMIT_PER_SITE}"
    --output "${JSON_PATH}"
  )
fi

if [[ -n "${FILTER_PATH}" ]]; then
  CRAWL_ARGS+=(--filter-file "${FILTER_PATH}")
fi

if [[ -z "${INPUT_JSON_PATH}" && "${RENDER_JS}" == "1" ]]; then
  CRAWL_ARGS+=(--render-js)
fi
if [[ -z "${INPUT_JSON_PATH}" && "${ENABLE_DUMP}" == "1" ]]; then
  CRAWL_ARGS+=(--dump-dir "${PROJECT_ROOT}/dump")
fi

"${CRAWL_ARGS[@]}"
ln -sf "${JSON_PATH}" "${JSON_LATEST}"

"${PYTHON_BIN}" "${PROJECT_ROOT}/main.py" \
  --input "${JSON_PATH}" \
  --output-format message \
  --message-title "${MESSAGE_TITLE}" \
  --message-max-items "${MESSAGE_MAX_ITEMS}" \
  --message-nickname-file "${NICKNAME_PATH}" \
  --message-greeting-template "${GREETING_TEMPLATE}" \
  --output "${MSG_PATH}"

ln -sf "${MSG_PATH}" "${MSG_LATEST}"

EMAIL_ARGS=(
  "${PYTHON_BIN}" "${PROJECT_ROOT}/email_sender.py"
  --message-file "${MSG_PATH}"
  --subject "${EMAIL_SUBJECT}"
  --recipients-file "${RECIPIENTS_PATH}"
  --send-mode "${EMAIL_SEND_MODE}"
  --env-file "${ENV_PATH}"
)

if [[ "${EMAIL_DRY_RUN}" == "1" ]]; then
  EMAIL_ARGS+=(--dry-run)
fi

"${EMAIL_ARGS[@]}"
