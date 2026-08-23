# ADR-DS-0003: 실행 환경에 따라 DB 커넥션 전략을 분기한다

## 상태

개정됨 (2026-08-23, rev.2) — 채택 2026-08-18

> **rev.2 에서 무엇이 바뀌었나 (2026-08-23).** 저장계층 전환 S2(ADR-DS-0011)에서 엔진을
> 실제로 붙여 보고 고친 것이다. 이 ADR 스스로 "값이 실제로 맞는지는 접속 코드를 쓰는 순간
> 처음 검증된다"고 적어 둔 항목을 닫는다.
>
> **① 결정이 바뀌었다 — 배포본 손잡이가 셋에서 넷이 됐다.** `statement_cache_size=0` ·
> `prepared_statement_cache_size=0` · `NullPool` 에 **준비구문 이름 유일화**가 더해진다(§4·§5).
> 캐시 둘만 끈 상태는 **갓 띄운 풀러에서만 0건**이고, 잔여물이 쌓이면 **첫 질의부터 100%
> 실패**한다.
>
> **② 근거의 세부를 고쳤다.** "하나만 끄면 빈도만 준다"는 서술이 실측과 달랐다 —
> 조합마다 결과가 전혀 다르고 asyncpg 버전에 따라서도 갈린다.
>
> ⚠️ **이 개정 자체가 거짓 음성을 한 번 통과했다.** rev.2 초안은 깨끗한 pgbouncer 에
> 한 번 대 보고 "0건이니 됐다"고 적었다. 같은 함정을 다시 밟지 않도록 §근거에 그 사실을
> 남겨 둔다.

## 맥락

ADR-DS-0002 가 저장 계층을 Postgres 로 옮기기로 했다. 그 첫 줄을 쓰려면 **어디에 어떻게
붙을지**부터 정해야 하는데, 이 레포는 그 정보를 읽을 자리가 없다.

실측 (2026-08-18):

| | 실물 |
|---|---|
| `APP_ENV` · `DATABASE_URL` | `compose.yaml:21-22` 에만 있다. `app/` 아래 **0건** |
| `app/core/settings.py` | **없다** (`__init__` · api_docs · parallel · paths · secrets · trading_calendar 뿐) |
| DB 접속 코드 | 없다 (`asyncpg` · `sqlalchemy` 를 import 하는 파일 0건) |

그런데 "여기가 배포본인가"를 묻는 코드는 **이미 세 곳에 흩어져 있다.** 셋이 서로 다른
방법으로 묻는다.

- `app/services/research/stages.py:287` — `os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")`
- `app/repositories/krx_store.py:52` — `KRX_DB_PATH` 환경변수
- `app/repositories/krx_store.py:59-68` — `os.access(..., os.W_OK)` 로 **쓰기 가능 여부**를 보고 추정

셋 다 같은 질문의 다른 표현이고, 답이 갈리면 한 프로세스 안에서 서로 다른 환경으로 행동한다.

배포본은 Vercel 서버리스이고 저장소는 Supabase 다 (ADR-CT-0007). 이 조합은 로컬 Postgres 와
**커넥션 수명이 근본적으로 다르다.** 로컬 규칙을 그대로 들고 가면 동작하지 않는다.

상위 문서는 ①저장계층 → ②`APP_ENV` 순서로 적었지만 **순서가 반대다.** 접속 코드 첫 줄부터
이 분기가 필요하므로 설정 진입점을 먼저 세운다.

## 결정

1. **`app/core/settings.py` 를 환경 읽기의 단일 진입점으로 둔다.** `paths.py` 가 경로의
   기준점인 것과 같은 자리다. 새 코드는 `os.getenv` 를 직접 부르지 않고 `env()` 를 거친다.
   기존 세 곳은 **그대로 둔다** — 각각 저장계층 전환(0002)·리서치 경계(0007) 작업에서 함께 옮긴다.
2. **`APP_ENV` 어휘는 `local` · `vercel` 둘뿐이다.** staging 이 생기면 이 ADR 을 개정한다.
3. **읽는 순서**: ① `APP_ENV` 명시값 → ② `VERCEL`·`VERCEL_ENV` 자동 감지 → ③ `local`.
   어휘 밖 값(`production` 등)은 조용히 넘기지 않고 **예외로 멈춘다.**
