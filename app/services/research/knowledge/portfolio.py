"""08강 14·15 포트폴리오·자산배분을 판정 함수로 옮긴 것 (명세 §5.6)

강의의 식을 그대로 쓴다 (14.md 89행·124행).

    CAGR    = 누적배수^(1/연수) - 1        연수 = 관측일수 / 252
    변동성   = 일간수익률 표준편차 × √252
    MDD     = (누적 / 누적최고 - 1) 의 최솟값
    샤프     = (평균초과수익 / 전체 변동성) × √252
    소르티노  = (평균초과수익 / 하방 변동성) × √252
    칼마     = CAGR / |MDD|

`market_data.py` 가 이미 같은 계산을 하고 있지만 그쪽은 **화면용 숫자**를 만든다.
여기는 리서치가 쓰는 **판정**이다 — 값에 등급과 이유를 붙이고, 관측이 모자라면
그럴듯한 숫자를 만들지 않고 못 낸다고 답한다. 그래서 두 곳이 겹치는 것이 아니라 층이 다르다.

무위험이자율은 **하드코딩하지 않는다** (U8 결정). `macro.risk_free_ratio()` 가 ECOS 국고채
3년을 실측해 준다. 인자로 직접 넣을 수도 있게 열어 둔다 (검사·재현성).
"""
from __future__ import annotations

import math
from statistics import fmean, stdev
from typing import Dict, List, Optional, Sequence

from .financials import GRADE_GOOD, GRADE_SERIOUS, GRADE_UNKNOWN, GRADE_WARNING, verdict

TRADING_DAYS_PER_YEAR = 252            # 연율 환산 관행 (market_data 와 같은 값)

# 판정 기준선 — 실무에서 널리 쓰는 눈금이다. 강의가 숫자를 못박지 않은 자리라
# **기준의 출처를 이 주석에 남기고** 리포트에도 "관행 기준" 이라고 밝힌다.
SHARPE_GOOD = 1.0                      # 1 이상이면 감수한 변동성만큼은 벌었다고 본다
SHARPE_WEAK = 0.5
MDD_WATCH = -20.0                      # 고점 대비 -20% 는 흔히 '조정' 과 '약세장' 을 가르는 선
MDD_SEVERE = -40.0
# 상관계수 — 15.md 자산배분에서 분산효과가 사라지는 구간
CORR_HIGH = 0.7

# 관측이 이보다 적으면 연율 환산 자체가 의미를 잃는다 (한 달치로 연 변동성을 말하지 않는다)
MIN_OBSERVATIONS = 60


def _returns(prices: Sequence[float]) -> List[float]:
    """일간 단순수익률. 0 이하 가격은 건너뛴다 (액면분할 전후 결측 방어)."""
    clean = [float(p) for p in prices if isinstance(p, (int, float)) and p > 0]
    return [clean[i] / clean[i - 1] - 1 for i in range(1, len(clean))]


