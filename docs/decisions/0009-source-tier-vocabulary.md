# ADR-DS-0009: 데이터 출처 표기를 `<provider>-<tier>` 한 어휘로 통일한다

## 상태

채택됨 (2026-08-18)

## 맥락

`krx_store` 는 원본 캐시 · 배포용 축약본 · 라이브 조회의 **3단 폴백**으로 돈다
(ADR-DS-0002 "어려워지는 것" 절이 이미 이것을 숙제로 지목했다). 그 층을 밖으로
알리는 통로가 다섯 갈래인데, **갈래마다 값의 어휘가 다르다.**

| 산출처 | 지금 값 |
|---|---|
| `krx_store.source()` | `cache` · `bundle` · `live` |
| `krx_store.stats()["mode"]` | `cache` · `bundle` · `live` |
| `krx_store.snapshot_live()` 3번째 값 | `live` · `live-cache` |
| `ts_service._load_domestic` | `krx-cache` · `krx-bundle` · `krx-live` · `yfinance` |
| `market_chart.breadth_series` | 위 3값 + `precomputed` |
| `stock_service` | `krx-cache`(하드코딩) · `yfinance` |
| `dashboard_data` 스냅샷 카드 | `cache`(하드코딩) · `live` · `live-cache` |

한 문자열에 **두 축이 섞여 있다.**

1. **tier** — 어느 층에서 읽었나 (원본 캐시 / 축약본 / 사전집계 / 원격 직접호출)
2. **provider** — 누가 준 자료인가 (KRX / 야후)

접두사를 붙이는 곳과 안 붙이는 곳이 갈려서, **같은 층이 `cache` 와 `krx-cache` 두 이름**으로
돌아다닌다. 화면은 이 값을 `=== 'cache'` · `=== 'krx-cache'` · `=== 'live'` 로 **정확히 비교**해서
배지와 안내 문구를 고른다 (실측 4곳 — `krx.html:174`·`188`·`243`, `stock.html:348`).
값이 하나만 어긋나도 화면이 조용히 틀린 말을 한다.

실제로 셋이 이미 틀려 있었다.

- `krx_router.py:220` — `source = "cache"` 하드코딩. `store.snapshot()` 이 번들로
  폴백해도(`krx_store.py:333`) 응답은 `cache` 라고 말한다.
- `krx_router.py:160` — `stats()` 의 3값 `mode` 를 `days > 0` 만 보고 2값으로 덮는다.
  번들이면 `days > 0` 이라 `cache` 가 되고, `stats()` 가 `db_path`·`db_size_mb`·`first_date`
  까지 **번들 것으로** 채워 주므로 `krx.html:190-193` 의 💾 캐시 배지에 번들 숫자가 찍힌다.
- `stock_service.py:282` — `"source": "krx-cache"` 하드코딩. `stock.html:348` 이 이 값을
  정확히 비교해 **`data/krx_cache.db` 라는 파일 경로를 사용자 화면에 그대로** 출력한다.

**그리고 이 어휘는 저장계층 전환(ADR-DS-0002)에서 재설계 대상이다.** Postgres 로 옮기면
`cache` 와 `bundle` 의 구분이 사라진다. 그때 무엇이 남을지를 지금 정해 두지 않으면,
전환 세션에서 화면 10곳을 다시 헤집게 된다.

## 결정

### 1. tier 어휘를 다섯으로 고정한다

| tier | 뜻 | 전환 후 |
|---|---|---|
| `db` | 이 서비스의 **정본 저장소**에서 읽었다 | 그대로 (`krx_cache.db` → Postgres) |
| `bundle` | 배포용 축약본(읽기 전용·구간이 짧다)에서 읽었다 | **사라진다** |
| `derived` | 사전집계 파생물(`krx_derived.json`)을 읽었다 | 그대로 |
| `live` | 원천을 그 자리에서 호출했다 | 그대로 |
| `live-memo` | 방금 그 `live` 응답을 프로세스 메모리에서 재사용했다 | 그대로 |

`cache` → **`db`** 로 개명한다. Postgres 가 정본이 되면 그건 캐시가 아니라 저장소다.
`precomputed` → **`derived`**, `live-cache` → **`live-memo`** 로 맞춘다
(`live-cache` 는 층 이름에 `cache` 가 또 들어가 `cache` 층과 헷갈렸다).

**저장계층 전환의 diff 는 "`bundle` 분기를 지운다" 한 줄이 된다.** 남는 값의 뜻은 안 바뀐다.

### 2. `source` 필드는 예외 없이 `<provider>-<tier>` 두 토막이다

```
provider, tier = source.split("-", 1)
```

`krx-db` · `krx-bundle` · `krx-derived` · `krx-live` · `krx-live-memo` · `yahoo-live`.
provider 를 붙일지 말지 **필드마다 판단하지 않는다.** 판단이 들어가는 순간 지금처럼
`cache` 와 `krx-cache` 로 갈린다. `split("-", 1)` 이라 tier 안의 하이픈(`live-memo`)은 안전하다.

`yfinance` → **`yahoo-live`**. `ts_service`·`stock_service` 의 `source` 는 KRX 값과
야후 값이 **한 필드에 섞이는** 자리라, 한쪽만 두 토막이면 규칙이 아니게 된다.

### 3. `mode` 는 맨 tier 다

`StatusResponse.mode` 는 store **하나**의 현재 층을 말하므로 provider 가 자명하다.
`db` · `bundle` · `live` 를 그대로 쓴다. `source` 와 어휘를 공유하되 접두사만 없다.

### 4. `CacheStats` 에 `mode` 를 넣지 않는다

