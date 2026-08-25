# AGENTS.md — data-service

> 공통 규칙 정본: `../quant-contract/AGENTS.md`
> 이 파일에는 **이 모듈에만 해당하는 것**만 적는다. 공통 규칙을 복사하지 않는다.

## 이 모듈이 무엇인가

> **흩어져 있는 국내 투자 정보를 한 곳에 모아 두는 수집·보관·조회 서비스.**
> 시세·통계는 정형 API 로, 공시·뉴스·커뮤니티는 수집으로 모으고, **기업과 산업**을 축으로 되찾는다.

⚠️ **이 문장은 네 곳이 공유한다** — `README.md` 첫머리 · 랜딩(`static/pages/dashboard.html`) ·
`/guide`(`static/pages/index.html`) · OpenAPI 개요(`app/core/api_docs.py`).
**넷이 어긋나면 그것이 곧 결함이다.** 정체성은 한 군데서만 바뀐다 (ADR-DS-0012 §1).

수집 대상은 **여섯 갈래**다 — 공시 · 정기보고서 · 뉴스 · 커뮤니티 · 동영상 · 시세/통계.
착수 순서는 **공시·보고서 → 뉴스 → 커뮤니티·동영상**이고, 인증키와 클라이언트가 이미
있고 이용약관 확인이 필요 없는 쪽부터다. ⚠️ **커뮤니티·동영상은 `robots.txt` 와 이용약관을
읽고 그 결과를 ADR 로 남긴 뒤에** 착수한다 — 읽지 않고 짜기 시작하지 않는다.

⚠️ **본문은 담지 않는다.** 링크·제목·출처·발행일·내 메모까지다 (ADR-DS-0008 · 0012 §3).
예외는 DART 공시·보고서 원문 하나뿐이다(공공데이터).

⚠️ **수집 저장은 Postgres 전환(ADR-DS-0011 S3~S5) 뒤에 시작한다.** 지금 SQLite 에 표를
새로 파면 나중에 두 번 옮긴다. **화면과 문서는 그 순서를 기다리지 않는다** — 이미 섰다.

⚠️ **화면은 자료 종류로 가른다. 원천별이 아니다** (ADR-DS-0012 §4). 사이드바 정본은
`static/assets/shell.js` 의 `NAV` 하나이고, 아직 없는 화면은 `soon: true` 로 **메뉴에 걸어 둔다** —
앞으로 무엇이 생기는지가 정보구조의 일부다. 감추지 않는다.

## 이 모듈의 경계

**데이터를 가져오는 것까지.** 리포트를 만드는 것은 `research-service` 다 (ADR-DS-0007).

⚠️ 다만 **경계만 그었을 뿐 아직 옮기지 않았다.** `app/services/research/` 24개 파일은
전부 이 레포에 남아 있고 배포본 화면이 그것을 쓴다. **검증 없이 지우지 않는다.**
경계표 정본은 `docs/decisions/0007-research-boundary.md` 에 있다.

**새 코드는 이 경계를 넘지 않는다** — `stays` 쪽 파일이 `goes` 쪽을 새로 import 하지 않는다.

## 검증 명령

- 전체 검증: `invoke check` (preflight → ruff check → pytest → uv export → docker build)
  - `preflight` 는 `.venv` 가 `pyproject.toml` 을 따라잡았는지만 본다. 의존성을 더한 날
    이것이 없으면 pytest 가 **원인에서 먼 곳**(conftest 의 앱 로딩)에서 죽는다.
  - ⚠️ `export` 가 `build` 보다 앞인 것이 뜻을 가진다. `invoke build` 단독은 의존성을 고친 날
    낡은 `uv.lock` 으로 하드 실패한다(Dockerfile 이 `uv sync --locked`).
- 문서 검증: `invoke docs-check` (markdownlint → 링크 → openapi export)
  - ✅ **2026-08-23 에 초록이 됐다.** 그전에는 설정 파일이 없어 기본값(영문 80자·compact 표)으로
    돌았고 **6,500건 넘게** 걸려 게이트가 "빨간불로 고정"돼 있었다. 설정은
    `.markdownlint-cli2.jsonc` 다 — 끈 규칙마다 **왜 이 레포와 충돌하는지**를 그 파일에 적어 두었다.
  - ⚠️ **`lychee` 는 이 환경에 없다.** 없으면 `tasks.py` 의 `_check_local_links()` 폴백이
    **상대 경로 링크가 실재하는지만** 본다. 앵커(`#절`)와 외부 URL 은 **여전히 안 본다** —
    그 사실을 실행할 때마다 화면에 밝힌다.
