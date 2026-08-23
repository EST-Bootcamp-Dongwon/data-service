"""DB 접속 실측 — 표명한 커넥션 전략이 **실제로** 맞는지 그 자리에서 재본다.

ADR-DS-0003 은 커넥션 전략을 정해 놓고 "값이 실제로 맞는지는 접속 코드를 쓰는 순간 처음
검증된다"고 적어 두었다. 이 스크립트가 그 검증을 아무 때나 다시 할 수 있게 만든 것이다.
`scripts/check_migration_fitness.py` 가 **자료**에 자를 대는 것처럼, 이쪽은 **커넥션**에 댄다.

읽기 전용이다. `SELECT` 만 하고 표를 만들지도 고치지도 않는다.

사용법
------
    python3 scripts/check_db_connection.py              # 지금 환경(APP_ENV)대로
    python3 scripts/check_db_connection.py --echo       # SQL 을 함께 찍는다

    # 배포본 전략을 로컬에서 흉내 내 볼 때 (포트 경고가 함께 뜬다)
    APP_ENV=vercel DATABASE_URL=postgresql+asyncpg://... python3 scripts/check_db_connection.py

종료 코드
--------
    0   붙었고, 부하 검사에서 준비구문 오류가 한 건도 없다
    1   못 붙었거나, 부하 검사에서 준비구문 오류가 났다

무엇을 보는가
------------
1. **붙는가** — 주소·풀 클래스·서버 버전·왕복 시간. 비밀번호는 가려서 찍는다.
2. **준비구문이 트랜잭션을 넘어 살아남는가** ★ 이 스크립트의 존재 이유다.

   ⚠️ **"이름 있는 준비구문이 0개인가"로는 판정할 수 없다.** 그 기준을 처음에 썼다가
   틀렸다 — asyncpg 0.30 은 `statement_cache_size=0` 이어도 이름을 붙인다
   (`connection.py:656` 의 `named=True if name is None else name`). 0.31 은 익명으로
   바꾼다. 즉 **버전마다 답이 달라서**, 남은 개수는 안전의 척도가 아니다.

   깨지는 조건은 하나뿐이다 — **한 이름이 만들어진 물리 커넥션과, 그 이름을 다시 쓰는
   트랜잭션이 가는 물리 커넥션이 다른 것.** 그래서 세는 대신 **그 상황을 직접 만든다.**
     ㄱ. **호출마다 엔진·커넥션을 새로** 여러 개 동시에 (서버리스의 실제 모습)
     ㄴ. 한 커넥션 안에서 커밋을 끼고 트랜잭션을 여러 번 (풀러가 서버를 갈아 끼우는 틈)

   ⚠️ ㄱ 에서 **엔진을 재사용하면 아무것도 못 잡는다.** 이름 카운터는 커넥션마다 1부터
   다시 세므로, 커넥션이 새로 열려야 이름이 부딪힌다. 초안이 엔진 하나를 돌려 써서
   거짓 음성(항상 0건)을 냈다.
   여기서 `InvalidSQLStatementNameError` · `DuplicatePreparedStatementError` 가 하나라도
   나면 그 전략은 이 상대에게 쓸 수 없다. 0건이어야 한다.

3. **스키마가 서 있는가** — `sql/init/*.sql` 이 도는 것은 빈 볼륨 최초 기동뿐이라
   (01-schema.sql:3-5) 표가 없는 채로 붙어 있는 상황이 실제로 생긴다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 이 스크립트는 scripts/ 안에 있어서 파이썬이 프로젝트 루트를 모른다.
# (parents[0]=scripts, parents[1]=프로젝트 루트)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import db as db_module  # noqa: E402  (경로를 넣은 뒤에 import 해야 한다)
from app.core import settings  # noqa: E402

# `build_engine()` 은 포트가 어긋나면 로거로 경고한다. 이 스크립트는 그 문장을 **직접**
# 서식을 갖춰 찍으므로, 로거 쪽을 막아 같은 말이 두 번 나오지 않게 한다.
# (앱에서는 반대다 — 로거가 유일한 통로라 그대로 둔다.)
logging.getLogger(db_module.__name__).addHandler(logging.NullHandler())
logging.getLogger(db_module.__name__).propagate = False

# ADR-DS-0002 가 정한 표. 여기 없는 것이 있어도 실패로 보지 않는다 — 늘어나는 것은 정상이다.
EXPECTED_TABLES = ("securities", "trading_calendar", "ohlcv", "ohlcv_sync_log", "watermark")


def rule(title: str) -> None:
    print()
    print(title)
    print("─" * 74)


# 부하 검사 규모. 상대가 실제 Supabase 일 수도 있어서 작게 잡는다 —
# 전부 `SELECT` 뿐이고 표를 건드리지 않는다.
STRESS_WORKERS = 6
STRESS_ROUNDS = 6
STRESS_TXNS_PER_CONN = 4


async def stress(echo: bool) -> tuple[int, int, list[str]]:
    """준비구문이 물리 커넥션을 넘어 살아남는지 **직접 만들어** 본다.

    ⚠️ **엔진을 재사용하면 이 검사는 아무것도 못 잡는다.** 준비구문 이름은 asyncpg
    커넥션마다 1부터 다시 세는 카운터라, 이름이 부딪히려면 **커넥션이 새로 열려야** 한다.
    엔진 하나를 돌려 쓰면 그 상황이 만들어지지 않아 항상 0건이 나온다 — 이 스크립트의
    초안이 실제로 그렇게 거짓 음성을 냈다. 그래서 아래 ㄱ 은 **호출마다 엔진을 새로 만든다**
    (서버리스에서 람다 인스턴스가 매번 새로 뜨는 것과 같은 모양이다).

    돌려주는 것은 (전체 시도, 실패, 실패 문장 예시).
    읽기만 한다 — `SELECT CAST(:n AS int)` 와 `SELECT 1` 뿐이다.
    """
    from sqlalchemy import text

    errors: list[str] = []
    attempts = 0

    async def fresh_engine_call(wid: int, i: int) -> None:
        """ㄱ. 호출마다 엔진·커넥션을 새로 — **서버리스의 실제 모양.**

        이름 카운터가 매번 1부터 다시 시작하므로, 풀러 뒤에 잔여물이 있으면 여기서 걸린다.
        """
        nonlocal attempts
        attempts += 1
        engine = db_module.build_engine(echo=echo)
        try:
            async with engine.connect() as conn:
                value = (
                    await conn.execute(text("SELECT CAST(:n AS int) AS n"), {"n": wid * 100 + i})
                ).scalar_one()
                assert value == wid * 100 + i
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"[새 커넥션] {type(exc).__name__}: {str(exc).splitlines()[0]}")
        finally:
            await engine.dispose()

    async def long_connection(engine, wid: int) -> None:
        """ㄴ. 한 커넥션 · 트랜잭션 여러 번 — 풀러가 서버를 갈아 끼우는 틈.

        ⚠️ **시도 수를 try 안에서 세지 않는다.** 중간에 죽으면 남은 회차가 안 세어져
        분모가 줄고, **실패할수록 비율이 좋아 보인다.** 시작할 때 미리 더한다.
        """
        nonlocal attempts
        attempts += STRESS_TXNS_PER_CONN
        try:
            async with engine.connect() as conn:
                for i in range(STRESS_TXNS_PER_CONN):
                    await conn.execute(text("SELECT CAST(:n AS int) AS n"), {"n": wid * 10 + i})
                    await conn.commit()          # 여기서 물리 커넥션이 반납된다
        except Exception as exc:  # noqa: BLE001
            errors.append(f"[긴 커넥션] {type(exc).__name__}: {str(exc).splitlines()[0]}")

    shared = db_module.build_engine(echo=echo)
    try:
        await asyncio.gather(
            *[
                fresh_engine_call(w, i)
                for w in range(STRESS_WORKERS)
                for i in range(STRESS_ROUNDS)
            ],
            *[long_connection(shared, w) for w in range(STRESS_WORKERS)],
        )
    finally:
        await shared.dispose()

    return attempts, len(errors), errors[:3]


async def existing_tables(echo: bool) -> tuple[list[str], str]:
    """public 스키마의 표 목록. 읽기만 한다."""
    from sqlalchemy import text

    engine = db_module.build_engine(echo=echo)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
            ).fetchall()
        return [r[0] for r in rows], ""
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        await engine.dispose()


async def main_async(echo: bool) -> int:
    db = settings.database_settings()

    rule("1. 접속")
    probe = await db_module.ping()
    for line in probe.as_lines():
        print(line)

    warning = db_module.port_warning(db)
    if warning:
        print()
        print("⚠️ 포트 경고")
        for line in warning.splitlines():
            print(f"   {line}")

    if not probe.ok:
        rule("판정")
        print("❌ 못 붙었다. 위 안내를 먼저 처리한다.")
        return 1

    rule("2. 준비구문이 트랜잭션을 넘어 살아남는가  ★")
    print(f"   전략: 풀={probe.pool} · 캐시={dict(db.connect_args)}")
    print(
        f"         준비구문 이름 유일화="
        f"{'켬' if db.unique_statement_names else '끔 (기본 카운터)'}"
    )
    print(
        f"   새 커넥션 {STRESS_WORKERS}×{STRESS_ROUNDS} + "
        f"긴 커넥션 {STRESS_WORKERS}×트랜잭션 {STRESS_TXNS_PER_CONN} (전부 SELECT)"
    )
    attempts, failures, samples = await stress(echo)
    print(f"   {attempts}회 중 실패 {failures}건")
    for sample in samples:
        print(f"     ! {sample}")

    verdict_ok = failures == 0
    print()
    if verdict_ok:
        print("   ✅ 0건. 이 상대에게 지금 전략을 써도 된다.")
    else:
        print("   ❌ 실패가 났다. 이 전략은 이 상대에게 쓸 수 없다.")
        print("      배포본 전략은 **넷이 한 벌**이다 — NullPool · 캐시 둘 0 · 이름 유일화.")
        print(f"      지금: 풀={probe.pool} · 캐시={dict(db.connect_args)} · "
              f"이름 유일화={'켬' if db.unique_statement_names else '끔'}")
        print("      넷이 다 켜져 있는데도 실패한다면 **풀러 쪽에 남은 준비구문**이다 —")
        print("      로컬 pgbouncer 는 관리 콘솔에서 `RECONNECT;` 또는 컨테이너 재시작.")

    rule("3. 스키마가 서 있는가")
    tables, error = await existing_tables(echo)
    if error:
        print(f"   표 목록을 읽지 못했다 — {error}")
    else:
        missing = [t for t in EXPECTED_TABLES if t not in tables]
        print(f"   public 표 {len(tables)}개")
        if missing:
            print(f"   ⚠️ ADR-DS-0002 가 정한 표 중 없는 것: {', '.join(missing)}")
            print("      sql/init/*.sql 은 **빈 볼륨 최초 기동에만** 돈다. 다시 세우려면:")
            print("        docker compose --profile local-db down -v")
            print("        docker compose --profile local-db up -d")
        else:
            print(f"   ✅ ADR-DS-0002 의 다섯 표가 전부 있다: {', '.join(EXPECTED_TABLES)}")

    rule("판정")
    if verdict_ok:
        print("✅ 이 환경에서 기대하는 커넥션 상태가 맞다.")
        return 0
    print("❌ 커넥션 전략과 실제 상태가 어긋난다.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="DB 접속 실측 (읽기 전용)")
    parser.add_argument("--echo", action="store_true", help="SQL 을 함께 찍는다")
    args = parser.parse_args()
    return asyncio.run(main_async(args.echo))


if __name__ == "__main__":
    raise SystemExit(main())
