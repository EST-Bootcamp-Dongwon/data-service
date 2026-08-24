"""일회성 적재기 — SQLite 캐시를 Postgres 로 옮긴다 (저장계층 전환 S3 · ADR-DS-0011).

`data/krx_cache.db` 의 세 갈래를 목표 스키마로 옮긴다.

    daily_price 780,484행  →  securities (종목당 1행) + ohlcv (거래일×종목)
    fetch_log   300행      →  ohlcv_sync_log
    (적재 사실 자체)        →  watermark 1행

**읽기 경로는 건드리지 않는다.** 화면과 API 는 여전히 SQLite 를 본다. 어댑터는 S4,
기본값 뒤집기는 S5 다 (ADR-DS-0011). 이 스크립트는 Postgres 쪽에 자료를 **놓기만** 한다.

사용법
------
    python3 scripts/load_pg.py --dry-run     # 원본만 읽고 무엇을 쓸지 보고한다 (DB 불필요)
    python3 scripts/load_pg.py               # 적재하고 원본과 대조한다
    python3 scripts/load_pg.py --verify-only # 이미 적재된 것을 원본과 대조만 한다
    python3 scripts/load_pg.py --batch-size 2000

종료 코드
--------
    0   적재했고 대조가 전부 일치한다 (또는 --dry-run 이 정상 종료했다)
    1   막는 조건에 걸렸거나, 적재에 실패했거나, 대조가 어긋났다

## 왜 COPY 가 아니라 INSERT … ON CONFLICT 인가

`COPY` 가 몇 배 빠르다. 실측은 이쪽도 **32초**(27,900행/초)라 속도가 병목이 아니다.

⚠️ **중단 복구가 이유는 아니다.** 이 스크립트는 전부를 한 트랜잭션에 넣으므로, 어느
쪽을 쓰든 중간에 죽으면 통째로 롤백되고 다시 처음부터다. `ON CONFLICT` 가 실제로 사는
자리는 **이미 자료가 있는 DB 에 다시 돌릴 때**다.

- 적재가 끝난 DB 에 다시 돌려도 아무 일도 안 일어난다 (`--verify-only` 를 쓸 필요조차 없다)
- **SQLite 에 새 거래일이 들어온 뒤 다시 돌리면 그 추가분만 들어간다.** S4~S5 동안
  두 저장소가 갈리는 것이 정상이라 이 재실행이 실제로 자주 필요하다.
  `COPY` 였다면 이미 있는 첫 행에서 중복 키로 배치가 통째로 죽는다

즉 고른 기준은 속도가 아니라 **"몇 번을 다시 돌려도 같은 결과"** 다.

## ⚠️ 이 스크립트는 DDL 을 발행하지 않는다

`sql/init/*.sql` 은 **빈 볼륨 최초 기동에만** 돈다(01-schema.sql:3-5). 표가 없으면
만들어 주는 대신 **무엇을 해야 하는지 말하고 멈춘다.** 적재기가 표를 만들기 시작하면
"스키마 정본이 어디인가"가 두 곳이 된다 (app/core/db.py 모듈 docstring 참조).

## ⚠️ 원본은 읽기 전용으로 연다

`sqlite3.connect(path)` 로 그냥 열면 WAL 파일을 만들며 **원본 폴더에 쓴다.** 전환 도중
원본이 바뀌면 대조의 뜻이 사라진다. `mode=ro` 로 연다.

## 반드시 살려야 하는 규칙 — rows=0 은 휴장일 마커다

`fetch_log.rows = 0` 은 "아직 안 받았다"가 아니라 **"받아 봤더니 없었다"** 는 뜻이다
(krx_store.fetched_dates() :160-173 이 그것으로 재요청을 억제한다). 옮기면서 이 뜻이
사라지면 확정된 휴장일을 매 수집마다 KRX 에 다시 물어보게 된다. 그래서 `rows` 값을
그대로 옮기고, `status` 를 `'empty'` 로 세워 **뜻을 한 번 더 적어 둔다.**
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from itertools import islice
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# 이 스크립트는 scripts/ 안에 있어서 파이썬이 프로젝트 루트를 모른다.
# (parents[0]=scripts, parents[1]=프로젝트 루트)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import db as db_module  # noqa: E402  (경로를 넣은 뒤에 import 해야 한다)
from app.core import settings  # noqa: E402

# `build_engine()` 은 포트가 어긋나면 로거로 경고한다. 이 스크립트는 그 문장을 직접 서식을
# 갖춰 찍으므로 로거 쪽을 막아 같은 말이 두 번 나오지 않게 한다
# (scripts/check_db_connection.py 와 같은 처리다).
logging.getLogger(db_module.__name__).addHandler(logging.NullHandler())
logging.getLogger(db_module.__name__).propagate = False

SQLITE_PATH = PROJECT_ROOT / "data" / "krx_cache.db"
INDUSTRY_MAP_PATH = PROJECT_ROOT / "data" / "industry_map.json"
CORP_CODE_PATH = PROJECT_ROOT / "data" / "corp_code.json"

# KST. `fetch_log.fetched_at` 은 `datetime.now().isoformat()` 이 남긴 **시간대 없는** 문자열이라
# (krx_store.py:193) 그것을 timestamptz 로 옮기려면 어느 시간대였는지를 정해야 한다.
# 수집기가 이 개발 기기에서 돌았고 이 프로젝트의 기준시가 KST 다.
KST = timezone(timedelta(hours=9))

# ohlcv.change_rate 는 numeric(12,4) 다. asyncpg 는 numeric 컬럼에 float 를 받지 않으므로
# 파이썬 쪽에서 Decimal 로 바꾼다. 어차피 Postgres 가 반올림할 자리라 여기서 미리 맞춘다.
RATE_QUANT = Decimal("0.0001")

DEFAULT_BATCH = 5_000

# ADR-DS-0002 가 정한 표. 하나라도 없으면 빈 볼륨 기동이 안 된 것이다.
REQUIRED_TABLES = ("securities", "ohlcv", "ohlcv_sync_log", "watermark", "clip")

# ⚠️ 적재를 시작한 뒤에는 이 값을 못 늘린다 — 마이그레이션 러너가 없다 (ADR-DS-0012 §2).
# 그래서 **적재 직전에** 볼륨이 넓혀진 DDL 로 섰는지 확인한다. 여기서 걸리면 `down -v` 다.
CLIP_KIND_REQUIRED = ("post", "video")

# 로컬 직결로 볼 호스트. 여기 없는 곳에 780,484행을 붓는 것은 사고일 가능성이 훨씬 높다.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db", "data-service-db"})

# 옮겨 온 행임을 표에 남긴다. S8 이 수집 경로로 쓰기 시작하면 그쪽은 note 가 비어 있어서,
# **이식분과 수집분이 구분된다.**
IMPORT_NOTE = "S3 이식 (SQLite krx_cache.db)"
WATERMARK_SOURCE = "krx_ohlcv"


# ─────────────────────────────────────────────────────────────────────────────
# 1. 값 변환 — 순수 함수라 DB 없이 검사된다 (tests/test_load_pg.py)
# ─────────────────────────────────────────────────────────────────────────────
def to_trade_date(bas_dd: str) -> date:
    """`'20250609'` → `date(2025, 6, 9)`.

    SQLite 는 날짜를 문자열로 담고 Postgres 는 `date` 다. 문자열인 채로 넘기면
    asyncpg 가 거부한다 — 그 편이 낫다. 조용히 통과하면 파티션이 엉뚱하게 갈린다.
    """
    return datetime.strptime(bas_dd, "%Y%m%d").date()


def to_change_rate(value: float | int | None) -> Decimal | None:
    """등락률을 `numeric(12,4)` 에 맞는 Decimal 로.

    ⚠️ `Decimal(float)` 을 쓰지 않는다. 그 생성자는 float 의 2진 근사를 **그대로** 옮겨
    `Decimal('1.230000000000000106581410364...')` 같은 값을 만든다. `str()` 을 거치면
    파이썬이 왕복 가능한 최단 표기를 주므로 원본이 뜻한 값에 가장 가깝다.
    """
    if value is None:
        return None
    return Decimal(str(value)).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def to_fetched_at(text: str | None) -> datetime:
    """`'2026-08-01T16:50:08'` → KST 를 붙인 aware datetime.

    시간대가 이미 붙어 있으면 그대로 둔다. 값이 없거나 못 읽으면 **지금 시각으로 때우지
    않고** 예외를 낸다 — 대장의 시각을 지어내면 신선도 판정이 조용히 틀어진다.
    """
    if not text:
        raise ValueError("fetched_at 이 비어 있다. 대장의 시각을 지어내지 않는다.")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed


def sync_log_status(rows: int) -> str:
    """`ohlcv_sync_log.status`. **rows=0 은 휴장일 마커다** (모듈 docstring 참조).

    `rows` 값 자체가 이미 그 뜻을 담고 있지만, 한 번 더 적어 둔다. 나중에 이 표를 읽는
    코드가 `rows` 의 뜻을 모른 채 `status` 만 볼 수 있기 때문이다.
    """
    return "empty" if rows == 0 else "ok"


def to_fiscal_month(raw: Any) -> int | None:
    """결산월 `'12'` → `12`. 1~12 밖이거나 못 읽으면 None.

    `industry_map.json` 이 문자열로 담고 있고 `securities.fiscal_month` 는 smallint 다.
    이상한 값을 억지로 넣기보다 비워 두는 편이 낫다 — 비어 있으면 모른다는 뜻이 되지만,
    잘못 채우면 아는 척이 된다.
    """
    try:
        month = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return month if 1 <= month <= 12 else None


def url_host(url: str) -> str:
    """접속 문자열에서 호스트만 뽑는다. 못 찾으면 빈 문자열.

    `settings.url_port()` 와 같은 자리를 훑지만 그쪽은 포트만 돌려준다. 여기서 필요한 것은
    **어디에 붓는가**라 호스트다. 값을 고치지 않고 읽기만 한다.
    """
    if "://" not in url:
        return ""
    rest = url.partition("://")[2]
    host_part = rest.rpartition("@")[2] if "@" in rest else rest
    host_part = host_part.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if host_part.startswith("["):                      # IPv6 리터럴 [::1]:5432
        return host_part.partition("]")[0][1:]
    return host_part.rpartition(":")[0] if ":" in host_part else host_part


def is_local_target(url: str) -> bool:
    """이 주소가 로컬 Postgres 인가. **원격에 붓는 사고를 막는 유일한 문지기다.**

    ⚠️ `APP_ENV` 로는 못 막는다. 개발자 셸에는 보통 `APP_ENV` 가 없어서 `app_env()` 가
    `local` 로 떨어지는데(settings.py), 그 셸의 `DATABASE_URL` 이 Supabase 를 가리키고
    있을 수 있다. 그러면 `full` 유니버스 780,484행이 `core` 만 두기로 한 데모용 DB 로
    쏟아진다 (ADR-CT-0007 · ADR-CT-0010). 그래서 **호스트를 직접 본다.**
    """
    return url_host(url).lower() in LOCAL_HOSTS


def batched(rows: Iterable[Any], size: int) -> Iterator[list[Any]]:
    """반복자를 `size` 개씩 끊어 준다. **원본을 통째로 메모리에 올리지 않기 위해서다.**

    780,484행을 한 번에 리스트로 만들면 수백 MB 가 된다. sqlite3 커서는 게으르게
    돌므로 이렇게 끊으면 메모리가 배치 크기에서 평평해진다.
    """
    if size < 1:
        raise ValueError(f"배치 크기는 1 이상이어야 한다: {size}")
    iterator = iter(rows)
    while chunk := list(islice(iterator, size)):
        yield chunk


def fold_securities(rows: Iterable[Sequence[Any]]) -> dict[str, dict[str, Any]]:
    """종목별 속성을 **가장 최근 거래일 값 하나로** 접는다.

    입력은 `(code, bas_dd, name, market, sector, listed_shares)` 다.

    ⚠️ **접으면 잃는 것이 있다.** 실측으로 name 이 101종목, sector 가 351종목,
    listed_shares 가 1,256종목에서 기간 안에 변한다. 그래서 거래일마다 달라지는
    `listed_shares` 는 `ohlcv` 쪽에 **그 거래일 값 그대로** 따로 싣는다 (ADR-DS-0010).
    `securities.listed_shares` 는 최신값이고 둘은 역할이 다르다 — 갈리는 것이 정상이다.

    name 이력은 이번에 **버린다.** 되살리려면 이력 표가 따로 필요한데 그것은 이 걸음의
    일이 아니다. 근거와 대가는 ADR-DS-0014 에 적어 둔다.
    """
    latest: dict[str, dict[str, Any]] = {}
    for code, bas_dd, name, market, sector, listed_shares in rows:
        current = latest.get(code)
        if current is not None and current["bas_dd"] >= bas_dd:
            continue
        latest[code] = {
            "code": code,
            "bas_dd": bas_dd,
            "name": name,
            "market": market,
            "sector": sector,
            "listed_shares": listed_shares,
        }
    return latest


def load_master_maps() -> tuple[dict[str, Any], dict[str, Any]]:
    """`securities` 보강용 마스터 두 개. 없으면 빈 맵으로 돌아간다.

    ⚠️ **마스터를 원천으로 쓰지 않는다.** 실측으로 마스터가 못 덮는 시세 종목이 3개 있다
    (000075 · 000885 · 45014K). 마스터를 원천으로 삼으면 그 셋이 `securities` 에서
    통째로 빠지고, `ohlcv` 가 참조할 `security_id` 가 없어 그 종목의 시세가 전부 사라진다.
    원천은 `daily_price` 이고 마스터는 **덧칠**이다.
    """
    industry: dict[str, Any] = {}
    corp: dict[str, Any] = {}
    if INDUSTRY_MAP_PATH.exists():
        industry = json.loads(INDUSTRY_MAP_PATH.read_text(encoding="utf-8")).get("map", {})
    if CORP_CODE_PATH.exists():
        corp = json.loads(CORP_CODE_PATH.read_text(encoding="utf-8")).get("map", {})
    return industry, corp


def enrich_security(
    folded: dict[str, Any], industry: dict[str, Any], corp: dict[str, Any]
) -> dict[str, Any]:
    """접힌 종목 한 줄에 마스터 값을 덧칠해 `securities` INSERT 파라미터로 만든다."""
    code = folded["code"]
    industry_row = industry.get(code) or {}
    corp_row = corp.get(code) or {}
    return {
        "code": code,
        "name": folded["name"],
        "market": folded["market"],
        "sector": folded["sector"],
        "listed_shares": folded["listed_shares"],
        "industry_code": industry_row.get("industry_code"),
        "fiscal_month": to_fiscal_month(industry_row.get("fiscal_month")),
        "corp_code": corp_row.get("corp_code"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. 원본 읽기 — 전부 읽기 전용이다
# ─────────────────────────────────────────────────────────────────────────────
def open_readonly() -> sqlite3.Connection:
    """SQLite 를 읽기 전용으로 연다 (`check_migration_fitness.py` 와 같은 처리)."""
    if not SQLITE_PATH.exists():
        raise SystemExit(
            f"원본 SQLite 를 찾지 못했다: {SQLITE_PATH}\n"
            "  이 레포의 .db 는 git 에 없다(배포 방침). 먼저 채운다:\n"
            "    python3 scripts/fetch_krx.py"
        )
    try:
        return sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"읽기 전용으로 열지 못했다: {exc}\n"
            "  서버가 이 파일을 물고 있으면 내린 뒤 다시 실행한다."
        ) from exc


def source_checksums(conn: sqlite3.Connection) -> dict[str, Any]:
    """원본의 지문. **적재기가 센 값이 아니라 원본을 다시 읽어 낸 값이다.**

    적재 중에 세어 두고 그것과 비교하면, 적재기가 잘못 읽은 경우를 못 잡는다. 대조의
    두 변이 같은 실수를 공유하면 대조가 아니다. 그래서 원본을 **따로 한 번 더** 훑는다.

    정수 컬럼의 합은 SQLite 에서도 정확하다(전부 bigint 안에 들어간다 — 적합성 검사가
    잰다). 유일한 실수 컬럼 `change_rate` 만 파이썬에서 Decimal 로 더한다 —
    SQLite 의 `SUM(REAL)` 은 부동소수 누적오차가 있어 대조 기준으로 쓸 수 없다.
    """
    row = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT code), COUNT(DISTINCT bas_dd),
               MIN(bas_dd), MAX(bas_dd),
               SUM(open), SUM(high), SUM(low), SUM(close), SUM(change),
               SUM(volume), SUM(value), SUM(market_cap), SUM(listed_shares)
        FROM daily_price
        """
    ).fetchone()
    zero_bars = conn.execute(
        "SELECT COUNT(*) FROM daily_price WHERE open = 0 AND high = 0 AND low = 0"
    ).fetchone()[0]

    # change_rate 만 파이썬에서 더한다. 적재할 때와 **똑같이** 양자화한 뒤 더해야
    # 대조가 뜻을 가진다 (Postgres 도 numeric(12,4) 로 반올림해 담기 때문이다).
    rate_sum = Decimal(0)
    for (value,) in conn.execute("SELECT change_rate FROM daily_price"):
        converted = to_change_rate(value)
        if converted is not None:
            rate_sum += converted

    log_row = conn.execute(
        "SELECT COUNT(*), SUM(rows), SUM(CASE WHEN rows = 0 THEN 1 ELSE 0 END) FROM fetch_log"
    ).fetchone()

    return {
        "ohlcv_rows": row[0],
        "codes": row[1],
        "dates": row[2],
        "min_date": to_trade_date(row[3]),
        "max_date": to_trade_date(row[4]),
        "sum_open": row[5],
        "sum_high": row[6],
        "sum_low": row[7],
        "sum_close": row[8],
        "sum_change": row[9],
        "sum_volume": row[10],
        "sum_value": row[11],
        "sum_market_cap": row[12],
        "sum_listed_shares": row[13],
        "sum_change_rate": rate_sum,
        "zero_bars": zero_bars,
        "log_rows": log_row[0],
        "log_sum_rows": log_row[1] or 0,
        "log_zero_rows": log_row[2] or 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. 목표 확인 — 막을 것은 적재 전에 막는다
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA_MISSING_HELP = (
    "sql/init/*.sql 은 **빈 볼륨 최초 기동에만** 돈다(01-schema.sql:3-5).\n"
    "  이 스크립트는 표를 만들지 않는다 — 스키마 정본이 두 곳이 되면 안 된다.\n"
    "    docker compose --profile local-db up -d\n"
    "  그래도 없으면 볼륨이 옛 DDL 로 서 있는 것이다 (⚠️ -v 는 그 DB 를 통째로 지운다):\n"
    "    docker compose --profile local-db down -v && docker compose --profile local-db up -d"
)


async def check_target(conn: Any) -> list[str]:
    """적재를 막아야 하는 조건을 전부 모아 돌려준다. 빈 리스트면 진행해도 된다.

    **하나 찾고 멈추지 않는다.** 표가 없는 것과 clip 제약이 낡은 것은 처방이 같아서
    (`down -v`), 따로 알려 주면 사용자가 볼륨을 두 번 지운다.
    """
    from sqlalchemy import text

    problems: list[str] = []

    tables = {
        r[0]
        for r in (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ).fetchall()
    }
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing:
        problems.append(f"표가 없다: {', '.join(missing)}\n  {SCHEMA_MISSING_HELP}")
        return problems                       # 표가 없으면 아래 검사는 뜻이 없다

    # ⚠️ clip 은 이 적재기가 손대는 표가 아니다. 그런데 여기서 본다 —
    #    **볼륨을 다시 세울 수 있는 마지막 순간이 지금**이기 때문이다 (ADR-DS-0012 §2).
    #    ohlcv 780,484행이 들어간 뒤에 clip 제약을 고치려면 그 전부를 다시 부어야 한다.
    definition = (
        await conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'clip_kind_ck'"
            )
        )
    ).scalar_one_or_none()
    if definition is None:
        problems.append("clip_kind_ck 제약을 찾지 못했다. clip 표가 옛 DDL 로 서 있다.")
    else:
        absent = [k for k in CLIP_KIND_REQUIRED if f"'{k}'" not in definition]
        if absent:
            problems.append(
                f"clip_kind_ck 가 {', '.join(absent)} 를 아직 못 받는다.\n"
                f"  지금: {definition}\n"
                "  이 볼륨은 sql/init/03-clip.sql 을 넓히기 **전에** 섰다.\n"
                "  ⚠️ 지금 고치지 않으면 커뮤니티·동영상을 담기 시작할 때 전면 재적재다.\n"
                f"  {SCHEMA_MISSING_HELP}"
            )

    partitions = (
        await conn.execute(
            text("SELECT COUNT(*) FROM pg_inherits WHERE inhparent = 'ohlcv'::regclass")
        )
    ).scalar_one()
    if partitions == 0:
        problems.append(
            "ohlcv 에 파티션이 하나도 없다. 02-partitions.sql 이 돌지 않았다.\n"
            f"  {SCHEMA_MISSING_HELP}"
        )

    return problems


