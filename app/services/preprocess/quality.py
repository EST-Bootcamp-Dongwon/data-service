"""P3 이상치 검사 · P6 품질 리포트 (전처리 계층) — 명세서 §3.1·§3.2

**이상치를 제거하지 않는다** (명세서 §3.3). 주가의 극단 수익률은 대부분
진짜 사건이다 — 실적 발표, 유상증자, 급등락. 제거하면 리스크가 실제보다 작아 보인다.
그래서 여기서는 **표시만 하고**, 모델에는 그대로 넣는다. 대신 해석 카드의
`limitation` 에 "표본에 ±N% 이상 변동일 M건 포함" 을 적게 한다.

품질 리포트는 그 자체로 `stage_result` 에 실린다. 무엇을 어떻게 손봤는지가
리포트에 남아야 독자가 결과를 믿을지 말지 판단할 수 있다.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.services.preprocess import calendar as cal

# 이상치 판정 기준 (명세서 §3.1 P3) — 둘 중 하나만 걸려도 표시한다
OUTLIER_SIGMA = 5.0          # 로그수익률이 표준편차의 5배를 넘으면
OUTLIER_ABS = 30.0           # 또는 하루 ±30% 를 넘으면

# 판정 경계 (명세서 §3.2)
COVERAGE_CAVEAT = 0.9        # 이 아래면 `usable-with-caveat`
COVERAGE_INSUFFICIENT = 0.6  # 이 아래면 `insufficient` — 시계열 모델링을 건너뛴다

# 시계열 모델을 적합하려면 최소한 이만큼은 있어야 한다.
# ARIMA 차수 탐색과 walk-forward 백테스트가 둘 다 가능한 하한이다.
MIN_ROWS = 60


def log_returns(closes: Sequence[float]) -> np.ndarray:
    """종가 → 로그수익률. 0 이하 값은 계산할 수 없어 `nan` 으로 둔다.

    로그를 쓰는 이유 — 더하기가 되기 때문이다. 일간 로그수익률을 그냥 더하면
    구간 수익률이 되고, 정규분포 가정을 다루기도 편하다. (05강 6절)
    """
    prices = np.asarray(closes, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = prices[1:] / prices[:-1]
        result = np.log(np.where(ratio > 0, ratio, np.nan))
    return result


def find_outliers(rows: Sequence[Dict]) -> Dict:
    """P3 — 이상치를 **표시만** 한다. 제거하지 않는다 (명세서 §3.3).

    → `{sigma, threshold_pct, rows[], count, max_abs_pct}`

    표준편차는 **이상치를 포함한 채로** 계산한다. 빼고 계산하면 기준이 좁아져
    평범한 변동까지 이상치로 잡히는 되먹임이 생긴다.
    """
    result: Dict = {
        "sigma": None, "threshold_pct": None, "rows": [],
        "count": 0, "max_abs_pct": None,
    }
    closes = [r.get("close") for r in rows]
    if len(closes) < 3 or any(c is None for c in closes):
        return result

    returns = log_returns(closes)
    finite = returns[np.isfinite(returns)]
    if finite.size < 2:
        return result

    sigma = float(np.std(finite, ddof=1))
    result["sigma"] = round(sigma * 100, 4)                  # %로 표기
    result["threshold_pct"] = round(sigma * OUTLIER_SIGMA * 100, 2)

    flagged: List[Dict] = []
    for index, value in enumerate(returns):
        if not math.isfinite(value):
            continue
        percent = (math.exp(value) - 1) * 100                # 로그수익률 → 실제 변동률
        by_sigma = sigma > 0 and abs(value) > OUTLIER_SIGMA * sigma
        by_absolute = abs(percent) >= OUTLIER_ABS
        if by_sigma or by_absolute:
            row = rows[index + 1]                            # 수익률은 두 번째 행부터 생긴다
            flagged.append({
                "date": row.get("date"),
                "change_pct": round(percent, 2),
                "close": row.get("close"),
                "prev_close": rows[index].get("close"),
                # 왜 걸렸는지 밝힌다 — 리포트가 이유를 그대로 인용할 수 있게
                "reason": "5σ 초과" if by_sigma and not by_absolute
                          else "±30% 초과" if by_absolute and not by_sigma
                          else "5σ · ±30% 동시 초과",
            })

    percents = [abs((math.exp(v) - 1) * 100) for v in returns if math.isfinite(v)]
    result["rows"] = flagged
    result["count"] = len(flagged)
    result["max_abs_pct"] = round(max(percents), 2) if percents else None
    return result


def build_report(raw_rows: Sequence[Dict], clean_rows: Sequence[Dict],
                 calendar_report: Dict, duplicates: int = 0,
                 trading_days: Optional[Sequence[str]] = None) -> Dict:
    """P6 — 품질 리포트를 만든다 (명세서 §3.2의 필드를 그대로 채운다).

    `verdict` 세 가지
      - `usable`              그대로 쓴다
      - `usable-with-caveat`  쓰되 리포트에 한계를 밝힌다
      - `insufficient`        시계열 모델링을 건너뛰고 `partial-continue` 로 넘긴다
                              (GIC 불변원칙 §2-2 — 부족하면 부족하다고 말한다)
    """
    gaps: List[Dict] = []
    outliers = find_outliers(clean_rows)

    first = clean_rows[0].get("date") if clean_rows else ""
    last = clean_rows[-1].get("date") if clean_rows else ""
    expected = cal.expected_trading_days(first, last, trading_days) if clean_rows else 0

    # 메워 넣은 날은 실제 거래일이 아니므로 분자에서 뺀다.
    # 이걸 빼지 않으면 구멍을 메울수록 품질이 좋아지는 이상한 셈이 된다.
    actual = len(clean_rows) - (calendar_report.get("filled_days") or 0)
    coverage = round(actual / expected, 4) if expected else 0.0

    # ── Gap 발행 ─────────────────────────────────
    if calendar_report.get("long_gap"):
        gap = calendar_report["long_gap"]
        gaps.append({
            "code": "G-DATA",
            "message": f"거래일 {gap['days']}일이 연속으로 비어 있어 구간을 잘라 냈습니다.",
            "detail": f"{gap['after']} ~ {gap['before']} 사이. "
                      "거래정지·관리종목 지정 등이 있었을 수 있습니다. "
                      f"앞쪽 {calendar_report.get('trimmed_rows', 0)}행을 분석에서 제외했습니다.",
        })

    if coverage and coverage < COVERAGE_INSUFFICIENT:
        gaps.append({
            "code": "G-DATA",
            "message": f"자료 충실도가 {coverage:.0%} 로 낮아 시계열 모델링을 건너뜁니다.",
            "detail": f"기대 거래일 {expected}일 중 실제 {actual}일. "
                      "추세·예측 대신 확인된 사실만 싣습니다.",
        })
    elif coverage and coverage < COVERAGE_CAVEAT:
        gaps.append({
            "code": "G-DATA",
            "message": f"자료 충실도가 {coverage:.0%} 입니다. 결과에 한계를 함께 적습니다.",
            "detail": f"기대 거래일 {expected}일 중 실제 {actual}일.",
        })

    if len(clean_rows) < MIN_ROWS:
        gaps.append({
            "code": "G-DATA",
            "message": f"관측치가 {len(clean_rows)}개뿐이라 시계열 모델을 적합할 수 없습니다.",
            "detail": f"차수 탐색과 walk-forward 검증에 최소 {MIN_ROWS}개가 필요합니다.",
        })

    if outliers["count"]:
        gaps.append({
            "code": "G-DATA",
            "message": f"극단 변동일 {outliers['count']}건을 표본에 그대로 두었습니다.",
            "detail": f"최대 {outliers['max_abs_pct']}%. 제거하면 위험을 실제보다 작게 보게 되므로 "
                      "표시만 하고 모델에는 넣습니다 (명세서 §3.3). "
                      "해석 카드의 limitation 에 그대로 옮겨 적어야 합니다.",
        })

    # ── 판정 ─────────────────────────────────────
    if not clean_rows or coverage < COVERAGE_INSUFFICIENT or len(clean_rows) < MIN_ROWS:
        verdict = "insufficient"
    elif coverage < COVERAGE_CAVEAT or calendar_report.get("long_gap") or outliers["count"]:
        verdict = "usable-with-caveat"
    else:
        verdict = "usable"

    return {
        "rows_raw": len(raw_rows),
        "rows_clean": len(clean_rows),
        "duplicates_removed": duplicates,
        "date_first": first,
        "date_last": last,
        "expected_trading_days": expected,
        "missing_days": calendar_report.get("missing_days", 0),
        "missing_dates": calendar_report.get("missing_dates", []),
        "filled_days": calendar_report.get("filled_days", 0),
        "filled_dates": calendar_report.get("filled_dates", []),
        "trimmed_rows": calendar_report.get("trimmed_rows", 0),
        "trim_reason": calendar_report.get("trim_reason"),
        "outliers": outliers,
        "coverage_ratio": coverage,
        # 거래일을 캘린더로 셌는지 근사로 셌는지. 근사면 짧은 결측을 놓칠 수 있다.
        "calendar_source": calendar_report.get("calendar_source", "weekday-approx"),
        "gaps_emitted": gaps,
        "verdict": verdict,
        # 모델링을 진행해도 되는지 — 라우터·서비스가 이 한 줄만 보고 갈라도 되게 한다
        "modelable": verdict != "insufficient",
        "limitations": _limitations(verdict, coverage, outliers, calendar_report),
    }


def _limitations(verdict: str, coverage: float, outliers: Dict,
                 calendar_report: Dict) -> List[str]:
    """해석 카드의 `limitation` 에 그대로 넣을 문장들.

    사람이 읽을 문장으로 만들어 두는 이유 — 리포트를 조립하는 쪽이 숫자를 다시
    해석해 문장을 지어내면, 같은 데이터에서 매번 다른 표현이 나온다.
    """
    notes: List[str] = []
    if coverage and coverage < COVERAGE_CAVEAT:
        notes.append(f"표본 충실도가 {coverage:.0%} 로 낮습니다. 추세 판단의 신뢰도를 한 단계 낮춰 읽으세요.")
    if outliers["count"]:
        notes.append(
            f"표본에 ±{outliers['threshold_pct']}% 이상 변동일 {outliers['count']}건이 "
            f"포함돼 있습니다 (최대 {outliers['max_abs_pct']}%). 제거하지 않았습니다.")
    if calendar_report.get("filled_days"):
        notes.append(
            f"거래일 {calendar_report['filled_days']}일은 직전 종가로 이어 붙인 값입니다. "
            "그 구간의 변동성은 실제보다 작게 잡힙니다.")
    if calendar_report.get("trim_reason"):
        notes.append(calendar_report["trim_reason"])
    if calendar_report.get("calendar_source") == "weekday-approx":
        notes.append(
            f"거래소 캘린더 없이 주말만 제외해 셌습니다. 연휴({cal.HOLIDAY_RUN_MAX}거래일 이하)와 "
            "구분되지 않는 짧은 결측은 잡히지 않았을 수 있습니다.")
    if verdict == "insufficient":
        notes.append("자료가 모자라 시계열 모델링을 하지 않았습니다. 확인된 사실만 실었습니다.")
    return notes
