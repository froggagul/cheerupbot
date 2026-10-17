## alarmbot

Collect job postings from Saramin, JobKorea, Wanted, Linkareer, Jasoseol, and Catch using keyword searches and job category discovery.

Korean strings in examples are intentional search terms, source labels, or message text. You can customize message text without changing the documentation language.

### Supported site IDs

- `saramin`
- `jobkorea`
- `wanted`
- `linkareer`
- `jasoseol`
- `catch`

### Usage

```bash
python main.py \
  --sites saramin,jobkorea,wanted,linkareer,jasoseol,catch \
  --keywords HR,HRD \
  --limit-per-site 20 \
  --output jobs.json
```

Use `--input` to filter an existing JSON file without crawling again.

```bash
python main.py \
  --input jobs_debug.json \
  --output jobs_filtered.json
```

### JavaScript rendering

`wanted`, `linkareer`, `jasoseol`, and `catch` support `--render-js` because static HTML may not provide enough information.

```bash
python main.py \
  --sites wanted,linkareer,jasoseol,catch \
  --keywords HR,HRD \
  --limit-per-site 20 \
  --render-js \
  --debug \
  --output jobs_debug.json
```

Install the Playwright browser before using this option.

```bash
python -m playwright install chromium
```

### Raw response dumps

Save response bodies to inspect and refine each site's parser.

```bash
python main.py \
  --sites wanted,catch \
  --keywords HR,HRD \
  --render-js \
  --debug \
  --dump-dir dumps \
  --output jobs_debug.json
```

`--dump-dir` saves raw HTML/JSON, final response URLs, and a run log (`crawl.log`).

If no output file is specified, JSON is written to standard output.

### Crawl limits and diagnostics

`--limit-per-site` limits the number of postings per site/keyword pair **after filtering**.
When a DSL, plugin, or agent filter is enabled, the crawler collects up to three times this number of candidates by default.
Without filters, it collects up to the specified limit. Use `--candidate-limit-per-site` to set the candidate limit explicitly.
Page/request budgets and available search results may reduce the final count.
Crawl limits do not apply when processing an existing JSON file with `--input`.

```bash
python main.py \
  --sites saramin,jobkorea,wanted,linkareer,jasoseol,catch \
  --keywords HR,인사 \
  --limit-per-site 20 \
  --candidate-limit-per-site 60 \
  --max-pages 3 \
  --max-requests 20 \
  --max-detail-requests 10 \
  --crawl-report crawl_report.json \
  --output jobs.json
```

- `--max-pages`: search request budget per site/keyword pair (default: 3). Wanted and Catch paginate their APIs; HTML sources follow identifiable next-page links. Linkareer's category discovery uses the response's total count and page number. Jasoseol requests the active catalog and job taxonomy once each; the taxonomy request also counts toward this budget.
- `--max-requests`: total search and detail request budget per site/keyword pair (default: 20). HTTP retries and browser resource requests are excluded.
- `--max-detail-requests`: Wanted detail enrichment budget (default: 10; `0` disables enrichment). Details already fetched during the same run are reused. Deadlines missing from the source remain empty.
- Postings are deduplicated by job ID, and search terms are retained in `matched_keywords`. Source role descriptions, experience requirements, and employment types are passed through as `description`, `experience`, and `employment_type`.
- `--crawl-report`: saves each search's `success`, `empty`, `partial`, or `failed` status, request counts, posting count, stop reason, and errors. Check `stop_reason` for budget limits. `strategy` identifies the discovery method: `keyword`, `category`, `category+keyword_fallback`, or `active_catalog`. `cache_hits` counts search responses reused during the run. Reused pages count toward `pages` but do not consume additional `requests` or `page_requests`.
- Connection errors and HTTP 429/500/502/503/504 responses are retried up to twice. Normal requests to the same host are spaced at least 0.25 seconds apart; retries use backoff and respect `Retry-After`.
- Failures are reported to stderr even without `--debug`. If every search fails, the process exits with an error without overwriting existing output, and the daily runner stops before sending email.

