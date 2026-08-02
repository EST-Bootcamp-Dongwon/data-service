"""H06 해석카드 — 관찰 → 의미 → 인과 → 대안 → 한계 → 다음 확인 (공통계약 §11)

LLM 없이 문장을 만든다. 그래서 **템플릿에 판정 결과를 끼워 넣는** 방식이다.
대신 지키는 것이 있다.

    · 관찰(observation)에는 **숫자만** 넣는다. 해석을 섞지 않는다.
    · 의미(meaning)와 인과(causal_hypothesis)를 나눈다.
    · 한계(limitation)는 M3 가 만든 `caveats` 를 **그대로** 싣는다 (같은 규약 유지).
    · 대안(alternative)은 Red Team 이 만든 반대 근거를 가져다 쓴다.

공통계약 §11 이 요구하는 10개 필드를 다 채우되, 채울 수 없으면 빈 문자열이 아니라
"확인하지 못했다" 를 적는다. 빈칸은 읽는 사람이 '없다' 와 '안 봤다' 를 구분하지 못한다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

UNCHECKED = "확인하지 못했다"


def card(card_id: str, visual_id: str, research_question: str, observation: str,
         meaning: str, causal: str = "", alternative: str = "", limitation: str = "",
         next_check: str = "", confidence: str = "medium") -> Dict:
    """C5 해석카드 한 장 (명세 §5.2 C5_interpretation 필드 그대로)."""
    return {
        "id": card_id,
        "visual_id": visual_id,
        "research_question": research_question,
        "observation": observation or UNCHECKED,
        "meaning": meaning or UNCHECKED,
        "causal_hypothesis": causal or UNCHECKED,
        "alternative": alternative or UNCHECKED,
        "limitation": limitation or UNCHECKED,
        "next_check": next_check or UNCHECKED,
        "confidence": confidence,
    }


def _fmt(value: Optional[float], unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}{unit}"


def financial_card(workstream: str, index: int, series: List[Dict], growth: Dict,
                   ratios: Dict, red: Dict) -> Dict:
    """재무 시계열 해석카드 (15장 slot 8)."""
    years = [row["year"] for row in series]
    revenues = [row.get("revenue") for row in series]
    span = f"{years[0]}~{years[-1]}" if years else "—"

    observation = (f"{span} 매출 {_fmt((revenues[0] or 0) / 1e12)}조 → "
                   f"{_fmt((revenues[-1] or 0) / 1e12)}조") if len(revenues) >= 2 else UNCHECKED
    if growth.get("available") and growth.get("cagr") is not None:
        observation += f" (CAGR {growth['cagr']:+.1f}%)"

    margin = ratios.get("operating_margin", {})
    roe_row = ratios.get("roe", {})
    meaning = (f"영업이익률 {_fmt(margin.get('value'), '%')} · ROE {_fmt(roe_row.get('value'), '%')}"
               f" — {roe_row.get('why', '')}")

    dupont = ratios.get("dupont", {})
    causal = (f"ROE 를 끄는 것은 {dupont.get('main_driver')} 다 "
              f"(순이익률 {dupont.get('net_margin')}% × 자산회전율 {dupont.get('asset_turnover')} "
              f"× 레버리지 {dupont.get('leverage')})") if dupont.get("available") else ""

    earnings = next((c for c in red.get("checks", []) if c["check"] == "실적 개선 주장"), {})
    alternative = earnings.get("counter") or ""

    quality = ratios.get("earnings_quality", {})
    limitation = "연결 기준 사업보고서 수치다. 일회성 손익은 주석을 사람이 확인해야 한다."
    if quality.get("available"):
        limitation += f" 영업현금흐름/영업이익 {quality['cash_conversion']:.2f}."

    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "이 회사의 매출과 이익은 어떤 모양으로 움직였나?",
        observation, meaning, causal, alternative, limitation,
        earnings.get("next_check") or "다음 분기 실적으로 마진 추세를 재확인한다",
        "high" if len(series) >= 4 else "medium")


def peer_card(workstream: str, index: int, peers: Dict, position: Dict,
              screen: Dict, red: Dict) -> Dict:
    """피어 비교 해석카드 (15장 slot 10)."""
    if not position.get("available"):
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "피어 대비 어느 위치인가?", position.get("reason", UNCHECKED),
                    "상대 위치를 내지 못했다", limitation=peers.get("note", ""),
                    next_check="피어를 직접 지정하면 비교가 가능하다", confidence="low")

    observation = (f"PER {position['value']} · 피어 중앙값 {position['peer_median']} "
                   f"({position['peer_count']}곳) · {position['premium_pct']:+.1f}%")
    meaning = f"{position['stance']} 구간이다 — {screen.get('verdict')}"
    causal = " / ".join(screen.get("reasons", [])) or ""
    undervalued = next((c for c in red.get("checks", []) if c["check"] == "저평가 주장"), {})
    alternative = undervalued.get("counter") or " / ".join(screen.get("blockers", []))
    limitation = (f"피어는 {peers.get('method')} 로 골랐다. {peers.get('note', '')} "
                  f"피어가 바뀌면 이 판정도 바뀐다.")
    return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                "같은 업종·비슷한 크기의 회사와 견주면 어디쯤인가?",
                observation, meaning, causal, alternative, limitation,
                undervalued.get("next_check") or "다음 분기 실적 발표 후 멀티플을 다시 잰다",
                "medium" if position["peer_count"] >= 5 else "low")


def timeseries_card(workstream: str, index: int, forecast: Dict, red: Dict) -> Dict:
    """시계열 예측 해석카드 (15장 slot 14).

    ⚠️ `limitation` 은 M3 가 만든 `caveats` 를 **그대로** 옮긴다.
    M3 가 이미 근사·한계를 문장으로 정리해 뒀으므로 여기서 다시 쓰면 규약이 갈라진다.
    """
    if not forecast or forecast.get("skipped"):
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "앞으로 어느 범위에서 움직일 가능성이 큰가?",
                    forecast.get("reason", UNCHECKED) if forecast else UNCHECKED,
                    "시계열 모델링을 건너뛰었다",
                    limitation="전처리 판정이 insufficient 라 모델을 적합하지 않았다 (명세 §3.2)",
                    next_check="자료 기간을 늘려 다시 시도한다", confidence="low")

    statistical = forecast.get("statistical", {})
    probability = forecast.get("probability", {})
    backtest = probability.get("backtest", {})
    scenarios = forecast.get("scenarios", [])

    observation = (f"{statistical.get('model', '모형')} · {forecast.get('horizon_days')}거래일 예측 · "
                   f"상승확률 {probability.get('up_prob', 0):.0%}")
    if scenarios:
        observation += " · " + " / ".join(f"{s['name']} {s.get('prob', 0):.0%}" for s in scenarios)

    trend = next((c for c in red.get("checks", []) if c["check"] == "추세 주장"), {})
    meaning = (f"백테스트 {backtest.get('folds')}폴드 · 적중률 "
               f"{backtest.get('hit_rate', 0):.0%} · 확률보행 대비 {backtest.get('vs_random_walk')}")
    causal = ("시나리오 경계는 최근 60거래일 고점·저점이고 확률은 예측분포에서 나왔다 "
              "(U4 선행결정 — 경계는 기술적, 확률은 통계적)")
    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "앞으로 어느 범위에서 움직일 가능성이 큰가?",
        observation, meaning, causal,
        trend.get("counter") or "",
        " / ".join(forecast.get("caveats", [])) or UNCHECKED,
        trend.get("next_check") or "다음 20거래일 뒤 실제값과 대조한다",
        "medium" if trend.get("verdict") == "통과" else "low")


def segment_card(workstream: str, index: int, facts: Dict) -> Dict:
    """사업부 구조 해석카드 (15장 slot 4) — 사업보고서 원문에서 나온다."""
    segments = (facts or {}).get("segments", {})
    if not facts or not facts.get("available") or not segments.get("found"):
        reason = (segments.get("reason") if segments else "") or (facts or {}).get("reason", UNCHECKED)
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "매출이 어느 사업에서 나오나?", reason,
                    "사업부 구조를 확인하지 못했다",
                    limitation="DART 정형 API 에는 세그먼트가 없어 사업보고서 원문에서 찾는다. "
                               "서식이 회사마다 달라 못 찾는 경우가 있다.",
                    next_check="사업보고서 'II. 사업의 내용' 을 사람이 확인한다", confidence="low")

    rows = segments["rows"]
    top = rows[0]
    observation = " · ".join(f"{r['segment']} {r['values'][0]:,.0f}" for r in rows[:4])
    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "매출이 어느 사업에서 나오나?",
        observation,
        f"가장 큰 사업은 {top['segment']} 다 — 이 부문의 업황이 전사 실적을 좌우한다",
        "부문 집중도가 높을수록 그 시장의 사이클이 실적에 그대로 전달된다",
        "부문 구분은 회사가 정한 것이라 피어와 기준이 다를 수 있다",
        f"출처: {facts.get('report_name')} ({facts.get('rcept_date')}) · 표 머리행 "
        f"{' / '.join(segments.get('header', [])[:4])}",
        "다음 사업보고서에서 부문 구성이 바뀌는지 본다",
        "high")


# ─────────────────────────────────────────────────────────────
# M5 — CORP-TP 해석카드
# ─────────────────────────────────────────────────────────────
def event_card(workstream: str, index: int, events: Dict) -> Dict:
    """12개월 이벤트 창 해석카드 (CORP-TP CTP-04)."""
    if not events or not events.get("available"):
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "최근 12개월에 무슨 일이 있었나?",
                    (events or {}).get("reason", UNCHECKED),
                    "이벤트 창을 만들지 못했다",
                    limitation="공시 목록을 받지 못했다",
                    next_check="DART 공시 목록을 다시 조회한다", confidence="low")

    counts = events.get("counts", {})
    window = events.get("window", {})
    observation = (f"{window.get('window_start_boundary')} 초과 ~ {window.get('window_end')} 이하 "
                   f"공시 {events['total']}건 (구조 {counts.get('구조', 0)} · "
                   f"일회성 {counts.get('일회성', 0)} · 예정 {counts.get('예정', 0)})")
    sentiment = events.get("sentiment", {})
    if sentiment.get("available"):
        observation += " · 감성 " + " / ".join(f"{k} {v}" for k, v in sentiment["counts"].items())

    structural = events.get("structural", [])
    meaning = (f"구조를 바꾼 사건이 {counts.get('구조', 0)}건 있다"
               + (f" — 가장 최근은 {structural[0]['title']}" if structural else ""))
    limitation = (f"창 밖 사건은 세지 않았다 (이전 {events.get('pre_window', 0)}건 · "
                  f"이후 {events.get('future_scheduled', 0)}건). ")
    if sentiment.get("available"):
        limitation += sentiment.get("limitation", "")
    else:
        limitation += f"감성 판정을 붙이지 못했다 — {sentiment.get('reason', '')}"

    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "최근 12개월에 무슨 일이 있었나?",
        observation, meaning,
        "구조 변화는 되돌리기 어려워 다음 분기 실적에 이어질 가능성이 크다 — 다만 가설이다",
        "공시는 회사가 낸 사실이지 시장의 해석이 아니다. 주가 반응과 인과를 섞지 않는다.",
        limitation,
        "예정 사건의 실현 여부를 확인일에 다시 본다",
        "high" if events["total"] >= 20 else "medium")


def score_card(workstream: str, index: int, scorecard: Dict, decision: Dict) -> Dict:
    """Quick Score 해석카드 (CORP-TP CTP-10·CTP-13)."""
    if not scorecard or scorecard.get("index") is None:
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "이 회사를 정식으로 볼 가치가 있나?",
                    "점수를 매긴 차원이 없다", "판정을 유보한다",
                    limitation="여섯 차원 모두 근거가 모자랐다",
                    next_check="자료를 더 모아 다시 돌린다", confidence="low")

    rows = [r for r in scorecard["rows"] if not r["separate"]]
    observation = " · ".join(
        f"{r['name']} {r['score'] if r['score'] is not None else 'Unscored'}" for r in rows)
    observation += f" → 기회지수 {scorecard['index']}/5"

    source_row = scorecard.get("source_confidence") or {}
    meaning = (f"{decision.get('verdict')} — " + " / ".join(decision.get("reasons", []))
               + f" (Source confidence {source_row.get('score', 'Unscored')} — 회사의 질이 아니라 근거의 강도다)")

    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "이 회사를 정식으로 볼 가치가 있나?",
        observation, meaning,
        scorecard.get("index_note", ""),
        " / ".join(sum((r.get("counter_evidence", []) for r in rows), [])[:3]) or "",
        (("여섯 차원을 모두 채점했다. " if not scorecard.get("unscored") else
          f"미채점 {len(scorecard['unscored'])}건({', '.join(scorecard['unscored'])}) — "
          "**Unscored 를 0점으로 읽지 않는다.** ")
         + "부담·위험은 높을수록 불리한 방향이라 지수에 넣을 때 뒤집었다."),
        " / ".join(decision.get("conditions", [])[:2]) or "재검토일을 정한다",
        "medium" if scorecard.get("coverage", 0) >= 0.6 else "low")


# ─────────────────────────────────────────────────────────────
# M5 — IND-R 해석카드
# ─────────────────────────────────────────────────────────────
def industry_card(workstream: str, index: int, analysis: Dict) -> Dict:
    """산업 구조·규모 해석카드 (IND-R H04·H05)."""
    target = analysis.get("target", {})
    market = analysis.get("market", {})
    chain = analysis.get("value_chain", {})
    if not market.get("available"):
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "이 산업의 경계와 크기는?",
                    market.get("reason", UNCHECKED), "산업 규모를 못 냈다",
                    limitation=target.get("limitation", ""),
                    next_check="업종 자릿수를 넓혀 다시 본다", confidence="low")

    production = market.get("production", {})
    observation = (f"{target.get('industry_code')} {target.get('name')} · 상장사 "
                   f"{market['listed_count']}곳 · 상장 시가총액 합계 "
                   f"{(market.get('listed_market_cap') or 0) / 1e12:,.1f}조 (기준일 {market.get('as_of')})")
    if production.get("available"):
        observation += (f" · {production['series_name']} 생산지수 {production['latest']}"
                        f" (YoY {production.get('yoy_pct')}% · {production.get('cagr_years')}년 "
                        f"CAGR {production.get('cagr_pct')}%)")

    position = (chain.get("position") or {})
    meaning = f"인접 소분류 {chain.get('adjacent_count', 0)}개와 같은 중분류를 이룬다"
    if position.get("available"):
        meaning += f" · 밸류체인 위치는 {position['estimated']} 로 추정된다 ({position['score']:.2f})"
    elif position.get("reason"):
        meaning += f" · 밸류체인 위치는 판정을 유보했다 ({position['reason']})"

    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "이 산업의 경계와 크기는?",
        observation, meaning,
        "생산지수가 오르면 매출이 따라오는 경로를 가정하지만, 가격 효과가 빠져 있어 가설이다",
        "상장 시가총액은 기대의 크기다 — 실제 시장 규모와 다르게 움직일 수 있다",
        market.get("limitation", "") + " " + (production.get("limitation", "")
                                              if production.get("available") else ""),
        "산업협회·시장조사 보고서에서 시장 정의와 규모를 확인한다",
        "medium")


def cycle_card(workstream: str, index: int, cycle: Dict) -> Dict:
    """산업 사이클 해석카드 (IND-R H11)."""
    if not cycle or not cycle.get("available"):
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "지금 이 산업은 어느 국면인가?",
                    (cycle or {}).get("reason", UNCHECKED), "국면을 판정하지 않았다",
                    limitation="경기·생산·주가 신호를 받지 못했다",
                    next_check="ECOS·KOSIS 조회를 다시 시도한다", confidence="low")

    observation = " · ".join(f"{v['source']}: {v['signal']}({v['detail']})"
                             for v in cycle.get("votes", []))
    economy = cycle.get("economy", {})
    sensitivity = cycle.get("sensitivity", {})
    meaning = f"{cycle['phase']} — {cycle.get('vote_summary')}"
    if economy.get("available"):
        meaning += f" · 거시 국면은 {economy['phase']}"
    causal = ""
    if sensitivity.get("available"):
        causal = (f"{sensitivity['name']} 은 {sensitivity['rate']['label']} · "
                  f"{sensitivity['fx']['label']} — {sensitivity['why']}")

    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "지금 이 산업은 어느 국면인가?",
        observation, meaning, causal,
        economy.get("divergence") or "선행과 동행이 갈리면 국면 판정이 뒤집힐 수 있다",
        cycle.get("limitation", ""),
        (cycle.get("scenarios") or [{}])[0].get("flip_kpi", "생산지수 YoY 부호 전환을 본다"),
        cycle.get("confidence", "low"))


# ─────────────────────────────────────────────────────────────
# M5 — IND-TP 해석카드
# ─────────────────────────────────────────────────────────────
def ranking_card(workstream: str, index: int, analysis: Dict) -> Dict:
    """후보 순위·민감도 해석카드 (IND-TP S10·S11·S13)."""
    if not analysis or not analysis.get("available"):
        return card(f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
                    "이 산업에서 먼저 볼 후보는?",
                    (analysis or {}).get("reason", UNCHECKED), "후보 순위를 못 냈다",
                    limitation="후보군을 만들지 못했다",
                    next_check="업종 범위를 넓혀 다시 본다", confidence="low")

    universe = analysis["universe"]
    ranking = [r for r in analysis["ranking"] if r.get("rank")]
    ranking.sort(key=lambda r: r["rank"])
    swings = analysis.get("sensitivity", {})

    observation = " · ".join(
        f"{r['rank']}위 {r['name']} {r.get('adjusted_display')}"
        + ("(공동)" if r.get("tied") else "") for r in ranking[:5])
    observation += f" (후보 {universe['count']}/{universe['universe_total']}곳)"

    meaning = (f"순위 안정성 {swings.get('stability')} — {swings.get('stability_why')}. "
               f"1·2위 점수 차이 {swings.get('top_margin')}")
    return card(
        f"I-{workstream}-{index:04d}", f"V-{workstream}-{index:04d}",
        "이 산업에서 먼저 볼 후보는?",
        observation, meaning,
        "점수는 후보군 안 상대 위치다 — 산업이 통째로 좋거나 나쁠 가능성은 여기에 안 들어 있다",
        (f"{swings.get('flip_count', 0)}개 시나리오에서 1위가 바뀐다"
         + (f" ({', '.join(swings.get('flip_scenarios', [])[:2])})"
            if swings.get("flip_scenarios") else "")),
        (universe.get("bias", "") + " " +
         "missing penalty 로 결측을 벌점 처리했다 — 자료가 적은 후보가 불리하게 나온다."),
        "총점 1위를 확정하지 않는다. 사람이 승인한다 (설계서 ITP-T10)",
        "medium" if swings.get("stability") == "높음" else "low")
