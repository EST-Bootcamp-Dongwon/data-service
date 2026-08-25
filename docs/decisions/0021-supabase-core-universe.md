# ADR-DS-0021: 배포본이 DB 를 읽게 하고, `core` 유니버스를 대용이 아닌 실물로 세운다

## 상태

채택됨 (2026-08-25) · 저장계층 전환 **S6** (ADR-DS-0011)

## 맥락

S5 가 **로컬** 읽기 경로를 Postgres 로 뒤집었다(ADR-DS-0018). 배포본은 그대로 `sqlite` 였다 —
`DATABASE_URL` 이 없었고, 배포본에서 `database_url()` 은 기본값으로 때우지 않고 **예외를
던지기** 때문이다. 그래서 배포본에서는 셋이 잠겨 있었다:

| 잠긴 것 | 증상 |
|---|---|
| 시세 | `krx_bundle.db` 가 git 에 없어 `라이브 조회` 배지 (ADR-DS-0016) |
| 자료 보관함 | `clip` 이 Postgres 전용이라 `/stock` 패널이 잠김 (ADR-DS-0019) |
| 자동 수집 | `watermark`·`clip` 이 없어 `503` (ADR-DS-0020) |

셋이 **한 원인**을 공유한다. 그것이 S6 이 걸음 하나인 이유다.

그런데 S6 의 완료 조건("배포본이 DB 를 읽는다")만으로는 답이 안 나오는 질문이 하나 있었다 —
**무엇을 담을 것인가.** ADR-CT-0010 이 `core = KOSPI200 + KOSDAQ150` 이라 정했는데,
**그 구성종목 목록이 이 레포에 없었다.**

그 부재는 이미 두 번 값을 치렀다.

- ADR-DS-0014 는 `universe_tier` 를 **전부 `full` 로 두고 비웠다** — "추정을 사실로 굳히지
  않는다". 그래서 `securities_universe_idx` 는 세워 놓고 아무도 쓰지 않는 인덱스였다.
- ADR-DS-0020 의 일괄 수집은 **시가총액 상위 N 을 대용**으로 쓰면서, "이것은 core 가
  아니다" 를 로그·watermark·화면 **셋 다**에 실어야 했다. 그 코드의 주석이 이렇게 적혀 있다 —
  *"낱말을 `core` 로 쓰면 다음 사람이 구성종목이라고 믿는다."*

즉 S6 은 **저장소를 하나 더 세우는 일**이면서 동시에 **미뤄 둔 빈칸을 채우는 일**이었다.

## 결정

### 1. `core` 는 대용이 아니라 실물이다 — 그러나 자동 경로는 하나뿐이었다

먼저 **가져올 수 있는 문이 있는지** 실측했다. 이 레포는 이미 KRX 인증키를 갖고 있으므로
그쪽이 첫 후보다.

| 경로 | 실측 (2026-08-25) |
|---|---|
| `sto/stk_bydd_trd` (보유 키) | ✅ 942행 — 지수 소속 필드 **없음** |
| `sto/stk_isu_base_info` (보유 키) | ✅ 942행 — 지수 소속 필드 **없음** |
| `idx/kospi_dd_trd` · `idx/kosdaq_dd_trd` | ✅ 51행·40행 — **지수 자체의 종가**다 |
| `idx/idx_isu_base_info` | ❌ `404 API referenced by the path does not exist` |
| 네이버 금융 `entryJongmok` | KOSPI200 만 200종목. ❌ **`robots.txt` 가 `Disallow: /`** |
| `index.krx.co.kr` | ❌ 404 (사이트 개편) |
| data.krx.co.kr `MDCSTAT00601` | ⚠️ **로그인 세션 필요** |

**⚠️ 이 표의 요점은 "KRX 키가 있으니 되겠지" 가 틀렸다는 것이다.** `idx/*` 는 "코스피 200 이
오늘 몇 포인트인가" 를 주지 **"거기 무슨 종목이 들었나" 를 주지 않는다.** 그 필드가 스펙에
없다. 문이 둘이고, 우리가 가진 키는 다른 문의 것이다.

