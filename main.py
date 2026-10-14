from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from agent_client import AgentError, create_agent_client
from agent_filter import AgentJobFilter
from crawl_state import CrawlState, StateError
from job_filters import FilterSpecError, build_filter_predicate, load_filter_spec, load_plugin_predicates
from job_crawler import DEFAULT_SITES, CrawlError, JobCrawler
from message_formatter import render_jobs_email_html, render_jobs_message

KST = timezone(timedelta(hours=9))


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=".env")
    pre_parser.add_argument("--no-dotenv", action="store_true")
    pre_args, _ = pre_parser.parse_known_args()
    if not pre_args.no_dotenv:
        load_dotenv(pre_args.env_file)

    parser = argparse.ArgumentParser(description="Crawl Korean job sites for keyword-based job postings")
    parser.add_argument("--env-file", default=pre_args.env_file, help="Path to .env file")
    parser.add_argument("--no-dotenv", action="store_true", help="Disable .env loading")
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
        help="Maximum postings per site/keyword pair after filtering (default: 20)",
    )
    parser.add_argument("--candidate-limit-per-site", type=int, default=0,
                        help="Candidates per site/keyword; 0 uses 3x the output limit when filtering")
    parser.add_argument("--max-pages", type=int, default=3, help="Search request budget per site/keyword")
    parser.add_argument("--max-requests", type=int, default=20,
                        help="Total request budget per site/keyword, excluding HTTP retries")
    parser.add_argument("--max-detail-requests", type=int, default=10,
                        help="Wanted detail request budget per site/keyword; 0 disables enrichment")
    parser.add_argument("--crawl-report", default="", help="Write per-site crawl outcomes as JSON")
    parser.add_argument("--state-file", default="", help="Restore/update a SQLite SQL text snapshot")
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
        "--agent-filter", choices=["none", "openai", "codex"], default="none",
        help="Prompt-based filter applied after any explicitly requested DSL/plugins (default: none)",
    )
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--agent-prompt", default=None, help="Inline heuristic policy")
    prompt_group.add_argument(
        "--agent-prompt-file", default="",
        help="Heuristic policy file (default: filters/new_grad_prompt.txt)",
    )
    parser.add_argument("--agent-model", default="", help="Override OPENAI_MODEL or CODEX_MODEL")
    parser.add_argument(
        "--agent-batch-size", type=int, default=os.getenv("JOB_AGENT_BATCH_SIZE", "20"),
        help="Postings per agent request, 1-100 (default: 20)",
    )
    parser.add_argument(
        "--agent-timeout", type=float, default=os.getenv("JOB_AGENT_TIMEOUT", "120"),
        help="Request timeout in seconds (OpenAI may retry twice; default: 120)",
    )
    parser.add_argument(
        "--agent-report", default="", help="Write decisions and reasons for all agent-evaluated postings to JSON",
    )
    parser.add_argument(
        "--message-title",
        default="채용 공고 요약",
        help="Message title used when --output-format message",
    )
    parser.add_argument(
        "--message-html-output", default="",
        help="Also write an HTML email alongside the plain message (requires --output-format message)",
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
    args = parser.parse_args()
    if args.state_file:
        if args.output_format != "json":
            parser.error("--state-file requires JSON output; rendering a message must not update crawl state")
        if any(path and Path(args.state_file).resolve() == Path(path).resolve()
               for path in (args.input, args.output, args.agent_report, args.crawl_report, args.agent_prompt_file, args.filter_file)):
            parser.error("--state-file must use a separate path from inputs and outputs")
    if args.message_html_output:
        if args.output_format != "message":
            parser.error("--message-html-output requires --output-format message")
        if any(path and Path(args.message_html_output).resolve() == Path(path).resolve()
               for path in (args.input, args.output, args.agent_report, args.crawl_report)):
            parser.error("--message-html-output must use a separate path from input and other outputs")
    if args.limit_per_site < 1 or args.candidate_limit_per_site < 0:
        parser.error("--limit-per-site must be positive; --candidate-limit-per-site must be nonnegative")
    if args.max_pages < 1 or args.max_requests < 1 or args.max_detail_requests < 0:
        parser.error("Page/request budgets must be positive; detail budget must be nonnegative")
    if args.crawl_report:
        if args.input:
            parser.error("--crawl-report requires a live crawl, not --input")
        if any(path and Path(args.crawl_report).resolve() == Path(path).resolve()
               for path in (args.output, args.agent_report)):
            parser.error("--crawl-report, --agent-report and --output must use different paths")
    if args.agent_filter == "none" and (args.agent_prompt is not None or args.agent_prompt_file or args.agent_report):
        parser.error("--agent-prompt, --agent-prompt-file and --agent-report require --agent-filter")
    if args.agent_report and args.output and Path(args.agent_report).resolve() == Path(args.output).resolve():
        parser.error("--agent-report and --output must use different paths")
    return args


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
    # Yearless deadlines near New Year belong to the closest calendar year.
    if month - base.month < -6:
        return base.year + 1
    if month - base.month > 6:
        return base.year - 1
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
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(KST)
            return timestamp.date().isoformat()
        except ValueError:
            return raw

    if raw in {"채용시", "채용시 마감", "채용 시 마감", "상시채용", "수시"}:
        return "OPEN"

    if raw in {"오늘마감", "오늘 마감"} or lowered == "d-day":
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

    date_match = re.search(r"(?:(\d{2,4})[/.])?(\d{1,2})[/.](\d{1,2})(?:\([^)]+\))?(?:\s*마감)?", raw)
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

    filtering = bool(args.filter_file or args.filter_json or args.filter_plugin or args.agent_filter != "none")
    candidate_limit = args.candidate_limit_per_site or args.limit_per_site * (3 if filtering else 1)
    crawler = JobCrawler(debug=args.debug, render_js=args.render_js, dump_dir=dump_dir,
                         max_pages=args.max_pages, max_requests=args.max_requests,
                         max_detail_requests=args.max_detail_requests)
    try:
        results = crawler.crawl(sites=sites, keywords=keywords, limit_per_site=candidate_limit)
    except (CrawlError, ValueError) as exc:
        raise SystemExit(f"Crawl failed: {exc}") from exc
    finally:
        crawler.close()
        if args.crawl_report:
            Path(args.crawl_report).write_text(
                json.dumps([asdict(outcome) for outcome in crawler.outcomes], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    rows: list[dict] = []
    for item in results:
        rows.append(item.to_dict())
    return rows


def _limit_crawled_results(rows: list[dict], limit: int) -> list[dict]:
    counts: Counter = Counter()
    kept: list[dict] = []
    for row in rows:
        keywords = row.get("matched_keywords") or [row.get("keyword", "")]
        keys = [(row.get("site", ""), keyword) for keyword in dict.fromkeys(keywords)]
        if any(counts[key] >= limit for key in keys):
            continue
        kept.append(row)
        counts.update(keys)
    return kept


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


def _build_agent_filter(args: argparse.Namespace) -> AgentJobFilter | None:
    if args.agent_filter == "none":
        return None
    prompt = args.agent_prompt
    if prompt is None:
        path = Path(args.agent_prompt_file) if args.agent_prompt_file else (
            Path(__file__).parent / "filters" / "new_grad_prompt.txt"
        )
        try:
            prompt = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentError(f"Could not read agent prompt file: {path}") from exc
    client = create_agent_client(args.agent_filter, model=args.agent_model, timeout=args.agent_timeout)
    return AgentJobFilter(client, prompt, batch_size=args.agent_batch_size)


def _apply_agent_filter(rows: list[dict], agent: AgentJobFilter, args: argparse.Namespace,
                        state: CrawlState | None = None) -> list[dict]:
    result = state.filter(rows, agent, args.agent_filter) if state is not None else agent.filter(rows)
    if args.agent_report:
        report = {
            "provider": args.agent_filter,
            "model": getattr(agent.client, "model", "") or "CLI default",
            "evaluated_at": datetime.now(KST).isoformat(),
            "input_count": len(rows),
            "kept_count": len(result.rows),
            "decisions": [
                {**item, "title": rows[item["id"]].get("title", ""), "link": rows[item["id"]].get("link", "")}
                for item in result.decisions
            ],
        }
        if args.agent_filter == "openai":
            report["reasoning_effort"] = agent.client.reasoning_effort or "model default"
        try:
            Path(args.agent_report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            raise AgentError(f"Could not write agent report: {args.agent_report}") from exc
    if args.debug:
        print(f"[agent-filter] provider={args.agent_filter} before={len(rows)} after={len(result.rows)}", file=sys.stderr)
    return result.rows


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
    try:
        with CrawlState(args.state_file) if args.state_file else nullcontext(None) as state:
            _run(args, state)
    except StateError as exc:
        raise SystemExit(f"Crawl state failed: {exc}") from exc


def _run(args: argparse.Namespace, state: CrawlState | None) -> None:
    try:
        agent = _build_agent_filter(args)
    except AgentError as exc:
        raise SystemExit(f"Agent filter failed: {exc}") from exc
    if args.input:
        payload = _load_payload_from_input(args.input)
    else:
        payload = _crawl_payload(args)
    payload = _normalize_payload_deadlines(payload)
    if state is not None:
        state.record(payload)
    payload = _apply_filters(payload, args)
    if agent is not None:
        try:
            payload = _apply_agent_filter(payload, agent, args, state)
        except AgentError as exc:
            raise SystemExit(f"Agent filter failed: {exc}") from exc

    if not args.input:
        payload = _limit_crawled_results(payload, args.limit_per_site)

    if args.output_format == "message":
        greeting = _build_message_greeting(args)
        generated_at = datetime.now(KST)
        message = render_jobs_message(
            payload,
            title=args.message_title,
            max_items=args.message_max_items,
            greeting=greeting,
            generated_at=generated_at,
        )
        if args.message_html_output:
            email_html = render_jobs_email_html(
                payload, title=args.message_title, max_items=args.message_max_items,
                greeting=greeting, generated_at=generated_at,
            )
            Path(args.message_html_output).write_text(email_html, encoding="utf-8")
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
