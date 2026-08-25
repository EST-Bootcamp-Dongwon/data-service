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
  - ✅ **실행처가 정해졌다 — 사람 + 화면이다** (ADR-DS-0017). 같은 사슬을 대시보드
    **자료 갱신** 패널에서도 돌린다(`POST /api/refresh/run`). CI 는 여전히 필수 경로가
    아니고, 스케줄러를 걸어도 **커밋·push 는 사람**이라(push 가 곧 배포) 화면은 어차피 필요하다.
    OS 스케줄러는 버린 것이 아니라 **그 위에 얹을 다음 순서**다.
- **자료 수집: `invoke collect`** (ADR-DS-0020) — DART 공시·정기보고서를 `clip` 에 담는다.
  ⚠️ **`invoke refresh` 와 다른 축이다.** 그쪽은 시세(SQLite)를 따라잡히고 이쪽은
  보관함(Postgres)에 담는다. **순서 의존이 없어 사슬에 얹지 않았다** — 얹으면
  `--skip-pg` 가 수집까지 삼키고 진행률이 "공시 0건에 100%" 가 된다(기각 근거는 그 ADR §1).
  - `invoke collect` (시총 상위 350 · **약 3분 반** · 1,000호출 남짓) · `--code 005930` (한 종목) ·
    `--dry-run` (네트워크 없이 예상 호출만) · `--check` (능력·예산·매핑 나이만)
  - ⚠️ **`invoke check` 에 묶지 않는다** — `refresh`·`hooks` 와 같은 이유다.
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
  (ADR-DS-0015 · 전환 S4~S6). ⭐ **이제 두 환경의 기본값이 같다 — `postgres`**
  (ADR-DS-0021 · S6, 2026-08-25). S5 가 로컬을, S6 이 배포본을 뒤집었다.
  ⚠️ **배포본에는 `DATABASE_URL` 이 반드시 있어야 한다** — 없으면 `database_url()` 이
  예외를 던져 **화면 HTML 은 뜨고 시세 API 4경로가 500** 이 된다(실측). 겉보기로는
  "화면은 열리는데 비어 있다" 라서 더 알아채기 어렵다. 그래서 **순서가 뜻을 가진다**: ①Supabase 적재 →
  ②Vercel 환경변수 → ③기본값을 뒤집은 push. ②와 ③을 바꾸면 깨진 배포본이 잠깐 존재한다.
  ⚠️ **값이 같아졌다고 상수 둘(`DEFAULT_STORE_BACKEND_LOCAL`·`_VERCEL`)을 합치지 않는다** —
  배포본만 되돌려야 하는 순간이 오고, 합치면 로컬까지 끌려 내려간다. **되돌림 단위가 값이다.**
  `settings.store_backend()` 가 정본이고 어휘 밖 값이면 **예외**다(`app_env()` 와 같은 모양).
  되돌리는 단위는 여전히 환경변수 한 줄이다 — `STORE_BACKEND=sqlite`.
  ⚠️ **상수가 아니라 함수인 것이 뜻을 가진다.** 모듈 상수로 두면 import 시점에 얼어붙어
  검사가 스위치를 못 뒤집는다 — `krx_store.DB_PATH` 가 실제로 그렇게 굳어 있어
  `KRX_DB_PATH` 를 `monkeypatch.setenv` 해도 아무 효과가 없다(실측).
  - **이음매는 아홉이고 한 벌이다** — `_cache_is_empty`·`latest_date`·`available_dates`·
    `snapshot_tiered`·`series_tiered`·`window`·`stats`·**`lookup_security`**, 그리고
    `tier()` 가 따라온다. (S4 때는 여덟이었고 **S5 가 `lookup_security` 를 들였다**.)
    하나만 남기면 SQLite 를 지운 셸에서 Postgres 는 꽉 찼는데 `tier()` 만 `bundle` 을 낸다.
    ⚠️ `lookup_security` 의 이름 검색은 **`ohlcv` 를 이어야 한다** — 이름은 `securities`,
    거래대금은 `ohlcv` 에 있다. `securities` 만 보면 정렬 근거가 사라져 "삼성" 이
    삼성전자가 아닌 것을 가리키는데 **오류는 안 뜬다**.
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
    ⚠️ 다만 **막다른 길로 두지도 않는다** (ADR-DS-0018). `_fetch()` 가 `OSError` 만 잡아
    처방 넉 줄(띄우기·URL·되돌리기)을 붙여 **다시 던진다** — 기본값이 `postgres` 가 되면서
    "DB 를 안 띄우고 앱을 켠다" 가 새 clone 의 첫 경험이 됐기 때문이다.
  - ✅ **읽기 표면을 우회하는 곳은 이제 하나이고, 그것은 의도된 것이다** (ADR-DS-0018).
    `stock_service.py` 는 S5 가 이음매로 들였다. 남은 `scripts/build_stock_master.py:51` 은
    갱신 사슬의 **4단계**이고 Postgres 적재는 **5단계**라, 뒤집으면 직전 회차 자료로
    마스터를 만든다. 결정적으로 `--skip-pg` 가 깨진다(그 플래그는 `needs_db` 단계만
    건너뛴다). **S8 이 쓰기를 옮길 때 자연히 사라진다.**
    `tests/test_krx_pg.py` 가 목록을 얼려 두고, 검사기는 산문이 아니라 **AST** 를 본다.
  - 검증: 함수 24항목 중 23 일치 · HTTP 20경로 중 19 완전 일치(나머지 하나는 야후 라이브라
    저장소와 무관) · 48 동시 요청에서 Postgres 48/48. 전부 ADR-DS-0015 "검증" 절에 있다.
