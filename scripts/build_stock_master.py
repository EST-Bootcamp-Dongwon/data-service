#!/usr/bin/env python3
"""종목 마스터(`data/stock_master.json`) 생성 스크립트

`data/krx_cache.db` 는 96MB 라 저장소에 올릴 수 없다(`.gitignore` 대상).
하지만 `/stock` 화면의 **티커 판별**에 필요한 것은 셋뿐이다 — 종목코드 · 종목명 · 시장 구분.
그 셋만 뽑아 두면 98KB 밖에 안 되므로, 이것만 저장소에 함께 올린다.

이 파일이 있으면 **DB 없이도** 아래가 그대로 동작한다.
  - `005930` → 코스피니까 `005930.KS`
  - `247540` → 코스닥이니까 `247540.KQ`   (`.KS` 로 물으면 엉뚱한 값이 온다)
  - `삼성전자` → `005930`                  (한글 종목명 검색)

Codespaces·배포 환경처럼 DB 를 못 올리는 곳에서 특히 중요하다.

    python3 scripts/build_stock_master.py            # 캐시 최신 거래일 기준으로 생성
    python3 scripts/build_stock_master.py --check    # 생성하지 않고 현재 파일 상태만 확인

시세를 새로 수집한 뒤(`scripts/fetch_krx.py`) 신규 상장 종목을 반영하려면 다시 돌리면 된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 이 스크립트는 scripts/ 안에 있다. 프로젝트 루트를 import 경로에 넣어야
# `app.*` 를 찾을 수 있다 (`python3 scripts/build_stock_master.py` 로 바로 실행하기 위함).
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.repositories import krx_store as store  # noqa: E402  (경로 설정 뒤에 import)

MASTER_PATH = BASE_DIR / "data" / "stock_master.json"


def build() -> dict:
    """캐시의 **가장 최근 거래일** 기준으로 종목 마스터를 만든다.

    최근 거래일 하루치만 보는 이유: 상장폐지된 종목까지 넣으면 이름이 겹쳐
    검색 결과가 엉뚱해진다. "지금 거래되는 종목" 만 담는 편이 맞다.
    """
    with store.connect() as conn:
        # 시가총액이 큰 순으로 뽑는다. 이 순서가 곧 "유명한 정도"의 근사값이라
        # 자동완성에서 무엇을 먼저 보여줄지 정하는 데 쓴다.
        # (삼성을 치면 삼성화재보다 삼성전자가 먼저 나와야 한다.)
        rows = conn.execute(
            """
            SELECT code, name, market, market_cap FROM daily_price
            WHERE bas_dd = (SELECT MAX(bas_dd) FROM daily_price)
            ORDER BY COALESCE(market_cap, 0) DESC, code
            """
        ).fetchall()

    if not rows:
        raise SystemExit(
            "시세 캐시가 비어 있습니다. 먼저 `python3 scripts/fetch_krx.py` 를 실행하세요."
        )

    # {종목코드: [종목명, 시장, 시총순위]}
    # 배열로 두면 키 이름이 반복되지 않아 파일이 작아진다.
    # 시총순위는 1부터. 금액을 그대로 담으면 파일이 커지고, 순위만 있으면 정렬에 충분하다.
    return {
        row["code"]: [row["name"], row["market"], rank]
        for rank, row in enumerate(rows, start=1)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="종목 마스터 JSON 생성")
    parser.add_argument("--check", action="store_true",
                        help="생성하지 않고 현재 파일 상태만 확인한다")
    args = parser.parse_args()

    if args.check:
        if not MASTER_PATH.exists():
            print(f"❌ 없음 — {MASTER_PATH}")
            raise SystemExit(1)
        data = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        size_kb = MASTER_PATH.stat().st_size / 1024
        markets: dict = {}
        for value in data.values():
            market = value[1]
            markets[market] = markets.get(market, 0) + 1
        print(f"✅ {MASTER_PATH.relative_to(BASE_DIR)} — {len(data):,}종목 · {size_kb:.0f}KB")
        print(f"   시장별: {markets}")
        return

    data = build()
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False 로 한글을 그대로 저장한다 (\uXXXX 로 쓰면 파일이 3배가 된다).
    # separators 로 공백을 빼서 크기를 더 줄인다.
    MASTER_PATH.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_kb = MASTER_PATH.stat().st_size / 1024
    print(f"✅ {MASTER_PATH.relative_to(BASE_DIR)} 생성 — {len(data):,}종목 · {size_kb:.0f}KB")


if __name__ == "__main__":
    main()
