# ADR-DS-0011: 저장계층 전환을 아홉 걸음으로 쪼개고 그 순서를 고정한다

## 상태

채택됨 (2026-08-23)

## 맥락

ADR-DS-0002 가 "파일 캐시를 Postgres 로 옮긴다"를 정했고 `sql/init/` 에 DDL 이 섰다.
그런데 **무엇을 먼저 하느냐**는 아무 데도 적혀 있지 않다. 한 번에 옮기는 것은 불가능하다.

실측 (2026-08-23):

| | 실물 |
|---|---|
| `app/repositories/krx_store.py` | 26,104 B. `init_db()`(:146)를 **9곳**에서 부른다 (167·214·265·306·320·342·425·501·528) |
| 3단 분기 | `krx_store.tier()`(:275-285)가 `db`·`bundle`·`live` 를 낸다. 화면 배지·리포트 근거 문구·`dashboard_data.py:317-342` 까지 전파돼 있다 |
| 대체 저장소 | `krx_bundle.db`(30MB) · `market_snapshot.json.gz` · `tmp_cache` — 원본이 없을 때의 대타가 **셋** |
| 파생 지표 | `market_snapshot` 의 `r60`·`r120`·`r250`·`per`·`pbr`·`roe` 는 원천이 아니다. 재계산 경로가 **없다** |
| 배포본 | `data/krx_cache.db`(123MB)가 git 에도 배포본에도 없다. 배포본은 번들·스냅샷으로만 돈다 |

즉 **저장소를 바꾸는 일이 아니라, 저장소가 바뀌어도 화면이 같은 말을 하게 만드는 일**이다.
그리고 이 레포에는 **마이그레이션 러너가 없다** — `sql/init/*.sql` 은 빈 볼륨 최초 기동에만
돈다(01-schema.sql:3-5). 적재를 시작한 뒤 컬럼 하나를 고치는 것은 전면 재적재다.

한 커밋으로 밀면 되돌릴 단위가 없어진다. 고장이 나도 "엔진 탓인가 질의 탓인가 어휘 탓인가"를
가릴 수 없고, 배포본은 push 가 곧 배포라(GitLab→Vercel) 그 상태가 그대로 나간다.

## 결정

1. **아홉 걸음으로 쪼갠다. 각 걸음은 그 자체로 되돌릴 수 있어야 한다.**

   | 걸음 | 하는 일 | 완료 조건 | 상태 |
   |---|---|---|---|
   | **S1** | 적합성 자 — SQLite 원본이 목표 DDL 을 통과하는지 잰다 | `check_migration_fitness.py` 가 치명 0 · `test_schema_fitness.py` 초록 | ☑ 2026-08-22 (`2d52be7`) |
   | **S2** | 엔진 계층 — `app/core/db.py` + `sqlalchemy[asyncio]`·`asyncpg` | 실 DB 없이 `invoke check` 초록 · 아래 §S2 재현 절차가 (a)(c) 0건 · (b) 실패 | ☑ 2026-08-23 |
   | **S3** | 일회성 적재기 — `scripts/load_pg.py` (SQLite → Postgres) | 780,484행이 들어가고 행수·합계가 원본과 일치 | ☐ |
   | **S4** | 읽기 어댑터 + `STORE_BACKEND` 스위치 (**기본은 `sqlite`**) | 스위치를 켠 상태로 계약 스냅샷 51경로가 그대로 | ☐ |
   | **S5** | 로컬 기본값 뒤집기 (`STORE_BACKEND=postgres`) | 로컬 화면 10개가 Postgres 로만 돈다 | ☐ |
   | **S6** | Supabase (core 유니버스만 · ADR-CT-0010) | 배포본이 DB 를 읽는다 | ☐ |
   | **S7** | `bundle`·`snapshot` 폐기 + 출처 어휘 정리 | `tier()` 3단 분기 제거 · `test_source_vocabulary.py` 개정 | ☐ |
   | **S8** | 쓰기 경로 — 수집이 Postgres 에 적재한다 | `ohlcv_sync_log` 의 `rows=0` 규칙이 살아 있다 | ☐ |
   | **S9** | 잔가지 — 파생 지표 재계산 · `trading_calendar` 채우기·읽기 | `weekday() < 5` 근사가 사라진다 | ☐ |

2. **S4 는 스위치를 `sqlite` 기본으로 넣는다.** 어댑터를 넣는 커밋과 기본값을 바꾸는
   커밋을 나눈다(S4/S5). 그래야 "어댑터가 틀렸다"와 "전환이 이르다"가 따로 되돌려진다.

