# ADR-DS-0015: 읽기 어댑터를 스위치 뒤에 두고, sync 호출 경로에는 전용 루프로 다리를 놓는다

## 상태

채택됨 (2026-08-25) — ADR-DS-0011 의 **S4**

## 맥락

S3 까지 780,484행이 Postgres 에 들어갔지만 **아무도 읽지 않았다.** 화면과 API 는 여전히
SQLite `daily_price` 를 본다. S4 는 그 읽기 경로를 옮기되, **기본값은 그대로 두는** 걸음이다
(ADR-DS-0011 §2 — 어댑터 커밋과 기본값 커밋을 나눈다).

옮기려고 보니 답해야 할 것이 넷이었다. 셋은 실측으로 갈렸고 하나는 판단이다.

### ① 호출 경로가 전부 동기다

라우트 핸들러 **60개가 전부 `def`** 다 — `async def` 가 하나도 없다. FastAPI 는 그것을
anyio 워커 스레드풀에서 돌리므로 저장소 계층은 **돌고 있는 루프가 없는 스레드**에서 불린다.
그런데 S2 가 세운 엔진 계층(`app/core/db.py`)은 async 전용이다.

핸들러 60개를 async 로 바꾸는 것은 S4 의 크기가 아니다. 그러면 다리를 놓아야 한다.

### ② 자료의 타입이 셋 달라진다

| 무엇 | SQLite | Postgres |
|---|---|---|
| `change_rate` | `float` (REAL) | **`Decimal`** (numeric(12,4)) |
| 날짜 | `'20260731'` 문자열 | **`datetime.date`** |
| `listed_shares` | `daily_price` 한 곳 | `ohlcv`(그 거래일) · `securities`(최신) **두 곳** |

셋 다 **예외 없이 조용히** 틀린다는 것이 공통점이다.

### ③ 검사가 개발자 셸의 `DATABASE_URL` 을 물고 돈다

`tests/conftest.py` 에 격리가 없다. S3 까지는 무사했다 — `settings.database_settings()` 를
부르는 파일이 `app/core/db.py` 하나뿐이었고 **그것을 아무도 import 하지 않았기** 때문이다.
S4 가 정확히 그 전제를 깬다.

실측: `APP_ENV=vercel DATABASE_URL=…supabase… pytest` 를 돌리면 세 값이 검사 안까지 그대로
도착한다. 그 URL 이 배포 DB 면 `invoke check` 가 **배포 DB 에 붙는다.**

### ④ `db` 라는 낱말이 두 저장소를 가리키게 된다

`tier()` 가 내는 `"db"` 는 지금까지 SQLite 원본 캐시를 뜻했다(ADR-DS-0009). Postgres 로
읽으면 같은 낱말이 다른 저장소를 가리킨다. ADR-DS-0011 이 이 겹침을 미리 경고했다.

## 결정

### 1. 다리는 **전용 배경 이벤트 루프** 하나다 — 실측으로 갈랐다

`app/core/db.py` §2-1 에 `run_sync()` 를 둔다. 데몬 스레드 하나에 루프를 띄우고
`asyncio.run_coroutine_threadsafe` 로 코루틴을 넘긴다.

기각한 대안은 **호출마다 `asyncio.run()`** 이다. 워커 스레드에는 루프가 없으니 문법적으로
성립하고, `scripts/check_db_connection.py` 가 이미 그 모양이라 자연스러워 보였다.
로컬 Postgres 에 스레드 8 × 6라운드로 대 봤다:

| 방법 | `APP_ENV=local` (정상 풀) | `APP_ENV=vercel` (NullPool) |
|---|---|---|
| 호출마다 `asyncio.run()` | **15/48 실패** · 88ms | 0 실패 · 71ms |
| 전용 루프 (1회차) | 0 실패 · 17ms | 0 실패 · 33ms |
| 전용 루프 (풀이 데워진 뒤) | 0 실패 · **13ms** | 0 실패 · 31ms |

실패는 `RuntimeError: Task … got Future … attached to a different loop` 다. 풀이 들고 있던
asyncpg 커넥션은 **그것을 만든 루프**에 묶여 있는데 `asyncio.run()` 은 매번 새 루프를 열고
닫는다. 다음 호출이 죽은 루프의 커넥션을 꺼내 쓰는 순간 터진다.

