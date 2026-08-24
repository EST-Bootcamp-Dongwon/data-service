"""저장계층 전환 적합성 검사 — SQLite 원본이 목표 DDL 을 통과할 수 있는지 미리 잰다.

ADR-DS-0002 를 실행하기 전에 **자를 먼저 댄다.** 780,484행을 다 적재한 뒤에
`numeric(8,4)` 한 칸이 모자란 것을 발견하면 전면 재적재다 — `sql/init/*.sql` 은
빈 볼륨에서만 실행되고(01-schema.sql:3-5) 이 레포에는 마이그레이션 러너가 없다.
지금은 Postgres 에 아무것도 없으므로 DDL 을 고치는 비용이 **0** 이다. 그 창이 열려 있는
동안 재는 것이 이 스크립트의 존재 이유다.

읽기 전용이다. `data/krx_cache.db` 를 `mode=ro` 로 열고 아무것도 쓰지 않는다.

사용법
------
    python3 scripts/check_migration_fitness.py            # 전체 검사
    python3 scripts/check_migration_fitness.py --quick    # 전체 스캔이 필요한 항목 생략

종료 코드
--------
    0   목표 DDL 로 적재할 수 있다
    1   치명 항목이 있다 — 적재하면 그 행에서 배치가 롤백된다

`--quick` 은 종목별 집계(변동 종목 수 등)를 건너뛴다. 그 항목들은 **판단 근거**이지
적재를 막는 조건이 아니라서, 빠르게 치명 항목만 보고 싶을 때 쓴다.

판단 대기 셋 — **결정됐다** (2026-08-23 · ADR-DS-0014)
------------------------------------------------------
치명이 아닌 줄은 **적재를 막지 않는다.** 전환 도중 조용히 틀어질 자리를 미리 세어 두는
것이다. 그중 셋은 여기서 "아직 결정이 안 났다"고 미뤄 두고 있었고, S3 적재기를 쓰면서
답이 나왔다. 이 자는 이제 그 결정이 **여전히 성립하는지**를 재는 쪽이 된다.

    securities 원천  daily_price 다. 마스터 JSON 은 세 컬럼만 덧칠한다   (ADR-DS-0014 §1)
    name 이력        최신값으로 접는다. 옛 이름은 버린다                 (ADR-DS-0014 §2)
    is_delisted      추정하지 않는다. 전부 false 로 두고 S8 에서 채운다  (ADR-DS-0014 §3)

즉 아래 세 줄은 "무엇을 고를까"가 아니라 **"고른 것의 대가가 아직 이만큼인가"** 를 센다.
숫자가 크게 움직이면 그 결정을 다시 볼 때다.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

# 이 스크립트는 scripts/ 안에 있어서 파이썬이 프로젝트 루트를 모른다.
# (parents[0]=scripts, parents[1]=프로젝트 루트)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SQLITE_PATH = PROJECT_ROOT / "data" / "krx_cache.db"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "init" / "01-schema.sql"

# bigint 상한. 이 값을 넘는 컬럼이 있으면 Postgres 가 적재를 거부한다.
BIGINT_MAX = 2**63 - 1

# 마스터 JSON — securities 의 속성 보강 원천. 코드 집합 커버리지를 재는 데도 쓴다.
MASTER_FILES = {
    "industry_map.json": ("map", None),
    "corp_code.json": ("map", None),
    "stock_master.json": (None, None),  # 최상위가 곧 코드 맵이다
}


class Finding:
    """검사 한 줄. `fatal` 이면 적재 자체가 불가능하다."""

    def __init__(
        self, name: str, measured: str, target: str, ok: bool, fatal: bool, note: str = ""
    ):
        self.name = name
        self.measured = measured
        self.target = target
        self.ok = ok
        self.fatal = fatal
        self.note = note


def human(n: int) -> str:
    """숫자에 천 단위 콤마를 붙인다."""
    return f"{n:,}"


# ─────────────────────────────────────────────────────────────────────────────
# 목표 DDL 읽기 — 스크립트와 스키마가 따로 놀지 않게 파일에서 직접 뽑는다
# ─────────────────────────────────────────────────────────────────────────────


def read_target_schema() -> dict:
    """`sql/init/01-schema.sql` 의 ohlcv 정의에서 검사에 필요한 값만 뽑는다.

    숫자를 스크립트에 베껴 두면 DDL 을 고쳤을 때 조용히 갈라진다. 파일이 정본이다.
    """
    if not SCHEMA_PATH.exists():
        raise SystemExit(f"목표 스키마를 찾지 못했다: {SCHEMA_PATH}")

    text = SCHEMA_PATH.read_text(encoding="utf-8")

    # ohlcv 블록만 떼어 낸다. securities 에도 listed_shares 가 있어서 전체를 훑으면 섞인다.
    match = re.search(r"CREATE TABLE ohlcv\s*\((.*?)\)\s*PARTITION BY", text, re.S)
    if match is None:
        raise SystemExit("01-schema.sql 에서 CREATE TABLE ohlcv 블록을 찾지 못했다.")
    block = match.group(1)

    rate = re.search(r"change_rate\s+numeric\(\s*(\d+)\s*,\s*(\d+)\s*\)", block)
    if rate is None:
        raise SystemExit("ohlcv.change_rate 의 numeric(p, s) 표기를 찾지 못했다.")
    precision, scale = int(rate.group(1)), int(rate.group(2))

    return {
        "rate_precision": precision,
        "rate_scale": scale,
        # 정수부 자릿수 = 전체 자릿수 − 소수부 자릿수
        "rate_int_digits": precision - scale,
        "bigint_cols": set(re.findall(r"^\s*(\w+)\s+bigint", block, re.M)),
        # NOT NULL 인 컬럼 — SQLite 쪽에 NULL 이 있으면 적재가 멈춘다
        "not_null_cols": set(re.findall(r"^\s*(\w+)\s+\w+[^,\n]*?NOT NULL", block, re.M)),
        "has_listed_shares": "listed_shares" in block,
    }


def open_readonly() -> sqlite3.Connection:
    """SQLite 를 읽기 전용으로 연다.

    ⚠️ 그냥 `sqlite3.connect(path)` 로 열면 WAL 파일을 만들면서 **원본 폴더에 쓴다.**
    이 스크립트는 전환 전 상태를 재는 것이 목적이라 원본을 건드리면 안 된다.
    """
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
            "  WAL 모드 DB 는 -shm 파일을 만들 수 있어야 읽힌다.\n"
            "  서버가 떠 있다면 내린 뒤 다시 실행한다."
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# 검사 항목
# ─────────────────────────────────────────────────────────────────────────────


def check_scale(conn: sqlite3.Connection, out: list) -> dict:
    """규모 — 이 뒤의 모든 불변식이 이 숫자를 기준으로 선다."""
    rows = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    dates = conn.execute("SELECT COUNT(DISTINCT bas_dd) FROM daily_price").fetchone()[0]
    codes = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_price").fetchone()[0]
    lo, hi = conn.execute("SELECT MIN(bas_dd), MAX(bas_dd) FROM daily_price").fetchone()
    log_rows = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0]
    zero_rows = conn.execute("SELECT COUNT(*) FROM fetch_log WHERE rows = 0").fetchone()[0]

    out.append(Finding("daily_price 행수", human(rows), "—", True, False, f"{lo}~{hi}"))
    out.append(Finding("거래일 수", human(dates), "—", True, False))
    out.append(Finding("종목 수", human(codes), "—", True, False))
    out.append(
        Finding(
            "fetch_log 행수",
            f"{human(log_rows)} (rows=0 이 {zero_rows})",
            f"= 거래일 {dates} + 휴장 {zero_rows}",
            log_rows == dates + zero_rows,
            True,
            "ohlcv_sync_log 로 그대로 옮겨진다. 어긋나면 재수집 판정이 틀어진다",
        )
    )
    return {"rows": rows, "dates": dates, "codes": codes, "zero_rows": zero_rows}


def check_ledger_consistency(conn: sqlite3.Connection, out: list) -> None:
    """대장(fetch_log)과 실적(daily_price)이 어긋나지 않는지.

    어긋나면 두 방향 모두 문제다.
      - 대장에 없는데 실적이 있다 → ohlcv_sync_log 가 그 날짜를 모르므로 매번 다시 받는다
      - 대장에 rows>0 인데 실적이 없다 → 받았다고 기록만 남고 자료가 없다
    """
    orphan = conn.execute(
        "SELECT COUNT(DISTINCT bas_dd) FROM daily_price "
        "WHERE bas_dd NOT IN (SELECT bas_dd FROM fetch_log)"
    ).fetchone()[0]
    phantom = conn.execute(
        "SELECT COUNT(*) FROM fetch_log WHERE rows > 0 "
        "AND bas_dd NOT IN (SELECT DISTINCT bas_dd FROM daily_price)"
    ).fetchone()[0]

    out.append(
        Finding(
            "대장↔실적 불일치",
            f"실적만 {orphan}일 · 대장만 {phantom}일",
            "둘 다 0",
            orphan == 0 and phantom == 0,
            True,
        )
    )


def check_numeric_fit(conn: sqlite3.Connection, target: dict, out: list) -> None:
    """수치 컬럼이 목표 타입 안에 들어가는지 — 여기가 이 스크립트의 핵심이다."""
    # change_rate — numeric(p, s) 의 정수부를 넘기는 값이 하나라도 있으면 그 행에서 멈춘다.
    worst = conn.execute(
        "SELECT bas_dd, code, name, close, change, change_rate FROM daily_price "
        "WHERE change_rate IS NOT NULL ORDER BY ABS(change_rate) DESC LIMIT 1"
    ).fetchone()
    if worst is None:
        out.append(Finding("change_rate 최대", "(값 없음)", "—", True, False))
    else:
        bas_dd, code, name, close, change, rate = worst
        limit = 10 ** target["rate_int_digits"]
        fits = abs(rate) < limit
        out.append(
            Finding(
                "change_rate 최대 절댓값",
                f"{abs(rate)}",
                f"< {limit} (numeric({target['rate_precision']},{target['rate_scale']}))",
                fits,
                True,
                f"{bas_dd} {code} {name} close={human(close)} change={human(change or 0)}",
            )
        )

    # bigint 컬럼 — SQLite INTEGER 는 상한이 같아서 실제로 넘칠 일은 없지만,
    # DDL 이 int4 로 바뀌는 사고를 잡으려면 재 두는 편이 싸다.
    for col in ("volume", "value", "market_cap", "listed_shares"):
        if col not in target["bigint_cols"] and col != "listed_shares":
            continue
        peak = conn.execute(f"SELECT MAX({col}) FROM daily_price").fetchone()[0] or 0
        out.append(
            Finding(
                f"{col} 최대",
                human(peak),
                f"<= bigint {human(BIGINT_MAX)}",
                peak <= BIGINT_MAX,
                True,
            )
        )


def check_not_null(conn: sqlite3.Connection, target: dict, out: list) -> None:
    """목표 DDL 이 NOT NULL 로 잡은 컬럼에 SQLite 쪽 NULL 이 있는지.

    SQLite 는 `open INTEGER` 라 NULL 을 받는다(krx_data.py 의 `_to_number` 가 `"-"` 를
    None 으로 만든다). ohlcv 는 OHLC 가 NOT NULL 이라, 그런 행이 하나 오면
    **그 하루치 배치 전체**가 롤백된다.
    """
    for col in ("open", "high", "low", "close"):
        if col not in target["not_null_cols"]:
            continue
        nulls = conn.execute(f"SELECT COUNT(*) FROM daily_price WHERE {col} IS NULL").fetchone()[0]
        out.append(Finding(f"{col} NULL", human(nulls), "0 (NOT NULL)", nulls == 0, True))


def check_zero_bars(conn: sqlite3.Connection, out: list) -> None:
    """OHL 이 전부 0 인 행 — 0 은 NULL 이 아니라 NOT NULL 을 통과한다.

    막지 않는다. 다만 **세어 두고 적재 전후가 같은지 확인**해야 한다. 그래야 나중에
    갭·트루레인지를 얹을 때 "여기가 0이었다"를 알고 시작한다.
    """
    bars = conn.execute(
        "SELECT COUNT(*) FROM daily_price WHERE open = 0 AND high = 0 AND low = 0"
    ).fetchone()[0]
    codes = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM daily_price WHERE open = 0 AND high = 0 AND low = 0"
    ).fetchone()[0]
    out.append(
        Finding(
            "OHL 이 0 인 행",
            f"{human(bars)}행 · {human(codes)}종목",
            "(막지 않음 · 적재 후 동일해야 한다)",
            True,
            False,
            "0 은 제약을 통과한다. 파생지표를 얹을 때 조용히 틀어지는 자리다",
        )
    )


def check_shares_identity(conn: sqlite3.Connection, out: list) -> None:
    """`market_cap = close × listed_shares` 가 아직 성립하는지.

    ADR-DS-0010 은 이 항등식이 **성립함에도** listed_shares 를 저장하기로 한 결정이다.
    역산을 안 쓰기로 했다고 항등식이 쓸모없어지지는 않는다 — 깨지는 순간이 **원본의
    정의가 바뀌었다는 신호**이기 때문이다(예: 상장주식수 → 유동주식수).

    막지 않는다. `CHECK` 제약으로 걸면 정의가 바뀐 첫 행에서 배치가 통째로 롤백된다.
    사람이 봐야 할 일이지 적재를 멈출 일이 아니다.
    """
    total, broken = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN market_cap <> close * listed_shares THEN 1 ELSE 0 END) "
        "FROM daily_price WHERE listed_shares IS NOT NULL AND close IS NOT NULL"
    ).fetchone()
    broken = broken or 0
    out.append(
        Finding(
            "market_cap = close × 주식수",
            f"어긋난 행 {human(broken)} / {human(total)}",
            "(막지 않음 · 깨지면 원본 정의가 바뀐 것이다)",
            True,
            False,
            "ADR-DS-0010 이 역산 대신 저장을 고른 근거. 깨져도 저장본은 영향이 없다",
        )
    )


def check_master_coverage(conn: sqlite3.Connection, out: list) -> None:
    """마스터 JSON 이 시세 종목코드를 덮는지 — securities 원천 선택의 근거."""
    codes = {r[0] for r in conn.execute("SELECT DISTINCT code FROM daily_price")}
    union: set[str] = set()
    for filename, (key, _) in MASTER_FILES.items():
        path = PROJECT_ROOT / "data" / filename
        if not path.exists():
            out.append(Finding(f"마스터 {filename}", "없음", "—", True, False, "건너뜀"))
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        mapping = payload.get(key, {}) if key else payload
        union |= set(mapping.keys())

    missing = sorted(codes - union)
    out.append(
        Finding(
            "마스터가 못 덮는 시세 종목",
            f"{len(missing)}개",
            "securities 원천을 daily_price 로 두면 0 (ADR-DS-0014 §1 — 그래서 그렇게 정했다)",
            True,
            False,
            ("예: " + ", ".join(missing[:5])) if missing else "",
        )
    )


def check_per_code_variance(conn: sqlite3.Connection, out: list) -> None:
    """종목 속성이 기간 안에서 변하는지 — securities 를 단일 행으로 접을 때의 손실량.

    전체 스캔이라 느리다(`--quick` 으로 건너뛴다). 한 번의 GROUP BY 로 다 센다.
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*)                                        AS codes,
            SUM(CASE WHEN ls > 1 THEN 1 ELSE 0 END)         AS ls_varying,
            SUM(CASE WHEN nm > 1 THEN 1 ELSE 0 END)         AS name_varying,
            SUM(CASE WHEN sc > 1 THEN 1 ELSE 0 END)         AS sector_varying
        FROM (
            SELECT code,
                          COUNT(DISTINCT listed_shares) AS ls,
                          COUNT(DISTINCT name)          AS nm,
                          COUNT(DISTINCT sector)        AS sc
            FROM daily_price GROUP BY code
        )
        """
    ).fetchone()
    codes, ls_varying, name_varying, sector_varying = row

    out.append(
        Finding(
            "listed_shares 가 변하는 종목",
            f"{human(ls_varying)} / {human(codes)}",
            "(ADR-DS-0010 — 그래서 ohlcv 에 따로 싣는다)",
            True,
            False,
            "securities 단일값으로 접으면 과거 회전율이 최신 주식수 기준이 된다"
            " — market_data.py:94·109",
        )
    )
    out.append(
        Finding(
            "name 이 변하는 종목",
            f"{human(name_varying)} / {human(codes)}",
            "(ADR-DS-0014 §2 — 접기로 한 대가)",
            True,
            False,
            "최신값으로 접으면 옛 이름 검색이 안 된다 — stock_service.py:154-159",
        )
    )
    out.append(
        Finding("sector 가 변하는 종목", f"{human(sector_varying)} / {human(codes)}", "(참고)", True, False)
    )

    # 최신 거래일에 없는 종목 — is_delisted 판단 후보
    gone = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM daily_price WHERE code NOT IN "
        "(SELECT code FROM daily_price WHERE bas_dd = (SELECT MAX(bas_dd) FROM daily_price))"
    ).fetchone()[0]
    out.append(
        Finding(
            "최신 거래일에 없는 종목",
            f"{human(gone)}",
            "(ADR-DS-0014 §3 — 추정하지 않기로 했다)",
            True,
            False,
            "상장폐지 후보. 원본에 폐지 정보가 없어 이건 추정이다",
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────────────────────


def render(findings: list) -> int:
    """검사 결과를 표로 찍고 종료 코드를 돌려준다."""
    width = max(len(f.name) for f in findings) + 2
    print("── 저장계층 전환 적합성 ──")
    print()
    for f in findings:
        mark = "  " if not f.fatal else ("OK" if f.ok else "!!")
        if f.fatal and f.ok:
            mark = "OK"
        print(f"{mark} {f.name:<{width}} {f.measured}")
        if f.target != "—":
            print(f"   {'':<{width}} 목표: {f.target}")
        if f.note:
            print(f"   {'':<{width}} ↳ {f.note}")
    print()

    broken = [f for f in findings if f.fatal and not f.ok]
    if broken:
        print(f"치명 {len(broken)}건 — 이대로 적재하면 그 행에서 배치가 롤백된다.")
        for f in broken:
            print(f"  · {f.name}: {f.measured} (목표 {f.target})")
        print()
        print("sql/init/01-schema.sql 을 먼저 고친다. 지금은 Postgres 가 비어 있어 비용이 0이다.")
        return 1

    print("치명 항목 없음 — 목표 DDL 로 적재할 수 있다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 원본이 목표 DDL 을 통과하는지 미리 잰다")
    parser.add_argument(
        "--quick", action="store_true", help="종목별 전체 스캔 항목을 건너뛴다(판단 근거용 지표)"
    )
    args = parser.parse_args()

    target = read_target_schema()
    conn = open_readonly()
    findings: list = []
    try:
        check_scale(conn, findings)
        check_ledger_consistency(conn, findings)
        check_numeric_fit(conn, target, findings)
        check_not_null(conn, target, findings)
        check_zero_bars(conn, findings)
        check_shares_identity(conn, findings)
        check_master_coverage(conn, findings)
        if not args.quick:
            check_per_code_variance(conn, findings)
    finally:
        conn.close()

    findings.append(
        Finding(
            "ohlcv.listed_shares",
            "DDL 에 있다" if target["has_listed_shares"] else "DDL 에 없다",
            "ADR-DS-0010 — 있어야 한다",
            target["has_listed_shares"],
            False,
            "빠지면 market_data.py:109 의 turnover 가 예외 없이 전 종목 0.0 이 된다",
        )
    )

    return render(findings)


if __name__ == "__main__":
    raise SystemExit(main())
