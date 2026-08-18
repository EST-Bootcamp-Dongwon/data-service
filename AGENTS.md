# AGENTS.md — data-service

> 공통 규칙 정본: `../quant-contract/AGENTS.md`
> 이 파일에는 **이 모듈에만 해당하는 것**만 적는다. 공통 규칙을 복사하지 않는다.

## 이 모듈의 경계

**데이터를 가져오는 것까지.** 리포트를 만드는 것은 `research-service` 다 (ADR-DS-0007).

⚠️ 다만 **경계만 그었을 뿐 아직 옮기지 않았다.** `app/services/research/` 24개 파일은
전부 이 레포에 남아 있고 배포본 화면이 그것을 쓴다. **검증 없이 지우지 않는다.**
경계표 정본은 `docs/decisions/0007-research-boundary.md` 에 있다.

**새 코드는 이 경계를 넘지 않는다** — `stays` 쪽 파일이 `goes` 쪽을 새로 import 하지 않는다.

## 검증 명령

- 전체 검증: `invoke check` (ruff check → pytest → uv export → docker build)
- 문서 검증: `invoke docs-check` (markdownlint → lychee --offline → openapi export)
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
- **DB 접속은 `APP_ENV`로 분기한다** (ADR-DS-0003).
  `vercel`: 6543 + `NullPool` + `statement_cache_size=0` + `prepared_statement_cache_size=0`
  `local` : 5432 직결 + 정상 풀. **셋 중 하나만 빠져도 prepared statement 충돌이 산발적으로 난다.**
- **OHLC는 `integer`.** 국내 주가는 원 단위 정수라 `numeric`이 필요 없다(25% 절약).
- `ohlcv`는 **연 단위 RANGE 파티셔닝**. 인덱스는 PK 하나로 시작한다.
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
- ⚠️ **`app/core/trading_calendar.py`는 공휴일을 모른다.** `weekday() < 5`로 주말만 거른다.
  거래일 판정이 필요하면 `trading_calendar` 테이블을 쓴다 (ADR-DS-0002).
  ⚠️ **그 안내는 아직 실행 불가다** — DDL만 섰고 표를 채우는 코드도 읽는 코드도 없다.
  저장계층 전환 때 함께 처리한다.
  ★ 이 파일은 하류 `label-service`의 **수직 배리어가 의존하는 정본**이 된다.
  공개 형태(함수 시그니처·반환 타입)를 바꿀 때 그 사실을 기억한다.
