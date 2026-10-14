#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CRON_EXPR="${CRON_EXPR:-0 9 * * *}"
ENV_FILE_NAME="${ENV_FILE_NAME:-.env}"
WITH_PLAYWRIGHT="${WITH_PLAYWRIGHT:-0}"
DRY_RUN="${DRY_RUN:-0}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/bootstrap_ubuntu.sh [options]

Options:
  --cron "M H * * *"        Cron expression (default: 0 9 * * *)
  --env-file .env           Env file name under project root (default: .env)
  --with-playwright         Install Playwright Chromium
  --dry-run                 Print commands without executing
  -h, --help                Show this help

Environment alternatives:
  CRON_EXPR, ENV_FILE_NAME, WITH_PLAYWRIGHT=1, DRY_RUN=1
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cron)
      CRON_EXPR="${2:-}"
      shift 2
      ;;
    --env-file)
      ENV_FILE_NAME="${2:-}"
      shift 2
      ;;
    --with-playwright)
      WITH_PLAYWRIGHT=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

run_cmd() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "[dry-run] $*"
    return
  fi
  eval "$@"
}

if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "Warning: this script is optimized for Ubuntu. detected=${ID:-unknown}" >&2
  fi
fi

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "[1/6] Install system packages"
run_cmd "${SUDO} apt-get update"
run_cmd "${SUDO} apt-get install -y python3 python3-venv python3-pip git cron"

echo "[2/6] Enable cron service"
run_cmd "${SUDO} systemctl enable --now cron"

echo "[3/6] Create venv and install Python deps"
run_cmd "cd \"${PROJECT_ROOT}\" && python3 -m venv .venv"
run_cmd "cd \"${PROJECT_ROOT}\" && .venv/bin/python -m ensurepip --upgrade"
run_cmd "cd \"${PROJECT_ROOT}\" && .venv/bin/python -m pip install --upgrade pip setuptools wheel"
run_cmd "cd \"${PROJECT_ROOT}\" && .venv/bin/python -m pip install requests beautifulsoup4 lxml playwright python-dotenv 'openai>=2.0.0,<3'"

if [[ "${WITH_PLAYWRIGHT}" == "1" ]]; then
  echo "[3.1/6] Install Playwright browser"
  run_cmd "cd \"${PROJECT_ROOT}\" && .venv/bin/python -m playwright install chromium"
fi

echo "[4/6] Prepare runtime directories and env file"
run_cmd "mkdir -p \"${PROJECT_ROOT}/out\" \"${PROJECT_ROOT}/logs\" \"${PROJECT_ROOT}/dump\""
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[dry-run] create ${PROJECT_ROOT}/${ENV_FILE_NAME} from .env.example if missing"
else
  if [[ ! -f "${PROJECT_ROOT}/${ENV_FILE_NAME}" ]]; then
    if [[ -f "${PROJECT_ROOT}/.env.example" ]]; then
      cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/${ENV_FILE_NAME}"
      echo "Created ${ENV_FILE_NAME}. Fill SMTP credentials before running."
    else
      touch "${PROJECT_ROOT}/${ENV_FILE_NAME}"
      echo "Created empty ${ENV_FILE_NAME}. Fill SMTP credentials."
    fi
  fi
fi

echo "[5/6] Install cron entry"
run_cmd "cd \"${PROJECT_ROOT}\" && bash scripts/install_cron.sh \"${CRON_EXPR}\" \"${PROJECT_ROOT}/${ENV_FILE_NAME}\""

echo "[6/6] Run smoke test (dry-run email, sample from jobs_debug.json if present)"
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[dry-run] JOB_INPUT_JSON=jobs_debug.json JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh"
else
  if [[ -f "${PROJECT_ROOT}/jobs_debug.json" ]]; then
    JOB_INPUT_JSON="${PROJECT_ROOT}/jobs_debug.json" JOB_EMAIL_DRY_RUN=1 bash "${PROJECT_ROOT}/scripts/run_daily_email.sh"
  else
    echo "jobs_debug.json not found; skipping smoke test."
  fi
fi

echo
echo "Done."
echo "Next:"
echo "1) Edit ${PROJECT_ROOT}/${ENV_FILE_NAME} with SMTP_*"
echo "2) Edit ${PROJECT_ROOT}/recipients.txt"
echo "3) Test: JOB_EMAIL_DRY_RUN=1 bash ${PROJECT_ROOT}/scripts/run_daily_email.sh"
echo "4) Real run: bash ${PROJECT_ROOT}/scripts/run_daily_email.sh"
