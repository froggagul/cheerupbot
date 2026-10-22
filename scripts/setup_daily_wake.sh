#!/usr/bin/env bash
# Wake this desktop before its existing 09:00 KST crawl.
set -euo pipefail
umask 022

case "${1:-}" in
  --help)
    cat <<'HELP'
Usage: sudo bash scripts/setup_daily_wake.sh [--sleep-now]
       bash scripts/setup_daily_wake.sh --dry-run

Installs a system timer that wakes the computer from suspend every day at
08:30 Asia/Seoul. Use desktop_power_policy.py --install as your regular user
to sleep after 10 idle minutes except during 08:30–09:30 KST.
Requires RTC wake support. Does not wake a powered-off computer or change
the existing 09:00 workflow trigger.

--dry-run   Validate and print units without changing the system.
--sleep-now Also schedule a one-time suspend in 60 seconds, after verifying
            that the daily wake timer is active. No recurring bedtime is set.
HELP
    exit 0 ;;
  ''|--dry-run|--sleep-now) ;;
  *) echo 'Unexpected arguments; use --help.' >&2; exit 1 ;;
esac
if (( $# > 1 )); then
  echo 'Unexpected arguments; use --help.' >&2
  exit 1
fi
mode="${1:-}"
if [[ "$mode" != --dry-run && "$EUID" != 0 ]]; then
  echo 'Installing a wake timer requires root. Run this script with sudo.' >&2
  exit 1
fi
for tool in systemctl systemd-analyze systemd-run; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
if [[ ! -r /sys/power/state ]] || ! grep -qw mem /sys/power/state; then
  echo 'This computer does not advertise suspend-to-RAM support.' >&2
  exit 1
fi
rtc_ready=false
for rtc in /sys/class/rtc/rtc*; do
  if [[ -e "$rtc/wakealarm" && -r "$rtc/device/power/wakeup" ]] &&
      [[ "$(cat "$rtc/device/power/wakeup")" == enabled ]]; then
    rtc_ready=true
    break
  fi
done
if [[ "$rtc_ready" != true ]]; then
  echo 'No RTC with wake-up enabled was found; check firmware wake settings.' >&2
  exit 1
fi

staging_dir="$(mktemp -d)"
trap 'rm -rf -- "$staging_dir"' EXIT
cat > "$staging_dir/cheerupbot-wake.service" <<'EOF'
[Unit]
Description=Wake the desktop for the 09:00 KST cheerupbot crawl

[Service]
Type=oneshot
ExecStart=/usr/bin/true
EOF
cat > "$staging_dir/cheerupbot-wake.timer" <<'EOF'
[Unit]
Description=Wake the desktop daily at 08:30 Korea time

[Timer]
OnCalendar=*-*-* 08:30:00 Asia/Seoul
WakeSystem=true
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false

[Install]
WantedBy=timers.target
EOF

systemd-analyze verify "$staging_dir/cheerupbot-wake.service" "$staging_dir/cheerupbot-wake.timer"
systemd-analyze calendar '*-*-* 08:30:00 Asia/Seoul'
if [[ "$mode" == --dry-run ]]; then
  cat "$staging_dir/cheerupbot-wake.service" "$staging_dir/cheerupbot-wake.timer"
  exit 0
fi

install -o root -g root -m 644 "$staging_dir/cheerupbot-wake.service" "$staging_dir/cheerupbot-wake.timer" /etc/systemd/system/
systemctl daemon-reload
# Release the older 90-minute inhibitor if upgrading an existing installation.
systemctl stop cheerupbot-wake.service
systemctl enable --now cheerupbot-wake.timer
# Restart to apply any edits when reinstalling an already-active timer.
systemctl restart cheerupbot-wake.timer
systemctl is-active --quiet cheerupbot-wake.timer
systemctl is-enabled --quiet cheerupbot-wake.timer
if [[ "$(systemctl show cheerupbot-wake.timer --property=WakeSystem --value)" != yes ]] ||
   [[ "$(systemctl show cheerupbot-wake.timer --property=NextElapseUSecRealtime --value)" == '' ]]; then
  echo 'The wake timer is not armed; refusing to suspend.' >&2
  exit 1
fi
systemctl list-timers cheerupbot-wake.timer --no-pager
echo 'Daily 08:30 KST wake timer installed.'

if [[ "$mode" == --sleep-now ]]; then
  # Give the user time to read the result and save work. Respect sleep inhibitors.
  systemd-run --unit=cheerupbot-sleep-once --description='One-time sleep after wake timer setup' \
    --on-active=60s --timer-property=AccuracySec=1s --collect \
    /usr/bin/systemctl --check-inhibitors=yes suspend
  echo 'Sleep scheduled in 60 seconds. Cancel with: sudo systemctl stop cheerupbot-sleep-once.timer'
fi