def performance(prices: Sequence[float], risk_free: Optional[float] = None,
                periods_per_year: int = TRADING_DAYS_PER_YEAR) -> Dict:
    """CAGR · 변동성 · MDD · 샤프 · 소르티노 · 칼마 · 승률 한 묶음 (14.md).

    `risk_free` 는 **연율 소수**다 (0.03758). 비우면 ECOS 국고채 3년을 실측해 쓴다.
    """
    clean = [float(p) for p in prices if isinstance(p, (int, float)) and p > 0]
    returns = _returns(clean)
    if len(returns) < MIN_OBSERVATIONS:
        return {"available": False,
                "reason": (f"관측 {len(returns)}일로는 연율 지표를 못 낸다 "
                           f"(최소 {MIN_OBSERVATIONS}일 — 한 달치로 연 변동성을 말하지 않는다)")}

    if risk_free is None:
        from . import macro                             # 순환 import 를 피해 함수 안에서 부른다
        rate_row = macro.risk_free_rate()
        risk_free = (rate_row.get("value") or macro.FALLBACK_RISK_FREE_PCT) / 100
        rate_source = rate_row.get("source") or "fallback"
        rate_as_of = rate_row.get("as_of", "")
    else:
        rate_source, rate_as_of = "호출자가 지정", ""

    years = len(returns) / periods_per_year
    total_multiple = clean[-1] / clean[0]
    cagr = (total_multiple ** (1 / years) - 1) * 100 if years > 0 else None
    volatility = stdev(returns) * math.sqrt(periods_per_year) * 100 if len(returns) > 1 else None

    # MDD — 누적 대비 최고점에서 얼마나 빠졌나
    peak = clean[0]
    drawdown = 0.0
    trough_index = 0
    for index, price in enumerate(clean):
        peak = max(peak, price)
        fall = price / peak - 1
        if fall < drawdown:
            drawdown, trough_index = fall, index
    mdd = drawdown * 100

    daily_rf = risk_free / periods_per_year
    excess = [r - daily_rf for r in returns]
    sharpe = None
    if len(returns) > 1 and stdev(returns) > 0:
        sharpe = fmean(excess) / stdev(returns) * math.sqrt(periods_per_year)

    downside = [r for r in returns if r < 0]
    sortino = None
    if len(downside) > 1 and stdev(downside) > 0:
        sortino = fmean(excess) / stdev(downside) * math.sqrt(periods_per_year)

    calmar = None if (cagr is None or mdd == 0) else (cagr / abs(mdd))
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100

    return {
        "available": True,
        "observations": len(returns),
        "years": round(years, 2),
        "cagr_pct": None if cagr is None else round(cagr, 2),
        "volatility_pct": None if volatility is None else round(volatility, 2),
        "mdd_pct": round(mdd, 2),
        "mdd_index": trough_index,
        "sharpe": None if sharpe is None else round(sharpe, 3),
        "sortino": None if sortino is None else round(sortino, 3),
        "calmar": None if calmar is None else round(calmar, 3),
        "win_rate_pct": round(win_rate, 1),
        "risk_free_pct": round(risk_free * 100, 3),
        "risk_free_source": rate_source,
        "risk_free_as_of": rate_as_of,
        "basis": "08강 14.md 성과 지표 · 위험 조정 지표",
        "limitation": ("과거 관측이다. 표본 구간을 바꾸면 값이 달라지고, "
                       "정규분포를 가정한 지표라 꼬리 위험을 과소평가한다."),
    }


def sharpe_verdict(row: Dict) -> Dict:
    """샤프지수 판정 — 무위험이자율 출처를 반드시 함께 적는다."""
    if not row.get("available") or row.get("sharpe") is None:
        return verdict(GRADE_UNKNOWN, "샤프지수", row.get("reason", "샤프지수를 못 냈다"),
                       basis="14.md")
    value = row["sharpe"]
    if value >= SHARPE_GOOD:
        grade, why = GRADE_GOOD, f"{SHARPE_GOOD:.1f} 이상 — 감수한 변동성만큼은 벌었다"
    elif value >= SHARPE_WEAK:
        grade, why = GRADE_WARNING, f"{SHARPE_WEAK:.1f}~{SHARPE_GOOD:.1f} — 위험 대비 성과가 얇다"
    else:
        grade, why = GRADE_SERIOUS, "위험을 감안하면 무위험자산보다 나을 것이 없다"
    why += f" (무위험이자율 {row['risk_free_pct']}% · {row['risk_free_source']})"
    return verdict(grade, "샤프지수", why, value, "", "08강 14.md — 초과수익 ÷ 전체 변동성")


def mdd_verdict(row: Dict) -> Dict:
    """MDD 판정 — '견뎌야 할 손실 폭' 으로 읽는다 (14.md)."""
    if not row.get("available"):
        return verdict(GRADE_UNKNOWN, "MDD", row.get("reason", "MDD 를 못 냈다"), basis="14.md")
    value = row["mdd_pct"]
    if value <= MDD_SEVERE:
        grade, why = GRADE_SERIOUS, f"{MDD_SEVERE:.0f}% 보다 깊다 — 회복에 오래 걸린다"
    elif value <= MDD_WATCH:
        grade, why = GRADE_WARNING, f"{MDD_WATCH:.0f}% 를 넘는 낙폭이 있었다"
    else:
        grade, why = GRADE_GOOD, "낙폭이 조정 수준에 머물렀다"
    return verdict(grade, "MDD", why, value, "%", "08강 14.md — 고점 대비 최대 하락률")


