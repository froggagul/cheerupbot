import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

import email_sender
import main
from message_formatter import render_jobs_email_html, render_jobs_message


NOW = datetime(2026, 10, 14, 15, 30, tzinfo=timezone.utc)  # Oct 15 in Korea


class EmailFormatTests(unittest.TestCase):
    def test_plain_and_html_agree_on_selection_and_new_crawler_fields(self):
        rows = [
            {"title": "HR role", "keyword": "HR", "deadline": "2026-10-15"},
            {"title": "HRD role", "keyword": "교육", "matched_keywords": ["교육", "HRD"],
             "deadline": "2026-10-18", "experience": "신입", "employment_type": "full_time",
             "site": "saramin", "link": "https://example.com/job?a=1&b=2"},
        ]
        plain = render_jobs_message(rows, max_items=1, greeting="안녕!", generated_at=NOW)
        markup = render_jobs_email_html(rows, max_items=1, greeting="안녕!", generated_at=NOW)
        soup = BeautifulSoup(markup, "html.parser")
        for content in (plain, soup.get_text()):
            for expected in ("총 2건 · 1건 표시", "HRD role", "신입 · 풀타임", "사람인", "D-3", "안녕!"):
                self.assertIn(expected, content)
            self.assertNotIn("HR role", content)
        self.assertEqual(soup.h2.get_text(), "HRD role")
        self.assertEqual(soup.h2.a["href"], rows[1]["link"])

    def test_deadline_status_uses_korean_date_and_handles_missing_values(self):
        rows = [{"title": deadline, "deadline": deadline} for deadline in
                ("2026-10-15", "2026-10-14", "2026-10-18", "2026-10-19", "OPEN", "", "2026-99-99")]
        markup = render_jobs_email_html(rows, generated_at=NOW)
        text = BeautifulSoup(markup, "html.parser").get_text()
        self.assertIn("오늘 마감 · 2026.10.15 (목)", text)
        self.assertIn("마감 · 2026.10.14 (수)", text)
        self.assertIn("3일 이내 마감 2건", text)
        self.assertIn("상시채용", text)
        self.assertIn("마감일 확인 필요", text)
        self.assertIn("2026-99-99", text)

    def test_crawled_content_cannot_inject_html_or_unsafe_links(self):
        for url in ("javascript:alert(1)", "data:text/html,hello", "https://example.com/\nscript", "https://[broken"):
            with self.subTest(url=url):
                markup = render_jobs_email_html([
                    {"title": '<script>alert("title")</script>', "company": '<img src=x onerror="alert(1)">',
                     "link": url, "experience": "<b>신입</b>"},
                ], title="<unsafe title>", greeting="<unsafe greeting>", generated_at=NOW)
                soup = BeautifulSoup(markup, "html.parser")
                self.assertIsNone(soup.find("script"))
                self.assertIsNone(soup.find("img"))
                self.assertIsNone(soup.find("a"))
                self.assertIn('<script>alert("title")</script>', soup.get_text())
                self.assertIn("공고 링크 확인 필요", soup.get_text())

    def test_empty_digest_is_readable_in_both_formats(self):
        for render in (render_jobs_message, render_jobs_email_html):
            content = render([], generated_at=NOW)
            self.assertIn("총 0건", content)
            self.assertIn("조건에 맞는 공고가 없습니다.", content)

    def test_multipart_keeps_plain_fallback_and_supplied_html(self):
        markup = render_jobs_email_html([{"title": "채용", "link": "https://example.com/job"}], generated_at=NOW)
        msg = email_sender._build_message(
            subject="채용 요약", sender="bot@example.com", recipients=["reader@example.com"],
            body="채용\nhttps://example.com/job", html_body=markup, reply_to="", use_bcc=True,
        )
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertIn("https://example.com/job", msg.get_body(preferencelist=("plain",)).get_content())
        self.assertEqual(msg.get_body(preferencelist=("html",)).get_content().strip(), markup.strip())

    def test_preview_needs_no_recipients_or_credentials_and_never_connects_smtp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html_file, preview = root / "email.html", root / "preview.html"
            html_file.write_text("<html><body>채용</body></html>", encoding="utf-8")
            argv = ["email_sender.py", "--no-dotenv", "--message-text", "채용",
                    "--html-file", str(html_file), "--preview-html", str(preview),
                    "--recipients-file", str(root / "missing.txt")]
            with patch.dict(os.environ, {}, clear=True), patch("sys.argv", argv), \
                    patch("email_sender._connect_smtp") as smtp, contextlib.redirect_stdout(io.StringIO()):
                email_sender.main()
            smtp.assert_not_called()
            self.assertEqual(preview.read_text(), html_file.read_text())

    def test_cli_generates_matching_text_and_html_with_one_greeting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, plain_file, html_file = (root / name for name in ("jobs.json", "jobs.txt", "jobs.html"))
            source.write_text(json.dumps([{"title": "HR 신입", "deadline": "OPEN"}]))
            argv = ["main.py", "--no-dotenv", "--input", str(source), "--output-format", "message",
                    "--output", str(plain_file), "--message-html-output", str(html_file)]
            with patch("sys.argv", argv), patch("main._build_message_greeting", return_value="안녕 친구야!") as greeting, \
                    contextlib.redirect_stdout(io.StringIO()):
                main.main()
            greeting.assert_called_once()
            for path in (plain_file, html_file):
                self.assertIn("안녕 친구야!", path.read_text())
                self.assertIn("HR 신입", path.read_text())

    def test_both_delivery_modes_use_html_alternative(self):
        for mode, count in (("single", 1), ("individual", 2)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                html_file = Path(folder) / "mail.html"
                html_file.write_text("<h1>채용 요약</h1>", encoding="utf-8")
                argv = ["email_sender.py", "--no-dotenv", "--message-text", "요약", "--html-file", str(html_file),
                        "--from-email", "bot@example.com", "--to", "a@example.com", "--to", "b@example.com",
                        "--recipients-file", str(Path(folder) / "missing.txt"), "--send-mode", mode]
                with patch("sys.argv", argv), patch("email_sender._connect_smtp") as smtp, \
                        contextlib.redirect_stdout(io.StringIO()):
                    email_sender.main()
                self.assertEqual(smtp.return_value.send_message.call_count, count)
                for call in smtp.return_value.send_message.call_args_list:
                    self.assertEqual(call.args[0].get_body(preferencelist=("html",)).get_content().strip(), "<h1>채용 요약</h1>")
                delivered_to = [address for call in smtp.return_value.send_message.call_args_list
                                for address in call.kwargs['to_addrs']]
                self.assertEqual(delivered_to, ['a@example.com', 'b@example.com'])

    def test_test_email_excludes_file_cli_and_sender_addresses_in_both_modes(self):
        for mode in ('single', 'individual'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                recipient_file = Path(folder) / 'recipients.txt'
                recipient_file.write_text('production@example.com\nother@example.com\n')
                argv = ['email_sender.py', '--no-dotenv', '--message-text', 'Test digest',
                        '--from-email', 'sender@example.com', '--subject', 'Digest',
                        '--recipients-file', str(recipient_file), '--to', 'cli@example.com',
                        '--send-mode', mode, '--test-email']
                with patch('sys.argv', argv), patch('email_sender._connect_smtp') as smtp, \
                        contextlib.redirect_stdout(io.StringIO()):
                    email_sender.main()
                smtp.return_value.send_message.assert_called_once()
                call = smtp.return_value.send_message.call_args
                self.assertEqual(call.kwargs['to_addrs'], ['froggaguldev@gmail.com'])
                self.assertEqual(call.args[0]['To'], 'froggaguldev@gmail.com')
                self.assertIsNone(call.args[0]['Cc'])
                self.assertIsNone(call.args[0]['Bcc'])
                self.assertEqual(call.args[0]['Subject'], '[TEST] Digest')

    def test_test_email_dry_run_never_connects_smtp(self):
        argv = ['email_sender.py', '--no-dotenv', '--message-text', 'Test digest',
                '--test-email', '--dry-run', '--to', 'production@example.com']
        output = io.StringIO()
        with patch('sys.argv', argv), patch('email_sender._connect_smtp') as smtp, \
                contextlib.redirect_stdout(output):
            email_sender.main()
        smtp.assert_not_called()
        self.assertIn('recipients=1', output.getvalue())
        self.assertIn('froggaguldev@gmail.com', output.getvalue())
        self.assertNotIn('production@example.com', output.getvalue())


if __name__ == "__main__":
    unittest.main()
