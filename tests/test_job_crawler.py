import contextlib
import io
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

import main
from job_crawler import CrawlError, JobCrawler, JobPosting, SITE_CONFIGS

FIXTURES = Path(__file__).parent / 'fixtures' / 'crawler'
NOW = '2026-10-14T00:00:00+00:00'


def response(payload, url='https://www.wanted.co.kr/api/search', status=200):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result.encoding = 'utf-8'
    result._content = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
    return result


def wanted_page(ids, total=100):
    return {'data': [{'id': i, 'position': f'HR role {i}', 'company': {'id': 999, 'name': 'HR Company'}}
                     for i in ids], 'total_count': total}


def linkareer_page(job_id=356625, total=1):
    payload = json.loads((FIXTURES / 'linkareer.json').read_text())
    cache = payload['props']['pageProps']['__APOLLO_STATE__']
    node = cache.pop('Activity:356625')
    node['id'] = str(job_id)
    cache[f'Activity:{job_id}'] = node
    connection = next(iter(cache['ROOT_QUERY'].values()))
    connection.update(totalCount=total, nodes=[{'__ref': f'Activity:{job_id}'}])
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + '</script>'


def posting(site='wanted', keyword='HR', title='HR role', job_id=1, **kwargs):
    paths = {'wanted': f'/wd/{job_id}', 'saramin': f'/zf_user/jobs/relay/view?rec_idx={job_id}'}
    return JobPosting(site, keyword, title, '', '', '', SITE_CONFIGS[site]['base_url'] + paths[site], '', NOW, **kwargs)