네이버 금융은 **쓰지 않는다.** `robots.txt` 가 모든 크롤러에 `Disallow: /` 이고 `/sise/`
허용은 네이버 자체 봇(`yeti`)에게만이다. 우리는 해당되지 않는다.

그래서 **KRX 정보데이터시스템 계정**(`KRX_ID`·`KRX_PW`)으로 간다. 익명 세션으로 워밍업해
`JSESSIONID` 를 받아도 400 이다 — pykrx 가 보내는 것과 **정확히 같은 네 파라미터**
(`bld`·`indIdx`·`indIdx2`·`trdDd`)를 같은 URL 로 보내도 그렇다. 진단이 **파라미터가 아니라
인증**이라는 것을 그렇게 갈랐다.

### 2. pykrx 를 의존성으로 들이지 않는다 — 프로토콜만 읽는다

pykrx 1.2.8 이 이 경로의 유일한 공개 구현이지만, 필요한 것은 **HTTP 두 번**(로그인·조회)인데
pandas 를 끌고 온다. 이 레포의 `app/clients/*.py` 아홉은 **전부 표준 라이브러리 urllib** 이고
(절대제약 5 — 이미지 용량), `scripts/build_universe.py` 도 같은 규칙을 따른다.

프로토콜 자체는 pykrx 의 `website/comm/auth.py` 와 `market/wrap.py:1267` 을 읽어 확인했다 —
지수 티커 `1028`·`2203` 에서 **앞 한 자가 group_id, 나머지가 지수코드**다.

### 3. 목록 파일은 `corp_code.json` 과 같은 취급이다

`data/universe_core.json` 을 **커밋한다**. 사람이 갱신하고, 낡으면 **막지 않고 알린다**.

⚠️ **낡음 기준을 90일로 둔다.** `corp_code` 는 7일인데 이쪽은 정기변경이 연 2회(6·12월)라
그보다 촘촘히 재촉하면 경고가 소음이 된다. **다만 90일을 "안전하다" 로 읽지 않는다** —
상장폐지·합병에 따른 수시변경은 그보다 잦다. 이 값은 **재촉 주기이지 정확성 보증이 아니다.**

⚠️ **종목 수를 세어 보고 예상(200·150)에서 ±5 를 넘으면 파일을 쓰지 않는다.** 응답이
잘렸거나 지수코드가 바뀐 것이다. **조용히 반쪽짜리 목록을 쓰는 것이 이 스크립트의 가장 비싼
고장**이라, 정말 맞다면 `--force` 를 요구한다. (ADR-DS-0020 §6 이 잘림으로 9종목을 잃은
전례가 이 가드의 근거다.)

⚠️ **`isdigit()` 로 거르지 않는다.** core 350 에 신형 종목코드가 **2건**(`0009K0`·`0126Z0`)
들어 있다. `build_corp_code.py` 가 정확히 그 결함으로 56건을 통째로 빼고 있었고
(ADR-DS-0020 §12), 같은 함정이 여기에도 있다.

### 4. **거르는 것과 딱지를 붙이는 것은 다른 일이다**

`scripts/load_pg.py` 에 `--universe {full,core}` 를 더했다. 그런데 둘은 축이 다르다.

- **거르기**(`--universe core`) — 무엇을 *담을* 것인가. 원격에만 쓴다.
- **딱지**(`universe_tier`) — 담은 것이 *무엇인가*. **로컬 full 적재에서도 붙는다.**

한 손잡이로 묶으면 로컬 정본의 `universe_tier` 가 영원히 `full` 로 남는다. 나눠 두었기에
로컬도 이번에 **core 350 / full 2,525** 로 갈렸고, `securities_universe_idx` 가 처음으로
뜻을 갖게 됐다.

