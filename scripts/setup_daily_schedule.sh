#!/usr/bin/env bash
# Dispatch the existing production workflow from the desktop clock at 09:00 KST.
set -euo pipefail
umask 077

case "${1:-}" in
  --help)
    cat <<'HELP'
Usage: bash scripts/setup_daily_schedule.sh [--dry-run]

Requires the configured desktop runner, user lingering, and gh authentication.
Installs cheerupbot-daily.timer for 09:00 Asia/Seoul and disables GitHub's
delayed schedule using JOB_SCHEDULE_ENABLED=false. The timer dispatches main
with production delivery enabled; installation does not dispatch a workflow.

RUNNER_REPOSITORY defaults to froggagul/cheerupbot.
--dry-run validates and prints the units without installing or changing GitHub.
HELP
    exit 0 ;;
  ''|--dry-run) ;;
  *) echo 'Unexpected arguments; use --help.' >&2; exit 1 ;;
esac
if (( $# > 1 )); then
  echo 'Unexpected arguments; use --help.' >&2
  exit 1
fi
for tool in gh systemctl systemd-analyze loginctl; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done

repository="${RUNNER_REPOSITORY:-froggagul/cheerupbot}"
gh_bin="$(command -v gh)"
if [[ ! "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ || ! "$gh_bin" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
  echo 'Use OWNER/REPO and an absolute gh path without spaces or special characters.' >&2
  exit 1
fi
systemctl --user is-active --quiet cheerupbot-runner.service || {
  echo 'Start the desktop runner with scripts/setup_self_hosted_runner.sh first.' >&2
  exit 1
}
if [[ "$(loginctl show-user "$(id -un)" --property=Linger --value)" != yes ]]; then
  echo 'Enable user lingering through scripts/setup_self_hosted_runner.sh first.' >&2
  exit 1
fi
if [[ "$(gh api "repos/$repository/actions/workflows/daily-crawl.yml" --jq '.state')" != active ]]; then
  echo 'The daily-crawl.yml workflow must be active.' >&2
  exit 1
fi

staging_dir="$(mktemp -d)"
trap 'rm -rf -- "$staging_dir"' EXIT
cat > "$staging_dir/cheerupbot-daily.service" <<EOF
[Unit]
Description=Launch the daily cheerupbot workflow
Wants=cheerupbot-runner.service
After=cheerupbot-runner.service

[Service]
Type=oneshot
Environment=GH_PROMPT_DISABLED=1
ExecStart=$gh_bin workflow run daily-crawl.yml --repo $repository --ref main -f dry_run=false -f test_delivery=false
TimeoutStartSec=60
UMask=0077
EOF
cat > "$staging_dir/cheerupbot-daily.timer" <<'EOF'
[Unit]
Description=Daily cheerupbot launch at 09:00 Korea time

[Timer]
OnCalendar=*-*-* 09:00:00 Asia/Seoul
AccuracySec=1s
RandomizedDelaySec=0
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemd-analyze --user verify "$staging_dir/cheerupbot-daily.service" "$staging_dir/cheerupbot-daily.timer"
if [[ "${1:-}" == --dry-run ]]; then
  cat "$staging_dir/cheerupbot-daily.service" "$staging_dir/cheerupbot-daily.timer"
  systemd-analyze calendar '*-*-* 09:00:00 Asia/Seoul'
  exit 0
fi

unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
mkdir -p "$unit_dir"
install -m 600 "$staging_dir/cheerupbot-daily.service" "$staging_dir/cheerupbot-daily.timer" "$unit_dir/"
systemctl --user daemon-reload
# Disable the old trigger before enabling this one to prevent duplicate digests.
gh variable set JOB_SCHEDULE_ENABLED --repo "$repository" --body false
systemctl --user enable --now cheerupbot-daily.timer
systemctl --user list-timers cheerupbot-daily.timer --no-pager