즉 `asyncio.run()` 방식은 **ADR-DS-0003 의 로컬 전략(정상 풀)을 포기해야만** 성립한다.
전용 루프는 커넥션이 전부 한 루프에 묶이므로 두 전략 어느 쪽에서도 돈다. 그것이 기준이었다 —
속도가 아니라 **ADR-DS-0003 을 건드리지 않는 것.**

세 번째 대안(`psycopg` 동기 엔진)은 다리 자체를 없애지만 접속 전략이 **두 벌**이 된다.
그러면 `scripts/check_db_connection.py` 가 화면이 쓰지 않는 드라이버를 재게 된다 —
ADR-DS-0003 rev.2 가 거짓 음성으로 한 번 데인 자리를 드라이버만 바꿔 다시 밟는 것이라 뺐다.

### 2. 사다리의 **모양**은 두 저장소에서 같다. 맨 윗단의 구현만 바뀐다

`snapshot_tiered`·`series_tiered` 의 폴백은 지금 그대로다 —
**정본 저장소에 있으면 `db`, 없으면 축약본, 축약본도 없으면 `tier()`**. `STORE_BACKEND` 는
"정본 저장소가 무엇인가"만 바꾼다.

기각한 것 둘:

- **"Postgres 에 없으면 빈 결과"** — ADR-DS-0011 §4 를 어긴다. 배포본은 아직 번들로만 도는데
  그 분기에 한해 S7 을 앞당기는 셈이라 화면이 빈다.
- **"Postgres → SQLite → 번들"** — 가장 안전해 보이지만 **S4·S5 의 검증을 통째로 무력화한다.**
  어댑터의 결함을 SQLite 가 조용히 메워 주면 화면은 초록인데 어댑터는 틀린 상태가 된다.
  S2 가 배운 것("깨끗한 상대에 한 번 대 보는 것은 검증이 아니다")과 같은 종류의 거짓 음성이다.

⚠️ **접속 실패를 빈 결과로 바꾸지 않는다.** 빈 결과셋은 폴백이고, 예외는 그대로 올라간다.
삼키면 DB 장애가 "그 날짜에 자료가 없음"으로 위장되어 화면이 150거래일짜리 번들로 조용히
강등된다 — 번들을 마지막에 버리는 이유로 든 실패 모드를 Postgres 쪽에서 재생산하는 꼴이다.

### 3. `tier` 는 `"db"` 그대로다. `postgres`·`pg` 를 만들지 않는다

④ 의 겹침은 **S7 에서 정리한다.** 지금 새 값을 만들면 셋이 동시에 깨진다 —
`tests/test_source_vocabulary.py` 의 어휘 다섯 값, `krx.html`·`stock.html` 의 정확 비교,
그리고 `dashboard_data.py:328-345` 의 `else` 가지(모르는 값이면 **"라이브 조회" 경고 배지**를
띄운다). 셋 다 S4 가 건드리면 안 되는 계약이다.

낱말의 뜻을 이렇게 고쳐 적는다 — **`db` 는 "이 서비스의 정본 저장소"이고, 그것이 어느
저장소인지는 `STORE_BACKEND` 가 정한다.** ADR-DS-0009 가 이미 그렇게 설계해 두었다
("`krx_cache.db` → Postgres, 전환 후 그대로").

### 4. 스위치는 `settings.py` 의 **함수**다. 어휘 밖 값이면 예외다

`settings.store_backend()` — 기본 `sqlite`, 허용값 `sqlite`·`postgres`.

**상수가 아니라 함수인 것이 뜻을 가진다.** 모듈 상수로 두면 import 시점에 얼어붙어 검사가
스위치를 뒤집을 방법이 없어진다. `krx_store.DB_PATH` 가 실제로 그렇게 굳어 있어
`KRX_DB_PATH` 를 `monkeypatch.setenv` 해도 아무 효과가 없다(실측 — 저장소를 빈 파일로 바꿔
`tier()` 를 뒤집었는데도 13개 검사가 전부 초록이었다).

오타(`pg`·`postgre`)는 **예외**다. 조용히 기본값으로 떨어뜨리면 "스위치를 켰다고 믿었는데
실은 SQLite 를 재고 있었다"가 되고, 그 거짓 음성이 이 전환에서 가장 비싼 실수다.
`app_env()` 가 같은 이유로 같은 모양이다.