⚠️ **`universe_tier` 는 `COALESCE` 가 아니라 그대로 덮는다.** 목록 파일이 정본이므로
편입·제외가 반영되려면 **core→full 로 내려가는** 갱신도 통해야 한다. `COALESCE` 로 두면
한 번 core 가 된 종목이 지수에서 빠져도 영원히 core 로 남는다.

⚠️ **`ohlcv_sync_log` 는 거르지 않는다.** 그 표는 종목이 아니라 **거래일** 단위이고
"KRX 가 그 날 몇 행을 줬나" 를 기록한다. 종목으로 거르면 `rows` 가 뜻을 잃고 휴장일
마커(rows=0) 규칙까지 흔들린다. 그래서 Supabase 의 `ohlcv` 는 103,663행인데
`ohlcv_sync_log` 의 `rows` 합은 821,928 이다 — **이 어긋남이 정상**이다.

### 5. `--allow-remote` 는 "전종목을 부어도 좋다" 가 아니다

가드를 **둘로 나눴다.**

```
원격인가?           → --allow-remote 가 없으면 막는다   (전부터 있던 가드)
원격인데 full 인가? → --universe core 를 요구한다        (이번에 더한 가드)
```

한 손잡이로 묶으면 "원격에 붙어도 좋다" 는 승인 하나에 **core 만 두기로 한 약속(ADR-CT-0007 ·
ADR-CT-0010)이 딸려 깨진다.** 승인의 범위를 승인한 것보다 넓히지 않는다.

### 6. 스키마는 손으로 옮기되 **지문으로 대조한다**

이 레포에는 마이그레이션 러너가 없고 `sql/init/*.sql` 은 빈 볼륨 최초 기동에만 돈다.
원격에는 그 기동이 없으므로 DDL 을 관리 API 로 올렸다 — **즉 사람이 옮겨 적은 것이다.**

옮겨 적은 것을 "잘 옮겼다" 로 믿지 않는다. 양쪽에서 카탈로그를 뽑아 **md5 로 대조**했다
(컬럼·제약·인덱스·파티션 넷). 넷이 다 맞아야 통과다. 아래 "검증" 참조.

⚠️ **버전이 다르다** — 로컬은 `pgvector/pgvector:0.8.2-pg16`, Supabase 는 **PostgreSQL 17.6**.
`format_type`·`pg_get_constraintdef`·`indexdef` 의 렌더링까지 같았지만, **같을 것이라고
가정한 것이 아니라 재서 알았다.** 다음에 어느 한쪽을 올릴 때 이 대조를 다시 돌린다.

### 7. 기본값을 뒤집되 **상수 둘을 하나로 합치지 않는다**

`DEFAULT_STORE_BACKEND_VERCEL` 을 `sqlite` → `postgres` 로 바꿨다. 이제 두 상수가 같은
값이다. **그래도 합치지 않는다** — push 가 곧 배포라(GitLab→Vercel) **배포본만** 되돌려야
하는 순간이 온다. 둘로 두면 그 되돌림이 한 줄이고, 합치면 로컬까지 함께 끌려 내려간다.
**되돌리는 단위가 곧 값이다.**

가장 빠른 되돌림은 여전히 환경변수 한 줄이다 — `STORE_BACKEND=sqlite`.
`tests/test_krx_pg.py` 가 그 되돌림이 살아 있는지 따로 검사한다.

### 8. **순서가 뜻을 가진다 — 환경변수가 먼저, push 가 나중**

배포본에서 `database_url()` 은 기본값으로 대신하지 않고 예외를 던진다. 그러므로

```
① Supabase 에 자료를 넣는다
② Vercel 에 DATABASE_URL(6543 풀러)을 넣는다
③ 그 다음에 기본값을 뒤집은 커밋을 push 한다
```

②와 ③을 바꾸면 **깨진 배포본이 잠깐 존재한다.** 실측으로 재 보았다 —
`APP_ENV=vercel` · `DATABASE_URL` 없음 · 기본값 `postgres` 에서:

