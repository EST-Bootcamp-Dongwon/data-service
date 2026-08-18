# ADR-DS-0003: 실행 환경에 따라 DB 커넥션 전략을 분기한다

## 상태

채택됨 (2026-08-18)

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

5. **배포본 쪽 넷은 한 벌이다.** 하나만 빠져도 prepared statement 충돌이 **산발적으로** 난다.
   따로 켜고 끄지 않는다.
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

  첫 번째만 끄면 두 번째가 남아 **오류 빈도만 낮아지고 사라지지 않는다.** 이것이
  "산발적으로 난다"의 정체다 — 빈도가 줄어든 것을 "고쳐졌다"로 읽고 넘어갔다가, 한참 뒤
  재현 안 되는 오류로 다시 만나 엉뚱한 곳을 파게 된다. 둘 다 `0` 이어야 끝난다.
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
- **이 결정은 아직 실행되지 않았다.** 커넥션 전략을 *표명*했을 뿐 엔진이 없어서, 값이 실제로
  맞는지는 접속 코드를 쓰는 순간 처음 검증된다. 그때 6543 접속과 캐시 설정을 실측한다.

## 참조

- 전역: ADR-CT-0007(저장소 이원화 — 배포본이 Supabase 인 근거)
- 모듈: ADR-DS-0002(저장계층 전환 — 이 분기가 필요한 이유) ·
  ADR-DS-0004(`/api/exports/*` 의 `APP_ENV=local` 조건부 등록)
- Supabase, "Connect to your database" — 포트별 모드와 서버리스 권장,
  transaction 모드의 prepared statement 제약
  <https://supabase.com/docs/guides/database/connecting-to-postgres>
- asyncpg, "Frequently Asked Questions" — pgbouncer transaction·statement 모드와
  `statement_cache_size=0` <https://magicstack.github.io/asyncpg/current/faq.html>
- SQLAlchemy, asyncpg 방언 — `prepared_statement_cache_size` 와 풀러 사용 시 지침
  <https://docs.sqlalchemy.org/en/20/dialects/postgresql.html>
- 실물: `app/core/settings.py` · `tests/test_settings.py`
