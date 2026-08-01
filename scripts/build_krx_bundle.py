#!/usr/bin/env python3
"""배포용 KRX 시세 번들 생성 스크립트 — 명세서 §8 (M3 변경 노트 N22)

배포본(Vercel)에는 `data/krx_cache.db`(123MB)를 올릴 수 없다. 그래서 **`/quant` 4종 ·
`/krx` 캔들 · `/market` 시장의 폭 · M3 전처리 캘린더**가 전부 배포본에서 죽어 있었다.
이 스크립트는 그 공백을 메울 **최소 크기의 파생물**을 만든다.

만드는 것 두 가지
----------------
| 산출물 | 크기 | 담는 것 | git |
|---|---|---|---|
| `data/krx_bundle.db` | ≈30MB | 전종목 최근 150거래일 OHLCV + 종목 메타 | ❌ 제외 |
| `data/krx_derived.json` | ≈25KB | 거래일 캘린더 · 시장의 폭 일별 집계 (전 구간) | ✅ 포함 |

**왜 JSON 이 아니라 SQLite 인가.** 같은 내용을 JSON 으로 담으면 gzip 12.9MB 인데,
서버리스는 요청마다 인스턴스가 새로 뜰 수 있어 **콜드스타트마다 그걸 통째로 풀어 파싱**하게 된다.
SQLite 는 읽기 전용으로 열어 인덱스로 필요한 행만 집으므로 실측 **0ms** 다. 파일이 조금 커도
이쪽이 맞다 (파이썬 함수 번들 한도는 500MB, 의존성 280MB 를 빼도 여유가 크다).

**왜 150거래일인가.** `/quant` 세 API 는 전부 60거래일(`market_data.DEFAULT_WINDOW`),
`/krx` 캔들 기본값은 120거래일이다. 150이면 둘 다 손실 없이 덮고 M4~M6 여유까지 남는다.
기간을 줄이는 대신 **종목은 자르지 않는다** — 스크리닝 깔때기의 첫 단계가
"전체 상장 종목 2,763" 이라 상위 N개로 자르면 그 화면의 존재 이유가 사라진다.

**왜 시장의 폭·캘린더는 따로 빼는가.** 둘 다 날짜별 집계라 종목별 원본이 필요 없다.
25KB 면 git 에 그대로 올릴 수 있고, 150거래일이 아니라 **캐시 전 구간(282일)** 을 담을 수 있다.

    python3 scripts/build_krx_bundle.py              # 둘 다 만든다
    python3 scripts/build_krx_bundle.py --days 250   # 기간을 바꾼다
    python3 scripts/build_krx_bundle.py --check      # 현재 산출물 상태만 본다

솔직하게 남기는 것들
------------------
- 번들 DB 는 **150거래일치뿐**이다. 그보다 긴 구간을 물으면 있는 만큼만 주고
  `app/repositories/krx_bundle.py` 가 그 사실을 `stats()` 로 밝힌다. 채워 넣지 않는다.
- 번들은 **수동 갱신**이다. 원본 캐시를 새로 받은 뒤 이 스크립트를 다시 돌리고 배포해야
  기준일이 따라온다. 뒤처진 정도는 대시보드 '데이터 상태' 가 보여 준다.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 스크립트를 어디서 실행하든 저장소 루트를 기준으로 삼는다
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_DB = PROJECT_ROOT / "data" / "krx_cache.db"
BUNDLE_DB = PROJECT_ROOT / "data" / "krx_bundle.db"
DERIVED_JSON = PROJECT_ROOT / "data" / "krx_derived.json"

# 번들에 담을 거래일 수 (위 머리말 참고)
DEFAULT_DAYS = 150

# 번들 DB 가 담는 시세 컬럼.
#
# `change`(전일대비)는 `close` 와 `change_rate` 로 역산할 수 있어 한 번 뺐다가 되돌렸다.
# `change_rate` 가 소수 2자리라 역산하면 삼성전자에서 55,500원이 55,497원으로 나왔다.
# 컬럼 하나에 1.2MB(전체의 4%)를 쓰고 정확한 값을 담는 편이 낫다 —
# 화면마다 3원씩 어긋나는 수를 두면 "이 데이터를 믿어도 되나" 부터 다시 묻게 된다.
PRICE_COLUMNS = ("open", "high", "low", "close", "change", "change_rate", "volume", "value")

KST = timezone(timedelta(hours=9))

BUNDLE_SCHEMA = f"""
-- 종목 메타는 날짜마다 반복되지 않도록 따로 뺀다.
-- (원본은 78만 행마다 종목명·시장·소속부를 되풀이해 담고 있어 그것만으로 수십 MB 다)
CREATE TABLE stock (
  code          TEXT PRIMARY KEY,
  name          TEXT,
  market        TEXT,
  sector        TEXT,
  listed_shares INTEGER,
  market_cap    INTEGER      -- 기준일 시점의 시가총액
);