4. **커넥션 전략을 이렇게 가른다.**

   | | `local` | `vercel` |
   |---|---|---|
   | 포트 | **5432** 직결 | **6543** (transaction 모드 풀러) |
   | 풀 | 정상 풀 | **`NullPool`** |
   | `statement_cache_size` | 그대로 | **`0`** |
   | `prepared_statement_cache_size` | 그대로 | **`0`** |
   | 준비구문 이름 | 기본(커넥션별 카운터) | **커넥션마다 유일** ★ rev.2 |

   ★ 네 번째는 값이 아니라 **함수**다(`prepared_statement_name_func`). 그래서 `settings.py` 는
   `unique_statement_names: bool` 이라는 **사실**만 들고, 이름을 만드는 함수는
   `app/core/db.py` 가 준다 — §6 의 경계와 같은 이유다.

5. **배포본 쪽 넷은 한 벌이다.** 하나만 빠져도 prepared statement 충돌이 난다.
   따로 켜고 끄지 않는다.
   ⚠️ **특히 네 번째를 빼면 "되는 것처럼 보인다."** 갓 띄운 풀러에서는 실제로 0건이고,
   잔여물이 쌓인 뒤에야 **첫 질의부터 전부** 실패한다. 실측표는 §근거에 있다.
6. **엔진 생성은 이 ADR 범위 밖이다.** `settings.py` 는 SQLAlchemy 를 import 하지 않고
   `use_null_pool: bool` 같은 **사실**만 내놓는다. 설정을 읽는 것만으로 무거운 의존성이
   딸려 오면 아직 없는 계층에 이 파일이 묶인다.
7. **`secrets.py` 와 경계를 나눈다.** 묻는 질문이 다르다.

   | | `secrets.py` | `settings.py` |
   |---|---|---|
   | 무엇 | 외부 API 인증키 | 이 프로세스가 어디서 도는가 |
   | 어디서 | 환경변수 → `.env` → `.key` | **환경변수만** |
   | 없으면 | 그 API 만 503, 서버는 뜬다 | 기본값으로 떨어지거나 즉시 실패 |

   `DATABASE_URL` 은 비밀번호를 품지만 `settings.py` 소관이다 — **접속 전략의 일부**이고,
   `.key` 처럼 파일로 흘리면 안 된다.
8. **`DATABASE_URL` 은 로그·화면에 원문으로 싣지 않는다.** `safe_url()` 로 비밀번호만 가리고
   호스트·포트·DB 이름은 남긴다. "어디에 붙으려 했는가"는 읽을 수 있어야 한다.

## 근거

- **6543 이 곧 transaction 모드다.** Supabase 문서가 포트로 모드를 가른다 — 5432 는 직결·session
  모드, 6543 은 transaction 모드다. 서버리스에 대해 "Use pooler transaction mode for
  application traffic from temporary clients (for example, serverless or edge functions)" 로
  명시한다. 서버리스는 인스턴스가 수십~수백 개로 흩어지므로 직결하면 Postgres 의
  `max_connections` 를 금방 소진한다.
- **그 모드에서는 prepared statement 를 쓸 수 없다.** 같은 문서가 "Transaction mode does not
  support prepared statements. To avoid errors, turn off prepared statements for your
  connection library" 라고 못박는다. transaction 모드는 트랜잭션 단위로 서버 커넥션을
  갈아 끼우므로, 앞선 호출이 만든 `__asyncpg_stmt_1__` 이 다음 호출에서는 없는 이름이 된다.
  asyncpg 문서도 같은 말을 한다 — pgbouncer 를 transaction·statement 모드로 쓰면
  prepared statement 가 동작하지 않는다.
- **⭐ 캐시가 두 겹이라 하나만 끄면 낫지 않는다.** 이름이 비슷하지만 **주인이 다르다.**

  | 손잡이 | 주인 | 기본값 | 끄는 법 |
  |---|---|---|---|
  | `statement_cache_size` | asyncpg 자신의 LRU | 100 | `connect_args` 로 `0` |
  | `prepared_statement_cache_size` | **SQLAlchemy 의 asyncpg 방언**이 DBAPI 커넥션마다 하나 더 두는 캐시 | 100 | `connect_args` 또는 URL 쿼리로 `0` |

  ⚠️ **rev.2 정정.** 채택 당시에는 "하나만 끄면 오류 **빈도만** 낮아진다"고 적었다.
  실측해 보니 **그 서술이 틀렸다.** 하나만 끈 결과는 "덜 난다"가 아니라 **조합마다 전혀
  다르다** — 어떤 칸은 완전히 낫고, 어떤 칸은 그대로이며, 어떤 칸은 **오류 종류가 바뀐다.**

  실측 환경 (2026-08-23): pgbouncer 1.25.2 · `pool_mode=transaction` ·
  **`max_prepared_statements=0`** · `default_pool_size=2`, Postgres 16.14,
  SQLAlchemy 2.0.52 · asyncpg 0.30.0. 각 칸 200회.

  | 손잡이 | NullPool | QueuePool(3) |
  |---|---|---|
  | 아무것도 안 끔 | 87건 실패 | 96건 실패 |
  | `statement_cache_size` 만 `0` | 0건 | **94건 실패** |
  | `prepared_statement_cache_size` 만 `0` | 87건 실패 | 7건 실패 |
  | **둘 다 `0`** | **0건** | **0건** |

  실패 문구는 `InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_1b__"
  does not exist` 와 `DuplicatePreparedStatementError: … already exists` 두 가지다.
  **결론은 그대로다 — 둘 다 `0` 인 칸만 모든 조합에서 0건이다.** 다만 그 이유가 "빈도"가
  아니라 "조합 의존"이라는 것이 rev.2 의 정정이다.

