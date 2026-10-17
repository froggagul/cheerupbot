from __future__ import annotations

import argparse
import html
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv


class EmailSendError(RuntimeError):
    """Raised when email sending fails."""


URL_PATTERN = re.compile(r"https?://[^\s<>'\"`]+")
URL_TRAILING_PUNCT = ".,;:!?)]}>\"'"
TEST_EMAIL_RECIPIENT = "froggaguldev@gmail.com"


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=".env", help="Path to .env file")
    pre_parser.add_argument("--no-dotenv", action="store_true", help="Disable .env loading")
    pre_args, _ = pre_parser.parse_known_args()
    if not pre_args.no_dotenv:
        load_dotenv(dotenv_path=pre_args.env_file)

    parser = argparse.ArgumentParser(description="Send job summary text to recipient email list")
    parser.add_argument("--env-file", default=pre_args.env_file, help="Path to .env file")
    parser.add_argument("--no-dotenv", action="store_true", help="Disable .env loading")
    parser.add_argument("--message-file", default="jobs_message.txt", help="Message text file path")
    parser.add_argument("--message-text", default="", help="Inline message text (overrides --message-file)")
    parser.add_argument("--html-file", default="", help="Formatted HTML email paired with the plain-text message")
    parser.add_argument("--preview-html", default="", help="Save the HTML preview and exit without sending (no SMTP required)")
    parser.add_argument("--subject", default="[알림] 오늘의 채용 요약", help="Email subject")
    parser.add_argument(
        "--recipients-file",
        default="recipients.txt",
        help="Recipient list file (newline-delimited)",
    )
    parser.add_argument(
        "--to",
        action="append",
        default=[],
        help="Recipient email address (repeatable). Combined with --recipients-file",
    )
    parser.add_argument(
        "--smtp-host",
        default=os.getenv("SMTP_HOST", ""),
        help="SMTP host (default: env SMTP_HOST)",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=int(os.getenv("SMTP_PORT", "587")),
        help="SMTP port (default: env SMTP_PORT or 587)",
    )
    parser.add_argument(
        "--smtp-user",
        default=os.getenv("SMTP_USER", ""),
        help="SMTP username/login (default: env SMTP_USER)",
    )
    parser.add_argument(
        "--smtp-password",
        default=os.getenv("SMTP_PASSWORD", ""),
        help="SMTP password or app-password (default: env SMTP_PASSWORD)",
    )
    parser.add_argument(
        "--from-email",
        default=os.getenv("SMTP_FROM", ""),
        help="Sender email address (default: env SMTP_FROM, then smtp-user)",
    )
    parser.add_argument(
        "--reply-to",
        default="",
        help="Optional Reply-To header",
    )
    parser.add_argument(
        "--use-ssl",
        action="store_true",
        help="Use SMTP over SSL (typically port 465)",
    )
    parser.add_argument(
        "--no-starttls",
        action="store_true",
        help="Disable STARTTLS for plain SMTP",
    )
    parser.add_argument(
        "--send-mode",
        choices=["single", "individual"],
        default="single",
        help="single: one email with all recipients in BCC, individual: one email per recipient",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send; print summary only")
    parser.add_argument("--test-email", action="store_true",
                        help=f"Send only to {TEST_EMAIL_RECIPIENT}; ignore all other recipients and prefix the subject with [TEST]")
    parser.add_argument("--debug", action="store_true", help="Print debug logs")
    args = parser.parse_args()
    if args.preview_html and any(
        path and Path(args.preview_html).resolve() == Path(path).resolve()
        for path in (args.message_file, args.html_file)
    ):
        parser.error("--preview-html must use a different path from the input files")
    return args


def _log(msg: str, debug: bool) -> None:
    if debug:
        print(msg, file=sys.stderr)


def _load_message(message_file: str, inline_text: str) -> str:
    text = inline_text.strip()
    if text:
        return text

    path = Path(message_file)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise EmailSendError(f"Failed to read message file '{path}': {exc}") from exc
    if not text:
        raise EmailSendError(f"Message is empty: {path}")
    return text


def _load_recipients(recipients_file: str, cli_recipients: list[str]) -> list[str]:
    recipients: list[str] = []
    for item in cli_recipients:
        token = item.strip()
        if token:
            recipients.append(token)

    path = Path(recipients_file)
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EmailSendError(f"Failed to read recipients file '{path}': {exc}") from exc
        for line in raw.splitlines():
            token = line.strip()
            if not token or token.startswith("#"):
                continue
            recipients.append(token)

    deduped: list[str] = []
    seen: set[str] = set()
    for recipient in recipients:
        key = recipient.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(recipient)

    if not deduped:
        raise EmailSendError("No recipients. Add addresses via --to or recipients file.")
    return deduped


def _load_html_body(html_file: str, body: str) -> str:
    if not html_file:
        return _render_html_body(body)
    try:
        content = Path(html_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise EmailSendError(f"Failed to read HTML file '{html_file}': {exc}") from exc
    if not content:
        raise EmailSendError(f"HTML email is empty: {html_file}")
    return content


def _validate_sender(from_email: str, smtp_user: str) -> str:
    sender = from_email.strip() or smtp_user.strip()
    if not sender:
        raise EmailSendError("Missing sender address. Set --from-email or SMTP_FROM (or smtp-user as fallback).")
    return sender


def _build_message(
    *,
    subject: str,
    sender: str,
    recipients: list[str],
    body: str,
    reply_to: str,
    use_bcc: bool,
    html_body: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    if use_bcc:
        msg["To"] = sender
        msg["Bcc"] = ", ".join(recipients)
    else:
        msg["To"] = ", ".join(recipients)
    if reply_to.strip():
        msg["Reply-To"] = reply_to.strip()
    msg.set_content(body, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body if html_body is not None else _render_html_body(body), subtype="html", charset="utf-8")
    return msg


def _render_html_body(body: str) -> str:
    chunks: list[str] = []
    cursor = 0

    for match in URL_PATTERN.finditer(body):
        start, end = match.span()
        chunks.append(html.escape(body[cursor:start]))

        raw_url = match.group(0)
        link_url, trailing = _split_url_trailing_punct(raw_url)
        escaped_href = html.escape(link_url, quote=True)
        escaped_label = html.escape(link_url)
        chunks.append(
            f'<a href="{escaped_href}" target="_blank" rel="noopener noreferrer" style="color:#0f766e;word-break:break-all;">{escaped_label}</a>'
        )
        if trailing:
            chunks.append(html.escape(trailing))
        cursor = end

    chunks.append(html.escape(body[cursor:]))
    linked = "".join(chunks).replace("\n", "<br>\n")
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="margin:0;padding:20px 12px;background:#f1f5f9;">'
        '<div style="max-width:640px;margin:0 auto;padding:24px;background:#ffffff;border:1px solid #e2e8f0;'
        'border-radius:12px;font-family:Arial,\'Malgun Gothic\',sans-serif;font-size:16px;line-height:1.8;'
        'color:#0f172a;overflow-wrap:anywhere;">'
        f"{linked}"
        "</div></body></html>"
    )


def _split_url_trailing_punct(url: str) -> tuple[str, str]:
    core = url
    trailing = ""
    while core and core[-1] in URL_TRAILING_PUNCT:
        trailing = core[-1] + trailing
        core = core[:-1]
    if not core:
        return url, ""
    return core, trailing


def _connect_smtp(args: argparse.Namespace) -> smtplib.SMTP:
    host = args.smtp_host.strip()
    user = args.smtp_user.strip()
    password = args.smtp_password

    if not host:
        raise EmailSendError("Missing SMTP host. Set --smtp-host or SMTP_HOST.")
    if not user:
        raise EmailSendError("Missing SMTP user. Set --smtp-user or SMTP_USER.")
    if not password:
        raise EmailSendError("Missing SMTP password. Set --smtp-password or SMTP_PASSWORD.")

    timeout = 20
    if args.use_ssl:
        context = ssl.create_default_context()
        client = smtplib.SMTP_SSL(host=host, port=args.smtp_port, timeout=timeout, context=context)
    else:
        client = smtplib.SMTP(host=host, port=args.smtp_port, timeout=timeout)
        if not args.no_starttls:
            context = ssl.create_default_context()
            client.starttls(context=context)
    client.login(user=user, password=password)
    return client


def main() -> None:
    args = parse_args()
    try:
        body = _load_message(args.message_file, args.message_text)
        html_body = _load_html_body(args.html_file, body)
        if args.preview_html:
            try:
                Path(args.preview_html).write_text(html_body, encoding="utf-8")
            except OSError as exc:
                raise EmailSendError(f"Failed to write HTML preview: {args.preview_html}") from exc
            print(f"Saved HTML preview to {args.preview_html}; no email sent.")
            return
        if args.test_email:
            recipients = [TEST_EMAIL_RECIPIENT]
            args.subject = f"[TEST] {args.subject}"
        else:
            recipients = _load_recipients(args.recipients_file, args.to)
        sender = (args.from_email.strip() or args.smtp_user.strip() or "(unset)")
        if not args.dry_run:
            sender = _validate_sender(args.from_email, args.smtp_user)
        _log(f"[email] recipients={len(recipients)} mode={args.send_mode}", debug=args.debug)

        if args.dry_run:
            print(
                f"[dry-run] from={sender} recipients={len(recipients)} "
                f"subject={args.subject!r} body_len={len(body)} mode={args.send_mode}"
            )
            for recipient in recipients:
                print(f" - {recipient}")
            return

        client = _connect_smtp(args)
        try:
            if args.send_mode == "single":
                msg = _build_message(
                    subject=args.subject,
                    sender=sender,
                    recipients=recipients,
                    body=body,
                    html_body=html_body,
                    reply_to=args.reply_to,
                    use_bcc=not args.test_email,
                )
                # Explicit envelope recipients prevent the sender's display To header
                # from receiving an unintended copy in BCC mode.
                client.send_message(msg, to_addrs=recipients)
                print(f"Sent 1 email to {len(recipients)} recipient(s).")
            else:
                sent = 0
                for recipient in recipients:
                    msg = _build_message(
                        subject=args.subject,
                        sender=sender,
                        recipients=[recipient],
                        body=body,
                        html_body=html_body,
                        reply_to=args.reply_to,
                        use_bcc=False,
                    )
                    client.send_message(msg, to_addrs=[recipient])
                    sent += 1
                print(f"Sent {sent} individual email(s).")
        finally:
            client.quit()
    except EmailSendError as exc:
        raise SystemExit(str(exc)) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise SystemExit(f"SMTP send failed: {type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    main()