- 미러 훅 설치: `invoke hooks` (clone 마다 한 번 · ADR-DS-0013)
- **자료 갱신: `invoke refresh`** (ADR-DS-0016) — 수집 → 축약본 → 스냅샷 → 마스터 → Postgres.
  ⚠️ **검증이 아니라 운영 명령이라 `invoke check` 에 묶지 않았다.** 검증 명령이 외부 API 를
  부르고 파일을 고치면 그 명령을 더는 신뢰할 수 없다 (`invoke hooks` 와 같은 이유).
  - **순서가 뜻을 가진다** — 2~4단계가 전부 `krx_cache.db` 를 읽으므로 수집이 먼저다.
    그리고 **`fetch_krx.py` 만 돌리면 로컬만 최신이 되고 배포본은 그대로 낡는다** —
    배포본이 읽는 것은 커밋되는 `market_snapshot.json.gz`·`stock_master.json` 쪽이다.
    이 어긋남은 오류로 뜨지 않고 **날짜로만** 드러나서, 실제로 24일치가 조용히 밀렸다.
  - **커밋하지 않는다.** push 가 곧 Vercel 배포라 그 시점은 사람이 정한다.
  - `--check` 는 아무것도 바꾸지 않고 낡음만 잰다. `--skip-pg` 는 DB 를 안 띄웠을 때.
    DB 에 못 붙으면 **알리고 건너뛴다** — 다만 그러면 두 저장소가 갈린다.
  - ⚠️ **호스트 셸에서는 `DATABASE_URL` 이 `@db:5432` 로 떨어지면 안 된다.** 그것은 compose
    네트워크 안쪽 이름이라 `socket.gaierror` 로 죽는데, 스택이 asyncpg 안에서 40줄 나와서
    "DB 가 안 떴나" 로 보인다. `refresh` 는 `localhost` 로 바꿔 준다.
  - ⚠️ **실행처는 아직 사람이다.** CI 를 필수 경로에 두지 않는 것이 이 레포 방침이라
    스케줄 자동화는 하지 않았다. 다음 후보는 OS 스케줄러(장 마감 후)다.
- **CI가 아니라 이 명령이 정본이다.** CI는 이 명령을 호출만 한다.
- ⚠️ **이 모듈은 `ruff format --check`를 넣지 않는다** (ADR-DS-0005). 공통 규칙과 한 단계 다르다.
  적용하면 93파일 15,442줄이 바뀌는데 거의 전부가 인라인 주석 정렬을 뭉개는 변경이다.
  포맷이 필요하면 `invoke format`으로 의도적으로만 돌리고 단독 커밋으로 남긴다.

## 금지

### 승격된 것 — 더 이상 legacy 가 아니다 (2026-08-16)

이 둘은 `upstream`(강사님 원본) 원격이 없어 3-way merge 계보 부담이 없다.
그래서 **복사하지 않고 폴더를 개명해 그 자리에서 발전시킨다.** 히스토리가 이어진다.

| 기호 | 이전 이름 | 현재 |
|---|---|---|
| ④ | `api-test` | **`projects/data-service`** |
| ① | `investment-portfolio-site` | **`projects/investment-dashboard`** |

### 읽기 전용 — 수정 금지

코드를 옮길 때는 이 모듈 안에 **복사한 뒤** 수정한다.

| 기호 | 경로 | 스택 | 계보 |
|---|---|---|---|
| ② | `C:\Users\kik32\workspace\Research-Prompt-Engineering` | Python | **projects 밖!** |
| ③ | `projects/stock-coin-trade` | Django 5.2.17 + DRF 3.18 + HTMX/Alpine | `upstream` + `upstream-main` |
| ⑤ | `projects/docker-class` | Docker·DevSecOps 실습 | `upstream` + `upstream-main` |
| — | `projects/investment-analysis` | 강의 원본 | `upstream` |