- **검사는 환경을 씻고 돈다** — `tests/conftest.py` 의 autouse `isolate_env` (ADR-DS-0015 §5).
  위험한 여섯을 지우고 `DATABASE_URL` 을 **붙을 수 없는 주소**(`127.0.0.1:1`)로 덮는다.
  ⚠️ **`STORE_BACKEND` 도 `sqlite` 로 덮는다** (ADR-DS-0018). S4 까지는 지우기만 해도
  `sqlite` 로 떨어졌지만 S5 부터는 `postgres` 로 떨어져 읽기 경로를 타는 검사가 전부 죽는다
  (실측 11건). 그래서 검사 묶음은 **새 기본값을 재현하지 않는다** — 기본값 자체는
  `tests/test_krx_pg.py` §3 이 `monkeypatch` 로 환경을 만들어 직접 본다.
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
- **core 유니버스 정본은 `data/universe_core.json` 이고 만드는 것은 `scripts/build_universe.py` 다**
  (ADR-DS-0021, 2026-08-25). KOSPI200 200 + KOSDAQ150 150 = **350종목**. 이 파일이 생기면서
  `securities.universe_tier` 가 **처음으로 참**이 됐다(core 350 / full 2,525).
  - ⚠️⚠️ **보유한 `KRX_API_KEY`(OpenAPI)로는 못 만든다.** 구성종목 서비스가 **스펙에 없다** —
    `idx/*` 는 "코스피 200 이 몇 포인트인가" 를 주지 "무슨 종목이 들었나" 를 주지 않고,
    `idx/idx_isu_base_info` 는 404 다. 문이 둘이고, 필요한 것은 **KRX 정보데이터시스템 계정**
    (`.key` 의 `KRX_ID`·`KRX_PW`)이다. 익명 세션으로는 400 이다(파라미터 문제가 아니다).
  - ⚠️ **네이버 금융은 쓰지 않는다** — `robots.txt` 가 `Disallow: /` 다(`/sise/` 허용은
    네이버 자체 봇 `yeti` 에게만). KOSPI200 이 실제로 나오지만 **되니까 한다는 근거가 아니다.**
  - ⚠️ **pykrx 를 의존성으로 들이지 않았다.** HTTP 두 번에 pandas 를 끌고 온다.
    프로토콜만 읽어 표준 라이브러리 urllib 으로 옮겼다(`app/clients/*.py` 아홉과 같은 규칙).
  - **`corp_code.json` 과 같은 취급이다** — 사람이 갱신하고 낡으면 **막지 않고 알린다**(90일).
    `python3 scripts/build_universe.py --check` 가 나이만 잰다. ⚠️ 90일은 **재촉 주기이지
    정확성 보증이 아니다** — 정기변경은 연 2회지만 수시변경은 그보다 잦다.
  - ⚠️ **종목 수가 예상(200·150)에서 ±5 를 넘으면 파일을 쓰지 않는다.** 조용히 반쪽짜리
    목록을 쓰는 것이 가장 비싼 고장이다. 정말 맞다면 `--force`.
  - ⚠️ **`isdigit()` 로 거르지 않는다** — core 350 에 신형 종목코드가 2건 있다
    (`0009K0`·`0126Z0`). `build_corp_code.py` 가 정확히 그 결함을 앓았다(ADR-DS-0020 §12).
