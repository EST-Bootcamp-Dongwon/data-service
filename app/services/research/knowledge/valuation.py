"""08강 상대가치평가(08.md)를 판정 함수로 옮긴 것 (명세 §5.6)

강의가 준 것 세 가지를 그대로 코드로 만든다.

1. **멀티플 선택 플로우차트** (08.md 260행)
       흑자인가? ─아니오→ 매출 성장 중인가? ─예→ PSR
                 └─예→ 자산집약 업종? ─예→ PBR
                        └─아니오→ 부채구조 복잡? ─예→ EV/EBITDA
                                  └─아니오→ 고성장 기대? ─예→ PER+PEG · 아니오→ PER

2. **업종별 대략 범위** (08.md 244행 표) — 은행 PBR 0.5~1.5 · 반도체 EV/EBITDA 10~25 ·
   플랫폼 PER 20~50 · 소비재 PER 10~20 · 바이오 PSR

3. **PER × 성장률 4분면** (08.md 180행) — 고성장 프리미엄 / 버블 위험 / 가치주 / 숨겨진 저평가

그리고 강의의 경고를 코드가 강제한다.

    "멀티플이 낮다고 무조건 저평가가 아니라, 성장 둔화나 재무 위험이 반영된 결과일 수 있다"

    → `screen()` 은 저PER 을 곧바로 '저평가' 라고 부르지 않는다. 성장률·ROE 를 함께 보고
      설명이 붙을 때만 저평가라고 쓴다. 이것이 명세 §5.4 의 **"약한 논리 시 밸류에이션 차단"** 이다.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional, Sequence

# 업종별 대략 범위 — 08.md 244행 표 그대로.
# 열쇠는 KSIC 2자리 대분류다 (`data/industry_map.json` 의 industry_code 앞 두 자리).
SECTOR_MULTIPLES = {
    "64": {"name": "금융업", "primary": "PBR", "low": 0.5, "high": 1.5,
           "why": "자산 가치 중심 — 은행·금융"},
    "65": {"name": "보험업", "primary": "PBR", "low": 0.5, "high": 1.5,
           "why": "자산 가치 중심 — 보험"},
    "66": {"name": "금융지원", "primary": "PBR", "low": 0.5, "high": 1.5,
           "why": "자산 가치 중심 — 금융지원"},
    "26": {"name": "전자·반도체", "primary": "EV/EBITDA", "low": 10, "high": 25,
           "why": "사이클 평탄화 목적"},
    "58": {"name": "출판·소프트웨어", "primary": "PER", "low": 20, "high": 50,
           "why": "고성장 프리미엄 — 플랫폼·IT"},
    "63": {"name": "정보서비스", "primary": "PER", "low": 20, "high": 50,
           "why": "고성장 프리미엄 — 플랫폼·IT"},
    "62": {"name": "소프트웨어 개발", "primary": "PER", "low": 20, "high": 50,
           "why": "고성장 프리미엄 — 플랫폼·IT"},
    "21": {"name": "의약품", "primary": "PSR", "low": None, "high": None,
           "why": "적자가 흔해 PER 을 쓰지 않는다 — 파이프라인 가치"},
    "47": {"name": "소매업", "primary": "PER", "low": 10, "high": 20,
           "why": "안정적 이익 — 소비재·유통"},
    "46": {"name": "도매업", "primary": "PER", "low": 10, "high": 20,
           "why": "안정적 이익 — 소비재·유통"},
}

ASSET_HEAVY_SECTORS = {"64", "65", "66", "68"}          # 은행·보험·금융지원·부동산
HIGH_GROWTH_SECTORS = {"58", "62", "63", "21", "72"}    # 플랫폼·소프트웨어·바이오·연구개발


def sector_guide(industry_code: str) -> Dict:
    """업종코드 → 어떤 멀티플을 볼 것인가 (08.md 업종별 기준표)."""
    major = str(industry_code or "")[:2]
    guide = SECTOR_MULTIPLES.get(major)
    if not guide:
        return {"major": major, "name": "", "primary": "PER", "low": None, "high": None,
                "why": "강의 표에 없는 업종 — 기본값 PER 로 두되 피어 비교를 우선한다",
                "in_lecture": False}
    return {**guide, "major": major, "in_lecture": True}


def choose_multiple(net_income: Optional[float], revenue_growth: Optional[float],
                    industry_code: str = "", debt_ratio: Optional[float] = None,
                    expected_growth: Optional[float] = None) -> Dict:
    """어떤 멀티플로 볼지 고른다 — 08.md 260행 플로우차트를 그대로 밟는다.

    고른 이유(`path`)를 함께 돌려준다. 리포트가 "왜 PBR 로 봤나" 를 설명해야 하기 때문이다.
    """
    major = str(industry_code or "")[:2]
    path: List[str] = []

    if net_income is None:
        path.append("순이익을 모른다")
        return {"multiple": "PER", "path": path, "confident": False,
                "why": "이익 여부를 몰라 기본값(PER)을 두었다 — 확인 필요"}

    if net_income <= 0:
        path.append("적자다")
        if revenue_growth is not None and revenue_growth > 0:
            path.append("매출은 성장 중이다")
            return {"multiple": "PSR", "path": path, "confident": True,
                    "why": "적자이지만 매출이 늘어 PSR 로 본다 (초기 성장·바이오)"}
        path.append("매출도 안 는다")
        return {"multiple": "자산가치", "path": path, "confident": True,
                "why": "적자에 성장도 없다 — 멀티플보다 자산가치로 봐야 한다 (08.md 재검토 경로)"}

    path.append("흑자다")
    if major in ASSET_HEAVY_SECTORS:
        path.append("자산집약 업종이다")
        return {"multiple": "PBR", "path": path, "confident": True,
                "why": "은행·보험·부동산은 자산 해산가치로 비교한다"}

    if debt_ratio is not None and debt_ratio >= 200:
        path.append("부채구조가 무겁다 (부채비율 200% 이상)")
        return {"multiple": "EV/EBITDA", "path": path, "confident": True,
                "why": "시가총액만 보면 인수 부담을 놓친다 — 순차입금을 더해 본다"}

    if major in HIGH_GROWTH_SECTORS or (expected_growth is not None and expected_growth >= 20):
        path.append("고성장 기대 구간이다")
        return {"multiple": "PER+PEG", "path": path, "confident": True,
                "why": "높은 PER 이 성장으로 정당화되는지 PEG 로 함께 본다"}

    path.append("일반 구간이다")
    return {"multiple": "PER", "path": path, "confident": True,
            "why": "업종 평균과 PER 로 비교한다"}


def peer_position(value: Optional[float], peer_values: Sequence[Optional[float]],
                  metric: str = "PER") -> Dict:
    """피어 무리 안에서 어디쯤인가 — 중앙값 대비 위치와 백분위.

    평균이 아니라 **중앙값**을 쓴다. 적자 직전 기업의 PER 수백 배 하나가 평균을 통째로 흔든다.
    """
    clean = [v for v in peer_values if isinstance(v, (int, float)) and v > 0]
    if value is None or value <= 0 or len(clean) < 3:
        return {"available": False,
                "reason": f"{metric} 이 없거나 피어가 3곳 미만이라 상대 위치를 못 낸다",
                "peer_count": len(clean)}

    mid = median(clean)
    below = sum(1 for v in clean if v < value)
    percentile = below / len(clean) * 100
    premium = (value / mid - 1) * 100
    if premium > 20:
        stance = "프리미엄"
    elif premium < -20:
        stance = "디스카운트"
    else:
        stance = "피어 수준"
    return {
        "available": True,
        "metric": metric,
        "value": round(value, 2),
        "peer_median": round(mid, 2),
        "peer_min": round(min(clean), 2),
        "peer_max": round(max(clean), 2),
        "peer_count": len(clean),
        "premium_pct": round(premium, 1),
        "percentile": round(percentile, 1),
        "stance": stance,
        "basis": "08강 08.md — 같은 업종·같은 성장 단계끼리 비교",
    }


def quadrant(per: Optional[float], growth: Optional[float],
             per_pivot: float = 20.0, growth_pivot: float = 10.0) -> Dict:
    """PER × 성장률 4분면 (08.md 180행 quadrantChart).

    낮은 PER 을 곧바로 '싸다' 라고 부르지 않기 위한 장치다. 성장률 축이 함께 있어야
    '가치주' 와 '싼 데는 이유가 있는 것' 이 갈린다.
    """
    if per is None or growth is None or per <= 0:
        return {"available": False, "reason": "PER 또는 성장률이 없어 4분면 판정을 못 한다"}

    high_per = per >= per_pivot
    high_growth = growth >= growth_pivot
    if high_per and high_growth:
        name, meaning = "고성장 프리미엄", "높은 PER 이 성장으로 설명된다"
    elif high_per and not high_growth:
        name, meaning = "버블 위험", "성장 없이 PER 만 높다 — 근거를 따져야 한다"
    elif not high_per and high_growth:
        name, meaning = "숨겨진 저평가", "성장하는데 PER 이 낮다 — 왜 그런지 확인이 필요하다"
    else:
        name, meaning = "가치주 구간", "저PER·저성장 — 싼 데는 이유가 있을 수 있다"
    return {
        "available": True,
        "quadrant": name,
        "meaning": meaning,
        "per": round(per, 2),
        "growth": round(growth, 2),
        "pivots": {"per": per_pivot, "growth": growth_pivot},
        "basis": "08강 08.md — PER 수준 vs 성장률 4분면",
    }


def screen(per: Optional[float], peer: Dict, growth: Optional[float],
           roe_value: Optional[float]) -> Dict:
    """저평가라고 부를 수 있는지 판정한다 — 명세 §5.4 "약한 논리 시 밸류에이션 차단".

    디스카운트가 났다고 곧장 저평가라고 쓰지 않는다. 강의(08.md 258행)가 경고한 대로
    **성장 둔화나 재무 위험이 이미 반영된 결과**일 수 있기 때문이다.
    셋 중 둘 이상이 받쳐 줄 때만 '저평가 후보' 라고 쓴다.
    """
    reasons: List[str] = []
    blockers: List[str] = []

    if not peer.get("available"):
        return {"verdict": "판정불가", "reasons": [],
                "blockers": [peer.get("reason", "피어 비교가 없다")],
                "allow_valuation": False,
                "basis": "08강 08.md — 피어 없이 저평가를 말하지 않는다"}

    discount = peer.get("premium_pct", 0) < -20
    if discount:
        reasons.append(f"피어 중앙값 대비 {peer['premium_pct']:.1f}% 낮다")
    else:
        blockers.append(f"피어 대비 {peer['premium_pct']:+.1f}% — 뚜렷한 디스카운트가 아니다")

    if growth is None:
        blockers.append("성장률을 모른다")
    elif growth > 0:
        reasons.append(f"매출이 성장 중이다 ({growth:+.1f}%)")
    else:
        blockers.append(f"매출이 줄고 있다 ({growth:+.1f}%) — 디스카운트에 이유가 있다")

    if roe_value is None:
        blockers.append("ROE 를 모른다")
    elif roe_value >= 10:
        reasons.append(f"ROE {roe_value:.1f}% 로 자본효율이 기준을 넘는다")
    else:
        blockers.append(f"ROE {roe_value:.1f}% — 10% 기준에 못 미친다")

    if discount and len(reasons) >= 2:
        verdict_text = "저평가 후보"
    elif discount:
        verdict_text = "디스카운트 — 이유 있음"
    elif peer.get("premium_pct", 0) > 20:
        verdict_text = "프리미엄"
    else:
        verdict_text = "피어 수준"

    return {
        "verdict": verdict_text,
        "reasons": reasons,
        "blockers": blockers,
        # 논리가 약하면 밸류에이션 결론을 내지 못하게 막는다 (명세 §5.4)
        "allow_valuation": len(reasons) >= 2,
        "basis": "08강 08.md — 멀티플이 낮다고 무조건 저평가가 아니다",
    }


def band_target(current_price: Optional[float], eps: Optional[float],
                peer_median_per: Optional[float]) -> Dict:
    """피어 멀티플 밴드로 주당 가치 범위를 만든다 (15장 양식 slot 14).

    **예측이 아니라 연습이다.** 양식매핑가이드가 "주당 가치 범위는 밸류에이션 연습 결과로
    표현한다" 고 못박았으므로 문구도 그렇게 낸다.
    """
    if not eps or eps <= 0 or not peer_median_per or peer_median_per <= 0:
        return {"available": False,
                "reason": "EPS 가 없거나 적자여서 PER 기반 가치 범위를 못 만든다"}

    low = eps * peer_median_per * 0.8
    base = eps * peer_median_per
    high = eps * peer_median_per * 1.2
    upside = None if not current_price else (base / current_price - 1) * 100
    return {
        "available": True,
        "low": round(low), "base": round(base), "high": round(high),
        "eps": round(eps, 1),
        "peer_median_per": round(peer_median_per, 2),
        "current_price": current_price,
        "upside_pct": None if upside is None else round(upside, 1),
        "method": "피어 중앙값 PER × EPS (밴드는 ±20%)",
        "caveat": "투자 권유가 아니라 밸류에이션 연습 결과다. 피어 선정이 바뀌면 범위도 바뀐다.",
    }
