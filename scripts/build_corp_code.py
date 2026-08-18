#!/usr/bin/env python3
"""DART 고유번호 매핑(`data/corp_code.json`) 생성 스크립트

DART 는 종목코드(`005930`)로 조회할 수 없다. **8자리 고유번호(`corp_code`)** 만 받는다.
그런데 그 매핑표는 `corpCode.xml` 한 곳에서만 주고, 압축을 풀면 **전 법인 약 10만 건**이다.
요청할 때마다 10만 건을 내려받을 수는 없으므로 미리 뽑아 둔다.

우리에게 필요한 것은 **상장사뿐**이다. 비상장사는 종목코드 칸이 비어 있어 그걸로 거른다.
그러면 3천 건 안팎, 수백 KB 로 줄어 저장소·배포 번들에 함께 올릴 수 있다
(명세서 §2.2 — `stock_master.json` 과 같은 취급).

    python3 scripts/build_corp_code.py            # DART 에서 받아 새로 만든다
    python3 scripts/build_corp_code.py --check    # 만들지 않고 현재 파일 상태만 확인

DART 인증키가 `.key` 에 있어야 한다. 신규 상장·상호 변경을 반영하려면 가끔 다시 돌리면 된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 이 스크립트는 scripts/ 안에 있다. 프로젝트 루트를 import 경로에 넣어야
# `app.*` 를 찾을 수 있다 (`python3 scripts/build_corp_code.py` 로 바로 실행하기 위함).
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.clients import dart_data  # noqa: E402  (경로 설정 뒤에 import)

OUTPUT_PATH = BASE_DIR / "data" / "corp_code.json"
KST = timezone(timedelta(hours=9))


def build() -> dict:
    """DART 에서 전 법인 목록을 받아 **상장사만** 추려 매핑을 만든다.

    돌려주는 구조는 종목코드를 열쇠로 삼는다. 조회는 언제나 종목코드에서 출발하기 때문이다.

        { "005930": {"corp_code": "00126380", "corp_name": "삼성전자"} , ... }
    """
    print("DART corpCode.xml 을 내려받는 중… (약 10만 건, 20~60초 걸립니다)")
    rows = dart_data.fetch_corp_code_rows()
    print(f"  전 법인 {len(rows):,}건을 받았습니다.")

    mapping: dict[str, dict] = {}
    duplicates = 0

    for row in rows:
        stock_code = row["stock_code"].strip()
        # 비상장사는 종목코드 칸이 비어 있다. 6자리가 아닌 것도 거른다.
        if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
            continue

        entry = {"corp_code": row["corp_code"], "corp_name": row["corp_name"]}

        # 같은 종목코드가 두 번 나오면 **수정일이 최신인 쪽**을 남긴다.
        # 합병·재상장으로 예전 법인 기록이 함께 남아 있는 경우가 있다.
        existing = mapping.get(stock_code)
        if existing:
            duplicates += 1
            if row["modify_date"] <= existing.get("_modify", ""):
                continue
        entry["_modify"] = row["modify_date"]
        mapping[stock_code] = entry

    # 내부 판단용으로만 쓴 수정일은 파일에 남기지 않는다 (용량·가독성)
    for entry in mapping.values():
        entry.pop("_modify", None)

    payload = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "DART corpCode.xml",
        "total_corps": len(rows),
        "listed_count": len(mapping),
        "map": dict(sorted(mapping.items())),      # 종목코드 순 — diff 를 읽기 쉽게 한다
    }
    if duplicates:
        print(f"  종목코드 중복 {duplicates}건은 수정일이 최신인 쪽만 남겼습니다.")
    return payload


def save(payload: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\n저장 완료 — {OUTPUT_PATH.relative_to(BASE_DIR)} "
          f"({payload['listed_count']:,}개 상장사 · {size_kb:.0f}KB)")


def check() -> int:
    """현재 파일 상태만 확인한다. 없으면 1 을 돌려준다(스크립트 종료 코드)."""
    if not OUTPUT_PATH.exists():
        print(f"{OUTPUT_PATH.relative_to(BASE_DIR)} 가 없습니다. "
              "`python3 scripts/build_corp_code.py` 로 만드세요.")
        return 1

    payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    mapping = payload.get("map", {})
    print(f"{OUTPUT_PATH.relative_to(BASE_DIR)}")
    print(f"  생성 시각 : {payload.get('generated_at', '?')}")
    print(f"  상장사     : {len(mapping):,}개 (전 법인 {payload.get('total_corps', 0):,}건 중)")
    print(f"  크기       : {OUTPUT_PATH.stat().st_size / 1024:.0f}KB")

    # 눈으로 확인할 수 있게 잘 알려진 종목 몇 개를 찍어 본다
    for code in ("005930", "000660", "035720", "247540"):
        entry = mapping.get(code)
        print(f"  {code} → {entry['corp_code']} {entry['corp_name']}" if entry
              else f"  {code} → 없음")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DART 고유번호 매핑 생성")
    parser.add_argument("--check", action="store_true",
                        help="생성하지 않고 현재 파일 상태만 확인한다")
    args = parser.parse_args()

    if args.check:
        return check()

    try:
        save(build())
    except dart_data.DartError as error:
        # 인증키 누락·DART 장애는 사용자가 고칠 수 있는 문제라 안내만 하고 조용히 끝낸다
        print(f"\n실패 — {error}", file=sys.stderr)
        return 1
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