**③⑤와 investment-analysis 는 폴더를 이동·개명하지 않는다.** 강사님 원본과 3-way merge
계보가 살아 있어서(`upstream-main` 브랜치), 파일을 대거 이동한 뒤 upstream 을 얹으면
병합이 깨진다. ③은 추가로 **체결 리팩터링의 회귀 확인 대상**이라 계속 돌아가야 한다.

각 레포의 `NOTICE.md`(원저작자 edumgt 표시)는 승격 후에도 유지한다.

- `requirements.txt` 직접 편집 (`uv export` 생성물)
- 매매 신호 생성 경로에 LLM 호출 추가 (ADR-CT-0001)
- `docs/decisions/` 번호 재사용 — 폐기 시 status만 `superseded`로 바꾼다

## 이 모듈 특화

- **엔트리포인트 정본은 `app.main:app`** (ADR-DS-0006). 루트 `main.py`는 그것을 다시 내보내는
  **shim**이라 강의 명령 `uvicorn main:app`도 그대로 돈다. 두 경로가 같은 객체인지는
  `tests/test_entrypoint.py`가 검사한다. Vercel에는 `pyproject.toml`의
  `[tool.vercel] entrypoint = "app/main.py"`로 별도 지정한다.
- **경로 기준점은 `app/core/paths.py`.** 새 코드는 `Path(__file__).parent` 로 루트를 직접
  계산하지 않는다 — 파일을 옮기는 순간 조용히 다른 곳을 가리키고, 정적 마운트는
  OpenAPI에 잡히지 않아 **서버는 정상 기동하고 화면만 404**가 된다.
- **환경 기준점은 `app/core/settings.py`** (ADR-DS-0003). `paths.py`가 경로의 기준점인 것과
  같은 자리다. 새 코드는 `os.getenv`를 직접 부르지 않고 `env()`·`app_env()`를 거친다.
  ⚠️ **인증키는 여기가 아니라 `app/core/secrets.py`다** — 그쪽은 환경변수→`.env`→`.key`로
  파일 폴백이 있고, 없으면 그 API만 503이 된다. 이쪽은 환경변수만 보고, 없으면 기본값이거나
  즉시 실패다. `DATABASE_URL`은 비밀번호를 품지만 **접속 전략의 일부라 `settings.py` 소관**이고
  로그·화면에는 `safe_url()`로 가려서 싣는다.
  기존 세 곳(`secrets.py`·`krx_store.py`·`research/stages.py`)은 **먼저 있던 것이라 그대로 둔다** —
  `tests/test_settings.py`가 그 목록을 얼려 두어 **새로 늘어나는 것만** 잡는다.
- **DB 접속은 `APP_ENV`로 분기한다** (ADR-DS-0003).
  `vercel`: 6543 + `NullPool` + `statement_cache_size=0` + `prepared_statement_cache_size=0`
  + **준비구문 이름 유일화**(`prepared_statement_name_func`) — **넷이 한 벌이다**(rev.2).
  `local` : 5432 직결 + 정상 풀. **넷 중 하나만 빠져도 prepared statement 충돌이 난다.**
  ⚠️ **`APP_ENV` 미설정이 기본 사고 지점이다.** 정적 기본값을 `local`로 두면 배포본이 조용히
  로컬 전략으로 뜬다. 그래서 `VERCEL`·`VERCEL_ENV`를 먼저 감지하고 그 다음에 `local`로 떨어진다.
  ✅ **엔진이 섰다** — `app/core/db.py` (ADR-DS-0011 S2, 2026-08-23). 표명이 실측으로 닫혔다.
