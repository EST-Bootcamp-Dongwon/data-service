"""IND-TP 산업 Top Pick 전용 판단 (명세 §5.4)

산업 안에서 후보를 골라 **점수 · missing penalty · 민감도 · 순위 안정성**을 낸다.
GIC v15 산업TopPick 하네스설계서 §7 의 계산 계약을 식 그대로 옮긴다.

    valid_weight      = 검증 가능한 근거가 있는 평가항목 가중치의 합
    coverage          = valid_weight ÷ 100%
    observed_score    = Σ(유효 항목 점수 × 가중치) ÷ valid_weight
    missing_ratio     = 1 - valid_weight ÷ 100%
    missing_penalty   = missing_ratio × penalty_coefficient      (기본 1.5)
    adjusted_score    = max(0, observed_score - missing_penalty)

가중치도 설계서 §7.1 그대로다 — 성장성 25 · 수익성 20 · 밸류에이션 20 ·
산업 모멘텀 15 · 재무 안정성 10 · Red Team 10.

**후보 수 5~10 을 하드 제약으로 쓰지 않는다** (M5 결정)
------------------------------------------------------
설계서는 후보를 5~10개로 못박고 그 밖이면 불변조건 위반(`ITP-T02`)으로 본다.
그런데 실데이터를 세어 보면 그 규칙이 KSIC 와 맞지 않는다.

    KSIC 세세분류(5자리) 401그룹 중 상장사 5곳 이상 …  66개 (16%)
    소분류(3자리)        166그룹 중                    92개 (55%)
    중분류(2자리)         63그룹 중                    50개 (79%)

정식 단위인 5자리로 자르면 **산업의 84% 에서 IND-TP 가 통째로 죽는다.**
규칙의 원래 목적은 체리피킹 방지인데, 그건 개수 제한이 아니라 **후보군 완전성 공개**로
지키는 것이 맞다. 그래서

    · 후보군 **전수**를 세어 `universe_total` 로 공개하고
    · 점수 대상은 상위 `MAX_CANDIDATES` 곳으로 자르되 **무엇을 왜 잘랐는지** 적고
    · 5곳 미만이면 죽이지 않고 `partial-continue` + `G-SCOPE`(대표성 경고)로 넘긴다.

이 판단과 근거는 변경노트에 남겨 M7 프롬프트 버전업 대상으로 올린다.
"""
from __future__ import annotations

from statistics import fmean, median
from typing import Dict, List, Optional, Sequence, Tuple

from ....repositories import industry_store, krx_store, snapshot_store
from ..knowledge import financials, macro, portfolio, valuation

# 설계서 §7.1 기본 가중치 (합 100)
WEIGHTS = [
    {"key": "growth", "name": "성장성", "weight": 25, "direction": +1},
    {"key": "profitability", "name": "수익성", "weight": 20, "direction": +1},
    {"key": "valuation", "name": "밸류에이션 매력도", "weight": 20, "direction": +1},
    {"key": "momentum", "name": "산업 모멘텀 노출도", "weight": 15, "direction": +1},
    {"key": "stability", "name": "재무 안정성", "weight": 10, "direction": +1},
    {"key": "redteam", "name": "Red Team 통과 가능성", "weight": 10, "direction": +1},
]
WEIGHT_BY_KEY = {w["key"]: w for w in WEIGHTS}
TOTAL_WEIGHT = sum(w["weight"] for w in WEIGHTS)

# 0/3/5 앵커 (설계서 §7.1)
ANCHOR = {0: "검증 가능한 근거가 없거나 후보군 내 매우 취약",
          3: "중립 또는 후보군 평균",
          5: "검증 가능한 근거상 후보군 내 매우 우수"}

PENALTY_COEFFICIENT = 1.5                 # 설계서 §7.2 기본값
PENALTY_SCENARIOS = (1.0, 1.5, 2.0)       # 설계서 §7.3-3 비교 대상

# 후보 수 — 설계서의 5~10 을 **권장 범위**로 쓴다 (위 docstring 참고)
RECOMMENDED_MIN = 5
MAX_CANDIDATES = 10

# 설계서 §7.3 민감도
WEIGHT_DELTA = 5                          # ±5%p
SCORE_DELTA = 1                           # 원점수 ±1 (0~5 밖으로 나가지 않는다)

