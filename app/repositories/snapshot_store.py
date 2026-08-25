"""사전계산 시장 스냅샷 읽기 (저장 계층)

스크리닝은 "전 종목을 훑어 조건에 맞는 것을 고르는" 일이다. 그런데 국내 2,764종목의
수익률을 요청할 때마다 계산하려면 캐시 DB(96MB)가 필요한데, 그 DB 는 배포 번들에 못 올린다.
그래서 **미리 계산해 압축해 둔다** (명세서 §2.3).

    scripts/build_market_snapshot.py  →  data/market_snapshot.json.gz  (읽기 전용)
                                              ↑ 이 모듈이 읽는다

이 모듈은 **읽기만 한다.** 조건에 맞는 종목을 고르는 판단(스크리닝 규칙)은
서비스 계층(`app/services/`)의 일이다. 여기서는 목록과 기준일을 꺼내 주기까지만 한다.

기준일이 중요한 이유
------------------
스냅샷은 수동 갱신이라(재빌드 후 커밋) **분석 기준일보다 뒤처질 수 있다.**
그 간격을 모른 채 "시총 상위" 를 말하면 옛날 이야기를 하는 셈이다.
그래서 `as_of` 를 항상 함께 내보내고, 5거래일 이상 벌어지면 `G-DATE` 를 발행한다
(명세서 §2.3). 그 판정도 여기서 해 준다 — 화면·리포트가 같은 기준을 쓰게 하기 위함이다.
"""

from __future__ import annotations

import gzip
import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# 이 파일은 app/repositories/ 안에 있으므로 parents[2] 가 프로젝트 루트다.
BASE_DIR = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = BASE_DIR / "data" / "market_snapshot.json.gz"

KST = timezone(timedelta(hours=9))

# 스냅샷 기준일이 분석 기준일보다 이만큼 넘게 뒤처지면 `G-DATE` 를 발행한다 (명세서 §2.3)
STALE_TRADING_DAYS = 5

# 한 번 읽으면 메모리에 둔다. 배포 번들의 읽기 전용 파일이라 도중에 바뀌지 않는다.
_snapshot: Optional[Dict] = None
_lock = threading.Lock()


def load() -> Dict:
    """스냅샷을 메모리에 한 번만 올린다. 파일이 없으면 **빈 스냅샷**을 돌려준다.

    파일이 없어도 예외를 던지지 않는다. 스냅샷은 스크리닝에만 필요하고,
    없으면 그 기능만 못 쓰는 것이지 앱 전체가 죽을 일은 아니다.
    (`stats()` 가 없다는 사실을 대시보드에 알린다)
    """
    global _snapshot
    if _snapshot is not None:
        return _snapshot

    with _lock:
        if _snapshot is not None:            # 기다리는 동안 다른 스레드가 채웠을 수 있다
            return _snapshot
        try:
            with gzip.open(SNAPSHOT_PATH, "rt", encoding="utf-8") as archive:
                _snapshot = json.load(archive)
        except Exception:
            _snapshot = {"as_of": "", "rows": [], "meta": {}, "available": False}
        else:
            _snapshot.setdefault("available", True)
    return _snapshot


def reload() -> Dict:
    """메모리에 올려 둔 스냅샷을 버리고 다시 읽는다. (재빌드 직후 로컬에서 쓴다)

    ⚠️ **종목코드 색인(`_code_index`)까지 함께 버려야 한다.** 스냅샷만 비우면
    `get(code)` 가 계속 옛 행을 돌려준다 — 파일은 새것인데 한 종목만 옛날 값이라
    화면에서 보고 알아채기가 매우 어렵다. (ADR-DS-0017 에서 화면 갱신 버튼이
    이 함수를 부르기 시작하면서 드러난 구멍이다.)
    """
    global _snapshot, _code_index
    with _lock:
        _snapshot = None
        _code_index = None
    return load()


def available() -> bool:
    """스냅샷을 쓸 수 있는지."""
    return bool(load().get("rows"))


def as_of() -> str:
    """스냅샷 기준일 (`YYYY-MM-DD`). 없으면 빈 문자열."""
    return load().get("as_of", "")


def rows(market: str = "") -> List[Dict]:
    """전 종목 목록. `market` 을 주면 그 시장만 (`KOSPI`·`KOSDAQ`·`US`).

    돌려주는 목록은 **내부 목록 그대로**다. 호출하는 쪽에서 정렬·필터를 하더라도
    원본을 바꾸지 않도록 주의한다 (읽기 전용으로 쓴다).
    """
    items = load().get("rows") or []
    if not market:
        return items
    wanted = market.strip().upper()
    return [r for r in items if (r.get("market") or "").upper() == wanted]