# ─────────────────────────────────────────────────────────────────────────────
# 4. 적재
# ─────────────────────────────────────────────────────────────────────────────
SECURITIES_UPSERT = """
INSERT INTO securities (code, name, market, sector, listed_shares,
                        industry_code, fiscal_month, corp_code)
VALUES (:code, :name, :market, :sector, :listed_shares,
        :industry_code, :fiscal_month, :corp_code)
ON CONFLICT (code) WHERE NOT is_delisted DO UPDATE SET
    name          = EXCLUDED.name,
    market        = EXCLUDED.market,
    sector        = EXCLUDED.sector,
    listed_shares = EXCLUDED.listed_shares,
    industry_code = COALESCE(EXCLUDED.industry_code, securities.industry_code),
    fiscal_month  = COALESCE(EXCLUDED.fiscal_month,  securities.fiscal_month),
    corp_code     = COALESCE(EXCLUDED.corp_code,     securities.corp_code),
    updated_at    = now()
"""

# ⚠️ `DO NOTHING` 이다. 이미 있는 행을 덮지 않는다 — 이 표는 **이미 지나간 거래일**이라
#    값이 바뀔 이유가 없고, 다시 돌렸을 때 아무 일도 안 일어나는 편이 안전하다.
#    (파티션 표에도 ON CONFLICT 가 선다 — PK 가 파티션 키를 포함하기 때문이다.)
OHLCV_INSERT = """
INSERT INTO ohlcv (security_id, trade_date, open, high, low, close,
                   change, change_rate, volume, value, market_cap, listed_shares)
VALUES (:security_id, :trade_date, :open, :high, :low, :close,
        :change, :change_rate, :volume, :value, :market_cap, :listed_shares)
ON CONFLICT (security_id, trade_date) DO NOTHING
"""