The daily runner also supports `JOB_CANDIDATE_LIMIT_PER_SITE`, `JOB_MAX_PAGES`, `JOB_MAX_REQUESTS`, and `JOB_MAX_DETAIL_REQUESTS`. Crawl diagnostics are saved to `out/crawl_report_*.json`.

Run regression tests with `.venv/bin/python -m unittest discover -s tests -v`.
Fixtures in `tests/fixtures/crawler` contain only the fields/cards needed for tests, extracted from public responses verified on October 14 and October 18, 2026 (KST).

### Discovery by job category

Text searches for HR can miss recruitment campaigns with broad titles or role labels such as `Corporate`.
Linkareer and Jasoseol use the following strategies to discover candidates:

- **Linkareer:** searches for `HR`, `HRM`, `HRD`, `인사` (HR), `노무` (labor relations), `채용` (recruitment), `조직문화` (organizational culture), `사내 교육` (internal training), and `직원 교육` (employee training) browse open postings in the `인사/채용/노무` (HR/recruitment/labor relations) category, newest first. Listings become candidates even when the keyword is absent from the title. The crawler preserves actual categories, entry-level/experienced status, internship/contract status, and location, while excluding recommendations or advertisements outside the search results. If the category listing cannot be read, it tries the existing text search within the remaining request budget. Other keywords continue to use text search.
- **Jasoseol:** fetches the full active catalog once, without restricting it through `field_query`. It resolves each role's `duty_group_ids` using the public job taxonomy, then matches candidates against source text or role categories. The label `Corporate` alone does not establish an HR role. If the taxonomy cannot be read, the crawler reports the error and uses only available text. Each role's category and recruitment type, such as internship or entry-level, are passed to the filter.

Both strategies reuse shared responses within a run and fetch fresh data on the next run.
HR-related searches use a broad HR category, so `HRD` may also discover general HR candidates.
Existing downstream filters assess specific role relevance, experience requirements, and whether general affairs duties disqualify a posting.
Candidate and page limits still apply, so discovery does not guarantee complete coverage of all postings.

### General-purpose filters (JSON DSL)

Declare filters as JSON rules instead of embedding entry-level or experienced-hire logic in the code.

- Logical nodes: `all`, `any`, `not`
- Condition nodes: `field` or `fields` + `op` + `value`
- All-text target: `field: "_text"`

Supported operators include:

- `contains`, `not_contains`
- `eq`, `ne`, `in`, `not_in`
- `regex`, `not_regex`
- `exists`, `is_open`
- `gt`, `gte`, `lt`, `lte`
- `date_before`, `date_on_or_before`, `date_after`, `date_on_or_after`

Use Python plugins for rules that are difficult to express in the DSL:

- `--filter-plugin filters/plugin_example.py:keep_non_manager_and_seoul`
- Multiple plugins are combined with AND: every plugin must accept a posting.

Example:

```bash
python main.py \
  --input jobs_debug.json \
  --filter-file filters/new_grad.json \
  --output jobs_new_grad.json
```

Inline JSON is also supported:

```bash
python main.py \
  --input jobs_debug.json \
  --filter-json '{"field":"site","op":"in","value":["saramin","wanted"]}' \
  --output jobs_site_filtered.json
```

Combine the DSL with a plugin:

```bash
python main.py \
  --input jobs_debug.json \
  --filter-file filters/new_grad.json \
  --filter-plugin filters/plugin_example.py:keep_non_manager_and_seoul \
  --output jobs_custom_filtered.json
```

### Agent filtering (OpenAI API / Codex)

`AgentJobFilter` reads natural-language criteria and returns an inclusion decision and a Korean explanation for each posting.
`OpenAIClient` uses the Responses API; `CodexClient` uses the Codex CLI login on the current machine.
Enable agent filtering with `--agent-filter`; ordinary runs retain their existing behavior.

```bash
uv sync

# Check the Codex login on this machine; run codex login if needed.
codex login status
uv run main.py \
  --input jobs_debug.json \
  --agent-filter codex \
  --agent-prompt-file filters/new_grad_prompt.txt \
  --agent-report out/agent_decisions.json \
  --output out/jobs_agent.json
```

