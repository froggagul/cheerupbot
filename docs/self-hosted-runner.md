# Daily crawling on the desktop

The daily crawl uses a Linux x64 GitHub Actions runner with the `cheerupbot`
label. GitHub manages scheduling, secrets, logs, and artifacts; requests to job
sites originate from the desktop's network. Tests still use GitHub-hosted runners.
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

## Operation

```bash
systemctl --user status cheerupbot-runner.service
journalctl --user -u cheerupbot-runner.service -n 50 --no-pager
loginctl show-user "$USER" -p Linger
gh api repos/froggagul/cheerupbot/actions/runners \
  --jq '.runners[] | {name, status, busy}'

# Full validation: save crawl state and preview the email, without delivery.
gh workflow run daily-crawl.yml --ref main -f dry_run=true
```

Existing repository secrets continue supplying the OpenAI and SMTP credentials.
Scheduled runs remain at approximately 09:00 Asia/Seoul. Keep the computer awake
and online; lingering does not wake a sleeping or powered-off computer. An offline
runner leaves jobs queued, and GitHub fails jobs that remain queued for 24 hours.
The installer does not change desktop power settings or install cron.

`JOB_SCHEDULE_ENABLED=false` pauses scheduled crawling while allowing manual runs.
Do not also schedule the local daily-email script: that would send duplicate mail.

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

For removal, disable the service with `systemctl --user disable --now
cheerupbot-runner.service`, remove the runner in repository Settings → Actions →
Runners, then delete its install directory and user service file. Disable lingering
only if no other user services need to run after logout.

References: [GitHub networking and runner requirements](https://docs.github.com/en/actions/reference/runners/self-hosted-runners),
[custom runner services](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application),
and [Playwright system requirements](https://playwright.dev/python/docs/intro#system-requirements).