SYNC_LOG_UPSERT = """
INSERT INTO ohlcv_sync_log (trade_date, rows, fetched_at, status, note)
VALUES (:trade_date, :rows, :fetched_at, :status, :note)
ON CONFLICT (trade_date) DO UPDATE SET
    rows       = EXCLUDED.rows,
    fetched_at = EXCLUDED.fetched_at,
    status     = EXCLUDED.status,
    note       = EXCLUDED.note
"""

WATERMARK_UPSERT = """
INSERT INTO watermark (source, last_synced_at, last_key, rows, status, note)
VALUES (:source, :last_synced_at, :last_key, :rows, 'ok', :note)
ON CONFLICT (source) DO UPDATE SET
    last_synced_at = EXCLUDED.last_synced_at,
    last_key       = EXCLUDED.last_key,
    rows           = EXCLUDED.rows,
    status         = EXCLUDED.status,
    note           = EXCLUDED.note,
    updated_at     = now()
"""


async def load_securities(conn: Any, sqlite_conn: sqlite3.Connection) -> dict[str, int]:
    """`securities` 를 세우고 `code → security_id` 지도를 돌려준다.

    `ohlcv` 가 `security_id` 를 참조하므로 **반드시 먼저** 선다. 코드(text)가 아니라
    정수 키를 쓰는 이유는 01-schema.sql 의 주석에 셋으로 적혀 있다(우선주·코드변경·재사용).
    """
    from sqlalchemy import text

    folded = fold_securities(
        sqlite_conn.execute(
            "SELECT code, bas_dd, name, market, sector, listed_shares FROM daily_price"
        )
    )
    industry, corp = load_master_maps()
    params = [enrich_security(row, industry, corp) for row in folded.values()]

    covered = sum(1 for p in params if p["industry_code"])
    print(f"   종목 {len(params):,}개 · 산업분류가 붙은 것 {covered:,}개")

    for chunk in batched(params, DEFAULT_BATCH):
        await conn.execute(text(SECURITIES_UPSERT), chunk)

    mapping = {
        code: security_id
        for code, security_id in (
            await conn.execute(text("SELECT code, security_id FROM securities"))
        ).fetchall()
    }
    missing = [p["code"] for p in params if p["code"] not in mapping]
    if missing:
        raise RuntimeError(
            f"securities 에 자리를 못 잡은 종목이 {len(missing)}개 있다: {missing[:5]}"
        )
    return mapping