# 설계서 §7.3-7 순위 안정성 판정
STABILITY_HIGH = 0.80
STABILITY_MID = 0.60


# ─────────────────────────────────────────────────────────────
# 1. 후보군 (설계서 S03)
# ─────────────────────────────────────────────────────────────
def candidates(target: Dict, want: int = MAX_CANDIDATES) -> Dict:
    """산업 안에서 점수를 매길 후보를 고른다.

    시가총액 상위로 고른다. **그것이 대표성 편향이라는 사실을 함께 낸다** —
    설계서 `ITP-T04` 가 "대형 상장사만 있는 후보군" 을 검출 대상으로 삼은 그 지점이다.
    """
    rows: List[Dict] = []
    unmatched: List[str] = []
    for member in target.get("members", []):
        snapshot = snapshot_store.get(member.get("code", ""))
        if snapshot:
            rows.append({**snapshot, "industry_code": member.get("industry_code", "")})
        else:
            unmatched.append(member.get("name") or member.get("code", ""))

    rows.sort(key=lambda r: -(r.get("market_cap") or 0))
    picked = rows[:want]
    total_cap = sum(r.get("market_cap") or 0 for r in rows) or 1
    picked_cap = sum(r.get("market_cap") or 0 for r in picked)

    warnings: List[Dict] = []
    if len(picked) < RECOMMENDED_MIN:
        warnings.append({
            "code": "G-SCOPE",
            "message": (f"후보가 {len(picked)}곳으로 권장 하한({RECOMMENDED_MIN})에 못 미친다 — "
                        "순위의 대표성이 낮다"),
            "treatment": "죽이지 않고 조건부로 낸다. 개수 제한을 이유로 결과를 버리지 않는다",
            "next_input": "업종 자릿수를 한 단계 넓히면 후보가 늘어난다",
        })
    if len(rows) > want:
        warnings.append({
            "code": "G-SCOPE",
            "message": (f"업종 상장사 {len(rows)}곳 중 시총 상위 {len(picked)}곳만 점수를 매겼다 "
                        f"(업종 시총의 {picked_cap / total_cap:.1%})"),
            "treatment": "잘린 후보 목록을 함께 싣는다 — 조용히 자르지 않는다",
            "next_input": "특정 종목을 반드시 넣어야 하면 직접 지정한다",
        })
    if unmatched:
        warnings.append({
            "code": "G-DATA",
            "message": f"업종 구성 종목 {len(unmatched)}곳이 시장 스냅샷에 없어 후보에서 빠졌다",
            "treatment": "빠진 이름을 목록으로 남긴다",
            "next_input": "스냅샷을 다시 만들면 들어온다",
        })

    return {
        "available": bool(picked),
        "rows": picked,
        "count": len(picked),
        "universe_total": len(rows),
        "universe_cap_share": round(picked_cap / total_cap, 4),
        "dropped": [r.get("name") for r in rows[want:]][:30],
        "unmatched": unmatched[:30],
        "warnings": warnings,
        "method": f"시가총액 상위 {want}곳",
        "bias": ("시총 상위로 골랐으므로 **규모 편향**이 있다. 작은 회사에서 성장이 나오는 "
                 "산업이면 이 후보군은 그 이야기를 놓친다 (설계서 ITP-T04)."),
        "recommended_range": [RECOMMENDED_MIN, MAX_CANDIDATES],
        "range_note": ("설계서는 5~10 을 불변조건으로 두지만 KSIC 세세분류 기준으로는 "
                       "산업의 16% 만 그 조건을 채운다. 개수로 자르지 않고 후보군 전수를 "
                       "공개하는 쪽으로 지킨다 (M5 결정)."),
        "reason": "" if picked else "이 업종에 시장 스냅샷과 맞는 상장사가 없다",
    }


# ─────────────────────────────────────────────────────────────
# 2. 항목별 0/3/5 점수 (설계서 S07·S08)
# ─────────────────────────────────────────────────────────────
def _rank_score(value: Optional[float], peers: Sequence[Optional[float]],
                higher_is_better: bool = True) -> Optional[int]:
    """후보군 안 백분위 → 0/3/5 앵커.

    설계서 §7.1 이 "후보군 내" 를 기준으로 삼았으므로 절대 기준을 쓰지 않는다.
    값이 없으면 **0 이 아니라 None** 이다 (그 항목은 분모에서 빠진다).
    """
    clean = [v for v in peers if isinstance(v, (int, float))]
    if value is None or not isinstance(value, (int, float)) or len(clean) < 3:
        return None
    below = sum(1 for v in clean if v < value)
    percentile = below / len(clean)
    if not higher_is_better:
        percentile = 1 - percentile
    if percentile >= 2 / 3:
        return 5
    if percentile >= 1 / 3:
        return 3
    return 0


