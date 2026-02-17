from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))


def render_jobs_message(
    rows: list[dict[str, Any]],
    *,
    title: str = "채용 공고 요약",
    max_items: int = 0,
    greeting: str = "",
) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    ordered = sorted(rows, key=_sort_key)
    if not rows:
        shown: list[dict[str, Any]] = []
    elif max_items <= 0:
        shown = ordered
    else:
        shown = ordered[:max_items]

    lines: list[str] = []
    greeting_text = (greeting or "").strip()
    if greeting_text:
        lines.append(greeting_text)
        lines.append("")

    lines.extend(
        [
            title.strip() or "채용 공고 요약",
            f"생성: {now} KST",
            f"총 {len(rows)}건",
        ]
    )

    keywords = sorted({str(row.get("keyword", "")).strip() for row in rows if str(row.get("keyword", "")).strip()})
    sites = sorted({str(row.get("site", "")).strip() for row in rows if str(row.get("site", "")).strip()})
    if keywords:
        lines.append("키워드: " + ", ".join(keywords))
    if sites:
        lines.append("사이트: " + ", ".join(sites))

    if not shown:
        lines.append("")
        lines.append("조건에 맞는 공고가 없습니다.")
        return "\n".join(lines).strip()

    lines.append("")
    for idx, row in enumerate(shown, start=1):
        site = _as_text(row.get("site")) or "-"
        title_text = _as_text(row.get("title")) or "(제목 없음)"
        company = _as_text(row.get("company")) or "-"
        location = _as_text(row.get("location")) or "-"
        deadline = _deadline_label(_as_text(row.get("deadline")))
        link = _as_text(row.get("link"))

        lines.append(f"{idx}. [{company}] {title_text}")
        lines.append(f"사이트: {site} | 지역: {location} | 마감: {deadline}")
        if link:
            lines.append(f"링크: {link}")
        lines.append("")

    return "\n".join(lines).strip()


def _sort_key(row: dict[str, Any]) -> tuple[int, str, str, str]:
    hrd_priority = _keyword_priority(row)
    deadline = _as_text(row.get("deadline"))
    deadline_key = _deadline_sort_key(deadline)
    site = _as_text(row.get("site")).lower()
    title = _as_text(row.get("title")).lower()
    company = _as_text(row.get("company")).lower()
    return hrd_priority, deadline_key, site, title, company


def _keyword_priority(row: dict[str, Any]) -> int:
    keyword = _as_text(row.get("keyword")).lower()
    return 0 if "hrd" in keyword else 1


def _deadline_sort_key(deadline: str) -> tuple[int, str]:
    token = (deadline or "").strip().upper()
    if not token:
        return 2, "9999-12-31"
    if token == "OPEN":
        return 1, "9999-12-30"
    if len(token) >= 10 and token[4] == "-" and token[7] == "-":
        return 0, token[:10]
    return 1, token


def _deadline_label(deadline: str) -> str:
    token = (deadline or "").strip()
    if not token:
        return "-"
    if token.upper() == "OPEN":
        return "상시채용"
    return token


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