- **원격(Supabase) 적재는 `load_pg.py --universe core --allow-remote` 다** (ADR-DS-0021).
  - ⭐ **거르는 것과 딱지를 붙이는 것은 다른 일이다.** `--universe core` 는 *무엇을 담을지*,
    `universe_tier` 는 *담은 것이 무엇인지*다. 로컬 `full` 적재에서도 딱지는 붙는다.
  - ⚠️ **`--allow-remote` 는 "전종목을 부어도 좋다" 가 아니다.** 가드가 둘이다 —
    원격이면 `--allow-remote`, 원격인데 `full` 이면 `--universe core` 를 요구한다.
    한 손잡이로 묶으면 승인 하나에 ADR-CT-0010 의 약속이 딸려 깨진다.
  - ⚠️ **`universe_tier` 는 `COALESCE` 가 아니라 그대로 덮는다** — 편입·제외가 반영되려면
    **core→full 로 내려가는** 갱신도 통해야 한다.
  - ⚠️ **`ohlcv_sync_log` 는 거르지 않는다**(거래일 단위 표다). 그래서 Supabase 의 `ohlcv` 는
    103,663행인데 `rows` 합은 821,928 이다 — **이 어긋남이 정상**이다.
  - ⚠️ **직결 주소를 쓸 수 없다.** `db.<ref>.supabase.co` 는 **IPv6 전용**이다. 6543 풀러
    (`aws-0-ap-northeast-2.pooler.supabase.com`)만 IPv4 다 — ADR-DS-0003 의 6543 선택에
    "커넥션 수명" 말고 **붙을 방법이 그것뿐**이라는 이유가 하나 더 붙었다.
  - ⚠️ **두 저장소는 갈린다.** 로컬에 새 거래일이 들어와도 Supabase 는 그대로다. 다시 돌리면
    따라잡지만 **그 실행이 어디에도 묶여 있지 않다**(갱신 사슬은 쓰기 측 SQLite 에 못 박혀 있다 — S8).
  - ⚠️ **배포본의 `clip` 은 비어 있다.** 표는 섰고 잠금은 풀렸지만 `security_id` 가 양쪽에서
    다르게 매겨져(IDENTITY) 종목코드로 다시 짝지어야 한다. S6 의 완료 조건 밖이다.
- **배포본의 `라이브 조회` 배지는 낡음이 아니라 저장소 부재다** (ADR-DS-0016).
  GitLab 연동 배포는 **git 에 있는 것만** 올리므로 `.gitignore` 된 `krx_bundle.db`(30MB)는
  `.vercelignore` 가 무슨 말을 하든 배포본에 닿지 않는다. ~~갱신으로 풀리지 않고 S6 이 푼다.~~
  ✅ **S6 이 풀었다** (ADR-DS-0021) — 배포본이 Supabase 를 읽어 `mode=db` 다.
  ⚠️ 다만 **core 350 에 한해서다.** 나머지 2,525종목은 배포본에 자료가 없어 여전히
  번들(없음)→라이브로 떨어진다. 배지가 사라진 것이 아니라 **적용 범위가 좁아진 것**이고,
  S7 에서 번들을 버릴 때 그 2,525종목이 갈 곳이 없어진다는 사실과 한 벌이다.
  그래서 `_data_status()` 의 KRX 카드는 `APP_ENV` 를 보고 처방을 가른다 — 배포본에
  "축약본을 만드세요" 를 띄우면 **따를 수 없는 처방**이라 시간만 버린다.
  스냅샷 카드는 반대다. 커밋되므로 로컬에서 만들어 push 하면 배포본까지 닿는다.