- **엔진 계층은 `app/core/db.py`다.** `settings.py`가 *무엇으로* 붙을지를 정하고 이쪽이 만든다.
  ⚠️ **캐시 두 값은 `connect_args` 안에 `int`로 둔다.** 옮길 자리가 셋처럼 보이는데 나머지 둘은
  각각 다르게 죽는다 — `create_async_engine(url, prepared_statement_cache_size=0)`은 `TypeError`
  (방언 인자가 아니라 **DBAPI 인자**), URL 쿼리 `?statement_cache_size=0`은 문자열 `"0"`로
  도착해 asyncpg가 `"0" < 0`을 시도하다 죽는다. `tests/test_db.py`가 자리와 타입을 얼려 둔다.
  ⚠️ **하나만 끄는 최적화를 하지 않는다.** "빈도만 준다"가 아니라 **조합마다 결과가 다르고
  asyncpg 버전에 달려 있다** — 0.30은 `statement_cache_size=0`이어도 이름을 붙이고 0.31은
  익명으로 바꾼다. 실측표는 ADR-DS-0003 rev.2에 있다. **`asyncpg==0.30.0` 핀을 올릴 때 다시 잰다**
  (0.31은 `manylinux_2_17` 휠이 없어 Vercel 빌드가 조용히 깨질 수 있다).
  ⚠️⚠️ **캐시를 둘 다 꺼도 이름은 계속 붙는다.** 그래서 네 번째 손잡이가 있다. 이것을 빼면
  **갓 띄운 풀러에서는 0건이다가** 잔여물이 쌓인 뒤 **첫 질의부터 전부** 실패한다
  (`DuplicatePreparedStatementError`). rev.2 초안이 그 거짓 음성을 한 번 통과했다.
  ⚠️ **접속 검증은 "한 번 초록"으로 끝내지 않는다.** `scripts/check_db_connection.py` 는
  **호출마다 엔진을 새로** 만들어(서버리스 모양) 재본다 — 엔진을 재사용하면 아무것도 못 잡는다.
  ⚠️ **이 계층은 DDL을 발행하지 않는다.** `krx_store`는 `init_db()`를 조회마다 부르지만
  (krx_store.py:146 · 호출 9곳) Postgres에서 DDL은 asyncpg 타입 캐시를 무효화한다.
  스키마는 `sql/init/*.sql`이 빈 볼륨에서 한 번 세운다.
  ✅ **이제 `app/repositories/krx_pg.py` 가 이것을 쓴다** (S4 · 2026-08-25). S2 의 경계 검사는
  그 표시로 지웠다. `tests/test_db.py` §5 의 **계층** 검사는 그대로 남는다 — 방향이 반대라서다
  (어댑터가 엔진을 부르는 것은 허용, 엔진이 위층을 부르는 것은 여전히 금지).
  ⚠️ **어댑터는 async 가 아니라 §2-1 의 동기 다리를 거친다.** 라우트 핸들러 60개가 전부
  `def` 이기 때문이다. 실측표와 기각한 대안이 그 절에 있다 (ADR-DS-0015 §1).
  실측 도구: `python3 scripts/check_db_connection.py` (읽기 전용 · 부하 검사로 판정한다)
  검증 상대: `docker compose --profile pooler up -d` (transaction 모드 풀러 · 6543).
  재현 절차 (a)(b)(c)는 ADR-DS-0011 의 "S2 재현 절차" 에 있다 — **(c)를 빼면 검증이 아니다.**
- **읽기 어댑터는 `app/repositories/krx_pg.py`이고 스위치는 `STORE_BACKEND`다**
  (ADR-DS-0015 · 전환 S4, 2026-08-25). **기본은 `sqlite` 다 — 뒤집는 것은 S5 다.**
  `settings.store_backend()` 가 정본이고 어휘 밖 값이면 **예외**다(`app_env()` 와 같은 모양).
  ⚠️ **상수가 아니라 함수인 것이 뜻을 가진다.** 모듈 상수로 두면 import 시점에 얼어붙어
  검사가 스위치를 못 뒤집는다 — `krx_store.DB_PATH` 가 실제로 그렇게 굳어 있어
  `KRX_DB_PATH` 를 `monkeypatch.setenv` 해도 아무 효과가 없다(실측).
  - **이음매는 여덟이고 한 벌이다** — `_cache_is_empty`·`latest_date`·`available_dates`·
    `snapshot_tiered`·`series_tiered`·`window`·`stats`, 그리고 `tier()` 가 따라온다.
    하나만 남기면 SQLite 를 지운 셸에서 Postgres 는 꽉 찼는데 `tier()` 만 `bundle` 을 낸다.
    `snapshot`·`series`·`universe`·`closes_matrix`·`source_tag` 는 **분기하지 않는다**(자동으로 따라온다).
  - **경계에서 셋을 되돌린다** — `change_rate` 는 `Decimal`→`float`, 날짜는 `date`→문자열
    (**`date` 키는 `YYYY-MM-DD`, `bas_dd`·`latest_date`·`stats` 는 `YYYYMMDD`**),
    `listed_shares` 는 **`ohlcv` 쪽**(`securities` 는 최신값이라 회전율이 10배 틀린다).
    ⚠️ `Decimal` 을 안 내리면 `tmp_cache.write()` 의 `json.dumps` 가 죽는데 **그 함수가 예외를
    삼킨다** — 캐시가 영원히 안 써지고 로그도 안 남는다. 가장 조용한 고장이다.
  - **`tier` 는 `db` 그대로다.** `postgres`·`pg` 를 만들지 않는다 — 어휘 검사·화면 정확비교·
    `dashboard_data.py:328-345` 의 경고 배지가 동시에 깨진다. 낱말 정리는 S7 이다.
  - **접속 실패를 빈 결과로 삼키지 않는다.** 빈 결과셋은 축약본 폴백이고 예외는 올라간다.
    삼키면 DB 장애가 "그 날짜에 자료 없음"으로 위장돼 화면이 조용히 강등된다.
  - ⚠️ **읽기 표면을 우회하는 곳이 둘 남아 있다** — `stock_service.py:144-159` 와
    `scripts/build_stock_master.py:44-54` 가 `store.connect()` 로 SQLite 에 생 SQL 을 던진다.
    스위치를 켜도 그 둘은 계속 SQLite 를 읽는다. **S5 가 갚아야 할 빚이고**,
    `tests/test_krx_pg.py` 가 목록을 얼려 두어 모르는 사이에 늘지 않게 한다.
  - 검증: 함수 24항목 중 23 일치 · HTTP 20경로 중 19 완전 일치(나머지 하나는 야후 라이브라
    저장소와 무관) · 48 동시 요청에서 Postgres 48/48. 전부 ADR-DS-0015 "검증" 절에 있다.