| | 결과 |
|---|---|
| `/` · `/krx` (화면 HTML) | 200 — **뜬다** |
| `/api/krx/status` · `/api/krx/stocks` · `/api/stocks` · `/api/stocks/{code}/ohlcv` | **500** |
| `/api/clips/status` · `/api/collect/dart/status` | 200 (설계대로 — 상태는 언제나 200) |

⚠️ **"화면 10개가 500" 이 아니다.** HTML 은 뜨고 그 화면이 부르는 **시세 API 가 500** 이라,
겉보기에는 "화면은 열리는데 비어 있다" 가 된다. 그 편이 더 나쁘다 — 500 페이지는 눈에
띄지만 빈 화면은 자료가 없는 것처럼 보인다. S5 까지 이 값이 `sqlite` 였던 이유가 정확히
그것이고, S6 이 먼저 한 일이 ①②다.

(`settings.py` 의 옛 주석과 `test_krx_pg.py` 의 옛 docstring 은 "화면 10개가 500" 이라
적고 있었다. 재 보기 전에는 아무도 확인하지 않은 문장이었다.)

⚠️ **직결 주소는 쓸 수 없다.** `db.<ref>.supabase.co` 는 **IPv6 전용**이다(실측:
`2406:da12:557:f800::`). 6543 풀러(`aws-0-ap-northeast-2.pooler.supabase.com`)는 IPv4 다.
ADR-DS-0003 이 6543 을 고른 이유는 커넥션 수명이었는데, **이제 그것 말고 붙을 방법이 없다는
이유가 하나 더 붙었다.**

## 검증

### 스키마가 정말 같은가 — 카탈로그 지문 넷

`pg_attribute`·`pg_constraint`·`pg_indexes`·`relpartbound` 를 각각 한 줄씩 문자열로 만들어
정렬한 뒤 md5 를 냈다.

| 갈래 | 개수 | 로컬(pg16) | Supabase(pg17.6) |
|---|---|---|---|
| 컬럼 | 226 | `89c73463cc3d253ed88ebb87ecb9dba8` | 같음 ✅ |
| 제약 | 43 | `776a47f6c5393bbf0721590a32c02662` | 같음 ✅ |
| 인덱스 | 46 | `154dfb5808a79265cbd21b3e271a8454` | 같음 ✅ |
| 파티션 | 14 | `d47e0a53448033a52fa60264bf52283d` | 같음 ✅ |
| 전체 | — | `c35d1401141452e23d5dc1f8ab4f5090` | 같음 ✅ |

### 구성종목이 정말 왔는가

- KOSPI200 **200종목** · KOSDAQ150 **150종목** · 합 **350** · 중복 0
- core 350 중 로컬 `securities` 에 **없는 것 0** · SQLite `daily_price` 에 **없는 것 0**
- 신형 종목코드 **2건** 포함 (`0009K0`·`0126Z0`)
- DART 고유번호가 붙은 것 **350/350** · 산업분류 **348/350**

### 적재 — Supabase

`--universe core --allow-remote`, 배포본 커넥션 전략으로 붙었다.

- **103,663행 · 25초 · 4,863행/초** (인터넷 너머 · 로컬은 38,707행/초)
- 대조 **21항목 전부 일치** (행수·종목·거래일·합계 10종·휴장일 마커·securities 행수·파티션 그물)
- `securities` 350행 전부 `universe_tier='core'`

### 로컬 — 딱지가 붙었는가

full 재적재 뒤 **core 350 / full 2,525**, 대조 21항목 전부 일치.
core 종목의 `ohlcv` 103,663행 · `clip` 13,287건 (Supabase 와 같은 350종목이다).

### 커넥션 전략 — 실제 Supavisor 를 상대로 (a)(b)(c)

ADR-DS-0011 이 "**(c) 가 없으면 검증이 아니다**" 라고 한 절차를, 이번에는 로컬 pgbouncer 가
아니라 **진짜 상대**에게 돌렸다.