- **⭐ 왜 하나만 끄는 최적화를 하면 안 되는가 — 답이 asyncpg 버전에 달려 있다.**
  `statement_cache_size` 는 캐시 크기만 정하는 값이 아니라 **준비구문에 이름을 붙일지**까지
  결정하는데, 그 규칙이 버전 사이에 바뀌었다(`asyncpg/connection.py` 의 `_prepare`).

  | asyncpg | `name=None` 일 때 | `statement_cache_size=0` 이면 |
  |---|---|---|
  | 0.30.0 | `named=True if name is None else name` | 그래도 **이름을 붙인다** |
  | 0.31.0 | `if name is None: name = self._stmt_cache_enabled` | **익명**이 된다 |

  즉 0.31 에서는 `statement_cache_size=0` 하나로도 위 표가 전부 0건이 되지만, 0.30 에서는
  **`QueuePool` 칸이 94건 실패한다.** 한쪽만 끄는 구성은 "지금 버전에서 우연히 되는" 것이고,
  의존성을 올리는 날 조용히 무너진다. 그래서 §5 의 "한 벌"은 편의가 아니라 **버전 독립성**이다.
  (이 레포는 휠 호환 범위 때문에 `asyncpg==0.30.0` 을 쓴다 — `pyproject.toml` 참조.)

- **⭐ 자리와 타입이 정해져 있다. 옮기면 두 방식으로 죽는다.**

  | 어디에 두나 | 결과 |
  |---|---|
  | `connect_args={...: 0}` | ✅ 정답 (둘 다) |
  | `create_async_engine(url, prepared_statement_cache_size=0)` | ❌ `TypeError: Invalid argument(s)` — 방언 인자가 아니라 **DBAPI 인자**다 |
  | URL 쿼리 `?statement_cache_size=0` | ❌ 문자열 `"0"` 로 도착 → asyncpg 가 `"0" < 0` 을 시도해 `TypeError` |

  URL 쿼리에서 int 로 강제되는 키는 `prepared_statement_cache_size` 하나뿐이다
  (방언의 `create_connect_args` 가 그 키에만 `coerce_kw_type` 을 건다). 맨
  `statement_cache_size` 는 강제 대상이 아니다. `tests/test_db.py` 가 자리와 타입을 얼려 둔다.

- **⭐⭐ 캐시를 둘 다 꺼도 이름은 계속 붙는다 — 그래서 네 번째 손잡이가 필요하다.**

  rev.2 초안은 여기에 "`prepared_statement_name_func`(uuid4)는 쓰지 않는다"고 적었다.
  근거는 "캐시를 둘 다 끄므로 위 표에서 이미 0건"이었다. **그 관찰이 거짓 음성이었다.**

  위 표는 **갓 띄운 pgbouncer**에서 잰 것이다. asyncpg 0.30 은 캐시를 꺼도 준비구문에
  이름을 붙이고(`connection.py` 의 `named=True if name is None else name`), 그 이름은
  `__asyncpg_stmt_1__` 처럼 **커넥션마다 1부터 다시 세는 카운터**다. 서버리스는 호출마다
  커넥션이 새로 열리므로 **매번 같은 이름을 다시 쓴다.** transaction 모드 풀러 뒤에서는
  DEALLOCATE 가 다른 물리 커넥션으로 갈 수 있어 이름이 서버에 남고, 그 뒤로는 새로 붙는
  모든 커넥션이 그 이름에 부딪힌다.

  잔여물이 쌓인 뒤 같은 풀러에 다시 쟀다 (호출마다 엔진을 새로 만드는 **서버리스 모양** · 24회):

  | 조합 | 실패 |
  |---|---|
  | 캐시 둘 다 0 (rev.2 초안의 전략) | **24건 — 전부** |
  | 이름 함수만 (캐시는 켠 채) | 23건 |
  | **캐시 둘 다 0 + 이름 함수** | **0건** |

  실패는 `DuplicatePreparedStatementError: prepared statement "__asyncpg_stmt_1__" already
  exists` 이고, SQLAlchemy 의 방언 초기화 질의(`select pg_catalog.version()`)에서 터진다 —
  즉 **첫 요청부터 전부 죽는다.** "산발적"이 아니라 "그 시점 이후 전부"다.

  SQLAlchemy 문서는 이것을 이미 적어 두었다 — PgBouncer 절이 `NullPool` 과 함께
  `connect_args={"prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__"}` 를 권한다.
  초안은 그 문단을 읽고도 "우리는 캐시를 끄니 해당 없다"로 넘겼다. **캐시와 이름은 다른
  축이다.**

