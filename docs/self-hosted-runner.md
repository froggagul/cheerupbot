# Daily crawling on the desktop

The daily crawl uses a Linux x64 GitHub Actions runner with the `cheerupbot`
label. A desktop systemd timer requests the workflow at 09:00 Asia/Seoul each day.
GitHub manages secrets, logs, and artifacts; requests to job sites originate from
the desktop's network. Tests still use GitHub-hosted runners.
No public/static IP or inbound port forwarding is needed.

## Install

Use a regular Linux user with `gh`, `curl`, `python3`, `tar`, `sha256sum`, and a
systemd user session. Authenticate `gh` with repository admin access, then run:

```bash
gh auth login
bash scripts/setup_self_hosted_runner.sh
```

The script downloads the latest official runner, verifies its SHA-256 digest,
registers it with a short-lived token, and starts `cheerupbot-runner.service`.
It reuses an existing registration for this repository on subsequent runs.
The runner lives in `~/.local/share/cheerupbot-runner`; jobs use its `_work`
directory, separate from the development checkout. Runner credentials stay in
that install directory, not in Git. The runner application updates itself.

This setup uses a user service and enables lingering so the service starts at
boot and survives logout. If local policy requires administrator authorization,
run the `sudo loginctl enable-linger` command printed by the installer.
Keep this runner assigned to trusted workflows in this private repository.

The workflow installs managed Python 3.10 with uv and uses `uv.lock`. Playwright
1.64 supports this desktop's Ubuntu 26.04; the older locked 1.58 did not.
Chromium downloads run without sudo. On a fresh desktop, provision missing
browser system libraries once from a project environment:

```bash
uv sync --frozen --python 3.10 --managed-python
.venv/bin/python -m playwright install-deps chromium
```

The dependency installer may ask for sudo. Each workflow installs the matching
browser and verifies it can launch before restoring state or crawling.

## Private recipients

`recipients.txt` is local configuration ignored by Git. On the runner machine,
create it from `recipients.example.txt`, replace the example addresses, and install
it outside the checkout as the regular user running the runner service:

```bash
# First setup only.
test -f recipients.txt || cp recipients.example.txt recipients.txt
```

Edit `recipients.txt` with the intended addresses, then copy it to the private location:

```bash
install -d -m 700 "$HOME/.config/cheerupbot"
install -m 600 recipients.txt "$HOME/.config/cheerupbot/recipients.txt"
```

The workflow uses this private file for production recipients and dry-run summaries.
Copy it again after changing recipients. An alternative absolute path can be set
with `JOB_RECIPIENTS_FILE` in the workflow's `env` section. Keep the file outside
the runner checkout, which is cleaned before each run. Test delivery uses the
fixed test mailbox instead of this file.

## Operation

```bash
systemctl --user status cheerupbot-runner.service
journalctl --user -u cheerupbot-runner.service -n 50 --no-pager
loginctl show-user "$USER" -p Linger
gh api repos/froggagul/cheerupbot/actions/runners \
  --jq '.runners[] | {name, status, busy}'

# Full validation: save crawl state and preview the email, without delivery.
gh workflow run daily-crawl.yml --ref main -f dry_run=true

# Real test delivery: only froggaguldev@gmail.com receives the message.
gh workflow run daily-crawl.yml --ref main -f dry_run=false -f test_delivery=true
```