def get(code: str) -> Optional[Dict]:
    """종목코드(또는 미국 티커)로 한 종목을 찾는다. 없으면 `None`."""
    key = (code or "").strip().upper()
    if not key:
        return None
    return _index().get(key)


_code_index: Optional[Dict[str, Dict]] = None


def _index() -> Dict[str, Dict]:
    """종목코드 → 행 색인. 스크리닝 결과에서 개별 종목을 꺼낼 때 훑지 않게 한다."""
    global _code_index
    if _code_index is None:
        _code_index = {(r.get("code") or "").upper(): r for r in rows()}
    return _code_index


def markets() -> List[Dict]:
    """시장별 종목 수. (화면 필터 상자용)"""
    counts: Dict[str, int] = {}
    for row in rows():
        key = row.get("market") or "기타"
        counts[key] = counts.get(key, 0) + 1
    return [{"market": k, "count": v} for k, v in sorted(counts.items())]


def sectors(market: str = "") -> List[Dict]:
    """업종별 종목 수 — 많은 순. (산업 리서치 IND-R 의 후보 목록에 쓴다)"""
    counts: Dict[str, int] = {}
    for row in rows(market):
        key = row.get("sector") or "미분류"
        counts[key] = counts.get(key, 0) + 1
    return [{"sector": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


def staleness(analysis_date: str = "") -> Dict:
    """스냅샷이 얼마나 뒤처졌는지 판정한다 → `G-DATE` 발행 여부.

    `analysis_date` 를 비우면 오늘(KST)을 쓴다.
    거래일 수는 정확히 세지 않고 **주말만 뺀 근사치**를 쓴다. 공휴일까지 반영하려면
    거래소 캘린더가 필요한데, "5일 이상 뒤처졌나" 를 가리는 데는 이 정도로 충분하다.
    (근사임을 `note` 에 밝힌다 — 품질을 속이지 않는다)
    """
    snapshot_date = as_of()
    result = {
        "as_of": snapshot_date,
        "analysis_date": analysis_date or datetime.now(KST).strftime("%Y-%m-%d"),
        "trading_days_behind": None,
        "stale": False,
        "gap": None,
        "note": "거래일 수는 주말만 제외한 근사치입니다 (공휴일 미반영).",
    }
    if not snapshot_date:
        result["gap"] = {
            "code": "G-DATE",
            "message": "시장 스냅샷이 없습니다.",
            "detail": "`python3 scripts/build_market_snapshot.py` 로 만들 수 있습니다. "
                      "스크리닝 결과 대신 개별 조회로 대체해야 합니다.",
        }
        result["stale"] = True
        return result

    try:
        start = date.fromisoformat(snapshot_date)
        end = date.fromisoformat(result["analysis_date"])
    except ValueError:
        return result

    behind = _weekdays_between(start, end)
    result["trading_days_behind"] = behind
    if behind > STALE_TRADING_DAYS:
        result["stale"] = True
        result["gap"] = {
            "code": "G-DATE",
            "message": f"시장 스냅샷이 분석 기준일보다 약 {behind}거래일 뒤처졌습니다.",
            "detail": f"스냅샷 기준일 {snapshot_date} · 분석 기준일 {result['analysis_date']}. "
                      "시총·수익률 순위가 현재와 다를 수 있습니다. "
                      "`python3 scripts/build_market_snapshot.py` 로 다시 만들면 해소됩니다.",
        }
    return result


def _weekdays_between(start: date, end: date) -> int:
    """두 날짜 사이의 평일 수 (주말 제외). 뒤가 앞보다 이르면 0."""
    if end <= start:
        return 0
    days = (end - start).days
    # 완전한 주 단위로 5일씩 세고, 남은 날은 하루씩 확인한다
    full_weeks, remainder = divmod(days, 7)
    count = full_weeks * 5
    cursor = start
    for _ in range(remainder):
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:            # 0=월 … 4=금
            count += 1
    return count


def stats() -> Dict:
    """스냅샷 현황 — 대시보드 '데이터 상태' 카드가 쓴다."""
    snapshot = load()
    items = snapshot.get("rows") or []
    meta = snapshot.get("meta") or {}
    size_kb = 0.0
    try:
        size_kb = SNAPSHOT_PATH.stat().st_size / 1024
    except Exception:
        pass

    return {
        "available": bool(items),
        "path": str(SNAPSHOT_PATH.relative_to(BASE_DIR)),
        "as_of": snapshot.get("as_of", ""),
        "count": len(items),
        "markets": markets(),
        "size_kb": round(size_kb, 1),
        "generated_at": meta.get("generated_at", ""),
        "sources": meta.get("sources", []),
        **staleness(),
    }