- **검사는 환경을 씻고 돈다** — `tests/conftest.py` 의 autouse `isolate_env` (ADR-DS-0015 §5).
  위험한 여섯을 지우고 `DATABASE_URL` 을 **붙을 수 없는 주소**(`127.0.0.1:1`)로 덮는다.
  ⚠️ 지우기만 하면 `local` 로 떨어져 기본값 `@db:5432` 를 쓰는데, compose 를 띄워 둔
  기계에서는 그것이 실재하는 DB 다. 실 DB 가 필요하면 `@pytest.mark.realdb` 로 빠져나간다.
- **OHLC는 `integer`.** 국내 주가는 원 단위 정수라 `numeric`이 필요 없다(25% 절약).
- `ohlcv`는 **연 단위 RANGE 파티셔닝**. 인덱스는 PK 하나로 시작한다.
- **`listed_shares`는 `ohlcv`와 `securities` 양쪽에 있고 중복이 아니다** (ADR-DS-0010).
  `securities`는 최신, `ohlcv`는 그 거래일이다. **값이 갈리는 것이 정상**이라
  동기화 오류로 읽지 않는다. 종목당 한 줄로 접으면 액면분할 종목(10:1 실측 4종목)의
  과거 회전율이 10배 틀리고, 아예 빼면 `market_data.py:109`의 turnover가 예외 없이
  전 종목 `0.0`이 된다 — 숫자가 나오므로 화면만 봐서는 안 잡힌다.
  ⚠️ `market_cap = close × listed_shares`는 780,484행 전부에서 성립하지만
  **`CHECK` 제약으로 걸지 않는다** — 원본 정의가 바뀌는 첫 행에서 배치가 롤백된다.
  `scripts/check_migration_fitness.py`가 세기만 한다.
