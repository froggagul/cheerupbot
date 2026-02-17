from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from job_filters import FilterSpecError, build_filter_predicate, load_filter_spec, load_plugin_predicates
from job_crawler import DEFAULT_SITES, JobCrawler
from message_formatter import render_jobs_message

KST = timezone(timedelta(hours=9))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Korean job sites for keyword-based job postings")
    parser.add_argument(
        "--sites",
        default=",".join(DEFAULT_SITES),
        help="Comma-separated site ids (default: all configured sites)",
    )
    parser.add_argument(
        "--keywords",
        default="HR,HRD",
        help="Comma-separated keywords to search",
    )
    parser.add_argument(
        "--limit-per-site",
        type=int,
        default=20,
        help="Maximum postings per site/keyword pair",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Write results to a file path (JSON in json mode, text in message mode)",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "message"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--input",
        default="",
        help="Read postings from an existing JSON file instead of crawling",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print crawl diagnostics to stderr",
    )
    parser.add_argument(
        "--render-js",
        action="store_true",
        help="Use Playwright rendering for JS-heavy sites (wanted/linkareer/jasoseol/catch)",
    )
    parser.add_argument(
        "--dump-dir",
        default="",
        help="Directory to dump raw responses (html/json) for debugging",
    )
    parser.add_argument(
        "--filter-file",
        default="",
        help="Path to JSON filter spec file",
    )
    parser.add_argument(
        "--filter-json",
        default="",
        help="Inline JSON filter spec string",
    )
    parser.add_argument(
        "--filter-plugin",
        action="append",
        default=[],
        help="Python predicate plugin as module:function or /path/to/file.py:function (repeatable)",
    )
    parser.add_argument(
        "--message-title",
        default="채용 공고 요약",
        help="Message title used when --output-format message",
    )
    parser.add_argument(
        "--message-max-items",
        type=int,
        default=0,
        help="Maximum postings to include in message mode (0 means all)",
    )
    parser.add_argument(
        "--message-nickname-file",
        default="nicknames.txt",
        help="Path to newline-delimited nicknames used in message mode",
    )
    parser.add_argument(
        "--message-greeting-template",
        default="안녕 {nickname}야! 오늘의 채용 요약이야.",
        help="Greeting template for message mode (must include {nickname}; empty disables greeting)",
    )
    return parser.parse_args()


def _parse_collected_date(collected_at: str) -> date:
    if not collected_at:
        return datetime.now(KST).date()
    try:
        dt = datetime.fromisoformat(collected_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).date()
    except ValueError:
        return datetime.now(KST).date()


def _resolve_year(year_text: str | None, month: int, day: int, base: date) -> int:
    if year_text:
        if len(year_text) == 2:
            return 2000 + int(year_text)
        return int(year_text)
    return base.year


def _to_iso_date(year: int, month: int, day: int) -> str:
    return date(year, month, day).isoformat()


def normalize_deadline(deadline: str, collected_at: str) -> str:
    raw = " ".join((deadline or "").replace("~", "").replace("–", "-").replace("—", "-").split())
    if not raw:
        return ""

    base = _parse_collected_date(collected_at)
    lowered = raw.lower()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}t.*", lowered):
        return raw[:10]

    if raw in {"채용시", "채용시 마감", "채용 시 마감", "상시채용", "수시"}:
        return "OPEN"

    if raw in {"오늘마감", "오늘 마감"}:
        return base.isoformat()
    if raw in {"내일마감", "내일 마감"}:
        return (base + timedelta(days=1)).isoformat()

    dday_match = re.search(r"D-?\s*(\d+)", raw, flags=re.IGNORECASE)
    if dday_match:
        days = int(dday_match.group(1))
        return (base + timedelta(days=days)).isoformat()

    range_match = re.search(
        r"(?:(\d{2,4})/)?(\d{2})/(\d{2})\s*-\s*(?:(\d{2,4})/)?(\d{2})/(\d{2})",
        raw,
    )
    if range_match:
        end_year = _resolve_year(range_match.group(4), int(range_match.group(5)), int(range_match.group(6)), base)
        try:
            return _to_iso_date(end_year, int(range_match.group(5)), int(range_match.group(6)))
        except ValueError:
            return raw

    date_match = re.search(r"~?\s*(?:(\d{2,4})/)?(\d{2})/(\d{2})(?:\([^)]+\))?(?:\s*마감)?", raw)
    if date_match:
        year = _resolve_year(date_match.group(1), int(date_match.group(2)), int(date_match.group(3)), base)
        try:
            return _to_iso_date(year, int(date_match.group(2)), int(date_match.group(3)))
        except ValueError:
            return raw

    return raw