- **갱신 사슬 정본은 `app/services/refresh_job.py` 의 `STEPS` 하나다** (ADR-DS-0017).
  `invoke refresh`(`tasks.py`)와 화면 버튼(`POST /api/refresh/run`)이 **같은 표**를 읽는다.
  ⚠️ **`tasks.py` 에 스크립트 경로를 다시 적지 않는다** — 두 벌이 되면 순서·인자가 한쪽만
  고쳐진 채로 오래 간다. 이 사슬은 이미 그래서 24거래일 밀렸다.
  `tests/test_refresh_job.py` 가 `tasks.py` 본문에 스크립트 경로가 없는지 검사한다.
  - **막는 것은 환경이 아니라 능력이다** — 배포본 · `REFRESH_API=off` · `scripts/` 부재 ·
    `data/` 쓰기 불가. 넷의 **처방이 각각 다르므로** 하나로 뭉치지 않는다.
    `settings.refresh_api()` 는 `store_backend()` 와 같은 모양이다(어휘 밖 값이면 예외).
  - **경로는 배포본에서도 등록한다. 실행만 `503` 으로 거절한다.** 조건부 등록을 하면
    계약 스냅샷이 환경에 따라 갈리고, **왜 안 되는지 설명할 자리가 사라진다.**
    거절 코드는 셋이다 — `503`(못 한다) · `409`(하나가 돌고 있다) · `422`(인자가 어휘 밖).
  - ⚠️ **갱신이 끝나면 프로세스가 든 사본 다섯을 버린다**(`reset_process_caches()`).
    대시보드 `/tmp` 캐시 · 스냅샷 · 파생 JSON · 자동완성 색인 · 라이브 조회 캐시.
    **이것이 가장 조용한 고장 지점이다** — 안 씻으면 "갱신은 됐다는데 화면은 그대로" 가 되고
    오류가 안 뜨므로 사람이 버튼을 의심한다. `snapshot_store.reload()` 는 스냅샷만 비우고
    `_code_index` 를 두던 버그가 있어 함께 고쳤다.
  - ⚠️ **상태는 프로세스 메모리다.** 워커가 여럿이면 화면이 조용히 어긋난다
    (지금은 로컬·이미지 둘 다 워커 1이라 참이다).
  - ⚠️ **컨테이너에서는 막힌다** — `.dockerignore` 가 `scripts/` 를 뺀다. 이미지에 넣지
    않기로 한 이유(uid 1001 이 쓴 파일을 호스트 사용자가 커밋해야 한다)는 ADR-DS-0017 에 있다.
  - **커밋하지 않는다.** `FORBIDDEN_IN_CHAIN` 이 그 약속을 검사 가능한 사실로 붙든다.
  - ⭐ **사슬은 쓰기 측(SQLite)에 못 박혀 있다** — `refresh_job.CHAIN_ENV` (ADR-DS-0018).
    다섯 단계가 전부 SQLite 를 다루는데 S5 가 **읽기** 기본값을 뒤집으면서 그 값이 자식까지
    새어 들었다. `fetch_krx.py --status` 는 시끄럽게 죽지만 `build_market_snapshot.py` 는
    **안 죽고 직전 회차 자료로 스냅샷을 만든다** — 날짜로만 드러난다. 아래쪽이 훨씬 비싸다.
    ⚠️ **한 표를 둘이 읽는다** — `_run_step()`(화면)과 `tasks.py`(셸). `tasks.py` 에 값을
    다시 적지 않는다. **S8 이 쓰기를 옮길 때 이 표를 지운다.**
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
- **자료 보관함은 `app/repositories/clip_store.py` + `app/routers/clip_router.py` 다**
  (ADR-DS-0019, 2026-08-25). ⭐ **Postgres 전용이라 배포본은 아직 잠겨 있다** —
  `DATABASE_URL` 을 주는 것이 S6 이고, 그 잠금은 **왜·언제**를 함께 말한다.
  - **막는 것은 환경이 아니라 능력이다** — `availability()` 는 `APP_ENV` 를 보지 않고
    **표에 붙어 보고** 판단한다. 그래야 Supabase 를 붙인 날 고치지 않아도 살아난다.
  - **경로는 배포본에서도 등록하고 실행만 `503`.** ⚠️ 단 `GET /api/clips/status` 만은
    `503` 을 내지 않는다 — 화면이 버튼을 잠글지 정하려면 이유를 200 으로 받아야 한다.
  - **`kind` 별 검증은 라우터에 둔다** (`REQUIRED_PAYLOAD`). `payload` 가 jsonb 라 표가
    못 본다. ⚠️ **DDL 이 이미 거는 것은 다시 걸지 않는다** — `clip_kind_ck`·
    `clip_link_needs_url_ck` 는 표 소관이고, `tests/test_clip.py` 가 **DDL 파일을 읽어**
    코드 어휘와 대조해 두 벌이 되지 않게 한다.
  - **중복은 오류가 아니다.** 같은 링크를 다시 담으면 기존 것을 돌려주고 `created=false`.
    `url_key` 정규화가 스킴·`www.`·추적 파라미터·프래그먼트를 떼고 질의를 정렬한다.
    ⚠️ 루트의 `/` 는 남긴다(떼면 서로 다른 사이트가 뭉친다) · **네트워크를 타지 않는다**.
  - ⚠️ **`reassign_industries()` 의 `WHERE industry_source = 'auto'` 한 줄이
    `industry_source` 컬럼이 존재하는 이유 전부다.** 빠지면 사람이 고친 값이 조용히 사라진다.
  - ⚠️ **화면에서는 `onStockResolved()` 를 `render()` 맨 앞에 둔다.** 뒤에 두면 차트
    라이브러리가 안 뜨는 환경에서 그 예외에 보관함이 **같이 죽는다**(jsdom 실측).
  - **접속 실패 처방은 `db.unreachable()` 한 곳에서 만든다.** 공통은 그쪽이,
    **되돌리는 법만** 부르는 쪽이 `extra` 로 얹는다 — 시세는 SQLite 로 되돌아가고
    보관함은 되돌아갈 곳이 없다.
  - ✅ **자동 수집이 생겼다** — DART 공시·정기보고서 (ADR-DS-0020 · 아래 항목).
    담기 버튼은 여전히 `/stock` 하나뿐이다 — 두 번째 화면을 붙일 때 공용 조각으로 뺀다.

