from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

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
    description: str = ""
    experience: str = ""
    employment_type: str = ""
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class CrawlError(RuntimeError):
    """No requested site/keyword search could be read successfully."""


@dataclass
class CrawlOutcome:
    site: str
    keyword: str
    status: str = "failed"
    count: int = 0
    pages: int = 0
    page_requests: int = 0
    requests: int = 0
    detail_requests: int = 0
    stop_reason: str = "exhausted"
    errors: list[str] = field(default_factory=list)


class JobCrawler:
    def __init__(
        self,
        timeout: int = 15,
        debug: bool = False,
        render_js: bool = False,
        dump_dir: str | None = None,
        max_pages: int = 3,
        max_requests: int = 20,
        max_detail_requests: int = 10,
        retries: int = 2,
        request_interval: float = 0.25,
    ) -> None:
        if timeout <= 0 or max_pages < 1 or max_requests < 1 or max_detail_requests < 0 or retries < 0:
            raise ValueError("Timeout/page/request budgets must be positive; detail/retry budgets may be zero")
        if not 0 <= request_interval <= 10:
            raise ValueError("request_interval must be between 0 and 10 seconds")
        self.timeout = timeout
        self.debug = debug
        self.render_js = render_js
        self.dump_dir = Path(dump_dir) if dump_dir else None
        self.session = requests.Session()
        retry = Retry(total=retries, backoff_factor=0.5, allowed_methods={"GET"},
                      status_forcelist=(429, 500, 502, 503, 504), respect_retry_after_header=True)
        for scheme in ("https://", "http://"):
            self.session.mount(scheme, HTTPAdapter(max_retries=retry))
        self.max_pages = max_pages
        self.max_requests = max_requests
        self.max_detail_requests = max_detail_requests
        self.request_interval = request_interval
        self.outcomes: list[CrawlOutcome] = []
        self._active: CrawlOutcome | None = None
        self._request_times: dict[str, float] = {}
        self._detail_cache: dict[str, dict[str, str]] = {}
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
        sites = list(dict.fromkeys(sites))
        keywords = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))
        if limit_per_site < 1 or not sites or not keywords:
            raise ValueError("A positive candidate limit, at least one site, and a keyword are required")
        unknown = set(sites) - SITE_CONFIGS.keys()
        if unknown:
            raise ValueError(f"Unknown site ids: {', '.join(sorted(unknown))}")
        self.outcomes = []
        self._detail_cache.clear()
        collected: list[JobPosting] = []
        for site in sites:
            for keyword in keywords:
                outcome = CrawlOutcome(site, keyword)
                self.outcomes.append(outcome)
                self._active = outcome
                try:
                    items = self._crawl_site_keyword(site, keyword, limit_per_site)
                    if not outcome.pages and not items and not outcome.errors:
                        self._error(f"site={site} keyword={keyword} no search page could be validated")
                finally:
                    self._active = None
                outcome.count = len(items)
                if outcome.errors:
                    outcome.status = "partial" if outcome.pages else "failed"
                else:
                    outcome.status = "success" if items else "empty"
                collected.extend(items)
                self._log(f"[result] site={site} keyword={keyword} status={outcome.status} "
                          f"count={len(items)} pages={outcome.pages} requests={outcome.requests} "
                          f"stop={outcome.stop_reason}")
        if all(o.status == "failed" for o in self.outcomes):
            raise CrawlError("All searches failed; see crawl diagnostics or --crawl-report")
        return self._deduplicate(collected)

    def _error(self, message: str) -> None:
        if self._active is not None:
            self._active.errors.append(message)
        self._log(f"[error] {message}")

    def _reserve_request(self, url: str, detail: bool = False) -> bool:
        if self._active is not None:
            if self._active.requests >= self.max_requests:
                self._active.stop_reason = "request_budget"
                return False
            if not detail and self._active.page_requests >= self.max_pages:
                self._active.stop_reason = "page_budget"
                return False
            if detail and self._active.detail_requests >= self.max_detail_requests:
                return False
            self._active.requests += 1
            self._active.detail_requests += int(detail)
            self._active.page_requests += int(not detail)
        host = urlparse(url).netloc
        delay = self.request_interval - (time.monotonic() - self._request_times.get(host, 0))
        if delay > 0:
            time.sleep(delay)
        self._request_times[host] = time.monotonic()
        return True

    def _get(self, site: str, keyword: str, url: str, *, detail: bool = False, **kwargs) -> requests.Response | None:
        if not self._reserve_request(url, detail):
            return None
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            self._error(f"site={site} keyword={keyword} request failed url={url} "
                        f"reason={type(exc).__name__}: {exc}")
            return None

    def _page_succeeded(self) -> None:
        if self._active is not None:
            self._active.pages += 1

    def _crawl_site_keyword(self, site: str, keyword: str, limit_per_site: int) -> list[JobPosting]:
        cfg = SITE_CONFIGS[site]
        api_crawlers = {"jasoseol": self._crawl_jasoseol_api, "wanted": self._crawl_wanted_api,
                        "catch": self._crawl_catch_api}
        collected: list[JobPosting] = []
        if site in api_crawlers:
            collected = api_crawlers[site](keyword, limit_per_site, cfg)
            # A validated empty API response is a real empty result, not a transport failure.
            if collected or (self._active is not None and self._active.pages):
                return self._finish_candidates(site, collected, limit_per_site)

        urls = [u.format(query=quote_plus(keyword))
                for u in cfg.get("search_urls", [cfg.get("search_url", "")]) if u]
        visited: set[str] = set()
        fingerprints: set[tuple[str, ...]] = set()
        for _ in range(self.max_pages):
            if not urls:
                break
            search_url = urls.pop(0)
            if search_url in visited:
                break
            visited.add(search_url)
            fetched = self._fetch_html(site, keyword, search_url)
            if not fetched:
                continue
            html, final_url = fetched
            soup = BeautifulSoup(html, "lxml")
            postings = self._extract_html(soup, site, keyword, final_url)
            links = tuple(sorted({self._canonical_job_url(site, urljoin(cfg["base_url"], a["href"]))
                                  for a in soup.select("a[href]")
                                  if self._canonical_job_url(site, urljoin(cfg["base_url"], a["href"]))}))
            empty = bool(re.search(r"검색\s*결과[\s\S]{0,40}(?:없|0\s*건)|검색된[\s\S]{0,30}없|no results",
                                   soup.get_text(" ", strip=True), re.IGNORECASE))
            if not postings and not links and not empty:
                self._error(f"site={site} keyword={keyword} unrecognized search HTML url={final_url}")
                continue
            self._page_succeeded()
            fingerprint = links or tuple(sorted(p.link for p in postings))
            if fingerprint and fingerprint in fingerprints:
                if self._active:
                    self._active.stop_reason = "repeated_page"
                break
            fingerprints.add(fingerprint)
            collected = self._deduplicate(collected + postings)
            if len(collected) >= limit_per_site:
                if self._active:
                    self._active.stop_reason = "candidate_limit"
                break
            next_url = self._next_html_page(soup, site, final_url)
            if next_url:
                urls.insert(0, next_url)
            elif postings or links or empty:
                break
        else:
            if urls and self._active and self._active.stop_reason != "request_budget":
                self._active.stop_reason = "page_budget"
        return self._finish_candidates(site, collected, limit_per_site)

    def _finish_candidates(self, site: str, items: list[JobPosting], limit: int) -> list[JobPosting]:
        items = self._deduplicate(items)[:limit]
        if site == "wanted":
            items = [self._enrich_wanted_posting(item) for item in items]
        return items

    def _extract_html(self, soup: BeautifulSoup, site: str, keyword: str, source_url: str) -> list[JobPosting]:
        cfg = SITE_CONFIGS[site]
        now = datetime.now(timezone.utc).isoformat()
        args = (soup, site, keyword, cfg["base_url"], source_url, now)
        postings = self._extract_from_json_ld(*args)
        postings.extend(self._extract_from_inline_json(*args))
        if site == "jobkorea":
            postings.extend(self._extract_jobkorea_cards(soup, site, keyword, source_url, now))
        if site == "jasoseol":
            postings.extend(self._extract_jasoseol_results(*args))
        postings.extend(self._extract_by_config(soup, site, keyword, cfg, source_url, now))
        postings.extend(self._extract_from_anchors(*args))
        return self._deduplicate(postings)

    def _next_html_page(self, soup: BeautifulSoup, site: str, source_url: str) -> str | None:
        params = dict(parse_qsl(urlparse(source_url).query))
        page_key = {"jobkorea": "Page_No", "linkareer": "page", "saramin": "recruitPage"}.get(site)
        if not page_key:
            return None
        current = self._parse_int(params.get(page_key, "1")) or 1
        for anchor in soup.select("a[href]"):
            target = urljoin(source_url, anchor["href"])
            parsed = urlparse(target)
            target_params = dict(parse_qsl(parsed.query))
            if (parsed.scheme in {"http", "https"} and parsed.netloc == urlparse(source_url).netloc
                    and parsed.path.rstrip("/") == urlparse(source_url).path.rstrip("/")
                    and target_params.get(page_key) == str(current + 1)):
                return target
        # Saramin uses fragment links with a page attribute instead of page URLs.
        if site == "saramin" and soup.select_one(f'a.page_move[page="{current + 1}"], a[data-page="{current + 1}"]'):
            params[page_key] = str(current + 1)
            return urlunparse(urlparse(source_url)._replace(query=urlencode(params), fragment=""))
        return None

    def _api_pages(self, site: str, keyword: str, cfg: dict, params: dict,
                   limit: int, list_key: str | None,
                   extractor: Callable[[object, str, str, str], list[JobPosting]]) -> list[JobPosting]:
        collected: list[JobPosting] = []
        seen: set[tuple[str, ...]] = set()
        offset = 0
        for page in range(1, self.max_pages + 1):
            if site == "wanted":
                params["offset"] = str(offset)
            elif site == "catch":
                params["curpage"] = str(page)
            response = self._get(site, keyword, cfg["api_url"], params=dict(params), headers={
                "Accept": "application/json", "Referer": cfg["base_url"] + "/",
            })
            if response is None:
                break
            self._dump_response(site, keyword, response.url, response.text, "json", "api")
            try:
                payload = response.json()
            except ValueError as exc:
                self._error(f"site={site} keyword={keyword} invalid JSON: {exc}")
                break
            rows = (payload.get(list_key) if isinstance(payload, dict) else None) if list_key else payload
            if not isinstance(rows, list):
                self._error(f"site={site} keyword={keyword} unexpected API schema")
                break
            # Validate records independently of keyword filtering so schema drift is visible.
            unfiltered = extractor(payload, "", response.url, cfg["base_url"])
            if rows and not unfiltered:
                self._error(f"site={site} keyword={keyword} API records have no valid job identities")
                break
            self._page_succeeded()
            if not rows:
                break
            fingerprint = tuple(sorted({item.link for item in unfiltered}))
            if fingerprint in seen:
                if self._active:
                    self._active.stop_reason = "repeated_page"
                break
            seen.add(fingerprint)
            collected = self._deduplicate(collected + extractor(payload, keyword, response.url, cfg["base_url"]))
            if len(collected) >= limit:
                if self._active:
                    self._active.stop_reason = "candidate_limit"
                break
            offset += len(rows)
            if site == "jasoseol":
                break  # This endpoint returns all matching companies in one response.
            total_key = "total_count" if site == "wanted" else "intTotalRecordCount"
            total = self._parse_int(str(payload.get(total_key, "")))
            if total is not None and offset >= total:
                break
            if site == "wanted" and isinstance(payload.get("links"), dict):
                if "next" in payload["links"] and not payload["links"]["next"]:
                    break
            if page == self.max_pages and self._active:
                self._active.stop_reason = "page_budget"
        return collected[:limit]

    def _crawl_jasoseol_api(self, keyword: str, limit_per_site: int, cfg: dict) -> list[JobPosting]:
        return self._api_pages("jasoseol", keyword, cfg, {
            "field_query": keyword, "all": "true",
            "after_end_time": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        }, limit_per_site, None, self._extract_from_jasoseol_api_payload)

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
            href = self._canonical_job_url(site, urljoin(base_url, href_raw))
            if not href:
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
        return self._api_pages("wanted", keyword, cfg, {
            "query": keyword, "country": "kr", "years": "-1", "locations": "all",
            "sort": "job.recommend_order", "limit": "20",
        }, limit_per_site, "data", self._extract_from_wanted_api_payload)

    def _crawl_catch_api(self, keyword: str, limit_per_site: int, cfg: dict) -> list[JobPosting]:
        return self._api_pages("catch", keyword, cfg, {
            "Keyword": keyword, "JobCode": "", "Sido": "", "Career": "", "JCode": "",
            "Size": "", "EduLevel": "", "WorkPosition": "", "CompID": "", "GroupCode": "",
            "Sort": "0", "pageSize": "30", "onRecruitYN": "Y", "ExceptIDList": "",
        }, limit_per_site, "recruitData", self._extract_from_catch_api_payload)

    def _fetch_html(self, site: str, keyword: str, search_url: str) -> tuple[str, str] | None:
        if self.render_js and site in self.js_sites:
            rendered = self._fetch_html_with_playwright(site, keyword, search_url)
            if rendered:
                return rendered
        response = self._get(site, keyword, search_url)
        if response is None:
            return None
        self._log(f"[fetch] site={site} keyword={keyword} status={response.status_code} "
                  f"bytes={len(response.text)} url={response.url}")
        self._dump_response(site, keyword, response.url, response.text, "html", "http")
        return response.text, response.url

    def _fetch_html_with_playwright(self, site: str, keyword: str, search_url: str) -> tuple[str, str] | None:
        if not self._reserve_request(search_url):
            return None
        try:
            context, page = self._new_page()
        except Exception as exc:
            self._error(
                f"site={site} keyword={keyword} playwright init failed "
                f"reason={type(exc).__name__}: {exc}"
            )
            return None

        try:
            response = page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            status = response.status if response else "n/a"
            if isinstance(status, int) and status >= 400:
                self._error(f"site={site} keyword={keyword} playwright status={status} url={page.url}")
                return None
            # Wait for actual content (or an explicit empty state), not an arbitrary delay.
            page.wait_for_function("""() => document.querySelector(
                'a[href*="/activity/"], a[href*="/wd/"], a[href*="/recruit/"], a[href*="/RecruitInfoDetails/"]'
            ) || /검색\\s*결과[\\s\\S]{0,40}(없|0\\s*건)|no results/i.test(document.body.innerText)""",
                                   timeout=self.timeout * 1000)
            html = page.content()
            final_url = page.url
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
            self._error(
                f"site={site} keyword={keyword} playwright request failed url={search_url} "
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
        seen_blocks: set[int] = set()
        for selector in cfg.get("containers", []):
            for block in soup.select(selector):
                if id(block) not in seen_blocks:
                    blocks.append(block)
                    seen_blocks.add(id(block))
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
                link = self._canonical_job_url(site, urljoin(cfg["base_url"], link_node["href"]))
            if not link:
                continue
            job_links = {self._canonical_job_url(site, urljoin(cfg["base_url"], a["href"]))
                         for a in block.select("a[href]")}
            if len(job_links - {""}) > 1:
                continue  # This is a list wrapper, not an individual job card.

            description = self._clean_text(block.get_text(" ", strip=True))
            text_blob = " ".join([title, company, location, description]).lower()
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
                    description=description,
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

        for card in soup.select('[data-sentry-component="CardJob"], div.dlua7o2'):
            job_links = card.select("a[href*='/Recruit/GI_Read/']")
            if not job_links:
                continue

            text_links = [self._clean_text(link.get_text(" ", strip=True)) for link in job_links]
            text_links = [txt for txt in text_links if txt]
            if not text_links:
                continue

            title_node = card.select_one('[data-sentry-component="Title"]')
            title = self._clean_text(title_node.get_text(" ", strip=True)) if title_node else text_links[0]
            company = text_links[1] if len(text_links) > 1 else ""
            link = self._canonical_job_url(site, urljoin(SITE_CONFIGS["jobkorea"]["base_url"], job_links[0].get("href", "")))
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
                    description=blob,
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

            href = self._canonical_job_url(site, urljoin(base_url, anchor["href"]))
            if not href:
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
        self, payload: object, site: str, keyword: str, base_url: str,
        source_url: str, collected_at: str,
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for node in self._walk_json(payload):
            if not isinstance(node, dict):
                continue
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            typed_job = isinstance(types, list) and "JobPosting" in types
            if types and not typed_job:
                continue
            title = self._pick_text(node, ["title", "position", "jobTitle", "postingTitle"])
            if typed_job and not title:
                title = self._pick_text(node, ["name"])
            link = self._pick_text(node, ["url", "link", "href", "jobUrl", "postingUrl"])
            if site == "linkareer" and node.get("__typename") == "Activity":
                activity_id = self._pick_text(node, ["id"])
                if activity_id:
                    link = f"{base_url}/activity/{activity_id}"
            if typed_job and not link:
                link = source_url  # Detail pages often omit their own URL.
            link = self._canonical_job_url(site, urljoin(base_url, link)) if link else ""
            if not title or not link:
                continue
            company = self._pick_text(node, ["hiringOrganization", "companyName", "company", "corpName",
                                             "organizationName", "organization"])
            location = self._location_value(node.get("jobLocation")) or self._pick_text(
                node, ["location", "region", "address", "workplace", "workLocation"])
            deadline = self._pick_text(node, ["validThrough", "deadline", "closeDate", "endDate", "dueDate", "expiredAt"])
            if site == "linkareer" and not deadline and isinstance(node.get("recruitCloseAt"), (int, float)):
                try:
                    deadline = datetime.fromtimestamp(node["recruitCloseAt"] / 1000, timezone.utc).isoformat()
                except (ValueError, OverflowError, OSError):
                    pass
            description = self._plain_text(" ".join(self._pick_text(node, [key]) for key in
                ("description", "responsibilities", "qualifications", "skills", "occupationalCategory")))
            experience = self._pick_text(node, ["experienceRequirements", "experience"])
            employment_type = self._pick_text(node, ["employmentType", "employment_type"])
            blob = " ".join([title, company, location, description, experience, employment_type])
            if not self._keyword_match(site, source_url, keyword, blob):
                continue
            postings.append(JobPosting(site, keyword, title, company, location, deadline, link,
                                       source_url, collected_at, description, experience, employment_type))
        return postings

    def _location_value(self, value: object) -> str:
        if isinstance(value, str):
            return self._clean_text(value)
        if isinstance(value, list):
            return "; ".join(dict.fromkeys(filter(None, (self._location_value(v) for v in value))))
        if isinstance(value, dict):
            if "address" in value:
                return self._location_value(value["address"])
            parts = [self._pick_text(value, [key]) for key in
                     ("addressCountry", "addressRegion", "addressLocality", "streetAddress")]
            return " ".join(dict.fromkeys(filter(None, parts))) or self._pick_text(value, ["name"])
        return ""

    def _plain_text(self, value: str) -> str:
        return self._clean_text(BeautifulSoup(value, "lxml").get_text(" ", strip=True)) if "<" in value else self._clean_text(value)

    def _extract_from_catch_api_payload(
        self, payload: object, keyword: str, source_url: str, base_url: str,
    ) -> list[JobPosting]:
        rows = payload.get("recruitData", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        postings: list[JobPosting] = []
        now = datetime.now(timezone.utc).isoformat()
        for node in rows:
            if not isinstance(node, dict):
                continue
            title = self._pick_text(node, ["recruitTitle", "title", "jobTitle"])
            recruit_id = self._pick_text(node, ["recruitID", "id"])
            link = self._canonical_job_url("catch", f"{base_url}/NCS/RecruitInfoDetails/{recruit_id}")
            if not title or not link:
                continue
            deadline = self._pick_text(node, ["ApplyEndCode", "ApplyEndDatetime"])
            dday = self._pick_text(node, ["Dday"])
            if not deadline and dday and dday.isdigit():
                deadline = f"D-{dday}"
            experience = " / ".join(dict.fromkeys(filter(None, (
                self._pick_text(node, ["NewbieText"]), self._pick_text(node, ["ExperienceText"]),
                self._pick_text(node, ["ExperienceRange"]), self._pick_text(node, ["CareerGubunCode"]),
            ))))
            description = " / ".join(filter(None, (self._pick_text(node, ["Depth"]),
                self._pick_text(node, ["AssignedTaskNameListString"]), self._pick_text(node, ["RecruitCategory"]))))
            postings.append(JobPosting(
                "catch", keyword, title, self._pick_text(node, ["compName", "companyName"]),
                self._pick_text(node, ["WorkArea", "sidoName", "location"]), deadline, link, source_url, now,
                description, experience, self._pick_text(node, ["GubunCode", "employmentType"]),
            ))
        return postings

    def _extract_from_wanted_api_payload(
        self, payload: object, keyword: str, source_url: str, base_url: str,
    ) -> list[JobPosting]:
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        postings: list[JobPosting] = []
        now = datetime.now(timezone.utc).isoformat()
        for node in rows:
            if not isinstance(node, dict):
                continue
            title = self._pick_text(node, ["position", "title", "positionTitle", "jobTitle"])
            position_id = self._pick_text(node, ["id", "positionId"])
            raw_link = self._pick_text(node, ["url", "link", "positionUrl", "jobUrl"])
            link = self._canonical_job_url("wanted", urljoin(base_url, raw_link or f"/wd/{position_id}"))
            if not title or not link:
                continue
            employment_type = self._pick_text(node, ["employment_type", "employmentType"])
            experience = self._wanted_experience_hint(self._pick_text(node, ["annual_from", "annualFrom"]),
                self._pick_text(node, ["annual_to", "annualTo"]), employment_type)
            # Preserve the existing human-readable experience hint as well as structured fields.
            if experience and experience not in title:
                title = f"{title} [{experience}]"
            postings.append(JobPosting(
                "wanted", keyword, title, self._pick_text(node, ["companyName", "company", "company_name"]),
                self._location_value(node.get("address")) or self._pick_text(node, ["location", "region"]),
                self._pick_text(node, ["dueTime", "deadline", "closeDate", "endDate"]), link, source_url, now,
                self._plain_text(self._pick_text(node, ["description", "responsibilities"])), experience, employment_type,
            ))
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
        self, payload: object, keyword: str, source_url: str, base_url: str,
    ) -> list[JobPosting]:
        if not isinstance(payload, list):
            return []
        postings: list[JobPosting] = []
        now = datetime.now(timezone.utc).isoformat()
        for node in payload:
            if not isinstance(node, dict):
                continue
            title = self._pick_text(node, ["title"])
            company = self._pick_text(node, ["name"])
            recruit_id = self._pick_text(node, ["id"])
            link = self._canonical_job_url("jasoseol", f"{base_url}/recruit/{recruit_id}")
            roles = node.get("employments", [])
            roles = [role for role in roles if isinstance(role, dict)] if isinstance(roles, list) else []
            description = " / ".join(filter(None, [self._pick_text(node, ["description"])] +
                                    [self._pick_text(role, ["field", "title"]) for role in roles]))
            if not title or not link or not self._keyword_match("jasoseol", source_url, keyword,
                                                                " ".join([title, company, description])):
                continue
            experience = " / ".join(filter(None, [self._pick_text(role, ["experience", "career"]) for role in roles]))
            employment_type = " / ".join(filter(None, [self._pick_text(role, ["work_type", "employment_type"]) for role in roles]))
            postings.append(JobPosting(
                "jasoseol", keyword, title, company, self._pick_text(node, ["location", "region", "work_area"]),
                self._pick_text(node, ["end_time", "endTime"]), link, source_url, now,
                description, experience, employment_type,
            ))
        return postings

    def _keyword_match(self, site: str, source_url: str, keyword: str, text_blob: str) -> bool:
        return keyword.casefold() in text_blob.casefold()

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
        if posting.site != "wanted" or (posting.location and posting.deadline and posting.description):
            return posting
        if posting.link in self._detail_cache:
            fields = self._detail_cache[posting.link]
        else:
            response = self._get(posting.site, posting.keyword, posting.link, detail=True)
            if response is None:
                return posting
            if self._canonical_job_url(posting.site, response.url) != posting.link:
                self._error(f"site={posting.site} detail redirected away from job url={posting.link}")
                self._detail_cache[posting.link] = {}
                return posting
            self._dump_response(posting.site, posting.keyword, response.url, response.text, "html", "detail")
            soup = BeautifulSoup(response.text, "lxml")
            records = self._extract_from_json_ld(soup, "wanted", "", SITE_CONFIGS["wanted"]["base_url"],
                                                  posting.link, posting.collected_at)
            fields = {}
            for record in records:
                if record.link == posting.link:
                    fields = {key: getattr(record, key) for key in
                              ("company", "location", "deadline", "description", "experience", "employment_type")}
                    break
            self._detail_cache[posting.link] = fields
        for key, value in fields.items():
            if value and not getattr(posting, key):
                setattr(posting, key, value)
        return posting

    def _extract_wanted_detail_fields(self, soup: BeautifulSoup) -> tuple[str, str]:
        location = ""
        deadline = ""
        for script in soup.select("script[type='application/ld+json']"):
            try:
                payload = json.loads(script.string or script.get_text())
            except (ValueError, TypeError):
                continue
            for node in self._walk_json(payload):
                if not isinstance(node, dict):
                    continue
                types = node.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if not isinstance(types, list) or "JobPosting" not in types:
                    continue
                location = location or self._location_value(node.get("jobLocation"))
                deadline = deadline or self._pick_text(node, ["validThrough"])
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
            if isinstance(value, list):
                nested = [self._pick_text({"value": item}, ["value"]) for item in value]
                if any(nested):
                    return " / ".join(filter(None, nested))
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
        for site in SITE_CONFIGS:
            canonical = JobCrawler._canonical_job_url(site, url)
            if canonical:
                return canonical
        parsed = urlparse(url)
        query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_"))
        return urlunparse(parsed._replace(query=urlencode(query), fragment=""))

    @staticmethod
    def _canonical_job_url(site: str, url: str) -> str:
        parsed = urlparse(url)
        expected = urlparse(SITE_CONFIGS[site]["base_url"])
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.netloc.lower().removeprefix("www.") != expected.netloc.removeprefix("www.")):
            return ""
        patterns = {"wanted": r"/wd/(\d+)/?", "jobkorea": r"/Recruit/GI_Read/(\d+)/?",
                    "linkareer": r"/activity/(\d+)/?", "jasoseol": r"/recruit/(\d+)/?",
                    "catch": r"/NCS/RecruitInfoDetails/(\d+)/?"}
        if site == "saramin":
            job_id = dict(parse_qsl(parsed.query)).get("rec_idx", "")
            if parsed.path.rstrip("/") == "/zf_user/jobs/relay/view" and job_id.isdigit():
                return f"{SITE_CONFIGS[site]['base_url']}/zf_user/jobs/relay/view?rec_idx={job_id}"
            return ""
        match = re.fullmatch(patterns[site], parsed.path, re.IGNORECASE)
        if not match:
            return ""
        paths = {"wanted": "/wd/", "jobkorea": "/Recruit/GI_Read/", "linkareer": "/activity/",
                 "jasoseol": "/recruit/", "catch": "/NCS/RecruitInfoDetails/"}
        return SITE_CONFIGS[site]["base_url"] + paths[site] + match.group(1)

    @staticmethod
    def _deduplicate(items: list[JobPosting]) -> list[JobPosting]:
        by_identity: dict[tuple[str, str], JobPosting] = {}
        for item in items:
            canonical = JobCrawler._canonical_job_url(item.site, item.link)
            if not canonical:
                continue
            item.link = canonical
            item.matched_keywords = list(dict.fromkeys([*item.matched_keywords, *([item.keyword] if item.keyword else [])]))
            key = (item.site, canonical)
            existing = by_identity.get(key)
            if existing is None:
                by_identity[key] = item
                continue
            existing.matched_keywords = list(dict.fromkeys(existing.matched_keywords + item.matched_keywords))
            for name in ("company", "location", "deadline", "description", "experience", "employment_type"):
                if not getattr(existing, name) and getattr(item, name):
                    setattr(existing, name, getattr(item, name))
        return list(by_identity.values())

    def _log(self, message: str) -> None:
        if self._log_path is not None:
            now = datetime.now(timezone.utc).isoformat()
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(f"{now} {message}\n")
        if self.debug or message.startswith(("[error]", "[warning]")):
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
        self.session.close()
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
