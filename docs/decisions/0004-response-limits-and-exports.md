# ADR-DS-0004: 응답에 상한을 걸고, 리포트 렌더러와 BI 익스포트를 구분한다

## 상태

채택됨 (2026-08-17) — 01 검증본의 초안을 **실물 대조 후 재정의**한 것이다

## 맥락

상위 문서는 이 결정을 두 문장으로 규정했다.

> "`/prices` 응답에 페이지네이션이 있다 (4.5MB 한도)"
> "`/export` 는 `APP_ENV=local` 에서만 등록된다"

**두 문장 다 이 레포의 코드를 가리키지 않는다.**

**① `/prices` 는 존재하지 않는다.** 레포 전역 grep 0건이다. 01번 리서치가 설계한
미래 계약의 경로명이고 대응물이 없다. 실제 시계열 경로는 다섯이며, 상한 현황이 갈린다.

| 경로 | 상한 | 판정 |
|---|---|---|
| `GET /api/krx/stocks/{code}/ohlcv` (krx_router.py:267) | `days` le=250 | 안전 |
| `GET /api/stocks/{code}/ohlcv` (market_router.py:212) | `count` le=250 | 안전 |
| `GET /api/krx/stocks` (krx_router.py:182) | `page` + `size` le=500 | **완전한 페이징 이미 있음** |
| `GET /api/chart/series`·`/overlay`·`/compare` (chart_router.py:196~) | **없음** | ⚠️ |
| `GET /api/yf/history` (yf_router.py:144) | `period=max` 허용 | ⚠️ |
| `GET /api/fred/series/{id}` (fred_router.py:125) | **개수 상한 전무** | ⚠️ DGS10 은 1962년부터 16,000행+ |

즉 위험한 곳은 KRX 계열이 아니라 **차트·야후·FRED** 쪽이고, 상위 문서는 그 반대를 지목했다.

**② `/export` 는 존재하지만 다른 물건이다.** 실물은
`POST /api/research/export/md`(research_router.py:255)와
`POST /api/research/export/html`(:276)로, GIC Context Pack 을 받아 마크다운·인쇄용 HTML
문자열을 돌려주는 **리포트 렌더러**다. 상위 문서가 말한 "BI 도구가 읽을 CSV 익스포트"
(`?format=csv` → `data/exports/*.csv`)는 이 레포에 **없다.**

두 물건을 같은 것으로 보고 "`/export` 를 `APP_ENV=local` 전용으로" 실행하면
**배포본의 리포트 내보내기가 통째로 죽는다** — `static/assets/research.js:1520`·`:1566` 이
배포본에서 이 두 경로를 호출한다. 덧붙여 `APP_ENV` 문자열은 레포 전체에 0건이라
조건부 등록은 한 줄도 구현돼 있지 않다.

## 결정

1. **"페이지네이션"을 일률 적용하지 않는다.** 응답 형태에 맞는 것을 쓴다.
   - 목록형(전종목 스냅샷·검색) → `page` + `size` 페이징. 이미 그렇게 돼 있다.
   - 시계열형(일봉·지표) → **구간 상한 + 잘림 고지**. 시계열을 페이지로 자르면
     이동평균·수익률 계산이 페이지 경계에서 깨진다.
2. 상한이 없는 세 곳에 상한을 넣는다 — `chart_router` 3종 · `yf_router.history` ·
   `fred_router.series`. 잘렸을 때는 응답 메타에 그 사실을 담는다
   (`kosis_router` 가 이미 `meta.row_truncated` 로 하고 있다 — 그 관례를 따른다).
3. **`/api/research/export/*` 는 그대로 둔다.** 환경으로 막지 않는다.
   화면 기능이고 배포본에서 쓰인다.
4. BI 익스포트는 **아직 없다.** 만들 때 `/api/exports/*` 네임스페이스를 새로 쓰고,
   그때 `APP_ENV=local` 조건부 등록을 적용한다. 리포트 렌더러와 경로를 섞지 않는다.

## 근거

- **Vercel 요청·응답 본문 4.5MB 한도**는 실재하고, 초과하면 413 이다.
  다만 실측 추정으로 그 한도를 실제로 위협하는 것은 FRED 장기 일별 시리즈
  (16,000~26,000행 · 0.3~0.6MB)가 아니라 **상한 없는 조합 호출**이다.
  한도를 넘지 않더라도 응답 시간이 `vercel.json` 의 `maxDuration: 60` 을 먼저 친다.