Create output directories such as `out/` before running (`mkdir -p out`).
The Codex provider requires a CLI that supports `--ignore-user-config`, `--ephemeral`, and `--output-schema`.
Logins are specific to the machine and user; a desktop login does not authenticate an EC2 instance.

To use the OpenAI API, copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and select `--agent-filter openai`.
The API key is separate from a ChatGPT login. Select another configuration file with an option such as `--env-file .env.ec2`.
Existing environment variables take precedence over the file; `--no-dotenv` disables file loading.

```bash
uv run main.py \
  --sites saramin,catch --keywords HR,HRD --limit-per-site 10 \
  --agent-filter openai \
  --agent-prompt "HR/인사 관련 신입 및 인턴만 포함. 경력직 전용, 총무, 인재풀은 제외." \
  --agent-report out/agent_decisions.json \
  --output out/jobs_agent.json
```

The example prompt includes only HR-related entry-level and internship positions, excluding experienced-only roles, general affairs, and talent pools.

- Default criteria: `filters/new_grad_prompt.txt`. The filter considers role context alongside entry-level, internship, and contract eligibility, and excludes unrelated education/culture postings and clearly closed jobs. It assesses postings open to any experience level, combined entry-level/experienced recruitment, and the word `manager` in context. Insufficient information results in exclusion with an explanation. Edit this file to change the criteria.
- `--agent-prompt` and `--agent-prompt-file` are mutually exclusive.
- Set the model with `--agent-model`. The OpenAI default is `OPENAI_MODEL` or `gpt-6-luna`; Codex uses `CODEX_MODEL` or the CLI default. Luna defaults to `medium` reasoning effort, configurable through `OPENAI_REASONING_EFFORT`. For other models, use a reasoning effort value supported by that model.
- `--agent-batch-size` defaults to 20, with a maximum of 100. `--agent-timeout` defaults to 120 seconds. The OpenAI client retries transient API errors up to twice, so total wait time may be longer.
- If provided, `--filter-file`, `--filter-json`, and `--filter-plugin` run before the agent. Omit these options to let the agent review all collected postings.
- Output JSON preserves the original fields and ordering. A separate `--agent-report` records the `id`, `keep`, `reason`, title, and link for every reviewed posting.
- Posting content is treated as untrusted input. The OpenAI client receives no tools. Codex runs read-only in a temporary directory with shell tools, apps, and web search disabled. SMTP/AWS environment variables are not passed to the Codex subprocess.
- The agent sees only collected fields such as title, company, location, deadline, and descriptions present in the input. It does not visit additional detail pages, and long fields are limited to 6,000 characters. Invalid responses, missing/duplicate IDs, authentication failures, and timeouts fail the run. There is no automatic fallback to the existing rules.

The daily runner also loads `.env` and defaults to OpenAI GPT-6 Luna with `medium` reasoning effort.
Use `JOB_ENV_FILE=.env.ec2` to select another file.
`JOB_AGENT_FILTER_PROVIDER=none` selects the existing regex filter; `codex` selects Codex.
An explicitly configured `JOB_FILTER_FILE` runs before the agent.
Message generation does not call the agent again, and filter failures stop delivery.

```dotenv
JOB_AGENT_FILTER_PROVIDER=openai
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=medium
JOB_AGENT_PROMPT_FILE=filters/new_grad_prompt.txt
JOB_AGENT_BATCH_SIZE=20
JOB_AGENT_TIMEOUT=120
```

Test the full local flow without sending email:

```bash
JOB_AGENT_FILTER_PROVIDER=codex JOB_INPUT_JSON=jobs_debug.json \
  JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh

# Run unit and script integration tests without network or live model calls.
uv run python -m unittest discover -s tests -v
```

API/CLI output references:
[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses),
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

### Message text output

Generate text suitable for pasting into KakaoTalk, Slack, or another messaging service.

```bash
python main.py \
  --input jobs_custom_filtered.json \
  --output-format message \
  --message-title "HRD 채용 요약" \
  --message-max-items 0 \
  --output jobs_message.txt
```

