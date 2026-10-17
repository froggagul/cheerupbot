## alarmbot

사람인, 잡코리아, 원티드, 링커리어, 자소설닷컴, 캐치에서 키워드 기반 채용공고를 수집하는 스크립트입니다.

### 지원 사이트 ID
- `saramin`
- `jobkorea`
- `wanted`
- `linkareer`
- `jasoseol`
- `catch`

### 실행
```bash
python main.py \
  --sites saramin,jobkorea,wanted,linkareer,jasoseol,catch \
  --keywords HR,HRD \
  --limit-per-site 20 \
  --output jobs.json
```

기존에 저장한 JSON만 다시 필터링하려면 `--input`을 사용할 수 있습니다.

```bash
python main.py \
  --input jobs_debug.json \
  --output jobs_filtered.json
```

### JS 렌더링 모드
`wanted`, `linkareer`, `jasoseol`, `catch`는 정적 HTML만으로는 부족할 수 있어 `--render-js`를 지원합니다.

```bash
python main.py \
  --sites wanted,linkareer,jasoseol,catch \
  --keywords HR,HRD \
  --limit-per-site 20 \
  --render-js \
  --debug \
  --output jobs_debug.json
```

Playwright 브라우저 설치가 필요합니다.

```bash
python -m playwright install chromium
```

### 원본 응답 덤프
실제 응답 본문을 저장해 사이트별 파서를 튜닝할 수 있습니다.

```bash
python main.py \
  --sites wanted,catch \
  --keywords HR,HRD \
  --render-js \
  --debug \
  --dump-dir dumps \
  --output jobs_debug.json
```

`--dump-dir`를 주면 `html/json` 원문과 최종 URL, 그리고 실행 로그(`crawl.log`)가 저장됩니다.

출력 파일을 지정하지 않으면 표준 출력(JSON)으로 출력됩니다.

### 수집 범위와 진단

`--limit-per-site`는 **필터 적용 후** 사이트/키워드별 최대 공고 수입니다.
필터(DSL, 플러그인, 에이전트)가 있으면 기본적으로 이 값의 3배까지 후보를 수집합니다.
필터가 없으면 지정한 수만큼 수집합니다. `--candidate-limit-per-site`로 후보 수를
직접 지정할 수 있습니다. 페이지/요청 예산이나 실제 검색 결과에 따라 최종 수는 적을 수 있습니다.
`--input`으로 기존 JSON을 처리할 때는 수집 관련 제한을 적용하지 않습니다.

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

- `--max-pages`: 사이트/키워드별 검색 요청 예산(기본 3). 원티드/캐치는 API 페이지를,
  HTML 사이트는 확인 가능한 다음 페이지 링크를 순회합니다. 자소설 API는 전체 결과를 한 번에 요청합니다.
- `--max-requests`: 사이트/키워드별 검색·상세 요청 합계(기본 20). HTTP 재시도와 브라우저 내부 리소스 요청은 제외합니다.
- `--max-detail-requests`: 원티드 상세 정보 보완 요청 수(기본 10, `0`이면 비활성화).
  같은 실행에서 이미 읽은 상세 정보는 재사용합니다. 원문에 없는 마감일은 빈 값으로 유지합니다.
- 공고 ID로 중복을 합치며 `matched_keywords`에 검색 키워드를 보존합니다.
  원문에서 얻은 역할·경력·고용형태를 `description`, `experience`, `employment_type`으로 전달합니다.
- `--crawl-report`: 검색별 `success`, `empty`, `partial`, `failed` 상태와 요청 수,
  수집 수, 중단 이유, 오류를 저장합니다. 예산 때문에 중단된 경우 `stop_reason`을 확인하세요.
- 연결 오류 및 HTTP 429/500/502/503/504는 최대 2회 재시도합니다. 같은 호스트의 일반 요청 사이에는
  최소 0.25초 간격을 두며 재시도에는 backoff와 `Retry-After`를 적용합니다.