`mode` 의 통로는 `StatusResponse.mode` 하나다. 두 곳에 실으면 갈라진다.
(지금도 `stats()` 는 `mode` 를 돌려주지만 `CacheStats` 에 선언이 없어 FastAPI 가
응답에서 **떨어뜨린다** — 즉 이미 단일 통로였고, 라우터가 그걸 재계산해 망가뜨렸을 뿐이다.)
대신 라우터는 재계산하지 않고 `stats()["mode"]` 를 **그대로 올린다.**

### 5. 층을 아는 것은 호출자가 아니라 저장소다

암묵 폴백(호출자가 어느 층에서 왔는지 모르는 채 값만 받는 구조)이 위 세 결함의 공통
원인이다. 폴백하는 조회 함수는 **층을 함께 돌려주는 짝**을 갖는다.

- `snapshot_tiered(bas_dd, market) -> (rows, tier)` / `snapshot()` 은 그 얇은 껍데기
- `series_tiered(code, days, end) -> (rows, tier)` / `series()` 는 그 얇은 껍데기

호출자가 `tier()` 를 따로 부르면 **틀린다.** `tier()` 는 저장소 전체의 상태이고,
`snapshot()` 은 *그 날짜가* 원본에 있는지로 갈리기 때문이다. 원본이 차 있어도 특정
날짜만 없으면 번들로 내려간다.

### 6. 이 ADR 의 범위 밖 — 이름이 `source` 라고 다 같은 축이 아니다

| 자리 | 무엇을 말하는가 | 처리 |
|---|---|---|
| `research/ledger.py` `SOURCE_GRADES` (`KRX`·`DART-…`·`yfinance`) | **누가 생산했나** (출처 등급 판정용 기관명) | 그대로 둔다 |
| `chart_router` 오버레이 계열의 `source` (`ECOS`·`FRED`·`yfinance`) | 같은 기관명 축 | 그대로 둔다 |
| `preprocess.mark_adjusted(source=...)` (`krx`·`yfinance`) | 수정주가 여부 기록용 provider | 그대로 둔다 |
| `preprocess/calendar.py` `calendar_source` | 거래일 축의 근거 | 그대로 둔다 |
| `/api/market/ticker`·`/summary`·`/api/chart/series` 의 `live`·`tmp-cache` | 여러 provider 를 합친 응답의 캐시 여부 | **후속** — KRX 값이 섞이지 않아 급하지 않다 |

## 근거

- **`cache` 를 지금 개명하는 이유.** 개명 비용은 어느 시점에 해도 같지만, 전환 세션에
  미루면 "번들 삭제"와 "이름 변경"이 한 커밋에 섞여 회귀 원인을 못 가린다. 지금은
  값 어휘만 바뀌고 분기 구조는 그대로라 되돌리기 쉽다.
- **정확 비교가 4곳뿐이라 지금이 값싸다** (실측 grep). 나머지 소비처
  (`timeseries.html:385`·`627`, `market.html`, `quant.html`, `research.js`)는 문자열을
  그대로 찍기만 해서 값이 바뀌어도 안 깨진다. 소비처가 더 늘기 전에 못박는다.
- **`live-memo` 를 `live` 에 합치지 않는 이유.** 화면은 둘을 구분하지 않지만
  (`krx.html:243` 은 `live` 계열이면 같은 문구), 라이브 조회가 실제로 KRX 를 두드렸는지
  10분 TTL 메모리에서 재사용했는지는 **인증 차단기·호출량을 볼 때** 필요한 구분이다.
- **`stats()` 가 번들 숫자를 `db_path`·`db_size_mb` 에 채우는 것은 고치지 않는다.**
  그게 의도다 — 화면 배지가 "0거래일"만 보여 주면 고장으로 보인다(krx_store.py:481-482).
  틀린 것은 숫자가 아니라 **그 숫자에 붙는 라벨**이었다. `mode` 가 바로 서면 라벨이 바로 선다.

## 결과

**쉬워지는 것**

- 값 하나로 층과 제공자를 둘 다 안다. `source.split("-", 1)` 이면 끝이다.
- 저장계층 전환(세션 D~G)의 출처 표기 diff 가 **`bundle` 분기 삭제**로 좁혀진다.
  `db`·`live`·`live-memo`·`derived` 는 뜻이 그대로다.
- 화면이 파일 경로를 말하지 않는다. `data/krx_cache.db` 는 구현 세부이고,
  Postgres 로 가면 존재하지도 않는다.

**어려워지는 것 · 남는 숙제**

- **암묵 폴백이 아직 셋 남았다.** `latest_date()`(krx_store.py:299) ·
  `available_dates()`(317) · `window()`(462) 는 층을 안 돌려준다. 지금은 그 값을 출처로
  보고하는 소비자가 없어 결함이 아니지만, 짝(`*_tiered`)이 없다는 사실은 남는다.
  `market_data`·`ind_r` 은 `window()`·`series()` 를 쓰면서 출처는 저장소 전체 상태
  (`source_tag()`)로 보고한다 — **근사치다.** 특정 종목만 번들로 내려간 경우를 놓친다.
- `/api/market/ticker`·`/summary`·`/api/chart/series` 의 `live`·`tmp-cache` 는 이 어휘
  밖에 남는다 (범위 §6). 언젠가 합칠 때 한 번 더 값이 바뀐다.
- `실습/pages/stock.html` 은 배포되지 않는 강의 실습 사본이라 옛 값(`krx-cache`)을
  그대로 둔다. `static/` 과 갈라진다는 사실을 여기 적어 둔다.

## 참조

- ADR-DS-0002 (저장계층 전환 — "어려워지는 것" 절이 이 3단 분기를 숙제로 지목)
- 실물: `app/repositories/krx_store.py` · `app/routers/krx_router.py` ·
  `app/services/stock_service.py` · `app/services/dashboard_data.py` ·
  `app/services/market_chart.py` · `app/services/ts_service.py`