Omit `--output` to print the message to the terminal.

Random nickname greetings are also supported. By default, the CLI chooses a nickname from `nicknames.txt` and starts with `안녕 {nickname}야! 오늘의 채용 요약이야.` ("Hi {nickname}! Here's today's job summary.").

```bash
python main.py \
  --input jobs_custom_filtered.json \
  --output-format message \
  --message-nickname-file nicknames.txt \
  --message-greeting-template "안녕 {nickname}야! 오늘의 채용 요약이야." \
  --output jobs_message.txt
```

Disable the greeting with `--message-greeting-template ""`.

### Notes

- Dynamic rendering and bot protection on some sites may limit collection results.
- Parsers use selectors with JSON-LD/inline JSON fallbacks, so changes to a site's DOM may require parser updates.
- Failure diagnostics and bounded retries are built in. Check crawl reports alongside cron logs during operation.

### Email delivery (recommended)

`email_sender.py` sends `jobs_message.txt` to a configured recipient list.
Emails use multipart `text/plain + text/html` so links are clickable.

HTML emails show cards with separate company names and job titles, deadlines and days remaining, location, experience, employment type, Korean site names, and `공고 보기` ("View posting") links.
Jobs closing within three days of message generation receive distinct styling and labels.
Long source URLs appear as buttons in HTML and remain copyable URLs in the text version.
Both versions use the same HRD-first/deadline ordering and display limit.

Generate text and HTML together, then preview the email in a browser:

```bash
mkdir -p out
python main.py \
  --input jobs_custom_filtered.json \
  --output-format message \
  --output out/jobs_message.txt \
  --message-html-output out/jobs_message.html

python email_sender.py \
  --message-file out/jobs_message.txt \
  --html-file out/jobs_message.html \
  --preview-html out/email_preview.html
```

Open `out/email_preview.html` in a browser. `--preview-html` only saves a preview and exits; it does not require SMTP configuration or recipients.
Pass `--html-file` when sending to use the card layout. If omitted, the text message is converted to HTML.

Example recipient file (`recipients.txt`):

```txt
# One recipient per line.
me@example.com
friend@example.com
```

Environment setup:

```bash
cp .env.example .env
# Set SMTP_* values in .env.
```

Dry run:

```bash
python email_sender.py \
  --message-file jobs_message.txt \
  --subject "[알림] 오늘의 채용 요약" \
  --recipients-file recipients.txt \
  --dry-run
```

Send email:

```bash
python email_sender.py \
  --message-file jobs_message.txt \
  --subject "[알림] 오늘의 채용 요약" \
  --recipients-file recipients.txt
```

Options:

- `--to user@example.com`: repeatable; combined with recipients from the file.
- `--send-mode single|individual`: defaults to `single`, sending one email using BCC.
- `--use-ssl`: typically port 465; otherwise uses STARTTLS, typically port 587.
- `--env-file .env`: default environment file; `--no-dotenv` disables loading.
- `--html-file jobs_message.html`: HTML body using the card layout.
- `--preview-html preview.html`: saves HTML and exits without sending.

### GitHub Actions and repository-backed SQLite state

No separate database server is required. During a run, Python's built-in SQLite operates in memory.
Between runs, state is stored as a **`state.sql` text file** on the `crawl-state` branch.
Code and data live on separate branches; the data branch is created automatically on the first run.

`.github/workflows/daily-crawl.yml` runs these steps:

1. Install dependencies from the checked-out code and lockfile (`uv.lock`).
2. Read and validate the latest SQL snapshot from the remote `crawl-state` branch, then restore it into SQLite.
3. Insert or update collected postings by `(site, canonical URL)`.
4. Apply configured filters and the AI filter, then generate the daily HTML/text email.
5. Export SQL and commit/push to `crawl-state` only when its contents change.
6. Send the prepared email through SMTP only if all preceding steps succeed. Dry runs skip this step.

