"""P1 거래일 정렬 · P2 결측 처리 (전처리 계층) — 명세서 §3.1

시계열 분석은 **점과 점 사이의 간격이 일정하다**고 가정한다. 그런데 주가 데이터는
그렇지 않다. 주말·공휴일에 구멍이 있고, 거래정지가 걸리면 며칠씩 비어 있다.
이 모듈이 그 간격을 다듬어 뒤의 분석이 기대는 전제를 실제로 만들어 준다.

    P1 거래일 정렬   중복 제거 · 날짜 오름차순 · 거래일 축 확정
    P2 결측 처리     짧은 구멍은 직전 종가로 메우고(ffill), 긴 구멍은 잘라 낸다

왜 짧은 구멍은 메우고 긴 구멍은 자르는가
--------------------------------------
하루 이틀 빠진 것은 대개 **데이터 공급 쪽 사정**(수집 실패·공시 지연)이라,
직전 종가를 이어 붙이면 실제와 거의 같다. 그 며칠 사이에 시장이 열렸다면 가격이
크게 변했을 수도 있지만, 짧은 구간에서는 오차가 작다.

반대로 **일주일 넘게 비어 있으면** 그건 거래정지·상장폐지 절차·관리종목 지정처럼
사건이 있었다는 뜻이다. 그 구간을 직전 종가로 메우면 "아무 일도 없었던 평온한 며칠"이
만들어져 **변동성이 과소평가된다.** 그래서 메우지 않고 구간을 잘라 내면서
`G-DATA` 를 발행해 리포트에 남긴다 (품질을 속이지 않는다 — GIC 불변원칙).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

# 이보다 길게 비면 메우지 않고 잘라 낸다 (명세서 §3.1 P2).
# **실제 거래일 기준**이다 — 달력 평일이 아니다 (아래 `find_gaps` 설명 참고).
MAX_FILL_GAP = 3

# 거래일 캘린더가 없을 때, 이만큼 이하의 평일 공백은 **공휴일 연휴로 본다.**
#
# 왜 필요한가 — 실측에서 드러난 문제다. 삼성전자 250거래일을 넣었더니 2025-10-02 뒤에
# 평일 5일이 비어 있어 "긴 결측" 으로 판정하고 앞쪽 51행(20%)을 잘라 냈다.
# 그런데 그 구간은 결측이 아니라 **추석 연휴**였다. 장이 안 선 날을 두고
# "데이터가 빠졌다" 고 한 셈이다.
#
# 한국은 설·추석에 3~5거래일, 미국은 최장 2거래일을 쉰다. 그래서 캘린더 없이 셀 때는
# 5일까지를 연휴로 보고 넘어간다. 정확히 가르려면 `trading_days` 를 넘겨야 한다.
HOLIDAY_RUN_MAX = 5

# 잘라 냈을 때 어느 쪽을 남길지 — 긴 구멍 뒤쪽(최근 구간)을 남긴다.
# 분석 기준일에 가까운 자료가 판단에 더 쓸모 있기 때문이다.


def _to_date(text: str) -> Optional[date]:
    """`2026-07-30` → `date`. 형식이 다르면 `None`."""
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def _calendar_set(trading_days: Optional[Sequence[str]]) -> Optional[Set[str]]:
    """거래일 목록을 집합으로. 비었으면 `None` (근사 방식으로 넘어간다)."""
    if not trading_days:
        return None
    days = {(d or "").strip() for d in trading_days}
    return days or None


def sort_and_dedupe(rows: Sequence[Dict]) -> Tuple[List[Dict], int]:
    """P1 — 날짜 오름차순으로 정렬하고 같은 날짜가 겹치면 하나만 남긴다.

    → `(정리된 행, 제거한 중복 수)`

    같은 날짜가 두 번 오는 일은 생각보다 흔하다. 캐시와 실시간 조회를 섞어 쓰거나,
    수정주가 반영으로 같은 날이 다시 내려오는 경우가 그렇다.
    **나중에 온 것을 남긴다** — 수정 반영본이 뒤에 오기 때문이다.
    """
    by_date: Dict[str, Dict] = {}
    duplicates = 0

    for row in rows:
        key = (row.get("date") or "").strip()
        if not key or _to_date(key) is None:
            continue                        # 날짜가 없거나 형식이 틀린 줄은 버린다
        if key in by_date:
            duplicates += 1
        by_date[key] = row                  # 나중 것으로 덮어쓴다

    ordered = [by_date[k] for k in sorted(by_date)]
    return ordered, duplicates


def find_gaps(rows: Sequence[Dict],
              trading_days: Optional[Sequence[str]] = None) -> List[Dict]:
    """연속한 두 행 사이에 **거래일이 몇 개 비었는지** 센다.

    세는 방법이 둘이고, `trading_days` 를 주느냐에 따라 갈린다.

    **(1) 거래소 캘린더를 준 경우 — 정확하다.**
    그 목록에 있는 날만 거래일로 센다. 공휴일은 애초에 목록에 없으므로 결측이 아니다.
    국내는 `krx_store.available_dates()` 가 실제 개장일을 준다.

    **(2) 주지 않은 경우 — 근사다.**
    주말만 빼고 세되, `HOLIDAY_RUN_MAX`(5일) 이하의 공백은 **연휴로 보고 결측에서 뺀다.**
    공휴일과 진짜 결측을 구분할 방법이 없기 때문이다. 이 경우 짧은 결측을 놓칠 수 있으므로
    품질 리포트에 근사를 썼다는 사실을 밝힌다.
    """
    calendar_days = _calendar_set(trading_days)
    gaps: List[Dict] = []

    for previous, current in zip(rows, rows[1:], strict=False):
        start, end = _to_date(previous.get("date", "")), _to_date(current.get("date", ""))
        if not start or not end:
            continue

        missing: List[str] = []
        cursor = start + timedelta(days=1)
        while cursor < end:
            iso = cursor.isoformat()
            if calendar_days is not None:
                if iso in calendar_days:         # 캘린더에 있는데 자료가 없다 → 진짜 결측
                    missing.append(iso)
            elif cursor.weekday() < 5:           # 근사: 평일이면 일단 후보로
                missing.append(iso)
            cursor += timedelta(days=1)

        if not missing:
            continue

        # 근사 방식에서 짧은 공백은 연휴로 본다. 캘린더가 있으면 이 완화를 쓰지 않는다.
        if calendar_days is None and len(missing) <= HOLIDAY_RUN_MAX:
            continue

        gaps.append({
            "after": previous.get("date"),
            "before": current.get("date"),
            "days": len(missing),
            "dates": missing,
            "source": "calendar" if calendar_days is not None else "weekday-approx",
        })
    return gaps


def fill_and_trim(rows: Sequence[Dict], max_gap: int = MAX_FILL_GAP,
                  trading_days: Optional[Sequence[str]] = None) -> Tuple[List[Dict], Dict]:
    """P2 — 짧은 구멍은 직전 종가로 메우고, 긴 구멍이 있으면 그 뒤 구간만 남긴다.

    → `(정리된 행, 처리 보고)`

    메운 행은 `filled=True` 로 표시한다. 나중에 품질 리포트가 세고, 차트도
    "이 점은 실제 거래가 아니라 이어 붙인 값" 이라고 밝힐 수 있다.

    `trading_days` 를 주면 공휴일과 결측을 정확히 가른다 (`find_gaps` 설명 참고).
    """
    report = {
        "filled_days": 0,
        "filled_dates": [],
        "trimmed_rows": 0,
        "trim_reason": None,
        "long_gap": None,
        "missing_days": 0,
        "missing_dates": [],
        # 캘린더를 썼는지 근사를 썼는지 — 품질 리포트가 한계를 밝힐 때 쓴다
        "calendar_source": "calendar" if trading_days else "weekday-approx",
    }
    if not rows:
        return [], report

    gaps = find_gaps(rows, trading_days)
    report["missing_days"] = sum(g["days"] for g in gaps)
    # 목록이 길어지면 응답이 무거워진다. 진단에 필요한 만큼만 남긴다.
    report["missing_dates"] = [d for g in gaps for d in g["dates"]][:100]

    # 긴 구멍이 여러 개면 **가장 마지막 것** 뒤부터 남긴다.
    # 그래야 남는 구간에 긴 구멍이 하나도 없다.
    long_gaps = [g for g in gaps if g["days"] > max_gap]
    start_index = 0
    if long_gaps:
        last = long_gaps[-1]
        start_index = next(
            (i for i, r in enumerate(rows) if r.get("date") == last["before"]), 0)
        report["trimmed_rows"] = start_index
        report["long_gap"] = last
        report["trim_reason"] = (
            f"{last['after']} 다음에 거래일 {last['days']}일이 비어 있어 "
            f"{last['before']} 부터만 씁니다.")

    kept = list(rows[start_index:])

    # 메울 날짜를 미리 확정한다. **`find_gaps` 가 이미 연휴를 걸러 냈으므로**
    # 그 결과만 쓰면 공휴일을 실수로 메우지 않는다.
    # (여기서 날짜를 다시 훑으면 연휴 판정이 두 벌이 되어 어긋난다)
    fillable: Set[str] = set()
    for gap in gaps:
        if gap["days"] <= max_gap:
            fillable.update(gap["dates"])

    filled: List[Dict] = []
    for index, row in enumerate(kept):
        filled.append({**row, "filled": False})
        if index + 1 >= len(kept):
            break

        start, end = _to_date(row.get("date", "")), _to_date(kept[index + 1].get("date", ""))
        if not start or not end:
            continue

        cursor = start + timedelta(days=1)
        while cursor < end:
            iso = cursor.isoformat()
            if iso in fillable:
                # 직전 거래일의 종가를 그대로 이어 쓴다. 시가·고가·저가도 종가로 맞춰
                # 그 날 가격이 움직이지 않았음을 분명히 한다 (거래량은 0).
                close = row.get("close")
                filled.append({
                    **row,
                    "date": iso,
                    "open": close, "high": close, "low": close, "close": close,
                    "volume": 0, "value": 0,
                    "filled": True,
                })
                report["filled_days"] += 1
                if len(report["filled_dates"]) < 100:
                    report["filled_dates"].append(iso)
            cursor += timedelta(days=1)

    return filled, report


def expected_trading_days(first: str, last: str,
                          trading_days: Optional[Sequence[str]] = None) -> int:
    """두 날짜 사이의 **기대 거래일 수**. `coverage_ratio` 의 분모다.

    캘린더를 주면 그 구간의 실제 개장일을 센다 — 정확하다.
    주지 않으면 주말만 빼고 세는데, 공휴일이 분모에 남아 **실제보다 크게** 나온다.
    그만큼 충실도가 보수적으로(낮게) 잡히므로, 품질을 좋아 보이게 만드는 쪽으로는
    틀리지 않는다.
    """
    start, end = _to_date(first), _to_date(last)
    if not start or not end or end < start:
        return 0

    calendar_days = _calendar_set(trading_days)
    if calendar_days is not None:
        return sum(1 for d in calendar_days if first <= d <= last)

    days = (end - start).days + 1
    full_weeks, remainder = divmod(days, 7)
    count = full_weeks * 5
    cursor = start
    for _ in range(remainder):
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count