def score_candidates(rows: Sequence[Dict], momentum_map: Optional[Dict] = None,
                     redteam_map: Optional[Dict] = None) -> List[Dict]:
    """후보별 6항목 원점수.

    쓰는 자료 (전부 시장 스냅샷·KRX — 후보 10곳에 DART 를 10번 부르면 배포본이 죽는다)

        성장성      250일 수익률 `r250` (실적 성장의 대용 — **대용이라는 사실을 밝힌다**)
        수익성      `roe`
        밸류에이션   `per` (낮을수록 매력적 → 방향 반전)
        모멘텀      60일 수익률 (없으면 `r60`)
        재무안정성   `pbr` 이 극단이 아닌가 + ROE 부호
        Red Team    후보별 검사 통과율 (설계서 §7.1 — 통과 5 · 부분 2.5 · 실패/불가 0)
    """
    momentum_map = momentum_map or {}
    redteam_map = redteam_map or {}

    growths = [r.get("r250") for r in rows]
    roes = [r.get("roe") for r in rows]
    pers = [r.get("per") for r in rows]
    moms = [momentum_map.get(r.get("code")) if momentum_map.get(r.get("code")) is not None
            else r.get("r60") for r in rows]
    pbrs = [r.get("pbr") for r in rows]

    scored: List[Dict] = []
    for index, row in enumerate(rows):
        items: Dict[str, Dict] = {}

        def put(key: str, value: Optional[int], evidence: str, note: str = "") -> None:
            items[key] = {
                "key": key, "name": WEIGHT_BY_KEY[key]["name"],
                "weight": WEIGHT_BY_KEY[key]["weight"],
                "score": value,
                "unscored": value is None,
                "anchor": ANCHOR.get(value, "") if value is not None else "Data unavailable / unverifiable",
                "evidence": evidence,
                "note": note,
            }

        put("growth", _rank_score(row.get("r250"), growths),
            f"250거래일 수익률 {row.get('r250')}%",
            "실적 성장이 아니라 **주가 수익률을 대용**으로 썼다 — 기대가 섞여 있다")
        put("profitability", _rank_score(row.get("roe"), roes),
            f"ROE {row.get('roe')}%")
        put("valuation", _rank_score(row.get("per"), pers, higher_is_better=False),
            f"PER {row.get('per')}",
            "PER 은 낮을수록 매력적이라 **방향을 뒤집어** 점수화했다 (설계서 ITP-T07)")
        put("momentum", _rank_score(moms[index], moms),
            f"60거래일 수익률 {moms[index]}%")

        # 재무 안정성 — PBR 과 ROE 부호로 본다. DART 를 안 부르는 대신 한계를 밝힌다.
        stability = None
        pbr = row.get("pbr")
        roe_value = row.get("roe")
        if pbr is not None and roe_value is not None:
            if roe_value > 0 and 0 < pbr <= 3:
                stability = 5
            elif roe_value > 0:
                stability = 3
            else:
                stability = 0
        put("stability", stability, f"PBR {pbr} · ROE {roe_value}%",
            "부채비율·현금흐름을 쓰지 않았다 — 후보 10곳에 DART 를 10번 부르지 않기 위해서다")

        checks = redteam_map.get(row.get("code")) or []
        if checks:
            points = []
            for check in checks:
                points.append(5.0 if check.get("verdict") == "통과"
                              else 2.5 if check.get("verdict") == "부분통과" else 0.0)
            put("redteam", round(fmean(points)), f"검사 {len(checks)}건 평균 {fmean(points):.1f}",
                "통과 5 · 부분통과 2.5 · 실패/검증불가 0 (설계서 §7.1)")
        else:
            put("redteam", None, "후보별 Red Team 검사를 돌리지 않았다")

        scored.append({
            "code": row.get("code"), "name": row.get("name"),
            "market_cap": row.get("market_cap"),
            "items": items,
            "raw": {"r250": row.get("r250"), "roe": row.get("roe"), "per": row.get("per"),
                    "pbr": row.get("pbr"), "momentum": moms[index]},
        })
    return scored


