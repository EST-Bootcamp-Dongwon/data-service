"""08강 재무제표 I·II 를 판정 함수로 옮긴 것 (명세 §5.6)

강의(`08-investment-analysis/lecture/docs/06.md`)의 기준선을 **그대로** 상수로 둔다.
숫자를 내 마음대로 정하면 "왜 이 기준인가" 를 리포트가 설명할 수 없다.

    부채비율     100% 이하 안정 · 200% 이상 주의        (06.md 안전성 지표 표)
    유동비율     150% 이상 양호
    이자보상배율  1배 이하 위험 · 3배 이상 안정
    ROE         10% 이상 좋은 수준
    ROA         5% 이상 좋은 수준
    ROE = ROA × (1 + 부채비율/100)                    (레버리지 효과 — 양날의 검)

모든 판정 함수는 **판정과 근거 문장을 같이** 돌려준다. 등급만 주면 리포트가
"🔴 둔화" 라고만 쓰게 되고, 읽는 사람은 왜인지 모른다.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# 강의에서 가져온 기준선 (06.md)
DEBT_RATIO_SAFE = 100.0
DEBT_RATIO_WATCH = 200.0
CURRENT_RATIO_GOOD = 150.0
INTEREST_COVER_DANGER = 1.0
INTEREST_COVER_SAFE = 3.0
ROE_GOOD = 10.0
ROA_GOOD = 5.0

# 상태 등급 — app.css 의 상태색과 짝이다 (색만으로 뜻을 전하지 않으므로 라벨을 함께 낸다)
GRADE_GOOD = "good"
GRADE_WARNING = "warning"
GRADE_SERIOUS = "serious"
GRADE_UNKNOWN = "unknown"


def _pct(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """백분율. 분모가 없거나 0이면 None (0으로 나누지 않는다)."""
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def verdict(grade: str, label: str, why: str, value: Optional[float] = None,
            unit: str = "", basis: str = "") -> Dict:
    """판정 한 덩어리 — 등급 · 한 줄 이름 · 이유 · 값 · 근거 기준."""
    return {"grade": grade, "label": label, "why": why,
            "value": round(value, 2) if isinstance(value, (int, float)) else None,
            "unit": unit, "basis": basis}


# ─────────────────────────────────────────────────────────────
# 수익성
# ─────────────────────────────────────────────────────────────
def operating_margin(operating_income: Optional[float], revenue: Optional[float]) -> Dict:
    """영업이익률 — 본업의 수익성 (06.md).

    절대 기준을 두지 않는다. 업종마다 정상 수준이 완전히 달라서
    (반도체 20%대 · 유통 2%대) 하나로 자르면 반드시 틀린다. 피어 비교에서 판정한다.
    """
    value = _pct(operating_income, revenue)
    if value is None:
        return verdict(GRADE_UNKNOWN, "영업이익률", "매출액 또는 영업이익이 없습니다", basis="06.md")
    return verdict(GRADE_GOOD if value > 0 else GRADE_SERIOUS, "영업이익률",
                   "본업에서 이익이 난다" if value > 0 else "본업에서 적자다",
                   value, "%", "08강 06.md 수익성 지표")


def roe(net_income: Optional[float], equity: Optional[float]) -> Dict:
    """ROE — 주주 돈으로 얼마나 벌었나. 10% 이상을 좋은 수준으로 본다 (06.md)."""
    value = _pct(net_income, equity)
    if value is None:
        return verdict(GRADE_UNKNOWN, "ROE", "순이익 또는 자본총계가 없습니다", basis="06.md")
    if value >= ROE_GOOD:
        grade, why = GRADE_GOOD, f"{ROE_GOOD:.0f}% 기준을 넘는다"
    elif value > 0:
        grade, why = GRADE_WARNING, f"양수이지만 {ROE_GOOD:.0f}% 기준에 못 미친다"
    else:
        grade, why = GRADE_SERIOUS, "적자다"
    return verdict(grade, "ROE", why, value, "%", "08강 06.md — ROE 10% 이상 양호")


def roa(net_income: Optional[float], assets: Optional[float]) -> Dict:
    """ROA — 자산 전체를 얼마나 효율적으로 쓰나. 5% 이상 (06.md)."""
    value = _pct(net_income, assets)
    if value is None:
        return verdict(GRADE_UNKNOWN, "ROA", "순이익 또는 자산총계가 없습니다", basis="06.md")
    grade = GRADE_GOOD if value >= ROA_GOOD else (GRADE_WARNING if value > 0 else GRADE_SERIOUS)
    return verdict(grade, "ROA", f"{ROA_GOOD:.0f}% 기준 대비 {'충족' if value >= ROA_GOOD else '미달'}",
                   value, "%", "08강 06.md — ROA 5% 이상 양호")


def dupont(net_income: Optional[float], revenue: Optional[float],
           assets: Optional[float], equity: Optional[float]) -> Dict:
    """ROE 듀폰 3분해 — 마진 × 회전율 × 레버리지.

    같은 ROE 라도 **어디서 나왔는지**가 다르다. 레버리지로 만든 ROE 는
    금리가 오르면 그대로 무너진다. 그래서 분해해서 보여 준다.
    """
    margin = _pct(net_income, revenue)
    turnover = _ratio(revenue, assets)
    leverage = _ratio(assets, equity)
    if None in (margin, turnover, leverage):
        return {"available": False, "reason": "순이익·매출·자산·자본 중 빠진 값이 있습니다"}

    combined = margin / 100 * turnover * leverage * 100

    # 어느 요인이 ROE 를 끌고 있나 — **보통 수준 대비 몇 배인지**로 고른다.
    # 세 값은 단위가 달라서(%, 회, 배) 날것으로 비교하면 늘 레버리지가 이긴다.
    # 기준선은 국내 제조업 평균 언저리다 (순이익률 5% · 자산회전율 0.7회 · 레버리지 2.0배).
    baseline = {"순이익률": (margin, 5.0), "자산회전율": (turnover, 0.7), "재무레버리지": (leverage, 2.0)}
    driver = max(baseline.items(), key=lambda kv: kv[1][0] / kv[1][1])[0]
    return {
        "available": True,
        "net_margin": round(margin, 2),
        "asset_turnover": round(turnover, 3),
        "leverage": round(leverage, 3),
        "roe_reconstructed": round(combined, 2),
        "main_driver": driver,
        "formula": "ROE = 순이익률 × 자산회전율 × 재무레버리지",
        "note": ("레버리지가 주된 동력이면 금리 상승기에 ROE 가 빠르게 나빠질 수 있다 (06.md 양날의 검)"
                 if driver == "재무레버리지" else ""),
    }


# ─────────────────────────────────────────────────────────────
# 안정성
# ─────────────────────────────────────────────────────────────
def debt_ratio(liabilities: Optional[float], equity: Optional[float]) -> Dict:
    """부채비율 — 100% 이하 안정 · 200% 이상 주의 (06.md).

    ⚠️ 금융업에는 이 기준을 쓰지 않는다. 은행은 예금이 부채라 부채비율이 1,000% 를 넘는 것이
    정상이다 (06.md 421행 "금융업은 일반 제조업 비율로 보지 않는다").
    """
    value = _pct(liabilities, equity)
    if value is None:
        return verdict(GRADE_UNKNOWN, "부채비율", "부채총계 또는 자본총계가 없습니다", basis="06.md")
    if value <= DEBT_RATIO_SAFE:
        grade, why = GRADE_GOOD, "100% 이하 — 안정 구간"
    elif value < DEBT_RATIO_WATCH:
        grade, why = GRADE_WARNING, "100~200% — 지켜볼 구간"
    else:
        grade, why = GRADE_SERIOUS, "200% 이상 — 주의 구간"
    return verdict(grade, "부채비율", why, value, "%", "08강 06.md 안전성 지표")


def current_ratio(current_assets: Optional[float], current_liabilities: Optional[float]) -> Dict:
    """유동비율 — 150% 이상 양호 (06.md)."""
    value = _pct(current_assets, current_liabilities)
    if value is None:
        return verdict(GRADE_UNKNOWN, "유동비율", "유동자산 또는 유동부채가 없습니다", basis="06.md")
    grade = GRADE_GOOD if value >= CURRENT_RATIO_GOOD else (
        GRADE_WARNING if value >= 100 else GRADE_SERIOUS)
    why = ("150% 이상 — 1년 내 빚을 1년 내 자산으로 감당한다" if value >= CURRENT_RATIO_GOOD
           else ("100~150% — 여유가 크지 않다" if value >= 100 else "100% 미만 — 단기 지급능력이 부족하다"))
    return verdict(grade, "유동비율", why, value, "%", "08강 06.md — 150% 이상 양호")


def interest_coverage(operating_income: Optional[float], interest_expense: Optional[float]) -> Dict:
    """이자보상배율 — 1배 이하 위험 · 3배 이상 안정 (06.md)."""
    if operating_income is None or not interest_expense:
        return verdict(GRADE_UNKNOWN, "이자보상배율", "영업이익 또는 이자비용이 없습니다", basis="06.md")
    value = operating_income / abs(interest_expense)
    if value <= INTEREST_COVER_DANGER:
        grade, why = GRADE_SERIOUS, "영업이익으로 이자도 못 낸다 — 재무 위기 신호"
    elif value < INTEREST_COVER_SAFE:
        grade, why = GRADE_WARNING, "이자는 내지만 여유가 크지 않다"
    else:
        grade, why = GRADE_GOOD, "3배 이상 — 이자 부담을 감당한다"
    return verdict(grade, "이자보상배율", why, value, "배", "08강 06.md — 1배 이하 위험")


# ─────────────────────────────────────────────────────────────
# 이익의 질 (현금전환)
# ─────────────────────────────────────────────────────────────
def earnings_quality(operating_cash_flow: Optional[float],
                     operating_income: Optional[float],
                     capex: Optional[float] = None) -> Dict:
    """이익의 질 — 장부 이익이 실제 현금으로 들어왔는가.

    06.md 743행: "유동비율이 높더라도 영업현금흐름이 마이너스라면 이익의 질을 의심하라."
    영업현금흐름 ÷ 영업이익 이 1 근처면 건강하고, 뚜렷하게 낮으면 매출채권·재고에
    이익이 묶여 있다는 뜻이다.
    """
    if operating_cash_flow is None or not operating_income:
        return {"available": False, "reason": "영업현금흐름 또는 영업이익이 없습니다"}

    conversion = operating_cash_flow / abs(operating_income)
    fcf = None if capex is None else operating_cash_flow - abs(capex)
    if operating_cash_flow < 0:
        grade, why = GRADE_SERIOUS, "영업활동에서 현금이 빠져나갔다 — 이익의 질을 의심해야 한다"
    elif conversion >= 0.8:
        grade, why = GRADE_GOOD, "영업이익의 대부분이 현금으로 들어왔다"
    elif conversion >= 0.5:
        grade, why = GRADE_WARNING, "이익의 절반 남짓만 현금이 됐다 — 운전자본을 봐야 한다"
    else:
        grade, why = GRADE_SERIOUS, "장부 이익에 비해 현금 유입이 크게 적다"
    return {
        "available": True,
        "grade": grade,
        "why": why,
        "cash_conversion": round(conversion, 3),
        "operating_cash_flow": operating_cash_flow,
        "free_cash_flow": fcf,
        "basis": "08강 06.md — 영업현금흐름과 이익의 질",
    }


# ─────────────────────────────────────────────────────────────
# 성장률
# ─────────────────────────────────────────────────────────────
def cagr(first: Optional[float], last: Optional[float], years: int) -> Optional[float]:
    """연평균 성장률(%).

    시작값이 0 이하면 **계산하지 않는다.** 적자→흑자 전환에 CAGR 을 붙이면
    수천 % 같은 무의미한 숫자가 나온다 (공통계약 §9 `E-CALC`).
    """
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def growth_series(values: Sequence[Optional[float]], labels: Sequence[str]) -> Dict:
    """연도별 성장률과 추세 판정.

    values 는 **오래된 것부터** 넣는다 (2021 → 2025).
    """
    clean = [(label, value) for label, value in zip(labels, values, strict=False) if value is not None]
    if len(clean) < 2:
        return {"available": False, "reason": "연도가 둘 미만이라 성장률을 못 낸다"}

    growths = []
    for (prev_label, prev), (label, value) in zip(clean, clean[1:], strict=False):
        rate = None if prev <= 0 else (value / prev - 1) * 100
        growths.append({"period": f"{prev_label}→{label}", "growth": None if rate is None else round(rate, 2)})

    rates = [g["growth"] for g in growths if g["growth"] is not None]
    total = cagr(clean[0][1], clean[-1][1], len(clean) - 1)
    if not rates:
        trend = "판정불가"
    elif all(r > 0 for r in rates):
        trend = "지속 성장"
    elif all(r < 0 for r in rates):
        trend = "지속 감소"
    elif len(rates) >= 2 and rates[-1] < rates[-2]:
        trend = "성장 둔화"
    else:
        trend = "혼조"
    return {
        "available": True,
        "growths": growths,
        "cagr": None if total is None else round(total, 2),
        "years": len(clean) - 1,
        "trend": trend,
        "basis": "08강 06.md 성장성 지표",
    }


def summarize(metrics: Dict) -> List[Dict]:
    """판정들을 모아 심각한 것부터 정렬한다 (리포트 상단 요약용)."""
    order = {GRADE_SERIOUS: 0, GRADE_WARNING: 1, GRADE_GOOD: 2, GRADE_UNKNOWN: 3}
    rows = [v for v in metrics.values() if isinstance(v, dict) and "grade" in v]
    return sorted(rows, key=lambda r: order.get(r.get("grade"), 9))