async def load_ohlcv(
    conn: Any, sqlite_conn: sqlite3.Connection, mapping: dict[str, int], batch_size: int
) -> int:
    """`daily_price` → `ohlcv`. 배치마다 진행을 찍는다."""
    from sqlalchemy import text

    cursor = sqlite_conn.execute(
        """
        SELECT code, bas_dd, open, high, low, close, change, change_rate,
               volume, value, market_cap, listed_shares
        FROM daily_price
        """
    )
    total = 0
    started = time.perf_counter()
    for index, chunk in enumerate(batched(cursor, batch_size), start=1):
        params = [
            {
                "security_id": mapping[row[0]],
                "trade_date": to_trade_date(row[1]),
                "open": row[2],
                "high": row[3],
                "low": row[4],
                "close": row[5],
                "change": row[6],
                "change_rate": to_change_rate(row[7]),
                "volume": row[8],
                "value": row[9],
                "market_cap": row[10],
                "listed_shares": row[11],
            }
            for row in chunk
        ]
        await conn.execute(text(OHLCV_INSERT), params)
        total += len(params)
        if index % 20 == 0 or len(chunk) < batch_size:
            elapsed = time.perf_counter() - started
            rate = total / elapsed if elapsed else 0
            print(f"   ohlcv {total:,}행 · {elapsed:.0f}초 · {rate:,.0f}행/초")
    return total