The configured recipients are **`froggaguldev@gmail.com`** and **`ohansae@naver.com`**. Edit `recipients.txt` to change them.
The email includes **all postings collected in the current run that survive filtering and the output limits**.
Stored historical postings are not merged back into the email, and previously sent postings are not excluded merely because they were sent before.
Rerunning the workflow may send the email again. Use a dry run to validate without delivery.

Configure these repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | AI filter authentication; also required for dry runs |
| `SMTP_HOST` | SMTP server |
| `SMTP_PORT` | Defaults to `587`, using STARTTLS |
| `SMTP_USER` | SMTP login account |
| `SMTP_PASSWORD` | SMTP password or app password |
| `SMTP_FROM` | Sender address; defaults to `SMTP_USER` if omitted |

Do not store a GitHub PAT as a workflow secret. State pushes use the per-run `GITHUB_TOKEN` with `contents: write` permission.
If repository/organization policies or branch rules block direct writes to `crawl-state`, pushes fail; allow automation to write to that data branch.

- **Manual validation:** Actions → Daily crawl and email → Run workflow. The default `dry_run=true` performs collection, filtering, and SQL persistence without sending email. HTML previews and reports are available in the artifact.
- **Live delivery:** disable `dry_run` for a manual run. Scheduled runs start at approximately 09:00 Korea time each day (`0 0 * * *` UTC). Email is sent after collection, filtering, and the SQL push finish. GitHub schedules can be delayed or skipped and do not guarantee an exact time. The workflow must exist on the default branch (`main`) for scheduled runs to operate.
- **Disable scheduling:** set the repository variable `JOB_SCHEDULE_ENABLED=false`. Manual runs remain available.
- **Model configuration:** set repository variables `OPENAI_MODEL` and `OPENAI_REASONING_EFFORT`. Defaults are `gpt-6-luna` and `medium`.
- **Overlapping runs:** a fixed concurrency group serializes the entire restore → crawl → push sequence. Runs wait for earlier runs to finish instead of canceling an active run.

The SQL snapshot stores normalized postings, content hashes, first/last seen timestamps, content change timestamps, and the latest AI decision for each posting.
It does not contain API keys, SMTP settings, recipient lists, or raw HTML.
A cached decision is reused only when the date, provider, model, reasoning settings, prompt, and AI input fields all match.
Because `collected_at` is an AI input field, a new collection timestamp or date can trigger reevaluation.

Runs with state enabled first update `last_seen` for collected postings, then clean up state using these rules:

- Delete postings whose normalized deadline is **before today in Asia/Seoul**. Keep postings closing today.
- Delete postings **not seen for at least seven days**, measured by Seoul calendar dates. A single missed collection does not immediately delete a posting. Rediscovery resets the seven-day clock.
- Delete cached AI decisions associated with removed postings. Closed postings are also excluded from the AI filter and email.
- Do not delete `OPEN`, empty, or unparseable deadlines based on deadline alone; the seven-day absence rule still applies. Postings that continue to be collected and remain open are retained regardless of their original discovery date.

Cleanup applies to crawl and JSON-input runs using `--state-file`, including existing SQL snapshots.
Partial source failures and missed runs count toward the absence period.
If all searches fail and collection aborts, cleanup does not run; the elapsed time is accounted for after the next completed collection.
The `[state-cleanup]` log entry reports deadline-based deletions, seven-day absence deletions, and the remaining posting count.

Even if filtering fails, the persistence step pushes state for postings already collected. Persistence finishes before email delivery.
A failed remote read or corrupt SQL stops the run instead of starting with an empty database.
Pushes never use force and fail if another writer causes a conflict; the artifact's SQL remains available for inspection.
Email is not sent if the SQL push fails. A forced process termination can lose progress made since the last SQL save.
Artifacts are retained for 14 days; the Git branch is the permanent state store.
Deleted postings remain in Git history, so this storage is not intended for large raw documents or attachments.

Use the same state file locally:

