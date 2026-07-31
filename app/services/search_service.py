"""종목 자동완성 검색 (서비스 계층)

HTS(홈트레이딩시스템)처럼 **입력할 때마다** 연관 종목을 띄우려면 응답이 아주 빨라야 한다.
사용자가 "삼" → "삼성" → "삼성전" 을 치는 사이 요청이 세 번 날아가므로,
한 번이라도 느리면 목록이 입력을 따라오지 못한다.

그래서 종목 목록을 **서버가 뜰 때 메모리에 한 번만 올려 두고**, 그 뒤로는 파일도 DB 도
건드리지 않는다. 15,000종목을 훑어도 몇 밀리초면 끝난다.

    data/stock_master.json   국내  2,764종목  (scripts/build_stock_master.py)
    data/us_master.json      미국 12,650종목  (scripts/build_us_master.py)

두 파일 모두 저장소에 함께 올라가므로 배포 환경(DB 없음)에서도 그대로 동작한다.

정렬 규칙
--------
같은 "포함" 이라도 사용자가 기대하는 순서가 다르다. `AAP` 를 쳤을 때 `AAPL`(애플)이
맨 위에 와야지, 이름 어딘가에 aap 가 들어간 종목이 먼저 오면 안 된다. 그래서 점수를 매긴다.

| 점수 | 조건 | 예 (`삼성` · `AAP`) |
|---|---|---|
| 0 | 티커/종목코드가 정확히 일치 | `AAPL` 검색 → AAPL |
| 1 | 티커가 검색어로 시작 | `AAP` → **AAPL** |
| 2 | 종목명이 검색어로 시작 | `삼성` → **삼성전자** |
| 3 | 종목명에 검색어가 포함 | `전자` → LG전자 |
| 4 | 티커 중간에 포함 | `APL` → AAPL |

점수만으로는 부족하다. `삼성` 은 삼성전자·삼성화재·삼성제약이 전부 점수 2 라
무엇을 먼저 보여줄지 정할 수 없다. 그래서 **중요도**를 함께 본다.

    국내 — 시가총액 순위 (삼성전자 1위 → 맨 위)
    미국 — S&P 500 편입 여부 (나스닥 파일에는 시가총액이 없다)

**ETF 는 언제나 뒤로 민다.** ETF 이름에는 유명 종목 티커가 그대로 들어가서
(`2x Long TSLA Daily ETF`), 그냥 두면 `TES` 검색에 테슬라보다 ETF 가 먼저 나온다.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

# 이 파일은 app/services/ 안에 있으므로 parents[2] 가 프로젝트 루트다.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
KR_MASTER = DATA_DIR / "stock_master.json"
US_MASTER = DATA_DIR / "us_master.json"

# KRX 시장 구분 → 야후 접미사 (stock_service 와 같은 규칙)
MARKET_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ", "KONEX": ".KN"}

MAX_LIMIT = 50          # 한 번에 돌려줄 수 있는 최대 개수 (기본은 10)
HANGUL = re.compile(r"[가-힣]")

# 메모리에 올려 둔 종목 목록. 서버가 살아 있는 동안 그대로 재사용한다.
_index: Optional[List[dict]] = None
_lock = threading.Lock()        # 요청 두 개가 동시에 들어와도 두 번 읽지 않도록


def _load_json(path: Path) -> dict:
    """마스터 파일을 읽는다. 없으면 빈 딕셔너리 (한쪽이 없어도 나머지는 검색되게)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _build_index() -> List[dict]:
    """국내·미국 마스터를 하나의 검색용 목록으로 합친다.

    검색할 때마다 `lower()` 를 부르면 15,000번씩 문자열을 새로 만들게 되므로,
    소문자로 바꾼 값(`_t`·`_n`)을 미리 만들어 둔다. 이 준비가 검색 속도를 좌우한다.
    """
    entries: List[dict] = []

    for code, value in _load_json(KR_MASTER).items():
        # [종목명, 시장, 시총순위] — 순위가 없는 옛 파일도 읽을 수 있게 기본값을 둔다
        name, market, cap_rank = (list(value) + ["", "", 99999])[:3]
        suffix = MARKET_SUFFIX.get(market, ".KS")
        entries.append({
            "ticker": f"{code}{suffix}",   # 야후 조회에 쓰는 값
            "code": code,                  # 화면에 보여 주는 6자리 코드
            "name": name,
            "market": "KR",
            "exchange": market,            # KOSPI · KOSDAQ
            "is_etf": False,
            "_t": code.lower(),
            "_n": name.lower(),
            # 시가총액 순위를 그대로 중요도로 쓴다 (1위 삼성전자 → 가장 먼저)
            "_p": int(cap_rank) if str(cap_rank).isdigit() else 99999,
        })

    for symbol, value in _load_json(US_MASTER).items():
        # [종목명, 거래소, ETF여부, S&P500여부]
        name, exchange, is_etf, in_sp500 = (list(value) + ["", "", 0, 0])[:4]
        entries.append({
            "ticker": symbol,
            "code": symbol,
            "name": name,
            "market": "US",
            "exchange": exchange,
            "is_etf": bool(is_etf),
            "_t": symbol.lower(),
            "_n": (name or "").lower(),
            # 미국은 시가총액이 없어 S&P 500 편입 여부를 중요도 대용으로 쓴다.
            # 편입 종목은 0(먼저), 나머지는 1000(뒤).
            "_p": 0 if in_sp500 else 1000,
        })

    return entries


