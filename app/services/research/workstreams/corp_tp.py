"""CORP-TP 기업 Top Pick 전용 판단 (명세 §5.4)

공통 상태(H00~H11)는 `stages.py` 에 있고, 여기에는 **기업 Top Pick 에만 있는 것**을 둔다.

    · 12개월 Event Window   (window_start, window_end] 규칙 · 구조/일회성/예정 분류
    · Quick Score 6차원      0/3/5 앵커 · 방향 잠금 · Unscored ≠ 0
    · Proceed / Watch / Drop 판정 문턱

CORP-R 이 "이 회사를 깊이 본다" 라면 CORP-TP 는 **"깊이 볼 가치가 있는가"** 를 빠르게 가린다.
그래서 재무를 5개년 훑지 않고 최근 12개월 사건과 여섯 차원 점수로 끝낸다.

GIC v15 기업TopPick 하네스설계서 §6 이 정한 규칙을 그대로 지킨다.

| 차원 | 0 | 3 | 5 | 방향 |
|---|---|---|---|---|
| Business clarity | 구조 확인 불가 | 일부 확인 | 고객·수익·비용·KPI 명확 | 높을수록 유리 |
| Financial quality | 취약 | 평균 | 우수 | 높을수록 유리 |
| **Valuation burden** | 부담 낮음 | 중립 | 부담 매우 높음 | **높을수록 불리** |
| Catalyst strength | 촉매 없음 | 조건부 | 조건·시점·경로 강함 | 높을수록 유리 |
| **Risk severity** | 영향 낮음 | 관리 필요 | 결론 훼손 위험 | **높을수록 불리** |
| Source confidence | 근거 부족 | 일부 확인 | 1차 출처 충분 | 근거의 강도 |

그리고 §6 의 여섯 규칙 중 코드가 강제해야 하는 것들.

1. **`Unscored` 는 0점이 아니다.** 자료가 없어 못 매긴 차원을 0으로 세면
   '확인 못 함' 이 '아주 나쁨' 이 된다. 그래서 점수 대신 `None` 을 두고 분모에서 뺀다.
2. **여섯 원점수를 단순 합산하지 않는다.** 방향이 반대인 것(부담·위험)이 섞여 있어서다.
   비교지수가 필요하면 **역변환 식을 공개하고** 원점수를 함께 낸다.
3. **Source confidence 는 회사의 질이 아니다.** 근거의 충분성이라 다른 다섯과 섞지 않는다.
4. **각 점수에 근거 2개 이상**을 요구한다. 못 채우면 점수보다 `Unscored` 를 우선한다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import fmean
from typing import Dict, List, Optional, Sequence

from ....clients import dart_data, hf_data
from ....repositories import industry_store, snapshot_store
from ..knowledge import financials, macro, valuation

KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────────────────────────
# 1. 12개월 Event Window (설계서 CTP-04)
# ─────────────────────────────────────────────────────────────
# 포함 규칙은 **(시작경계, 종료] 반개구간**이다. 설계서가 못박은 그대로다 —
# "기준일이 2026-07-17이면 2025-07-17 초과, 2026-07-17 이하의 사건을 포함한다".
# 경계일을 양쪽에 넣으면 12개월이 아니라 12개월+1일이 되고, 해가 바뀌면 그 하루가
# 실적 공시 하나를 통째로 넣거나 뺀다.
EVENT_WINDOW_MONTHS = 12

# 공시 카테고리 → 사건 성격. `dart_data._classify` 가 붙여 주는 카테고리를 받는다.
#   구조     회사의 구조가 바뀐 것 (되돌리기 어렵다)
#   일회성   그 분기에만 영향을 주는 것
#   예정     아직 일어나지 않았거나 안내만 된 것
EVENT_KIND = {
    "실적": {"kind": "일회성", "weight": 3, "why": "분기 실적은 그 기간의 결과다"},
    "자본": {"kind": "구조", "weight": 5, "why": "증자·감자·자기주식은 주주 구조를 바꾼다"},
    "지배구조": {"kind": "구조", "weight": 4, "why": "최대주주·경영진 변경은 되돌리기 어렵다"},
    "사업": {"kind": "구조", "weight": 5, "why": "영업양수도·신규사업은 사업 구조를 바꾼다"},
    "배당": {"kind": "일회성", "weight": 2, "why": "배당은 현금 배분 결정이다"},
    "감사": {"kind": "구조", "weight": 4, "why": "감사의견은 신뢰성 자체에 걸린다"},
    "제재": {"kind": "구조", "weight": 5, "why": "제재·불성실공시는 신뢰도에 직접 영향을 준다"},
    "기타": {"kind": "일회성", "weight": 1, "why": "성격을 가르지 못했다"},
}

# 제목에 이것이 들어 있으면 '예정' 으로 본다 (아직 일어나지 않은 일).
#
# ⚠️ '결정' 을 넣었다가 실측에서 틀렸다 — `현금ㆍ현물배당결정` 이 예정으로 잡혔는데
#    그건 이미 **결정된 사실**이다. 낱말 하나가 사실과 예정을 뒤집으므로 좁게 잡는다.
SCHEDULED_MARKERS = ("예고", "안내공시", "개최", "예정")

# 감성 판정을 붙일 최대 건수. 전부 보내면 배포본에서 지연이 커진다 (실측 배치 30건 0.66초·웜).
SENTIMENT_LIMIT = 40


def event_window(as_of: str = "", months: int = EVENT_WINDOW_MONTHS) -> Dict:
    """(시작경계, 종료] 반개구간을 만든다.

    시작 경계는 "기준일에서 `months` 개월 전의 같은 달·일" 이고 **그날은 포함하지 않는다.**
    2월 29일처럼 같은 날이 없으면 그 달의 마지막 날로 맞춘다.
    """
    end = as_of or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        end_date = date.fromisoformat(end)
    except ValueError:
        end_date = datetime.now(KST).date()
        end = end_date.isoformat()

    year = end_date.year - months // 12
    month = end_date.month - months % 12
    if month <= 0:
        month += 12
        year -= 1
    day = end_date.day
    while day > 1:                                  # 2/29 → 2/28 같은 보정
        try:
            start_boundary = date(year, month, day)
            break
        except ValueError:
            day -= 1
    else:
        start_boundary = date(year, month, 1)

    return {
        "window_start_boundary": start_boundary.isoformat(),
        "window_end": end,
        "months": months,
        "rule": "(window_start_boundary, window_end] — 시작 경계일은 포함하지 않는다",
        "basis": "GIC v15 기업TopPick 하네스설계서 CTP-04",
    }


def _is_scheduled(report_name: str) -> bool:
    return any(marker in str(report_name or "") for marker in SCHEDULED_MARKERS)


def classify_events(rows: Sequence[Dict], window: Dict,
                    with_sentiment: bool = True) -> Dict:
    """공시 목록 → 사건 타임라인 (구조 / 일회성 / 예정).

    `with_sentiment` 를 켜면 HuggingFace 금융 감성 모델로 제목의 방향을 판정한다 (U5).
    **실패해도 죽지 않는다** — 규칙 기반으로 떨어지고 그 사실을 `sentiment.reason` 에 적는다.
    """
    start = window["window_start_boundary"]
    end = window["window_end"]

    inside: List[Dict] = []
    before: List[Dict] = []
    after: List[Dict] = []
    for row in rows:
        published = str(row.get("date") or "")
        if not published:
            continue
        if published <= start:
            before.append(row)                       # pre-window context
        elif published > end:
            after.append(row)                        # future scheduled item
        else:
            inside.append(row)

    events: List[Dict] = []
    for row in inside:
        category = row.get("category", "기타")
        spec = EVENT_KIND.get(category, EVENT_KIND["기타"])
        kind = "예정" if _is_scheduled(row.get("report_name", "")) else spec["kind"]
        events.append({
            "event_id": f"EV-{len(events) + 1:03d}",
            "date": row.get("date"),
            "title": row.get("report_name"),
            "category": category,
            "kind": kind,
            "weight": spec["weight"],
            "why": spec["why"],
            "rcept_no": row.get("rcept_no"),
            "url": row.get("url", ""),
            "sentiment": None,
        })
    events.sort(key=lambda e: str(e["date"]), reverse=True)

    # 감성 (U5) — 제목만 있고 본문이 없다는 한계를 반드시 함께 낸다
    sentiment_summary: Dict = {"available": False, "reason": "감성 판정을 끄고 불렀다"}
    if with_sentiment and events:
        titles = [e["title"] for e in events[:SENTIMENT_LIMIT]]
        judged = hf_data.sentiment(titles)
        if judged.get("available"):
            for event, row in zip(events, judged["rows"]):
                event["sentiment"] = {"label": row["label"], "score": row["score"]}
            sentiment_summary = {
                "available": True,
                "model": judged["model"],
                "counts": judged["counts"],
                "judged": len(judged["rows"]),
                "total_events": len(events),
                "elapsed_sec": judged.get("elapsed_sec"),
                "limitation": ("공시 **제목**만 넣었다. 본문이 없어 문맥이 빠지고, "
                               "제목이 중립적인 정정공시가 중립으로 나온다."
                               + (f" 최신 {SENTIMENT_LIMIT}건만 판정했다 "
                                  f"(전체 {len(events)}건 — 나머지는 감성 없음)"
                                  if len(events) > SENTIMENT_LIMIT else "")),
                "basis": "U5 결정 — HuggingFace 추론 API (금융 도메인 감성 모델)",
            }
        else:
            sentiment_summary = {"available": False, "reason": judged.get("reason", ""),
                                 "fallback": "감성 없이 카테고리 가중치만으로 진행한다"}

    by_kind: Dict[str, int] = {}
    for event in events:
        by_kind[event["kind"]] = by_kind.get(event["kind"], 0) + 1

    return {
        "available": bool(events),
        "window": window,
        "events": events,
        "counts": by_kind,
        "total": len(events),
        "pre_window": len(before),
        "future_scheduled": len(after),
        "structural": [e for e in events if e["kind"] == "구조"][:8],
        "sentiment": sentiment_summary,
        "reason": "" if events else "12개월 창 안에 공시가 없다",
        "note": ("창 밖 사건은 버리지 않고 pre-window / future scheduled 로 갈라 세었다 "
                 f"(이전 {len(before)}건 · 이후 {len(after)}건)"),
        "basis": "GIC v15 기업TopPick 하네스설계서 CTP-04",
    }


# ─────────────────────────────────────────────────────────────
# 2. Quick Score 6차원 (설계서 §6 · CTP-10)
# ─────────────────────────────────────────────────────────────
DIMENSIONS = [
    {"key": "business", "name": "Business clarity", "direction": +1,
     "anchor": {0: "사업·수익 구조 확인 불가", 3: "핵심 구조 일부 확인",
                5: "고객·수익·비용·KPI 명확"}},
    {"key": "financial", "name": "Financial quality", "direction": +1,
     "anchor": {0: "재무 질 취약", 3: "평균·혼재", 5: "성장·수익·현금·안정성 우수"}},
    {"key": "valuation", "name": "Valuation burden", "direction": -1,
     "anchor": {0: "부담 낮음", 3: "중립", 5: "부담 매우 높음"}},
    {"key": "catalyst", "name": "Catalyst strength", "direction": +1,
     "anchor": {0: "확인 촉매 없음", 3: "조건부 촉매", 5: "조건·시점·전달 경로 강함"}},
    {"key": "risk", "name": "Risk severity", "direction": -1,
     "anchor": {0: "영향 낮음", 3: "관리 필요", 5: "결론 훼손 위험 큼"}},
    {"key": "source", "name": "Source confidence", "direction": +1, "separate": True,
     "anchor": {0: "근거 부족", 3: "핵심 일부 확인", 5: "1차 출처·기준일 충분"}},
]
DIMENSION_BY_KEY = {d["key"]: d for d in DIMENSIONS}

# 설계서 §6 규칙 2 — 점수 하나에 최소 이만큼의 근거가 붙어야 한다
MIN_EVIDENCE_PER_SCORE = 2

UNSCORED = None                                    # 0 과 구분하기 위해 None 을 쓴다


def _score(key: str, value: Optional[int], reasons: List[str], counters: List[str],
           evidence_ids: Sequence[str], confidence: str = "medium",
           retrigger: str = "") -> Dict:
    """차원 하나의 원점수. 근거가 모자라면 **점수를 지우고 Unscored 로 만든다.**"""
    spec = DIMENSION_BY_KEY[key]
    enough = len(evidence_ids) >= MIN_EVIDENCE_PER_SCORE
    if value is not None and not enough:
        reasons = list(reasons) + [
            f"근거가 {len(evidence_ids)}건뿐이라 점수를 매기지 않는다 "
            f"(최소 {MIN_EVIDENCE_PER_SCORE}건 — 설계서 §6 규칙 2)"]
        value = UNSCORED
    return {
        "key": key,
        "name": spec["name"],
        "score": value,
        "unscored": value is UNSCORED,
        "direction": spec["direction"],
        "direction_label": "높을수록 유리" if spec["direction"] > 0 else "높을수록 불리",
        "anchor": spec["anchor"],
        "reasons": list(reasons),
        "counter_evidence": list(counters),
        "evidence_ids": list(evidence_ids),
        "confidence": confidence if value is not None else "low",
        "retrigger": retrigger or "어떤 자료가 들어오면 이 점수가 바뀌는지 적는다",
        "separate": bool(spec.get("separate")),
    }


def score_business(report_facts: Dict, segments_found: bool, evidence_ids: Sequence[str]) -> Dict:
    """사업모델이 얼마나 또렷한가 (CTP-03)."""
    reasons, counters = [], []
    segments = (report_facts or {}).get("segments", {})
    rnd = (report_facts or {}).get("rnd", {})

    if segments.get("found"):
        rows = segments.get("rows", [])
        reasons.append(f"사업보고서에서 부문 {len(rows)}개를 확인했다")
        top = rows[0] if rows else None
        if top and len(rows) >= 2:
            reasons.append(f"가장 큰 부문은 {top['segment']} 다")
    else:
        counters.append(segments.get("reason") or "부문별 매출을 확인하지 못했다")
    if rnd.get("found"):
        reasons.append("연구개발 지표를 확인했다")

    if len(reasons) >= 2:
        value = 5
    elif reasons:
        value = 3
    elif counters:
        value = UNSCORED                       # 0 이 아니다 — 못 본 것이지 나쁜 것이 아니다
    else:
        value = UNSCORED
    return _score("business", value, reasons, counters, evidence_ids,
                  retrigger="사업보고서 'II. 사업의 내용' 원문을 받으면 다시 매긴다")


def score_financial(series: List[Dict], ratios: Dict, growth: Dict,
                    evidence_ids: Sequence[str], industry_code: str = "",
                    name: str = "") -> Dict:
    """재무의 질 (CTP-05). 성장 · 수익성 · 현금전환 · 안정성 넷을 본다."""
    reasons, counters = [], []
    if not series:
        return _score("financial", UNSCORED, [], ["재무 시계열이 없다"], evidence_ids,
                      retrigger="DART 재무제표를 받으면 매긴다")

    # 금융업이면 안정성 판정을 보류한다 (U8 ② — macro.stability_verdicts)
    adjusted = macro.stability_verdicts(ratios, industry_code, name)
    ratios = adjusted["ratios"]

    if growth.get("available") and (growth.get("cagr") or 0) > 0:
        reasons.append(f"매출 CAGR {growth['cagr']:+.1f}% ({growth.get('trend')})")
    elif growth.get("available"):
        counters.append(f"매출 CAGR {growth.get('cagr')}% — 역성장이다")

    margin = ratios.get("operating_margin", {})
    if margin.get("grade") == financials.GRADE_GOOD:
        reasons.append(f"영업이익률 {margin.get('value')}% — 본업에서 이익이 난다")
    elif margin.get("grade") == financials.GRADE_SERIOUS:
        counters.append("본업에서 적자다")

    roe_row = ratios.get("roe", {})
    if roe_row.get("grade") == financials.GRADE_GOOD:
        reasons.append(f"ROE {roe_row.get('value')}% — 10% 기준을 넘는다")
    elif roe_row.get("grade") == financials.GRADE_WARNING:
        counters.append(f"ROE {roe_row.get('value')}% — 10% 기준에 못 미친다")

    quality = ratios.get("earnings_quality", {})
    if quality.get("available"):
        if quality.get("grade") == financials.GRADE_GOOD:
            reasons.append(f"영업현금흐름/영업이익 {quality['cash_conversion']:.2f}")
        else:
            counters.append(quality.get("why", ""))

    debt = ratios.get("debt_ratio", {})
    if debt.get("grade") == financials.GRADE_GOOD:
        reasons.append(f"부채비율 {debt.get('value')}% — 안정 구간")
    elif debt.get("grade") == financials.GRADE_SERIOUS:
        counters.append(f"부채비율 {debt.get('value')}% — 주의 구간")
    elif adjusted.get("applied"):
        counters.append(adjusted.get("note", ""))

    if len(reasons) >= 3 and len(counters) <= 1:
        value = 5
    elif reasons and len(reasons) >= len(counters):
        value = 3
    elif counters:
        value = 0                              # 여기는 진짜 '취약' 이라 0 이 맞다
    else:
        value = UNSCORED
    return _score("financial", value, reasons, counters, evidence_ids,
                  confidence="high" if len(series) >= 3 else "medium",
                  retrigger="다음 분기 실적으로 마진·현금전환을 재확인한다")


def score_valuation(target: Dict, position: Dict, quadrant: Dict,
                    evidence_ids: Sequence[str]) -> Dict:
    """밸류에이션 **부담** (CTP-06).

    ⚠️ 방향이 반대다 — 점수가 높을수록 **불리**하다.
    그리고 `Unscored` 를 저평가로 읽으면 안 된다 (설계서 §6 미확인 처리).
    """
    reasons, counters = [], []
    if not position.get("available"):
        return _score("valuation", UNSCORED, [],
                      [position.get("reason", "피어 비교가 없어 부담을 못 잰다"),
                       "**미확인을 저평가로 읽지 않는다**"],
                      evidence_ids, retrigger="피어를 직접 지정하면 부담을 잴 수 있다")

    premium = position.get("premium_pct", 0)
    if premium > 50:
        value, why = 5, f"피어 중앙값보다 {premium:+.1f}% 비싸다 — 부담이 크다"
    elif premium > 20:
        value, why = 4, f"피어 중앙값보다 {premium:+.1f}% 비싸다"
    elif premium >= -20:
        value, why = 3, f"피어 중앙값 대비 {premium:+.1f}% — 중립 구간이다"
    elif premium >= -50:
        value, why = 2, f"피어 중앙값보다 {abs(premium):.1f}% 싸다"
    else:
        value, why = 0, f"피어 중앙값보다 {abs(premium):.1f}% 싸다 — 부담이 낮다"
    reasons.append(why)

    if quadrant.get("available"):
        reasons.append(f"{quadrant['quadrant']} — {quadrant['meaning']}")
        if quadrant["quadrant"] == "버블 위험":
            value = min(5, value + 1)
            counters.append("성장 없이 PER 만 높다 — 부담을 한 단계 올렸다")
    if position.get("peer_count", 0) < 5:
        counters.append(f"피어가 {position.get('peer_count')}곳뿐이라 중앙값이 흔들린다")

    return _score("valuation", value, reasons, counters, evidence_ids,
                  confidence="medium" if position.get("peer_count", 0) >= 5 else "low",
                  retrigger="피어 구성이 바뀌면 부담 점수가 바뀐다")


def score_catalyst(events: Dict, evidence_ids: Sequence[str]) -> Dict:
    """촉매의 강도 (CTP-07).

    설계서가 "확률을 만들지 않고 **실현 조건·확인 일정·반대 근거**를 제시한다" 고 했다.
    그래서 여기서도 '몇 % 확률' 을 만들지 않고 사건의 성격과 개수만 센다.
    """
    reasons, counters = [], []
    if not events.get("available"):
        return _score("catalyst", UNSCORED, [], [events.get("reason", "사건 자료가 없다"),
                                                 "**없음과 미확인을 갈라 둔다**"],
                      evidence_ids, retrigger="공시 목록을 받으면 매긴다")

    structural = [e for e in events["events"] if e["kind"] == "구조"]
    scheduled = [e for e in events["events"] if e["kind"] == "예정"]
    positive = [e for e in events["events"]
                if (e.get("sentiment") or {}).get("label") == "긍정"]

    if structural:
        reasons.append(f"구조 변화 사건 {len(structural)}건 "
                       f"(최근: {structural[0]['title']})")
    if scheduled:
        reasons.append(f"예정 사건 {len(scheduled)}건 — 확인 일정이 있다")
    if positive:
        reasons.append(f"감성 긍정으로 판정된 공시 {len(positive)}건")
    if not structural:
        counters.append("12개월 안에 구조를 바꾼 사건이 없다 — 촉매가 약하다")

    if len(structural) >= 3 and scheduled:
        value = 5
    elif structural or scheduled:
        value = 3
    else:
        value = 0
    return _score("catalyst", value, reasons, counters, evidence_ids,
                  retrigger="예정 사건의 실현 여부를 확인일에 다시 본다")


def score_risk(events: Dict, ratios: Dict, red_checks: Sequence[Dict],
               evidence_ids: Sequence[str]) -> Dict:
    """위험의 **심각도** (CTP-08).

    ⚠️ 방향이 반대다 — 점수가 높을수록 불리하다.
    그리고 **미확인 위험을 낮은 위험으로 처리하지 않는다.**
    """
    reasons, counters = [], []
    if not events.get("available") and not ratios:
        return _score("risk", UNSCORED, [], ["위험을 판정할 자료가 없다",
                                             "**미확인을 안전으로 읽지 않는다**"],
                      evidence_ids, retrigger="공시·재무를 받으면 매긴다")

    penalties = 0
    sanctions = [e for e in events.get("events", []) if e["category"] == "제재"]
    if sanctions:
        penalties += 2
        reasons.append(f"제재·불성실공시 {len(sanctions)}건 — {sanctions[0]['title']}")
    audit = [e for e in events.get("events", []) if e["category"] == "감사"]
    if audit:
        penalties += 1
        reasons.append(f"감사 관련 공시 {len(audit)}건")

    debt = ratios.get("debt_ratio", {})
    if debt.get("grade") == financials.GRADE_SERIOUS:
        penalties += 1
        reasons.append(f"부채비율 {debt.get('value')}% — 주의 구간")
    quality = ratios.get("earnings_quality", {})
    if quality.get("available") and quality.get("grade") == financials.GRADE_SERIOUS:
        penalties += 1
        reasons.append(quality.get("why", "이익의 질이 나쁘다"))

    failed = [c for c in red_checks if c.get("verdict") == "실패"]
    if failed:
        penalties += 1
        reasons.append(f"Red Team 실패 {len(failed)}건")
    unknown = [c for c in red_checks if c.get("verdict") == "판정불가"]
    if unknown:
        counters.append(f"판정불가 {len(unknown)}건 — 확인하지 못한 위험이 남아 있다")

    value = min(5, penalties) if penalties else 0
    if not reasons:
        reasons.append("확인된 위험 신호가 없다")
    return _score("risk", value, reasons, counters, evidence_ids,
                  retrigger="분기마다 제재·감사·부채 지표를 다시 본다")


def score_source(coverage: Dict, evidence_ids: Sequence[str]) -> Dict:
    """근거의 충분성 (CTP-09).

    ⚠️ **회사의 질이 아니다.** 다른 다섯 차원과 섞어 평균 내지 않는다 (설계서 §6 규칙 5).
    """
    reasons, counters = [], []
    total = coverage.get("evidence", 0)
    linked = coverage.get("data_linked_ratio", 0.0)
    grades = coverage.get("by_grade", {})
    primary = grades.get("1차공식", 0) + grades.get("회사원문", 0)

    if total:
        reasons.append(f"근거 {total}건 (1차공식·회사원문 {primary}건)")
    if linked >= 0.8:
        reasons.append(f"수치의 {linked:.0%} 가 근거에 연결됐다")
    elif total:
        counters.append(f"수치의 {linked:.0%} 만 근거에 연결됐다")
    if coverage.get("gaps"):
        counters.append(f"미해결 Gap {coverage['gaps']}건")

    if primary >= 5 and linked >= 0.8:
        value = 5
    elif primary >= 2:
        value = 3
    elif total:
        value = 0
    else:
        value = UNSCORED
    return _score("source", value, reasons, counters, evidence_ids,
                  retrigger="1차 출처를 더 붙이면 올라간다")


def scorecard(scores: Sequence[Dict]) -> Dict:
    """여섯 원점수를 표로 묶는다.

    설계서 §6 규칙 4 — **단순 합산 금지.** 그래도 후보를 견주려면 하나의 숫자가 필요하므로
    비교지수를 내되 **역변환 식과 원점수를 반드시 함께 공개한다.**

        기회지수 = mean(Business, Financial, Catalyst, 5 - Valuation, 5 - Risk)

    Source confidence 는 회사의 질이 아니라 근거의 강도라 **지수에 넣지 않는다.**
    """
    rows = list(scores)
    quality = [s for s in rows if not s["separate"]]
    scored = [s for s in quality if s["score"] is not None]
    unscored = [s["name"] for s in quality if s["score"] is None]

    converted = []
    for row in scored:
        value = row["score"] if row["direction"] > 0 else (5 - row["score"])
        converted.append({"name": row["name"], "raw": row["score"], "aligned": value,
                          "direction": row["direction_label"]})

    index = round(fmean(c["aligned"] for c in converted), 2) if converted else None
    source_row = next((s for s in rows if s["separate"]), None)

    return {
        "rows": rows,
        "index": index,
        "index_max": 5,
        "index_formula": ("기회지수 = mean(Business, Financial, Catalyst, "
                          "5 - Valuation burden, 5 - Risk severity)"),
        "index_note": ("부담·위험은 방향이 반대라 `5 - 원점수` 로 뒤집어 넣었다. "
                       "원점수는 위 표에 그대로 있다 — 단순 합산이 아니다 (설계서 §6 규칙 4)."),
        "scored_count": len(scored),
        "quality_dimensions": len(quality),
        "unscored": unscored,
        "coverage": round(len(scored) / len(quality), 3) if quality else 0.0,
        "source_confidence": source_row,
        "separate_note": ("Source confidence 는 기업의 질이 아니라 근거의 충분성이라 "
                          "지수에 넣지 않는다 (설계서 §6 규칙 5)."),
        "basis": "GIC v15 기업TopPick 하네스설계서 §6 Quick Score 방향·판정 잠금",
    }


# ─────────────────────────────────────────────────────────────
# 3. Proceed / Watch / Drop (설계서 §6 판정 계약)
# ─────────────────────────────────────────────────────────────
# 문턱은 **사용자가 CTP-00 에서 정한다** (설계서 사용자 질문 ③).
# 아래는 답이 없을 때 쓰는 기본값이고, 그 사실을 판정 결과에 밝힌다.
DEFAULT_THRESHOLDS = {"proceed": 3.5, "watch": 2.5}

# 자료 부족만으로 Drop 하지 않는다 (설계서 §6 판정 계약 마지막 줄).
# coverage 가 이보다 낮으면 점수와 무관하게 Watch 로 보낸다.
MIN_COVERAGE_FOR_VERDICT = 0.6


def verdict(card: Dict, red: Dict, coverage: Dict,
            thresholds: Optional[Dict] = None) -> Dict:
    """Proceed / Watch / Drop.

    **점수만으로 판정하지 않는다** (설계서 §6 규칙 6). Quick Score · Red Team ·
    coverage · 치명 Gap 을 함께 본다. 그리고 결과는 늘 **AI 제안**이다 — 사람이 승인한다.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    index = card.get("index")
    reasons: List[str] = []
    conditions: List[str] = []

    if index is None:
        return {
            "verdict": "Watch",
            "ai_proposal": True,
            "human_decision": "사람 승인 필요 — AI 는 제안만 한다",
            "reasons": ["여섯 차원 중 점수를 매긴 것이 없다"],
            "conditions": ["자료를 더 모아 다시 돌린다"],
            "review_date": "",
            "thresholds": limits,
            "basis": "GIC v15 기업TopPick 하네스설계서 §6 판정 계약",
            "note": "자료 부족만으로 Drop 하지 않는다",
        }

    failed = red.get("failed", 0)
    unknown = red.get("unknown", 0)
    score_coverage = card.get("coverage", 0.0)
    risk_row = next((r for r in card["rows"] if r["key"] == "risk"), {})
    risk_score = risk_row.get("score")

    if score_coverage < MIN_COVERAGE_FOR_VERDICT:
        decision = "Watch"
        reasons.append(f"여섯 차원 중 {score_coverage:.0%} 만 점수를 매겼다 — "
                       "자료 부족만으로 Drop 하지 않는다")
        conditions.append(f"미채점 차원({', '.join(card['unscored'])})의 자료를 받는다")
    elif risk_score is not None and risk_score >= 5:
        decision = "Drop"
        reasons.append("Risk severity 가 최고치다 — 결론을 훼손할 위험이 확인됐다")
        conditions.append("위험이 해소되면 재진입을 검토한다")
    elif index >= limits["proceed"] and failed == 0:
        decision = "Proceed"
        reasons.append(f"기회지수 {index}/5 로 문턱({limits['proceed']})을 넘었고 "
                       "Red Team 실패가 없다")
        conditions.append("정식 CORP-R 에서 피어·밸류에이션을 다시 검증한다")
    elif index >= limits["watch"]:
        decision = "Watch"
        reasons.append(f"기회지수 {index}/5 — 문턱({limits['proceed']})에 못 미친다")
        if failed:
            reasons.append(f"Red Team 실패 {failed}건")
        conditions.append("보강할 KPI·이벤트와 재검토일을 정한다")
    else:
        decision = "Drop"
        reasons.append(f"기회지수 {index}/5 로 보류 문턱({limits['watch']})에도 못 미친다")
        conditions.append("구조적 매력이 달라지면 다시 본다")

    if unknown:
        conditions.append(f"Red Team 판정불가 {unknown}건은 사람이 원문으로 확인한다")
    if coverage.get("gaps"):
        conditions.append(f"미해결 Gap {coverage['gaps']}건을 닫는다")

    return {
        "verdict": decision,
        "ai_proposal": True,
        "human_decision": "사람 승인 필요 — AI 는 제안만 한다 (불변원칙 §2-9·§2-10)",
        "index": index,
        "reasons": reasons,
        "conditions": conditions,
        "thresholds": limits,
        "threshold_source": ("사용자가 정한 값" if thresholds else
                             "기본값 — CTP-00 에서 사용자가 정하면 그 값을 쓴다"),
        "red_team": {"failed": failed, "unknown": unknown},
        "score_coverage": score_coverage,
        "basis": "GIC v15 기업TopPick 하네스설계서 §6 판정 계약",
        "note": ("Proceed = 정식 CORP-R 에서 검증할 가치가 충분하다 / "
                 "Watch = 보강 자료·재검토일이 필요하다 / "
                 "Drop = 우선순위가 낮거나 치명 취약점이 있다. "
                 "**자료 부족만으로 Drop 을 고르지 않는다.**"),
    }