3. **읽기(S3~S5)를 쓰기(S8)보다 먼저 한다.** 적재는 일회성 스크립트로 시작하고,
   수집 경로를 바꾸는 것은 읽기가 전부 Postgres 로 돈 뒤다.

4. **`bundle` 폐기(S7)는 배포본이 DB 를 읽은 뒤(S6)에만 한다.** 순서를 뒤집으면
   배포본이 조용히 빈 화면이 된다 — 지금 배포본은 번들로만 돌기 때문이다.

5. **S2 는 아무도 import 하지 않는 상태로 끝낸다.** `tests/test_db.py` 가 그것을 얼려 두고,
   **S4 에서 그 테스트를 지우는 것이 곧 "이제 연결했다"는 표시**다.

6. **새 ADR 번호를 미리 예약하지 않는다.** 전환 중 결정이 생기면 그때 다음 번호를 쓴다.
   (예정으로만 적어 둔 것: `securities` 승격 · `bundle` 폐기 — 확정되면 번호를 받는다.)

### S2 재현 절차 — 레포 안에서 끝난다

검증 상대(transaction 모드 풀러)를 `compose.yaml` 의 `pooler` 프로파일로 넣어 뒀다.
**`max_prepared_statements=0` 이 그 컨테이너의 존재 이유다** — PgBouncer 1.21+ 의 기본값
(200)으로 세우면 틀린 전략도 통과한다.

```bash
docker compose --profile pooler up -d
V=postgresql+asyncpg://postgres:postgres@localhost:6543/data_service

# (a) 갓 띄운 풀러 · 배포본 전략        → 0건이어야 한다
APP_ENV=vercel DATABASE_URL=$V python3 scripts/check_db_connection.py

# (b) 로컬 전략을 풀러에 대 본다        → **실패해야 한다** (검사기가 살아 있다는 증거)
APP_ENV=local  DATABASE_URL=$V python3 scripts/check_db_connection.py

# (c) (b) 가 남긴 잔여물 위에서 다시    → 여전히 0건이어야 한다  ★ 여기가 핵심
APP_ENV=vercel DATABASE_URL=$V python3 scripts/check_db_connection.py
```

⚠️ **(c) 가 없으면 검증이 아니다.** (a) 만 보면 이전 전략(캐시 둘만)도 0건을 낸다.
그 거짓 음성이 ADR-DS-0003 rev.2 초안을 한 번 통과했다.

## 근거

- **엔진이 어댑터보다 먼저인 이유 — 실패를 가릴 수 있어야 한다.** 둘을 한 커밋에 넣으면
  붙지 않을 때 커넥션 설정 탓인지 질의 탓인지 모른다. 이 이유가 S2 에서 그대로 값을 했다 —
  ADR-DS-0003 이 표명해 둔 배포본 전략에 **손잡이 하나가 통째로 빠져 있었다**는 것이
  드러났다(ADR-DS-0003 rev.2 §4). 증상은 `DuplicatePreparedStatementError` 가 **첫 질의부터**
  나는 것이라, 어댑터와 섞였으면 "질의가 틀렸나" 를 먼저 의심하며 시간을 썼을 것이다.
- **⚠️ S2 에서 배운 것 — 깨끗한 상대에 한 번 대 보는 것은 검증이 아니다.** 그 전략은 갓 띄운
  풀러에서 60회 0건이었고, 잔여물이 쌓인 같은 풀러에서 24회 24건 실패였다. **한 번 초록**과
  **반복해도 초록**은 다르다. S3 이후의 검증도 같은 기준으로 한다.
- **S1 을 먼저 한 이유가 그대로 S3 에도 적용된다.** 마이그레이션 러너가 없으므로 DDL 을
  고치는 비용은 적재 전에는 0 이고 적재 후에는 전면 재적재다. 실제로 S1 이
  `change_rate numeric(8,4)` 로는 못 담는 행 **1개**(29948.08)를 780,484행 중에서 찾아냈다.
- **스위치를 `sqlite` 기본으로 두는 이유.** 배포는 push 가 곧 배포다(GitLab→Vercel).
  기본값을 바꾸는 커밋이 어댑터 커밋과 같으면, 어댑터에 결함이 있을 때 되돌릴 단위가
  "전부"뿐이다. 나눠 두면 `STORE_BACKEND` 를 되돌리는 것만으로 화면이 살아난다.
