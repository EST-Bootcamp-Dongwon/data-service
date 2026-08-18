"""IND-R 산업 리서치 전용 판단 (명세 §5.4)

공통 상태(H00~H11)는 `stages.py` 에 있고, 여기에는 **산업 리서치에만 있는 것**을 둔다.

    · 밸류체인 · 이익풀 · 병목      (설계서 H04)
    · 시장 규모 · 성장률            (설계서 H05)
    · 수요 · 공급 · 가격            (설계서 H06~H08)
    · 경쟁 구도 · Porter 5 Forces   (설계서 H09)
    · 산업 사이클 · 6/12/24개월     (설계서 H11)

이 워크스트림의 어려움은 **자료가 없다는 것**이다. 명세 §5.4 가 미리 못박아 뒀다.

    "산업 계열(IND-*)은 밸류체인 데이터가 공공 API에 없다.
     KRX 업종코드 + KOSIS 산업생산지수 + DART 표준산업분류로 조립하고,
     부족분은 G-SCOPE 로 명시한 뒤 조건부 결과를 낸다."

그래서 이 파일의 규칙은 하나다 — **있는 것과 없는 것을 갈라 놓는다.**

| 낼 수 있는 것 | 어디서 | 성격 |
|---|---|---|
| 산업 경계·구성 종목 | `industry_map.json` 3,925종목 | 사실 |
| 상장 시가총액 합계·집중도(HHI) | 시장 스냅샷 | 사실 (**시장 규모가 아니다**) |
| 산업 생산지수 추이·CAGR | KOSIS `DT_1JH20201` | 사실 |
| 경기 국면 | ECOS 경기선행·동행 순환변동치 | 사실 |
| 업종 주가지수·모멘텀 | KRX 일별 시세 (시총가중) | 사실 |
| 밸류체인 위치(소재/부품/완제품/유통/서비스) | HuggingFace 제로샷 (U5) | **추정** |
| 이익풀 · TAM/SAM/SOM · 시장점유율 | — | **없음 → G-SCOPE** |

`상장 시가총액 합계` 를 `시장 규모` 라고 부르지 않는 것이 특히 중요하다.
비상장사가 빠져 있고, 시총은 매출이 아니라 기대의 크기다.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional

from ....clients import hf_data, kosis_data
from ....repositories import industry_store, krx_store, snapshot_store
from ..knowledge import macro, portfolio

# KOSIS 전산업생산지수 (실측 확인 — 2000~2026 · 월별 · 5계열)
KOSIS_PRODUCTION = {"org_id": "101", "tbl_id": "DT_1JH20201",
                    "name": "전산업생산지수(원지수)"}

# KSIC 대분류(알파벳) → KOSIS 생산지수 계열.
# 표에 없는 대분류는 **전산업으로 대신하고 그 사실을 밝힌다** — 억지로 갖다 붙이지 않는다.
PRODUCTION_SERIES = {
    "C": "광공업",
    "F": "건설업",
    "G": "서비스업", "H": "서비스업", "I": "서비스업", "J": "서비스업",
    "K": "서비스업", "L": "서비스업", "M": "서비스업", "N": "서비스업",
    "P": "서비스업", "Q": "서비스업", "R": "서비스업", "S": "서비스업",
    "O": "공공행정",
}
PRODUCTION_FALLBACK = "전산업생산지수(농림어업 제외)"

# 밸류체인 위치 후보 — HuggingFace 제로샷에 넘길 한국어 라벨 (U5).
# 라벨을 한국어로 두는 이유는 `hf_data` 주석 4번 참고 (영어 전용 모델은 한국어를 못 읽는다).
VALUE_CHAIN_LABELS = ["원재료·소재", "부품·중간재", "완제품·조립", "장비·설비",
                      "유통·물류", "서비스·플랫폼", "금융"]

# 제로샷 점수가 이보다 낮으면 **판정을 유보한다.**
# 실측 — '반도체 제조업' 이 `완제품·조립 0.34` 로 나왔다. 라벨이 7개라 무작위면 0.14 인데
# 0.34 는 그보다 높을 뿐 "이거다" 라고 말할 만한 값이 아니다. 낮은 점수를 사실처럼 실으면
# 틀린 밸류체인 위치가 리포트에 들어간다.
ZEROSHOT_MIN_SCORE = 0.5

# 집중도 판정 (미 법무부 HHI 기준선을 시가총액에 적용한다 — **점유율이 아니라는 점**을 밝힌다)
HHI_COMPETITIVE = 1500
HHI_CONCENTRATED = 2500

# 업종 지수를 만들 최소 종목 수 · 관측 거래일
MIN_INDEX_MEMBERS = 3
INDEX_DAYS = 250


# ─────────────────────────────────────────────────────────────
# 0. 대상 산업 확정 (설계서 H01·H02)
# ─────────────────────────────────────────────────────────────
def resolve_target(query: str, min_members: int = 5) -> Dict:
    """'261' · '반도체' · '삼성전자' → 분석할 산업을 확정한다.

    KSIC 의 정식 단위는 **세세분류 5자리**다. 그런데 DART `induty_code` 는 자릿수가
    섞여 있어(2~5자리) 5자리로 자르면 401개 그룹 중 66개(16%)만 상장사가 5곳을 넘는다.
    그래서 **입력한 자릿수 그대로 시작해 후보가 모자라면 넓히고, 몇 자리까지 넓혔는지 밝힌다.**
    CORP-R 의 피어 선정(N33)과 같은 규칙이라 두 워크스트림의 '업종' 이 어긋나지 않는다.
    """
    resolved = industry_store.resolve(query)
    if not resolved.get("found"):
        return {"available": False, "reason": resolved.get("reason", "산업을 못 찾았다"),
                "candidates": resolved.get("candidates", [])}

    code = resolved["industry_code"]
    widened: List[str] = []
    rows = industry_store.members(code)
    while len(rows) < min_members and len(code) > 2:
        code = code[:-1]
        rows = industry_store.members(code)
        widened.append(code)

    named = industry_store.name_of(code)
    return {
        "available": bool(rows),
        "industry_code": code,
        "requested_code": resolved["industry_code"],
        "name": named["name"],
        "level": named["level"],
        "section": named["section"],
        "digits": len(code),
        "widened": widened,
        "member_count": len(rows),
        "members": rows,
        "matched_by": resolved.get("kind", ""),
        "candidates": resolved.get("candidates", []),
        "ambiguous": bool(resolved.get("ambiguous")),
        "note": (f"업종 {resolved['industry_code']} 로 시작했으나 구성 종목이 {min_members}곳에 "
                 f"못 미쳐 {code}({named['name']}) 까지 넓혔다 — 산업 경계가 그만큼 넓어졌다"
                 if widened else
                 f"업종 {code} ({named['name']} · {named['level']}) · 상장사 {len(rows)}곳"),
        "limitation": ("KSIC 는 생산 기술 기준 분류라 **밸류체인 순서를 담고 있지 않다.** "
                       "같은 중분류라도 소재와 완제품이 섞여 있다."),
        "basis": "DART 표준산업분류(KSIC) · 변경노트 N32·N33",
    }


# ─────────────────────────────────────────────────────────────
# 1. 밸류체인 · 이익풀 · 병목 (설계서 H04)
# ─────────────────────────────────────────────────────────────
def value_chain(target: Dict, with_zeroshot: bool = True) -> Dict:
    """밸류체인 위치와 인접 업종.

    **사실과 추정을 갈라 낸다.**

        사실  같은 중분류 안에 어떤 소분류들이 있는가 (`industry_map.json`)
        추정  이 산업이 소재·부품·완제품·유통·서비스 중 어디인가 (HF 제로샷 — U5)
        없음  이익풀 규모 · 구간별 마진 · 병목의 실제 위치 → G-SCOPE

    이익풀 수치를 만들지 않는다. 설계서 H04 도 "이익풀 수치가 없으면 정성 후보·반대 근거·
    확인 KPI 를 제공한다" 고 했다.
    """
    code = target.get("industry_code", "")
    if not code:
        return {"available": False, "reason": "업종코드가 없다"}

    division = code[:2]
    siblings = []
    for row in industry_store.directory(digits=3):
        if row["industry_code"].startswith(division) and row["industry_code"] != code[:3]:
            siblings.append(row)
    siblings.sort(key=lambda r: r["industry_code"])

    named = industry_store.name_of(code)
    position: Dict = {"available": False,
                      "reason": "제로샷 분류를 끄고 불렀다 (규칙 기반 위치 추정 없음)"}
    if with_zeroshot and named["name"]:
        # 이름 + 대표 종목을 문장으로 만들어 넘긴다. 코드만 주면 모델이 읽을 것이 없다.
        samples = [m.get("name", "") for m in (target.get("members") or [])[:8]]
        sentence = f"{named['name']}. 대표 기업: {', '.join(s for s in samples if s)}"
        judged = hf_data.classify(sentence, VALUE_CHAIN_LABELS)
        if judged.get("available"):
            confident = judged["top_score"] >= ZEROSHOT_MIN_SCORE
            position = {
                "available": confident,
                "estimated": judged["top"] if confident else "",
                "score": judged["top_score"],
                "rows": judged["rows"],
                "model": judged["model"],
                "input": sentence,
                "kind": "추정",
                "threshold": ZEROSHOT_MIN_SCORE,
                "reason": ("" if confident else
                           f"1순위 '{judged['top']}' 의 점수가 {judged['top_score']:.2f} 로 "
                           f"문턱({ZEROSHOT_MIN_SCORE})에 못 미쳐 판정을 유보한다"),
                "gap_code": "" if confident else "G-SCOPE",
                "limitation": ("제로샷 분류다 — 학습된 산업 분류기가 아니라 문장 유사도로 "
                               "가른 값이다. 후보 라벨 문구를 바꾸면 결과가 달라진다."),
                "basis": "U5 결정 — HuggingFace 추론 API (다국어 NLI 제로샷)",
            }
        else:
            position = {"available": False, "reason": judged.get("reason", ""),
                        "fallback": "밸류체인 위치를 비운 채로 진행한다"}

    return {
        "available": True,
        "industry_code": code,
        "industry_name": named["name"],
        "division": division,
        "division_name": industry_store.name_of(division)["name"],
        "adjacent": [{"industry_code": r["industry_code"], "name": r["name"],
                      "member_count": r["member_count"]} for r in siblings[:12]],
        "adjacent_count": len(siblings),
        "position": position,
        "profit_pool": {
            "available": False,
            "reason": "구간별 이익풀·마진 자료가 공개 API 에 없다",
            "gap_code": "G-SCOPE",
            "qualitative": [
                "상장사 재무로는 '이 산업 안에서 누가 이익을 가져가나' 까지만 볼 수 있다",
                "비상장 소재·부품사가 빠져 있어 상류 구간이 통째로 안 보인다",
            ],
            "next_check": "구간별 매출·영업이익을 회사별 사업보고서에서 사람이 모은다",
        },
        "bottleneck": {
            "available": False,
            "reason": "생산능력·가동률은 회사별 사업보고서에만 있고 산업 단위 집계가 없다",
            "gap_code": "G-DATA",
            "next_check": "구성 종목의 사업보고서 '생산 및 설비' 를 표본으로 확인한다",
        },
        "fact_vs_estimate": {
            "사실": ["같은 중분류 안의 인접 소분류 목록", "구성 종목"],
            "추정": ["밸류체인 위치 (제로샷 분류)"],
            "없음": ["이익풀 규모", "구간별 마진", "병목 위치"],
        },
        "basis": "GIC v15 산업리서치 하네스설계서 H04",
    }


# ─────────────────────────────────────────────────────────────
# 2. 시장 규모 · 성장 (설계서 H05)
# ─────────────────────────────────────────────────────────────
def _cagr(first: Optional[float], last: Optional[float], years: float) -> Optional[float]:
    if not first or not last or years <= 0 or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def production_index(section_letter: str, months: int = 60) -> Dict:
    """KOSIS 전산업생산지수에서 해당 대분류 계열을 뽑는다.

    대분류에 맞는 계열이 없으면 **전산업으로 대신하고 그 사실을 밝힌다.**
    억지로 다른 계열을 갖다 붙이면 다른 산업 이야기를 하게 된다.
    """
    wanted = PRODUCTION_SERIES.get(section_letter)
    substituted = wanted is None
    wanted = wanted or PRODUCTION_FALLBACK

    try:
        payload = kosis_data.fetch_data(KOSIS_PRODUCTION["org_id"], KOSIS_PRODUCTION["tbl_id"],
                                        prd_se="M", period_count=months)
    except Exception as error:
        return {"available": False, "reason": f"KOSIS 조회 실패 — {str(error)[:100]}",
                "gap_code": "G-SOURCE"}

    chart = payload.get("chart") or {}
    series = next((s for s in chart.get("series", []) if s.get("name") == wanted), None)
    if not series:
        return {"available": False,
                "reason": f"KOSIS 표에 '{wanted}' 계열이 없다",
                "gap_code": "G-DATA",
                "series_available": [s.get("name") for s in chart.get("series", [])]}

    labels = chart.get("categories", [])
    values = [v for v in series.get("data", []) if isinstance(v, (int, float))]
    if len(values) < 13:
        return {"available": False, "reason": f"관측 {len(values)}개월로는 추세를 못 낸다",
                "gap_code": "G-DATA"}

    years = (len(values) - 1) / 12
    growth = _cagr(values[0], values[-1], years)
    yoy = None
    if len(values) >= 13 and values[-13]:
        yoy = (values[-1] / values[-13] - 1) * 100

    return {
        "available": True,
        "series_name": wanted,
        "substituted": substituted,
        "substitute_note": (f"KSIC 대분류 '{section_letter}' 에 맞는 생산지수 계열이 없어 "
                            f"'{PRODUCTION_FALLBACK}' 로 대신했다 — 이 산업만의 지표가 아니다"
                            if substituted else ""),
        "labels": labels[-len(values):],
        "values": values,
        "latest": values[-1],
        "latest_period": labels[-1] if labels else "",
        "cagr_pct": None if growth is None else round(growth, 2),
        "cagr_years": round(years, 2),
        "yoy_pct": None if yoy is None else round(yoy, 2),
        # 설계서 H05 가 요구하는 CAGR 검산 — 공식·시작·종료값을 다 보여 준다
        "cagr_check": {
            "formula": "(마지막/처음)^(1/연수) - 1",
            "first": values[0], "last": values[-1], "years": round(years, 2),
            "first_period": labels[-len(values)] if labels else "",
            "last_period": labels[-1] if labels else "",
        },
        "source": f"KOSIS {KOSIS_PRODUCTION['tbl_id']} {KOSIS_PRODUCTION['name']}",
        "limitation": ("생산지수는 물량 기준이라 가격 변화가 빠져 있다. "
                       "그리고 이 계열은 대분류 단위라 이 산업만의 움직임이 아니다."),
        "basis": "GIC v15 산업리서치 하네스설계서 H05",
    }


def market_size(target: Dict, production: Dict) -> Dict:
    """'시장 규모' 대신 낼 수 있는 것을 낸다.

    ⚠️ **상장 시가총액 합계를 시장 규모라고 부르지 않는다.** 비상장사가 빠져 있고,
    시총은 매출이 아니라 기대의 크기다. 그래서 이름을 따로 붙이고 한계를 함께 낸다.
    TAM/SAM/SOM 은 정의 자체를 못 세우므로 `G-SCOPE` 로 남긴다.
    """
    rows = []
    caps, _revenues = [], []
    for member in target.get("members", []):
        snapshot = snapshot_store.get(member.get("code", ""))
        if not snapshot:
            continue
        cap = snapshot.get("market_cap") or 0
        rows.append({"code": snapshot.get("code"), "name": snapshot.get("name"),
                     "market_cap": cap, "per": snapshot.get("per"),
                     "pbr": snapshot.get("pbr"), "roe": snapshot.get("roe"),
                     "r250": snapshot.get("r250"), "close": snapshot.get("close")})
        if cap:
            caps.append(cap)

    rows.sort(key=lambda r: -(r["market_cap"] or 0))
    missing = len(target.get("members", [])) - len(rows)

    return {
        "available": bool(rows),
        "listed_market_cap": sum(caps),
        "listed_count": len(rows),
        "unmatched_members": missing,
        "median_cap": median(caps) if caps else None,
        "rows": rows,
        "as_of": snapshot_store.as_of(),
        "production": production,
        "tam_sam_som": {
            "available": False,
            "reason": "시장 정의(제품·지역·최종수요처)를 세울 원자료가 공개 API 에 없다",
            "gap_code": "G-SCOPE",
            "next_check": "산업협회·시장조사기관 보고서에서 시장 정의와 규모를 사람이 확인한다",
        },
        "label": "상장 시가총액 합계",
        "limitation": ("**시장 규모가 아니다.** 비상장사가 빠져 있고 시가총액은 매출이 아니라 "
                       f"기대의 크기다. 구성 종목 {len(target.get('members', []))}곳 중 "
                       f"{missing}곳은 시장 스냅샷에 없어 합계에서 빠졌다."),
        "basis": "GIC v15 산업리서치 하네스설계서 H05 · 시장 스냅샷 §2.3",
    }


# ─────────────────────────────────────────────────────────────
# 3. 업종 지수 · 수급 (설계서 H06~H08)
# ─────────────────────────────────────────────────────────────
def industry_index(target: Dict, days: int = INDEX_DAYS, top: int = 20) -> Dict:
    """구성 종목 시가총액 가중 업종 지수 (100 기준).

    전 종목을 다 넣으면 KRX 캐시를 수백 번 읽는다. **시총 상위 `top` 곳**으로 만들고
    그 사실과 커버리지(상위 N곳이 업종 시총의 몇 %인가)를 밝힌다.
    """
    members = []
    for member in target.get("members", []):
        snapshot = snapshot_store.get(member.get("code", ""))
        if snapshot and (snapshot.get("market_cap") or 0) > 0:
            members.append(snapshot)
    if len(members) < MIN_INDEX_MEMBERS:
        return {"available": False,
                "reason": f"시총을 아는 구성 종목이 {len(members)}곳뿐이라 업종 지수를 못 만든다 "
                          f"(최소 {MIN_INDEX_MEMBERS}곳)"}

    members.sort(key=lambda r: -(r.get("market_cap") or 0))
    picked = members[:top]
    total_cap = sum(m.get("market_cap") or 0 for m in members)
    picked_cap = sum(m.get("market_cap") or 0 for m in picked)

    closes: Dict[str, List[float]] = {}
    dates: List[str] = []
    for member in picked:
        series = krx_store.series(member["code"], days=days)
        values = [r.get("close") for r in series if r.get("close")]
        if len(values) < 30:
            continue
        closes[member["code"]] = values
        if len(series) > len(dates):
            dates = [r.get("date") for r in series]

    if len(closes) < MIN_INDEX_MEMBERS:
        return {"available": False,
                "reason": f"시세를 받은 종목이 {len(closes)}곳뿐이라 업종 지수를 못 만든다"}

    length = min(len(v) for v in closes.values())
    weights = {code: (next(m["market_cap"] for m in picked if m["code"] == code) or 0)
               for code in closes}
    weight_sum = sum(weights.values()) or 1

    index_values: List[float] = []
    for position in range(length):
        level = 0.0
        for code, values in closes.items():
            window = values[-length:]
            level += (window[position] / window[0]) * (weights[code] / weight_sum)
        index_values.append(level * 100)

    performance = portfolio.performance(index_values)
    return {
        "available": True,
        "values": [round(v, 2) for v in index_values],
        "dates": dates[-length:] if len(dates) >= length else dates,
        "members_used": len(closes),
        "members_total": len(members),
        "coverage_pct": round(picked_cap / total_cap * 100, 1) if total_cap else None,
        "observations": length,
        "source": krx_store.source(),
        "performance": performance,
        "momentum": {
            "d20": round((index_values[-1] / index_values[-21] - 1) * 100, 2)
                   if length > 21 else None,
            "d60": round((index_values[-1] / index_values[-61] - 1) * 100, 2)
                   if length > 61 else None,
            "d250": round((index_values[-1] / index_values[0] - 1) * 100, 2)
                    if length > 200 else None,
        },
        "method": f"시가총액 가중 · 시총 상위 {len(closes)}곳 · 첫날 100 기준",
        "limitation": (f"업종 시총의 {round(picked_cap / total_cap * 100, 1) if total_cap else '?'}% 를 "
                       "덮는 상위 종목만 넣었다. 신규 상장·상장폐지는 반영하지 않아 생존 편향이 있다."),
        "basis": "GIC v15 산업리서치 하네스설계서 H06~H08",
    }


def supply_demand(target: Dict, production: Dict, index_row: Dict) -> Dict:
    """수요·공급 신호를 견준다 (설계서 H06·H07).

    산업 단위 생산능력·가동률 자료가 없으므로 **낼 수 있는 것만** 낸다 —
    수요 쪽은 생산지수, 공급 쪽은 상장사 수와 집중도다. 진짜 수급 불일치는 못 낸다.
    """
    signals: List[Dict] = []
    if production.get("available"):
        yoy = production.get("yoy_pct")
        signals.append({
            "kind": "수요",
            "name": f"{production['series_name']} 생산지수",
            "value": production.get("latest"),
            "yoy_pct": yoy,
            "direction": "확대" if (yoy or 0) > 0 else "축소" if yoy is not None else "판정불가",
            "as_of": production.get("latest_period"),
            "source": production.get("source"),
        })
    if index_row.get("available"):
        momentum = index_row.get("momentum", {})
        signals.append({
            "kind": "가격",
            "name": "업종 주가지수 모멘텀",
            "value": index_row["values"][-1] if index_row.get("values") else None,
            "d60_pct": momentum.get("d60"),
            "d250_pct": momentum.get("d250"),
            "direction": ("상승" if (momentum.get("d60") or 0) > 0 else "하락"
                          if momentum.get("d60") is not None else "판정불가"),
            "source": f"KRX {index_row.get('source')}",
        })

    mismatch = None
    demand = next((s for s in signals if s["kind"] == "수요"), None)
    price = next((s for s in signals if s["kind"] == "가격"), None)
    if demand and price and demand.get("yoy_pct") is not None and price.get("d250_pct") is not None:
        same = (demand["yoy_pct"] > 0) == (price["d250_pct"] > 0)
        mismatch = {
            "aligned": same,
            "text": (f"생산 {demand['yoy_pct']:+.1f}%(YoY) 와 주가 {price['d250_pct']:+.1f}%(1년) 의 "
                     f"방향이 {'같다' if same else '다르다'}"),
            "meaning": ("실물과 주가가 같은 방향이다 — 다만 같이 움직였다는 것이 인과는 아니다"
                        if same else
                        "실물과 주가의 방향이 갈린다 — 기대가 앞서 있거나 뒤처져 있을 수 있다"),
            "caution": "상관을 인과로 읽지 않는다. 제3의 변수(금리·환율)가 둘 다 움직였을 수 있다.",
        }

    return {
        "available": bool(signals),
        "signals": signals,
        "mismatch": mismatch,
        "capacity": {
            "available": False,
            "reason": "산업 단위 생산능력·가동률 집계가 공개 API 에 없다",
            "gap_code": "G-DATA",
            "next_check": "구성 종목 사업보고서의 '생산능력·가동률' 을 표본으로 모은다",
        },
        "price": {
            "available": False,
            "reason": "산업 단위 ASP·판가 지표가 없다 (품목별 단가는 회사 원문에만 있다)",
            "gap_code": "G-DATA",
            "next_check": "대표 기업의 '매출 및 수주상황' 표에서 품목별 단가를 확인한다",
        },
        "basis": "GIC v15 산업리서치 하네스설계서 H06·H07·H08",
    }


# ─────────────────────────────────────────────────────────────
# 4. 경쟁 구도 (설계서 H09)
# ─────────────────────────────────────────────────────────────
def competition(size_row: Dict) -> Dict:
    """집중도와 상대 위치.

    ⚠️ **시장점유율이 아니다.** 시가총액 기준 집중도라 매출 점유율과 다르다.
    설계서 H09 도 "점유율이 없으면 비교 가능한 운영·구조 지표와 실사 질문을 제공한다" 고 했다.
    """
    rows = [r for r in size_row.get("rows", []) if (r.get("market_cap") or 0) > 0]
    if len(rows) < 2:
        return {"available": False, "reason": "시총을 아는 종목이 둘 미만이라 집중도를 못 낸다"}

    total = sum(r["market_cap"] for r in rows)
    shares = [(r["name"], r["market_cap"] / total * 100) for r in rows]
    shares.sort(key=lambda kv: -kv[1])
    hhi = sum(share ** 2 for _, share in shares)
    cr3 = sum(share for _, share in shares[:3])

    if hhi < HHI_COMPETITIVE:
        level, why = "경쟁적", f"HHI {hhi:,.0f} — {HHI_COMPETITIVE:,} 미만"
    elif hhi < HHI_CONCENTRATED:
        level, why = "중간 집중", f"HHI {hhi:,.0f} — {HHI_COMPETITIVE:,}~{HHI_CONCENTRATED:,}"
    else:
        level, why = "고집중", f"HHI {hhi:,.0f} — {HHI_CONCENTRATED:,} 이상"

    return {
        "available": True,
        "hhi": round(hhi, 1),
        "cr3_pct": round(cr3, 1),
        "level": level,
        "why": why,
        "top": [{"name": name, "share_pct": round(share, 2)} for name, share in shares[:8]],
        "member_count": len(rows),
        "porter": _porter(hhi, len(rows)),
        "limitation": ("**시가총액 기준이라 시장점유율이 아니다.** 비상장사가 빠져 있고, "
                       "시총 비중이 큰 회사가 매출도 큰 것은 아니다."),
        "next_check": "대표 기업 사업보고서의 시장점유율 표를 사람이 확인한다",
        "basis": "GIC v15 산업리서치 하네스설계서 H09 · HHI 기준선은 경쟁정책 관행",
    }


def _porter(hhi: float, member_count: int) -> Dict:
    """Porter 5 Forces — **근거가 있는 항목만** 채운다.

    다섯 힘 중 공개 자료로 말할 수 있는 것은 '기존 경쟁' 하나뿐이다.
    나머지 넷을 그럴듯한 문장으로 채우면 근거 없는 결론이 된다 (설계서 §7 `ERR-UNSOURCED`).
    """
    rivalry = ("집중도가 낮아 기존 경쟁이 치열할 가능성이 크다" if hhi < HHI_COMPETITIVE
               else "소수 기업에 집중돼 있다" if hhi >= HHI_CONCENTRATED
               else "중간 수준의 집중도다")
    return {
        "기존 경쟁": {"available": True, "finding": f"{rivalry} (상장사 {member_count}곳)",
                   "evidence": "시가총액 집중도(HHI)", "kind": "사실 기반 추정"},
        "신규 진입 위협": {"available": False, "reason": "진입 장벽(인허가·설비·기술)을 잴 자료가 없다",
                     "gap_code": "G-SCOPE"},
        "공급자 교섭력": {"available": False, "reason": "원재료 조달 구조 자료가 없다",
                    "gap_code": "G-SCOPE"},
        "구매자 교섭력": {"available": False, "reason": "고객 집중도 자료가 없다 (회사별 원문에만 있다)",
                    "gap_code": "G-SCOPE"},
        "대체재 위협": {"available": False, "reason": "대체 기술·제품 정의를 세울 자료가 없다",
                   "gap_code": "G-SCOPE"},
        "note": "다섯 힘 중 근거가 있는 것은 하나다. 나머지를 문장으로 채우지 않는다.",
    }


# ─────────────────────────────────────────────────────────────
# 5. 산업 사이클 · 시나리오 (설계서 H11)
# ─────────────────────────────────────────────────────────────
# 6/12/24개월 — 설계서 H11 이 정한 시간축
SCENARIO_HORIZONS = (6, 12, 24)


def cycle(target: Dict, production: Dict, index_row: Dict) -> Dict:
    """산업 국면 판정 + 6/12/24개월 조건부 시나리오.

    경기 국면(ECOS)·생산지수 방향·업종 주가 모멘텀 셋을 **투표**로 모은다.
    셋이 갈리면 국면을 하나로 좁히지 않는다 (설계서 H11 "가능한 국면을 복수 유지").
    **근거 없는 확률을 만들지 않는다** — 조건만 적는다.
    """
    economy = macro.cycle_phase()
    votes: List[Dict] = []

    if economy.get("available"):
        votes.append({"source": "경기선행지수 순환변동치 (ECOS)",
                      "signal": economy["phase"],
                      "up": economy["phase"] in ("확장", "저점"),
                      "detail": economy["why"]})
    if production.get("available") and production.get("yoy_pct") is not None:
        votes.append({"source": f"{production['series_name']} 생산지수 (KOSIS)",
                      "signal": "확대" if production["yoy_pct"] > 0 else "축소",
                      "up": production["yoy_pct"] > 0,
                      "detail": f"전년 동월 대비 {production['yoy_pct']:+.1f}%"})
    if index_row.get("available") and index_row.get("momentum", {}).get("d60") is not None:
        momentum = index_row["momentum"]["d60"]
        votes.append({"source": "업종 주가지수 60일 모멘텀 (KRX)",
                      "signal": "상승" if momentum > 0 else "하락",
                      "up": momentum > 0,
                      "detail": f"60거래일 {momentum:+.1f}%"})

    if not votes:
        return {"available": False,
                "reason": "경기·생산·주가 신호를 하나도 받지 못해 국면을 판정하지 않는다",
                "candidates": list(macro.CYCLE_PLAYBOOK.keys()),
                "gap_code": "G-DATA"}

    ups = sum(1 for v in votes if v["up"])
    downs = len(votes) - ups
    if ups == len(votes):
        phase, confidence = "확장 국면", "medium"
    elif downs == len(votes):
        phase, confidence = "수축 국면", "medium"
    else:
        phase, confidence = "전환 구간 (신호가 갈린다)", "low"

    sensitivity = macro.sector_sensitivity(target.get("industry_code", ""))
    scenarios = []
    for horizon in SCENARIO_HORIZONS:
        scenarios.append({
            "horizon_months": horizon,
            "bull": {
                "condition": (f"{production.get('series_name', '생산')}지수가 "
                              f"{horizon}개월 연속 전년 대비 플러스를 유지하고, "
                              "경기선행지수가 100 위에서 오른다"),
                "watch": "월별 생산지수 · 경기선행지수 순환변동치",
            },
            "base": {
                "condition": "생산지수와 경기지표가 지금 방향을 유지한다",
                "watch": "업종 주가지수 60일 모멘텀",
            },
            "bear": {
                "condition": ("경기선행지수가 100 아래로 내려가거나 "
                              "생산지수가 2분기 연속 전년 대비 마이너스가 된다"),
                "watch": "생산지수 YoY · 금리 방향",
            },
            "flip_kpi": "생산지수 YoY 부호 전환 · 경기선행지수의 100선 교차",
        })

    return {
        "available": True,
        "phase": phase,
        "confidence": confidence,
        "votes": votes,
        "vote_summary": f"신호 {len(votes)}개 중 상방 {ups} · 하방 {downs}",
        "economy": economy,
        "sensitivity": sensitivity,
        "scenarios": scenarios,
        "horizons": list(SCENARIO_HORIZONS),
        "limitation": ("**확률을 만들지 않았다.** 산업 사이클의 길이를 추정할 표본이 없어 "
                       "조건과 확인 지표만 적는다 (설계서 H11 '근거 없는 확률 배제')."),
        "basis": "GIC v15 산업리서치 하네스설계서 H11 · 08강 03.md 경기 4국면",
    }


# ─────────────────────────────────────────────────────────────
# 입구 — H04 가 부른다
# ─────────────────────────────────────────────────────────────
def analyze(target: Dict, with_zeroshot: bool = True) -> Dict:
    """밸류체인 → 시장·수급 → 사이클 3단을 한 번에 만든다 (명세 §5.4)."""
    section = (target.get("section") or {}).get("letter", "")
    chain = value_chain(target, with_zeroshot=with_zeroshot)
    production = production_index(section)
    size = market_size(target, production)
    index_row = industry_index(target)
    flow = supply_demand(target, production, index_row)
    rivalry = competition(size)
    phase = cycle(target, production, index_row)
    return {
        "target": {k: v for k, v in target.items() if k != "members"},
        "member_count": target.get("member_count", 0),
        "value_chain": chain,
        "market": size,
        "index": index_row,
        "supply_demand": flow,
        "competition": rivalry,
        "cycle": phase,
        "gaps": _gap_list(chain, size, flow, rivalry, phase),
    }


def _gap_list(*blocks: Dict) -> List[Dict]:
    """각 블록이 남긴 '없음' 을 모아 H04 가 Gap 으로 발행할 목록을 만든다."""
    found: List[Dict] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            if node.get("gap_code") and not node.get("available", False):
                found.append({"code": node["gap_code"], "affected": path,
                              "reason": node.get("reason", ""),
                              "next_check": node.get("next_check", "")})
            for key, value in node.items():
                if isinstance(value, (dict, list)) and key not in ("rows", "values", "labels"):
                    walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    for index, block in enumerate(blocks):
        walk(block, ["밸류체인", "시장 규모", "수급", "경쟁", "사이클"][index])
    return found