def parse_thresholds(answer: str) -> Optional[Dict]:
    """H01 답변 문자열에서 P/W/D 문턱을 읽는다 (`"3.5 / 2.5"` 같은 형태)."""
    numbers = []
    for token in str(answer or "").replace("/", " ").replace(",", " ").split():
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    if len(numbers) >= 2:
        return {"proceed": max(numbers[0], numbers[1]), "watch": min(numbers[0], numbers[1])}
    return None


def analyze(pack_target: Dict, series: List[Dict], ratios: Dict, growth: Dict,
            peers: Dict, position: Dict, quadrant: Dict, report_facts: Dict,
            disclosures: Sequence[Dict], coverage: Dict, red_checks: Sequence[Dict],
            evidence_ids: Sequence[str], as_of: str = "",
            thresholds: Optional[Dict] = None, with_sentiment: bool = True) -> Dict:
    """H04 가 부르는 입구 — 이벤트 창 · 6차원 점수 · P/W/D 를 한 번에 만든다."""
    window = event_window(as_of)
    events = classify_events(disclosures, window, with_sentiment=with_sentiment)

    industry_code = industry_store.industry_of(pack_target.get("code", ""))
    ids = list(evidence_ids)
    scores = [
        score_business(report_facts, bool(report_facts), ids),
        score_financial(series, ratios, growth, ids, industry_code,
                        pack_target.get("name", "")),
        score_valuation(pack_target, position, quadrant, ids),
        score_catalyst(events, ids),
        score_risk(events, ratios, red_checks, ids),
        score_source(coverage, ids),
    ]
    card = scorecard(scores)
    decision = verdict(card, {"failed": len([c for c in red_checks
                                             if c.get("verdict") == "실패"]),
                              "unknown": len([c for c in red_checks
                                              if c.get("verdict") == "판정불가"])},
                       coverage, thresholds)
    return {"events": events, "scorecard": card, "verdict": decision,
            "window": window, "industry_code": industry_code}
