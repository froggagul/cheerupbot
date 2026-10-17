#!/usr/bin/env bash
# Register a repository runner outside the source checkout and start a user service.
set -euo pipefail
# Never trace the short-lived registration token, including under bash -x.
set +x
umask 077

if [[ "${1:-}" == "--help" ]]; then
  cat <<'HELP'
Usage: bash scripts/setup_self_hosted_runner.sh

Requires Linux x64, gh authenticated with repository admin access, curl,
python3, tar, sha256sum, and a running systemd user session.

Optional environment variables:
  RUNNER_REPOSITORY  Repository (default: froggagul/cheerupbot)
  RUNNER_DIRECTORY   Install directory (default: ~/.local/share/cheerupbot-runner)
  RUNNER_NAME        GitHub runner name (default: cheerupbot-<hostname>)

Creates cheerupbot-runner.service and enables user lingering for boot startup.
Does not install cron, change power settings, or dispatch a workflow.
HELP
  exit 0
fi
if (( $# )); then
  echo 'Unexpected arguments; use --help.' >&2
  exit 1
fi
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 || "$EUID" == 0 ]]; then
  echo 'Run as the regular runner user on Linux x64, without sudo.' >&2
  exit 1
fi
for tool in gh curl python3 tar sha256sum systemctl loginctl; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done

RUNNER_REPOSITORY="${RUNNER_REPOSITORY:-froggagul/cheerupbot}"
RUNNER_DIRECTORY="${RUNNER_DIRECTORY:-${HOME}/.local/share/cheerupbot-runner}"
RUNNER_NAME="${RUNNER_NAME:-cheerupbot-$(hostname -s)}"
if [[ ! "$RUNNER_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo 'RUNNER_REPOSITORY must be OWNER/REPO.' >&2
  exit 1
fi
# Keep systemd unit paths unambiguous; reject specifiers and shell metacharacters.
if [[ ! "$RUNNER_DIRECTORY" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
  echo 'RUNNER_DIRECTORY must be an absolute path without spaces or special characters.' >&2
  exit 1
fi
systemctl --user show-environment >/dev/null
if [[ "$(gh api "repos/$RUNNER_REPOSITORY" --jq '.permissions.admin')" != true ]]; then
  echo "Repository admin access is required: $RUNNER_REPOSITORY" >&2
  exit 1
fi

if [[ ! -f "$RUNNER_DIRECTORY/config.sh" ]]; then
  if [[ -d "$RUNNER_DIRECTORY" && -n "$(ls -A "$RUNNER_DIRECTORY")" ]]; then
    echo "Refusing to unpack into a nonempty directory: $RUNNER_DIRECTORY" >&2
    exit 1
  fi
  download_dir="$(mktemp -d)"
  trap 'rm -rf -- "$download_dir"' EXIT
  release="$(gh api repos/actions/runner/releases/latest --jq \
    '.assets[] | select(.name | test("^actions-runner-linux-x64-.*[.]tar[.]gz$")) | [.browser_download_url, .digest] | @tsv')"
  IFS=$'\t' read -r download_url digest <<< "$release"
  if [[ ! "$download_url" == https://github.com/actions/runner/releases/download/* || ! "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    echo 'Runner release is missing a verified download URL or SHA-256 digest.' >&2
    exit 1
  fi
  curl --fail --silent --show-error --location --retry 3 "$download_url" -o "$download_dir/runner.tar.gz"
  printf '%s  %s\n' "${digest#sha256:}" "$download_dir/runner.tar.gz" | sha256sum --check --status
  mkdir -p "$RUNNER_DIRECTORY"
  tar -xzf "$download_dir/runner.tar.gz" -C "$RUNNER_DIRECTORY"
fi

cd "$RUNNER_DIRECTORY"
if [[ -f .runner ]]; then
  python3 - "$RUNNER_REPOSITORY" <<'PY'
import json
import sys
from pathlib import Path

config = json.loads(Path('.runner').read_text(encoding='utf-8-sig'))
expected = 'https://github.com/' + sys.argv[1]
if config.get('gitHubUrl', '').rstrip('/').casefold() != expected.casefold():
    raise SystemExit('This directory is registered to a different repository.')
print('Using existing runner:', config['agentName'])
PY
else
  # Passed through the environment, never printed or written into the repository.
  ACTIONS_RUNNER_INPUT_TOKEN="$(gh api --method POST \
    "repos/$RUNNER_REPOSITORY/actions/runners/registration-token" --jq '.token')" \
    ./config.sh --unattended --url "https://github.com/$RUNNER_REPOSITORY" \
      --name "$RUNNER_NAME" --labels cheerupbot --work _work
fi

service_dir="${HOME}/.config/systemd/user"
mkdir -p "$service_dir"
cat > "$service_dir/cheerupbot-runner.service" <<EOF
[Unit]
Description=GitHub Actions runner for $RUNNER_REPOSITORY

[Service]
WorkingDirectory=$RUNNER_DIRECTORY
ExecStart=$RUNNER_DIRECTORY/bin/runsvc.sh
Restart=always
RestartSec=5
TimeoutStopSec=5min
UMask=0077

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now cheerupbot-runner.service
if ! loginctl enable-linger --no-ask-password "$(id -un)"; then
  echo "Runner started, but boot startup needs: sudo loginctl enable-linger $(id -un)" >&2
  exit 1
fi
systemctl --user is-active cheerupbot-runner.service
echo "Runner service installed. Check: gh api repos/$RUNNER_REPOSITORY/actions/runners"
