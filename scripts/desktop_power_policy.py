#!/usr/bin/env python3
"""Use GNOME idle detection, with a cron-managed 08:30–09:30 KST exception."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo


SCHEMA = "org.gnome.settings-daemon.plugins.power"
KEYS = tuple(
    f"sleep-inactive-{source}-{setting}"
    for source in ("ac", "battery")
    for setting in ("type", "timeout")
)
MARKER = "# cheerupbot-desktop-power"
KST = ZoneInfo("Asia/Seoul")


def command(*args, check=True, **kwargs):
    return subprocess.run(
        args, check=check, text=True, capture_output=True, timeout=15, **kwargs
    )


def idle_action(now, wake_ready):
    local = now.astimezone(KST)
    minute = local.hour * 60 + local.minute
    if not wake_ready:
        return "nothing", "waiting for the daily wake timer to be installed/enabled"
    if 510 <= minute < 570:
        return "nothing", "08:30–09:30 KST: automatic sleep disabled"
    return "suspend", "automatic sleep after 10 minutes of desktop inactivity"


def wake_timer_ready():
    result = command(
        "/usr/bin/systemctl", "show", "cheerupbot-wake.timer",
        "--property=ActiveState,SubState,WakeSystem,UnitFileState", check=False,
    )
    properties = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    return result.returncode == 0 and all(
        properties.get(key) == value for key, value in {
            "ActiveState": "active", "SubState": "waiting",
            "WakeSystem": "yes", "UnitFileState": "enabled",
        }.items()
    )


def settings():
    return {
        key: command("/usr/bin/gsettings", "get", SCHEMA, key).stdout.strip()
        for key in KEYS
    }


def write_settings(desired):
    current = settings()
    for source in ("ac", "battery"):
        action = f"sleep-inactive-{source}-type"
        timeout = f"sleep-inactive-{source}-timeout"
        # Disable sleep before shortening a timeout; set the timeout before enabling it.
        order = (action, timeout) if desired[action] == "'nothing'" else (timeout, action)
        for key in order:
            if current[key] != desired[key]:
                command("/usr/bin/gsettings", "set", SCHEMA, key, desired[key])


def read_crontab():
    result = command("/usr/bin/crontab", "-l", check=False,
                     env={**os.environ, "LC_ALL": "C"})
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and result.stderr.startswith("no crontab for "):
        return ""
    raise RuntimeError(f"Cannot read crontab: {result.stderr.strip()}")


def write_crontab(content):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cron") as staged:
        staged.write(content)
        staged.flush()
        command("/usr/bin/crontab", "-n", staged.name)
        command("/usr/bin/crontab", staged.name)


def without_our_cron(content):
    return "".join(
        line for line in content.splitlines(keepends=True)
        if not line.rstrip().endswith(MARKER)
    )


def apply_policy(state_dir, dry_run=False):
    now = datetime.now(KST)
    ready = wake_timer_ready()
    action, reason = idle_action(now, ready)
    desired = {
        key: "600" if key.endswith("-timeout") else repr(action) for key in KEYS
    }
    if dry_run:
        print(json.dumps({
            "time": now.isoformat(), "wake_timer_ready": ready,
            "policy": reason, "current": settings(), "desired": desired,
        }, ensure_ascii=False, indent=2))
        return
    write_settings(desired)
    status_file = state_dir / "desktop-power-status.json"
    status = {"wake_timer_ready": ready, "action": action, "policy": reason}
    previous = json.loads(status_file.read_text()) if status_file.exists() else None
    if status != previous:
        print(f"{now.isoformat()} {reason}", flush=True)
        status_file.write_text(json.dumps(status, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="install the user's cron job")
    mode.add_argument("--remove", action="store_true", help="remove cron and restore power settings")
    mode.add_argument("--status", action="store_true", help="show the policy without changing settings")
    mode.add_argument("--apply", action="store_true", help="apply the current policy (default)")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as your desktop user, without sudo.")
    runtime = Path(f"/run/user/{os.getuid()}")
    if not (runtime / "bus").is_socket():
        if args.apply or not any(vars(args).values()):
            return  # No desktop bus yet at boot; cron retries next minute.
        raise RuntimeError("Log into your GNOME desktop first.")
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    state_dir = Path.home() / ".local/state/cheerupbot"
    backup = state_dir / "desktop-power-original.json"
    if args.status:
        apply_policy(state_dir, dry_run=True)
        return
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.remove:
        if not backup.exists():
            raise RuntimeError("Original settings backup is missing; restore settings manually.")
        original = json.loads(backup.read_text())
        if set(original) != set(KEYS):
            raise RuntimeError("Original settings backup is invalid.")
        write_crontab(without_our_cron(read_crontab()))
        write_settings(original)
        backup.unlink()
        (state_dir / "desktop-power-status.json").unlink(missing_ok=True)
        print("Power cron removed; original automatic-sleep settings restored.")
        return
    if args.install:
        command("/usr/bin/systemctl", "is-active", "--quiet", "cron")
        for key in KEYS:
            if command("/usr/bin/gsettings", "writable", SCHEMA, key).stdout.strip() != "true":
                raise RuntimeError(f"The desktop policy locks {key}.")
        installed = Path.home() / ".local/lib/cheerupbot/desktop_power_policy.py"
        log = state_dir / "desktop-power.log"
        for path in (installed, log):
            if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)):
                raise RuntimeError("Cron installation requires a home path without special characters.")
        current = read_crontab()
        if not backup.exists():
            backup.write_text(json.dumps(settings(), indent=2) + "\n")
        installed.parent.mkdir(parents=True, exist_ok=True)
        if Path(__file__).resolve() != installed.resolve():
            shutil.copyfile(__file__, installed)
        entry = f"* * * * * /usr/bin/python3 {installed} --apply >> {log} 2>&1 {MARKER}\n"
        preserved = without_our_cron(current)
        write_crontab(preserved + ("\n" if preserved and not preserved.endswith("\n") else "") + entry)
        print("Installed one-minute power-policy cron; unrelated cron entries preserved.")
    apply_policy(state_dir)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"desktop-power: {error}", file=sys.stderr)
        sys.exit(1)
