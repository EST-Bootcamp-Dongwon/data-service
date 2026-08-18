# ADR-DS-0002: 파일 캐시를 Postgres 로 옮기되 수집 대장은 그대로 살린다

## 상태

채택됨 (2026-08-17)

## 맥락

승격 문서는 이 모듈의 저장 계층을 "파일 캐시라 DB가 없다"로 서술했다. **실물은 다르다.**
실측하면 저장소가 넷으로 갈려 있다.

| 갈래 | 실물 | 크기 |
|---|---|---|
| SQLite | `data/krx_cache.db`(daily_price 780,484행·282거래일) | 123,207,680 B |
| SQLite | `data/krx_bundle.db`(읽기전용 축약본 415,803행) | 30,461,952 B |
| SQLite | `data/report_index.db`(int8 벡터 2,489건) | 18,526,208 B |
| JSON·gzip | `industry_map.json` · `market_snapshot.json.gz` · `krx_derived.json` | 약 650 KB |
| `/tmp` | `tmp_cache`(TTL 기반, 서버리스 전용 탈출구) | 가변 |
| 프로세스 메모리 | `user_store`(실습용 mock 30명) | — |

즉 문제는 "DB가 없다"가 아니라 **여섯 갈래로 흩어져 있고 배포를 재현할 수 없다**는 것이다.

- `data/` 171 MB 중 git 에 있는 것은 **2 MB(JSON 7개)뿐**이다. 나머지 3개 `.db` 는
  `.gitignore` 대상이다.
- 그런데 `.vercelignore` 는 `krx_bundle.db`·`report_index.db` 를 "반드시 올라가야 한다"고
  명시한다. `vercel deploy` 가 git 이 아니라 **로컬 폴더를 그대로 올리기** 때문에 지금은 돈다.
- 결과적으로 **`git clone` 만으로는 배포를 재현할 수 없다.** 팀에 넘길 때 그대로 깨진다.
- 서버리스에서 `krx_store` 는 쓰기 불가를 감지해 `/tmp` 로 내려가고(krx_store.py:59-68),
  거기 만들어진 DB 는 항상 비어 있다. 실질 조회는 번들 폴백이 담당한다.

## 결정

Postgres 로 옮긴다. 표는 다섯이다 (`sql/init/01-schema.sql`).

1. **`securities`** — 종목 마스터. 지금 `daily_price` 가 780,484행마다 반복하는
   name·market·sector·listed_shares 를 분리한다. 키는 `security_id integer`
   (ADR-CT-0009). 상장폐지 종목을 지우지 않는다 — 지우면 생존 편향이 생긴다.
2. **`ohlcv`** — 일별 시세. OHLC 는 `integer`, 연 단위 RANGE 파티셔닝.
3. **`trading_calendar`** — 거래일 달력. **신설**이다(아래 근거 참조).
4. **`ohlcv_sync_log`** — 거래일 단위 수집 대장. `fetch_log` 를 그대로 이식한다.
5. **`watermark`** — 소스별 최신 동기화 지점. 날짜로 나뉘지 않는 수집물용.

**옮기면서 반드시 살리는 규칙 — `rows = 0` 은 휴장일 마커다.**

`fetched_dates()`(krx_store.py:160-173)가
`WHERE rows > 0 OR bas_dd < (오늘 - ZERO_ROW_RETRY_DAYS)` 로 '다시 받을 필요 없는 날짜'를
계산한다. 7일(krx_store.py:157)이 지나면 0건을 확정 휴장으로 본다.
이걸 "받은 날짜만 기록"하는 단순 워터마크로 바꾸면 **확정 휴장일을 매 수집마다 다시
물어보게 된다.** 국내 공휴일이 연 15일 안팎이므로 10년이면 150번의 헛된 왕복이
매 수집마다 생긴다.

## 근거

- **스키마를 백지에서 쓰지 않는다.** 원재료가 이미 코드에 있다 —
  `ohlcv` 는 krx_store.py:81-97(OHLC 가 이미 `INTEGER`, PK `(bas_dd, code)`),
  `securities` 는 scripts/build_krx_bundle.py:75-83 의 `stock` 표가 **분리 선례**를
  이미 만들어 뒀고, `ohlcv_sync_log` 는 krx_store.py:106-110 의 `fetch_log` 다.
- **`trading_calendar` 만 신설인 이유.** 상위 문서는 "휴장일 계산 이미 구현됨"이라
  적었으나 `app/core/trading_calendar.py:38-51` 은 `weekday() < 5` 로 주말만 거르고
  docstring 이 스스로 "공휴일은 반영하지 않는다"고 밝힌다. 실제 휴장 판정은
  ① `fetch_log` 의 0건 ② `preprocess/calendar.py:40` 의 `HOLIDAY_RUN_MAX = 5` 근사
  ③ `krx_derived.json` 의 파생 거래일 282일 — 세 곳에 흩어져 있었다. 한 곳으로 모은다.
- **OHLC 가 `integer` 인 근거.** 국내 주가는 원 단위 정수다. `numeric` 대비 약 25% 절약이고,
  SQLite 쪽도 이미 `INTEGER` 라 표현이 바뀌지 않는다. 유일한 실수는 `change_rate` 다.
- **`change` 를 역산하지 않고 컬럼으로 남기는 근거.** 종가 차이로 계산하면 3원씩
  어긋나는 종목이 있다(scripts/build_krx_bundle.py:64-67 의 실측).

## 결과

**쉬워지는 것**

- `git clone` → `docker compose --profile local-db up -d` 만으로 스키마가 선다. 배포 재현이 된다.
- 날짜 표기 변환이 사라진다. 지금은 SQLite 가 `bas_dd TEXT`(YYYYMMDD), JSON 이
  `YYYY-MM-DD` 라 `krx_store.py:316-319` 가 손으로 `replace("-", "")` 한다.
- `_resolve_db_path()` 의 `/tmp` 폴백(krx_store.py:59-68)과 `KRX_DB_PATH` 환경변수가 불필요해진다.
- `market_snapshot.json.gz` 의 존재 이유(캐시 DB 를 배포에 못 올려 미리 계산해 둠)가 사라진다.

**어려워지는 것 · 남는 숙제**

- `krx_store.source()` 의 **cache / bundle / live 3단 분기**(krx_store.py:263-278)가
  화면 배지·리포트 근거 문구·대시보드 등급(dashboard_data.py:317-341)까지 전파돼 있다.
  단일 저장소가 되면 이 3단 분기와 그것을 소비하는 문자열이 전부 재설계 대상이다.
- `market_snapshot` 의 `r60`·`r120`·`r250`·`per`·`pbr`·`roe` 는 원천이 아니라 **파생 지표**다.
  ohlcv + 재무에서 재계산하는 경로가 따로 필요하다. 지금 대응물이 없다.
- `report_index.db` 의 `vector BLOB`(int8 1024차원)은 pgvector 를 켜기 전까지 **파일로 남는다.**
  옮길 이유가 없다.
- `user_store` 는 이 모듈 도메인과 무관한 실습용 mock 이다. 전환 대상이 아니다.

## 참조

- 전역: ADR-CT-0007(저장소 이원화) · ADR-CT-0009(종목 식별자) · ADR-CT-0010(유니버스 2단계)
- 스키마 실물: `sql/init/01-schema.sql` · `sql/init/02-partitions.sql`
