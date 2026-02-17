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
- 운영 시에는 실패 로그, 재시도, 스케줄링(cron 등)을 함께 붙이는 것을 권장합니다.

### 이메일 전송(추천)
`email_sender.py`는 `jobs_message.txt`를 지정한 이메일 목록으로 발송합니다.
메일은 `text/plain + text/html` 멀티파트로 보내서 링크가 클릭 가능하도록 처리합니다.

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

### 매일 자동 실행(cron)
`scripts/run_daily_email.sh`가 아래 순서로 실행합니다.
1. 채용 수집/필터 -> `out/jobs_*.json`
2. 메시지 생성 -> `out/jobs_message_*.txt`
3. 이메일 발송

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
