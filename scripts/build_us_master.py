#!/usr/bin/env python3
"""미국 종목 마스터(`data/us_master.json`) 생성 스크립트

HTS 스타일 자동완성(`GET /api/search`)이 미국 종목을 찾으려면 **티커 ↔ 종목명** 목록이 필요하다.
야후 파이낸스에는 "전체 종목 목록" API 가 없으므로, 나스닥이 공개하는 **공식 심볼 디렉터리**를 쓴다.

    https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   나스닥 상장
    https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    NYSE·AMEX·ARCA 등

두 파일은 `|` 로 구분된 텍스트이고, 마지막 줄에 파일 생성 시각이 붙어 있다.

    python3 scripts/build_us_master.py            # 새로 받아 생성
    python3 scripts/build_us_master.py --check    # 현재 파일 상태만 확인

국내 종목 마스터는 `scripts/build_stock_master.py` 가 만든다. 둘 다 저장소에 커밋해 두므로
배포 환경(DB 없음)에서도 자동완성이 그대로 동작한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[1]
MASTER_PATH = BASE_DIR / "data" / "us_master.json"

SOURCES = (
    # (URL, 티커 컬럼명, 거래소 컬럼명 또는 고정값)
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol", "NASDAQ"),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol", None),
)

# otherlisted.txt 의 거래소 코드 → 읽기 쉬운 이름
EXCHANGES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}

# 종목명 뒤에 붙는 상투적인 꼬리표. 자동완성 목록에서는 잡음이라 떼어 낸다.
# (긴 것부터 지워야 "- Class A Common Stock" 이 "- Common Stock" 보다 먼저 걸린다)
NAME_SUFFIXES = (
    " - Common Stock", " - Common Shares", " - Ordinary Shares",
    " - Class A Common Stock", " - Class B Common Stock",
    " - Class A Ordinary Shares", " - Class B Ordinary Shares",
    " Common Stock", " Common Shares",
)


def _fetch(url: str) -> list:
    """파이프 구분 텍스트를 받아 딕셔너리 목록으로 바꾼다."""
    # 기본 파이썬 UA 는 막힐 수 있어 브라우저 UA 로 요청한다 (kosis_rss.py 와 같은 이유)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (api-test build script)"})
    with urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        # 마지막 줄은 "File Creation Time: ..." 이라 컬럼 수가 맞지 않는다. 건너뛴다.
        parts = line.split("|")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def _clean_name(raw: str) -> str:
    """종목명에서 상투적인 꼬리표를 떼고 공백을 정리한다."""
    name = (raw or "").strip()
    for suffix in sorted(NAME_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name.rstrip(" -").strip()


SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _fetch_sp500() -> set:
    """S&P 500 편입 종목 티커를 받아 온다. (자동완성 우선순위용)

    미국 종목은 나스닥 파일에 시가총액이 없어서, `TES` 를 쳤을 때 테슬라(TSLA)보다
    이름이 비슷한 잡다한 ETF 가 먼저 나오는 문제가 있었다. **S&P 500 편입 여부**를
    "널리 알려진 기업" 의 근사값으로 삼아 위로 올린다.

    위키백과를 못 읽어도 빌드를 멈추지는 않는다 (우선순위만 덜 정확해진다).
    """
    try:
        from bs4 import BeautifulSoup

        request = Request(SP500_URL, headers={"User-Agent": "Mozilla/5.0 (api-test build script)"})
        with urlopen(request, timeout=60) as response:
            soup = BeautifulSoup(response.read().decode("utf-8", errors="replace"), "html.parser")

        table = soup.find("table", {"id": "constituents"})
        if table is None:
            return set()

        symbols = set()
        for tr in table.find_all("tr")[1:]:          # 첫 줄은 머리글
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            symbol = cells[0].get_text(strip=True)
            if re.fullmatch(r"[A-Z.\-]{1,6}", symbol):
                symbols.add(symbol)
        return symbols
    except Exception as error:                        # 네트워크·표 구조 변경 등
        print(f"  [안내] S&P 500 목록을 받지 못했습니다 ({error}). 우선순위 없이 진행합니다.")
        return set()


def build() -> dict:
    """두 파일을 받아 `{티커: [종목명, 거래소, ETF여부, S&P500여부]}` 로 합친다."""
    master: dict = {}

    for url, symbol_col, fixed_exchange in SOURCES:
        rows = _fetch(url)
        if not rows:
            raise SystemExit(f"목록을 받지 못했습니다: {url}")

        for row in rows:
            symbol = (row.get(symbol_col) or "").strip().upper()
            # 테스트용 가상 종목은 실제로 거래되지 않으므로 뺀다
            if not symbol or row.get("Test Issue", "N").strip() == "Y":
                continue
            # 야후 티커에 쓸 수 없는 기호가 붙은 것들(우선주 등)은 조회가 안 되므로 뺀다
            if not symbol.replace(".", "").replace("-", "").isalnum():
                continue

            name = _clean_name(row.get("Security Name", ""))
            if not name:
                continue

            exchange = fixed_exchange or EXCHANGES.get(
                (row.get("Exchange") or "").strip(), "US")
            is_etf = 1 if (row.get("ETF") or "").strip() == "Y" else 0

            # 두 파일에 겹쳐 나오면 먼저 읽은 쪽(나스닥)을 남긴다
            master.setdefault(symbol, [name, exchange, is_etf, 0])

        print(f"  {url.rsplit('/', 1)[-1]:20} {len(rows):>6}줄 → 누적 {len(master):,}종목")

    sp500 = _fetch_sp500()
    for symbol in sp500:
        if symbol in master:
            master[symbol][3] = 1
    print(f"  S&P 500 표시              {len(sp500):>6}개 중 {sum(1 for v in master.values() if v[3]):,}개 매칭")

    return dict(sorted(master.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description="미국 종목 마스터 JSON 생성")
    parser.add_argument("--check", action="store_true", help="생성하지 않고 상태만 확인")
    args = parser.parse_args()

    if args.check:
        if not MASTER_PATH.exists():
            print(f"❌ 없음 — {MASTER_PATH}")
            raise SystemExit(1)
        data = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        etf = sum(1 for v in data.values() if v[2])
        sp = sum(1 for v in data.values() if len(v) > 3 and v[3])
        print(f"✅ {MASTER_PATH.relative_to(BASE_DIR)} — {len(data):,}종목 "
              f"({MASTER_PATH.stat().st_size / 1024:.0f}KB, ETF {etf:,}개, S&P500 {sp:,}개)")
        return

    print("▶ 나스닥 공식 심볼 디렉터리 수신")
    data = build()
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    MASTER_PATH.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"✅ {MASTER_PATH.relative_to(BASE_DIR)} 생성 — "
          f"{len(data):,}종목 · {MASTER_PATH.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    main()