- **읽기가 쓰기보다 먼저인 이유.** 읽기는 틀리면 화면이 빈다. 쓰기는 틀리면 **자료가
  틀어진 채 쌓인다.** 되돌리는 비용이 다르다.
- **`bundle` 을 마지막에 버리는 이유.** `krx_bundle.available()`(krx_bundle.py:69)은 파일
  존재만 보고 False 를 돌려주므로, 없으면 500 이 아니라 **조용한 기능 강등**이 된다.
  대타를 먼저 치우면 무엇이 깨졌는지 알아채는 데 오래 걸린다.
- **아홉으로 나눈 근거는 되돌림 단위다.** 더 잘게 쪼개면 걸음마다 계약이 안 바뀌어
  검증할 것이 없고, 더 크게 묶으면 되돌릴 때 멀쩡한 것까지 함께 돌아간다.

## 결과

**쉬워지는 것**

- 걸음마다 `invoke check` 로 닫힌다. "저장계층 전환 중"이라는 애매한 상태가 없다.
- 실패했을 때 되돌릴 커밋이 하나로 특정된다.
- S2 가 닫히면서 접속 문제와 질의 문제가 영구히 갈렸다 —
  `python3 scripts/check_db_connection.py` 가 접속 쪽만 따로 판정한다.
  ⚠️ 그 판정은 **호출마다 엔진을 새로 만드는** 데 기대고 있다(서버리스 모양). 재사용으로
  "정리"하면 검사가 조용히 무력해진다 — 초안이 그렇게 거짓 음성을 냈다.

**어려워지는 것 · 남는 숙제**

- **걸음이 아홉이라 오래 걸린다.** 중간 상태(S4)에서는 저장소가 둘이 되고, 그동안
  `STORE_BACKEND` 분기가 코드에 남는다. S7 에서 걷어낸다.
- ⚠️ **`db` 라는 낱말이 두 뜻으로 겹친다.** `app/core/db.py` 는 Postgres 를 뜻하는데,
  `krx_store.tier()` 가 내는 `"db"`(→ 출처 `krx-db`)는 **SQLite 원본 캐시**를 뜻한다
  (ADR-DS-0009). 화면이 그 문자열을 정확히 비교하므로(`krx.html`·`stock.html`),
  S7 에서 어휘를 정리할 때 **같은 낱말이 가리키는 저장소가 바뀐다는 사실**을 먼저 적는다.
  지금은 겹쳐도 깨지지 않는다 — `tests/test_source_vocabulary.py` 가 어휘 쪽을 따로 지킨다.
- ⚠️ **S4 가 시작되면 검사 격리가 먼저 필요하다.** `tests/conftest.py` 에 DB 픽스처가 없어서
  개발자 셸의 `DATABASE_URL` 을 그대로 물고 돈다. 그 값이 Supabase 면 검사가 배포 DB 에
  붙는다. S2 는 실 DB 를 안 열어서 무사하지만, 어댑터 검사는 그럴 수 없다.
- ⚠️ **S4 에서 `app/main.py:58-64` 의 흡수 구조를 조심한다.** 그 `try` 는
  `ModuleNotFoundError` 만, 라우터 3개만 감싼다. 그 안쪽에서 엔진 계층을 import 했다가
  의존성이 빠지면 **"야후 파이낸스 기능을 끕니다"** 라는 틀린 안내와 함께 엔드포인트
  10개가 사라진다. `test_contract.py:85` 의 `>= 40` 가드는 51→41 을 통과시키므로,
  이것을 잡는 것은 계약 스냅샷뿐이다.
- lifespan 에 `dispose_engine()` 을 걸어도 **검사에서는 한 번도 실행되지 않는다** —
  `conftest.py:45-53` 이 `TestClient` 를 `with` 없이 만든다(색인 15,414종목이 느려서).
  커넥션 누수는 검사로 못 잡는다. S4~S6 에서 손으로 확인한다.

## 참조

- 모듈: ADR-DS-0002(무엇을 옮기나) · ADR-DS-0003 rev.2(어떻게 붙나 · S2 실측) ·
  ADR-DS-0009(출처 어휘 — S7 의 대상) · ADR-DS-0010(`ohlcv.listed_shares`)
- 전역: ADR-CT-0007(저장소 이원화) · ADR-CT-0010(유니버스 2단계)
- 실물: `app/core/db.py` · `tests/test_db.py` · `scripts/check_db_connection.py` ·
  `scripts/check_migration_fitness.py` · `sql/init/01-schema.sql`