- 실패는 `--debug` 없이도 stderr에 표시합니다. 모든 검색이 실패하면 비정상 종료하여
  기존 출력 파일을 덮어쓰지 않고, 매일 실행 스크립트도 발송 전에 중단합니다.

매일 실행에서도 `JOB_CANDIDATE_LIMIT_PER_SITE`, `JOB_MAX_PAGES`, `JOB_MAX_REQUESTS`,
`JOB_MAX_DETAIL_REQUESTS`를 사용할 수 있으며 수집 진단은 `out/crawl_report_*.json`에 저장됩니다.

회귀 테스트: `.venv/bin/python -m unittest discover -s tests -v`
`tests/fixtures/crawler`는 2026-10-14에 확인한 공개 응답에서 테스트에 필요한 필드/카드만 추린 자료입니다.

### 범용 필터(JSON DSL)
신입/경력 전용 로직을 코드에 박지 않고, JSON 규칙으로 필터를 선언할 수 있습니다.

- 논리 노드: `all`, `any`, `not`
- 조건 노드: `field` 또는 `fields` + `op` + `value`
- 전체 텍스트 대상: `field: "_text"`

주요 연산자:
- `contains`, `not_contains`
- `eq`, `ne`, `in`, `not_in`
- `regex`, `not_regex`
- `exists`, `is_open`
- `gt`, `gte`, `lt`, `lte`
- `date_before`, `date_on_or_before`, `date_after`, `date_on_or_after`

DSL로 표현하기 어려운 경우 Python 플러그인도 연결할 수 있습니다.
- `--filter-plugin filters/plugin_example.py:keep_non_manager_and_seoul`
- 여러 개를 주면 AND 체인으로 모두 적용됩니다.

실행 예시:
```bash
python main.py \
  --input jobs_debug.json \
  --filter-file filters/new_grad.json \
  --output jobs_new_grad.json
```

인라인 JSON으로도 가능:
```bash
python main.py \
  --input jobs_debug.json \
  --filter-json '{"field":"site","op":"in","value":["saramin","wanted"]}' \
  --output jobs_site_filtered.json
```

DSL + 플러그인 동시 적용:
```bash
python main.py \
  --input jobs_debug.json \
  --filter-file filters/new_grad.json \
  --filter-plugin filters/plugin_example.py:keep_non_manager_and_seoul \
  --output jobs_custom_filtered.json
```

### 에이전트 필터 (OpenAI API / Codex)

`AgentJobFilter`가 자연어 기준을 읽고 공고마다 포함 여부와 한국어 사유를 반환합니다.
`OpenAIClient`는 Responses API를, `CodexClient`는 현재 컴퓨터의 Codex CLI 로그인을
사용합니다. 기본 실행은 기존과 동일하며 `--agent-filter`로 활성화합니다.

```bash
uv sync

# 현재 데스크톱에서 Codex 로그인 상태 확인 (미로그인 시 codex login)
codex login status
uv run main.py \
  --input jobs_debug.json \
  --agent-filter codex \
  --agent-prompt-file filters/new_grad_prompt.txt \
  --agent-report out/agent_decisions.json \
  --output out/jobs_agent.json
```

`out/` 등 출력 디렉터리는 실행 전에 생성하세요 (`mkdir -p out`). Codex 방식은
`--ignore-user-config`, `--ephemeral`, `--output-schema`를 지원하는 CLI가 필요합니다.
로그인은 실행하는 컴퓨터/사용자별로 필요하며 데스크톱 로그인이 EC2에 적용되지는 않습니다.

OpenAI API를 사용하려면 `.env.example`을 `.env`로 복사하고 `OPENAI_API_KEY`를
설정한 뒤 `--agent-filter openai`를 사용합니다. API 키는 ChatGPT 로그인과 별개입니다.
다른 설정 파일은 `--env-file .env.ec2`처럼 선택합니다. 환경에 이미 설정된 값이
파일보다 우선하며 `--no-dotenv`로 파일 로딩을 끌 수 있습니다.

