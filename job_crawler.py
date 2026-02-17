from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

DEFAULT_SITES = [
    "saramin",
    "jobkorea",
    "wanted",
    "linkareer",
    "jasoseol",
    "catch",
]

SITE_CONFIGS = {
    "saramin": {
        "base_url": "https://www.saramin.co.kr",
        "search_url": "https://www.saramin.co.kr/zf_user/search/recruit?searchType=search&searchword={query}",
        "containers": [".item_recruit", ".list_item", ".item", "article"],
        "title": [".job_tit a", ".tit_job a", "a"],
        "company": [".corp_name", ".company_nm", ".job_company"],
        "location": [".job_condition span", ".loc_m", ".job_sector"],
        "deadline": [".job_date .date", ".date", ".end_day"],
    },
    "jobkorea": {
        "base_url": "https://www.jobkorea.co.kr",
        "search_url": "https://www.jobkorea.co.kr/Search/?stext={query}",
        "containers": [".list-post li", ".post", ".recruit-info", "article"],
        "title": [".title", ".post-list-info a", "a"],
        "company": [".name", ".corp-name-link", ".company"],
        "location": [".loc", ".option", ".etc"],
        "deadline": [".date", ".dday", ".option"],
    },
    "wanted": {
        "base_url": "https://www.wanted.co.kr",
        "api_url": "https://www.wanted.co.kr/api/chaos/search/v1/position",
        "search_urls": [],
        "containers": ["[data-cy='job-card']", ".JobCard_container__FqChn", "article", "li"],
        "title": ["a", "h2", "h3"],
        "company": [".JobCard_companyName__vZMqJ", ".company", "span"],
        "location": [".JobCard_location__Rj1L2", ".location", "span"],
        "deadline": [".JobCard_deadline__AX7fX", ".deadline", ".dday"],
    },
    "linkareer": {
        "base_url": "https://linkareer.com",
        "search_urls": [
            "https://linkareer.com/search?direction=DESC&page=1&q={query}&sort=CREATED_AT&tab=open-activity",
        ],
        "containers": [".activity-list-item", ".search-item", "article", "li"],
        "title": ["h2 a", ".title a", "a"],
        "company": [".organization-name", ".company", ".subtitle"],
        "location": [".location", ".region", ".meta"],
        "deadline": [".due-date", ".deadline", ".meta"],
    },
    "jasoseol": {
        "base_url": "https://jasoseol.com",
        "api_url": "https://jasoseol.com/api/v1/employment_companies",
        "search_urls": [
        ],
        "containers": [".recruit-card", ".recruit-list-item", "article", "li"],
        "title": [".title a", "h3 a", "a"],
        "company": [".company-name", ".company", ".subtitle"],
        "location": [".location", ".region", ".meta"],
        "deadline": [".end-date", ".deadline", ".meta"],
    },
    "catch": {
        "base_url": "https://www.catch.co.kr",
        "api_url": "https://www.catch.co.kr/api/v1.0/recruit/information/getRecruitList",
        "search_urls": [
            "https://www.catch.co.kr/NCS/RecruitSearch/?keyword={query}",
            "https://www.catch.co.kr/NCS/RecruitSearch?keyword={query}",
        ],
        "containers": [".recruit_list li", ".recruit-item", "article", "li"],
        "title": [".title a", "h3 a", "a"],
        "company": [".company", ".corp", ".subtitle"],
        "location": [".location", ".region", ".meta"],
        "deadline": [".deadline", ".dday", ".meta"],
    },
}