-- WITHOUT ROWID: 기본키가 곧 저장 순서가 되어 숨은 rowid 열과 그 인덱스가 사라진다.
CREATE TABLE daily_price (
  bas_dd TEXT NOT NULL,
  code   TEXT NOT NULL,
  {", ".join(f"{c} {'REAL' if c == 'change_rate' else 'INTEGER'}" for c in PRICE_COLUMNS)},
  PRIMARY KEY (bas_dd, code)
) WITHOUT ROWID;

-- 종목 하나의 시계열(`series`)을 뽑을 때 쓴다. 없으면 33만 행을 전부 훑는다.
CREATE INDEX idx_code_date ON daily_price(code, bas_dd);

-- 이 번들이 무엇인지 스스로 밝힌다. 읽는 쪽이 기준일·기간을 물어볼 수 있어야
-- "언제 것인지 모르는 데이터" 가 되지 않는다.
CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / 1e6, 1)
    except OSError:
        return 0.0


# ==================================================
# 1. 번들 DB — 전종목 최근 N거래일
# ==================================================
def build_bundle(days: int = DEFAULT_DAYS) -> dict:
    """원본 캐시에서 최근 `days` 거래일을 뽑아 슬림 DB 로 다시 쓴다."""
    started = time.time()
    source = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row

    latest = source.execute("SELECT MAX(bas_dd) FROM daily_price").fetchone()[0]
    if not latest:
        raise SystemExit("원본 캐시가 비어 있습니다. `python3 scripts/fetch_krx.py` 를 먼저 실행하세요.")

    # 최근 N거래일의 **첫 날짜**를 구한다. 이 날짜 이상만 담는다.
    floor = source.execute(
        "SELECT MIN(bas_dd) FROM (SELECT DISTINCT bas_dd FROM daily_price"
        " ORDER BY bas_dd DESC LIMIT ?)", (days,)).fetchone()[0]

    # 이어 만들지 않고 매번 새로 쓴다. 스키마가 바뀌었을 때 옛 파일이 남아 섞이는 것을 막는다.
    for leftover in (BUNDLE_DB, Path(f"{BUNDLE_DB}-wal"), Path(f"{BUNDLE_DB}-shm")):
        if leftover.exists():
            leftover.unlink()

    target = sqlite3.connect(BUNDLE_DB)
    target.executescript(BUNDLE_SCHEMA)

    # ── 종목 메타 (기준일 한 장에서 뽑는다) ──
    target.executemany(
        "INSERT OR REPLACE INTO stock VALUES (?,?,?,?,?,?)",
        source.execute(
            "SELECT code, name, market, sector, listed_shares, market_cap"
            " FROM daily_price WHERE bas_dd = ?", (latest,)).fetchall(),
    )

    # ── 시세 ──
    # fetchall() 로 한꺼번에 들고 있지 않고 커서를 그대로 넘긴다 (33만 행 × 9열).
    columns = ",".join(PRICE_COLUMNS)
    placeholders = ",".join("?" * (len(PRICE_COLUMNS) + 2))
    target.executemany(
        f"INSERT INTO daily_price VALUES ({placeholders})",
        source.execute(
            f"SELECT bas_dd, code, {columns} FROM daily_price WHERE bas_dd >= ?", (floor,)),
    )

    rows = target.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    codes = target.execute("SELECT COUNT(DISTINCT code) FROM daily_price").fetchone()[0]
    dates = target.execute("SELECT COUNT(DISTINCT bas_dd) FROM daily_price").fetchone()[0]

    target.executemany("INSERT OR REPLACE INTO bundle_meta VALUES (?,?)", [
        ("generated_at", _now_kst()),
        ("first_date", floor),
        ("last_date", latest),
        ("days", str(dates)),
        ("rows", str(rows)),
        ("codes", str(codes)),
        ("source", "data/krx_cache.db"),
        ("note", f"배포 번들용 축약본입니다. 최근 {dates}거래일만 담았고 그 이전은 없습니다."),
    ])
    target.commit()

    # VACUUM — INSERT 중 생긴 빈 페이지를 회수한다. 이걸 빼면 파일이 1.5배쯤 커진다.
    target.execute("VACUUM")
    target.commit()
    target.close()
    source.close()

    return {
        "path": str(BUNDLE_DB.relative_to(PROJECT_ROOT)),
        "size_mb": _size_mb(BUNDLE_DB),
        "days": dates, "rows": rows, "codes": codes,
        "first_date": floor, "last_date": latest,
        "elapsed": round(time.time() - started, 1),
    }