```bash
uv run main.py \
  --sites saramin,catch --keywords HR,HRD --limit-per-site 10 \
  --agent-filter openai \
  --agent-prompt "HR/인사 관련 신입 및 인턴만 포함. 경력직 전용, 총무, 인재풀은 제외." \
  --agent-report out/agent_decisions.json \
  --output out/jobs_agent.json
```

- 기본 기준: `filters/new_grad_prompt.txt`. 기존 신입/인턴/계약직 기준에 직무 문맥을
  반영하고, 무관한 교육/문화 공고와 명백히 마감된 공고를 제외합니다. 경력무관,
  신입/경력 동시 모집, 단순히 `manager` 단어가 들어간 공고는 문맥으로 판단합니다.
  정보가 부족하면 제외하고 사유를 기록합니다. 이 파일을 수정하면 기준을 바꿀 수 있습니다.
- `--agent-prompt`와 `--agent-prompt-file`은 함께 사용할 수 없습니다.
- `--agent-model`로 모델을 지정할 수 있습니다. OpenAI 기본값은 `OPENAI_MODEL`
  또는 `gpt-6-luna`, Codex는 `CODEX_MODEL` 또는 CLI 기본 모델입니다.
  Luna의 기본 추론 강도는 `medium`이며 `OPENAI_REASONING_EFFORT`로 바꿀 수 있습니다.
  다른 모델에 추론 강도를 지정할 때는 해당 모델의 지원 값을 사용하세요.
- `--agent-batch-size` (기본 20, 최대 100), `--agent-timeout` (기본 120초)을 지원합니다.
  OpenAI는 일시적 API 오류를 최대 두 번 재시도하므로 총 대기 시간이 더 길 수 있습니다.
- `--filter-file`, `--filter-json`, `--filter-plugin`을 같이 지정하면 먼저 적용됩니다.
  에이전트가 모든 수집 공고를 검토하게 하려면 기존 필터 옵션을 생략하세요.
- 반환된 JSON은 원래 공고 필드/순서를 유지합니다. 별도 `--agent-report`에는
  검토한 모든 공고의 `id`, `keep`, `reason`, 제목, 링크를 기록합니다.
- 공고 내용은 신뢰하지 않는 입력으로 전달합니다. OpenAI에는 도구를 제공하지 않고,
  Codex는 임시 디렉터리에서 읽기 전용으로 실행하며 셸 도구/앱/웹 검색을 비활성화합니다.
  SMTP/AWS 환경변수는 Codex 자식 프로세스로 전달하지 않습니다.
- 에이전트는 수집된 제목/회사/지역/마감일과 입력에 있는 설명 등만 봅니다. 상세 페이지를
  추가로 방문하지 않으며 긴 필드는 6,000자로 제한합니다. 잘못된 응답, 누락/중복 ID,
  인증 실패, 시간 초과 시 실행을 실패 처리합니다. 자동으로 기존 규칙으로 대체하지 않습니다.

매일 실행 스크립트도 `.env`를 읽으며 기본 필터는 OpenAI GPT-6 Luna / medium입니다.
`JOB_ENV_FILE=.env.ec2`로 다른 설정 파일을 선택할 수 있습니다.
`JOB_AGENT_FILTER_PROVIDER=none`이면 기존 정규식 필터를 사용하고, `codex`이면 Codex를 사용합니다.
`JOB_FILTER_FILE`을 명시하면 해당 필터를 먼저 적용합니다.
메시지 생성 단계에서 에이전트를 다시 호출하지 않으며 필터 실패 시 발송도 중단합니다.

```dotenv
JOB_AGENT_FILTER_PROVIDER=openai
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=medium
JOB_AGENT_PROMPT_FILE=filters/new_grad_prompt.txt
JOB_AGENT_BATCH_SIZE=20
JOB_AGENT_TIMEOUT=120
```

실제 이메일을 보내지 않는 로컬 전체 흐름 테스트:

```bash
JOB_AGENT_FILTER_PROVIDER=codex JOB_INPUT_JSON=jobs_debug.json \
  JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh

# 네트워크/실제 모델 호출 없이 단위 및 스크립트 통합 테스트
uv run python -m unittest discover -s tests -v
```