async def load_sync_log(conn: Any, sqlite_conn: sqlite3.Connection) -> tuple[int, int]:
    """`fetch_log` → `ohlcv_sync_log`. **rows=0 을 그대로 옮긴다** (모듈 docstring 참조)."""
    from sqlalchemy import text

    params = []
    zero = 0
    for bas_dd, rows, fetched_at in sqlite_conn.execute(
        "SELECT bas_dd, rows, fetched_at FROM fetch_log"
    ):
        rows = int(rows or 0)
        if rows == 0:
            zero += 1
        params.append(
            {
                "trade_date": to_trade_date(bas_dd),
                "rows": rows,
                "fetched_at": to_fetched_at(fetched_at),
                "status": sync_log_status(rows),
                "note": IMPORT_NOTE,
            }
        )
    if params:
        await conn.execute(text(SYNC_LOG_UPSERT), params)
    return len(params), zero


async def load_watermark(conn: Any, source: dict[str, Any], sqlite_conn: sqlite3.Connection) -> None:
    """언제 무엇을 어디서 옮겨 왔는지 한 줄 남긴다.

    `watermark` 는 '날짜로 나뉘지 않는 수집물'의 자리이고 DDL 의 예시가 `krx_ohlcv` 를
    직접 든다(01-schema.sql). 이 줄이 없으면 DB 만 보고는 **이 자료가 언제 어디서
    왔는지** 알 수 없다. S8 이 수집 경로로 이 표를 갱신하기 시작하면 그때 덮인다.
    """
    from sqlalchemy import text

    last = (
        sqlite_conn.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()[0]
    )
    await conn.execute(
        text(WATERMARK_UPSERT),
        {
            "source": WATERMARK_SOURCE,
            "last_synced_at": to_fetched_at(last),
            "last_key": source["max_date"].strftime("%Y%m%d"),
            "rows": source["ohlcv_rows"],
            "note": IMPORT_NOTE,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. 대조 — 이 걸음의 완료 조건이다 (ADR-DS-0011 S3)
# ─────────────────────────────────────────────────────────────────────────────
async def target_checksums(conn: Any) -> dict[str, Any]:
    """적재된 쪽의 지문. `source_checksums()` 와 같은 열쇠를 낸다."""
    from sqlalchemy import text

    row = (
        await conn.execute(
            text(
                """
                SELECT COUNT(*), COUNT(DISTINCT security_id), COUNT(DISTINCT trade_date),
                       MIN(trade_date), MAX(trade_date),
                       SUM(open), SUM(high), SUM(low), SUM(close), SUM(change),
                       SUM(volume), SUM(value), SUM(market_cap), SUM(listed_shares),
                       SUM(change_rate),
                       COUNT(*) FILTER (WHERE open = 0 AND high = 0 AND low = 0)
                FROM ohlcv
                """
            )
        )
    ).fetchone()
    log_row = (
        await conn.execute(
            text(
                "SELECT COUNT(*), COALESCE(SUM(rows), 0), "
                "COUNT(*) FILTER (WHERE rows = 0) FROM ohlcv_sync_log"
            )
        )
    ).fetchone()
    # ⚠️ 파티션 그물이 비어 있어야 한다. 여기 쌓였다면 그 연도 파티션을 안 만든 것이고,
    #    행은 들어갔으므로 **행수 대조만으로는 절대 안 잡힌다** (02-partitions.sql 참조).
    default_rows = (
        await conn.execute(text("SELECT COUNT(*) FROM ohlcv_default"))
    ).scalar_one()
    securities_rows = (
        await conn.execute(text("SELECT COUNT(*) FROM securities"))
    ).scalar_one()

    return {
        "ohlcv_rows": row[0],
        "codes": row[1],
        "dates": row[2],
        "min_date": row[3],
        "max_date": row[4],
        "sum_open": row[5],
        "sum_high": row[6],
        "sum_low": row[7],
        "sum_close": row[8],
        "sum_change": row[9],
        "sum_volume": row[10],
        "sum_value": row[11],
        "sum_market_cap": row[12],
        "sum_listed_shares": row[13],
        "sum_change_rate": row[14] or Decimal(0),
        "zero_bars": row[15],
        "log_rows": log_row[0],
        "log_sum_rows": log_row[1],
        "log_zero_rows": log_row[2],
        "default_partition_rows": default_rows,
        "securities_rows": securities_rows,
    }


# 대조할 열쇠와 사람이 읽을 이름. `securities_rows` 는 원본에 대응 열쇠가 없어서
# (종목 수 = `codes`) 아래 compare() 가 따로 짝지어 준다.
COMPARED = (
    ("ohlcv_rows", "ohlcv 행수"),
    ("codes", "종목 수"),
    ("dates", "거래일 수"),
    ("min_date", "첫 거래일"),
    ("max_date", "끝 거래일"),
    ("sum_open", "시가 합"),
    ("sum_high", "고가 합"),
    ("sum_low", "저가 합"),
    ("sum_close", "종가 합"),
    ("sum_change", "전일대비 합"),
    ("sum_change_rate", "등락률 합"),
    ("sum_volume", "거래량 합"),
    ("sum_value", "거래대금 합"),
    ("sum_market_cap", "시가총액 합"),
    ("sum_listed_shares", "상장주식수 합"),
    ("zero_bars", "OHL 이 0 인 행"),
    ("log_rows", "수집대장 행수"),
    ("log_sum_rows", "수집대장 rows 합"),
    ("log_zero_rows", "수집대장 rows=0 (휴장일 마커) ★"),
)


def compare(source: dict[str, Any], target: dict[str, Any]) -> list[str]:
    """어긋난 항목만 문장으로 돌려준다. 빈 리스트면 전부 일치다."""
    problems = []
    for key, label in COMPARED:
        left, right = source[key], target[key]
        if isinstance(left, Decimal) or isinstance(right, Decimal):
            left, right = Decimal(str(left)), Decimal(str(right))
        if left != right:
            problems.append(f"{label}: 원본 {left:,} ≠ 적재 {right:,}"
                            if isinstance(left, int) else
                            f"{label}: 원본 {left} ≠ 적재 {right}")
    if target["securities_rows"] != source["codes"]:
        problems.append(
            f"securities 행수: 원본 종목 {source['codes']:,} ≠ 적재 {target['securities_rows']:,}"
        )
    if target["default_partition_rows"]:
        problems.append(
            f"ohlcv_default 파티션에 {target['default_partition_rows']:,}행이 쌓였다 — "
            "그 연도 파티션이 없다. 02-partitions.sql 의 범위를 넓혀야 한다."
        )
    return problems


def render_comparison(source: dict[str, Any], target: dict[str, Any]) -> None:
    """대조표. 어긋난 줄만 보면 되지만 **맞은 줄도 보여 준다** — 무엇을 쟀는지가 곧 신뢰다."""
    width = max(len(label) for _, label in COMPARED) + 2
    for key, label in COMPARED:
        left, right = source[key], target[key]
        same = Decimal(str(left)) == Decimal(str(right)) if isinstance(left, Decimal) else left == right
        mark = "OK" if same else "!!"
        shown = f"{left:,}" if isinstance(left, int) else str(left)
        tail = "" if same else f"   ← 적재 {right}"
        print(f"   {mark} {label:<{width}} {shown}{tail}")
    mark = "OK" if target["securities_rows"] == source["codes"] else "!!"
    print(f"   {mark} {'securities 행수':<{width}} {target['securities_rows']:,}")
    mark = "OK" if not target["default_partition_rows"] else "!!"
    print(f"   {mark} {'파티션 그물(비어야 한다)':<{width}} {target['default_partition_rows']:,}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. 실행
# ─────────────────────────────────────────────────────────────────────────────
def rule(title: str) -> None:
    print()
    print(title)
    print("─" * 74)


def render_source(source: dict[str, Any]) -> None:
    print(f"   daily_price   {source['ohlcv_rows']:,}행 "
          f"({source['min_date']} ~ {source['max_date']})")
    print(f"   종목 · 거래일  {source['codes']:,}종목 · {source['dates']:,}일")
    print(f"   fetch_log     {source['log_rows']:,}행 "
          f"(rows=0 이 {source['log_zero_rows']} — 휴장일 마커)")
    print(f"   OHL 이 0 인 행  {source['zero_bars']:,}행")


async def run(args: argparse.Namespace) -> int:
    sqlite_conn = open_readonly()

    rule("1. 원본")
    started = time.perf_counter()
    source = source_checksums(sqlite_conn)
    render_source(source)
    print(f"   ({time.perf_counter() - started:.0f}초 걸려 읽었다)")

    if args.dry_run:
        rule("2. 무엇을 쓸 것인가 (--dry-run · DB 에 붙지 않는다)")
        industry, corp = load_master_maps()
        folded = fold_securities(
            sqlite_conn.execute(
                "SELECT code, bas_dd, name, market, sector, listed_shares FROM daily_price"
            )
        )
        enriched = [enrich_security(row, industry, corp) for row in folded.values()]
        print(f"   securities      {len(enriched):,}행 (종목당 1행 · 최신 거래일 속성)")
        print(f"     산업분류 있음   {sum(1 for e in enriched if e['industry_code']):,}")
        print(f"     DART 고유번호   {sum(1 for e in enriched if e['corp_code']):,}")
        print(f"   ohlcv           {source['ohlcv_rows']:,}행")
        print(f"   ohlcv_sync_log  {source['log_rows']:,}행 "
              f"(status='empty' 가 {source['log_zero_rows']})")
        print(f"   watermark       1행 (source='{WATERMARK_SOURCE}')")
        rule("판정")
        print("✅ 원본은 옮길 준비가 됐다. 실제 적재는 --dry-run 없이 다시 돌린다.")
        return 0

    db = settings.database_settings()
    if not is_local_target(db.url) and not args.allow_remote:
        rule("판정")
        print("❌ 로컬 Postgres 가 아니다. 780,484행을 여기에 붓지 않는다.")
        print(f"   붙으려던 곳: {db_module.displayable_url(db.url)}")
        print(f"   호스트     : {url_host(db.url) or '(못 읽었다)'}")
        print("   로컬 정본은 full 유니버스이고, 원격(Supabase)은 core 만 두기로 했다")
        print("   (ADR-CT-0007 · ADR-CT-0010). 일부러 그랬다면 --allow-remote 를 붙인다.")
        return 1

    engine = db_module.build_engine()
    try:
        rule("2. 목표 확인")
        async with engine.connect() as conn:
            problems = await check_target(conn)
        if problems:
            for problem in problems:
                # 여러 줄짜리 안내가 있어서 줄마다 들여쓰기를 맞춘다. 첫 줄만 맞추면
                # 뒤따르는 처방(`down -v` 등)이 판정문처럼 왼쪽에 붙어 읽힌다.
                head, *tail = problem.splitlines()
                print(f"   ❌ {head}")
                for line in tail:
                    print(f"      {line.lstrip()}")
            rule("판정")
            print("❌ 목표 스키마가 준비되지 않았다. 위 안내를 먼저 처리한다.")
            return 1
        print(f"   {', '.join(REQUIRED_TABLES)} 가 전부 있다")
        print(f"   clip_kind_ck 가 {', '.join(CLIP_KIND_REQUIRED)} 를 받는다")

        if not args.verify_only:
            rule("3. 적재")
            print(f"   붙는 곳: {db_module.displayable_url(db.url)}")
            load_started = time.perf_counter()
            # ⚠️ 한 트랜잭션이다. 중간에 죽으면 통째로 롤백되어 **반쯤 들어간 상태가 없다.**
            async with engine.begin() as conn:
                mapping = await load_securities(conn, sqlite_conn)
                rows = await load_ohlcv(conn, sqlite_conn, mapping, args.batch_size)
                log_rows, log_zero = await load_sync_log(conn, sqlite_conn)
                await load_watermark(conn, source, sqlite_conn)
            print(f"   ohlcv_sync_log {log_rows}행 (rows=0 이 {log_zero} · status='empty')")
            print(f"   보낸 행 {rows:,} · {time.perf_counter() - load_started:.0f}초")

        rule("4. 대조 — 원본을 다시 읽어 낸 값과 맞춰 본다")
        async with engine.connect() as conn:
            target = await target_checksums(conn)
        render_comparison(source, target)
        problems = compare(source, target)
    finally:
        await engine.dispose()
        sqlite_conn.close()

    rule("판정")
    if problems:
        print(f"❌ 어긋난 항목 {len(problems)}건")
        for problem in problems:
            print(f"   · {problem}")
        print()
        print("   다시 돌려도 안전하다 — 이 스크립트는 같은 행을 두 번 넣지 않는다.")
        return 1
    print("✅ 원본과 적재본이 전부 일치한다. S3 완료 조건을 채웠다.")
    print("   다음은 S4 (읽기 어댑터 + STORE_BACKEND 스위치) 다. 읽기 경로는 아직 SQLite 다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SQLite 캐시를 Postgres 로 옮긴다 (S3 · 원본은 읽기 전용)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="원본만 읽고 무엇을 쓸지 보고한다 (DB 에 붙지 않는다)")
    parser.add_argument("--verify-only", action="store_true",
                        help="적재하지 않고 이미 들어간 것과 원본을 대조만 한다")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                        help=f"한 번에 보낼 행 수 (기본 {DEFAULT_BATCH:,})")
    parser.add_argument("--allow-remote", action="store_true",
                        help="로컬이 아닌 DB 에 붓는 것을 허용한다 (기본은 막는다)")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size 는 1 이상이어야 한다")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