| | 전략 | 결과 |
|---|---|---|
| (a) | `APP_ENV=vercel` · 갓 붙은 풀러 | 60회 중 **실패 0건** ✅ |
| (b) | `APP_ENV=local` 을 풀러에 | **실패** — `InvalidSQLStatementNameError` ✅ (검사기가 살아 있다) |
| (c) | `APP_ENV=vercel` · (b)의 잔여물 위에서 | 60회 중 **실패 0건** ✅ ★ |

**손잡이 넷(NullPool · 캐시 둘 0 · 준비구문 이름 유일화)이 실제 배포 상대에서 확인됐다.**
ADR-DS-0003 rev.2 는 로컬 pgbouncer 실측이었고, 이제 표명이 실물로 닫혔다.

### 배포본 환경으로 화면·API 를 재 본다

`APP_ENV=vercel` + Supabase `DATABASE_URL`, `STORE_BACKEND` 는 **주지 않았다**(기본값을 잰다).

- 화면 **11/11** · API **13/13** — 합계 **24/24 가 200**
- `/api/krx/status` → `mode=db` · `cache.db_path=postgres.ohlcv` · 103,663행 · 297일 · 350종목 · 15.4MB
- `/api/krx/stocks` → `source=krx-db`
- `/api/clips/status` → **`available: true`** (보관함 잠금 풀림)
- `/api/collect/dart/status` → **`available: true`** (수집 잠금 풀림)

### 실제 배포본 — 모의가 아니라 실물에서

위 스모크는 로컬에서 배포본 환경을 **흉내 낸** 것이다. 배포 뒤 진짜 프로덕션을 다시 쟀다.

배포 전 (`mode` 가 `live` 였다 — ADR-DS-0016 이 말한 저장소 부재):

```
mode  : live
cache : {"rows": 0, "days": 0, "codes": 0, "db_path": "krx_cache.db", "db_size_mb": 0.0}
```

배포 후 (`https://api-test-sable-phi.vercel.app`):

```
mode  : db
cache : {"rows": 103663, "days": 297, "codes": 350,
         "first_date": "20250609", "last_date": "20260824",
         "db_path": "postgres.ohlcv", "db_size_mb": 15.4}
```

- 화면 11 + API 9 = **20/20 이 200** · `/api/krx/stocks` 의 `source` = **`krx-db`**
- `/api/clips/status` → `available: true` · `/api/collect/dart/status` → `available: true`
- `DART_API_KEY` 는 **이미 Vercel 에 있었다**(24일 전 등록). 배포본 수집이 실제로 돈다.

⚠️ **`DATABASE_URL` 은 Production 스코프에만 넣었다.** 이 레포는 main 직커밋이라 preview
배포가 생기지 않기 때문이다. 브랜치를 파는 날 preview 는 `DATABASE_URL` 없이 뜬다 —
그때는 화면이 열리고 시세만 비는 모양이 된다(§8 의 실측과 같다).

### 검사

`invoke check` 초록 (**396 tests** · +5). `tests/test_load_pg.py` 38개(+2) ·
`tests/test_krx_pg.py` 55개(+3 — 되돌림이 살아 있는지 보는 검사를 새로 넣었다).
`invoke docs-check` 초록. **계약은 60경로 그대로** — S6 은 공개 계약을 바꾸지 않았다.

## 기각한 대안

- **전종목 2,875 를 Supabase 에 붓는다.** 135MB 라 free 티어 500MB 에 들어가기는 한다.
  기각한 이유는 용량이 아니라 **ADR-CT-0007·ADR-CT-0010 이 이미 정한 저장소 이원화**다 —
  로컬이 정본(full)이고 원격은 데모용(core)이다. 계약을 바꾸려면 그 ADR 을 먼저 개정한다.
- **네이버 금융 `entryJongmok` 스크래핑.** KOSPI200 은 실제로 200종목이 나왔다.
  `robots.txt` 가 `Disallow: /` 라 기각했다. **되니까 한다** 는 근거가 아니다.