API/CLI 출력 방식 참고:
[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses),
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

### 메시지 텍스트 포맷 출력
카카오톡/슬랙 등으로 보내기 전에 붙여넣기용 텍스트를 생성할 수 있습니다.

```bash
python main.py \
  --input jobs_custom_filtered.json \
  --output-format message \
  --message-title "HRD 채용 요약" \
  --message-max-items 0 \
  --output jobs_message.txt
```

`--output`을 생략하면 터미널에 바로 출력됩니다.

별명 랜덤 인사문도 지원합니다. 기본값은 `nicknames.txt`에서 한 개를 랜덤 선택해
`안녕 {nickname}야! 오늘의 채용 요약이야.` 형식으로 시작합니다.

```bash
python main.py \
  --input jobs_custom_filtered.json \
  --output-format message \
  --message-nickname-file nicknames.txt \
  --message-greeting-template "안녕 {nickname}야! 오늘의 채용 요약이야." \
  --output jobs_message.txt
```

인사문을 끄려면 `--message-greeting-template ""`를 사용하세요.

### 참고
- 일부 사이트는 동적 렌더링/봇 차단 정책으로 인해 수집 결과가 제한될 수 있습니다.
- 현재 파서는 셀렉터 기반 + fallback(JSON-LD/inline JSON) 방식이라 사이트 DOM 변경 시 보정이 필요합니다.
- 실패 진단과 제한된 재시도는 내장되어 있습니다. 운영 시에는 수집 보고서와 cron 로그를 함께 확인하세요.

### 이메일 전송(추천)
`email_sender.py`는 `jobs_message.txt`를 지정한 이메일 목록으로 발송합니다.
메일은 `text/plain + text/html` 멀티파트로 보내서 링크가 클릭 가능하도록 처리합니다.

HTML 메일은 회사명과 공고 제목을 구분한 카드, 마감일/D-day 표시, 지역·경력·고용형태,
한국어 사이트 이름과 `공고 보기` 링크를 제공합니다. 생성 시점 기준 3일 이내 마감은
별도 색상과 문구로 표시합니다. 긴 원문 URL은 버튼으로 표시하고, 텍스트 버전에는
복사 가능한 URL을 유지합니다. HRD 우선/마감일 순 정렬과 표시 개수는 두 버전이 같습니다.

텍스트와 HTML을 함께 생성하고 브라우저에서 미리 보기:

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

`out/email_preview.html`을 브라우저에서 여세요. `--preview-html`은 미리보기만 저장하고
종료하며 SMTP 설정이나 수신자가 필요 없습니다. 발송 시에도 `--html-file`을 함께
전달하면 카드 형식이 적용됩니다. 생략하면 기존 텍스트를 HTML로 변환합니다.

수신자 목록 파일(`recipients.txt`) 예시:
```txt
# 한 줄에 한 명
me@example.com
friend@example.com
```

환경 변수 예시:
```bash
cp .env.example .env
# .env 파일에서 SMTP_* 값 수정
```

드라이런:
```bash
python email_sender.py \
  --message-file jobs_message.txt \
  --subject "[알림] 오늘의 채용 요약" \
  --recipients-file recipients.txt \
  --dry-run
```

실제 발송:
```bash
python email_sender.py \
  --message-file jobs_message.txt \
  --subject "[알림] 오늘의 채용 요약" \
  --recipients-file recipients.txt
```

옵션:
- `--to user@example.com` (반복 가능, 파일과 합쳐서 사용)
- `--send-mode single|individual` (기본 `single`: BCC 1회 발송)
- `--use-ssl` (보통 465 포트), 기본은 STARTTLS(587)
- `--env-file .env` (기본값), `--no-dotenv` (비활성화)
- `--html-file jobs_message.html` (카드 형식 HTML 본문)
- `--preview-html preview.html` (HTML만 저장 후 종료, 발송 안 함)

### GitHub Actions + 저장소에 보관하는 SQLite 상태