# ==================================================
# 2. 파생 JSON — 거래일 캘린더 · 시장의 폭
# ==================================================
def build_derived() -> dict:
    """날짜별 집계만 뽑는다. 종목별 원본이 필요 없어 캐시 **전 구간**을 담을 수 있다.

    담는 것
      `trading_days`  실제 개장일 목록 (`YYYY-MM-DD`, 오름차순)
                      → M3 전처리의 `trading_days` 인자. 연휴를 결측으로 오판하지 않게 한다
      `breadth`       날짜별 상승·하락·보합 종목 수와 거래대금
                      → `/market` 시장의 폭. 종목별 데이터 없이도 그릴 수 있다
    """
    started = time.time()
    source = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row

    # 등락률의 부호로 센다. `change_rate` 가 NULL 인 줄은 보합으로 보지 않고 아예 뺀다 —
    # 값을 모르는 것과 0인 것은 다르다.
    rows = source.execute("""
        SELECT bas_dd,
               SUM(CASE WHEN change_rate > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN change_rate < 0 THEN 1 ELSE 0 END) AS down,
               SUM(CASE WHEN change_rate = 0 THEN 1 ELSE 0 END) AS flat,
               SUM(COALESCE(value, 0))                          AS value,
               COUNT(*)                                         AS total
        FROM daily_price
        WHERE change_rate IS NOT NULL
        GROUP BY bas_dd
        ORDER BY bas_dd
    """).fetchall()
    source.close()

    if not rows:
        raise SystemExit("원본 캐시가 비어 있습니다.")

    trading_days = [f"{r['bas_dd'][:4]}-{r['bas_dd'][4:6]}-{r['bas_dd'][6:]}" for r in rows]
    payload = {
        "generated_at": _now_kst(),
        "source": "data/krx_cache.db",
        "first_date": trading_days[0],
        "last_date": trading_days[-1],
        "trading_days": trading_days,
        # 배열 6개로 나란히 담는다. 딕셔너리 목록보다 키 반복이 없어 3배쯤 작다.
        "breadth": {
            "up":    [r["up"] for r in rows],
            "down":  [r["down"] for r in rows],
            "flat":  [r["flat"] for r in rows],
            "value": [r["value"] for r in rows],
            "total": [r["total"] for r in rows],
        },
        "note": "거래일 캘린더와 시장의 폭은 날짜별 집계라 종목별 원본 없이 만들 수 있습니다. "
                "번들 DB(150거래일)보다 긴 구간을 담습니다.",
    }
    DERIVED_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    return {
        "path": str(DERIVED_JSON.relative_to(PROJECT_ROOT)),
        "size_kb": round(DERIVED_JSON.stat().st_size / 1024, 1),
        "days": len(trading_days),
        "first_date": trading_days[0], "last_date": trading_days[-1],
        "elapsed": round(time.time() - started, 1),
    }


