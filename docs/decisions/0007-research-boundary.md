# ADR-DS-0007: 리서치 리포트 생성 파이프라인의 경계를 긋는다 (이관은 보류)

## 상태

채택됨 (2026-08-18)

## 맥락

이 레포에는 GIC 리서치 리포트 생성 파이프라인이 `app/services/research/` 아래 **24개 파일 ·
약 11,241행**으로 살아 있다. 그런데 **이전 작업 문서 어디에도 이 코드의 처리 계획이 없었다** —
작업 목록에도 DoD 에도 나오지 않았고, 문서는 라우터를 "7종"이라 적으면서 그중 하나가
`research_router.py` 라는 사실을 놓치고 있었다. 방치하면 리팩터링 과정에서 조용히 사라진다.

상위 문서(`quant-contract/research/09-프로젝트-구조-재설계.md`, `CONTEXT.md` rev.7)는 이것을
신설 `research-service` 로 **이관**하라고 적는다. 판정 기준은 하나다 —
**"데이터를 가져오는가, 리포트를 만드는가."**

그러나 이 레포의 실제 작업 순서는 **개인 프로젝트를 먼저 완성하고, 그 다음 코드를 가져가
팀 프로젝트로 발전시키는 것**이다. 반대가 아니다. 지금 쪼개면 돌아가는 완성품 하나를
미완성 조각 둘로 만든다. 게다가 `research-service` 는 저장소조차 아직 없다(5차 생성 예정).

## 결정

1. **경계를 긋는다. 지금 옮기지 않는다.** `app/services/research/` 는 전부 이 레포에 남는다.
2. 아래 **경계표를 이 ADR 안에 정본으로 둔다.** `research-service` 착수 시
   **복사 → 검증 → 그다음 원본 제거** 순서로 실행한다.
3. **검증 전에 원본을 지우지 않는다.**
4. 이관 실행 결정은 별도 ADR(DS-0008 이후)로 남긴다. 이 ADR 은 방향만 정한다.
5. 새 코드를 쓸 때 이 경계를 **넘지 않는다** — `stays` 쪽 파일이 `goes` 쪽을 새로 import 하지 않는다.

## 경계표

판정 기준: **데이터를 가져오는가(stays) / 리포트를 만드는가(goes)**

### 간다 (research-service) — 19

| 파일 | 행 | 한 줄 |
|---|---:|---|
| `services/research/stages.py` | 2,278 | 12상태 본체. 조회 자리 6곳을 빼면 9할이 리포트 생산 |
| `services/research/export_md.py` | 1,297 | **H09 그 자체.** 내보내기 전용이 아니다 |
| `services/research/export_html.py` | 837 | 인쇄용 HTML 조립. 데이터 호출 0줄 |
| `services/research/workstreams/ind_r.py` | 682 | 산업 리서치 판단 |
| `services/research/workstreams/corp_tp.py` | 682 | 기업 Top Pick 판단 |
| `services/research/workstreams/ind_tp.py` | 579 | 산업 Top Pick 판단 |
| `services/research/charts.py` | 588 | 차트 명세 → 그릴 수 있는 계열 |
| `services/research/headline.py` | 492 | 표지 조립 |
| `services/research/contracts.py` | 387 | 하네스 공통계약 |
| `services/research/tables.py` | 364 | 리포트용 표 |
| `services/research/narrative.py` | 358 | 해석카드 6칸 생성기 |
| `services/research/ledger.py` | 309 | E-/D-/CALC- 일련번호 장부 |
| `services/research/workstreams/corp_r.py` | 294 | 기업 리서치 판단 |
| `services/research/knowledge/financials.py` | 268 | 재무 기준선 + **한국어 이유문** |
| `services/research/knowledge/valuation.py` | 262 | 멀티플 선택 + **한국어 발자취** |
| `services/research/linkcheck.py` | 230 | 증거 추적성 점수 재료 |
| `services/research/redteam.py` | 220 | 반대심문 검사기 |
| `services/research/plan.py` | 214 | 12상태 진행률 상수표 |
| `routers/research_router.py` | 311 | 라우터 |

### 남는다 (data-service) — 2

| 파일 | 행 | 근거 |
|---|---:|---|
| `services/research/knowledge/glossary.py` | 229 | **유일하게 명백한 데이터 사전.** `data/glossary.json`(용어 427개)을 읽어 돌려줄 뿐 뜻을 만들지 않는다 |
| `services/semantic_search.py` | 207 | **종목 검색이다.** ①색인 소스가 DART 사업보고서 원문(PK=종목코드, 2,489행)이고 이 서비스가 만든 리포트는 색인에 한 글자도 없다 ②반환이 종목이다 ③임베딩 실패 시 종목 이름 검색으로 강등된다 ④라우터가 `/api/search/semantic` 이다. `research/` 안 참조 0건 |