별도 DB 서버 없이 실행할 수 있습니다. 실행 중에는 Python 내장 SQLite를 메모리에서
사용하고, 실행 사이에는 `crawl-state` 브랜치의 **`state.sql` 텍스트 파일**을 보관합니다.
코드와 데이터는 서로 다른 브랜치에 저장되며 데이터 브랜치는 첫 실행 때 자동으로 생성됩니다.

`.github/workflows/daily-crawl.yml` 실행 순서:

1. 코드와 잠금 파일(`uv.lock`) 기준 의존성을 설치합니다.
2. 원격 `crawl-state`의 최신 SQL을 읽고 검증한 뒤 SQLite로 복원합니다.
3. 수집한 공고를 `(site, canonical URL)` 기준으로 추가/갱신합니다.
4. 기존 필터와 AI 필터를 적용하고 일일 HTML/텍스트 이메일을 생성합니다.
5. SQL을 내보내고 내용이 바뀌었을 때만 `crawl-state`에 커밋/푸시합니다.
6. 앞 단계가 모두 성공한 경우에만 준비한 이메일을 SMTP로 발송합니다. Dry run은 이 단계를 건너뜁니다.

기본 수신자는 현재 **`froggaguldev@gmail.com`**, **`ohansae@naver.com`** 두 명입니다. 변경은 `recipients.txt`에서 합니다.
이메일은 기존과 같이 **오늘 수집하여 필터를 통과한 전체 공고**를 포함합니다.
저장된 과거 공고를 이메일에 다시 합치거나, 이미 보냈다는 이유로 공고를 제외하지 않습니다.
재실행하면 이메일이 다시 발송될 수 있습니다. 발송 없이 검증하려면 dry run을 사용하세요.

GitHub 저장소의 **Settings → Secrets and variables → Actions**에 다음 repository secrets를 설정합니다:

| Secret | 용도 |
| --- | --- |
| `OPENAI_API_KEY` | AI 필터 인증 (dry run에도 필요) |
| `SMTP_HOST` | SMTP 서버 |
| `SMTP_PORT` | 기본 `587`, STARTTLS |
| `SMTP_USER` | SMTP 로그인 계정 |
| `SMTP_PASSWORD` | SMTP 비밀번호 / 앱 비밀번호 |
| `SMTP_FROM` | 발신자 주소; 생략하면 `SMTP_USER` 사용 |

GitHub PAT는 workflow secret으로 저장하지 않습니다. 상태 푸시는 실행마다 발급되는
`GITHUB_TOKEN`의 `contents: write` 권한을 사용합니다. 저장소/조직 정책이나 branch rules가
`crawl-state` 직접 쓰기를 막으면 푸시가 실패하므로 해당 데이터 브랜치에 자동화 쓰기를 허용해야 합니다.

- **수동 검증:** Actions → Daily crawl and email → Run workflow. 기본 `dry_run=true`는
  이메일을 보내지 않고 수집/필터/SQL 저장까지 수행합니다. HTML 미리보기와 보고서는 artifact에서 확인합니다.
- **실제 발송:** 수동 실행에서 `dry_run`을 끕니다. 예약 실행은 매일 한국 시간 약 09:00에 시작합니다
  (`0 0 * * *` UTC). 수집/필터/SQL 푸시가 끝난 뒤 발송하므로 이메일은 그 이후 도착합니다.
  GitHub 예약 작업은 지연/누락될 수 있으며 정확한 시각을 보장하지 않습니다.
  예약 실행을 활성화하려면 이 workflow 파일이 기본 브랜치(`main`)에 있어야 합니다.
- **예약 중지:** repository variable `JOB_SCHEDULE_ENABLED=false`로 설정합니다. 수동 실행은 가능합니다.
- **모델 설정:** repository variables `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`로 변경합니다.
  기본은 `gpt-6-luna`, `medium`입니다.
- **겹치는 실행:** 고정 concurrency group으로 전체 restore→crawl→push 과정을 직렬화합니다.
  다른 실행이 끝날 때까지 기다리며 실행 중인 작업을 취소하지 않습니다.