# ─────────────────────────────────────────────────────────────
# 2-1. 후보별 Red Team (설계서 S09 · ITP-T09)
# ─────────────────────────────────────────────────────────────
# 설계서 `ITP-T09` 는 "한 후보만 질문 1개" 를 **검증 비대칭**으로 잡아낸다.
# 그래서 모든 후보에 **같은 세 질문**을 던진다. 후보마다 다른 질문을 만들면
# 어느 쪽에 유리한 질문을 골랐는지 알 수 없게 된다.
#
# 재무 원자료(DART)를 후보 수만큼 부르지 않는다 — 배포본에서 10회 호출이면 90초다.
# 시장 스냅샷으로 답할 수 있는 것만 묻고, 못 묻는 것은 '검증 불가' 로 남긴다.
def redteam_candidates(rows: Sequence[Dict]) -> Dict[str, List[Dict]]:
    """후보별 공격 질문 3개와 판정 (통과 / 부분통과 / 실패 / 검증 불가)."""
    pers = [r.get("per") for r in rows if isinstance(r.get("per"), (int, float)) and r["per"] > 0]
    roes = [r.get("roe") for r in rows if isinstance(r.get("roe"), (int, float))]
    peer_per = median(pers) if len(pers) >= 3 else None
    peer_roe = median(roes) if len(roes) >= 3 else None
    caps = [r.get("market_cap") or 0 for r in rows]
    cap_median = median(caps) if caps else 0

    result: Dict[str, List[Dict]] = {}
    for row in rows:
        checks: List[Dict] = []
        per = row.get("per")
        roe_value = row.get("roe")
        growth = row.get("r250")

        # ① 싼 데는 이유가 있는가
        if per is None or peer_per is None or roe_value is None or peer_roe is None:
            checks.append({"question": "낮은 PER 이 저평가인가, 반영된 결과인가?",
                           "verdict": "검증 불가",
                           "finding": "PER 또는 ROE 가 없어 판정하지 못한다"})
        elif per < peer_per and roe_value < peer_roe:
            checks.append({"question": "낮은 PER 이 저평가인가, 반영된 결과인가?",
                           "verdict": "실패",
                           "finding": (f"PER {per} 은 후보군 중앙값 {peer_per} 보다 낮지만 "
                                       f"ROE {roe_value}% 도 중앙값 {peer_roe}% 보다 낮다 — "
                                       "디스카운트에 이유가 있다")})
        else:
            checks.append({"question": "낮은 PER 이 저평가인가, 반영된 결과인가?",
                           "verdict": "통과",
                           "finding": f"PER {per} · ROE {roe_value}% — 뚜렷한 열위를 찾지 못했다"})

        # ② 주가 상승이 실적인가 기대인가
        if growth is None or per is None:
            checks.append({"question": "1년 주가 상승이 실적인가 기대인가?",
                           "verdict": "검증 불가",
                           "finding": "수익률 또는 PER 이 없어 판정하지 못한다"})
        elif growth > 50 and (peer_per is not None and per > peer_per * 1.5):
            checks.append({"question": "1년 주가 상승이 실적인가 기대인가?",
                           "verdict": "실패",
                           "finding": (f"1년 {growth:+.0f}% 오르는 동안 PER 이 후보군 중앙값의 "
                                       f"{per / peer_per:.1f}배가 됐다 — 기대가 앞서 있다")})
        elif growth > 0:
            checks.append({"question": "1년 주가 상승이 실적인가 기대인가?",
                           "verdict": "부분통과",
                           "finding": (f"1년 {growth:+.0f}% — 실적 기여를 분리하려면 "
                                       "분기 실적이 필요하다 (여기서는 확인하지 않았다)")})
        else:
            checks.append({"question": "1년 주가 상승이 실적인가 기대인가?",
                           "verdict": "통과",
                           "finding": f"1년 {growth:+.0f}% — 상승 주장을 하지 않았다"})

        # ③ 규모 때문에 뽑힌 것은 아닌가
        cap = row.get("market_cap") or 0
        if cap_median and cap >= cap_median * 5:
            checks.append({"question": "후보군에 든 이유가 규모뿐인가?",
                           "verdict": "부분통과",
                           "finding": (f"시가총액이 후보군 중앙값의 {cap / cap_median:.1f}배다 — "
                                       "규모 편향을 감안해 읽어야 한다")})
        else:
            checks.append({"question": "후보군에 든 이유가 규모뿐인가?",
                           "verdict": "통과",
                           "finding": "시가총액이 후보군 안에서 극단적이지 않다"})

        result[row.get("code")] = checks
    return result