### 5. 검사 격리를 `conftest.py` 에 **autouse** 로 넣는다

위험한 여섯(`DATABASE_URL`·`APP_ENV`·`VERCEL`·`VERCEL_ENV`·`STORE_BACKEND`·`KRX_DB_PATH`)을
지우고, `DATABASE_URL` 을 **붙을 수 없는 주소**(`127.0.0.1:1`)로 덮는다.

⚠️ **지우는 것만으로는 부족하다.** `APP_ENV` 를 지우면 `local` 로 떨어지고 `database_url()` 이
기본값(`@db:5432`)을 주는데, compose 를 띄워 둔 기계에서는 그것이 **실재하는 DB** 다.

autouse 여야 뜻이 있다 — 보호가 필요한 쪽은 픽스처를 요청하지 않는 96개이고, S4 이후
`krx_store` 읽기 경로를 타는 것이 정확히 그쪽이다. 탈출구는 `@pytest.mark.realdb` 로 내 두되
지금 그 마커를 단 검사는 **하나도 없다**.

## 근거

### 왜 이음매가 여덟이고 그 이상도 이하도 아닌가

자기 SQL 을 직접 실행하는 잎 함수만 분기한다 — `_cache_is_empty`·`latest_date`·
`available_dates`·`snapshot_tiered`·`series_tiered`·`window`·`stats`, 그리고 `tier()` 는
`_cache_is_empty()` 를 통해 따라온다.

`snapshot`·`series`·`universe`·`closes_matrix`·`source_tag` 는 **분기하지 않는다.** 전부 모듈
전역 이름으로 위 함수들을 부르므로 자동으로 따라온다. 거기까지 넣으면 이중 분기가 되고
S7 에서 걷어낼 것이 는다.

**여덟은 한 벌이다.** 하나만 남겨 두면, 로컬 SQLite 를 지운 개발자 셸에서 Postgres 는 꽉
차 있는데 `tier()` 만 `bundle` 을 내는 어긋난 상태가 된다.

### 질의를 두 걸음으로 나눈 이유 — 127배

종목 하나의 시계열을 `JOIN securities ON code = ?` 로 한 번에 뽑으면 플래너가 파티션 14개를
전부 훑는다. `security_id` 를 먼저 풀면 파티션마다 PK 를 곧장 탄다.

| 질의 | 실측 |
|---|---|
| `latest_date` `max(trade_date)` | 0.8 ms |
| `available_dates` `DISTINCT trade_date LIMIT 400` | 45 ms (ANALYZE 후) |
| `snapshot` 하루 전종목 2,763행 | 1.9 ms |
| `series` — JOIN 한 방 | **73.5 ms** |
| `series` — `security_id` 를 먼저 | **0.58 ms** ← 127배 |
| `window` 60일 166,057행 | 299 ms (정렬 없음) → **446 ms** (`code` 순 포함) |
| `stats` 전수 집계 | 447 ms |

### `window()` 에서 `code` 순을 **사서** 쓰는 이유

SQLite 는 PK 가 `(bas_dd, code)` 라 그 순서가 공짜였다. Postgres PK 는
`(security_id, trade_date)` 라 명시해야 나오고, 정렬 비용이 299 → 446ms 다.

그래도 산 이유는 둘이다. ① 빼면 Postgres 쪽 순서가 **미정**이 된다(힙 순서라 VACUUM 한 번에
바뀔 수 있다). ② `market_data.screening_funnel()` 의 `sorted(key=score)` 가 **안정 정렬**이라
동점 종목의 등수가 입력 순서를 그대로 따라간다 — 실측으로 75.6점 동점인 셀트리온·한국가스공사의
10·11위가 뒤바뀌었다. 그리고 이 함수가 **대신하는 SQLite 경로가 800ms** 다
(`krx_store.window()` docstring 의 실측). 순서를 사고도 여전히 더 빠르다.

### `available_dates()` 가 `ohlcv_sync_log` 를 안 보는 이유

대장 쪽이 56배 빠르다(45ms → 0.8ms). 그런데 그것은 "받아 봤다"는 기록이고 이 함수의 계약은
"자료가 있다"이다. SQLite 쪽도 `daily_price` 를 본다. 두 표가 갈릴 수 있는 경로가 실재하므로
(`OHLCV_INSERT` 는 `DO NOTHING` 인데 `SYNC_LOG_UPSERT` 는 `DO UPDATE` 다) 빠른 쪽을 택하려면
**갈림을 감시하는 자가 먼저** 있어야 한다. 그것은 S9 의 일이다.

