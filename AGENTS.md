# AGENTS.md — data-service

> 공통 규칙 정본: `../quant-contract/AGENTS.md`
> 이 파일에는 **이 모듈에만 해당하는 것**만 적는다. 공통 규칙을 복사하지 않는다.

## 검증 명령

- 전체 검증: `invoke check` (ruff → pytest → uv export → docker build)
- 문서 검증: `invoke docs-check` (markdownlint → lychee --offline → openapi export)
- **CI가 아니라 이 명령이 정본이다.** CI는 이 명령을 호출만 한다.

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

- **엔트리포인트 정본은 `app.main:app`.** README·Dockerfile·Vercel 세 곳이 어긋나면 배포가 실패한다.
  Vercel에는 `pyproject.toml`의 `[tool.vercel] entrypoint = "app/main.py"`로 별도 지정한다.
- **DB 접속은 `APP_ENV`로 분기한다** (ADR-DS-0003).
  `vercel`: 6543 + `NullPool` + `statement_cache_size=0` + `prepared_statement_cache_size=0`
  `local` : 5432 직결 + 정상 풀. **셋 중 하나만 빠져도 prepared statement 충돌이 산발적으로 난다.**
- **OHLC는 `integer`.** 국내 주가는 원 단위 정수라 `numeric`이 필요 없다(25% 절약).
- `ohlcv`는 **연 단위 RANGE 파티셔닝**. 인덱스는 PK 하나로 시작한다.
- **응답 페이지네이션 필수** — Vercel 요청·응답 본문 4.5MB 한도.
- **`/export` 경로는 `APP_ENV=local`에서만 라우터에 등록한다** (ADR-DS-0004).
- `app/clients/*.py`는 ④ api-test에서 승격한 코드다. 리팩터링 전 `NOTICE.md`를 확인한다.