SQL에는 정규화된 공고, 내용 해시, 최초/최근 발견 시각, 내용 변경 시각과 공고별 최신 AI 판단이 저장됩니다.
API 키, SMTP 설정, 수신자 목록, 원문 HTML은 SQL에 넣지 않습니다. 날짜/제공자/모델/추론 설정/프롬프트와
AI 입력 필드가 모두 같을 때만 기존 판단을 재사용합니다. `collected_at`도 AI 입력이므로 새 수집 시각이나
새 날짜는 재평가를 유발할 수 있습니다.

상태를 사용하는 실행은 수집 결과의 `last_seen`을 먼저 갱신한 뒤 아래 기준으로 자동 정리합니다.

- 정규화된 마감일이 **오늘(Asia/Seoul)보다 이전**인 공고를 삭제합니다. 오늘 마감인 공고는 유지합니다.
- **최근 발견일로부터 7일 이상** 지난 공고를 삭제합니다. 서울 달력 날짜 기준이며,
  한 번 누락되었다고 바로 삭제하지 않습니다. 다시 수집되면 7일 계산이 새로 시작됩니다.
- 삭제된 공고의 AI 판단 캐시도 함께 삭제합니다. 마감 공고는 AI 필터와 이메일에서도 제외합니다.
- `OPEN`, 빈 값, 해석할 수 없는 마감일은 날짜만으로 삭제하지 않으며 7일 미발견 기준을 적용합니다.
  계속 수집되고 아직 마감되지 않은 공고는 최초 발견일이 오래되어도 유지합니다.

정리는 `--state-file`이 있는 수집/JSON 입력 실행에서 적용되며 기존 SQL 스냅샷에도 바로 적용됩니다.
일부 사이트 실패나 실행 누락도 미발견 기간에 포함됩니다. 모든 검색이 실패하여 수집이 중단되면
그 실행에서는 정리하지 않고, 다음 수집 완료 시 경과한 기간을 반영합니다.
실행 로그의 `[state-cleanup]`에 마감 삭제 수, 7일 미발견 삭제 수, 남은 공고 수를 기록합니다.

필터가 실패해도 이미 수집된 SQL은 저장 단계에서 푸시하며, 이메일 발송 전에 저장을 완료합니다. 원격 읽기 실패나
손상된 SQL은 빈 DB로 대체하지 않고 실행을 중단합니다. 푸시는 force를 사용하지 않으며 다른 작성자와
충돌하면 실패합니다. 이 경우 artifact의 SQL을 확인할 수 있습니다. SQL 푸시 실패 시 이메일을 발송하지 않습니다.
프로세스 강제 종료 시에는 마지막 SQL 저장 이후의 진행을 잃을 수 있습니다.
Artifact 보관 기간은 14일이며 영구 상태는 Git 브랜치입니다.
파일에서 오래된 공고를 지워도 Git 이력에는 남으므로 대규모 원문/첨부파일 저장 용도로 사용하지 마세요.

로컬에서도 같은 파일을 사용할 수 있습니다:

```bash
# 기본 경로는 out/state.sql이며 이메일 발송 없이도 수집 상태는 갱신됩니다.
JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh

# 직접 실행할 때 명시적으로 상태 활성화
.venv/bin/python main.py --sites wanted --keywords HR \
  --state-file out/state.sql --output out/jobs.json

# 저장소 상태를 별도 디렉터리로 복원/갱신/푸시 (Git 쓰기 인증 필요)
.venv/bin/python scripts/sync_crawl_state.py restore --directory .crawl-state
JOB_STATE_FILE=.crawl-state/state.sql JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh
.venv/bin/python scripts/sync_crawl_state.py save --directory .crawl-state
```

`JOB_STATE_FILE`로 경로를 변경하고 빈 값(`JOB_STATE_FILE=`)으로 영속 상태를 끌 수 있습니다.
restore 디렉터리는 비어 있어야 합니다. GitHub Actions는 매번 새 runner를 사용하므로 자동으로 충족합니다.
서버 cron과 GitHub Actions를 동시에 예약하면 이메일이 중복될 수 있으므로 실행 위치는 하나만 선택하세요.