@dataclass
class JobPosting:
    site: str
    keyword: str
    title: str
    company: str
    location: str
    deadline: str
    link: str
    source_url: str
    collected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class JobCrawler:
    def __init__(
        self,
        timeout: int = 15,
        debug: bool = False,
        render_js: bool = False,
        dump_dir: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.debug = debug
        self.render_js = render_js
        self.dump_dir = Path(dump_dir) if dump_dir else None
        self.session = requests.Session()
        self.js_sites = {"wanted", "linkareer", "jasoseol", "catch"}
        self._playwright = None
        self._browser = None
        self._dump_seq = 0
        self._log_path: Path | None = None
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = self.dump_dir / "crawl.log"
            self._log_path.write_text("", encoding="utf-8")
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def crawl(self, sites: Iterable[str], keywords: Iterable[str], limit_per_site: int = 20) -> list[JobPosting]:
        collected: list[JobPosting] = []

        for site in sites:
            if site not in SITE_CONFIGS:
                self._log(f"[skip] Unknown site id: {site}")
                continue
            for keyword in keywords:
                site_keyword_items = self._crawl_site_keyword(site, keyword, limit_per_site)
                collected.extend(site_keyword_items)
                self._log(f"[result] site={site} keyword={keyword} count={len(site_keyword_items)}")

        return self._deduplicate(collected)

    def _crawl_site_keyword(self, site: str, keyword: str, limit_per_site: int) -> list[JobPosting]:
        cfg = SITE_CONFIGS[site]
        if site == "jasoseol":
            api_results = self._crawl_jasoseol_api(keyword=keyword, limit_per_site=limit_per_site, cfg=cfg)
            if api_results:
                return api_results
        if site == "wanted":
            api_results = self._crawl_wanted_api(keyword=keyword, limit_per_site=limit_per_site, cfg=cfg)
            if api_results:
                return api_results
        if site == "catch":
            api_results = self._crawl_catch_api(keyword=keyword, limit_per_site=limit_per_site, cfg=cfg)
            if api_results:
                return api_results

        encoded_keyword = quote_plus(keyword)
        query_urls = cfg.get("search_urls", [cfg.get("search_url", "")])
        query_urls = [u.format(query=encoded_keyword) for u in query_urls if u]

        collected: list[JobPosting] = []
        for search_url in query_urls:
            fetched = self._fetch_html(site=site, keyword=keyword, search_url=search_url)
            if not fetched:
                continue
            html, final_url = fetched

            soup = BeautifulSoup(html, "lxml")
            now = datetime.now(timezone.utc).isoformat()

            if site == "jasoseol":
                postings = self._extract_jasoseol_results(
                    soup=soup,
                    site=site,
                    keyword=keyword,
                    base_url=cfg["base_url"],
                    source_url=final_url,
                    collected_at=now,
                )
                self._log(f"[parse] site={site} keyword={keyword} jasoseol_extract_count={len(postings)}")
                collected.extend(postings)
                if len(collected) >= limit_per_site:
                    break
                continue

            postings = self._extract_by_config(soup, site, keyword, cfg, final_url, now)
            self._log(f"[parse] site={site} keyword={keyword} css_extract_count={len(postings)}")

            if not postings and site == "jobkorea":
                postings = self._extract_jobkorea_cards(soup, site, keyword, final_url, now)
                self._log(f"[fallback] site={site} keyword={keyword} jobkorea_extract_count={len(postings)}")
            if not postings:
                postings = self._extract_from_anchors(soup, site, keyword, cfg["base_url"], final_url, now)
                self._log(f"[fallback] site={site} keyword={keyword} anchor_extract_count={len(postings)}")
            if not postings:
                postings = self._extract_from_json_ld(soup, site, keyword, cfg["base_url"], final_url, now)
                self._log(f"[fallback] site={site} keyword={keyword} jsonld_extract_count={len(postings)}")
            if not postings:
                postings = self._extract_from_inline_json(soup, site, keyword, cfg["base_url"], final_url, now)
                self._log(f"[fallback] site={site} keyword={keyword} json_extract_count={len(postings)}")

            collected.extend(postings)
            if len(collected) >= limit_per_site:
                break

        if site == "wanted":
            collected = [self._enrich_wanted_posting(item) for item in collected]
        return collected[:limit_per_site]

    def _crawl_jasoseol_api(self, keyword: str, limit_per_site: int, cfg: dict) -> list[JobPosting]:
        api_url = cfg.get("api_url")
        if not api_url:
            return []

        after_end_time = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        params = {
            "field_query": keyword,
            "all": "true",
            "after_end_time": after_end_time,
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://jasoseol.com/",
        }
        try:
            response = self.session.get(api_url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            self._log(
                f"[error] site=jasoseol keyword={keyword} jasoseol-api failed url={api_url} "
                f"reason={type(exc).__name__}: {exc}"
            )
            return []
        except json.JSONDecodeError as exc:
            self._log(
                f"[error] site=jasoseol keyword={keyword} jasoseol-api json decode failed "
                f"reason={type(exc).__name__}: {exc}"
            )
            return []

        self._log(
            f"[fetch-api] site=jasoseol keyword={keyword} status={response.status_code} "
            f"bytes={len(response.text)} url={response.url}"
        )
        self._dump_response(
            site="jasoseol",
            keyword=keyword,
            url=response.url,
            content=response.text,
            ext="json",
            stage="api",
        )
        postings = self._extract_from_jasoseol_api_payload(
            payload=payload,
            keyword=keyword,
            source_url=response.url,
            base_url=cfg["base_url"],
        )
        self._log(f"[parse-api] site=jasoseol keyword={keyword} count={len(postings)}")
        return postings[:limit_per_site]

    def _extract_jasoseol_results(
        self,
        soup: BeautifulSoup,
        site: str,
        keyword: str,
        base_url: str,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_links: set[str] = set()
        selectors = [
            "#__next > div > div.responsive-layout > main > div.px-4 > div > main > div > a[href]",
            "div.responsive-layout main div.px-4 div main div > a[href]",
            "a[href^='/recruit/']",
        ]
        anchors: list[Tag] = []
        for selector in selectors:
            anchors = [a for a in soup.select(selector) if isinstance(a, Tag)]
            if anchors:
                break

        for anchor in anchors:
            href_raw = anchor.get("href", "")
            href = self._normalize_url(urljoin(base_url, href_raw))
            if "/recruit/" not in href:
                continue
            if href in seen_links:
                continue
            seen_links.add(href)

            text = self._clean_text(anchor.get_text(" ", strip=True))
            if len(text) < 4:
                continue
            if not self._keyword_match(site=site, source_url=source_url, keyword=keyword, text_blob=text.lower()):
                continue

            title, company, location, deadline = self._extract_anchor_metadata(site=site, anchor=anchor, raw_text=text)
            postings.append(
                JobPosting(
                    site=site,
                    keyword=keyword,
                    title=title or text,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=href,
                    source_url=source_url,
                    collected_at=collected_at,
                )
            )
        return postings

    def _crawl_wanted_api(self, keyword: str, limit_per_site: int, cfg: dict) -> list[JobPosting]:
        api_url = cfg.get("api_url")
        if not api_url:
            return []

        ts = str(int(time.time() * 1000))
        params = {
            "query": keyword,
            "country": "kr",
            "years": "-1",
            "locations": "all",
            "sort": "job.recommend_order",
            "limit": str(max(20, limit_per_site)),
            "offset": "0",
            ts: "",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.wanted.co.kr/",
        }

        try:
            response = self.session.get(api_url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            self._log(
                f"[error] site=wanted keyword={keyword} wanted-api failed url={api_url} "
                f"reason={type(exc).__name__}: {exc}"
            )
            return []
        except json.JSONDecodeError as exc:
            self._log(
                f"[error] site=wanted keyword={keyword} wanted-api json decode failed "
                f"reason={type(exc).__name__}: {exc}"
            )
            return []

        self._log(
            f"[fetch-api] site=wanted keyword={keyword} status={response.status_code} "
            f"bytes={len(response.text)} url={response.url}"
        )
        self._dump_response(
            site="wanted",
            keyword=keyword,
            url=response.url,
            content=response.text,
            ext="json",
            stage="api",
        )
        postings = self._extract_from_wanted_api_payload(
            payload=payload,
            keyword=keyword,
            source_url=response.url,
            base_url=cfg["base_url"],
        )
        self._log(f"[parse-api] site=wanted keyword={keyword} count={len(postings)}")
        return postings[:limit_per_site]

    def _crawl_catch_api(self, keyword: str, limit_per_site: int, cfg: dict) -> list[JobPosting]:
        api_url = cfg.get("api_url")
        if not api_url:
            return []

        params = {
            "Keyword": keyword,
            "JobCode": "",
            "Sido": "",
            "Career": "",
            "JCode": "",
            "Size": "",
            "EduLevel": "",
            "WorkPosition": "",
            "CompID": "",
            "GroupCode": "",
            "Sort": "0",
            "curpage": "1",
            "pageSize": str(max(30, limit_per_site)),
            "onRecruitYN": "Y",
            "ExceptIDList": "",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.catch.co.kr/NCS/RecruitSearch",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            response = self.session.get(api_url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            self._log(
                f"[error] site=catch keyword={keyword} catch-api failed url={api_url} "
                f"reason={type(exc).__name__}: {exc}"
            )
            return []
        except json.JSONDecodeError as exc:
            self._log(
                f"[error] site=catch keyword={keyword} catch-api json decode failed "
                f"reason={type(exc).__name__}: {exc}"
            )
            return []

        self._log(
            f"[fetch-api] site=catch keyword={keyword} status={response.status_code} "
            f"bytes={len(response.text)} url={response.url}"
        )
        self._dump_response(
            site="catch",
            keyword=keyword,
            url=response.url,
            content=response.text,
            ext="json",
            stage="api",
        )
        postings = self._extract_from_catch_api_payload(
            payload=payload,
            keyword=keyword,
            source_url=response.url,
            base_url=cfg["base_url"],
        )
        self._log(f"[parse-api] site=catch keyword={keyword} count={len(postings)}")
        return postings[:limit_per_site]

    def _fetch_html(self, site: str, keyword: str, search_url: str) -> tuple[str, str] | None:
        if self.render_js and site in self.js_sites:
            rendered = self._fetch_html_with_playwright(site, keyword, search_url)
            if rendered:
                return rendered

        try:
            response = self.session.get(search_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            self._log(
                f"[error] site={site} keyword={keyword} request failed url={search_url} "
                f"reason={type(exc).__name__}: {exc}"
            )
            return None

        self._log(
            f"[fetch] site={site} keyword={keyword} status={response.status_code} "
            f"bytes={len(response.text)} url={response.url}"
        )
        self._dump_response(
            site=site,
            keyword=keyword,
            url=response.url,
            content=response.text,
            ext="html",
            stage="http",
        )
        return response.text, response.url

    def _fetch_html_with_playwright(self, site: str, keyword: str, search_url: str) -> tuple[str, str] | None:
        try:
            context, page = self._new_page()
        except Exception as exc:
            self._log(
                f"[error] site={site} keyword={keyword} playwright init failed "
                f"reason={type(exc).__name__}: {exc}"
            )
            return None

        try:
            response = page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            page.wait_for_timeout(1200)
            html = page.content()
            status = response.status if response else "n/a"
            final_url = page.url
            if isinstance(status, int) and status >= 400:
                self._log(
                    f"[error] site={site} keyword={keyword} playwright status={status} url={final_url} "
                    "falling back to requests"
                )
                return None
            self._log(
                f"[fetch-js] site={site} keyword={keyword} status={status} "
                f"bytes={len(html)} url={final_url}"
            )
            self._dump_response(
                site=site,
                keyword=keyword,
                url=final_url,
                content=html,
                ext="html",
                stage="js",
            )
            return html, final_url
        except Exception as exc:
            self._log(
                f"[error] site={site} keyword={keyword} playwright request failed url={search_url} "
                f"reason={type(exc).__name__}: {exc}"
            )
            return None
        finally:
            page.close()
            context.close()

    def _new_page(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        context = self._browser.new_context(locale="ko-KR")
        return context, context.new_page()

    def _extract_by_config(
        self,
        soup: BeautifulSoup,
        site: str,
        keyword: str,
        cfg: dict,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []

        blocks: list[Tag] = []
        for selector in cfg.get("containers", []):
            blocks.extend(soup.select(selector))
        if not blocks:
            return postings

        for block in blocks:
            title_node = self._first_match(block, cfg.get("title", []))
            link_node = title_node if title_node and title_node.name == "a" else block.find("a")

            title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")
            if not title:
                continue

            company = self._clean_text(self._node_text(block, cfg.get("company", [])))
            location = self._clean_text(self._node_text(block, cfg.get("location", [])))
            deadline = self._clean_text(self._node_text(block, cfg.get("deadline", [])))

            link = ""
            if link_node and link_node.get("href"):
                link = self._normalize_url(urljoin(cfg["base_url"], link_node["href"]))

            text_blob = " ".join([title, company, location]).lower()
            if not self._keyword_match(site=site, source_url=source_url, keyword=keyword, text_blob=text_blob):
                continue

            postings.append(
                JobPosting(
                    site=site,
                    keyword=keyword,
                    title=title,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=link,
                    source_url=source_url,
                    collected_at=collected_at,
                )
            )

        return postings

    def _extract_jobkorea_cards(
        self,
        soup: BeautifulSoup,
        site: str,
        keyword: str,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_links: set[str] = set()

        for card in soup.select("div.dlua7o2"):
            job_links = card.select("a[href*='/Recruit/GI_Read/']")
            if not job_links:
                continue

            text_links = [self._clean_text(link.get_text(" ", strip=True)) for link in job_links]
            text_links = [txt for txt in text_links if txt]
            if not text_links:
                continue

            title = text_links[0]
            company = text_links[1] if len(text_links) > 1 else ""
            link = self._normalize_url(urljoin(SITE_CONFIGS["jobkorea"]["base_url"], job_links[0].get("href", "")))
            if not link:
                continue
            if link in seen_links:
                continue
            seen_links.add(link)

            blob = self._clean_text(card.get_text(" ", strip=True))
            text_blob = " ".join([title, company, blob]).lower()
            if not self._keyword_match(site=site, source_url=source_url, keyword=keyword, text_blob=text_blob):
                continue

            location = ""
            loc_match = re.search(
                r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)"
                r"(?:\s+[가-힣0-9·]+(?:시|군|구))?(?:\s+외\s+\d+)?",
                blob,
            )
            if loc_match:
                location = self._clean_text(loc_match.group(0))

            deadline = ""
            end_match = re.search(r"(\d{2}/\d{2}\([^)]+\)\s*마감)", blob)
            if end_match:
                deadline = self._clean_text(end_match.group(1))
            else:
                if "오늘마감" in blob or "오늘 마감" in blob:
                    deadline = "오늘마감"
                for marker in ("채용시 마감", "상시채용"):
                    if marker in blob:
                        deadline = marker
                        break

            postings.append(
                JobPosting(
                    site=site,
                    keyword=keyword,
                    title=title,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=link,
                    source_url=source_url,
                    collected_at=collected_at,
                )
            )

        return postings

    def _extract_from_anchors(
        self,
        soup: BeautifulSoup,
        site: str,
        keyword: str,
        base_url: str,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_links: set[str] = set()

        for anchor in soup.select("a[href]"):
            text = self._clean_text(anchor.get_text(" ", strip=True))
            if len(text) < 4:
                continue

            href = self._normalize_url(urljoin(base_url, anchor["href"]))
            if site == "linkareer" and "/activity/" not in href:
                continue
            if site == "jasoseol" and "/recruit/" not in href:
                continue
            if not self._keyword_match(site=site, source_url=source_url, keyword=keyword, text_blob=text.lower()):
                continue
            if href in seen_links:
                continue
            seen_links.add(href)

            title, company, location, deadline = self._extract_anchor_metadata(site=site, anchor=anchor, raw_text=text)

            postings.append(
                JobPosting(
                    site=site,
                    keyword=keyword,
                    title=title or text,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=href,
                    source_url=source_url,
                    collected_at=collected_at,
                )
            )

        return postings

    def _extract_from_json_ld(
        self,
        soup: BeautifulSoup,
        site: str,
        keyword: str,
        base_url: str,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for script in soup.select("script[type='application/ld+json']"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            postings.extend(self._collect_jobs_from_json(payload, site, keyword, base_url, source_url, collected_at))
        return postings

    def _extract_from_inline_json(
        self,
        soup: BeautifulSoup,
        site: str,
        keyword: str,
        base_url: str,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for script in soup.select("script"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            raw = raw.strip()
            if len(raw) < 50:
                continue

            json_candidates: list[str] = []
            if raw.startswith("{") or raw.startswith("["):
                json_candidates.append(raw)
            else:
                # Common SPA assignments: window.__NEXT_DATA__ = {...};
                match = re.search(r"=\s*(\{.*\}|\[.*\])\s*;?\s*$", raw, flags=re.DOTALL)
                if match:
                    json_candidates.append(match.group(1))

            for candidate in json_candidates:
                try:
                    payload = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                postings.extend(
                    self._collect_jobs_from_json(payload, site, keyword, base_url, source_url, collected_at)
                )
        return postings

    def _collect_jobs_from_json(
        self,
        payload: object,
        site: str,
        keyword: str,
        base_url: str,
        source_url: str,
        collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for node in self._walk_json(payload):
            if not isinstance(node, dict):
                continue

            title = self._pick_text(node, ["title", "name", "position", "jobTitle", "postingTitle"])
            company = self._pick_text(node, ["companyName", "company", "corpName", "organizationName", "organization"])
            location = self._pick_text(node, ["location", "region", "address", "workplace", "workLocation"])
            deadline = self._pick_text(node, ["deadline", "closeDate", "endDate", "dueDate", "expiredAt"])
            link = self._pick_text(node, ["url", "link", "href", "jobUrl", "postingUrl"])

            if not title:
                continue
            if not link:
                continue

            normalized_link = self._normalize_url(urljoin(base_url, link))
            if not self._looks_like_job_link(normalized_link):
                continue

            text_blob = " ".join([title, company, location]).lower()
            if not self._keyword_match(site=site, source_url=source_url, keyword=keyword, text_blob=text_blob):
                continue

            postings.append(
                JobPosting(
                    site=site,
                    keyword=keyword,
                    title=title,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=normalized_link,
                    source_url=source_url,
                    collected_at=collected_at,
                )
            )

        return postings

    def _extract_from_catch_api_payload(
        self,
        payload: object,
        keyword: str,
        source_url: str,
        base_url: str,
    ) -> list[JobPosting]:
        now = datetime.now(timezone.utc).isoformat()
        postings: list[JobPosting] = []
        for node in self._walk_json(payload):
            if not isinstance(node, dict):
                continue

            title = self._pick_text(node, ["recruitTitle", "title", "jobTitle", "name"])
            company = self._pick_text(node, ["compName", "companyName", "company", "corpName"])
            location = self._pick_text(node, ["WorkArea", "sidoName", "location", "region", "workPlace"])
            apply_end_code = self._pick_text(node, ["ApplyEndCode"])
            apply_end_datetime = self._pick_text(node, ["ApplyEndDatetime"])
            dday = self._pick_text(node, ["Dday"])
            deadline = ""
            if apply_end_code:
                deadline = apply_end_code
            elif apply_end_datetime:
                deadline = apply_end_datetime[:10]
            elif dday:
                deadline = f"D-{dday}"

            if not title:
                continue

            recruit_id = self._pick_text(node, ["recruitID", "recruitId", "id", "idx"])
            link = ""
            if recruit_id:
                link = f"{base_url}/NCS/RecruitInfoDetails/{recruit_id}"
            if not link:
                link = source_url

            postings.append(
                JobPosting(
                    site="catch",
                    keyword=keyword,
                    title=title,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=link,
                    source_url=source_url,
                    collected_at=now,
                )
            )

        return postings

    def _extract_from_wanted_api_payload(
        self,
        payload: object,
        keyword: str,
        source_url: str,
        base_url: str,
    ) -> list[JobPosting]:
        now = datetime.now(timezone.utc).isoformat()
        postings: list[JobPosting] = []
        for node in self._walk_json(payload):
            if not isinstance(node, dict):
                continue

            title = self._pick_text(node, ["title", "position", "positionTitle", "jobTitle", "name"])
            annual_from_text = self._pick_text(node, ["annual_from", "annualFrom"])
            annual_to_text = self._pick_text(node, ["annual_to", "annualTo"])
            employment_type = self._pick_text(node, ["employment_type", "employmentType"])
            experience_hint = self._wanted_experience_hint(
                annual_from_text=annual_from_text,
                annual_to_text=annual_to_text,
                employment_type=employment_type,
            )
            if title and experience_hint and experience_hint not in title:
                title = f"{title} [{experience_hint}]"
            company = self._pick_text(node, ["companyName", "company", "company_name", "name"])
            location = self._pick_text(node, ["location", "address", "region", "country"])
            deadline = self._pick_text(node, ["dueTime", "deadline", "closeDate", "endDate"])

            if not title:
                continue
            text_blob = " ".join([title, company, location]).lower()
            if keyword.lower() not in text_blob:
                continue

            link = self._pick_text(node, ["url", "link", "href", "positionUrl", "jobUrl"])
            if not link:
                position_id = self._pick_text(node, ["id", "positionId", "positionID"])
                if position_id:
                    link = f"{base_url}/wd/{position_id}"
            if not link:
                link = source_url

            postings.append(
                JobPosting(
                    site="wanted",
                    keyword=keyword,
                    title=title,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=urljoin(base_url, link),
                    source_url=source_url,
                    collected_at=now,
                )
            )

        return postings

    def _wanted_experience_hint(self, annual_from_text: str, annual_to_text: str, employment_type: str) -> str:
        from_year = self._parse_int(annual_from_text)
        to_year = self._parse_int(annual_to_text)

        hint_parts: list[str] = []

        if from_year is not None and to_year is not None:
            if from_year <= 0 and to_year <= 0:
                hint_parts.append("신입 가능")
            elif from_year <= 0:
                hint_parts.append(f"신입 가능 (0~{to_year}년)")
            elif to_year < from_year or to_year >= 100:
                hint_parts.append(f"경력 {from_year}년+")
            elif from_year == to_year:
                hint_parts.append(f"경력 {from_year}년")
            else:
                hint_parts.append(f"경력 {from_year}~{to_year}년")
        elif from_year is not None:
            if from_year <= 0:
                hint_parts.append("신입 가능")
            else:
                hint_parts.append(f"경력 {from_year}년+")
        elif to_year is not None:
            if to_year <= 0:
                hint_parts.append("신입 가능")
            else:
                hint_parts.append(f"경력 ~{to_year}년")

        emp_token = employment_type.strip().lower().replace("-", "_").replace(" ", "_")
        emp_map = {
            "regular": "정규직",
            "contract": "계약직",
            "temporary": "계약직",
            "intern": "인턴",
            "freelancer": "프리랜서",
            "part_time": "파트타임",
            "full_time": "정규직",
        }
        emp_label = emp_map.get(emp_token, "")
        if not emp_label and employment_type and not re.fullmatch(r"[a-zA-Z_]+", employment_type.strip()):
            emp_label = self._clean_text(employment_type)
        if emp_label and emp_label not in hint_parts:
            hint_parts.append(emp_label)

        return " / ".join(hint_parts)

    def _extract_from_jasoseol_api_payload(
        self,
        payload: object,
        keyword: str,
        source_url: str,
        base_url: str,
    ) -> list[JobPosting]:
        now = datetime.now(timezone.utc).isoformat()
        postings: list[JobPosting] = []
        if not isinstance(payload, list):
            return postings

        for node in payload:
            if not isinstance(node, dict):
                continue

            title = self._pick_text(node, ["title"])
            company = self._pick_text(node, ["name"])
            deadline = self._pick_text(node, ["end_time", "endTime"])
            location = self._pick_text(node, ["location", "region", "work_area", "workArea"])
            recruit_id = self._pick_text(node, ["id"])
            link = f"{base_url}/recruit/{recruit_id}" if recruit_id else source_url

            if not title:
                continue
            text_blob = " ".join([title, company]).lower()
            if keyword.lower() not in text_blob and "hr" not in text_blob:
                continue

            postings.append(
                JobPosting(
                    site="jasoseol",
                    keyword=keyword,
                    title=title,
                    company=company,
                    location=location,
                    deadline=deadline,
                    link=link,
                    source_url=source_url,
                    collected_at=now,
                )
            )
        return postings

    def _keyword_match(self, site: str, source_url: str, keyword: str, text_blob: str) -> bool:
        if site == "jasoseol" and "dutyGroupIds=115%2C117" in source_url:
            return True
        return keyword.lower() in text_blob

    def _extract_anchor_metadata(self, site: str, anchor: Tag, raw_text: str) -> tuple[str, str, str, str]:
        title = raw_text
        company = ""
        location = ""
        deadline = ""

        if site == "linkareer":
            title_node = anchor.select_one("p.title")
            if isinstance(title_node, Tag):
                title = self._clean_text(title_node.get_text(" ", strip=True))
            card = anchor
            for _ in range(5):
                parent = card.parent if isinstance(card.parent, Tag) else None
                if not parent:
                    break
                card = parent
                if card.select_one("p.company-name"):
                    break
            company_node = card.select_one("p.company-name")
            if isinstance(company_node, Tag):
                company = self._clean_text(company_node.get_text(" ", strip=True))
            for node in card.select("p.short-info-typo"):
                value = self._clean_text(node.get_text(" ", strip=True))
                if "마감" in value or value.startswith("~") or re.search(r"\d{1,2}\.\d{1,2}", value):
                    deadline = value
                    break
            location = self._extract_location_text(self._clean_text(card.get_text(" ", strip=True)))

        elif site == "jasoseol":
            title_node = anchor.select_one("h4")
            if isinstance(title_node, Tag):
                title = self._clean_text(title_node.get_text(" ", strip=True))
            company_node = anchor.select_one("h5")
            if isinstance(company_node, Tag):
                company = self._clean_text(company_node.get_text(" ", strip=True))
            blob = self._clean_text(anchor.get_text(" ", strip=True))
            range_match = re.search(
                r"(?:\d{2,4}/)?\d{2}/\d{2}\s*-\s*(?:\d{2,4}/)?\d{2}/\d{2}",
                blob,
            )
            if range_match:
                deadline = range_match.group(0)
            elif "수시" in blob:
                deadline = "수시"
            elif "오늘 마감" in blob or "오늘마감" in blob:
                deadline = "오늘마감"
            location = self._extract_location_text(blob)

        elif site == "jobkorea":
            location = self._extract_location_text(raw_text)
            if "오늘 마감" in raw_text or "오늘마감" in raw_text:
                deadline = "오늘마감"

        return title, company, location, deadline

    def _extract_location_text(self, text: str) -> str:
        match = re.search(
            r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주|전국)"
            r"(?:\s+[가-힣0-9·]+(?:시|군|구))?(?:\s+외\s+\d+)?",
            text,
        )
        return self._clean_text(match.group(0)) if match else ""

    def _enrich_wanted_posting(self, posting: JobPosting) -> JobPosting:
        if posting.site != "wanted":
            return posting
        if posting.location and posting.deadline:
            return posting
        try:
            response = self.session.get(posting.link, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException:
            return posting

        self._dump_response(
            site="wanted",
            keyword=posting.keyword,
            url=response.url,
            content=response.text,
            ext="html",
            stage="detail",
        )
        soup = BeautifulSoup(response.text, "lxml")
        location, deadline = self._extract_wanted_detail_fields(soup)
        if location:
            posting.location = location
        if deadline:
            posting.deadline = deadline
        return posting

    def _extract_wanted_detail_fields(self, soup: BeautifulSoup) -> tuple[str, str]:
        location = ""
        deadline = ""
        for script in soup.select("script[type='application/ld+json']"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for node in self._walk_json(payload):
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("@type", "")).lower()
                if node_type != "jobposting":
                    continue
                if not location:
                    location = self._pick_text(node, ["jobLocation", "address", "addressLocality", "addressRegion"])
                if not deadline:
                    deadline = self._pick_text(node, ["validThrough", "datePosted"])
        if not location:
            txt = self._clean_text(soup.get_text(" ", strip=True))
            location = self._extract_location_text(txt)
        return location, deadline

    def _pick_text(self, node: dict, keys: list[str]) -> str:
        for key in keys:
            value = self._dict_get_case_insensitive(node, key)
            if value is None:
                continue
            if isinstance(value, str):
                cleaned = self._clean_text(value)
                if cleaned:
                    return cleaned
            if isinstance(value, (int, float)):
                return str(value)
            if isinstance(value, dict):
                nested = self._pick_text(value, ["name", "title", "value", "label"])
                if nested:
                    return nested
        return ""

    @staticmethod
    def _parse_int(value: str) -> int | None:
        token = (value or "").strip()
        if not token:
            return None
        match = re.search(r"-?\d+", token)
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    @staticmethod
    def _dict_get_case_insensitive(node: dict, key: str):
        if key in node:
            return node[key]
        lower_key = key.lower()
        for k, v in node.items():
            if isinstance(k, str) and k.lower() == lower_key:
                return v
        return None

    def _walk_json(self, payload: object):
        if isinstance(payload, dict):
            yield payload
            for value in payload.values():
                yield from self._walk_json(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._walk_json(item)

    @staticmethod
    def _looks_like_job_link(link: str) -> bool:
        lowered = link.lower()
        return any(token in lowered for token in ["job", "recruit", "position", "posting", "hire"])

    @staticmethod
    def _first_match(root: Tag, selectors: list[str]) -> Tag | None:
        for selector in selectors:
            node = root.select_one(selector)
            if isinstance(node, Tag):
                return node
        return None

    def _node_text(self, root: Tag, selectors: list[str]) -> str:
        node = self._first_match(root, selectors)
        return node.get_text(" ", strip=True) if node else ""

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        query = [(k, v) for (k, v) in parse_qsl(parsed.query, keep_blank_values=True) if not k.startswith("utm_")]
        cleaned = parsed._replace(query=urlencode(query), fragment="")
        return urlunparse(cleaned)

    @staticmethod
    def _deduplicate(items: list[JobPosting]) -> list[JobPosting]:
        deduped: list[JobPosting] = []
        seen: set[tuple[str, str, str, str]] = set()

        for item in items:
            key = (
                item.site,
                item.title.strip().lower(),
                item.company.strip().lower(),
                item.link.strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        return deduped

    def _log(self, message: str) -> None:
        if self._log_path is not None:
            now = datetime.now(timezone.utc).isoformat()
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(f"{now} {message}\n")
        if self.debug:
            print(message, file=sys.stderr)

    def _dump_response(self, site: str, keyword: str, url: str, content: str, ext: str, stage: str) -> None:
        if not self.dump_dir:
            return
        safe_site = re.sub(r"[^a-zA-Z0-9_-]+", "_", site)
        safe_keyword = re.sub(r"[^a-zA-Z0-9_-]+", "_", keyword)
        safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "_", stage)
        self._dump_seq += 1
        file_name = f"{self._dump_seq:03d}_{safe_site}_{safe_keyword}_{safe_stage}.{ext}"
        path = self.dump_dir / file_name
        path.write_text(content, encoding="utf-8")
        meta_name = f"{self._dump_seq:03d}_{safe_site}_{safe_keyword}_{safe_stage}.url.txt"
        (self.dump_dir / meta_name).write_text(url, encoding="utf-8")
        self._log(f"[dump] saved={path} url={url}")

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