def get_index() -> List[dict]:
    """메모리에 올려 둔 종목 목록을 돌려준다. 처음 부를 때만 파일을 읽는다."""
    global _index
    if _index is None:
        with _lock:
            if _index is None:          # 잠금을 기다리는 사이 다른 요청이 채웠을 수 있다
                _index = _build_index()
    return _index


def warm_up() -> int:
    """서버 시작 시 미리 읽어 둔다. 첫 검색이 느려지지 않게 하려는 것.

    돌려주는 값은 올라간 종목 수 (기동 로그에 찍어 확인용으로 쓴다).
    """
    return len(get_index())


def _score(entry: dict, needle: str) -> Optional[int]:
    """검색어와 얼마나 잘 맞는지 점수를 매긴다. 안 맞으면 `None`. (낮을수록 먼저)"""
    ticker, name = entry["_t"], entry["_n"]

    if ticker == needle:
        return 0
    if ticker.startswith(needle):
        return 1
    if name.startswith(needle):
        return 2
    if needle in name:
        return 3
    if needle in ticker:
        return 4
    return None


def search(query: str, limit: int = 10, market: str = "") -> List[dict]:
    """검색어가 포함된 종목을 점수 순으로 최대 `limit` 개 돌려준다.

    - `market` 에 `KR`·`US` 를 주면 그 시장만 추린다 (안 주면 둘 다).
    - 한 글자만 입력해도 동작하지만, 결과가 너무 많으므로 화면에서 2글자부터 부른다.
      (영문 티커는 1글자로도 의미가 있어 서버는 막지 않는다.)
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []

    limit = max(1, min(limit, MAX_LIMIT))
    wanted = market.strip().upper()

    scored = []
    for entry in get_index():
        if wanted and entry["market"] != wanted:
            continue
        rank = _score(entry, needle)
        if rank is None:
            continue
        # 정렬 우선순위 (앞쪽이 셀수록 강하다)
        #  ① 티커가 정확히 일치하면 무조건 맨 위
        #  ② ETF 는 뒤로 — `TES` 를 쳤을 때 테슬라보다 "2x Long TSLA ETF" 가
        #     먼저 나오면 안 된다. ETF 이름에는 유명 종목 티커가 자주 들어간다.
        #  ③ 일치 방식 점수 (티커 시작 → 이름 시작 → 포함 …)
        #  ④ 중요도 (국내=시총순위 · 미국=S&P500 편입 여부)
        #  ⑤ 티커가 짧은 것 → 사전순 (마지막 동점 처리)
        scored.append((
            0 if rank == 0 else 1,
            1 if entry["is_etf"] else 0,
            rank,
            entry["_p"],
            len(entry["ticker"]),
            entry["ticker"],
            entry,
        ))

    scored.sort(key=lambda row: row[:6])

    # 화면에 필요 없는 내부 필드(_t·_n)는 빼고 돌려준다
    return [
        {k: v for k, v in entry.items() if not k.startswith("_")}
        for *_ignored, entry in scored[:limit]
    ]


def stats() -> Dict[str, int]:
    """색인 현황 — 화면 배지와 상태 확인용."""
    index = get_index()
    return {
        "total": len(index),
        "kr": sum(1 for e in index if e["market"] == "KR"),
        "us": sum(1 for e in index if e["market"] == "US"),
    }
