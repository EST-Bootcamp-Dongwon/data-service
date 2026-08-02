"""H09 Assemble + MD 내보내기 — 최대 15장 고정 양식 (U4 결정)

U4 는 "무엇을 새로 정할까" 가 아니라 **"이미 정해진 것을 어떻게 지킬까"** 였다.
GIC v15 CORP-R 하네스설계서 §6.1 이 15슬롯의 순서를 이미 못박아 두었기 때문이다.
그래서 이 파일은 그 표를 그대로 옮기고, §6.2 의 밀도 조정 규칙을 코드로 만든다.

    §6.1  슬롯 15개와 순서 (아래 SLOTS)
    §6.2  "실제 장수는 먼저 고정하지 않고 핵심 메시지 수와 검증된 자료 밀도로 정한다"
          "자료가 적으면 3+4, 5+7, 8+9, 11+12, 13+14 순으로 합친다"

즉 **15장은 상한이지 목표가 아니다.** 자료가 없는 슬롯을 억지로 한 장씩 만들면
빈 장이 생기고, 그건 §6.2 가 하지 말라고 한 "페이지 수를 채우기 위한 반복 설명" 이다.
그래서 자료가 없으면 정해진 순서대로 합치고, **합쳤다는 사실과 이유를 페이지에 적는다.**

각 장의 필드는 공통계약 §14 의 10개를 그대로 쓴다
(page · title · key_message · body · visual · interpretation · sources · confidence ·
 human_decision · presenter_note).

M5 — 워크스트림마다 양식이 다르다
--------------------------------
| 작업 | 양식 | 근거 |
|---|---|---|
| CORP-R | 고정 15슬롯 | 기업리서치 하네스설계서 §6.1 |
| IND-R | 고정 15슬롯 (**구성이 다르다**) | 산업리서치 하네스설계서 §9 |
| CORP-TP | 자유양식 (권장 6~15장) | 기업TopPick 하네스설계서 §10 |
| IND-TP | 자유양식 (최대 15장) | 산업TopPick 하네스설계서 §10 |

기업 리포트의 '재무 시계열' 자리에 산업 리포트는 '수요 KPI' 가 온다. 슬롯 표를 하나로
합치면 어느 쪽에도 안 맞는 장이 생기므로 **`FORMATS` 로 갈라 두고 `assemble` 이 분기**한다.
합치는 순서(MERGE)와 '자료 없으면 만들지 않는다' 는 원칙은 넷 다 같다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import contracts

# §6.1 기본 15개 슬롯 — 순서를 바꾸지 않는다
SLOTS = [
    {"slot": 1, "title": "Cover", "needs": "제목 · 기준일 · 한 줄 논리 · 학습용 고지"},
    {"slot": 2, "title": "Executive Summary", "needs": "기업 요약 · Red Team 생존 포인트 · 리스크 · 핵심 수치"},
    {"slot": 3, "title": "회사 개요 · 사업모델", "needs": "제품 · 고객 · 수익 · 비용 · 채널"},
    {"slot": 4, "title": "사업부 · 제품 · 지역 구조", "needs": "세그먼트 시계열 · 집중도"},
    {"slot": 5, "title": "가치사슬 · 경쟁우위 · 운영 KPI", "needs": "병목 · 협상력 · 확인 KPI"},
    {"slot": 6, "title": "산업 정의 · 시장 규모 · 성장 · 사이클", "needs": "시장 경계와 산업 매력도"},
    {"slot": 7, "title": "경쟁구조 · 시장 위치", "needs": "점유율 · 역할 · 기업 포지션"},
    {"slot": 8, "title": "재무 시계열", "needs": "성장 · 마진 · 자본효율"},
    {"slot": 9, "title": "이익의 질 · 현금전환 · 안정성", "needs": "정상화 · 운전자본 · FCF · 부채"},
    {"slot": 10, "title": "피어 선정 · 정규화 비교", "needs": "적합도 · 성장 · 마진 · 멀티플"},
    {"slot": 11, "title": "투자포인트", "needs": "근거 → 인과 → 의미 → 조건"},
    {"slot": 12, "title": "리스크 · Red Team · 모니터링", "needs": "공격·방어 · 선행 신호"},
    {"slot": 13, "title": "가치평가 입력 · 가정", "needs": "기준일 · 정상화 · 방법 근거"},
    {"slot": 14, "title": "시나리오 · 가치 범위 · 민감도", "needs": "Bear/Base/Bull · 주당가치"},
    {"slot": 15, "title": "결론 · 향후 이벤트 · KPI · Disclaimer", "needs": "승인된 해석 · 확인 일정 · 고지"},
]

# §6.2 합치는 순서 — 앞의 것부터 적용한다
MERGE_ORDER = [(3, 4), (5, 7), (8, 9), (11, 12), (13, 14)]

# ── IND-R 산업리서치 고정양식 (산업리서치 하네스설계서 §9 표 그대로) ──
# CORP-R 과 **슬롯 구성이 다르다.** 기업 리포트의 '재무 시계열' 자리에 산업 리포트는
# '수요 KPI' 가 온다. 그래서 표를 하나로 합치지 않고 워크스트림별로 나눠 둔다.
IND_R_SLOTS = [
    {"slot": 1, "title": "표지 · 리서치 질문", "needs": "무엇을 왜 분석하는가 · 범위와 기준일"},
    {"slot": 2, "title": "산업 경계 · Ontology", "needs": "포함·제외가 결론에 미치는 영향"},
    {"slot": 3, "title": "밸류체인 · 이익풀 · 병목", "needs": "이익풀 후보와 반대 근거"},
    {"slot": 4, "title": "시장 규모 (실적)", "needs": "범위 · 단위 · 기간"},
    {"slot": 5, "title": "성장 조건 · TAM/SAM/SOM", "needs": "전망 가정과 둔화 조건"},
    {"slot": 6, "title": "수요 구조 · 수요 KPI", "needs": "구조 · 경기 · 계절 구분"},
    {"slot": 7, "title": "공급 역량 · 병목", "needs": "명목·유효 역량과 시차"},
    {"slot": 8, "title": "가격 · 수익화 · 마진 전달", "needs": "계약 · 믹스 · 전가 경로"},
    {"slot": 9, "title": "경쟁 구도 · 기업 비교", "needs": "사실과 경쟁우위 해석 분리"},
    {"slot": 10, "title": "산업 매력도 · Porter 5 Forces", "needs": "찬성 · 반대 근거"},
    {"slot": 11, "title": "정책 · 규제 영향 경로", "needs": "초안·확정, 발표·시행 분리"},
    {"slot": 12, "title": "매크로 · 이벤트 시차", "needs": "수혜 · 피해 조건"},
    {"slot": 13, "title": "산업 사이클 국면", "needs": "근거 · 반대 신호 · 신뢰도"},
    {"slot": 14, "title": "6/12/24개월 시나리오", "needs": "판정을 바꾸는 KPI"},
    {"slot": 15, "title": "결론 · Gap · IND-TP 질문", "needs": "핵심 해석 · 반대 근거 · 사람 승인"},
]
# 설계서 §9 — "인접 슬롯을 MERGE 하거나 근거 없는 슬롯을 OMIT"
IND_R_MERGE_ORDER = [(4, 5), (6, 7), (7, 8), (9, 10), (11, 12), (13, 14)]

# ── CORP-TP · IND-TP 는 **자유양식**이다 (각 설계서 §10) ──
# 순서는 자유지만 논리 순서(질문 → 확인 사실 → 점수·반대 근거 → Red Team → 조건부 판정 →
# handoff)가 드러나야 한다. 그래서 '슬롯' 이 아니라 '섹션' 으로 두고 자료가 있는 것만 낸다.
CORP_TP_SECTIONS = [
    {"slot": 1, "title": "표지 · 스크리닝 질문", "needs": "후보 식별 · 기준일 · 관심 이유"},
    {"slot": 2, "title": "한 줄 제안 · 사람 승인 상태", "needs": "P/W/D AI 제안과 승인란"},
    {"slot": 3, "title": "사업모델 · 왜 볼 만한가", "needs": "제품 · 고객 · KPI"},
    {"slot": 4, "title": "최근 12개월 이벤트", "needs": "구조 · 일회성 · 예정 분류"},
    {"slot": 5, "title": "재무 Quick Data", "needs": "성장 · 마진 · 현금전환"},
    {"slot": 6, "title": "Valuation burden", "needs": "비교 적합성 · 프리미엄/디스카운트"},
    {"slot": 7, "title": "Catalyst · Risk · 모니터링", "needs": "조건 · 시점 · 반전"},
    {"slot": 8, "title": "Quick Score 6차원", "needs": "원점수 · 방향 · Unscored · confidence"},
    {"slot": 9, "title": "Red Team · 반전 조건", "needs": "공격 질문 · 방어 상태"},
    {"slot": 10, "title": "P/W/D 시나리오와 조건", "needs": "진입 · 보류 · 폐기 · 재검토일"},
    {"slot": 11, "title": "CORP-R Handoff · 고지", "needs": "전달 범위 · 미해결 질문 · Disclaimer"},
]
CORP_TP_MERGE_ORDER = [(3, 4), (5, 6), (7, 8), (9, 10)]

IND_TP_SECTIONS = [
    {"slot": 1, "title": "표지 · Selection Brief", "needs": "산업 · 기준일 · Top Pick 질문"},
    {"slot": 2, "title": "IND-R 맥락 · 산업→실적 경로", "needs": "산업 변화가 후보 실적에 닿는 경로"},
    {"slot": 3, "title": "후보 Universe · 포함·제외", "needs": "후보군 완전성과 대표성"},
    {"slot": 4, "title": "정량 비교표", "needs": "성장 · 수익 · 밸류 · 모멘텀"},
    {"slot": 5, "title": "후보별 Red Team", "needs": "같은 질문 · 반대 증거"},
    {"slot": 6, "title": "점수 · coverage · missing penalty", "needs": "계산 재현"},
    {"slot": 7, "title": "민감도 · 순위 안정성", "needs": "뒤집히는 조건"},
    {"slot": 8, "title": "잠정 Top Pick · 대안 후보", "needs": "모델 제안 / 사람 결정 분리"},
    {"slot": 9, "title": "Gap · 다음 확인 KPI · 고지", "needs": "CORP-TP 재검증 질문 · Disclaimer"},
]
IND_TP_MERGE_ORDER = [(3, 4), (5, 6), (6, 7)]

FORMATS = {
    "CORP-R": {"slots": SLOTS, "merge": MERGE_ORDER, "kind": "고정양식",
               "policy_note": "GIC v15 CORP-R 하네스설계서 §6.1 순서 · §6.2 밀도 규칙"},
    "IND-R": {"slots": IND_R_SLOTS, "merge": IND_R_MERGE_ORDER, "kind": "고정양식",
              "policy_note": "GIC v15 산업리서치 하네스설계서 §9 고정 순서 · MERGE/OMIT"},
    "CORP-TP": {"slots": CORP_TP_SECTIONS, "merge": CORP_TP_MERGE_ORDER, "kind": "자유양식",
                "policy_note": "GIC v15 기업TopPick 하네스설계서 §10 (권장 6~15장)"},
    "IND-TP": {"slots": IND_TP_SECTIONS, "merge": IND_TP_MERGE_ORDER, "kind": "자유양식",
               "policy_note": "GIC v15 산업TopPick 하네스설계서 §10 (최대 15장)"},
}

DISCLAIMER = ("본 보고서는 GIC 학회 내부 학습 목적으로 작성되며 투자 권유가 아닙니다. "
              "투자 판단과 책임은 투자자 본인에게 있습니다.")


def _page(slot: Dict, key_message: str, body: List[str], visual: str = "",
          interpretation: Optional[Dict] = None, sources: Optional[List[str]] = None,
          confidence: str = "medium", human_decision: str = "AI 제안 — 사람 승인 전",
          presenter_note: str = "", merged_from: Optional[List[int]] = None,
          gaps: Optional[List[str]] = None) -> Dict:
    """공통계약 §14 의 페이지 필드 10개를 채운다."""
    return {
        "page": 0,                                   # 번호는 마지막에 매긴다
        "slot": slot["slot"],
        "title": slot["title"],
        "key_message": key_message,
        "body": body,
        "visual": visual,
        "interpretation": interpretation or {},
        "sources": sources or [],
        "confidence": confidence,
        "human_decision": human_decision,
        "presenter_note": presenter_note,
        "merged_from": merged_from or [],
        "gaps": gaps or [],
    }


def _trillion(value, digits: int = 1) -> str:
    """원 단위 금액 → '12.3조'. 값이 없으면 **0 으로 채우지 않고** 물결표를 쓴다.

    ⚠️ `row.get("revenue", 0)` 로는 못 막는다 — 키는 있는데 값이 `None` 인 경우가 있다.
    금융업이 그렇다: 은행·지주는 '매출액' 계정이 없고 '영업수익' 을 쓴다
    (실측 — 신한지주 CORP-TP 가 `None / 1e12` 로 500 이 났다).
    """
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value / 1e12:,.{digits}f}조"


def _percent(value, digits: int = 1) -> str:
    """부호를 붙인 퍼센트. 값이 없으면 물결표."""
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:+.{digits}f}%"


def assemble(pack: Dict, analysis: Dict) -> Dict:
    """Context Pack + H04 분석 결과 → 최대 15장 페이지 계약.

    **워크스트림마다 양식이 다르다.** CORP-R·IND-R 은 순서가 못박힌 고정양식이고,
    TP 두 개는 자유양식이다 (각 하네스설계서 §6·§9·§10). 여기서 갈라 부른다.
    """
    workstream = pack.get("C0_charter", {}).get("workstream_id", "CORP-R")
    if workstream == "IND-R":
        return _assemble_ind_r(pack, analysis)
    if workstream == "CORP-TP":
        return _assemble_corp_tp(pack, analysis)
    if workstream == "IND-TP":
        return _assemble_ind_tp(pack, analysis)
    return _assemble_corp_r(pack, analysis)


def _assemble_corp_r(pack: Dict, analysis: Dict) -> Dict:
    """CORP-R 기업리서치 15슬롯 (하네스설계서 §6.1).

    자료가 없는 슬롯은 **버리지 않고 합친다.** 합친 사실은 `merged_from` 에 남아
    리포트 하단의 '왜 12장인가' 설명이 된다.
    """
    charter = pack.get("C0_charter", {})
    target = charter.get("target", {})
    name = target.get("name") or target.get("code") or "대상"
    as_of = charter.get("as_of", "")
    cards = {c.get("id"): c for c in pack.get("C5_interpretation", [])}

    financial = analysis.get("financial", {})
    peers = analysis.get("peers", {})
    valuation_view = analysis.get("valuation", {})
    forecast = analysis.get("forecast", {})
    facts = analysis.get("report_facts", {})
    red = analysis.get("red_team", {})
    ratios = financial.get("latest_ratios", {})

    filled: Dict[int, Dict] = {}

    # slot 1 Cover
    band = valuation_view.get("band", {})
    headline = (f"{name} — {valuation_view.get('screen', {}).get('verdict', '판정 유보')}"
                if valuation_view else f"{name} 기업 리서치")
    filled[1] = _page(SLOTS[0], headline, [
        f"분석 기준일 {as_of}",
        (f"주당 가치 범위 {band['low']:,}~{band['high']:,}원 (기준 {band['base']:,}원)"
         if band.get("available") else "주당 가치 범위 — 산출 조건을 못 채웠다"),
        DISCLAIMER,
    ], confidence="medium", presenter_note="학습용 자료임을 먼저 말한다")

    # slot 2 Executive Summary — Red Team 을 통과한 것만 올린다
    survived = [c["finding"] for c in red.get("checks", []) if c.get("verdict") == "통과"]
    risks = [c["counter_evidence"] for c in red.get("checks", [])
             if c.get("verdict") == "실패" and c.get("counter_evidence")]
    filled[2] = _page(SLOTS[1], f"{name} 요약", [
        f"Red Team 을 통과한 관찰 {len(survived)}건: " + (" / ".join(survived[:3]) or "없음"),
        f"반증에 걸린 주장 {len(risks)}건: " + (" / ".join(risks[:2]) or "없음"),
        red.get("summary", ""),
    ], confidence="medium",
        presenter_note="통과한 것과 걸린 것을 반드시 함께 말한다 (AI 제안 상태)")

    # slot 3 회사 개요 · 사업모델
    if facts.get("available") and facts.get("business_summary"):
        filled[3] = _page(SLOTS[2], "사업의 내용", [facts["business_summary"]],
                          sources=[f"{facts.get('report_name')} ({facts.get('rcept_date')})"],
                          confidence="high")

    # slot 4 사업부 구조
    segments = (facts or {}).get("segments", {})
    if segments.get("found"):
        rows = segments["rows"]
        filled[4] = _page(SLOTS[3], f"부문 {len(rows)}개로 나뉜다",
                          [f"{r['segment']}: {r['values'][0]:,.0f}" for r in rows[:6]],
                          visual="부문별 매출 막대",
                          interpretation=cards.get(_card_id(pack, "segment"), {}),
                          sources=[f"{facts.get('report_name')} · 표 "
                                   f"{' / '.join(segments.get('header', [])[:4])}"],
                          confidence="high")

    # slot 5 운영 KPI (생산능력 · 연구개발)
    operational = []
    for key, label in (("capacity", "생산능력·가동률"), ("rnd", "연구개발")):
        block = (facts or {}).get(key, {})
        if block.get("found"):
            table = block["tables"][0]
            operational.append(f"{label}: " + " · ".join(
                f"{r['label']} {r['values'][0]:,.0f}" for r in table["rows"][:3]))
    if operational:
        filled[5] = _page(SLOTS[4], "운영 지표", operational,
                          sources=[facts.get("report_name", "")], confidence="medium")

    # slot 7 경쟁구조 — 점유율이 있으면 진짜 점유율, 없으면 피어 안 순위
    share = (facts or {}).get("market_share", {})
    if share.get("found"):
        table = share["tables"][0]
        filled[7] = _page(SLOTS[6], "시장점유율",
                          [f"{r['label']} {r['values'][0]:.1f}%" for r in table["rows"][:6]],
                          visual="점유율 추이 선그래프",
                          sources=[facts.get("report_name", "")], confidence="high")
    elif peers.get("available"):
        rank = _cap_rank(peers)
        filled[7] = _page(SLOTS[6], "피어 안에서의 위치", [
            f"시가총액 기준 {rank['rank']}위 / {rank['total']}곳",
            "공개 자료에 시장점유율이 없어 **피어 안 순위**로 대신한다",
        ], confidence="low",
            gaps=["G-DATA — 시장점유율 원자료 없음. 피어 순위는 점유율이 아니다"])

    # slot 8 재무 시계열
    series = financial.get("series", [])
    if series:
        growth = financial.get("growth", {})
        filled[8] = _page(SLOTS[7],
                          f"매출 CAGR {_percent(growth.get('cagr'))} · {growth.get('trend', '')}"
                          if growth.get("available") else "재무 시계열",
                          [f"{row['year']}: 매출 {_trillion(row.get('revenue'))} · "
                           f"영업이익 {_trillion(row.get('operating_income'))}"
                           for row in series],
                          visual="매출·영업이익 시계열",
                          interpretation=cards.get(_card_id(pack, "financial"), {}),
                          sources=[f"DART 사업보고서 {series[0]['year']}~{series[-1]['year']}"],
                          confidence="high")

    # slot 9 이익의 질 · 안정성
    quality = ratios.get("earnings_quality", {})
    stability = [ratios.get(key, {}) for key in ("debt_ratio", "current_ratio")]
    if quality.get("available") or any(s.get("value") is not None for s in stability):
        filled[9] = _page(SLOTS[8], quality.get("why", "재무 안정성"),
                          [f"{s['label']} {s['value']}{s['unit']} — {s['why']}"
                           for s in stability if s.get("value") is not None]
                          + ([f"영업현금흐름/영업이익 {quality['cash_conversion']:.2f}"]
                             if quality.get("available") else []),
                          confidence="high")

    # slot 10 피어 비교
    if peers.get("available"):
        position = valuation_view.get("per_position", {})
        filled[10] = _page(SLOTS[9],
                           f"{position.get('stance', '피어 비교')} — {peers.get('method')}",
                           [f"{r['name']} · PER {r.get('per') or '—'} · PBR {r.get('pbr') or '—'}"
                            for r in peers.get("table", {}).get("rows", [])[:8]],
                           visual="피어 PER·PBR 산점도",
                           interpretation=cards.get(_card_id(pack, "peer"), {}),
                           sources=[f"시장 스냅샷 {peers.get('table', {}).get('as_of', '')}"],
                           confidence="medium",
                           gaps=peers.get("dropped", []))

    # slot 11 투자포인트 · slot 12 Red Team
    points = analysis.get("investment_points", [])
    if points:
        filled[11] = _page(SLOTS[10], f"투자포인트 {len(points)}개",
                           [f"{p['claim']} — {p['because']}" for p in points],
                           confidence="medium",
                           presenter_note="Red Team 을 통과한 것만 올렸다")
    if red.get("checks"):
        filled[12] = _page(SLOTS[11], red.get("summary", "Red Team"),
                           [f"[{c['verdict']}] {c['check']} — {c['finding']}"
                            for c in red["checks"]],
                           confidence="high",
                           presenter_note="판정불가는 통과가 아니다")

    # slot 13 · 14 밸류에이션
    if valuation_view:
        choice = valuation_view.get("multiple_choice", {})
        filled[13] = _page(SLOTS[12], f"{choice.get('multiple')} 로 본다",
                           [f"선택 경로: {' → '.join(choice.get('path', []))}",
                            choice.get("why", ""),
                            f"업종 기준: {valuation_view.get('sector_guide', {}).get('why', '')}"],
                           confidence="medium")
    if band.get("available") or forecast.get("scenarios"):
        body = []
        if band.get("available"):
            body.append(f"피어 PER {band['peer_median_per']} × EPS {band['eps']:,} → "
                        f"{band['low']:,}~{band['high']:,}원")
            body.append(band["caveat"])
        for scenario in forecast.get("scenarios", []):
            body.append(f"{scenario['name']}: 목표 {scenario.get('target')} · "
                        f"확률 {scenario.get('prob', 0):.0%} · {scenario.get('trigger', '')}")
        filled[14] = _page(SLOTS[13], "가치 범위와 시나리오", body,
                           visual="시나리오 팬차트",
                           interpretation=cards.get(_card_id(pack, "timeseries"), {}),
                           confidence="low" if valuation_view.get("blocked") else "medium",
                           gaps=([valuation_view["blocked_reason"]]
                                 if valuation_view.get("blocked") else []))

    # slot 15 결론
    filled[15] = _page(SLOTS[14], "결론과 다음 확인", [
        (valuation_view.get("blocked_reason")
         or f"현 시점 판정: {valuation_view.get('screen', {}).get('verdict', '유보')}"),
        f"확인할 것 {len(red.get('checks', []))}건은 12장에 있다",
        DISCLAIMER,
    ], confidence="medium", human_decision="사람 승인 필요 — H08 에서 확인한다")

    return _finalize(filled, pack, "CORP-R")


# ─────────────────────────────────────────────────────────────
# IND-R 산업리서치 (하네스설계서 §9 고정양식)
# ─────────────────────────────────────────────────────────────
def _assemble_ind_r(pack: Dict, analysis: Dict) -> Dict:
    charter = pack.get("C0_charter", {})
    as_of = charter.get("as_of", "")
    cards = pack.get("C5_interpretation", [])
    slots = {s["slot"]: s for s in IND_R_SLOTS}

    target = analysis.get("target", {})
    chain = analysis.get("value_chain", {})
    market = analysis.get("market", {})
    production = (market or {}).get("production", {})
    index_row = analysis.get("index", {})
    flow = analysis.get("supply_demand", {})
    rivalry = analysis.get("competition", {})
    phase = analysis.get("cycle", {})
    red = analysis.get("red_team", {})
    name = target.get("name") or charter.get("target", {}).get("code") or "산업"

    filled: Dict[int, Dict] = {}

    filled[1] = _page(slots[1], f"{target.get('industry_code', '')} {name} 산업 리서치", [
        f"분석 기준일 {as_of} · 상장사 {analysis.get('member_count', 0)}곳",
        target.get("note", ""),
        DISCLAIMER,
    ], presenter_note="Top Pick 을 고르지 않는다 — 산업 구조까지가 이 리포트의 경계다")

    filled[2] = _page(slots[2], f"업종 {target.get('industry_code')} ({target.get('level')})", [
        f"대분류 {target.get('section', {}).get('letter', '')} "
        f"{target.get('section', {}).get('name', '')}",
        target.get("note", ""),
        target.get("limitation", ""),
    ], confidence="high", sources=["DART 표준산업분류(KSIC)"])

    if chain.get("available"):
        position = chain.get("position") or {}
        body = [f"같은 중분류 안 인접 소분류 {chain.get('adjacent_count', 0)}개: "
                + " · ".join(f"{a['industry_code']} {a['name']}"
                             for a in chain.get("adjacent", [])[:5])]
        if position.get("available"):
            body.append(f"밸류체인 위치 추정: {position['estimated']} ({position['score']:.2f}) "
                        f"— {position['limitation']}")
        elif position.get("reason"):
            body.append(f"밸류체인 위치: {position['reason']}")
        body.append("이익풀·병목: " + chain["profit_pool"]["reason"])
        filled[3] = _page(slots[3], "밸류체인 위치와 인접 업종", body,
                          visual="인접 업종 구성 막대",
                          interpretation=_card_by_question(cards, "이 산업의 경계와 크기는?"),
                          confidence="low",
                          gaps=[f"{chain['profit_pool']['gap_code']} — {chain['profit_pool']['reason']}",
                                f"{chain['bottleneck']['gap_code']} — {chain['bottleneck']['reason']}"])

    if market.get("available"):
        filled[4] = _page(slots[4], f"{market['label']} {_trillion(market.get('listed_market_cap'))}", [
            f"상장사 {market['listed_count']}곳 · 기준일 {market.get('as_of')}",
            (f"{production['series_name']} 생산지수 {production['latest']} "
             f"({production.get('latest_period')}) · YoY {production.get('yoy_pct')}%"
             if production.get("available") else "생산지수를 받지 못했다"),
            market["limitation"],
        ], visual="구성 종목 시가총액 막대", confidence="medium",
            sources=[f"시장 스냅샷 {market.get('as_of')}"]
                    + ([production["source"]] if production.get("available") else []))

    if production.get("available"):
        check = production["cagr_check"]
        filled[5] = _page(slots[5], f"생산지수 {production['cagr_years']}년 CAGR "
                                    f"{production.get('cagr_pct')}%", [
            f"검산: {check['formula']} · {check['first']}({check['first_period']}) → "
            f"{check['last']}({check['last_period']}) · {check['years']}년",
            production.get("substitute_note") or f"계열 {production['series_name']}",
            production["limitation"],
        ], visual="생산지수 추이 선", confidence="medium",
            sources=[production["source"]],
            gaps=[f"{market['tam_sam_som']['gap_code']} — {market['tam_sam_som']['reason']}"])

    if flow.get("available"):
        demand = [s for s in flow["signals"] if s["kind"] == "수요"]
        if demand:
            filled[6] = _page(slots[6], "수요 신호",
                              [f"{s['name']} {s['value']} · YoY {s.get('yoy_pct')}% — {s['direction']}"
                               for s in demand] + [
                                  (flow["mismatch"] or {}).get("text", ""),
                                  (flow["mismatch"] or {}).get("caution", "")],
                              visual="생산지수 vs 업종지수", confidence="medium")
        filled[7] = _page(slots[7], "공급 역량 — 확인하지 못했다",
                          [flow["capacity"]["reason"], flow["capacity"]["next_check"]],
                          confidence="low",
                          gaps=[f"{flow['capacity']['gap_code']} — {flow['capacity']['reason']}"])
        filled[8] = _page(slots[8], "가격 — 확인하지 못했다",
                          [flow["price"]["reason"], flow["price"]["next_check"]],
                          confidence="low",
                          gaps=[f"{flow['price']['gap_code']} — {flow['price']['reason']}"])

    if rivalry.get("available"):
        filled[9] = _page(slots[9], f"{rivalry['level']} — {rivalry['why']}",
                          # '비중' 을 붙여 쓴다 — 점유율이 아니라는 이 장의 요지와도 맞고,
                          # 화면이 이 수치를 장부의 D- 와 이을 때 지표 이름이 같은 줄에
                          # 있어야 한다는 규칙(§7.4)도 여기서 지켜진다.
                          [f"{r['name']} 시가총액 비중 {r['share_pct']}%"
                           for r in rivalry["top"][:6]]
                          + [f"CR3 {rivalry['cr3_pct']}%", rivalry["limitation"]],
                          visual="시가총액 비중 막대", confidence="medium",
                          gaps=["G-DATA — 시장점유율 원자료 없음. 시총 비중은 점유율이 아니다"])
        porter = rivalry.get("porter", {})
        available_forces = [k for k, v in porter.items()
                            if isinstance(v, dict) and v.get("available")]
        filled[10] = _page(slots[10], f"Porter 5 Forces — 근거 있는 항목 {len(available_forces)}개",
                           [f"{key}: {value['finding']}" if value.get("available")
                            else f"{key}: {value.get('reason', '')}"
                            for key, value in porter.items() if isinstance(value, dict)],
                           confidence="low", presenter_note=porter.get("note", ""))

    if phase.get("available"):
        economy = phase.get("economy", {})
        sensitivity = phase.get("sensitivity", {})
        if sensitivity.get("available"):
            filled[12] = _page(slots[12], f"거시 민감도 — {sensitivity['name']}", [
                sensitivity["rate"]["label"], sensitivity["fx"]["label"],
                sensitivity["inflation"]["label"], sensitivity["why"],
                sensitivity["limitation"],
            ], confidence="low", sources=["08강 02.md · 03.md"])
        filled[13] = _page(slots[13], f"{phase['phase']} — {phase.get('vote_summary')}",
                           [f"{v['source']}: {v['signal']} ({v['detail']})"
                            for v in phase.get("votes", [])]
                           + [economy.get("divergence") or "", phase["limitation"]],
                           visual="경기지수 · 생산지수 · 업종지수 3단",
                           interpretation=_card_by_question(cards, "지금 이 산업은 어느 국면인가?"),
                           confidence=phase.get("confidence", "low"))
        scenario_lines = []
        for scenario in phase.get("scenarios", []):
            scenario_lines.append(f"[{scenario['horizon_months']}개월] "
                                  f"Bull — {scenario['bull']['condition']}")
            scenario_lines.append(f"[{scenario['horizon_months']}개월] "
                                  f"Bear — {scenario['bear']['condition']}")
        filled[14] = _page(slots[14], "6/12/24개월 조건", scenario_lines + [
            "판정을 바꾸는 KPI: " + (phase.get("scenarios") or [{}])[0].get("flip_kpi", ""),
            phase["limitation"],
        ], confidence="low")

    unresolved = analysis.get("gaps", [])
    filled[15] = _page(slots[15], "결론과 다음 확인", [
        f"{name} 은 {phase.get('phase', '국면 판정 유보')} 로 읽힌다 "
        f"(신뢰도 {phase.get('confidence', 'low')})",
        f"미해결 Gap {len(unresolved)}건 — " + " / ".join(
            f"{g['code']} {g['affected']}" for g in unresolved[:4]),
        "IND-TP 로 넘길 질문: 이 산업에서 조건이 맞는 후보는 누구인가",
        DISCLAIMER,
    ], confidence="medium", human_decision="사람 승인 필요 — H08 에서 확인한다",
        presenter_note="산업리서치는 1위 기업을 확정하지 않는다 (설계서 ERR-TOP-PICK-BOUNDARY)")

    return _finalize(filled, pack, "IND-R")


# ─────────────────────────────────────────────────────────────
# CORP-TP 기업 Top Pick (하네스설계서 §10 자유양식)
# ─────────────────────────────────────────────────────────────
def _assemble_corp_tp(pack: Dict, analysis: Dict) -> Dict:
    charter = pack.get("C0_charter", {})
    target = charter.get("target", {})
    name = target.get("name") or target.get("code") or "대상"
    as_of = charter.get("as_of", "")
    cards = pack.get("C5_interpretation", [])
    slots = {s["slot"]: s for s in CORP_TP_SECTIONS}

    events = analysis.get("events", {})
    card_deck = analysis.get("scorecard", {})
    decision = analysis.get("verdict", {})
    financial = analysis.get("financial", {})
    view = analysis.get("valuation", {})
    facts = analysis.get("report_facts", {})
    red = analysis.get("red_team", {})
    ratios = financial.get("latest_ratios", {})

    filled: Dict[int, Dict] = {}

    filled[1] = _page(slots[1], f"{name} 기업 Top Pick 스크리닝", [
        f"분석 기준일 {as_of} · 종목 {target.get('code')}",
        f"이벤트 창 {events.get('window', {}).get('window_start_boundary', '')} 초과 ~ "
        f"{events.get('window', {}).get('window_end', '')} 이하",
        DISCLAIMER,
    ], presenter_note="정식 리서치가 아니라 '깊이 볼 가치가 있는가' 를 가리는 단계다")

    filled[2] = _page(slots[2], f"AI 제안: {decision.get('verdict', '판정 유보')}",
                      list(decision.get("reasons", [])) + [
                          f"기회지수 {decision.get('index')} / 문턱 "
                          f"{decision.get('thresholds', {}).get('proceed')} "
                          f"({decision.get('threshold_source', '')})",
                          decision.get("human_decision", ""),
                      ], confidence="medium",
                      human_decision="사람 승인 필요 — AI 는 제안만 한다")

    if facts.get("available") and (facts.get("segments") or {}).get("found"):
        rows = facts["segments"]["rows"]
        filled[3] = _page(slots[3], f"부문 {len(rows)}개",
                          [f"{r['segment']}: {r['values'][0]:,.0f}" for r in rows[:6]],
                          sources=[facts.get("report_name", "")], confidence="high")

    if events.get("available"):
        counts = events.get("counts", {})
        body = [f"구조 {counts.get('구조', 0)} · 일회성 {counts.get('일회성', 0)} · "
                f"예정 {counts.get('예정', 0)} (총 {events['total']}건)"]
        body += [f"[{e['kind']}] {e['date']} {e['title']}"
                 + (f" · 감성 {e['sentiment']['label']}" if e.get("sentiment") else "")
                 for e in events.get("structural", [])[:5]]
        body.append(events.get("note", ""))
        filled[4] = _page(slots[4], "최근 12개월 사건", body,
                          visual="이벤트 타임라인",
                          interpretation=_card_by_question(cards, "최근 12개월에 무슨 일이 있었나?"),
                          confidence="high", sources=["DART 공시목록"])

    series = financial.get("series", [])
    if series:
        growth = financial.get("growth", {})
        quality = ratios.get("earnings_quality", {})
        filled[5] = _page(slots[5],
                          f"매출 CAGR {_percent(growth.get('cagr'))}" if growth.get("available")
                          else "재무 Quick Data",
                          [f"{row['year']}: 매출 {_trillion(row.get('revenue'))} · "
                           f"영업이익 {_trillion(row.get('operating_income'))}"
                           for row in series[-3:]]
                          + ([f"영업현금흐름/영업이익 {quality['cash_conversion']:.2f} — "
                              f"{quality['why']}"] if quality.get("available") else []),
                          confidence="high", sources=["DART 재무제표"])

    position = (view or {}).get("per_position", {})
    if position.get("available"):
        filled[6] = _page(slots[6], f"{position['stance']} — 피어 대비 {position['premium_pct']:+.1f}%",
                          [f"PER {position['value']} · 피어 중앙값 {position['peer_median']} "
                           f"({position['peer_count']}곳)",
                           (view.get("quadrant") or {}).get("meaning", ""),
                           "**부담 점수는 높을수록 불리하다. 미확인을 저평가로 읽지 않는다.**"],
                          visual="피어 PER 분포", confidence="medium")

    catalyst = next((r for r in card_deck.get("rows", []) if r["key"] == "catalyst"), {})
    risk = next((r for r in card_deck.get("rows", []) if r["key"] == "risk"), {})
    if catalyst or risk:
        filled[7] = _page(slots[7], "촉매와 위험",
                          [f"Catalyst {catalyst.get('score', 'Unscored')}: "
                           + " / ".join(catalyst.get("reasons", []) or ["—"]),
                           f"Risk {risk.get('score', 'Unscored')}: "
                           + " / ".join(risk.get("reasons", []) or ["—"]),
                           "확률을 만들지 않았다 — 실현 조건과 확인 일정만 적는다"],
                          confidence="medium")

    if card_deck.get("rows"):
        filled[8] = _page(slots[8], f"기회지수 {card_deck.get('index')}/5",
                          [f"{r['name']} {r['score'] if r['score'] is not None else 'Unscored'} "
                           f"({r['direction_label']}) — " + (" / ".join(r["reasons"][:2]) or "—")
                           for r in card_deck["rows"]]
                          + [card_deck.get("index_formula", ""), card_deck.get("index_note", ""),
                             card_deck.get("separate_note", "")],
                          visual="6차원 레이더 (기회/부담 분리)",
                          interpretation=_card_by_question(cards, "이 회사를 정식으로 볼 가치가 있나?"),
                          confidence="medium",
                          gaps=([f"미채점 {', '.join(card_deck['unscored'])} — Unscored 는 0점이 아니다"]
                                if card_deck.get("unscored") else []))

    if red.get("checks"):
        filled[9] = _page(slots[9], red.get("summary", "Red Team"),
                          [f"[{c['verdict']}] {c['check']} — {c['finding']}" for c in red["checks"]],
                          confidence="high", presenter_note="판정불가는 통과가 아니다")

    filled[10] = _page(slots[10], f"{decision.get('verdict', '판정 유보')} 조건",
                       list(decision.get("conditions", [])) + [
                           f"문턱: Proceed ≥ {decision.get('thresholds', {}).get('proceed')} · "
                           f"Watch ≥ {decision.get('thresholds', {}).get('watch')}",
                           decision.get("note", ""),
                       ], confidence="medium",
                       human_decision="사람 승인 필요 — 재검토일을 함께 정한다")

    filled[11] = _page(slots[11], "CORP-R Handoff", [
        f"넘길 것: {name}의 12개월 사건 {events.get('total', 0)}건 · "
        f"Quick Score {card_deck.get('index')}/5",
        "미해결 질문: " + (" / ".join(decision.get("conditions", [])[:2]) or "없음"),
        DISCLAIMER,
    ], confidence="medium", human_decision="사람 승인 후 CORP-R 로 넘긴다")

    return _finalize(filled, pack, "CORP-TP")


# ─────────────────────────────────────────────────────────────
# IND-TP 산업 Top Pick (하네스설계서 §10 자유양식)
# ─────────────────────────────────────────────────────────────
def _assemble_ind_tp(pack: Dict, analysis: Dict) -> Dict:
    charter = pack.get("C0_charter", {})
    as_of = charter.get("as_of", "")
    cards = pack.get("C5_interpretation", [])
    slots = {s["slot"]: s for s in IND_TP_SECTIONS}

    industry = analysis.get("industry", {})
    ranking_rows = [r for r in analysis.get("ranking", []) if r.get("rank")]
    ranking_rows.sort(key=lambda r: r["rank"])
    universe = analysis.get("universe", {})
    swings = analysis.get("sensitivity", {})
    attacks = (analysis.get("red_team") or {}).get("per_candidate", {})
    proposal = analysis.get("proposal", {})
    name = industry.get("name") or charter.get("target", {}).get("code") or "산업"

    filled: Dict[int, Dict] = {}

    filled[1] = _page(slots[1], f"{industry.get('industry_code', '')} {name} Top Pick", [
        f"분석 기준일 {as_of} · 후보 {universe.get('count', 0)}곳 "
        f"(업종 상장사 {universe.get('universe_total', 0)}곳)",
        universe.get("method", ""),
        DISCLAIMER,
    ], presenter_note="모델 제안과 사람 결정을 갈라 말한다")

    filled[2] = _page(slots[2], "산업 → 후보 실적 경로", [
        industry.get("note", ""),
        "산업 변화가 후보 실적에 닿는 경로는 IND-R 에서 넘어온 맥락이다",
        "이 리포트는 **후보 간 상대 비교**만 한다 — 산업 자체의 매력도는 IND-R 이 다룬다",
    ], confidence="low")

    if universe.get("available"):
        filled[3] = _page(slots[3], f"후보 {universe['count']}곳 · 업종 시총의 "
                                    f"{universe.get('universe_cap_share', 0):.1%}",
                          [f"{r['name']} ({r['code']})" for r in universe.get("rows", [])]
                          + [universe.get("bias", ""), universe.get("range_note", "")],
                          confidence="medium",
                          gaps=[f"{w['code']} — {w['message']}"
                                for w in universe.get("warnings", [])])

    if ranking_rows:
        filled[4] = _page(slots[4], "정량 비교",
                          [f"{r['rank']}위 {r['name']} · adjusted {r.get('adjusted_display')} "
                           f"· observed {r.get('observed_display')} "
                           f"· coverage {r.get('coverage_display')}% "
                           f"· 신뢰 {r.get('confidence')}"
                           + ("  (공동 순위)" if r.get("tied") else "")
                           for r in ranking_rows],
                          visual="후보별 점수 막대 (coverage 표기)",
                          interpretation=_card_by_question(cards, "이 산업에서 먼저 볼 후보는?"),
                          confidence="medium")

    if attacks:
        body = []
        for code, checks in list(attacks.items())[:6]:
            row = next((r for r in ranking_rows if r["code"] == code), {})
            body.append(f"{row.get('name', code)}: "
                        + " / ".join(f"[{c['verdict']}] {c['question']}" for c in checks))
        filled[5] = _page(slots[5], f"후보별 공격 질문 {len(next(iter(attacks.values()), []))}개씩",
                          body + [(analysis.get("red_team") or {}).get("note", ""),
                                  (analysis.get("red_team") or {}).get("limitation", "")],
                          confidence="medium")

    if ranking_rows:
        top = ranking_rows[0]
        filled[6] = _page(slots[6], "점수 · coverage · missing penalty", [
            top.get("formula", ""),
            f"기본 penalty 계수 {top.get('penalty_coefficient')}",
            *[f"{r['name']}: observed {r.get('observed_display')} - penalty "
              f"{round(r.get('missing_penalty', 0), 2)} = adjusted {r.get('adjusted_display')}"
              f"  (결측 {', '.join(r.get('missing', [])) or '없음'})"
              for r in ranking_rows[:6]],
        ], confidence="high", presenter_note="반올림 전 값을 계산 원장에 보존했다")

    if swings.get("available"):
        filled[7] = _page(slots[7], f"순위 안정성 {swings['stability']} — {swings['stability_why']}", [
            f"시나리오 {swings['scenario_count']}개 (가중치 ±{swings['rules']['weight_delta']} · "
            f"{swings['rules']['score_delta']} · penalty {swings['rules']['penalty']})",
            f"1위 단독 {swings['solo_ratio']:.0%} · 공동 {swings['shared_ratio']:.0%}",
            f"1·2위 점수 차이 {swings.get('top_margin')}",
            (f"순위를 뒤집는 시나리오 {swings['flip_count']}개: "
             + " / ".join(swings.get("flip_scenarios", [])[:3])
             if swings.get("flip_count") else "순위를 뒤집는 시나리오가 없다"),
            swings["rules"]["ties"],
        ], visual="시나리오별 1위 빈도", confidence="high")

    filled[8] = _page(slots[8], f"모델 제안: {(proposal.get('top_pick') or {}).get('name', '판정 유보')}", [
        (f"adjusted {(proposal.get('top_pick') or {}).get('adjusted')} · "
         f"coverage {(proposal.get('top_pick') or {}).get('coverage')}%"
         if proposal.get("top_pick") else "1위를 정하지 못했다"),
        f"대안 후보: " + " / ".join(f"{r['name']}({r.get('adjusted_display')})"
                                for r in ranking_rows[1:4]),
        proposal.get("caution", ""),
        proposal.get("human_decision", ""),
    ], confidence="medium", human_decision="사람 승인 필요 — 총점 1위를 확정하지 않는다")

    filled[9] = _page(slots[9], "Gap 과 다음 확인", [
        "CORP-TP 로 넘길 재검증 질문: 승인 후보의 실적·현금흐름을 원자료로 확인한다",
        "여기서는 시장 스냅샷만 썼다 — 후보별 DART 재무는 확인하지 않았다",
        DISCLAIMER,
    ], confidence="medium",
        presenter_note="승인 후보를 CORP-TP Proceed 로 그대로 복사하지 않는다 (설계서 ITP-T17)")

    return _finalize(filled, pack, "IND-TP")


def _card_by_question(cards: List[Dict], question: str) -> Dict:
    """해석카드를 '연구 질문' 으로 찾는다 (순서에 기대지 않는다)."""
    return next((c for c in cards if c.get("research_question") == question), {})


def _card_id(pack: Dict, kind: str) -> str:
    """해석카드를 종류로 찾는다 (조립할 때 카드 ID 를 몰라도 되게)."""
    order = {"segment": 0, "financial": 1, "peer": 2, "timeseries": 3}
    cards = pack.get("C5_interpretation", [])
    index = order.get(kind)
    if index is None or index >= len(cards):
        return ""
    return cards[index].get("id", "")


def _cap_rank(peers: Dict) -> Dict:
    """피어 무리 안에서 시총 순위 (점유율 대용 — 점유율이 아님을 반드시 밝힌다)."""
    target = peers.get("target", {})
    caps = [(p.get("name"), p.get("market_cap") or 0) for p in peers.get("peers", [])]
    caps.append((target.get("name"), target.get("market_cap") or 0))
    caps.sort(key=lambda kv: -kv[1])
    rank = next((i + 1 for i, (nm, _) in enumerate(caps) if nm == target.get("name")), 0)
    return {"rank": rank, "total": len(caps)}


def _finalize(filled: Dict[int, Dict], pack: Dict, workstream: str = "CORP-R") -> Dict:
    """밀도 조정 — 빈 슬롯을 정해진 순서대로 합치고 번호를 매긴다.

    합치는 순서는 워크스트림마다 다르다 (각 하네스설계서). CORP-R 은 §6.2,
    IND-R 은 §9 의 "인접 슬롯 MERGE", TP 두 개는 §10 의 자유양식 논리 순서를 따른다.
    """
    layout = FORMATS.get(workstream, FORMATS["CORP-R"])
    merged_notes: List[str] = []
    for keep, drop in layout["merge"]:
        if drop in filled and keep in filled:
            continue                                  # 둘 다 있으면 합치지 않는다
        if drop in filled and keep not in filled:
            # 앞 슬롯이 비었으면 뒤엣것을 앞자리로 올린다 (순서 보존)
            filled[keep] = filled.pop(drop)
            filled[keep]["merged_from"] = [drop]
            merged_notes.append(f"slot {keep} 자리에 slot {drop} 을 올렸다 (앞 슬롯 자료 없음)")
        elif keep in filled and drop not in filled:
            filled[keep]["merged_from"] = list(filled[keep].get("merged_from", [])) + [drop]
            merged_notes.append(f"slot {drop} 을 slot {keep} 에 합쳤다 (자료 없음)")

    pages = [filled[slot] for slot in sorted(filled)]
    for number, page in enumerate(pages, 1):
        page["page"] = number

    missing = [s["slot"] for s in layout["slots"] if s["slot"] not in filled]
    return {
        "pages": pages,
        "page_count": len(pages),
        "max_pages": 15,
        "workstream_id": workstream,
        "format": layout["kind"],
        "slot_count": len(layout["slots"]),
        "merged": merged_notes,
        "empty_slots": missing,
        "policy": (f"{layout['policy_note']} · {layout['kind']}. "
                   "15장은 상한이지 목표가 아니다 — 자료 없는 장을 만들지 않는다."),
        "generated_at": contracts.now_kst(),
    }


def to_markdown(pack: Dict, report: Dict) -> str:
    """페이지 계약 → GIC 양식 마크다운."""
    charter = pack.get("C0_charter", {})
    target = charter.get("target", {})
    lines = [
        f"# {charter.get('task_name') or f'{target.get('name', '')} 리서치'}",
        "",
        f"> 분석 기준일 {charter.get('as_of')} · {report['page_count']}장 (상한 15장)",
        f"> 워크스트림 {charter.get('workstream_id')} · 스키마 {pack.get('schema_version')}",
        "",
        DISCLAIMER,
        "",
    ]

    for page in report["pages"]:
        lines.append(f"## {page['page']}. {page['title']}")
        lines.append("")
        lines.append(f"**{page['key_message']}**")
        lines.append("")
        for item in page["body"]:
            lines.append(f"- {item}")
        if page.get("visual"):
            lines.append("")
            lines.append(f"*차트: {page['visual']}*")

        card = page.get("interpretation") or {}
        if card:
            lines.append("")
            lines.append("| 해석 카드 | 내용 |")
            lines.append("|---|---|")
            for key, label in (("observation", "관찰"), ("meaning", "의미"),
                               ("causal_hypothesis", "인과 가설"), ("alternative", "다른 설명"),
                               ("limitation", "한계"), ("next_check", "다음 확인")):
                if card.get(key):
                    lines.append(f"| {label} | {card[key]} |")

        if page.get("gaps"):
            lines.append("")
            for gap in page["gaps"]:
                lines.append(f"> ⚠️ {gap}")
        if page.get("sources"):
            lines.append("")
            lines.append(f"출처: {' · '.join(page['sources'])}")
        lines.append("")
        lines.append(f"신뢰도 {page['confidence']} · {page['human_decision']}")
        if page.get("merged_from"):
            lines.append(f"(slot {' · '.join(str(s) for s in page['merged_from'])} 을 합친 장이다)")
        lines.append("")

    # 운영 부록 — 공통계약 §14 "Evidence Ledger 전체는 부록으로 분리할 수 있다"
    logs = pack.get("logs", {})
    lines += [
        "---", "",
        "## 부록 · 운영 기록",
        "",
        f"- 근거 {len(pack.get('C1_evidence', []))}건 · 데이터 {len(pack.get('C2_data', []))}건 "
        f"· 계산 {len(logs.get('calculations', []))}건",
        f"- Gap {len(logs.get('gaps', []))}건 · 충돌 {len(logs.get('conflicts', []))}건",
        f"- 변경 이력 {len(pack.get('C6_decisions', {}).get('changes', []))}건 "
        f"· 사용자 의견 {len(pack.get('C6_decisions', {}).get('feedback_log', []))}건",
        "",
    ]
    if report.get("merged"):
        lines.append("장 합침: " + " / ".join(report["merged"]))
        lines.append("")
    for gap in logs.get("gaps", []):
        lines.append(f"- `{gap['id']}` {gap.get('affected_claim_or_field')} — "
                     f"{gap.get('current_treatment')}")
    return "\n".join(lines)
