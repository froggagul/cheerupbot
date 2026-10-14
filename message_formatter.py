from __future__ import annotations

import html
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

KST = timezone(timedelta(hours=9))
SITE_NAMES = {
    "saramin": "사람인", "jobkorea": "잡코리아", "wanted": "원티드",
    "linkareer": "링커리어", "jasoseol": "자소설닷컴", "catch": "캐치",
}
WEEKDAYS = "월화수목금토일"


def render_jobs_message(
    rows: list[dict[str, Any]],
    *,
    title: str = "채용 공고 요약",
    max_items: int = 0,
    greeting: str = "",
    generated_at: datetime | None = None,
) -> str:
    now = (generated_at or datetime.now(KST)).astimezone(KST)
    shown = _shown_rows(rows, max_items)

    lines: list[str] = []
    greeting_text = (greeting or "").strip()
    if greeting_text:
        lines.append(greeting_text)
        lines.append("")

    lines.extend(
        [
            title.strip() or "채용 공고 요약",
            f"{now:%Y.%m.%d} ({WEEKDAYS[now.weekday()]}) · {now:%H:%M} KST",
            _count_label(len(rows), len(shown)),
        ]
    )

    keywords = sorted({keyword for row in rows for keyword in _row_keywords(row)})
    sites = sorted({str(row.get("site", "")).strip() for row in rows if str(row.get("site", "")).strip()})
    if keywords:
        lines.append("키워드: " + ", ".join(keywords))
    if sites:
        lines.append("사이트: " + ", ".join(_site_name(site) for site in sites))

    if not shown:
        lines.append("")
        lines.append("조건에 맞는 공고가 없습니다.")
        return "\n".join(lines).strip()

    lines.append("")
    for idx, row in enumerate(shown, start=1):
        site = _site_name(_as_text(row.get("site")))
        title_text = _as_text(row.get("title")) or "(제목 없음)"
        company = _as_text(row.get("company")) or "기업명 미제공"
        location = _as_text(row.get("location")) or "지역 확인 필요"
        deadline, _ = _deadline_info(_as_text(row.get("deadline")), now.date())
        link = _safe_job_url(row.get("link"))

        lines.append(f"{idx:02d}. {company}")
        lines.append(title_text)
        lines.append(f"마감: {deadline}")
        lines.append(f"지역: {location} · {site}")
        requirements = _requirements_label(row)
        if requirements:
            lines.append(f"지원 정보: {requirements}")
        if link:
            lines.append(f"공고 보기: {link}")
        lines.append("")

    return "\n".join(lines).strip()


def render_jobs_email_html(
    rows: list[dict[str, Any]],
    *,
    title: str = "채용 공고 요약",
    max_items: int = 0,
    greeting: str = "",
    generated_at: datetime | None = None,
) -> str:
    """Render structured job data with inline styles and table-based email layout."""
    now = (generated_at or datetime.now(KST)).astimezone(KST)
    shown = _shown_rows(rows, max_items)
    title = title.strip() or "채용 공고 요약"
    count_label = _count_label(len(rows), len(shown))
    urgent_count = sum(_deadline_info(_as_text(row.get("deadline")), now.date())[1] == "urgent" for row in shown)
    urgent_label = f" · 3일 이내 마감 {urgent_count}건" if urgent_count else ""
    greeting_html = (
        f'<p style="margin:14px 0 0;color:#475569;font-size:15px;line-height:1.7;">{html.escape(greeting.strip())}</p>'
        if greeting.strip() else ""
    )
    cards = "".join(_render_job_card(row, index, now.date()) for index, row in enumerate(shown, 1))
    if not shown:
        cards = '''<tr><td style="padding:32px 24px;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;text-align:center;">
<h2 style="margin:0 0 8px;font-size:19px;color:#0f172a;">조건에 맞는 공고가 없습니다.</h2>
<p style="margin:0;color:#475569;font-size:14px;line-height:1.7;">이번 채용 요약에 포함할 공고가 없어요.</p></td></tr>'''
    sites = sorted({_site_name(_as_text(row.get("site"))) for row in shown})
    source_label = " · ".join(sites)
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;color:#0f172a;font-family:Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{html.escape(count_label + urgent_label)}. 관심 있는 공고와 마감일을 확인해 보세요.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;"><tr><td align="center" style="padding:28px 12px;">
<!--[if mso]><table role="presentation" width="640"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;table-layout:fixed;overflow-wrap:anywhere;word-break:keep-all;">
<tr><td style="padding:26px 24px;background:#ffffff;border-top:4px solid #0f766e;border-radius:12px 12px 0 0;">
<p style="margin:0 0 16px;font-size:11px;font-weight:bold;letter-spacing:2px;color:#0f766e;">CHEERUPBOT</p>
<p style="margin:0 0 8px;color:#64748b;font-size:13px;">{now:%Y.%m.%d} ({WEEKDAYS[now.weekday()]})</p>
<h1 style="margin:0;font-size:28px;line-height:1.35;letter-spacing:-1px;color:#0f172a;">{html.escape(title)}</h1>{greeting_html}
</td></tr>
<tr><td style="padding:16px 24px;background:#e6f4f1;color:#115e59;font-size:15px;line-height:1.7;">
<strong>{html.escape(count_label)}</strong>{urgent_label}
<div style="margin-top:3px;font-size:12px;color:#475569;">HRD 우선 · 마감일 순</div></td></tr>
<tr><td height="20" style="font-size:0;line-height:0;">&nbsp;</td></tr>
{cards}
<tr><td style="padding:12px 12px 0;color:#64748b;font-size:12px;line-height:1.8;text-align:center;">
{html.escape(source_label)}<br>생성 {now:%Y.%m.%d %H:%M} KST · 마감일은 메일 생성 시점 기준입니다.<br>
지원 전 원문에서 모집 조건과 마감 시간을 확인해 주세요.</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def _render_job_card(row: dict[str, Any], index: int, today: date) -> str:
    company = html.escape(_as_text(row.get("company")) or "기업명 미제공")
    title = html.escape(_as_text(row.get("title")) or "(제목 없음)")
    location = html.escape(_as_text(row.get("location")) or "지역 확인 필요")
    site = html.escape(_site_name(_as_text(row.get("site"))))
    requirements = _requirements_label(row)
    requirements_html = f"<br>{html.escape(requirements)}" if requirements else ""
    deadline, tone = _deadline_info(_as_text(row.get("deadline")), today)
    background, color = {
        "urgent": ("#fff1f2", "#9f1239"),
        "open": ("#e6f4f1", "#115e59"),
        "normal": ("#f1f5f9", "#334155"),
        "closed": ("#f1f5f9", "#64748b"),
    }[tone]
    link = _safe_job_url(row.get("link"))
    if link:
        href = html.escape(link, quote=True)
        title = f'<a href="{href}" target="_blank" rel="noopener noreferrer" style="color:#0f172a;text-decoration:none;">{title}</a>'
        action = f'<a href="{href}" target="_blank" rel="noopener noreferrer" style="display:inline-block;background:#0f766e;color:#ffffff;text-decoration:none;font-size:14px;font-weight:bold;line-height:22px;padding:10px 18px;border-radius:6px;">공고 보기 &rarr;</a>'
    else:
        action = '<span style="font-size:13px;color:#64748b;">공고 링크 확인 필요</span>'
    return f'''<tr><td style="padding:22px 24px;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;">
<p style="margin:0 0 8px;font-size:13px;color:#475569;">{index:02d} &nbsp;·&nbsp; {company}</p>
<h2 style="margin:0 0 14px;font-size:19px;line-height:1.55;font-weight:bold;">{title}</h2>
<span style="display:inline-block;padding:5px 9px;border-radius:4px;background:{background};color:{color};font-size:12px;font-weight:bold;line-height:1.6;">{html.escape(deadline)}</span>
<p style="margin:12px 0 18px;color:#475569;font-size:13px;line-height:1.7;">{location} &nbsp;·&nbsp; {site}{requirements_html}</p>
{action}</td></tr><tr><td height="12" style="font-size:0;line-height:0;">&nbsp;</td></tr>'''