- **자동 수집 정본은 `app/services/dart_collector.py` 의 `collect()` 하나다**
  (ADR-DS-0020, 2026-08-25). 여섯 갈래 중 **첫 갈래**다. 부르는 곳이 둘이고 같은 함수다 —
  `POST /api/collect/dart/security`(종목 하나 · 동기 · 3호출) 와
  `scripts/collect_dart.py`(유니버스 일괄 · 셸).
  - ⭐ **갱신 사슬 밖이다. `refresh_job.py` 와 그 검사 39건은 한 줄도 안 바뀌었다.**
    `refresh_job` 800줄 중 사슬 표는 52줄이고 나머지는 자식 프로세스 실행기인데,
    DART 수집에는 자식 프로세스가 필요 없다. 새 실행기를 만들지도 일반화하지도 않았다.
  - **② 일괄에 HTTP 경로가 없다.** 진행률·중단 DTO 를 쓸 곳이 아직 하나도 없어서다 —
    지금 만들면 쓰는 곳이 하나뿐인 추상이 된다. 두 번째 수집기(네이버)가 그것을 정당화한다.
  - ⚠️⚠️ **증분은 호출 수를 줄이지 않는다.** 창을 좁혀도 종목당 `len(types)` 만큼 그대로다
    (`page_no` 고정). 줄이는 것은 **재방문 억제(`REVISIT_HOURS=20`)** 하나뿐이고 그때는
    **0호출**이다. "증분이니 한도 걱정 없다" 는 틀린 결론이고 그 오해가 예산을 태운다.
  - ⚠️⚠️ **`dart_data` 의 최상위 `truncated` 는 거짓 음성을 낸다** — 유형 셋을 합친 뒤라
    A 하나가 100건에서 잘려도 `False` 다. 그래서 `by_type` 을 보고 **창을 반으로 쪼개**
    그 유형만 다시 묻는다(`MAX_WINDOW_SPLITS=3`). **상한이 있어야 `--dry-run` 의 예상
    호출이 참으로 남는다.** 못 메운 것은 `partial` + `truncated_codes` 로 **보인다** —
    자식의 잔여 잘림을 버리면 `ok` 로 기록되고 그 구간이 영영 안 메워진다(실측으로 잡았다).
  - ⚠️ **`screen="stock"` 하나 · URL 은 `dart_data` 것 그대로.** 유니크가 `(kind, url_key)`
    뿐이라 `screen` 이 갈리면 나중 것이 흡수되고 목록이 조용히 반쪽이 된다. URL 을 새로
    조립하면 `url_key` 가 갈려 멱등성이 무너지는데 `payload.rcept_no` 에는 제약이 없다.
  - ⚠️ **①공시와 ②정기보고서는 `payload.filing_kind`** 로 가른다(`kind` 는 둘 다 `filing`).
    판정은 `public_type=="A"` **와** 정규식을 **동시에** 본다 — `startswith` 는 `[기재정정]`
    정정본을 놓치고, `category=='실적'` 은 직교하는 축이라 못 쓴다.
  - **예산은 사후 집계다.** `watermark:dart_calls:<KST 날짜>` 는 **이 수집기가 부른 것만**
    센다 — 리서치 화면 호출은 안 세어진다. 그래서 일 한도 20,000 중 **12,000 만 쓰고**
    나머지를 마진으로 둔다. 화면 라벨이 **「이 수집기가 쓴 호출」** 인 것이 그 이유다.
    상한 정본은 `dart_collector` 이고 `settings` 가 아니다(그쪽은 환경이 정하는 것의 집).
  - **거절은 넷** — `503`(껐거나·키 없음·보관함 없음, **셋을 뭉치지 않는다**) ·
    **`429`**(오늘 예산 소진 · `Retry-After`) · `422` · `404`. `429` 가 이 레포의 **넷째**
    거절 어휘다 — 기존 셋 중 어느 것도 "지금은 안 되지만 내일은 된다" 를 못 말한다.
    `GET /api/collect/dart/status` 만은 **언제나 200** 이다.
  - **되돌리는 단위는 환경변수 한 줄** — `COLLECT_API=off` (`refresh_api()` 와 같은 모양).
  - ⚠️ **`data/corp_code.json` 이 낡으면 막지 않고 알린다**(7일). 낡으면 신형 종목코드로
    새로 상장한 회사가 404 로 떨어지는데 원인이 "DART 가 이상한가" 로 보인다.
    ⚠️ `scripts/build_corp_code.py` 가 `isdigit()` 로 걸러 **신형 코드(`0001A0`)를 통째로
    빼고 있었다** — 다시 만들어도 안 낫던 결함이라 오래 숨어 있었다(ADR-DS-0020 §12).
  - **수집기는 시세 읽기 경로를 타지 않는다** — `krx_*`·`snapshot_store`·`app.core.db`·
    `research` 를 import 하지 않고, `tests/test_dart_collect.py` 가 **AST 로** 검사한다.
  - ⚠️⚠️ **잘림은 두 갈래이고 둘 다 영구 손실이다** — ①DART 쪽이 잘라 보낸 것과
    ②우리가 유형 셋을 합친 뒤 `limit` 로 자른 것. **초안은 ①만 봤고 9종목이 자료를 잃었다**
    (정확히 100건에 멈춘 채 `status='ok'`). `truncated_types()` 가 둘을 함께 본다.
  - 실측: 상위 350종목 **16,286건 · 209초 · 1,039호출 · 실패 0**, 재실행 시 새로 47 ·
    이미 16,482(멱등). 전부 ADR-DS-0020 "검증" 절에 있다.
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
