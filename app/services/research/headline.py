"""리포트 표지 — 판정 · 핵심 수치 · 3칼럼 시나리오 · 9축 (M8 · 변경노트 N86)

왜 이 파일이 생겼나
    목표 양식(GIC 리서치 리포트)은 첫 화면에서 **판정 한 줄과 핵심 수치 몇 개**를 보여
    준다. 우리 리포트에는 그 자리가 없었다. 재료는 **팩 안에 이미 다 있었는데**
    읽는 코드가 없었을 뿐이다.

        analysis.valuation.band.upside_pct     계산만 되고 **읽는 코드가 0건**이었다
        analysis.forecast.scenarios            9필드 중 export_md 가 3개만 썼다
        cycle.scenarios[].base / .watch        export_md 가 bull·bear 만 쓰고 버렸다
        evaluation.scores                      9축이 다 있는데 화면은 총점 타일 하나뿐

    `charts.py` 와 같은 자리에 같은 방식으로 둔다 — **여기서 한 번 만들고 세 렌더러가
    그리기만 한다.** 규칙을 두 곳에 두면 어긋날 때 어느 쪽이 거짓말인지 알 수 없다.

        headline.build(pack, analysis, evaluation)  →  CX_workstream.headline
              │
              ├─ static/assets/research.js   화면 (판정 배지 · 타일 · 3칼럼 · 가로 바)
              ├─ export_md.py                마크다운 표
              └─ export_html.py              인쇄용 (인라인 SVG · CDN 없음)

★ 판정을 지어내지 않는다 (이 파일에서 가장 중요한 규칙)
    목표 양식의 첫 줄은 `BUY` 다. **우리는 BUY 를 만들지 않는다.** 우리 시스템이
    실제로 내리는 판정은 워크스트림마다 다르고, 그것을 그대로 쓴다.

        CORP-R    valuation.screen.verdict     저평가 후보 · 프리미엄 · 피어 수준 …
        CORP-TP   verdict.verdict              Proceed · Watch · Drop  (설계서 §6 판정 계약)
        IND-R     cycle.phase                  확장 국면 · 수축 국면 …
        IND-TP    proposal.top_pick            총점 1위 후보 (**확정이 아니다** · ITP-T10)

    `knowledge/technical.py` 의 `SIGNAL_BUY` 는 **사장 모듈**이라 쓰지 않는다
    (어디에서도 import 되지 않는다 · 요약본 §6 U-신규 발견).

    그리고 **모든 판정은 AI 제안이다.** 사람이 승인한다 (불변원칙 §2-9·§2-10).
    그 문장을 판정 옆에서 떼지 않는다.

★ 부호를 뒤집거나 감추지 않는다
    삼성전자 실측 `upside_pct` 가 **-24.5%** 다. 목표 양식이 '상승여력' 이라고 부르는
    자리에 음수가 오는 것이 어색하다고 절댓값을 쓰거나 숨기면 그것은 거짓말이다.
    라벨을 `현재가 대비` 로 두고 부호를 그대로 낸다.

표시 규칙 (U3 · `app.css:11~33` · dataviz 스킬)
    · **9축은 한 색이다.** 축마다 다른 색을 주면 "여덟 개 범주형 색인데 이야기는 숫자
      하나" 라는 전형적 오답이 된다. 채움은 한 색에 상태만 얹고 트랙은 같은 색의 옅은 단계다.
    · **색만으로 방향을 말하지 않는다.** 부호(+/-)·화살표·글자를 늘 함께 낸다.
    · 상태색(good·warning·serious)은 예약색이라 계열 색으로 재사용하지 않는다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 9축 막대의 상태. 채움 색은 한 가지이고 **상태만** 얹는다 (위 표시 규칙 참고).
GOOD_AT = 0.9
WARN_AT = 0.7

SCENARIO_ORDER = ("Bear", "Base", "Bull")     # 왼쪽부터 하락 → 횡보 → 상승


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value, digits: int = 1) -> str:
    """숫자 → 표기. 값이 없으면 **0 이 아니라 물결표**다."""
    if not _is_num(value):
        return "—"
    return f"{value:,.{digits}f}"


def _signed(value, digits: int = 1) -> str:
    """부호를 **반드시** 붙인다 — 색만으로 방향을 말하지 않기 위한 두 번째 채널이다."""
    if not _is_num(value):
        return "—"
    return f"{value:+,.{digits}f}"


def _tone_for_delta(value) -> str:
    """등락 방향 (국내 관행: 상승 빨강 · 하락 파랑 — `app.css` 의 `--up`·`--down`)."""
    if not _is_num(value):
        return "flat"
    return "up" if value > 0 else "down" if value < 0 else "flat"


def _metric(label: str, value, text: str, sub: str = "", tone: str = "",
            unit: str = "") -> Dict:
    """타일 한 칸.

    **원값(`value`)과 표기(`text`)를 같이 싣는다.** 파생값은 원값을 싣고 반올림은
    표기하는 쪽에서만 한다는 규칙 때문이다 (요약본 지뢰 3 — 반올림을 두 번 하면
    사슬이 끊긴다). 렌더러 셋은 `text` 를 그대로 찍어 **같은 글자**를 낸다.
    """
    return {"label": label, "value": value, "text": text, "unit": unit,
            "sub": sub, "tone": tone}


def _verdict(label: str, kind: str, tone: str, basis: str,
             reasons: Optional[List] = None, conditions: Optional[List] = None,
             ai_proposal: bool = True, human_decision: str = "",
             caveat: str = "") -> Dict:
    return {
        "label": label,
        "kind": kind,                 # 무엇에 대한 판정인지 — `BUY` 로 오해되지 않게 한다
        "tone": tone,                 # good · warning · serious · neutral
        "basis": basis,
        "reasons": [str(r) for r in (reasons or []) if r],
        "conditions": [str(c) for c in (conditions or []) if c],
        "ai_proposal": ai_proposal,
        "human_decision": human_decision or "사람 승인 필요 — AI 는 제안만 한다",
        "caveat": caveat,
    }


# ─────────────────────────────────────────────────────────────
# 밸류에이션 밴드 → 타일 (CORP-R · CORP-TP 공용)
# ─────────────────────────────────────────────────────────────
def _band_metrics(band: Dict) -> List[Dict]:
    """피어 멀티플 밴드. **목표주가가 아니라 '밸류에이션 연습 결과' 다.**

    `band_target` 의 docstring 이 그렇게 못박았고 `caveat` 도 그 말을 담고 있다.
    라벨을 `목표주가` 로 바꾸면 우리가 하지 않은 판단을 한 것이 된다.
    """
    if not band.get("available"):
        return []
    metrics = [
        _metric("가치 범위 (중앙)", band.get("base"), f"{_num(band.get('base'), 0)}",
                f"밴드 {_num(band.get('low'), 0)} ~ {_num(band.get('high'), 0)}원 "
                f"· {band.get('method', '')}", unit="원"),
    ]
    # ★ `upside_pct` 는 M7 까지 **계산만 되고 읽는 코드가 0건**이던 필드다 (N86).
    #    부호를 그대로 낸다 — 삼성전자 실측이 -24.5% 다.
    if _is_num(band.get("upside_pct")):
        metrics.append(_metric(
            "현재가 대비", band.get("upside_pct"), f"{_signed(band.get('upside_pct'), 1)}",
            f"현재가 {_num(band.get('current_price'), 0)}원 기준 — "
            "밸류에이션 연습 결과와의 차이지 목표수익률이 아니다",
            tone=_tone_for_delta(band.get("upside_pct")), unit="%"))
    metrics.append(_metric(
        "피어 중앙값 PER", band.get("peer_median_per"),
        f"{_num(band.get('peer_median_per'), 2)}",
        f"EPS {_num(band.get('eps'), 1)}원에 곱했다", unit="배"))
    return metrics


def _price_scenarios(forecast: Dict) -> Dict:
    """가격 시나리오 3칼럼 (Bear · Base · Bull).

    `forecast.scenarios` 는 필드가 아홉 개인데 M7 까지 리포트는 `target`·`prob`·`trigger`
    **셋만** 썼다 (`export_md.py:344~346`). 경계와 그 근거, 무효화 조건이 빠지면
    "참·거짓을 눈으로 판정할 수 있어야 가설이다" 라던 U4 선행 결정이 무너진다.
    """
    rows = forecast.get("scenarios") or []
    if not rows:
        return {}
    by_name = {str(r.get("name")): r for r in rows}
    columns = []
    for name in SCENARIO_ORDER:
        row = by_name.get(name)
        if not row:
            continue
        columns.append({
            "name": name,
            "label": str(row.get("label") or name),
            "tone": {"Bull": "up", "Bear": "down"}.get(name, "flat"),
            "target": row.get("target"),
            "target_text": _num(row.get("target"), 0),
            "target_pct": row.get("target_pct"),
            "target_pct_text": _signed(row.get("target_pct"), 2),
            "prob": row.get("prob"),
            "prob_text": (f"{row['prob'] * 100:,.1f}%" if _is_num(row.get("prob")) else "—"),
            "boundary": row.get("boundary"),
            "boundary_text": (_num(row.get("boundary"), 0) if _is_num(row.get("boundary"))
                              else "구간 안"),
            "boundary_reason": str(row.get("boundary_reason") or ""),
            "trigger": str(row.get("trigger") or ""),
            "invalidation": str(row.get("invalidation") or ""),
        })
    if not columns:
        return {}
    meta = forecast.get("scenario_meta") or {}
    return {
        "kind": "price",
        "unit": "원",
        "columns": columns,
        "fields": [("target_text", "목표"), ("target_pct_text", "현재가 대비 %"),
                   ("prob_text", "확률"), ("boundary_text", "경계"),
                   ("boundary_reason", "경계 근거"), ("trigger", "트리거"),
                   ("invalidation", "무효화")],
        "note": (f"기준 종가 {_num(forecast.get('last_close'), 0)}원 "
                 f"({forecast.get('last_date', '')}) · 확률은 "
                 f"{forecast.get('horizon_days', '—')}거래일 예측분포에서 그 경계를 넘을 확률이다. "
                 "경계는 기술적(고점·저점)이고 확률은 통계적이다 — 둘의 출처가 다르다"),
        "basis": str(meta.get("basis") or "명세 §4.4 · U4 선행 결정 (트리거·무효화 규칙)"),
    }


def _condition_scenarios(cycle: Dict) -> Dict:
    """조건 시나리오 3칼럼 (IND-R).

    원본은 `기간 × {bull, base, bear}` 인데 **읽는 사람은 Bear/Base/Bull 을 나란히
    견주고 싶어 한다.** 그래서 축을 뒤집어 칼럼을 시나리오로 두고 행을 기간으로 둔다.
    `export_md.py:494~499` 는 bull·bear 의 `condition` 만 쓰고 `base` 전부와
    `watch`(무엇을 지켜볼지) 를 버렸다 — 여기서는 버리지 않는다.
    """
    horizons = cycle.get("scenarios") or []
    if not horizons:
        return {}
    key_of = {"Bear": "bear", "Base": "base", "Bull": "bull"}
    columns = []
    for name in SCENARIO_ORDER:
        key = key_of[name]
        cells = []
        for horizon in horizons:
            block = horizon.get(key) or {}
            if not block.get("condition"):
                continue
            cells.append({
                "horizon": f"{horizon.get('horizon_months', '—')}개월",
                "condition": str(block.get("condition") or ""),
                "watch": str(block.get("watch") or ""),
            })
        if cells:
            columns.append({
                "name": name,
                "label": {"Bull": "상승", "Base": "유지", "Bear": "하락"}[name],
                "tone": {"Bull": "up", "Bear": "down"}.get(name, "flat"),
                "cells": cells,
            })
    if not columns:
        return {}
    flip = [str(h.get("flip_kpi")) for h in horizons if h.get("flip_kpi")]
    return {
        "kind": "condition",
        "unit": "",
        "columns": columns,
        "flip_kpi": flip[0] if flip else "",
        "note": (f"판정을 바꾸는 KPI: {flip[0]}" if flip else "")
                + (f" · {cycle.get('limitation', '')}" if cycle.get("limitation") else ""),
        "basis": str(cycle.get("basis") or ""),
    }


# ─────────────────────────────────────────────────────────────
# 워크스트림별 표지
# ─────────────────────────────────────────────────────────────
def _corp_r(analysis: Dict) -> Dict:
    valuation = analysis.get("valuation") or {}
    screen = valuation.get("screen") or {}
    band = valuation.get("band") or {}

    # 밸류에이션 결론을 못 내는 경우를 **먼저** 본다. 논리가 약하면 `screen()` 이
    # `allow_valuation=False` 로 막아 두었는데, 그 사실을 표지가 감추면 안 된다.
    blocked = bool(valuation.get("blocked"))
    allowed = screen.get("allow_valuation", True)
    label = str(screen.get("verdict") or "판정 유보")
    tone = "serious" if blocked else ("warning" if not allowed else "neutral")
    if not blocked and allowed:
        tone = {"저평가 후보": "good", "프리미엄": "warning",
                "디스카운트 — 이유 있음": "warning"}.get(label, "neutral")

    verdict = _verdict(
        label=label, kind="밸류에이션 스크리닝", tone=tone,
        basis=str(screen.get("basis") or ""),
        reasons=screen.get("reasons"),
        conditions=(screen.get("blockers") or [])
        + ([str(valuation.get("blocked_reason"))] if blocked else [])
        + ([] if allowed else ["근거가 둘에 못 미쳐 밸류에이션 결론을 내지 않는다"]),
        human_decision="사람 승인 필요 — 투자 판단이 아니라 스크리닝 결과다",
        caveat=str(band.get("caveat") or ""))

    metrics = _band_metrics(band)
    quadrant = valuation.get("quadrant") or {}
    if quadrant.get("label"):
        metrics.append(_metric("PER·PBR 사분면", None, str(quadrant["label"]),
                               str(quadrant.get("why") or "")))
    return {"verdict": verdict, "metrics": metrics,
            "scenarios": _price_scenarios(analysis.get("forecast") or {})}


def _corp_tp(analysis: Dict) -> Dict:
    decision = analysis.get("verdict") or {}
    card = analysis.get("scorecard") or {}
    label = str(decision.get("verdict") or "판정 유보")
    verdict = _verdict(
        label=label, kind="Top Pick 판정",
        tone={"Proceed": "good", "Watch": "warning", "Drop": "serious"}.get(label, "neutral"),
        basis=str(decision.get("basis") or ""),
        reasons=decision.get("reasons"), conditions=decision.get("conditions"),
        human_decision=str(decision.get("human_decision") or ""),
        caveat=str(decision.get("note") or ""))

    limits = decision.get("thresholds") or {}
    metrics = [
        _metric("기회지수", decision.get("index"), _num(decision.get("index"), 1),
                f"문턱 Proceed {limits.get('proceed', '—')} · Watch {limits.get('watch', '—')} "
                f"({decision.get('threshold_source', '')})", unit="/5"),
        # ⚠️ 분모는 여섯이 아니라 **다섯**이다. Source confidence 는 기업의 질이 아니라
        #    근거의 충분성이라 지수에서 뺀다 (설계서 §6 규칙 5 · `scorecard.separate_note`).
        #    표에는 여섯 줄이 보이므로 "여섯 중 다섯" 이라고 적으면 사실과 다르다.
        _metric("채점 커버리지", card.get("coverage"),
                f"{_num((card.get('coverage') or 0) * 100, 0)}",
                f"지수에 들어가는 {card.get('quality_dimensions', '—')}차원 중 "
                f"{card.get('scored_count', '—')}개를 채점했다"
                + (f" · 미채점 {', '.join(card.get('unscored') or [])}"
                   if card.get("unscored") else "")
                + f" · {card.get('separate_note', '')}",
                tone="warning" if (card.get("coverage") or 0) < 0.6 else "", unit="%"),
    ]
    red = decision.get("red_team") or {}
    metrics.append(_metric(
        "Red Team", None, f"실패 {red.get('failed', 0)} · 판정불가 {red.get('unknown', 0)}",
        "판정불가를 통과로 세지 않는다",
        tone="serious" if red.get("failed") else ("warning" if red.get("unknown") else "good")))
    metrics += _band_metrics((analysis.get("valuation") or {}).get("band") or {})
    return {"verdict": verdict, "metrics": metrics,
            "scenarios": _price_scenarios(analysis.get("forecast") or {})}


def _ind_r(analysis: Dict) -> Dict:
    cycle = analysis.get("cycle") or {}
    competition = analysis.get("competition") or {}
    market = analysis.get("market") or {}
    label = str(cycle.get("phase") or "국면 판정 유보")
    verdict = _verdict(
        label=label, kind="사이클 국면",
        tone={"확장 국면": "good", "수축 국면": "serious"}.get(label, "warning"),
        basis=str(cycle.get("basis") or ""),
        reasons=[str(cycle.get("vote_summary") or "")]
        + [f"{v.get('source')}: {v.get('signal')} ({v.get('detail')})"
           for v in (cycle.get("votes") or [])],
        conditions=[str(cycle.get("limitation") or "")],
        human_decision="사람 승인 필요 — 국면 판정은 신호 다수결이지 예측이 아니다",
        caveat=str(cycle.get("limitation") or ""))

    metrics = [_metric("신호 신뢰도", None, str(cycle.get("confidence") or "—"),
                       str(cycle.get("vote_summary") or ""),
                       tone={"high": "good", "low": "serious"}.get(
                           str(cycle.get("confidence")), "warning"))]
    if competition.get("available"):
        metrics.append(_metric("집중도 (CR3)", competition.get("cr3_pct"),
                               _num(competition.get("cr3_pct"), 1),
                               f"HHI {_num(competition.get('hhi'), 0)} · "
                               f"{competition.get('level', '')} · "
                               f"구성 {competition.get('member_count', '—')}곳", unit="%"))
    index = analysis.get("index") or {}
    if index.get("values"):
        metrics.append(_metric(
            "업종 지수 (100 기준)", (index.get("values") or [None])[-1],
            _num((index.get("values") or [None])[-1], 1),
            f"구성 {index.get('members_used', '—')}/{index.get('members_total', '—')}곳 "
            f"· 커버리지 {_num(index.get('coverage_pct'), 1)}%", unit=""))
    if _is_num(market.get("listed_market_cap")):
        # ⚠️ 라벨을 `시장 규모` 로 쓰지 않는다. `market.limitation` 이 "**시장 규모가
        #    아니다** — 비상장사가 빠져 있고 시가총액은 매출이 아니라 기대의 크기다" 라고
        #    못박아 두었다. 그 한계를 타일 밑줄에 그대로 싣는다.
        metrics.append(_metric(
            str(market.get("label") or "상장 시가총액 합계"), market.get("listed_market_cap"),
            _num((market.get("listed_market_cap") or 0) / 1e12, 1),
            f"상장 {market.get('listed_count', '—')}곳 · "
            + str(market.get("limitation") or "").replace("**", ""),
            unit="조원"))
    return {"verdict": verdict, "metrics": metrics,
            "scenarios": _condition_scenarios(cycle)}


def _ind_tp(analysis: Dict) -> Dict:
    proposal = analysis.get("proposal") or {}
    top = proposal.get("top_pick") or {}
    sensitivity = analysis.get("sensitivity") or {}
    universe = analysis.get("universe") or {}
    label = str(top.get("name") or "후보 없음")
    verdict = _verdict(
        label=label, kind="총점 1위 후보 (확정 아님)",
        tone={"높음": "good", "낮음": "serious"}.get(str(proposal.get("stability")), "warning"),
        basis="GIC v15 산업TopPick 하네스설계서 ITP-T10",
        reasons=[f"보정 점수 {_num(top.get('adjusted'), 1)} · "
                 f"자료 커버리지 {_num(top.get('coverage'), 0)}%",
                 str(proposal.get("caution") or "")],
        conditions=[str(sensitivity.get("stability_why") or "")],
        human_decision=str(proposal.get("human_decision") or ""),
        caveat="총점 1위를 확정하지 않는다 — 순위는 후보군 안 백분위라 절대 평가가 아니다")

    metrics = [
        _metric("보정 점수", top.get("adjusted"), _num(top.get("adjusted"), 1),
                f"결측 penalty 반영 · 커버리지 {_num(top.get('coverage'), 0)}%", unit="/5"),
        _metric("순위 안정성", None, str(sensitivity.get("stability") or "—"),
                f"시나리오 {sensitivity.get('scenario_count', '—')}회 중 "
                f"1위가 바뀐 것 {sensitivity.get('flip_count', '—')}회",
                tone={"높음": "good", "낮음": "serious"}.get(
                    str(sensitivity.get("stability")), "warning")),
        # ⚠️ `universe.bias` 를 반드시 함께 낸다. 후보군은 **시총 상위로 고른 것**이라
        #    규모 편향이 있고, 점수는 그 후보군 안 백분위라 다른 산업과 견줄 수 없다.
        #    이 두 사실을 빼면 "4.6점" 이 절대 평가처럼 읽힌다 (설계서 ITP-T04).
        _metric("후보 수", universe.get("count"),
                str(universe.get("count") if _is_num(universe.get("count"))
                    else len(analysis.get("ranking") or [])),
                f"{universe.get('method', '')} · 업종 {universe.get('universe_total', '—')}곳 중 "
                f"시총의 {_num((universe.get('universe_cap_share') or 0) * 100, 1)}% 를 덮는다 · "
                + str(universe.get("bias") or "").replace("**", ""),
                unit="곳"),
    ]
    if top.get("tied"):
        metrics.append(_metric("동점", None, "있음", "공동 1위가 있어 하나로 좁히지 않는다",
                               tone="warning"))
    # 조건 시나리오가 없다. **없다고 말한다** — 빈 자리를 만들어 두지 않는다.
    return {"verdict": verdict, "metrics": metrics,
            "scenarios": {"kind": "none", "columns": [],
                          "note": "이 작업은 시나리오를 만들지 않는다 — 후보 순위와 "
                                  "민감도(1위가 언제 바뀌나)가 그 자리를 대신한다"}}


_BUILDERS = {"CORP-R": _corp_r, "CORP-TP": _corp_tp,
             "IND-R": _ind_r, "IND-TP": _ind_tp}


# ─────────────────────────────────────────────────────────────
# H10 9축 → 가로 바
# ─────────────────────────────────────────────────────────────
def axes(evaluation: Dict) -> List[Dict]:
    """평가 9축을 가로 막대로 그릴 수 있게 만든다.

    **축마다 다른 색을 주지 않는다.** 아홉 개 범주형 색을 쓰면 "색이 여덟인데 이야기는
    숫자 하나" 라는 전형적인 오답이 된다 (dataviz 안티패턴 · Form). 채움은 한 색이고
    **상태만** 얹는다. 트랙은 같은 색의 옅은 단계다.

    비율은 여기서 계산해 싣는다 — 렌더러 셋이 각자 나누면 반올림이 갈린다.
    """
    rows = []
    for score in evaluation.get("scores") or []:
        limit = score.get("max") or 0
        value = score.get("score")
        ratio = (value / limit) if (_is_num(value) and limit) else None
        rows.append({
            "axis": str(score.get("axis") or ""),
            "max": limit,
            "score": value,
            "score_text": _num(value, 1),
            "ratio": ratio,
            "pct": round(ratio * 100, 1) if ratio is not None else None,
            "tone": ("good" if ratio is not None and ratio >= GOOD_AT else
                     "warning" if ratio is not None and ratio >= WARN_AT else
                     "serious" if ratio is not None else "neutral"),
            "why": str(score.get("why") or ""),
            # 축 안의 세부 채점 — 어느 조각에서 깎였는지가 여기 있다
            "parts": [{"weight": p.get("weight"), "ratio": p.get("ratio"),
                       "note": str(p.get("note") or ""),
                       "got": (round((p.get("weight") or 0) * (p.get("ratio") or 0), 1))}
                      for p in (score.get("parts") or [])],
        })
    return rows


def build(pack: Dict, analysis: Dict, evaluation: Optional[Dict] = None) -> Dict:
    """표지 한 덩어리. 워크스트림이 달라도 **모양은 하나**다.

    자료가 없어 못 만드는 조각은 빈 채로 두고 그 사실을 `note` 에 적는다.
    조용히 빼면 화면이 "판정이 없다" 를 "판정이 통과다" 로 보이게 만든다 (§2-3).
    """
    charter = pack.get("C0_charter") or {}
    workstream = str(charter.get("workstream_id") or "")
    builder = _BUILDERS.get(workstream)
    target = charter.get("target") or {}

    base = {
        "workstream_id": workstream,
        "target": f"{target.get('name', '')}"
                  + (f" ({target.get('code')})" if target.get("code") else ""),
        "as_of": str(charter.get("as_of") or ""),
        "verdict": {}, "metrics": [], "scenarios": {}, "axes": [],
        "evaluation": {"total": (evaluation or {}).get("total"),
                       "grade": (evaluation or {}).get("grade"),
                       "critical": list((evaluation or {}).get("critical") or [])},
        "note": "",
        # 표지의 모든 숫자는 본문·장부에도 그대로 있다. 표지는 **요약이지 새 값이 아니다.**
        "disclaimer": "표지는 본문에 있는 값을 모아 보인 것이다 — 여기서 새로 만든 수치는 없다. "
                      "모든 판정은 AI 제안이고 사람이 승인한다.",
    }
    if not builder:
        base["note"] = f"{workstream} 의 표지를 만드는 규칙이 아직 없다"
        return base

    try:
        base.update(builder(analysis or {}))
    except Exception as error:            # 표지 하나가 리서치를 죽이지 않는다 (§2-2)
        base["note"] = f"표지를 만들다 실패했다 — {type(error).__name__}: {error}"
        return base

    base["axes"] = axes(evaluation or {})
    if not base["axes"]:
        base["note"] = "평가 9축이 아직 없다 — H10 을 돌리면 채워진다"
    return base