```bash
# The default path is out/state.sql; state updates even without email delivery.
JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh

# Explicitly enable state when running the crawler directly.
.venv/bin/python main.py --sites wanted --keywords HR \
  --state-file out/state.sql --output out/jobs.json

# Restore/update/push repository state in a separate directory (requires Git write access).
.venv/bin/python scripts/sync_crawl_state.py restore --directory .crawl-state
JOB_STATE_FILE=.crawl-state/state.sql JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh
.venv/bin/python scripts/sync_crawl_state.py save --directory .crawl-state
```

Change the path with `JOB_STATE_FILE`, or disable persistent state with an empty value (`JOB_STATE_FILE=`).
The restore directory must be empty; GitHub Actions satisfies this by using a fresh runner for each run.
Choose one scheduling environment: scheduling both server cron and GitHub Actions can result in duplicate emails.

Validation:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

### Daily automation with cron

`scripts/run_daily_email.sh` performs these steps:

1. Collect and filter postings → `out/jobs_*.json`.
2. Generate text/HTML messages → `out/jobs_message_*.txt`, `out/jobs_message_*.html`.
3. Send email.

View the latest HTML at `out/jobs_message_latest.html`.

Test with existing JSON and email dry-run mode:

```bash
JOB_INPUT_JSON=jobs_debug.json JOB_EMAIL_DRY_RUN=1 ./scripts/run_daily_email.sh
```

Install a cron job for 09:00 daily in the server's local timezone:

```bash
./scripts/install_cron.sh "0 9 * * *"
```

Key environment variables:

- `JOB_KEYWORDS`: defaults to `HR,인사,조직문화,문화,교육,HRD`.
- `JOB_FILTER_FILE`: defaults to `filters/new_grad.json` when the agent provider is `none`; otherwise unset unless explicitly configured.
- `JOB_RECIPIENTS_FILE`: defaults to `recipients.txt`.
- `JOB_EMAIL_SUBJECT`: defaults to `[알림] 오늘의 채용 요약` ("[Notification] Today's job summary").
- `JOB_EMAIL_DRY_RUN`: set to `1` for a dry run instead of delivery.
- `JOB_INPUT_JSON`: skips crawling and processes the specified JSON file.
- `JOB_ENV_FILE`: defaults to `.env`.
- `JOB_MESSAGE_MAX_ITEMS`: defaults to `0`, displaying all items.

### Deployment on a single Ubuntu server

Clone the repository on the server, then run:

```bash
bash scripts/bootstrap_ubuntu.sh --cron "0 9 * * *"
```

Options:

- `--with-playwright`: install support for JavaScript rendering during crawling.
- `--env-file .env`: select an environment file.
- `--dry-run`: inspect installation commands without executing them.

After installation:

1. Set `SMTP_*` values in `.env`.
2. Configure recipients in `recipients.txt`.
3. Test with `JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh`.
4. Check cron with `crontab -l`.

### KakaoTalk friend delivery (test app members)

`kakao_sender.py` sends a text file such as `jobs_message.txt` through KakaoTalk.

Requirements:

- A Kakao app REST API key
- A `refresh_token` (recommended) or `access_token`
- Recipient UUIDs or nicknames

Example token file (`kakao_tokens.json`):

```json
{
  "refresh_token": "YOUR_REFRESH_TOKEN"
}
```

List friends to look up nicknames and UUIDs:

```bash
python kakao_sender.py \
  --rest-api-key "$KAKAO_REST_API_KEY" \
  --token-file kakao_tokens.json \
  --list-friends
```

Dry run to inspect the payload without sending:

```bash
python kakao_sender.py \
  --rest-api-key "$KAKAO_REST_API_KEY" \
  --token-file kakao_tokens.json \
  --message-file jobs_message.txt \
  --receiver-name "홍길동" \
  --dry-run
```

Send a message:

```bash
python kakao_sender.py \
  --rest-api-key "$KAKAO_REST_API_KEY" \
  --token-file kakao_tokens.json \
  --message-file jobs_message.txt \
  --receiver-name "홍길동"
```

Long messages are automatically split according to `--max-text-length`.
When using `.env`, set the `KAKAO_*` variables there and omit `--rest-api-key`.

### TODO

- Ensure links are clickable in Naver Mail.