# ==================================================
# 3. 현황 확인
# ==================================================
def check() -> None:
    """만들어 둔 산출물이 무엇을 담고 있는지 보여 준다. (배포 전 확인용)"""
    print(f"원본 캐시   {SOURCE_DB.relative_to(PROJECT_ROOT)} · "
          f"{_size_mb(SOURCE_DB)}MB" + ("" if SOURCE_DB.exists() else " — 없음"))

    if BUNDLE_DB.exists():
        conn = sqlite3.connect(f"file:{BUNDLE_DB}?mode=ro", uri=True)
        meta = dict(conn.execute("SELECT key, value FROM bundle_meta").fetchall())
        conn.close()
        print(f"번들 DB     {BUNDLE_DB.relative_to(PROJECT_ROOT)} · {_size_mb(BUNDLE_DB)}MB · "
              f"{meta.get('days')}거래일 · {int(meta.get('rows', 0)):,}행 · "
              f"{meta.get('codes')}종목 · {meta.get('first_date')}~{meta.get('last_date')} · "
              f"생성 {meta.get('generated_at')}")
    else:
        print("번들 DB     없음 — `python3 scripts/build_krx_bundle.py` 로 만드세요.")

    if DERIVED_JSON.exists():
        data = json.loads(DERIVED_JSON.read_text(encoding="utf-8"))
        print(f"파생 JSON   {DERIVED_JSON.relative_to(PROJECT_ROOT)} · "
              f"{round(DERIVED_JSON.stat().st_size / 1024, 1)}KB · "
              f"{len(data.get('trading_days', []))}거래일 · "
              f"{data.get('first_date')}~{data.get('last_date')} · "
              f"생성 {data.get('generated_at')}")
    else:
        print("파생 JSON   없음")


def main() -> None:
    parser = argparse.ArgumentParser(description="배포용 KRX 시세 번들을 만든다.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"번들 DB 에 담을 거래일 수 (기본 {DEFAULT_DAYS})")
    parser.add_argument("--check", action="store_true", help="현재 산출물 상태만 확인한다")
    parser.add_argument("--derived-only", action="store_true",
                        help="파생 JSON 만 다시 만든다 (빠름)")
    args = parser.parse_args()

    if args.check:
        check()
        return

    if not SOURCE_DB.exists():
        raise SystemExit(f"원본 캐시가 없습니다: {SOURCE_DB}\n"
                         "`python3 scripts/fetch_krx.py --days 250` 으로 먼저 채우세요.")

    print(f"[1/2] 파생 JSON (거래일 캘린더 · 시장의 폭) …")
    derived = build_derived()
    print(f"      {derived['path']} · {derived['size_kb']}KB · {derived['days']}거래일 "
          f"({derived['first_date']}~{derived['last_date']}) · {derived['elapsed']}초")

    if args.derived_only:
        return

    print(f"[2/2] 번들 DB (전종목 {args.days}거래일) …")
    bundle = build_bundle(args.days)
    print(f"      {bundle['path']} · {bundle['size_mb']}MB · {bundle['days']}거래일 · "
          f"{bundle['rows']:,}행 · {bundle['codes']:,}종목 "
          f"({bundle['first_date']}~{bundle['last_date']}) · {bundle['elapsed']}초")
    print()
    print("배포하려면 `vercel --prod` 를 실행하세요. "
          "번들 DB 는 git 에 올리지 않고(.gitignore) 배포 번들에만 실립니다.")


if __name__ == "__main__":
    main()
