"""08강 02·03 경제지표·거시를 판정 함수로 옮긴 것 (명세 §5.6)

세 가지를 한다.

1. **무위험이자율을 실측한다** (U8 결정 ①)
   `market_data.RISK_FREE_RATE = 0.032` 가 하드코딩돼 있었다. 샤프지수·효율적투자선·
   밸류에이션 할인율이 전부 이 상수에 걸려 있는데 실측하면 국고채 3년이 3.758% 라
   **0.56%p 어긋나 있었다.** 1차 소스는 ECOS 국고채 3년, 교차검증은 금감원 정기예금 금리다.

2. **경기 국면을 판정한다** (03.md 4국면 — Expansion · Peak · Contraction · Trough)
   ECOS 경기선행지수·동행지수 **순환변동치**로 판정한다. IND-R 의 사이클 판정이 이걸 쓴다.

3. **금리·환율·물가에 대한 섹터 민감도를 매핑한다** (02.md · 03.md 섹터 로테이션)
   그리고 **금융업 예외**를 판정한다 (U8 결정 ②) — 06.md 421행이 "금융업은 일반 제조업
   비율로 보지 않는다" 고 했는데 지금 코드는 신한지주 부채비율을 그냥 계산해
   `🔴 200% 이상 주의` 로 찍고 있었다. 은행은 예금이 부채라 1,000% 가 정상이다.

판정은 늘 **값 + 이유 + 근거 강의**를 함께 낸다. 등급만 주면 리포트가 왜인지 설명하지 못한다.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ....clients import ecos_data, fss_data
from .financials import GRADE_GOOD, GRADE_SERIOUS, GRADE_UNKNOWN, GRADE_WARNING

# ─────────────────────────────────────────────────────────────
# 1. 무위험이자율 (U8 ①)
# ─────────────────────────────────────────────────────────────
# 실측이 실패했을 때 쓸 마지막 대비값. **되도록 안 쓰이게 하는 것이 목적**이라
# 이 값을 썼다는 사실을 반드시 응답에 남긴다 (`source = "fallback"`).
FALLBACK_RISK_FREE_PCT = 3.2

# 국고채와 예금금리가 이만큼(%p) 넘게 벌어지면 출처 충돌로 본다.
# 둘은 신용위험이 달라 원래 같지 않다 — 그래도 1%p 를 넘으면 어느 쪽을 쓰는지가
# 샤프지수를 눈에 띄게 흔들기 때문에 밝히고 넘어간다.
RATE_CONFLICT_THRESHOLD = 1.0


def risk_free_rate(prefer: str = "ktb3y", cross_check: bool = True) -> Dict:
    """무위험이자율(%)을 실측한다.

    | 후보 | 출처 | 왜 |
    |---|---|---|
    | **국고채 3년** | ECOS `817Y002` | 국가 신용이라 무위험에 가장 가깝다. 일별이라 최신이다 |
    | 정기예금 12개월 | 금감원 finlife | 시중 실세금리. 예금자보호 한도가 있어 무위험이 아니다 |
    | 한은 기준금리 | ECOS `722Y001` | 정책금리라 시장금리와 시차가 있다 |

    **국고채를 1차로 쓰고 예금금리는 나란히 싣는다.** 하나를 임의로 고르지 않는 것이
    GIC 불변원칙 §2-4 다. 두 값이 크게 벌어지면 `conflict` 에 담아 돌려준다.
    """
    result: Dict = {
        "available": False, "value": None, "unit": "%", "source": "", "as_of": "",
        "candidates": [], "conflict": None,
        "basis": "08강 02.md 금리 지표 · 14.md 샤프비율 무위험수익률",
    }

    # 1차 — ECOS 국고채 3년
    for indicator, label in (("ktb3y", "국고채 3년"), ("base_rate", "한국은행 기준금리")):
        try:
            series = ecos_data.fetch_series(indicator, years=1)
        except Exception as error:
            result["candidates"].append({"name": label, "value": None,
                                         "reason": f"조회 실패 — {str(error)[:80]}"})
            continue
        values = [v for v in (series.get("values") or []) if v is not None]
        dates = series.get("dates") or []
        if not values:
            result["candidates"].append({"name": label, "value": None, "reason": "값이 비어 있다"})
            continue
        entry = {"name": label, "value": round(float(values[-1]), 3),
                 "as_of": dates[-1] if dates else "", "source": "ECOS", "id": indicator}
        result["candidates"].append(entry)
        if indicator == prefer and not result["available"]:
            result.update(available=True, value=entry["value"], source=f"ECOS {label}",
                          as_of=entry["as_of"])

    # 교차검증 — 금감원 정기예금 (은행 권역만. 저축은행은 신용위험이 달라 무위험 대용이 아니다)
    deposit = fss_data.deposit_rates() if cross_check else {"available": False,
                                                            "reason": "교차검증을 끄고 불렀다"}
    if deposit.get("available"):
        result["candidates"].append({
            "name": f"정기예금 {deposit['term_months']}개월 (기본금리 중앙값)",
            "value": deposit["base_median"], "as_of": deposit.get("as_of_month", ""),
            "source": "금융감독원 finlife", "id": "deposit",
            "range": [deposit["base_min"], deposit["base_max"]],
        })
    else:
        result["candidates"].append({"name": "정기예금 12개월", "value": None,
                                     "reason": deposit.get("reason", "조회 실패")})

    # 두 값이 벌어졌는가
    primary = result.get("value")
    secondary = deposit.get("base_median") if deposit.get("available") else None
    if primary is not None and secondary is not None:
        spread = abs(primary - secondary)
        if spread >= RATE_CONFLICT_THRESHOLD:
            result["conflict"] = {
                "code": "C-SOURCE",
                "message": (f"국고채 3년 {primary}% 와 정기예금 {secondary}% 가 "
                            f"{spread:.2f}%p 벌어져 있다"),
                "treatment": "국고채를 쓰고 예금금리를 병기한다 — 하나를 임의로 고르지 않는다",
                "sources": ["ECOS 817Y002", "금융감독원 finlife depositProductsSearch"],
            }
        result["spread_pp"] = round(spread, 3)

    if not result["available"]:
        result.update(available=False, value=FALLBACK_RISK_FREE_PCT, source="fallback",
                      note=(f"실측에 실패해 대비값 {FALLBACK_RISK_FREE_PCT}% 를 썼다. "
                            "이 값으로 낸 샤프지수는 근사다."))
    else:
        result["note"] = ("국고채 3년을 무위험이자율로 쓴다. 정기예금 금리는 예금자보호 한도가 "
                          "있어 완전한 무위험이 아니므로 교차검증용으로만 병기한다.")
    return result


def risk_free_ratio(prefer: str = "ktb3y") -> float:
    """무위험이자율을 **소수**로 돌려준다 (0.03758). 기존 상수 자리에 그대로 넣기 위한 것."""
    row = risk_free_rate(prefer)
    value = row.get("value")
    return float(value) / 100 if isinstance(value, (int, float)) else FALLBACK_RISK_FREE_PCT / 100


# ─────────────────────────────────────────────────────────────
# 2. 경기 국면 (03.md 4국면)
# ─────────────────────────────────────────────────────────────
# 03.md 는 4국면을 GDP·고용·기업실적으로 설명하지만, 그 셋은 발표가 늦다.
# 통계청 경기종합지수의 **순환변동치**는 100 을 추세선으로 두고 위·아래를 나타내므로
# "지금 어느 국면인가" 를 바로 읽을 수 있다. 그래서 이 값을 쓴다.
#
#   순환변동치 > 100  이고 오르는 중   → 확장 (Expansion)
#   순환변동치 > 100  이고 꺾이는 중   → 정점 (Peak)
#   순환변동치 < 100  이고 내리는 중   → 수축 (Contraction)
#   순환변동치 < 100  이고 도는 중     → 저점 (Trough)
CYCLE_PLAYBOOK = {
    "확장": {"favor": ["소재", "에너지", "산업재", "금융"], "avoid": ["유틸리티", "필수소비재"],
             "assets": ["주식", "원자재", "부동산"]},
    "정점": {"favor": ["에너지", "금융"], "avoid": ["기술", "경기소비재"],
             "assets": ["인플레이션 연동 자산", "현금 비중 확대"]},
    "수축": {"favor": ["헬스케어", "유틸리티", "필수소비재"], "avoid": ["에너지", "소재", "금융"],
             "assets": ["채권", "금", "방어주"]},
    "저점": {"favor": ["IT", "경기소비재"], "avoid": ["필수소비재"],
             "assets": ["경기민감주 선매수"]},
}

# 국면 판정에 쓰는 최소 관측 수. 03.md 는 "최근 최소 4개 분기 신호" 를 요구하는데
# 월별 지수라 12개월이 4분기에 해당한다.
CYCLE_MIN_MONTHS = 12
# 방향 판정에 쓰는 기간 (개월). 한 달치 등락은 잡음이라 3개월 기울기로 본다.
CYCLE_SLOPE_MONTHS = 3


def _slope(values: Sequence[float], months: int = CYCLE_SLOPE_MONTHS) -> Optional[float]:
    """마지막 `months` 개월 동안 얼마나 움직였나 (단순 차이). 관측이 모자라면 None."""
    clean = [v for v in values if v is not None]
    if len(clean) < months + 1:
        return None
    return float(clean[-1]) - float(clean[-1 - months])


def cycle_phase(leading: Optional[Sequence[float]] = None,
                coincident: Optional[Sequence[float]] = None) -> Dict:
    """경기 국면을 판정한다 (03.md 4국면).

    값을 주지 않으면 ECOS 에서 직접 받는다. 부족하면 **국면을 하나로 좁히지 않고**
    가능한 후보를 복수로 남긴다 (IND-R 하네스설계서 H11 "데이터가 부족하면 가능한 국면을
    복수 유지한다").
    """
    fetched: Dict[str, Dict] = {}
    if leading is None or coincident is None:
        for key, indicator in (("leading", "leading"), ("coincident", "coincident")):
            try:
                fetched[key] = ecos_data.fetch_series(indicator, years=3)
            except Exception as error:
                fetched[key] = {"values": [], "dates": [], "error": str(error)[:80]}
        leading = leading if leading is not None else fetched.get("leading", {}).get("values", [])
        coincident = (coincident if coincident is not None
                      else fetched.get("coincident", {}).get("values", []))

    lead = [v for v in (leading or []) if v is not None]
    coin = [v for v in (coincident or []) if v is not None]
    dates = (fetched.get("leading") or {}).get("dates") or []

    if len(lead) < CYCLE_MIN_MONTHS:
        return {
            "available": False,
            "reason": f"경기선행지수 관측이 {len(lead)}개월뿐이라 국면을 못 가른다 "
                      f"(최소 {CYCLE_MIN_MONTHS}개월 — 03.md 4개 분기)",
            "candidates": list(CYCLE_PLAYBOOK.keys()),
            "basis": "08강 03.md 경기 사이클 4국면",
        }

    level = float(lead[-1])
    slope = _slope(lead)
    coin_level = float(coin[-1]) if coin else None
    coin_slope = _slope(coin) if coin else None

    above = level >= 100
    rising = slope is not None and slope > 0
    if above and rising:
        phase, why = "확장", "선행지수가 추세선(100) 위에 있고 오르는 중이다"
    elif above and not rising:
        phase, why = "정점", "선행지수가 추세선 위이지만 꺾이고 있다 — 성장 둔화 신호"
    elif not above and not rising:
        phase, why = "수축", "선행지수가 추세선 아래이고 계속 내려가고 있다"
    else:
        phase, why = "저점", "선행지수가 추세선 아래이지만 돌아서고 있다 — 회복 초기 가능성"

    # 선행과 동행이 어긋나면 국면 판정의 신뢰도를 내린다.
    # 선행이 먼저 도는 것이 정상이지만, 방향이 갈리면 전환 구간이라 단정하기 어렵다.
    divergence = ""
    confidence = "medium"
    if coin_slope is not None and slope is not None:
        if (slope > 0) != (coin_slope > 0):
            divergence = (f"선행({slope:+.2f})과 동행({coin_slope:+.2f})의 방향이 갈린다 — "
                          "국면 전환 구간일 수 있다")
            confidence = "low"
        else:
            confidence = "high"

    play = CYCLE_PLAYBOOK[phase]
    return {
        "available": True,
        "phase": phase,
        "why": why,
        "confidence": confidence,
        "leading": {"level": round(level, 2), "slope_3m": None if slope is None else round(slope, 2),
                    "as_of": dates[-1] if dates else ""},
        "coincident": ({"level": round(coin_level, 2),
                        "slope_3m": None if coin_slope is None else round(coin_slope, 2)}
                       if coin_level is not None else None),
        "divergence": divergence,
        "favor_sectors": play["favor"],
        "avoid_sectors": play["avoid"],
        "favor_assets": play["assets"],
        "months_observed": len(lead),
        "basis": "08강 03.md 경기 사이클 4국면 · 섹터 로테이션 표",
        "limitation": ("순환변동치는 추세를 제거한 값이라 '지금 위치' 는 보여 주지만 "
                       "'얼마나 오래 갈지' 는 말해 주지 않는다. 월별 발표라 시차도 있다."),
    }


# ─────────────────────────────────────────────────────────────
# 3. 섹터 민감도 · 금융업 예외 (U8 ②)
# ─────────────────────────────────────────────────────────────
# KSIC 중분류(2자리) → 금리·환율·물가 민감도. 02.md·03.md 의 설명을 표로 옮겼다.
#   direction  +1 이면 그 변수가 오를 때 유리, -1 이면 불리, 0 이면 뚜렷하지 않다
SECTOR_SENSITIVITY = {
    "26": {"name": "전자·반도체", "rate": -1, "fx": +1, "inflation": -1,
           "why": "설비투자가 커 금리에 민감하고, 수출 비중이 커 원화 약세에 유리하다"},
    "27": {"name": "의료·정밀·광학", "rate": -1, "fx": +1, "inflation": -1,
           "why": "수출 비중이 크다"},
    "29": {"name": "기타 기계·장비", "rate": -1, "fx": +1, "inflation": -1,
           "why": "설비투자 사이클을 따라간다"},
    "30": {"name": "자동차·트레일러", "rate": -1, "fx": +1, "inflation": -1,
           "why": "할부금융 의존과 수출 비중이 함께 크다"},
    "20": {"name": "화학물질·화학제품", "rate": -1, "fx": 0, "inflation": -1,
           "why": "원유·나프타 투입 원가가 물가를 타고 들어온다"},
    "19": {"name": "코크스·석유정제", "rate": 0, "fx": -1, "inflation": +1,
           "why": "유가가 오르면 정제마진과 재고평가이익이 함께 움직인다"},
    "24": {"name": "1차 금속", "rate": -1, "fx": 0, "inflation": +1,
           "why": "원자재 가격 전가력이 국면마다 다르다"},
    "21": {"name": "의료용 물질·의약품", "rate": -1, "fx": 0, "inflation": 0,
           "why": "장기 연구개발이라 할인율(금리)에 민감하다"},
    "58": {"name": "출판·소프트웨어", "rate": -1, "fx": 0, "inflation": 0,
           "why": "먼 미래 현금흐름의 비중이 커 금리가 오르면 할인율 부담이 크다"},
    "62": {"name": "소프트웨어 개발·공급", "rate": -1, "fx": 0, "inflation": 0,
           "why": "같은 이유 — 성장주 할인율 민감"},
    "63": {"name": "정보서비스", "rate": -1, "fx": 0, "inflation": 0,
           "why": "같은 이유 — 성장주 할인율 민감"},
    "64": {"name": "금융업", "rate": +1, "fx": 0, "inflation": 0,
           "why": "예대마진이 금리 상승기에 벌어진다 (다만 연체율은 나빠진다)"},
    "65": {"name": "보험업", "rate": +1, "fx": 0, "inflation": 0,
           "why": "운용자산 이자수익이 금리를 따라간다"},
    "66": {"name": "금융지원", "rate": +1, "fx": 0, "inflation": 0,
           "why": "증권·여신 모두 금리 방향에 걸린다"},
    "35": {"name": "전기·가스·수도", "rate": -1, "fx": -1, "inflation": -1,
           "why": "요금이 규제돼 원가 상승을 바로 못 넘긴다 — 대표적 방어주"},
    "41": {"name": "종합 건설업", "rate": -1, "fx": 0, "inflation": -1,
           "why": "PF 금융비용과 자재비가 동시에 무겁다"},
    "68": {"name": "부동산업", "rate": -1, "fx": 0, "inflation": 0,
           "why": "차입 의존도가 높다"},
    "47": {"name": "소매업", "rate": -1, "fx": -1, "inflation": -1,
           "why": "가계 실질소득이 줄면 바로 매출에 반영된다"},
    "10": {"name": "식료품", "rate": 0, "fx": -1, "inflation": 0,
           "why": "원재료 수입 비중이 커 원화 약세가 원가 부담이다 — 다만 필수소비라 방어적이다"},
    "49": {"name": "육상운송", "rate": 0, "fx": -1, "inflation": -1,
           "why": "연료비가 원가의 큰 몫이다"},
    "50": {"name": "수상운송", "rate": 0, "fx": +1, "inflation": +1,
           "why": "운임이 오르는 국면과 물가 국면이 겹치는 경향이 있다"},
}

# 금융업 — 08강 06.md 421행 "금융업은 일반 제조업 비율로 보지 않는다"
FINANCIAL_MAJORS = {"64", "65", "66"}


def sector_sensitivity(industry_code: str) -> Dict:
    """업종코드 → 금리·환율·물가 민감도 (02.md·03.md).

    표에 없는 업종은 **지어내지 않는다.** 모른다고 답한다.
    """
    major = str(industry_code or "")[:2]
    row = SECTOR_SENSITIVITY.get(major)
    if not row:
        return {"available": False, "major": major,
                "reason": "강의 표에 없는 업종이라 민감도를 지어내지 않는다",
                "basis": "08강 02.md 경제지표 · 03.md 섹터 로테이션"}
    arrow = {1: "유리 ▲", -1: "불리 ▼", 0: "뚜렷하지 않음 —"}
    return {
        "available": True,
        "major": major,
        "name": row["name"],
        "rate": {"direction": row["rate"], "label": f"금리 상승 시 {arrow[row['rate']]}"},
        "fx": {"direction": row["fx"], "label": f"원화 약세 시 {arrow[row['fx']]}"},
        "inflation": {"direction": row["inflation"],
                      "label": f"물가 상승 시 {arrow[row['inflation']]}"},
        "why": row["why"],
        "basis": "08강 02.md 경제지표 · 03.md 섹터 로테이션",
        "limitation": "업종 평균이다. 같은 업종 안에서도 수출 비중·차입 구조에 따라 갈린다.",
    }


def is_financial(industry_code: str, name: str = "", cross_check: bool = False) -> Dict:
    """이 회사에 일반 제조업 재무비율 기준을 써도 되는가 (U8 ②).

    1차 기준은 **KSIC 64~66** 이다. `cross_check=True` 면 금감원 금융회사 목록으로
    한 번 더 확인한다 — 다만 상장사는 지주회사 이름이라 그대로 겹치지 않으므로
    **목록에 없다고 금융업이 아니라고 판정하지 않는다.**
    """
    major = str(industry_code or "")[:2]
    financial = major in FINANCIAL_MAJORS
    confirmed_by = ""
    if cross_check and name:
        listing = fss_data.companies()
        if listing.get("available"):
            stem = str(name).replace("(주)", "").replace("주식회사", "").strip()
            # '신한지주' ↔ '신한은행' 처럼 앞 두 글자만 겹치는 일이 흔하다.
            head = stem[:2]
            if head and any(head in row["name"] for row in listing["rows"]):
                confirmed_by = f"금융감독원 {listing['group_name']} 목록에 같은 계열 이름이 있다"
                financial = True
    return {
        "financial": financial,
        "major": major,
        "confirmed_by": confirmed_by,
        "why": ("KSIC 64~66 (금융·보험·금융지원)" if major in FINANCIAL_MAJORS
                else f"KSIC {major or '?'} — 금융 대분류가 아니다"),
        "treatment": ("부채비율·유동비율 판정을 보류한다 — 은행은 예금이 부채라 1,000% 가 정상이다"
                      if financial else "일반 제조업 기준을 그대로 쓴다"),
        "basis": "08강 06.md 421행 — 금융업은 일반 제조업 비율로 보지 않는다",
    }


def stability_verdicts(ratios: Dict, industry_code: str, name: str = "") -> Dict:
    """재무 안정성 판정에 **금융업 예외**를 씌운다 (U8 ②).

    `financials.debt_ratio()` 등이 이미 계산한 판정을 받아, 금융업이면 등급을
    `unknown` 으로 되돌리고 그 이유를 적는다. 숫자 자체는 지우지 않는다 —
    값은 남기고 '이 기준으로 읽지 말라' 고만 한다.
    """
    verdict = is_financial(industry_code, name)
    if not verdict["financial"]:
        return {"applied": False, "financial": False, "ratios": ratios, "note": verdict["why"]}

    adjusted = dict(ratios)
    changed: List[str] = []
    for key in ("debt_ratio", "current_ratio"):
        row = adjusted.get(key)
        if isinstance(row, dict) and row.get("value") is not None:
            adjusted[key] = {**row, "grade": GRADE_UNKNOWN,
                             "why": (f"{row.get('value')}{row.get('unit', '')} — "
                                     "금융업이라 제조업 기준(100%/150%)으로 읽지 않는다"),
                             "basis": verdict["basis"]}
            changed.append(row.get("label", key))
    return {
        "applied": bool(changed),
        "financial": True,
        "ratios": adjusted,
        "changed": changed,
        "note": verdict["treatment"],
        "basis": verdict["basis"],
    }


def rate_impact(sensitivity: Dict, rate_direction: int) -> Dict:
    """금리 방향과 섹터 민감도를 맞춰 한 문장으로 만든다 (해석카드용)."""
    if not sensitivity.get("available") or rate_direction == 0:
        return {"available": False,
                "reason": "업종 민감도나 금리 방향 중 하나가 없어 판정하지 않는다"}
    direction = sensitivity["rate"]["direction"]
    if direction == 0:
        return {"available": True, "grade": GRADE_UNKNOWN,
                "text": f"{sensitivity['name']} 은 금리 방향과의 관계가 뚜렷하지 않다"}
    favorable = direction * rate_direction > 0
    move = "오르는" if rate_direction > 0 else "내리는"
    return {
        "available": True,
        "grade": GRADE_GOOD if favorable else GRADE_WARNING,
        "text": (f"금리가 {move} 국면이고 {sensitivity['name']} 은 "
                 f"{'유리' if favorable else '불리'}한 쪽이다 — {sensitivity['why']}"),
        "basis": sensitivity["basis"],
    }


__all__ = ["risk_free_rate", "risk_free_ratio", "cycle_phase", "sector_sensitivity",
           "is_financial", "stability_verdicts", "rate_impact",
           "SECTOR_SENSITIVITY", "CYCLE_PLAYBOOK", "FINANCIAL_MAJORS",
           "GRADE_GOOD", "GRADE_WARNING", "GRADE_SERIOUS", "GRADE_UNKNOWN"]