class CrawlerTests(unittest.TestCase):
    def crawler(self, **kwargs):
        crawler = JobCrawler(request_interval=0, max_detail_requests=kwargs.pop('max_detail_requests', 0), **kwargs)
        self.addCleanup(crawler.close)
        return crawler

    def test_captured_jobkorea_cards_include_company_and_category_matches(self):
        crawler = self.crawler()
        html = (FIXTURES / 'jobkorea.html').read_text()
        rows = crawler._extract_html(BeautifulSoup(html, 'lxml'), 'jobkorea', 'HR', SITE_CONFIGS['jobkorea']['base_url'])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row.company for row in rows))
        prada = next(row for row in rows if '프라다' in row.title)
        self.assertNotIn('hr', prada.title.lower())
        self.assertIn('hr', prada.description.lower())
        self.assertNotIn('traceId', rows[0].link)

    def test_captured_jasoseol_role_search_and_no_hardcoded_hr_override(self):
        crawler = self.crawler()
        payload = json.loads((FIXTURES / 'jasoseol.json').read_text())
        rows = crawler._extract_from_jasoseol_api_payload(payload, 'HR', 'https://jasoseol.com/api', 'https://jasoseol.com')
        self.assertEqual(len(rows), 4)
        row = next(row for row in rows if row.company == '한국투자증권')
        self.assertIn('경영관리_HR', row.description)
        self.assertEqual(crawler._extract_from_jasoseol_api_payload(payload, 'unrelated', '', 'https://jasoseol.com'), [])

    def test_captured_api_metadata_and_no_nested_company_jobs(self):
        crawler = self.crawler()
        wanted = json.loads((FIXTURES / 'wanted.json').read_text())
        wanted['data'][0]['company'] = {'id': 999, 'name': 'HR Company'}
        rows = crawler._extract_from_wanted_api_payload(wanted, 'HR', '', 'https://www.wanted.co.kr')
        self.assertEqual(len(rows), len(wanted['data']))
        self.assertNotIn('https://www.wanted.co.kr/wd/999', [row.link for row in rows])
        self.assertTrue(rows[0].experience)
        catch = json.loads((FIXTURES / 'catch.json').read_text())
        rows = crawler._extract_from_catch_api_payload(catch, 'HR', '', 'https://www.catch.co.kr')
        self.assertIn('4년', rows[0].experience)
        self.assertEqual(rows[0].employment_type, '정규직')
        self.assertIn('ERP', rows[0].description)

    def test_linkareer_discovers_bundled_recruitment_by_category_and_reuses_pages(self):
        crawler = self.crawler()
        with patch.object(crawler.session, 'get', side_effect=[
            response(linkareer_page(100, total=2)), response(linkareer_page(total=2))]) as get:
            rows = crawler.crawl(['linkareer'], ['HR', '인사'], 60)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(all('/list/recruit?' in call.args[0] for call in get.call_args_list))
        self.assertIn('filterBy_categoryIDs=101004', get.call_args_list[0].args[0])
        self.assertIn('page=2', get.call_args_list[1].args[0])
        row = next(row for row in rows if row.link.endswith('/356625'))
        self.assertNotIn('인사', row.title)
        self.assertIn('인사/채용/노무', row.description)
        self.assertEqual((row.company, row.experience, row.employment_type, row.location), ('크래프톤', '신입', '인턴', '서울'))
        self.assertEqual(row.matched_keywords, ['HR', '인사'])
        self.assertEqual(crawler.outcomes[1].cache_hits, 2)
        self.assertEqual(crawler.outcomes[1].requests, 0)

    def test_linkareer_only_reads_main_results_and_resolves_missing_references(self):
        crawler = self.crawler()
        payload = json.loads((FIXTURES / 'linkareer.json').read_text())
        cache = payload['props']['pageProps']['__APOLLO_STATE__']
        cache['Activity:999'] = dict(cache['Activity:356625'], id='999', title='HR sidebar advertisement')
        cache['Activity:356625']['categories'].append({'__ref': 'Category:missing'})
        soup = BeautifulSoup('<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>', 'lxml')
        rows = crawler._extract_html(soup, 'linkareer', '인사', 'https://linkareer.com/search')
        self.assertEqual([row.link for row in rows], ['https://linkareer.com/activity/356625'])
        self.assertEqual(crawler._extract_html(soup, 'linkareer', 'unrelated', 'https://linkareer.com/search'), [])

    def test_linkareer_category_page_budget_and_repeated_pages(self):
        for budget, requests_count, reason in [(1, 1, 'page_budget'), (3, 2, 'repeated_page')]:
            with self.subTest(budget=budget):
                crawler = self.crawler(max_pages=budget)
                with patch.object(crawler.session, 'get', return_value=response(linkareer_page(total=50))) as get:
                    rows = crawler.crawl(['linkareer'], ['HR'], 60)
                self.assertEqual(len(rows), 1)
                self.assertEqual(get.call_count, requests_count)
                self.assertEqual(crawler.outcomes[0].stop_reason, reason)

    def test_linkareer_empty_category_and_keyword_fallback(self):
        crawler = self.crawler()
        payload = json.loads((FIXTURES / 'linkareer.json').read_text())
        connection = next(iter(payload['props']['pageProps']['__APOLLO_STATE__']['ROOT_QUERY'].values()))
        connection.update(totalCount=0, nodes=[])
        html = '<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>'
        with patch.object(crawler.session, 'get', return_value=response(html)) as get:
            self.assertEqual(crawler.crawl(['linkareer'], ['HR']), [])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(crawler.outcomes[0].status, 'empty')
        with contextlib.redirect_stderr(io.StringIO()), patch.object(crawler.session, 'get', side_effect=[
            response('Unavailable', status=503), response('<a href="/activity/1">HR job</a>')]):
            rows = crawler.crawl(['linkareer'], ['HR'], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(crawler.outcomes[0].status, 'partial')
        self.assertEqual(crawler.outcomes[0].strategy, 'category+keyword_fallback')

    def test_jasoseol_discovers_corporate_hr_from_catalog_and_live_taxonomy(self):
        crawler = self.crawler()
        catalog = json.loads((FIXTURES / 'jasoseol_catalog.json').read_text())
        categories = json.loads((FIXTURES / 'jasoseol_categories.json').read_text())
        # A generic Corporate label with only engineering categories is not HR.
        unrelated = dict(catalog[0], id=999, name='Other company')
        unrelated['employments'] = [dict(catalog[0]['employments'][0], duty_group_ids=[168])]
        catalog.append(unrelated)
        with patch.object(crawler.session, 'get', side_effect=[response(catalog), response(categories)]) as get:
            rows = crawler.crawl(['jasoseol'], ['HR', '인사', 'unrelated'], 60)
        self.assertEqual(get.call_count, 2)
        self.assertNotIn('field_query', get.call_args_list[0].kwargs['params'])
        self.assertEqual(get.call_args_list[0].kwargs['params']['all'], 'true')
        self.assertIn('after_end_time', get.call_args_list[0].kwargs['params'])
        self.assertEqual([r.company for r in rows], ['크래프톤'])
        self.assertIn('Corporate [인턴] (직무 분류: 인사', rows[0].description)
        self.assertIn('인턴', rows[0].employment_type)
        self.assertEqual(rows[0].matched_keywords, ['HR', '인사'])
        self.assertEqual(crawler.outcomes[1].requests, 0)
        self.assertEqual(crawler.outcomes[2].status, 'empty')

    def test_jasoseol_missing_taxonomy_keeps_literal_matches_without_guessing(self):
        crawler = self.crawler()
        catalog = json.loads((FIXTURES / 'jasoseol_catalog.json').read_text())
        catalog.append({'id': 1, 'name': 'Example', 'title': 'HR intern', 'employments': []})
        with contextlib.redirect_stderr(io.StringIO()), patch.object(crawler.session, 'get', side_effect=[
            response(catalog), response({'unexpected': []})]):
            rows = crawler.crawl(['jasoseol'], ['HR', '인사'], 60)
        self.assertEqual([r.company for r in rows], ['Example'])
        self.assertEqual([o.status for o in crawler.outcomes], ['partial', 'partial'])

    def test_jasoseol_catalog_respects_budgets_and_refreshes_on_next_run(self):
        crawler = self.crawler(max_requests=1)
        catalog = [{'id': 1, 'name': 'Example', 'title': 'HR intern'}]
        with patch.object(crawler.session, 'get', return_value=response(catalog)) as get:
            self.assertEqual(len(crawler.crawl(['jasoseol'], ['HR'])), 1)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(crawler.outcomes[0].stop_reason, 'request_budget')
        with patch.object(crawler.session, 'get', return_value=response([])) as get:
            self.assertEqual(crawler.crawl(['jasoseol'], ['HR']), [])
        self.assertEqual(get.call_count, 1)

    def test_overlapping_containers_do_not_consume_limit(self):
        crawler = self.crawler()
        html = ('<div class="item_recruit item"><a href="/zf_user/jobs/relay/view?rec_idx=1">HR Alpha</a></div>'
                '<article><a href="/zf_user/jobs/relay/view?rec_idx=2">HR Beta</a></article>')
        with patch.object(crawler.session, 'get', return_value=response(html, 'https://www.saramin.co.kr/search')):
            rows = crawler.crawl(['saramin'], ['HR'], 2)
        self.assertEqual(len(rows), 2)

    def test_non_job_links_do_not_suppress_structured_jobs(self):
        crawler = self.crawler()
        node = {'@type': 'JobPosting', 'title': 'HR actual job', 'url': 'https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1'}
        html = ('<article><a href="/salary/HR">HR salary guide</a></article>'
                '<article><a href="javascript:alert(1)">HR unsafe</a></article>'
                '<script type="application/ld+json">' + json.dumps(node) + '</script>')
        rows = crawler._extract_html(BeautifulSoup(html, 'lxml'), 'saramin', 'HR', 'https://www.saramin.co.kr/search')
        self.assertEqual([row.title for row in rows], ['HR actual job'])

    def test_schema_fields_graph_arrays_and_publication_date(self):
        crawler = self.crawler()
        job = {'@type': ['Thing', 'JobPosting'], 'title': 'People operations', 'url': 'https://www.wanted.co.kr/wd/1',
               'description': '<p>HR onboarding</p>', 'hiringOrganization': {'name': 'Acme'},
               'jobLocation': [{'address': {'addressCountry': {'name': 'KR'}, 'addressLocality': 'Seoul'}}],
               'validThrough': '2026-12-31', 'datePosted': '2026-10-01'}
        def soup():
            return BeautifulSoup('<script type="application/ld+json">' + json.dumps({'@graph': [job]}) + '</script>', 'lxml')
        rows = crawler._extract_html(soup(), 'wanted', 'HR', 'https://www.wanted.co.kr/search')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].company, rows[0].location, rows[0].deadline), ('Acme', 'KR Seoul', '2026-12-31'))
        self.assertEqual(rows[0].description, 'HR onboarding')
        del job['validThrough']
        self.assertEqual(crawler._extract_wanted_detail_fields(soup())[1], '')
        job['@type'] = 'Organization'
        self.assertEqual(crawler._extract_html(soup(), 'wanted', 'HR', 'https://www.wanted.co.kr/search'), [])

    def test_identity_merges_tracking_titles_metadata_and_keywords(self):
        first = posting(site='saramin')
        second = replace(first, keyword='인사', title='Alternate title', company='Acme', description='HR onboarding',
                         link=first.link + '&search_uuid=abc&searchword=HR')
        rows = JobCrawler._deduplicate([first, second])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].matched_keywords, ['HR', '인사'])
        self.assertEqual(rows[0].company, 'Acme')
        self.assertEqual(rows[0].title, 'HR role')
        self.assertNotIn('search_uuid', rows[0].link)
        self.assertEqual(JobCrawler._canonical_job_url('wanted', 'https://evil.example/wd/1'), '')

    def test_wanted_paginates_using_actual_server_page_size(self):
        crawler = self.crawler()
        with patch.object(crawler.session, 'get', side_effect=[response(wanted_page([1, 2])), response(wanted_page([3, 4]))]) as get:
            rows = crawler.crawl(['wanted'], ['HR'], 4)
        self.assertEqual(len(rows), 4)
        self.assertEqual([call.kwargs['params']['offset'] for call in get.call_args_list], ['0', '2'])
        self.assertEqual(crawler.outcomes[0].pages, 2)

    def test_catch_paginates_and_stops_at_total(self):
        crawler = self.crawler()
        pages = [response({'recruitData': [{'RecruitID': i, 'RecruitTitle': f'HR {i}'}], 'intTotalRecordCount': 2}) for i in (1, 2)]
        with patch.object(crawler.session, 'get', side_effect=pages) as get:
            rows = crawler.crawl(['catch'], ['HR'], 20)
        self.assertEqual(len(rows), 2)
        self.assertEqual([call.kwargs['params']['curpage'] for call in get.call_args_list], ['1', '2'])

    def test_repeated_pages_and_page_budgets_stop(self):
        for max_pages, expected, reason in [(3, 2, 'repeated_page'), (1, 1, 'page_budget')]:
            with self.subTest(max_pages=max_pages):
                crawler = self.crawler(max_pages=max_pages)
                with patch.object(crawler.session, 'get', return_value=response(wanted_page([1]))) as get:
                    rows = crawler.crawl(['wanted'], ['HR'], 100)
                self.assertEqual(len(rows), 1)
                self.assertEqual(get.call_count, expected)
                self.assertEqual(crawler.outcomes[0].stop_reason, reason)

    def test_html_follows_next_page(self):
        crawler = self.crawler()
        first = '<a href="/activity/1">Engineer first</a><a href="/search?q=Engineer&page=2">2</a>'
        second = '<a href="/activity/2">Engineer second</a>'
        with patch.object(crawler.session, 'get', side_effect=[response(first, 'https://linkareer.com/search?q=Engineer&page=1'),
                                                              response(second, 'https://linkareer.com/search?q=Engineer&page=2')]) as get:
            rows = crawler.crawl(['linkareer'], ['Engineer'], 2)
        self.assertEqual(len(rows), 2)
        self.assertIn('page=2', get.call_args_list[1].args[0])

    def test_successful_api_results_are_enriched_with_a_budget_and_cache(self):
        crawler = self.crawler(max_detail_requests=1)
        page = response(wanted_page([1, 2], total=2))
        detail = response('<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'title': 'HR role', 'description': 'Onboarding',
            'jobLocation': {'address': {'addressLocality': 'Seoul'}}, 'validThrough': '2026-12-31',
        }) + '</script>', 'https://www.wanted.co.kr/wd/1')
        detail_two = response(detail.text, 'https://www.wanted.co.kr/wd/2')
        with patch.object(crawler.session, 'get', side_effect=[page, detail, page, detail_two]) as get:
            rows = crawler.crawl(['wanted'], ['HR', '인사'], 2)
        self.assertEqual(rows[0].location, 'Seoul')
        self.assertEqual(rows[0].deadline, '2026-12-31')
        self.assertEqual(rows[0].description, 'Onboarding')
        self.assertEqual(rows[0].matched_keywords, ['HR', '인사'])
        # One search + one detail per keyword; the cached first job doesn't consume another request.
        detail_urls = [call.args[0] for call in get.call_args_list if '/wd/' in call.args[0]]
        self.assertEqual(detail_urls, ['https://www.wanted.co.kr/wd/1', 'https://www.wanted.co.kr/wd/2'])

    def test_request_budget_limits_detail_fetches(self):
        crawler = self.crawler(max_requests=1, max_detail_requests=5)
        with patch.object(crawler.session, 'get', return_value=response(wanted_page([1, 2], total=2))) as get:
            rows = crawler.crawl(['wanted'], ['HR'], 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(crawler.outcomes[0].stop_reason, 'request_budget')

    def test_valid_empty_api_is_distinct_from_schema_and_network_failure(self):
        crawler = self.crawler()
        with patch.object(crawler.session, 'get', return_value=response({'data': [], 'total_count': 0})):
            self.assertEqual(crawler.crawl(['wanted'], ['HR']), [])
        self.assertEqual(crawler.outcomes[0].status, 'empty')
        for result in [response({'unexpected': []}), response([{'id': 1, 'position': 'HR'}]),
                       response('<html>blocked</html>'), requests.ConnectionError('offline')]:
            with self.subTest(result=result), contextlib.redirect_stderr(io.StringIO()) as errors:
                with patch.object(crawler.session, 'get', side_effect=[result]):
                    with self.assertRaises(CrawlError):
                        crawler.crawl(['wanted'], ['HR'])
                self.assertEqual(crawler.outcomes[0].status, 'failed')
                self.assertIn('[error]', errors.getvalue())

    def test_partial_failure_retains_jobs_and_records_error(self):
        crawler = self.crawler()
        with contextlib.redirect_stderr(io.StringIO()), patch.object(crawler.session, 'get', side_effect=[
            response(wanted_page([1])), requests.ConnectionError('offline')]):
            rows = crawler.crawl(['wanted'], ['HR'], 10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(crawler.outcomes[0].status, 'partial')
        self.assertTrue(crawler.outcomes[0].errors)

    def test_unrecognized_html_fails_but_known_empty_page_succeeds(self):
        crawler = self.crawler()
        with contextlib.redirect_stderr(io.StringIO()), patch.object(crawler.session, 'get', return_value=response('<h1>Access denied</h1>')):
            with self.assertRaises(CrawlError):
                crawler.crawl(['saramin'], ['HR'])
        with patch.object(crawler.session, 'get', return_value=response('<p>검색 결과가 없습니다.</p>')):
            self.assertEqual(crawler.crawl(['saramin'], ['HR']), [])
        self.assertEqual(crawler.outcomes[0].status, 'empty')

    def test_generator_keywords_are_reused_and_invalid_inputs_rejected(self):
        crawler = self.crawler()
        with patch.object(crawler, '_crawl_site_keyword', return_value=[posting()]) as crawl:
            crawler.crawl(['wanted', 'catch'], iter(['HR', 'HRD']))
        self.assertEqual(crawl.call_count, 4)
        for sites, keywords, limit in [(['typo'], ['HR'], 1), (['wanted'], [], 1), (['wanted'], ['HR'], 0)]:
            with self.assertRaises(ValueError):
                crawler.crawl(sites, keywords, limit)

    def test_saramin_fragment_pager_uses_page_attribute(self):
        crawler = self.crawler()
        soup = BeautifulSoup('<a href="#recruit_info" page="2" class="page page_move">2</a>', 'lxml')
        url = crawler._next_html_page(soup, 'saramin', 'https://www.saramin.co.kr/zf_user/search/recruit?searchword=HR')
        self.assertIn('recruitPage=2', url)

    def test_render_failure_is_reported_and_http_fallback_is_bounded(self):
        crawler = self.crawler(render_js=True, max_pages=2)
        page = Mock()
        page.goto.side_effect = RuntimeError('browser unavailable')
        context = Mock()
        with patch.object(crawler, '_new_page', return_value=(context, page)), contextlib.redirect_stderr(io.StringIO()):
            with patch.object(crawler.session, 'get', return_value=response('<a href="/activity/1">Engineer job</a>', 'https://linkareer.com/search')):
                rows = crawler.crawl(['linkareer'], ['Engineer'], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(crawler.outcomes[0].status, 'partial')
        self.assertEqual(crawler.outcomes[0].requests, 2)
        page.close.assert_called_once()
        context.close.assert_called_once()

    def test_detail_redirect_does_not_enrich_wrong_job(self):
        crawler = self.crawler(max_detail_requests=1)
        detail = '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'title': 'Wrong job', 'validThrough': '2026-12-31',
        }) + '</script>'
        with contextlib.redirect_stderr(io.StringIO()), patch.object(crawler.session, 'get', side_effect=[
            response(wanted_page([1], total=1)), response(detail, 'https://www.wanted.co.kr/wd/999')]):
            rows = crawler.crawl(['wanted'], ['HR'], 1)
        self.assertEqual(rows[0].deadline, '')
        self.assertEqual(crawler.outcomes[0].status, 'partial')

    def test_http_adapter_retries_transient_status_but_not_forbidden(self):
        class Handler(BaseHTTPRequestHandler):
            statuses = [503, 200, 403]
            calls = 0
            def do_GET(self):
                Handler.calls += 1
                self.send_response(Handler.statuses.pop(0))
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        crawler = self.crawler(retries=1)
        url = f'http://127.0.0.1:{server.server_port}/'
        try:
            self.assertIsNotNone(crawler._get('wanted', 'HR', url))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(crawler._get('wanted', 'HR', url))
            self.assertEqual(Handler.calls, 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class CrawlerCliTests(unittest.TestCase):
    def test_filtering_happens_before_final_limit(self):
        rows = [posting(job_id=i, title='Senior' if i < 3 else 'Junior').to_dict() for i in range(1, 6)]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'jobs.json'
            argv = ['main.py', '--no-dotenv', '--sites', 'wanted', '--keywords', 'HR', '--limit-per-site', '2',
                    '--filter-json', '{"field":"title","op":"contains","value":"Junior"}', '--output', str(output)]
            with patch('sys.argv', argv), patch('main._crawl_payload', return_value=rows), contextlib.redirect_stdout(io.StringIO()):
                main.main()
            result = json.loads(output.read_text())
            self.assertEqual(len(result), 2)
            self.assertTrue(all(row['title'] == 'Junior' for row in result))

    def test_filtering_expands_candidate_budget(self):
        argv = ['main.py', '--no-dotenv', '--sites', 'wanted', '--keywords', 'HR', '--limit-per-site', '2',
                '--filter-json', '{"field":"title","op":"contains","value":"Junior"}']
        with patch('sys.argv', argv):
            args = main.parse_args()
        with patch('main.JobCrawler') as cls:
            cls.return_value.crawl.return_value = []
            main._crawl_payload(args)
        self.assertEqual(cls.return_value.crawl.call_args.kwargs['limit_per_site'], 6)
        cls.return_value.close.assert_called_once()

    def test_failure_writes_report_without_overwriting_output(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'report.json'
            output = Path(temp) / 'jobs.json'
            output.write_text('previous result')
            argv = ['main.py', '--no-dotenv', '--sites', 'wanted', '--keywords', 'HR',
                    '--crawl-report', str(report), '--output', str(output)]
            with patch('sys.argv', argv), patch('requests.Session.get', side_effect=requests.ConnectionError('offline')):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(SystemExit, 'Crawl failed'):
                    main.main()
            self.assertEqual(output.read_text(), 'previous result')
            self.assertEqual(json.loads(report.read_text())[0]['status'], 'failed')

    def test_report_output_collisions_and_negative_budgets_are_rejected(self):
        for flags in [ ['--crawl-report', 'same.json', '--output', 'same.json'], ['--max-pages', '0'],
                       ['--max-requests', '-1'], ['--max-detail-requests', '-1'], ['--limit-per-site', '0'] ]:
            with self.subTest(flags=flags), patch('sys.argv', ['main.py', '--no-dotenv', *flags]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main.parse_args()

    def test_deadline_timezone_rollover_and_publication_independence(self):
        self.assertEqual(main.normalize_deadline('2026-10-14T23:00:00Z', NOW), '2026-10-15')
        self.assertEqual(main.normalize_deadline('01/05', '2026-12-31T00:00:00+00:00'), '2027-01-05')
        self.assertEqual(main.normalize_deadline('12/31', '2027-01-02T00:00:00+00:00'), '2026-12-31')
        self.assertEqual(main.normalize_deadline('10.31', NOW), '2026-10-31')
        self.assertEqual(main.normalize_deadline('D-Day', NOW), '2026-10-14')


if __name__ == '__main__':
    unittest.main()