검증:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

### 매일 자동 실행(cron)
`scripts/run_daily_email.sh`가 아래 순서로 실행합니다.
1. 채용 수집/필터 -> `out/jobs_*.json`
2. 텍스트/HTML 메시지 생성 -> `out/jobs_message_*.txt`, `out/jobs_message_*.html`
3. 이메일 발송

최신 HTML은 `out/jobs_message_latest.html`에서 확인할 수 있습니다.

테스트(기존 JSON 사용 + 이메일 드라이런):
```bash
JOB_INPUT_JSON=jobs_debug.json JOB_EMAIL_DRY_RUN=1 ./scripts/run_daily_email.sh
```

크론 설치(매일 09:00, 서버 로컬 시간대 기준):
```bash
./scripts/install_cron.sh "0 9 * * *"
```

주요 환경변수:
- `JOB_KEYWORDS` (기본 `HR,인사,조직문화,문화,교육,HRD`)
- `JOB_FILTER_FILE` (기본 `filters/new_grad.json`)
- `JOB_RECIPIENTS_FILE` (기본 `recipients.txt`)
- `JOB_EMAIL_SUBJECT` (기본 `[알림] 오늘의 채용 요약`)
- `JOB_EMAIL_DRY_RUN` (`1`이면 발송 대신 dry-run)
- `JOB_INPUT_JSON` (설정 시 크롤링 생략, 해당 JSON으로 실행)
- `JOB_ENV_FILE` (기본 `.env`)
- `JOB_MESSAGE_MAX_ITEMS` (기본 `0`, 전체 출력)

### 가장 간단한 배포(우분투 1대)
서버에서 레포를 클론한 뒤 아래 한 줄로 세팅합니다.

```bash
bash scripts/bootstrap_ubuntu.sh --cron "0 9 * * *"
```

옵션:
- `--with-playwright` : JS 렌더링 크롤링까지 사용할 때
- `--env-file .env` : 다른 env 파일명 사용
- `--dry-run` : 설치 명령 미실행 검증

설치 후 확인:
1. `.env`에 `SMTP_*` 값 입력
2. `recipients.txt` 수신자 입력
3. 테스트: `JOB_EMAIL_DRY_RUN=1 bash scripts/run_daily_email.sh`
4. 크론 확인: `crontab -l`

### 카카오톡 친구 전송(테스트 앱 멤버 범위)
`kakao_sender.py`는 `jobs_message.txt` 같은 텍스트 파일을 카카오톡으로 전송합니다.

필수:
- 카카오 앱 `REST API 키`
- `refresh_token` (권장) 또는 `access_token`
- 수신자 UUID 또는 닉네임

토큰 파일 예시(`kakao_tokens.json`):
```json
{
  "refresh_token": "YOUR_REFRESH_TOKEN"
}
```

친구 목록 확인(닉네임/UUID 조회):
```bash
python kakao_sender.py \
  --rest-api-key "$KAKAO_REST_API_KEY" \
  --token-file kakao_tokens.json \
  --list-friends
```

드라이런(실제 발송 없이 페이로드 확인):
```bash
python kakao_sender.py \
  --rest-api-key "$KAKAO_REST_API_KEY" \
  --token-file kakao_tokens.json \
  --message-file jobs_message.txt \
  --receiver-name "홍길동" \
  --dry-run
```

실제 발송:
```bash
python kakao_sender.py \
  --rest-api-key "$KAKAO_REST_API_KEY" \
  --token-file kakao_tokens.json \
  --message-file jobs_message.txt \
  --receiver-name "홍길동"
```

메시지가 길면 `--max-text-length` 기준으로 자동 분할 전송합니다.
`.env`를 쓸 때는 `KAKAO_*` 값을 넣고 `--rest-api-key` 인자를 생략해도 됩니다.


todo
* 링크 네이버 메일에서 클릭 가능하게