# ─────────────────────────────────────────────────────────────
# 3. 계산 (설계서 §7.2) — 식 그대로
# ─────────────────────────────────────────────────────────────
def compute(entry: Dict, weights: Optional[Dict[str, float]] = None,
            coefficient: float = PENALTY_COEFFICIENT,
            overrides: Optional[Dict[str, int]] = None) -> Dict:
    """후보 하나의 observed / penalty / adjusted 를 낸다.

    `weights` 는 {key: 가중치}, `overrides` 는 민감도 검토용 원점수 대체값이다.
    **반올림 전 값을 보존한다** (설계서 §7.2 — "계산 원장에는 반올림 전 값을 보존").
    """
    table = weights or {w["key"]: w["weight"] for w in WEIGHTS}
    overrides = overrides or {}

    valid_weight = 0.0
    weighted_sum = 0.0
    used: List[Dict] = []
    missing: List[str] = []
    for key, weight in table.items():
        item = entry["items"].get(key) or {}
        score = overrides.get(key, item.get("score"))
        if score is None:
            missing.append(item.get("name") or key)
            continue
        valid_weight += weight
        weighted_sum += score * weight
        used.append({"key": key, "name": item.get("name"), "score": score, "weight": weight})

    if valid_weight <= 0:
        return {
            "available": False,
            "observed_score": None, "adjusted_score": None,
            "coverage": 0.0, "missing": missing,
            "text": "Data unavailable / unverifiable",
            "note": "유효 가중치가 0이라 점수를 내지 않는다 (설계서 §7.2)",
        }

    total = sum(table.values()) or TOTAL_WEIGHT
    observed = weighted_sum / valid_weight
    coverage = valid_weight / total
    missing_ratio = 1 - coverage
    penalty = missing_ratio * coefficient
    adjusted = max(0.0, observed - penalty)

    return {
        "available": True,
        "observed_score": observed,                    # 반올림 전 원값
        "observed_display": round(observed, 1),
        "valid_weight": valid_weight,
        "coverage": coverage,
        "coverage_display": round(coverage * 100, 1),
        "missing_ratio": missing_ratio,
        "penalty_coefficient": coefficient,
        "missing_penalty": penalty,
        "adjusted_score": adjusted,
        "adjusted_display": round(adjusted, 1),
        "used": used,
        "missing": missing,
        "formula": ("observed = Σ(점수×가중치)÷valid_weight · "
                    "penalty = (1-coverage)×계수 · adjusted = max(0, observed-penalty)"),
        # 설계서 §6-4 — 핵심 결측이 있으면 confidence 를 최대 '중', coverage 80% 미만이면 '하'
        "confidence": ("하" if coverage < 0.8 else
                       "중" if any(k in missing for k in ("성장성", "수익성", "밸류에이션 매력도"))
                       else "상"),
        "confidence_reason": (f"coverage {coverage:.0%} 가 80% 미만이다" if coverage < 0.8
                              else f"핵심 항목 결측 {len(missing)}건" if missing
                              else "핵심 항목이 모두 채워졌다"),
    }


def rank(entries: Sequence[Dict], weights: Optional[Dict[str, float]] = None,
         coefficient: float = PENALTY_COEFFICIENT) -> List[Dict]:
    """adjusted_score 로 순위를 매긴다. **동점은 공동 순위로 둔다.**

    설계서 §7.3-6 — "동점은 공동 순위로 유지하고 숨은 타이브레이커를 적용하지 않는다".
    시가총액이나 이름 순으로 몰래 가르지 않는다.
    """
    rows = []
    for entry in entries:
        result = compute(entry, weights, coefficient)
        rows.append({"code": entry["code"], "name": entry["name"], **result})

    scored = [r for r in rows if r.get("available")]
    # 소수점 아래 잡음으로 동점이 갈리지 않게 소수 3자리에서 끊어 비교한다
    scored.sort(key=lambda r: -round(r["adjusted_score"], 3))
    position = 0
    previous = None
    for index, row in enumerate(scored):
        value = round(row["adjusted_score"], 3)
        if value != previous:
            position = index + 1
            previous = value
        row["rank"] = position
    for row in rows:
        if not row.get("available"):
            row["rank"] = None
    ties = {}
    for row in scored:
        ties[row["rank"]] = ties.get(row["rank"], 0) + 1
    for row in scored:
        row["tied"] = ties[row["rank"]] > 1
    return rows