### 모르는 컬럼에서 예외를 내는 이유

`krx_bundle.window()` 는 같은 경우에 빈 목록을 준다. 그쪽은 축약본에 **실제로 없는**
컬럼(`market_cap` 등)을 묻는 상황이라 "없다"가 맞는 답이다. 이쪽은 열세 컬럼을 전부 갖고
있으므로 모르는 이름은 자료가 없다는 뜻이 아니라 **부르는 쪽의 오타**다. SQLite 경로도 그때
`OperationalError` 로 죽는다(실측) — 같이 죽는 편이 맞다.

## 검증

### 함수 단위 — 24항목 중 23항목 동일

두 백엔드로 `krx_store` 를 직접 불러 대조했다. `tier`·`source_tag`·`latest_date`·
`available_dates`·`snapshot`(키·행수·개별 종목)·`series`(양끝·구간)·`window`(두 가지 컬럼
조합)·`stats`·`universe`·`closes_matrix` 가 **전부 일치**한다.

유일한 차이는 모르는 컬럼일 때의 예외 **종류**(`OperationalError` vs `ValueError`)다.
둘 다 죽는다. 위 "근거" 참조.

### HTTP 단위 — 20경로 중 19경로가 완전 동일

같은 코드를 두 포트에 `STORE_BACKEND` 만 달리해 띄우고 응답을 통째로 비교했다.
`krx_store` 를 읽는 20경로가 전부 200 이고, **의도된 두 자리를 빼면 불일치 0건**이다.

의도된 두 자리는 `stats()` 의 `db_path`(`krx_cache.db` → `data_service.ohlcv`)와
`db_size_mb`(117.5 → 107.0)뿐이다. 파일 개념이라 Postgres 에 대응물이 없는데 `CacheStats` 가
둘 다 필수로 요구한다. 없는 척하는 대신 DB 이름과 **파티션까지 합한** 실제 크기를 넣는다.

> ⚠️ `pg_total_relation_size('ohlcv')` 만 쓰면 **0** 이 나온다. 파티션 부모는 자기 자신이
> 비어 있다. 화면이 그 값을 "0.0MB" 로 찍어 고장처럼 보인다 — 초안이 실제로 그랬다.

`/api/stock/{ticker}` 하나만 74개 필드가 달랐는데 **저장소와 무관하다.** 그 응답의 `source`
가 `yahoo-live` 이고, 독립적으로 띄운 **sqlite 서버 둘**을 비교해도 같은 74개가 어긋난다
(반면 postgres 대 새 sqlite 는 3개). 프로세스마다 야후에서 받아 둔 값이 다른 것이다.

### 부하 — 48 동시 요청

8경로 × 6라운드를 동시에 던졌다(`-m 60`).

| 회차 | Postgres | SQLite |
|---|---|---|
| 1 | 48/48 · 9.2초 | 36/48 · 60초 초과 |
| 2 | 48/48 · **2.5초** | 31/48 · 60초 초과 |
| 3 | 48/48 · **2.2초** | 7/48 · 60초 초과 |

⚠️ **이 숫자를 "Postgres 가 빠르다"로만 읽지 않는다.** 이 기계에서 SQLite 원본은 `/mnt/c`
(WSL2 9p 파일시스템)에 있고 Postgres 는 도커 ext4 볼륨에 있다. 매체가 다르다.
다만 **회차마다 나빠지는 것**은 매체로 설명되지 않는다 — `krx_store` 는 조회마다
`init_db()` 를 부르고(호출 9곳) 그것이 전역 자물쇠 + SQLite 쓰기(`PRAGMA journal_mode=WAL`)라,
동시 요청이 거기서 줄을 선다. **S5 에서 이 값을 다시 잰다.**

### 검사

`invoke check` 5단계 초록. 226 tests (177 → 176: S2 경계 검사 하나를 지웠다 → 226: S4 검사
41 + 다리 검사 9를 더했다).

새 검사가 **장식이 아닌지**를 돌연변이로 확인했다 — 일곱 가지를 일부러 망가뜨렸고
전부 이름 있는 검사 하나가 잡았다:

