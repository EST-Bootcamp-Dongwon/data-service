"""H07 Red Team — 반대 근거를 **규칙으로** 만든다 (명세 §5.5)

LLM 이 없다. 그래도 반대 심문은 해야 한다. 명세 §5.5 의 규칙표 5종을 그대로 옮긴다.

    "매출이 성장했다"   → 환율 기여 분리 · 물량/가격 분해가 되는가 → 불가하면 E-CAUSAL
    "저평가 상태다"     → 피어 대비 성장률·마진 차이 → 차이가 크면 "디스카운트에 이유가 있다"
    "추세가 상승이다"   → 확률보행 대비 유의한가 · 구간을 바꾸면 뒤집히는가
    "실적이 개선됐다"   → 일회성·회계기준 변경·기저효과
    모든 상관 주장      → 경쟁 가설 최소 1개 + 중간 KPI 요구

각 검사는 **통과 · 실패 · 판정불가** 를 돌려주고, 실패하면 그 주장의 confidence 를 한 단계 내린다.
'판정불가' 를 '통과' 로 세지 않는 것이 중요하다 — 못 본 것과 봤는데 괜찮은 것은 다르다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

PASS = "통과"
FAIL = "실패"
UNKNOWN = "판정불가"


def _check(name: str, verdict: str, finding: str, counter: str = "",
           code: str = "", next_check: str = "") -> Dict:
    return {"check": name, "verdict": verdict, "finding": finding,
            "counter_evidence": counter, "code": code, "next_check": next_check}


# ─────────────────────────────────────────────────────────────
# 1. "매출이 성장했다"
# ─────────────────────────────────────────────────────────────
def revenue_growth_claim(growth: Optional[float], currency: str = "KRW",
                         has_fx_breakdown: bool = False,
                         has_volume_price: bool = False) -> Dict:
    """환율 기여와 물량/가격 분해가 되는지 본다.

    공개 API 로는 둘 다 **안 된다.** DART 재무제표에는 환율 효과도, 물량·단가도 없다.
    그래서 이 검사는 거의 언제나 '판정불가' 다 — 그리고 그 사실을 숨기지 않는다.
    """
    if growth is None:
        return _check("매출 성장 주장", UNKNOWN, "성장률 자체가 없다")
    if growth <= 0:
        return _check("매출 성장 주장", PASS, f"성장 주장을 하지 않았다 (성장률 {growth:+.1f}%)")

    missing = []
    if not has_fx_breakdown:
        missing.append("환율 기여")
    if not has_volume_price:
        missing.append("물량/가격 분해")
    if not missing:
        return _check("매출 성장 주장", PASS, "환율과 물량/가격을 분리해 확인했다")

    return _check(
        "매출 성장 주장", UNKNOWN,
        f"매출이 {growth:+.1f}% 늘었지만 {' · '.join(missing)}를 분리하지 못했다",
        counter="해외 매출 비중이 큰 회사라면 성장의 일부가 환율일 수 있다. "
                "단가 인상인지 물량 증가인지도 구분되지 않는다.",
        code="E-CAUSAL",
        next_check="사업보고서 '매출 및 수주상황' 의 품목별 단가·수량 표로 확인한다")


# ─────────────────────────────────────────────────────────────
# 2. "저평가 상태다"
# ─────────────────────────────────────────────────────────────
def undervalued_claim(peer_position: Dict, growth: Optional[float],
                      margin: Optional[float], peer_growth: Optional[float] = None,
                      peer_margin: Optional[float] = None) -> Dict:
    """싼 데는 이유가 있는지 따진다 (명세 §5.5 2행)."""
    if not peer_position.get("available"):
        return _check("저평가 주장", UNKNOWN, "피어 비교가 없어 저평가를 논할 수 없다",
                      code="G-SCOPE")

    premium = peer_position.get("premium_pct", 0)
    if premium >= -20:
        return _check("저평가 주장", PASS,
                      f"피어 대비 {premium:+.1f}% — 저평가 주장을 하지 않았다")

    problems = []
    if growth is not None and growth < 0:
        problems.append(f"매출이 {growth:+.1f}% 로 줄고 있다")
    if peer_growth is not None and growth is not None and growth < peer_growth - 5:
        problems.append(f"성장률이 피어({peer_growth:+.1f}%)보다 뚜렷이 낮다")
    if peer_margin is not None and margin is not None and margin < peer_margin - 3:
        problems.append(f"마진이 피어({peer_margin:.1f}%)보다 낮다")

    if problems:
        return _check(
            "저평가 주장", FAIL,
            f"PER 이 피어보다 {abs(premium):.1f}% 낮지만 그럴 만한 이유가 있다",
            counter="디스카운트는 시장의 오류가 아니라 반영일 수 있다 — " + " · ".join(problems),
            next_check="디스카운트가 좁혀지려면 무엇이 달라져야 하는지 조건을 적는다")

    return _check("저평가 주장", PASS,
                  f"피어 대비 {premium:.1f}% 낮은데 성장·마진에서 뚜렷한 열위를 찾지 못했다",
                  next_check="다음 분기 실적으로 마진 추세를 재확인한다")


# ─────────────────────────────────────────────────────────────
# 3. "추세가 상승이다"
# ─────────────────────────────────────────────────────────────
def trend_claim(backtest: Optional[Dict], up_prob: Optional[float]) -> Dict:
    """확률보행을 못 이기면 '추세' 라고 부르지 않는다 (기획 D6 · 명세 §4.4)."""
    if not backtest:
        return _check("추세 주장", UNKNOWN, "백테스트가 없어 추세의 유의성을 못 본다",
                      code="G-DATA")

    versus = backtest.get("vs_random_walk")
    hit = backtest.get("hit_rate")
    beats = False
    if isinstance(versus, str):
        beats = versus.strip().startswith("+")
    elif isinstance(versus, (int, float)):
        beats = versus > 0

    if not beats:
        return _check(
            "추세 주장", FAIL,
            f"모형이 확률보행을 못 이겼다 (vs_random_walk={versus})",
            counter="추세라고 부를 근거가 없다. 같은 방향이 이어진 것은 우연과 구분되지 않는다.",
            next_check="구간을 바꿔 다시 적합해 보고, 그래도 못 이기면 방향 주장을 빼라")

    if hit is not None and hit < 0.5:
        return _check("추세 주장", FAIL,
                      f"RMSE 는 이겼지만 방향 적중률이 {hit:.0%} 로 동전 던지기보다 못하다",
                      counter="오차는 줄었어도 방향을 맞히지 못하면 매매 판단에 쓸 수 없다")

    return _check("추세 주장", PASS,
                  f"확률보행 대비 개선({versus}) · 적중률 {hit:.0%}" if hit is not None
                  else f"확률보행 대비 개선({versus})",
                  next_check="표본 구간을 바꿔도 결론이 유지되는지 확인한다")


# ─────────────────────────────────────────────────────────────
# 4. "실적이 개선됐다"
# ─────────────────────────────────────────────────────────────
def earnings_improved_claim(series: List[Dict], quality: Optional[Dict]) -> Dict:
    """기저효과·일회성·회계기준 변경을 짚는다 (명세 §5.5 4행)."""
    values = [row.get("operating_income") for row in series if row.get("operating_income") is not None]
    if len(values) < 3:
        return _check("실적 개선 주장", UNKNOWN, "연도가 3개 미만이라 기저효과를 못 본다")

    last, prev, prev2 = values[-1], values[-2], values[-3]
    findings = []
    verdict_value = PASS

    # 기저효과 — 직전 해가 유난히 나빴으면 '개선' 은 회복일 뿐이다
    if prev > 0 and prev2 > 0 and prev < prev2 * 0.6 and last > prev:
        verdict_value = FAIL
        findings.append(f"직전 해 영업이익이 그 전해의 {prev / prev2:.0%} 수준으로 급락했다 — "
                        "올해 증가는 기저효과일 수 있다")
    if prev <= 0 < last:
        verdict_value = FAIL
        findings.append("적자에서 흑자로 돌아선 것이라 증가율(%)은 의미가 없다")

    # 이익의 질 — 장부 이익만 늘고 현금이 안 따라오면 개선이라 부르기 어렵다
    if quality and quality.get("available") and quality.get("cash_conversion") is not None:
        if quality["cash_conversion"] < 0.5:
            verdict_value = FAIL
            findings.append(f"영업현금흐름이 영업이익의 {quality['cash_conversion']:.0%} 에 그친다 — "
                            "이익이 현금으로 들어오지 않았다")

    if not findings:
        return _check("실적 개선 주장", PASS, "기저효과·현금전환에서 뚜렷한 문제를 찾지 못했다",
                      next_check="일회성 손익은 재무제표 주석을 사람이 확인해야 한다")

    return _check("실적 개선 주장", verdict_value, " / ".join(findings),
                  counter="증가율만 보면 개선으로 보이지만 출발점과 현금흐름을 함께 봐야 한다",
                  code="E-PERIOD" if "기저효과" in " ".join(findings) else "",
                  next_check="사업보고서 주석에서 일회성 항목과 회계기준 변경을 확인한다")


# ─────────────────────────────────────────────────────────────
# 5. 모든 상관관계 주장
# ─────────────────────────────────────────────────────────────
def correlation_claim(claims: List[Dict]) -> Dict:
    """상관을 인과로 말한 주장에 경쟁 가설과 중간 KPI 를 요구한다 (GIC §12.1 분석 논리 15점)."""
    weak = [c for c in claims
            if c.get("kind") == "correlation" and not (c.get("competing") and c.get("mid_kpi"))]
    if not claims:
        return _check("상관→인과 검사", UNKNOWN, "검사할 주장이 없다")
    if not weak:
        return _check("상관→인과 검사", PASS, "상관 주장마다 경쟁 가설과 중간 KPI 가 붙어 있다")
    return _check(
        "상관→인과 검사", FAIL,
        f"경쟁 가설이나 중간 KPI 가 없는 상관 주장 {len(weak)}건",
        counter="두 값이 같이 움직였다는 것은 원인을 말해 주지 않는다. "
                "제3의 변수가 둘 다 움직였을 수 있다.",
        code="E-CAUSAL",
        next_check="주장마다 '이게 사실이면 먼저 보여야 할 중간 지표' 를 하나씩 정한다")


def run_all(context: Dict) -> Dict:
    """검사 5종을 모두 돌리고 요약한다.

    `context` 에는 H04 가 만든 재료가 들어온다 (성장률 · 피어 위치 · 백테스트 · 재무 시계열).
    """
    checks = [
        revenue_growth_claim(context.get("revenue_growth"), context.get("currency", "KRW")),
        undervalued_claim(context.get("peer_position") or {}, context.get("revenue_growth"),
                          context.get("operating_margin"), context.get("peer_growth"),
                          context.get("peer_margin")),
        trend_claim(context.get("backtest"), context.get("up_prob")),
        earnings_improved_claim(context.get("financial_series") or [],
                                context.get("earnings_quality")),
        correlation_claim(context.get("claims") or []),
    ]
    failed = [c for c in checks if c["verdict"] == FAIL]
    unknown = [c for c in checks if c["verdict"] == UNKNOWN]
    return {
        "checks": checks,
        "failed": len(failed),
        "unknown": len(unknown),
        "passed": len(checks) - len(failed) - len(unknown),
        # 실패 하나당 confidence 를 한 단계 내린다 (명세 §5.5 마지막 줄)
        "confidence_penalty": len(failed),
        "codes": sorted({c["code"] for c in checks if c.get("code")}),
        "summary": (f"검사 {len(checks)}건 중 통과 {len(checks) - len(failed) - len(unknown)} · "
                    f"실패 {len(failed)} · 판정불가 {len(unknown)}"),
        "note": "판정불가를 통과로 세지 않는다 — 못 본 것과 봤는데 괜찮은 것은 다르다",
    }