# ─────────────────────────────────────────────────────────────
# 4. 민감도 · 순위 안정성 (설계서 §7.3)
# ─────────────────────────────────────────────────────────────
def _renormalize(table: Dict[str, float]) -> Dict[str, float]:
    """가중치 합을 100 으로 되돌린다 (설계서 §7.3-1)."""
    total = sum(table.values())
    if total <= 0:
        return table
    return {key: value / total * TOTAL_WEIGHT for key, value in table.items()}


def sensitivity(entries: Sequence[Dict]) -> Dict:
    """가중치 ±5%p · 원점수 ±1 · penalty 계수 3종을 돌려 순위 안정성을 본다.

    설계서 §7.3 의 여덟 규칙을 그대로 지킨다. 특히
    **동점은 공동 1위로 세고, 단독·공동 비율을 갈라 공개한다** (7·8번).
    """
    if not entries:
        return {"available": False, "reason": "후보가 없어 민감도를 못 낸다"}

    base_weights = {w["key"]: float(w["weight"]) for w in WEIGHTS}
    scenarios: List[Dict] = []

    def add(label: str, weights: Dict[str, float], coefficient: float,
            overrides: Optional[Dict[str, Dict[str, int]]] = None) -> None:
        rows = []
        for entry in entries:
            patched = (overrides or {}).get(entry["code"], {})
            result = compute(entry, weights, coefficient, patched)
            rows.append({"code": entry["code"], "name": entry["name"], **result})
        usable = [r for r in rows if r.get("available")]
        if not usable:
            return
        top_value = max(round(r["adjusted_score"], 3) for r in usable)
        leaders = [r["code"] for r in usable if round(r["adjusted_score"], 3) == top_value]
        scenarios.append({"label": label, "coefficient": coefficient,
                          "leaders": leaders, "shared": len(leaders) > 1,
                          "top_score": round(top_value, 3),
                          "ranking": sorted(
                              ({"code": r["code"], "score": round(r["adjusted_score"], 3)}
                               for r in usable), key=lambda r: -r["score"])})

    # 1) 기준
    add("기준 (가중치 기본 · 계수 1.5)", base_weights, PENALTY_COEFFICIENT)

    # 2) 가중치 ±5%p (합 100 재정규화)
    for key in base_weights:
        for delta in (WEIGHT_DELTA, -WEIGHT_DELTA):
            patched = dict(base_weights)
            patched[key] = max(0.0, patched[key] + delta)
            add(f"{WEIGHT_BY_KEY[key]['name']} {delta:+d}%p", _renormalize(patched),
                PENALTY_COEFFICIENT)

    # 3) penalty 계수 1.0 / 2.0 (1.5 는 기준에서 이미 봤다)
    for coefficient in PENALTY_SCENARIOS:
        if coefficient == PENALTY_COEFFICIENT:
            continue
        add(f"penalty 계수 {coefficient}", base_weights, coefficient)

    # 4) 원점수 ±1 — 후보마다 자기 점수를 흔든다 (0~5 밖으로 나가지 않는다)
    for direction in (SCORE_DELTA, -SCORE_DELTA):
        overrides: Dict[str, Dict[str, int]] = {}
        for entry in entries:
            patch = {}
            for key, item in entry["items"].items():
                if item.get("score") is not None:
                    patch[key] = max(0, min(5, item["score"] + direction))
            overrides[entry["code"]] = patch
        add(f"모든 원점수 {direction:+d}", base_weights, PENALTY_COEFFICIENT, overrides)

    total = len(scenarios)
    counts: Dict[str, Dict[str, int]] = {}
    for scenario in scenarios:
        for code in scenario["leaders"]:
            bucket = counts.setdefault(code, {"solo": 0, "shared": 0})
            bucket["shared" if scenario["shared"] else "solo"] += 1

    ranked = sorted(counts.items(), key=lambda kv: -(kv[1]["solo"] + kv[1]["shared"]))
    leader_code, leader_counts = ranked[0] if ranked else ("", {"solo": 0, "shared": 0})
    lead_ratio = (leader_counts["solo"] + leader_counts["shared"]) / total if total else 0.0

    if lead_ratio >= STABILITY_HIGH:
        stability, why = "높음", f"{lead_ratio:.0%} 의 시나리오에서 1위를 지켰다 (80% 이상)"
    elif lead_ratio >= STABILITY_MID:
        stability, why = "중간", f"{lead_ratio:.0%} 의 시나리오에서 1위였다 (60~79%)"
    else:
        stability, why = "낮음", f"{lead_ratio:.0%} 만 1위였다 — 작은 변화로 순위가 뒤집힌다"

    # 순위를 뒤집는 가장 작은 변화 (설계서 §7.3-8)
    base_leader = scenarios[0]["leaders"] if scenarios else []
    flips = [s["label"] for s in scenarios[1:]
             if s["leaders"] and set(s["leaders"]) != set(base_leader)]

    # 1·2위 점수 차이
    base_ranking = scenarios[0]["ranking"] if scenarios else []
    margin = (round(base_ranking[0]["score"] - base_ranking[1]["score"], 3)
              if len(base_ranking) >= 2 else None)

    return {
        "available": True,
        "scenario_count": total,
        "scenarios": scenarios,
        "leader_frequency": [{"code": code, "solo": row["solo"], "shared": row["shared"],
                              "total": row["solo"] + row["shared"],
                              "ratio": round((row["solo"] + row["shared"]) / total, 3)}
                             for code, row in ranked],
        "leader": leader_code,
        "lead_ratio": round(lead_ratio, 3),
        "solo_ratio": round(leader_counts["solo"] / total, 3) if total else 0.0,
        "shared_ratio": round(leader_counts["shared"] / total, 3) if total else 0.0,
        "stability": stability,
        "stability_why": why,
        "flip_scenarios": flips,
        "flip_count": len(flips),
        "top_margin": margin,
        "rules": {
            "weight_delta": f"±{WEIGHT_DELTA}%p 후 합 100 재정규화",
            "score_delta": f"원점수 ±{SCORE_DELTA} (0~5 유지)",
            "penalty": list(PENALTY_SCENARIOS),
            "ties": "동점은 공동 순위 — 숨은 타이브레이커를 쓰지 않는다",
            "thresholds": {"높음": ">= 80%", "중간": "60~79%", "낮음": "< 60%"},
        },
        "basis": "GIC v15 산업TopPick 하네스설계서 §7.3 민감도와 순위 안정성",
    }