def _shown_rows(rows: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=_sort_key)
    return ordered[:max_items] if max_items > 0 else ordered


def _count_label(total: int, shown: int) -> str:
    return f"총 {total}건 · {shown}건 표시" if shown < total else f"총 {total}건"


def _site_name(site: str) -> str:
    return SITE_NAMES.get(site.lower(), site) or "출처 확인 필요"


def _requirements_label(row: dict[str, Any]) -> str:
    experience = _as_text(row.get("experience"))
    employment = _as_text(row.get("employment_type"))
    employment = {
        "full_time": "풀타임", "fulltime": "풀타임", "regular": "정규직",
        "part_time": "파트타임", "parttime": "파트타임", "contract": "계약직",
        "contractor": "계약직", "temporary": "기간제", "intern": "인턴",
        "internship": "인턴", "freelance": "프리랜서",
    }.get(employment.lower().replace("-", "_").replace(" ", "_"), employment)
    # Some APIs expose numeric employment codes without a human-readable label.
    if employment.isdigit():
        employment = ""
    return " · ".join(dict.fromkeys(value for value in (experience, employment) if value))


def _row_keywords(row: dict[str, Any]) -> list[str]:
    matched = row.get("matched_keywords")
    values = [row.get("keyword"), *(matched if isinstance(matched, list) else [])]
    return list(dict.fromkeys(_as_text(value) for value in values if _as_text(value)))


def _safe_job_url(value: Any) -> str:
    url = str(value).strip() if value is not None else ""
    if any(char.isspace() or ord(char) < 32 for char in url):
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return url
    except ValueError:
        pass
    return ""


def _deadline_info(deadline: str, today: date) -> tuple[str, str]:
    token = deadline.strip()
    if not token:
        return "마감일 확인 필요", "normal"
    if token.upper() == "OPEN":
        return "상시채용", "open"
    try:
        end = date.fromisoformat(token)
    except ValueError:
        return token, "normal"
    days = (end - today).days
    formatted = f"{end:%Y.%m.%d} ({WEEKDAYS[end.weekday()]})"
    if days < 0:
        return f"마감 · {formatted}", "closed"
    if days == 0:
        return f"오늘 마감 · {formatted}", "urgent"
    return f"D-{days} · {formatted}", "urgent" if days <= 3 else "normal"


def _sort_key(row: dict[str, Any]) -> tuple[int, tuple[int, str], str, str, str]:
    hrd_priority = _keyword_priority(row)
    deadline = _as_text(row.get("deadline"))
    deadline_key = _deadline_sort_key(deadline)
    site = _as_text(row.get("site")).lower()
    title = _as_text(row.get("title")).lower()
    company = _as_text(row.get("company")).lower()
    return hrd_priority, deadline_key, site, title, company


def _keyword_priority(row: dict[str, Any]) -> int:
    return 0 if any("hrd" in keyword.lower() for keyword in _row_keywords(row)) else 1


def _deadline_sort_key(deadline: str) -> tuple[int, str]:
    token = (deadline or "").strip().upper()
    if not token:
        return 2, "9999-12-31"
    if token == "OPEN":
        return 1, "9999-12-30"
    if len(token) >= 10 and token[4] == "-" and token[7] == "-":
        return 0, token[:10]
    return 1, token


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