- **시계열을 페이지로 자르면 안 되는 이유가 코드에 있다.** `/api/krx/stocks/{code}/ohlcv`
  는 캔들과 함께 이동평균 3계열을 돌려준다. 페이지 경계에서 앞선 구간이 없으면
  이동평균의 앞부분이 비어 화면이 잘못 그려진다. 그래서 `days` 상한 방식이 맞다.
- **`kosis_data.py:361` 의 `max_rows=5000` 이 이미 같은 패턴이다.** 상한을 걸고
  `meta.row_truncated`·`series_truncated` 로 잘린 사실을 밝힌다. 새 규칙을 만들 필요가 없다.
- **경로를 섞지 않는 이유.** `/export` 라는 이름 하나에 "화면이 쓰는 리포트 렌더러"와
  "로컬 전용 BI 덤프"가 함께 들어가면, 환경 분기를 걸 때 어느 쪽이 죽는지 알 수 없다.
  이번에 실제로 그 혼동이 DoD 문장으로 굳어 있었다.

## 결과

- DoD 문장 두 개를 고쳐야 한다 — "`/prices` 페이지네이션"과 "`/export` 는 local 전용"은
  현 코드에 대해 검증 대상이 없다. 세션 문서·CONTEXT 정정 목록에 올린다.
- `tests/__snapshots__/test_contract.ambr` 의 쿼리 파라미터 스냅샷이 상한 추가를 잡는다.
  의도한 변경이므로 `pytest --snapshot-update` 로 갱신하고 그 diff 를 커밋에 남긴다.
- BI 익스포트를 만들 때 `data/exports/` 는 `.gitignore` 대상이다(팩터 익스포트는 금방 수십 MB).

## 구현 (2026-08-17 완료)

결정 2번의 다섯 자리에 모두 상한이 섰다.

| 경로 | 파라미터 | 기본값 | 상수 |
|---|---|---|---|
| `GET /api/fred/series/{id}` | `max_points` | 2,000 | `fred_data.MAX_SERIES_POINTS` |
| `GET /api/yf/history` | `max_rows` | 3,000 | `yf_data.MAX_HISTORY_ROWS` |
| `GET /api/chart/series`·`/overlay`·`/compare` | `max_points` | 3,000 | `market_chart.MAX_POINTS` |

셋이 같은 규칙을 쓴다 — **최근 것을 남기고, 구간 통계는 남긴 구간에서 다시 내고,
캐시에 든 원본은 건드리지 않는다.** 잘리면 `truncated`·`total_count`로 알리고,
`overlay`·`compare`는 화면이 이미 찍고 있는 `note` 문장에도 실어 보낸다.

야후·차트가 3,000인 이유는 **`PERIODS` 에 있는 `10y`(약 2,470거래일)를 온전히 통과시키기
위해서**다. 기간 버튼에 있는 구간을 상한이 잘라 버리면 버튼이 거짓말을 한다.
`max` 만 잘린다.

구현하며 확인한 것 둘:

1. **자르는 순서가 결과를 바꾼다.** `market_chart.series` 는 야후를 `max_rows=0` 으로
   불러 이동평균을 **전 구간에서 계산한 뒤** 자른다. 잘라 놓고 계산하면 120일선의 앞
   119일이 통째로 null 이라 선이 늦게 시작한다. 이 ADR 이 "페이지로 자르면 안 된다"고
   한 것의 실체가 이것이고, `tests/test_limits.py` 가 그 지점을 검사한다.
2. **`overlay` 의 FRED 조회는 상한을 꺼야 한다**(`max_points=0`). 조회 구간이 이미
   주가 구간으로 묶여 있는데 기본 상한(2,000)이 또 걸리면 **오래된 쪽이 잘려**
   구간 앞부분의 거시지표 선이 통째로 빈다. 자른 티도 안 나는 조용한 결손이다.

## 참조

- 전역: ADR-CT-0006(CI 비필수 — 검증은 `invoke check` 가 정본)
- 관련: ADR-DS-0003(환경별 분기 — `APP_ENV` 도입 지점)
