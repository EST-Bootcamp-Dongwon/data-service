"""전처리 파이프라인 (서비스 계층) — 명세서 §3

기획서 §1의 "데이터 전처리가 확실해야 시계열 분석이 정확하다" 를 코드로 옮긴 곳이다.
원본 OHLCV 를 받아 **분석에 바로 쓸 수 있는 시계열 + 품질 리포트**를 돌려준다.

    from app.services import preprocess

    result = preprocess.run(rows, ticker="005930")
    result["rows"]     # 정리된 시계열
    result["quality"]  # 품질 리포트 (명세서 §3.2)
    result["quality"]["verdict"]     # usable | usable-with-caveat | insufficient

파이프라인 (명세서 §3.1)
------------------------
    P1 거래일 정렬     중복 제거 · 날짜 오름차순            calendar.sort_and_dedupe
    P2 결측 처리       3일 이하 ffill / 초과 절단 + G-DATA   calendar.fill_and_trim
    P3 이상치 검사     5σ 또는 ±30% → 플래그 (제거 안 함)    quality.find_outliers
    P4 액면·배당 조정  수정주가 사용 여부 기록                normalize.mark_adjusted
    P5 통화·단위       KRW/USD 명시                         normalize.normalize_series
    P6 품질 리포트     §3.2 표 생성                          quality.build_report

**전처리 결과는 그 자체로 `stage_result` 에 실린다** (명세서 §3 머리말).
무엇을 어떻게 손봤는지가 리포트에 남아야 독자가 결과를 믿을지 판단할 수 있다.
그래서 이 모듈은 "조용히 고쳐 주는" 일을 하지 않는다 — 고친 것을 전부 세어서 함께 돌려준다.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence

from app.services.preprocess import calendar, normalize, quality

# 하위 모듈을 밖에서도 바로 쓸 수 있게 열어 둔다
__all__ = ["run", "calendar", "normalize", "quality", "summarize"]


def run(rows: Sequence[Dict], ticker: str = "", currency: str = "",
        auto_adjust: bool = True, source: str = "yfinance",
        max_gap: int = calendar.MAX_FILL_GAP,
        trading_days: Optional[Sequence[str]] = None) -> Dict:
    """P1~P6 을 순서대로 돌린다.

    `rows` 는 `{"date": "2026-07-30", "open":…, "high":…, "low":…, "close":…,
    "volume":…}` 모양의 목록이다 (`yf_data.fetch_history` 의 `rows` 가 그대로 맞는다).

    `trading_days` 는 그 시장의 **실제 개장일 목록**이다. 주면 공휴일과 결측을
    정확히 가르고, 주지 않으면 주말만 빼는 근사로 넘어간다.
    국내는 `krx_store.available_dates()` 를 그대로 넘기면 된다.

        실측 — 캘린더 없이 삼성전자 250거래일을 넣었더니 추석 연휴(5거래일)를
        결측으로 오판해 앞쪽 51행(20%)을 잘라 냈다. 캘린더를 주면 사라지는 문제다.

    돌려주는 것
      `rows`      정리된 시계열 (`filled=True` 로 이어 붙인 행을 표시)
      `quality`   품질 리포트 — 명세서 §3.2 필드 그대로
      `units`     통화·단위 (P5)
      `adjustment` 수정주가 여부 (P4)
      `gaps`      발행한 Gap 목록 (품질 리포트 안의 것과 같은 객체)

    **입력이 비어 있어도 예외를 던지지 않는다.** 빈 결과 + `insufficient` 판정으로
    돌려주고, 호출한 쪽이 `partial-continue` 로 넘어가게 한다 (GIC 불변원칙 §2-2).
    """
    raw = list(rows or [])

    # P1 — 정렬 · 중복 제거
    ordered, duplicates = calendar.sort_and_dedupe(raw)

    # P2 — 결측 처리 (짧은 구멍 ffill · 긴 구멍 절단)
    clean, calendar_report = calendar.fill_and_trim(
        ordered, max_gap=max_gap, trading_days=trading_days)

    # P3·P6 — 이상치 검사와 품질 리포트 (이상치는 표시만 하고 제거하지 않는다)
    report = quality.build_report(raw, clean, calendar_report, duplicates, trading_days)

    # P4 — 수정주가 여부
    adjustment = normalize.mark_adjusted(clean, auto_adjust, source)

    # P5 — 통화·단위
    units = normalize.normalize_series(clean, ticker, currency)

    return {
        "ticker": ticker,
        "rows": clean,
        "quality": report,
        "units": units,
        "adjustment": adjustment,
        "gaps": report["gaps_emitted"],
        "verdict": report["verdict"],
        "modelable": report["modelable"],
    }


def summarize(result: Dict) -> str:
    """품질 리포트를 한 줄로 줄인다. (진행률 모달·로그용)

        "usable-with-caveat · 248행 · 충실도 96% · 이어붙임 2일 · 이상치 3건"
    """
    report = result.get("quality") or {}
    parts = [
        report.get("verdict", "?"),
        f"{report.get('rows_clean', 0)}행",
        f"충실도 {report.get('coverage_ratio', 0):.0%}",
    ]
    if report.get("filled_days"):
        parts.append(f"이어붙임 {report['filled_days']}일")
    if report.get("trimmed_rows"):
        parts.append(f"절단 {report['trimmed_rows']}행")
    outliers = (report.get("outliers") or {}).get("count") or 0
    if outliers:
        parts.append(f"이상치 {outliers}건")
    return " · ".join(parts)
