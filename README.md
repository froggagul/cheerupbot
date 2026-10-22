## alarmbot

Collect job postings from Saramin, JobKorea, Wanted, Linkareer, Jasoseol, and Catch using keyword searches and job category discovery.

Korean strings in examples are intentional search terms, source labels, or message text. You can customize message text without changing the documentation language.

### Environment setup

For the default daily run (OpenAI filtering and email delivery), fill the four values in [`.env.example`](.env.example). Install dependencies and create your local file once:

```bash
uv sync --frozen
test -f .env || cp .env.example .env
chmod 600 .env
```

Edit `.env` with your own values. It is ignored by Git.

| Variable | Where to get the value |
| --- | --- |
| `OPENAI_API_KEY` | Create a key in your OpenAI API account, as described below. |
| `SMTP_HOST` | Your sending email provider's SMTP server; Gmail uses `smtp.gmail.com`. |
| `SMTP_USER` | Your provider's SMTP login; for Gmail, your full sending email address. |
| `SMTP_PASSWORD` | Your provider's SMTP credential; for Gmail, an app password. |

**Get an OpenAI API key**

1. Sign in to the [OpenAI API dashboard](https://platform.openai.com/api-keys) and create an API key for the project you will use.
2. Copy the key into `OPENAI_API_KEY` in `.env`.
3. Check your [API billing and credits](https://platform.openai.com/account/billing/overview) before running the daily filter. See the [official OpenAI quickstart](https://developers.openai.com/api/docs/quickstart) for key setup and billing guidance.

**Get Gmail SMTP credentials**

1. Sign in to the Google account that will send the emails and enable **2-Step Verification**.
2. Open [Google App passwords](https://myaccount.google.com/apppasswords), create a password for this bot, and save it as `SMTP_PASSWORD`. App passwords require 2-Step Verification and may be unavailable for some account policies; see [Google's app-password guide](https://support.google.com/accounts/answer/185833?hl=en).
3. Set `SMTP_HOST=smtp.gmail.com` and `SMTP_USER` to that account's full email address. This app already uses STARTTLS on port `587`, matching [Google's SMTP settings](https://knowledge.workspace.google.com/admin/gmail/send-email-from-a-printer-scanner-or-app).

For another email provider, get its SMTP server, login, and SMTP/app password from its mail settings or administrator. The daily script and workflow use password authentication with STARTTLS; they do not implement OAuth login. A provider that requires SSL on port `465` needs `email_sender.py --use-ssl --smtp-port 465`; changing the port alone does not switch the daily automation to SSL.

**Values you can leave out**

- `SMTP_PORT` defaults to `587`; `SMTP_FROM` defaults to `SMTP_USER`. Add a sender override only when your provider requires an approved address different from the SMTP login.
- The daily runner already defaults to OpenAI, `gpt-6-luna`, `medium` reasoning, `filters/new_grad_prompt.txt`, batches of `20`, a `120`-second timeout, and state at `out/state.sql`. Optional overrides are described under [agent filtering](#agent-filtering-openai-api--codex) and [daily automation](#daily-automation-with-cron).
- SMTP credentials are unnecessary for `JOB_EMAIL_DRY_RUN=1` or HTML previews. OpenAI filtering still needs its API key during a dry run. Plain crawling and `JOB_AGENT_FILTER_PROVIDER=none` do not need an OpenAI key.

GitHub Actions reads [repository secrets](#github-actions-and-repository-backed-sqlite-state), rather than your local `.env`. Exported environment variables take precedence over `.env`; select another local file with `JOB_ENV_FILE=.env.ec2` for the daily script or `--env-file .env.ec2` for the Python CLIs.

### Adapt for another user

1. **Set up your own copy.** Clone your own repository and follow [environment setup](#environment-setup) with your OpenAI account and sending mailbox.
2. **Choose recipients and greetings.** Copy `recipients.example.txt` to `recipients.txt` and replace the example addresses with the intended recipients, one per line. Copy `nicknames.example.txt` to `nicknames.txt` and edit the names. Both local files are ignored by Git. Leaving the nickname file absent omits the greeting.
3. **Change job preferences.** Edit `filters/new_grad_prompt.txt` for your role, experience, and location criteria. Add `JOB_KEYWORDS` to `.env` when changing search terms; `JOB_MESSAGE_TITLE` and `JOB_EMAIL_SUBJECT` customize the message labels. For Actions, put these optional overrides in the workflow's `env` section.
4. **Set your fork's test mailbox.** In your own fork, change `TEST_EMAIL_RECIPIENT` in `email_sender.py` and the matching expectations in `tests/test_email_format.py` and `tests/test_daily_runner.py`. Update the test-address wording in `.github/workflows/daily-crawl.yml`, this README, and `docs/self-hosted-runner.md`. Test mode ignores `recipients.txt`; changing only that file does not redirect test emails.
5. **Preview before scheduling.** Run `JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh`, then open `out/jobs_message_latest.html`. This may call the OpenAI API and updates local crawl state, but sends no email. After setting your fork's test mailbox, `JOB_EMAIL_TEST=1 bash scripts/run_daily_email.sh` sends only to that address.
6. **Configure automation for your repository.** Add the four [Actions secrets](#github-actions-and-repository-backed-sqlite-state), then follow the [runner guide](docs/self-hosted-runner.md). Pass your repository to both installers, as shown below. The schedule remains 09:00 Asia/Seoul; choose one trigger, either the desktop timer or cron.

On your own runner machine, after completing the runner prerequisites and preview:

```bash
RUNNER_REPOSITORY=YOUR_USER/YOUR_REPO bash scripts/setup_self_hosted_runner.sh
RUNNER_REPOSITORY=YOUR_USER/YOUR_REPO bash scripts/setup_daily_schedule.sh
```

Actions checks out a separate working directory, so it does not see your development copies of the ignored files. Install the private recipient file using the [runner guide](docs/self-hosted-runner.md#private-recipients). For personalized greetings in Actions, keep the nickname file outside the runner checkout and set `JOB_NICKNAME_FILE` to its absolute path in the workflow's `env` section.

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

To use the OpenAI API, follow [environment setup](#environment-setup), set `OPENAI_API_KEY`, and select `--agent-filter openai`.
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

These optional variables override the built-in defaults; add them to `.env` only when changing behavior:

| Variable | Default |
| --- | --- |
| `JOB_AGENT_FILTER_PROVIDER` | `openai` in the daily runner |
| `OPENAI_MODEL` | `gpt-6-luna` |
| `OPENAI_REASONING_EFFORT` | `medium` for the default model |
| `JOB_AGENT_PROMPT_FILE` | `filters/new_grad_prompt.txt` |
| `JOB_AGENT_BATCH_SIZE` | `20` |
| `JOB_AGENT_TIMEOUT` | `120` seconds |

For the optional Codex provider, `CODEX_BIN` overrides the `codex` executable and `CODEX_MODEL` overrides the CLI's model choice.

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

Generate text suitable for pasting into a messaging service.

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

Create your local nickname file from the example, then edit it with one nickname per line:

```bash
cp nicknames.example.txt nicknames.txt
```

`nicknames.txt` is ignored by Git. Blank lines and lines starting with `#` are ignored; if the file is missing or empty, the greeting is omitted.

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

Create your local recipient file from [`recipients.example.txt`](recipients.example.txt), then replace the example addresses:

```bash
cp recipients.example.txt recipients.txt
```

```txt
# One recipient per line.
me@example.com
friend@example.com
```

`recipients.txt` is ignored by Git. Use one address per line; blank lines and lines starting with `#` are ignored. Normal delivery needs at least one recipient, while `--test-email` uses the fixed test mailbox.

Follow [environment setup](#environment-setup) to obtain the three SMTP values. Sending an already-generated message does not require `OPENAI_API_KEY`.

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

The daily workflow runs on the desktop's self-hosted runner. See the
[runner setup and operation guide](docs/self-hosted-runner.md).
The guide also covers [10-minute idle sleep with an 08:30–09:30 KST awake window](docs/self-hosted-runner.md#idle-sleep-with-an-08300930-kst-awake-window),
using a desktop cron policy and a hardware wake timer.

**All test emails must go only to `froggaguldev@gmail.com`.** Manual runs default
to `dry_run=true` (no delivery) and `test_delivery=true`. To test actual delivery:

```bash
gh workflow run daily-crawl.yml --ref main -f dry_run=false -f test_delivery=true
```

Test delivery ignores `recipients.txt` and any other recipient arguments, sends
only to the test address, and prefixes the subject with `[TEST]`. The email CLI
also supports `--test-email`, and the daily shell script accepts `JOB_EMAIL_TEST=1`.
For manual production delivery, explicitly set both `dry_run=false` and
`test_delivery=false`. Scheduled production runs use the runner's private recipient file.

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

Local runs use your ignored `recipients.txt`. Actions uses `~/.config/cheerupbot/recipients.txt` under the runner user's home directory; follow the [private recipient setup](docs/self-hosted-runner.md#private-recipients). Set `JOB_RECIPIENTS_FILE` in the workflow's `env` section to use another absolute path. Updating the local file requires copying it to the runner's private location again.
The email includes **all postings collected in the current run that survive filtering and the output limits**.
Stored historical postings are not merged back into the email, and previously sent postings are not excluded merely because they were sent before.
Rerunning the workflow may send the email again. Use a dry run to validate without delivery.

Use the values from [environment setup](#environment-setup). In your GitHub repository, open **Settings → Secrets and variables → Actions → Secrets → New repository secret**. Add each name and its value, then select **Add secret**, following [GitHub's secrets guide](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

Only these four secrets are needed for the default daily email:

| Secret | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | AI filter authentication; also required for dry runs |
| `SMTP_HOST` | SMTP server |
| `SMTP_USER` | SMTP login account |
| `SMTP_PASSWORD` | SMTP password or app password |

`SMTP_PORT` and `SMTP_FROM` are optional secrets: omit them to use `587` with STARTTLS and `SMTP_USER` as the sender. A dry run only requires `OPENAI_API_KEY`. The workflow sets `JOB_ENV_FILE=/dev/null` and does not load `.env` from the runner.

Do not store a GitHub PAT as a workflow secret. State pushes use the per-run `GITHUB_TOKEN` with `contents: write` permission.
If repository/organization policies or branch rules block direct writes to `crawl-state`, pushes fail; allow automation to write to that data branch.

- **Manual validation:** Actions → Daily crawl and email → Run workflow. The default `dry_run=true` performs collection, filtering, and SQL persistence without sending email. HTML previews and reports are available in the artifact.
- **Live delivery:** explicitly disable both `dry_run` and `test_delivery` to send a manual run to all configured recipients. The desktop timer requests this production workflow daily at **09:00 Korea time**; install it with `bash scripts/setup_daily_schedule.sh`. Email is sent after collection, filtering, and the SQL push finish. GitHub API and runner availability still affect workflow startup. See the [runner operation guide](docs/self-hosted-runner.md).
- **Scheduling:** the installer sets `JOB_SCHEDULE_ENABLED=false` to disable GitHub's native `0 0 * * *` UTC fallback, which can arrive hours late. Pause the desktop trigger with `systemctl --user disable --now cheerupbot-daily.timer`. The repository variable only controls the GitHub fallback; manual and desktop-triggered runs remain available.
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
The restore directory must be empty. GitHub Actions uses a separate directory under the runner temporary path for each run ID and attempt number.
Choose one trigger: the desktop timer, server cron, or GitHub's native schedule. Enabling more than one can result in duplicate emails.

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

1. Fill the four values in `.env` using [environment setup](#environment-setup).
2. Copy `recipients.example.txt` to `recipients.txt` and replace the example addresses.
3. Test with `JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh`.
4. Check cron with `crontab -l`.

### TODO

- Ensure links are clickable in Naver Mail.