- **⭐ 왜 이 고장을 처음에 못 봤는가 — 거짓 음성의 구조.** 두 겹이었다.
  ① 검증 대상이 **깨끗한 풀러**였다. 이 고장은 잔여물이 쌓인 뒤에만 나타난다.
  ② 부하 검사가 **엔진 하나를 돌려 썼다.** 이름 카운터는 커넥션마다 1부터 다시 세므로,
     커넥션이 새로 열려야 이름이 부딪힌다. 엔진을 재사용하면 그 상황이 만들어지지 않는다.
  `scripts/check_db_connection.py` 는 이제 **호출마다 엔진을 새로 만든다.** 그 한 줄이
  이 검사의 전부다 — 재사용으로 되돌리면 검사가 조용히 무력해진다.

- **⭐ "transaction 모드면 준비구문이 안 된다"는 상대에 따라 다르다.** PgBouncer 는
  1.21 부터 transaction 모드에서 준비구문을 대신 관리한다 — 실측한 1.25.2 의 기본값은
  `max_prepared_statements = 200` 이었고, **그 상태에서는 위 표의 모든 칸이 0건이었다.**
  그 값을 `0` 으로 낮춘 뒤에야 표의 실패가 나타났다. Supabase 문서는 Supavisor 에 대해
  준비구문 미지원을 명시하므로 우리는 `0` 쪽을 전제로 잡았다.
  ⚠️ **이것은 로컬 pgbouncer 로 검증할 때의 함정이다** — 기본값 그대로 세우면 틀린 전략도
  통과한다. `scripts/check_db_connection.py` 로 상대를 바꿔 가며 재는 이유가 이것이다.
- **`NullPool` 인 이유는 두 가지다.** ① 서버리스 인스턴스는 호출 사이에 얼었다 녹고 언제든
  회수된다. 풀이 들고 있던 소켓은 다음 호출에서 이미 죽어 있다. ② 앞단의 Supavisor 가
  이미 풀이라, 그 위에 풀을 또 얹으면 이중 풀이 되어 커넥션 회계가 어긋난다.
- **기본값을 정적 `local` 로 두면 안 되는 이유.** 배포 환경변수에서 `APP_ENV` 를 한 번
  빠뜨리면 배포본이 **조용히** 로컬 전략(5432 + 정상 풀 + 캐시 켬)으로 뜬다. 그 결과는
  즉사가 아니라 위의 산발적 실패라 가장 찾기 어렵다. `VERCEL` 은 플랫폼이 항상 넣어 주는
  변수이므로 감지가 확실하다(이미 `stages.py:287` 이 그 사실에 기대고 있다).
  반대로 기본을 `vercel` 로 두면 로컬이 6543 으로 붙으려다 즉시 실패한다 — 시끄러워서 안전하지만
  개발 기계마다 `APP_ENV` 를 요구하게 된다. 그래서 **감지 우선 + `local` 폴백**이다.
- **상수가 아니라 함수로 노출하는 이유.** `paths.py` 는 모듈 상수다. 경로는 파일 위치가
  정하므로 import 시점에 확정되고 변할 일이 없다. 환경은 프로세스마다 다르고 테스트가
  `monkeypatch.setenv` 로 바꿔 가며 분기를 확인한다. 상수로 두면 첫 import 에 얼어붙어
  **한 프로세스 안에서 두 갈래를 볼 수 없다.**
- **포트를 자동으로 고치지 않는 이유.** `expected_port` 는 알려 주기만 한다. 사용자가 적은
  `DATABASE_URL` 을 말없이 6543 으로 바꾸면, 설정 화면에 적힌 값과 실제로 붙는 곳이 갈린다.

## 결과

**쉬워지는 것**

- 접속 코드가 분기를 몰라도 된다. `database_settings()` 가 주는 값을 그대로 엔진에 넘긴다.
- ADR-DS-0004 가 미뤄 둔 `/api/exports/*` 의 `APP_ENV=local` 조건부 등록도 같은 문을 쓴다.
  그때 새로 환경을 읽을 필요가 없다.