- **일회성 적재기는 `scripts/load_pg.py`다** (ADR-DS-0011 S3 · ADR-DS-0014, 2026-08-23).
  SQLite `daily_price` 780,484행 → `ohlcv` + `securities`, `fetch_log` → `ohlcv_sync_log`.
  실측 **32초**(27,900행/초)에 대조 21항목 전부 일치. **다시 돌려도 안전하다**
  (`ON CONFLICT`). 원본은 `mode=ro` 로 연다.
  - `python3 scripts/load_pg.py --dry-run` (DB 없이 원본만) · `--verify-only`(대조만)
  - ⚠️ **읽기 경로는 아직 SQLite다.** Postgres에 자료가 있지만 **아무도 읽지 않는다** —
    그것이 S3의 정의다. 그래서 SQLite에 새 거래일이 들어오면 두 저장소가 갈린다.
    다시 돌리면 따라잡는다.
  - ⚠️ **원격 DB는 호스트로 막는다.** `APP_ENV`로는 못 막는다 — 개발자 셸은 그 값이 없어
    `local`로 떨어지는데 `DATABASE_URL`은 Supabase일 수 있다. 뚫으려면 `--allow-remote`.
  - ⚠️ **`clip_kind_ck`가 일곱 값인지 적재 직전에 확인한다.** clip은 이 적재기가 손대는
    표가 아닌데도 본다 — 볼륨을 다시 세울 수 있는 마지막 순간이 그때이기 때문이다.
    실제로 이 가드가 S2 때 만든 옛 볼륨을 잡아냈다.
  - ⚠️ **접은 것과 비워 둔 것**: `securities`에 옛 이름이 없다(101종목) ·
    `is_delisted`는 전부 false(107종목이 후보이나 **추정하지 않는다**) ·
    `universe_tier`는 전부 `full`(구성종목 목록이 레포에 없다). 근거는 ADR-DS-0014.
- **배포본의 `라이브 조회` 배지는 낡음이 아니라 저장소 부재다** (ADR-DS-0016).
  GitLab 연동 배포는 **git 에 있는 것만** 올리므로 `.gitignore` 된 `krx_bundle.db`(30MB)는
  `.vercelignore` 가 무슨 말을 하든 배포본에 닿지 않는다. **갱신으로 풀리지 않고 S6 이 푼다.**
  그래서 `_data_status()` 의 KRX 카드는 `APP_ENV` 를 보고 처방을 가른다 — 배포본에
  "축약본을 만드세요" 를 띄우면 **따를 수 없는 처방**이라 시간만 버린다.
  스냅샷 카드는 반대다. 커밋되므로 로컬에서 만들어 push 하면 배포본까지 닿는다.

- **응답에 상한을 건다** — Vercel 요청·응답 본문 4.5MB 한도 (ADR-DS-0004).
  목록형은 `page`+`size`, **시계열형은 구간 상한 + 잘림 고지**(`meta.row_truncated`).
  시계열을 페이지로 자르면 이동평균이 페이지 경계에서 깨진다.
  ADR-DS-0004가 지목한 다섯 곳은 **전부 상한이 섰다** (2026-08-17).
  기본값은 FRED `max_points=2000` · 야후·차트 `3000`이고, 잘리면
  `truncated`·`total_count`로 알린다. **`yf_data.MAX_HISTORY_ROWS`와
  `market_chart.MAX_POINTS`는 같은 값을 유지한다** — 같은 야후 일봉을 보는 두 경로라
  값이 갈리면 `/api/yf/history`와 `/api/chart/series`가 다른 봉 수를 준다
  (`tests/test_limits.py`가 검사한다).
  ⚠️ **자르는 순서가 있다.** `market_chart.series`는 야후를 `max_rows=0`으로 불러
  이동평균을 전 구간에서 계산한 뒤 자른다. 잘라 놓고 계산하면 120일선의 앞 119일이
  통째로 null이 된다.
- ⚠️ **`/api/research/export/*`는 환경으로 막지 않는다.** 이건 화면이 쓰는 **리포트 렌더러**
  (md·html)이고 배포본에서 `static/assets/research.js`가 호출한다. 막으면 기능이 죽는다.
  ADR-DS-0004가 말하는 **BI용 CSV 익스포트는 아직 없다** — 만들 때 `/api/exports/*`로
  네임스페이스를 새로 쓰고 그때 `APP_ENV=local` 조건부 등록을 적용한다.
- `app/clients/*.py`는 **9종**이다 — dart_data · dart_report · ecos_data · fred_data ·
  fss_data · hf_data · kosis_data · krx_data · yf_data. 상위 문서의 "5종"·"7종"은 낡았다.
  개명 전(`api-test`) 시절부터 있던 코드이므로 리팩터링 전 `NOTICE.md`를 확인한다.
