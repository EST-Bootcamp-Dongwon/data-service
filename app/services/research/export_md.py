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


def assemble(pack: Dict, analysis: Dict) -> Dict:
    """Context Pack + H04 분석 결과 → 최대 15장 페이지 계약.

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
                          f"매출 CAGR {growth.get('cagr', 0):+.1f}% · {growth.get('trend', '')}"
                          if growth.get("available") else "재무 시계열",
                          [f"{row['year']}: 매출 {row.get('revenue', 0) / 1e12:,.1f}조 · "
                           f"영업이익 {row.get('operating_income', 0) / 1e12:,.1f}조"
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

    return _finalize(filled, pack)


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


def _finalize(filled: Dict[int, Dict], pack: Dict) -> Dict:
    """§6.2 밀도 조정 — 빈 슬롯을 정해진 순서대로 합치고 번호를 매긴다."""
    merged_notes: List[str] = []
    for keep, drop in MERGE_ORDER:
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

    missing = [s["slot"] for s in SLOTS if s["slot"] not in filled]
    return {
        "pages": pages,
        "page_count": len(pages),
        "max_pages": 15,
        "merged": merged_notes,
        "empty_slots": missing,
        "policy": ("GIC §6.1 순서를 지키고 §6.2 밀도 규칙으로 합쳤다. "
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