### 갈라야 한다 (split) — 3

**`services/research/knowledge/macro.py` (408행)**
knowledge 5개 중 **유일하게 `clients` 를 import 하고, 유일하게 리서치 밖에서 쓰인다.**

| 부분 | 판정 |
|---|---|
| `risk_free_rate()` · `risk_free_ratio()` | **stays.** `market_data.monte_carlo_frontier()` → `/api/market/portfolio/frontier` → `quant.html` 이라는 리포트 무관 경로가 걸려 있다 |
| `cycle_phase()` | **한 번 더 갈라야 한다** — ECOS 지표 수집(stays) / 국면 라벨링(goes) |
| `SECTOR_SENSITIVITY` 이하 | **goes.** 이미 계산된 판정의 등급을 되돌리고 한국어 이유문을 갈아끼운다 |

⚠️ 통째로 옮기면 **효율적 투자선의 무위험수익률이 하드코딩 0.032 로 되감긴다.**

**`services/research/knowledge/portfolio.py` (261행)**
`performance` · `correlation*` · `diversification` 은 순수 계산(stays 성격),
`sharpe_verdict` · `mdd_verdict` · `summarize` 는 한국어 등급 문장(goes).
⚠️ **261행 중 실제로 도는 것은 `performance()` 하나뿐**이다(`ind_r.py:391`). 나머지 public 5개는
호출자 0건 — `technical.py` 를 지울 때(N93) 쓴 판정 기준과 같은 냄새다.
**옮기기 전에 사장 함수 정리가 먼저다.**

**`services/research/stages.py` (2,278행)**
갈라지는 선은 **H04 안쪽**이다. 외부 조회를 부르는 자리는 여섯 군데뿐이고
(`snapshot_store` · `industry_store` · `dart_data` · `dart_report` · `report_index` · `ts_service`),
그조차 구현은 이 파일에 없다 — 결과를 봉투에 씌울 뿐이다. **H05 이후는 데이터 호출이 한 줄도 없다.**
→ 통째로 따라가고, 저 여섯 자리는 data-service HTTP API 호출로 바뀌는 것이 자연스럽다.

## 이관 시 깨지는 것 (실행 시점에 반드시 확인)

| 옮기면 | 깨지는 것 |
|---|---|
| `export_md.py` 단독 | **`POST /api/research/runs/steps/H09`·`H10` 이 ImportError 로 죽고 리서치 화면 리포트 탭이 빈다.** `static/assets/research.js:375` 가 H09 산출물을 직접 읽는다 |
| `export_html.py` 단독 | PDF 버튼 사망 + **MD 내보내기의 차트 그림도 함께 죽는다** (`export_md` 가 `chart_svg_standalone` 을 지연 import). **둘은 반드시 같이 움직인다** |
| `macro.py` 통째로 | `/api/market/portfolio/frontier` 의 무위험수익률이 하드코딩으로 되감긴다 |
| `data/report_index.db` | **소비자가 둘이다** — `semantic_search` 는 `vector` 표, `stages.py:528` 은 `report` 표. 파일과 `repositories/report_index.py` 는 양쪽이 공유해야 한다 |

## 근거

- 판정은 24개 파일 + `semantic_search.py` 를 **전부 읽고** import 그래프를 grep 으로 실측해 내렸다.
  `knowledge/*` 5건은 교차 반증까지 돌리지 못했으므로 **1차 판정이다** — 이관 착수 시 재확인한다.
- `semantic_search.py` 판정은 근거가 네 겹이고 파일 docstring 이 스스로
  "이 검색은 리서치 파이프라인이 아니다" 라고 못박는다. 재확인 부담이 가장 낮다.
- **지금 옮기지 않는 이유는 준비가 안 돼서가 아니라 순서 때문이다.** `research-service` 는
  저장소가 아직 없고(5차), 이 레포의 DoD 가 먼저다.

## 결과

- 리서치 코드가 리팩터링 중에 조용히 사라지는 일이 없어진다.
- 배포본 화면이 지금 그대로 돈다 — 아무것도 옮기지 않았으므로.
- **비용**: 이 레포가 당분간 "데이터 + 리포트" 두 성격을 함께 갖는다. 그것을 감수한다.
- 새 코드가 경계를 넘으면 이관 비용이 다시 커지므로, 5번 규칙을 리뷰에서 지킨다.