# ─────────────────────────────────────────────────────────────
# ⚠️ 이 모듈에는 `analyze()` 가 **없다** — 형제 모듈 셋과 다른 점이다 (M9 · N93)
# ─────────────────────────────────────────────────────────────
#
# `corp_tp.analyze` · `ind_r.analyze` 는 `stages.h04_analyze` 가 실제로 부르지만,
# 여기 있던 `analyze()` 는 **호출자가 0건인 죽은 코드**였다. `stages._h04_ind_tp` 가
# 같은 3단(후보 → 점수 → 순위 → 민감도)을 직접 조립하기 때문이다.
#
# 왜 stages 쪽이 직접 조립하나 —
#   ① 후보 수를 사용자 답변(`Q-H01-2`)으로 정해야 해서 `candidates(target, want=…)` 를
#      먼저 부른다. 죽은 `analyze()` 에는 `want` 인자가 없었다.
#   ② 그 사이사이에 Gap 발행과 장부 등재(`add_derived`)가 끼어든다.
#
# 그냥 안 쓰이기만 한 것이 아니라 **내용이 갈라져 있었다** — `note`·`limitation` 문구가
# 달랐고 `anchor` 키가 더 있었다. 아무도 검증하지 않는 두 번째 진실이 있는 셈이라,
# 누군가 죽은 쪽을 고치면 리포트는 그대로인 채 고쳤다고 믿게 된다. 그래서 지웠다.
#
# **다시 만들지 마라.** 3단을 한 함수로 묶고 싶으면 `stages._h04_ind_tp` 를 고치고,
# 이 모듈은 조각 함수(`candidates` · `score_candidates` · `rank` · `sensitivity`)만 낸다.