| 망가뜨린 것 | 잡은 검사 |
|---|---|
| `NOT is_delisted` 제거 | `test_join_excludes_delisted_rows` |
| `Decimal` 을 안 내림 | `test_row_has_exactly_the_sqlite_key_set` |
| 기본값을 `postgres` 로 | `test_empty_value_is_the_default` |
| `window` 이음매를 끊음 | `test_postgres_backend_reaches_the_adapter[window]` |
| `date` 객체를 그대로 흘림 | `test_row_has_exactly_the_sqlite_key_set` |
| 격리에서 `STORE_BACKEND` 뺌 | `test_isolation_covers_every_dangerous_name` |
| 오타를 조용히 기본값으로 | `test_typos_raise_instead_of_falling_back` |

## 결과

**쉬워지는 것**

- `STORE_BACKEND=postgres` 한 줄로 읽기 경로 전체가 넘어간다. 되돌리는 것도 한 줄이다.
- 검사가 개발자 셸과 무관해졌다. 오염된 환경에서 돌려도 같은 결과가 나온다.
- 어댑터의 값 되돌리기가 **경계 한 곳**에 모여 있다 — 소비자 20여 곳을 안 고쳤다.

**어려워지는 것 · 남는 숙제**

- ⚠️ **읽기 표면을 우회하는 곳이 둘 남아 있다.** `stock_service.py:144-159` 와
  `scripts/build_stock_master.py:44-54` 가 `store.connect()` 로 SQLite 에 생 SQL 을 던진다.
  스위치를 켜도 **그 둘은 계속 SQLite 를 읽는다.** 두 저장소가 같은 자료를 갖고 있는 동안은
  아무 증상이 없어서 S4 의 계약 보존은 그대로 만족된다. 그러나 **S5 에서 기본값을 뒤집으면**
  SQLite 가 뒤처지는 순간 한글 종목명 검색만 조용히 옛 자료를 본다.
  `tests/test_krx_pg.py::test_the_list_of_sqlite_bypassers_has_not_grown` 이 목록을 얼려 둔다.
- ⚠️ **종목 이름이 최신값으로 접혀 있다.** `securities` 는 종목당 한 줄이라 기간 중 이름이
  바뀐 101종목의 옛 이름이 없다(ADR-DS-0014 §2 가 그렇게 정했다). `sector` 도 351종목이
  같다. 오늘 화면에는 안 드러난다 — 스냅샷은 최신 거래일을 보기 때문이다.
- ⚠️ **루프가 하나라 조회가 직렬화된다.** `window()` 의 `fetchall()` 이 166,057개 행 객체를
  루프 스레드에서 만드는 동안(300~450ms) 다른 DB 조회가 밀린다. 지금 이대로 둔다 —
  갈아야 할 이유가 실측으로 서기 전에 손잡이를 늘리면 복잡도만 는다. **S5 에서 다시 잰다.**
- ⚠️ **어휘 밖 값이 요청 시점에 터진다.** `store_backend()` 를 읽기 함수마다 부르므로,
  배포본에 오타를 넣으면 화면이 전부 500 이 된다. `app_env()` 의 예외는 엔진을 만들 때만
  나므로 폭발 범위가 다르다. 지금은 배포본에 이 변수가 **없고**(기본 `sqlite`) S6 이전에는
  들어갈 일이 없어서 감수한다. **S6 에서 Vercel 에 이 변수를 넣을 때 기동 시점 검증을
  함께 넣는다.**
- `db` 낱말의 겹침이 그대로 남는다 — S7 의 일이다.

## 참조

- 모듈: ADR-DS-0011(전환 순서 · S4 의 완료 조건) · ADR-DS-0003 rev.2(커넥션 전략) ·
  ADR-DS-0009(출처 어휘 — `tier` 를 `db` 로 두는 근거) · ADR-DS-0010(`listed_shares` 두 자리) ·
  ADR-DS-0014(종목 속성을 접은 방식) · ADR-DS-0004(응답 상한)
- 전역: ADR-CT-0007(저장소 이원화) · ADR-CT-0010(유니버스 2단계)
- 실물: `app/repositories/krx_pg.py` · `app/core/db.py` §2-1 · `app/core/settings.py` §3 ·
  `app/repositories/krx_store.py`(이음매 여덟) · `tests/test_krx_pg.py` ·
  `tests/test_db.py` §7 · `tests/conftest.py`