- 배포본에서 `DATABASE_URL` 을 빠뜨리면 **즉시** 안내와 함께 멈춘다. compose 전용 호스트인
  `@db` 를 물고 떠서 정체 모를 이름 해석 실패가 되는 일이 없다.

**어려워지는 것 · 남는 숙제**

- **환경을 읽는 곳이 당분간 넷이다.** `settings.py` 외에 세 곳이 남아 있다(§결정 1).
  `tests/test_settings.py` 가 그 목록을 얼려 두어 **새로 늘어나는 것만** 잡는다.
  지금 옮기면 24개 파일짜리 리서치 하네스를 건드리게 되어 이번 범위를 넘는다.
- `krx_store` 의 `os.access` 쓰기 감지(krx_store.py:59-68)는 `APP_ENV` 와 **다른 질문**이다
  — "배포본인가"가 아니라 "이 폴더에 쓸 수 있나"다. 저장계층이 Postgres 로 가면
  질문 자체가 사라지므로(ADR-DS-0002) 지금 통합하지 않는다.
- 어휘가 둘뿐이라 staging·preview 를 따로 다뤄야 할 때 이 ADR 을 개정해야 한다.
  Vercel 의 preview 배포는 지금 `vercel` 로 접힌다.
- ~~**이 결정은 아직 실행되지 않았다.**~~ **rev.2 에서 닫혔다** (2026-08-23).
  `app/core/db.py` 가 섰고, 전략 넷(포트·풀·캐시 둘·이름 유일화)이 엔진까지 도착하는 것을
  `tests/test_db.py` 가, 실제로 통하는 것을 `scripts/check_db_connection.py` 가 잰다.
  **잔여물이 쌓여 이전 전략이 100% 실패하던 그 풀러**에서 새 전략이 60회 중 0건이다.
  ⚠️ 다만 **아직 Supabase 실물에는 대 보지 않았다.** 검증에 쓴 것은 로컬 pgbouncer 1.25.2 다.
  S6(ADR-DS-0011)에서 같은 스크립트를 Supabase 주소로 한 번 더 돌린다.
  ⚠️ **`prepared_statement_name_func` 는 표준 SQL 이 아니라 SQLAlchemy asyncpg 방언의 것**이라,
  드라이버를 바꾸면(psycopg3 등) 같은 자리가 없다. 그때 이 ADR 을 다시 연다.
- ⚠️ **`expected_port` 는 단순화다.** Supabase 의 접속 경로는 넷이고(직결 `:5432` ·
  Shared Pooler session `:5432` · Shared Pooler transaction `:6543` · Dedicated Pooler
  `:6543`), **6543 이라는 숫자만으로는 상대가 Supavisor 인지 전용 PgBouncer 인지 모른다.**
  둘은 준비구문 처리가 다르다(위 §근거 마지막 항목). 우리 전략은 둘 다에서 안전한 쪽이라
  이 단순화가 지금은 해가 없지만, `expected_port` 를 "모드 판정"으로 읽지는 않는다 —
  그것은 **경고 문구를 고르는 데만** 쓰인다.

## 참조

- 전역: ADR-CT-0007(저장소 이원화 — 배포본이 Supabase 인 근거)
- 모듈: ADR-DS-0002(저장계층 전환 — 이 분기가 필요한 이유) ·
  ADR-DS-0004(`/api/exports/*` 의 `APP_ENV=local` 조건부 등록) ·
  **ADR-DS-0011(전환 순서 — rev.2 를 낳은 S2 가 그 두 번째 걸음이다)**
- Supabase, "Connect to your database" — 포트별 모드와 서버리스 권장,
  transaction 모드의 prepared statement 제약
  <https://supabase.com/docs/guides/database/connecting-to-postgres>
- asyncpg, "Frequently Asked Questions" — pgbouncer transaction·statement 모드와
  `statement_cache_size=0` <https://magicstack.github.io/asyncpg/current/faq.html>
- SQLAlchemy, asyncpg 방언 — `prepared_statement_cache_size` 와 풀러 사용 시 지침
  <https://docs.sqlalchemy.org/en/20/dialects/postgresql.html>
- 실물: `app/core/settings.py` · `tests/test_settings.py` ·
  **`app/core/db.py` · `tests/test_db.py` · `scripts/check_db_connection.py`** (rev.2)
- PgBouncer, "Config — `max_prepared_statements`" (1.21+ 의 transaction 모드 준비구문 지원)
  <https://www.pgbouncer.org/config.html>