def _load_payload_from_input(path: str) -> list[dict]:
    input_path = Path(path)
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Failed to read input JSON: {input_path} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in input file: {input_path} ({exc})") from exc

    if not isinstance(payload, list):
        raise SystemExit(f"Input JSON must be an array of postings: {input_path}")

    rows: list[dict] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _crawl_payload(args: argparse.Namespace) -> list[dict]:
    sites = [s.strip().lower() for s in args.sites.split(",") if s.strip()]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    dump_dir = None
    if args.dump_dir:
        timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
        dump_dir = str(Path(args.dump_dir) / timestamp)

    crawler = JobCrawler(debug=args.debug, render_js=args.render_js, dump_dir=dump_dir)
    try:
        results = crawler.crawl(sites=sites, keywords=keywords, limit_per_site=args.limit_per_site)
    finally:
        crawler.close()

    rows: list[dict] = []
    for item in results:
        rows.append(item.to_dict())
    return rows


def _normalize_payload_deadlines(rows: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for row in rows:
        out = dict(row)
        raw_deadline = out.get("deadline")
        raw_collected_at = out.get("collected_at")
        deadline_text = raw_deadline if isinstance(raw_deadline, str) else ""
        collected_at_text = raw_collected_at if isinstance(raw_collected_at, str) else ""
        out["deadline"] = normalize_deadline(deadline_text, collected_at_text)
        normalized.append(out)
    return normalized


def _apply_filters(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    try:
        spec = load_filter_spec(filter_file=args.filter_file, filter_json=args.filter_json)
        predicate = build_filter_predicate(spec)
        plugin_predicates = load_plugin_predicates(args.filter_plugin)
    except FilterSpecError as exc:
        raise SystemExit(f"Invalid filter spec: {exc}") from exc

    if spec is None and not plugin_predicates:
        return rows

    filtered = [row for row in rows if predicate(row)]
    for plugin_predicate in plugin_predicates:
        filtered = [row for row in filtered if plugin_predicate(row)]
    if args.debug:
        print(
            f"[filter] before={len(rows)} after={len(filtered)} plugins={len(plugin_predicates)}",
            file=sys.stderr,
        )
    return filtered


def _load_nicknames(path: str) -> list[str]:
    if not path.strip():
        return []
    nickname_path = Path(path)
    if not nickname_path.exists():
        return []
    try:
        raw = nickname_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Failed to read nickname file: {nickname_path} ({exc})") from exc
    nicknames: list[str] = []
    for line in raw.splitlines():
        token = line.strip()
        if not token:
            continue
        if token.startswith("#"):
            continue
        nicknames.append(token)
    return nicknames


def _build_message_greeting(args: argparse.Namespace) -> str:
    template = (args.message_greeting_template or "").strip()
    if not template:
        return ""
    nicknames = _load_nicknames(args.message_nickname_file)
    if not nicknames:
        return ""
    nickname = random.choice(nicknames)
    return template.replace("{nickname}", nickname)


def main() -> None:
    args = parse_args()
    if args.input:
        payload = _load_payload_from_input(args.input)
    else:
        payload = _crawl_payload(args)
    payload = _normalize_payload_deadlines(payload)
    payload = _apply_filters(payload, args)

    if args.output_format == "message":
        greeting = _build_message_greeting(args)
        message = render_jobs_message(
            payload,
            title=args.message_title,
            max_items=args.message_max_items,
            greeting=greeting,
        )
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(message, encoding="utf-8")
            print(f"Saved message for {len(payload)} postings to {output_path}")
            return
        print(message)
        return

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {len(payload)} postings to {output_path}")
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