# ─────────────────────────────────────────────────────────────
# 상관 · 분산효과 (15.md 자산배분)
# ─────────────────────────────────────────────────────────────
def correlation(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """두 수익률 계열의 피어슨 상관계수.

    ⚠️ **수준(가격)끼리 재지 않는다.** 둘 다 우상향이면 허위 상관이 나온다
    (U7 결정에서 이미 못박은 규칙 — `/market` 겹쳐보기와 같은 규약을 쓴다).
    """
    pairs = [(x, y) for x, y in zip(a, b)
             if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    if len(pairs) < 3:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x, mean_y = fmean(xs), fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator = math.sqrt(sum((x - mean_x) ** 2 for x in xs) *
                            sum((y - mean_y) ** 2 for y in ys))
    return None if denominator == 0 else numerator / denominator


def correlation_matrix(price_map: Dict[str, Sequence[float]]) -> Dict:
    """종목별 종가 → 일간 **수익률** 상관 행렬 + 분산효과 판정."""
    codes = [c for c, prices in price_map.items() if len(_returns(prices)) >= 3]
    if len(codes) < 2:
        return {"available": False, "reason": "수익률을 낼 수 있는 계열이 둘 미만이다"}

    returns = {code: _returns(price_map[code]) for code in codes}
    length = min(len(r) for r in returns.values())
    returns = {code: values[-length:] for code, values in returns.items()}

    matrix = []
    high_pairs = []
    undefined = []
    for row_code in codes:
        row = []
        for col_code in codes:
            value = 1.0 if row_code == col_code else correlation(returns[row_code],
                                                                returns[col_code])
            row.append(None if value is None else round(value, 3))
            if row_code < col_code:
                if value is None:
                    undefined.append(f"{row_code}–{col_code}")
                elif value >= CORR_HIGH:
                    high_pairs.append({"pair": [row_code, col_code], "rho": round(value, 3)})
        matrix.append(row)

    return {
        "available": True,
        "codes": codes,
        "matrix": matrix,
        "observations": length,
        "high_pairs": high_pairs,
        "undefined_pairs": undefined,
        "grade": GRADE_WARNING if high_pairs else GRADE_GOOD,
        "why": (f"상관 {CORR_HIGH} 이상인 쌍이 {len(high_pairs)}개다 — 그만큼 분산효과가 줄어든다"
                if high_pairs else f"상관 {CORR_HIGH} 이상인 쌍이 없다 — 분산효과가 살아 있다"),
        "basis": "08강 15.md 자산배분 — 상관이 낮을수록 분산효과가 크다",
        "limitation": ("일간 변화율 기준이다. 수준끼리 재면 둘 다 우상향해 허위 상관이 나온다. "
                       "그리고 상관은 위기 국면에서 함께 1 로 수렴하는 경향이 있다."),
    }


def diversification(matrix_row: Dict, weights: Optional[Sequence[float]] = None) -> Dict:
    """분산 정도를 한 숫자로 — 유효 종목 수 (Herfindahl 역수).

    비중을 안 주면 동일비중으로 본다. 비중이 한 종목에 쏠려 있으면
    상관이 낮아도 분산이 안 된 것이다.
    """
    codes = matrix_row.get("codes") or []
    if not codes:
        return {"available": False, "reason": "구성 종목이 없다"}
    if not weights:
        weights = [1 / len(codes)] * len(codes)
    total = sum(weights)
    if total <= 0:
        return {"available": False, "reason": "비중 합이 0이다"}
    normalized = [w / total for w in weights]
    hhi = sum(w * w for w in normalized)
    effective = 1 / hhi if hhi else None
    return {
        "available": True,
        "hhi": round(hhi, 4),
        "effective_count": None if effective is None else round(effective, 2),
        "count": len(codes),
        "grade": (GRADE_GOOD if effective and effective >= len(codes) * 0.6
                  else GRADE_WARNING),
        "why": (f"종목 {len(codes)}개인데 유효 종목 수는 {effective:.1f}개다"
                if effective else "유효 종목 수를 못 냈다"),
        "basis": "08강 15.md 자산배분 — 비중 집중도(HHI)의 역수",
    }


def summarize(prices: Sequence[float], peers: Optional[Dict[str, Sequence[float]]] = None,
              risk_free: Optional[float] = None) -> Dict:
    """H04 가 통째로 받는 묶음."""
    perf = performance(prices, risk_free)
    return {
        "performance": perf,
        "sharpe": sharpe_verdict(perf),
        "mdd": mdd_verdict(perf),
        "correlation": correlation_matrix(peers) if peers else
                       {"available": False, "reason": "비교 대상이 없어 상관을 내지 않았다"},
        "basis": "08강 14·15 포트폴리오·자산배분",
    }


__all__ = ["performance", "sharpe_verdict", "mdd_verdict", "correlation",
           "correlation_matrix", "diversification", "summarize",
           "TRADING_DAYS_PER_YEAR", "SHARPE_GOOD", "MDD_WATCH", "CORR_HIGH"]