- **시총 상위 350 을 `core` 로 찍는다.** 가장 싸고, 마침 clip 16,286건과도 맞물린다.
  기각한 이유는 **컬럼이 거짓말을 하기 때문**이다. ADR-DS-0014 가 비워 둔 자리를
  추정으로 채우면, 다음 사람은 그것이 구성종목인 줄 안다.
- **pykrx 를 `scripts` 엑스트라로 들인다.** 자동 갱신이 되지만 HTTP 두 번을 위해 pandas 를
  끌고 온다. 프로토콜만 읽어 60줄로 옮겼다.
- **`ALTER ROLE postgres PASSWORD` 로 비밀번호를 직접 설정한다.** 시도했고 **실패했다** —
  Supabase 의 `postgres` 는 슈퍼유저가 아니다(`42501: Only superusers can alter privileged
  roles`). 대시보드에서만 된다. **기각이 아니라 막힌 것이고, 그 사실을 여기 적어 둔다.**

## 결과

**쉬워지는 것**

- 배포본에서 셋이 한꺼번에 풀렸다 — 시세·보관함·수집.
- `universe_tier` 가 **처음으로 참**이다. ADR-DS-0020 의 대용 문구
  (`"universe_tier 가 전부 full 이라 대용이다"`)를 걷어낼 근거가 생겼다.
- S7(`bundle`·`snapshot` 폐기)의 전제가 섰다 — ADR-DS-0011 §4 가 "S6 뒤에만" 이라 못박은 것.

**어려워지는 것 · 남는 숙제**

- ⚠️ **배포본의 `clip` 은 비어 있다.** 표는 서 있고 잠금은 풀렸지만 로컬의 13,287건을
  옮기지 않았다. `security_id` 가 `GENERATED ALWAYS AS IDENTITY` 라 **로컬과 Supabase 의
  id 가 다르다** — 종목코드로 다시 짝지어야 한다. S6 의 완료 조건은 "읽는다" 이므로
  걸음을 넓히지 않았다. 다음 걸음의 첫 후보다.
- ⚠️ **목록 갱신이 사람 손이다.** `--check` 가 나이만 잰다. 이 레포는 정확히 그 종류의
  고장을 겪었다(ADR-DS-0016 — 24거래일이 조용히 밀렸다). 화면에 나이를 싣는 것이 다음이다.
- ⚠️ **두 저장소가 갈린다.** 로컬에 새 거래일이 들어와도 Supabase 는 그대로다.
  `load_pg.py --universe core --allow-remote` 를 다시 돌리면 따라잡지만, **그 실행이
  어디에도 묶여 있지 않다.** `invoke refresh` 사슬은 쓰기 측(SQLite)에 못 박혀 있다(S8).
- ⚠️ **`DART_API_KEY` 가 Vercel 환경변수에 있어야** 배포본 수집이 실제로 돈다.
  로컬 실측에서는 `.key` 파일에서 읽혔다 — 그 파일은 배포본에 없다.
- 이 걸음은 **외부 계정 셋**에 기대게 됐다 — Supabase · KRX 정보데이터시스템 · (기존) Vercel.
  자격증명이 늘어난 만큼 `.key` 가 무거워졌다.

## 참조

- 모듈: ADR-DS-0011(전환 순서 · S6) · ADR-DS-0003 rev.2(커넥션 손잡이 넷) ·
  ADR-DS-0014(`universe_tier` 를 비워 둔 결정) · ADR-DS-0015(읽기 어댑터) ·
  ADR-DS-0018(S5 · 기본값이 환경마다 다름) · ADR-DS-0019(보관함) · ADR-DS-0020(자동 수집)
- 전역: ADR-CT-0007(저장소 이원화) · ADR-CT-0010(유니버스 2단계)
- 실물: `scripts/build_universe.py` · `data/universe_core.json` · `scripts/load_pg.py` ·
  `app/core/settings.py` · `tests/test_load_pg.py` · `tests/test_krx_pg.py`