- **출처 표기는 `<provider>-<tier>` 두 토막이다** (ADR-DS-0009). tier 는 `db`·`bundle`·
  `derived`·`live`·`live-memo` 다섯이 전부고, `source.split("-", 1)` 로 항상 쪼개진다.
  `mode`(`/api/krx/status`)만 접두사 없는 맨 tier 다.
  ⚠️ **폴백하는 조회에서 층을 알려면 `*_tiered()` 짝을 쓴다** — `snapshot_tiered()` ·
  `series_tiered()`. `tier()` 는 저장소 **전체** 상태라, 원본이 차 있는데 그 날짜·그 종목만
  없어 축약본으로 내려간 경우를 `db` 라고 잘못 말한다. 하드코딩된 `"cache"` 가 세 곳에서
  이 방식으로 틀려 있었다.
  화면이 이 값을 **정확히 비교**하므로(`krx.html`·`stock.html`) 값을 바꾸면 화면도 같이 본다.
  `tests/test_source_vocabulary.py`가 어휘와 화면 양쪽을 검사한다.
  ⚠️ 이름이 `source` 라고 다 같은 축이 아니다 — `research/ledger.py`의 출처 등급(`KRX`·
  `DART-…`)과 `preprocess`의 `source`는 **누가 생산했나**라서 이 어휘 밖이다.
- **문서는 Obsidian 볼트로 자동 미러된다** (ADR-DS-0013). `post-commit` 훅이
  `scripts/sync_obsidian.py` 를 불러 `Master_Obsidian/30_Projects/data-service/` 에 쓴다.
  대상은 `README` · `AGENTS` · `docs/decisions/*` · `docs/README` · `세션-시작-프롬프트` 다.
  ⚠️ **한 방향이다. 볼트 쪽에서 고친 것은 되돌아오지 않고 다음 커밋에 덮인다.**
  메모를 남기려면 미러 폴더 **밖**(`00_Inbox` 등)에 노트를 만들고 링크한다.
  ⚠️ **`invoke check` 에 묶지 않았다** — 검증 명령이 레포 밖에 쓰기를 하면 그 명령을
  신뢰할 수 없게 된다. 부작용의 자리는 훅이다.
  ⚠️ **훅은 clone 마다 새로 설치해야 한다**(`invoke hooks`). `invoke preflight` 가
  없으면 한 줄 알려 주되 **막지는 않는다** — 미러는 문서 편의이지 품질 게이트가 아니다.
  ⚠️ 이 레포는 서브모듈이라 `.git` 이 **포인터 파일**이다. 훅 자리는 `git rev-parse --git-dir` 로 찾는다.
  볼트가 최신인지 의심스러우면 `python3 scripts/sync_obsidian.py --check` 로 잰다.
  ⚠️ 전역 규칙(`~/.claude/CLAUDE.md` §7.1)은 "`docs/`는 `.gitignore`"라고 하는데
  **이 레포는 한 단계 다르다** — ADR 이 코드와 같은 커밋에 묶여야 하므로 `docs/`를 커밋한다.
  그래서 여기서는 gitignore 대신 **양쪽 다 보관**이 된다.
- **`clip.kind`는 일곱 값이다** — `news`·`filing`·`dataset`·`report`·`memo`·`post`·`video`
  (ADR-DS-0014 §8, 2026-08-23). 수집 갈래가 여섯이고 그것이 `kind` 넷으로 접히며
  `dataset`·`report`·`memo`는 갈래가 아니라 **내가 담는 것**이라 원래부터 있었다.
  ⚠️ ADR-DS-0012 §2의 "여섯 값"은 그 정정 전 표기다. **갈래 수와 값 수는 1:1이 아니다.**
- **팀 프로젝트는 별도 레포다** (ADR-DS-0012 §9). 이 레포는 **개인 프로젝트로 완성**하고,
  팀 협업용 장치(브랜치 전략·이슈 템플릿·다인 배포)를 미리 넣지 않는다.
- ⚠️ **`app/core/trading_calendar.py`는 공휴일을 모른다.** `weekday() < 5`로 주말만 거른다.
  거래일 판정이 필요하면 `trading_calendar` 테이블을 쓴다 (ADR-DS-0002).
  ⚠️ **그 안내는 아직 실행 불가다** — DDL만 섰고 표를 채우는 코드도 읽는 코드도 없다.
  저장계층 전환 때 함께 처리한다 (ADR-DS-0011 **S9**).
  ★ 이 파일은 하류 `label-service`의 **수직 배리어가 의존하는 정본**이 된다.
  공개 형태(함수 시그니처·반환 타입)를 바꿀 때 그 사실을 기억한다.