Manual runs default to both `dry_run=true` and `test_delivery=true`. All test
emails must go only to `froggaguldev@gmail.com`. Test mode replaces all configured
recipients and adds `[TEST]` to the subject. The SMTP envelope contains only that
address, including when a different sender is configured. To send a manual
production digest, explicitly set `dry_run=false` and `test_delivery=false`.
Scheduled production runs use the [private recipient file](#private-recipients).

Existing repository secrets continue supplying the OpenAI and SMTP credentials.
Install the daily trigger after setting up the runner:

```bash
bash scripts/setup_daily_schedule.sh --dry-run
bash scripts/setup_daily_schedule.sh
systemctl --user list-timers cheerupbot-daily.timer --no-pager
journalctl --user -u cheerupbot-daily.service -n 30 --no-pager
```

The timer requests `daily-crawl.yml` on `main` at **09:00 Asia/Seoul**, with
one-second timer accuracy and no randomized delay. It passes `dry_run=false`
and `test_delivery=false`, so the existing workflow sends the production digest
after crawling, filtering, and saving state. Installation does not dispatch a
workflow. The service uses the same user's `gh` login; keep that login valid.
It does not automatically retry a failed dispatch, avoiding duplicate deliveries
if GitHub accepted a request but its response was lost. Check the service journal
if a morning run is missing, and check Actions before manually retrying.

GitHub's native scheduled event can arrive hours late. The installer sets
`JOB_SCHEDULE_ENABLED=false` to disable that fallback and prevent duplicate
digests. Desktop-triggered runs appear as `workflow_dispatch` in Actions; they
keep the same concurrency lock, secrets, state storage, and delivery checks.
Workflow startup still depends on GitHub API availability and an available runner;
09:00 is the dispatch time, and email follows when the run finishes.

Keep the computer awake and online during the crawl; lingering starts services
after boot and logout but does not wake the computer. `Persistent=true` catches
up once after a missed dispatch while powered off. The daily schedule installer
does not change power settings.
Do not also schedule the local daily-email script: that would send duplicate mail.

### Idle sleep with an 08:30–09:30 KST awake window

Install the desktop user's cron policy, then the privileged hardware wake timer:

```bash
python3 scripts/desktop_power_policy.py --install
sudo bash scripts/setup_daily_wake.sh
```

The cron job checks the time every minute and configures GNOME's built-in idle
detection. Outside **08:30–09:30 Asia/Seoul**, automatic suspend occurs after
**600 seconds without desktop activity**. During that window, automatic suspend
is disabled. Settings are written only when they change, so minute-by-minute
checks do not repeatedly reset idle detection. Desktop applications that inhibit
automatic sleep, such as media playback, retain their normal behavior.

Manual Suspend and Shutdown remain available at all times: the policy does not
hold a sleep inhibitor or change the power button action. There is one scheduled
wake at 08:30; manually suspending during the protected hour does not cause
repeated wake-ups. At 09:30, normal idle-based suspend resumes; an active user
is not forced to sleep. The policy applies to this user's logged-in GNOME session,
including its lock screen. Log out is different from locking the screen.

Cron itself cannot wake a suspended machine. `cheerupbot-wake.timer` runs in the
system manager with `WakeSystem=true` and requires administrator authentication.
It resumes the computer at **08:30 KST daily**, but cannot power on a shut-down
computer. The cron policy leaves automatic sleep disabled until that timer is
enabled and active. After installing the timer, the next cron tick enables the
requested policy. The existing desktop runner and 09:00 dispatch remain local.
Upgrading the wake timer removes the old 90-minute sleep inhibitor.

The installer copies the policy into `~/.local/lib/cheerupbot/`, preserves unrelated
cron entries, and saves the original four automatic-sleep settings under
`~/.local/state/cheerupbot/desktop-power-original.json`. Reinstall after editing
the source. Inspect settings, schedule, and policy changes with:

```bash
python3 scripts/desktop_power_policy.py --status
crontab -l
cat ~/.local/state/cheerupbot/desktop-power.log
systemctl list-timers cheerupbot-wake.timer --no-pager
systemctl --user status cheerupbot-runner.service cheerupbot-daily.timer
```

`bash scripts/setup_daily_wake.sh --dry-run` validates units without installing.
Its optional `--sleep-now` flag explicitly requests one suspend in 60 seconds;
omit that flag for normal idle-based sleep. Cancel a pending one-time sleep with
`sudo systemctl stop cheerupbot-sleep-once.timer`. Advertised RTC capability and
an armed timer do not replace a real suspend/resume check of firmware behavior.

To restore the original idle settings and remove this power schedule:

```bash
python3 scripts/desktop_power_policy.py --remove
sudo systemctl disable --now cheerupbot-wake.timer
```

References: [GNOME automatic suspend](https://help.gnome.org/gnome-help/power-autosuspend.html),
[systemd wake timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html#WakeSystem=).

### Pause and resume

Pause/resume the desktop schedule with `systemctl --user disable --now
cheerupbot-daily.timer` / `systemctl --user enable --now cheerupbot-daily.timer`.
`JOB_SCHEDULE_ENABLED` controls only GitHub's fallback, not the desktop timer.

Each run restores `crawl-state` into a unique directory beneath `RUNNER_TEMP`,
including both the GitHub run ID and attempt number. Checkout cleans previous
job outputs. The SQL snapshot is uploaded with the current reports and remains
persisted on the `crawl-state` branch. Successful state publication still precedes
SMTP delivery. A dry run updates that branch but does not send an email.

To stop/start the runner:

```bash
systemctl --user stop cheerupbot-runner.service
systemctl --user start cheerupbot-runner.service
```

For removal, disable the timer with `systemctl --user disable --now
cheerupbot-daily.timer`, then disable the runner with `systemctl --user disable --now
cheerupbot-runner.service`, remove the runner in repository Settings → Actions →
Runners, then delete its install directory and user service file. Disable lingering
only if no other user services need to run after logout.

References: [GitHub networking and runner requirements](https://docs.github.com/en/actions/reference/runners/self-hosted-runners),
[custom runner services](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application),
and [Playwright system requirements](https://playwright.dev/python/docs/intro#system-requirements).
