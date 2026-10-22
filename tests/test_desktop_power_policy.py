from datetime import datetime
import subprocess
import unittest
from unittest.mock import patch

from scripts import desktop_power_policy as power


class DesktopPowerPolicyTests(unittest.TestCase):
    def test_morning_window_boundaries_and_timezone(self):
        cases = {
            "2026-10-20T00:00:00+09:00": "suspend",
            "2026-10-20T08:29:59+09:00": "suspend",
            "2026-10-20T08:30:00+09:00": "nothing",
            "2026-10-20T09:00:00+09:00": "nothing",
            "2026-10-20T09:29:59+09:00": "nothing",
            "2026-10-20T09:30:00+09:00": "suspend",
            "2026-10-20T23:59:59+09:00": "suspend",
            "2026-10-19T23:30:00+00:00": "nothing",
            "2026-10-20T00:30:00+00:00": "suspend",
        }
        for timestamp, expected in cases.items():
            with self.subTest(timestamp=timestamp):
                self.assertEqual(power.idle_action(datetime.fromisoformat(timestamp), True)[0], expected)

    def test_missing_wake_timer_prevents_auto_sleep_even_at_night(self):
        now = datetime.fromisoformat("2026-10-20T23:00:00+09:00")
        self.assertEqual(power.idle_action(now, False)[0], "nothing")

    def test_disabled_or_failed_timer_is_not_considered_ready(self):
        good = "ActiveState=active\nSubState=waiting\nWakeSystem=yes\nUnitFileState=enabled\n"
        cases = [(good, 0, True), ("", 1, False)]
        for old, new in [("active", "failed"), ("waiting", "dead"), ("yes", "no"), ("enabled", "disabled")]:
            cases.append((good.replace(old, new), 0, False))
        for output, code, expected in cases:
            with self.subTest(output=output), patch.object(
                power, "command", return_value=subprocess.CompletedProcess([], code, output, "")
            ):
                self.assertEqual(power.wake_timer_ready(), expected)

    def test_unchanged_settings_are_not_rewritten_each_minute(self):
        desired = {key: "600" if key.endswith("-timeout") else "'suspend'" for key in power.KEYS}
        with patch.object(power, "settings", return_value=desired), patch.object(power, "command") as run:
            power.write_settings(desired)
            run.assert_not_called()

    def test_disabling_auto_sleep_does_not_change_manual_power_actions(self):
        current = {key: "900" if key.endswith("-timeout") else "'suspend'" for key in power.KEYS}
        desired = {key: "600" if key.endswith("-timeout") else "'nothing'" for key in power.KEYS}
        with patch.object(power, "settings", return_value=current), patch.object(power, "command") as run:
            power.write_settings(desired)
        calls = [call.args for call in run.call_args_list]
        self.assertEqual(len(calls), 4)
        for args in calls:
            self.assertEqual(args[:3], ("/usr/bin/gsettings", "set", power.SCHEMA))
            self.assertIn(args[3], power.KEYS)
        self.assertEqual(calls[0][3], "sleep-inactive-ac-type")
        self.assertEqual(calls[2][3], "sleep-inactive-battery-type")

    def test_unrelated_cron_entries_are_preserved(self):
        existing = "MAILTO=user@example.com\n# My backup\n0 2 * * * backup\n"
        ours = f"* * * * * python power.py {power.MARKER}\n"
        self.assertEqual(power.without_our_cron(existing + ours + ours), existing)


if __name__ == "__main__":
    unittest.main()
