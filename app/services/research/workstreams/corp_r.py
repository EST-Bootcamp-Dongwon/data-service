"""CORP-R 기업리서치 전용 판단 (명세 §5.4)

공통 상태(H00~H11)가 하는 일은 `stages/` 에 있고, 여기에는 **기업리서치에만 있는 것**을 둔다.

    · 피어 선정          업종(표준산업분류 접두어) + 시총 밴드
    · H03 3단 정규화     회계기간 → 재무 → 피어
    · 밸류에이션 차단     논리가 약하면 결론을 내지 않는다 (§5.4)

피어 선정이 이 워크스트림의 심장이다. 피어가 바뀌면 "저평가" 판정이 통째로 뒤집히므로
**어떻게 골랐는지**를 늘 함께 낸다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ....clients import dart_data
from ....repositories import industry_store, snapshot_store
from ....services.preprocess import normalize
from ..knowledge import financials, macro, valuation

# 시총 밴드 기본값 — 0.5~2.0배.
# ±50%(0.5~1.5배)로 잡으면 큰 회사 쪽이 지나치게 좁아진다. 시총은 로그 분포라
# '절반~두 배' 가 사람이 생각하는 '비슷한 크기' 에 더 가깝다.
CAP_BAND_LOW = 0.5
CAP_BAND_HIGH = 2.0
MAX_PEERS = 12


def select_peers(code: str, mode: str = "업종", max_peers: int = MAX_PEERS,
                 manual: Optional[List[str]] = None) -> Dict:
    """피어를 고른다. `mode` 는 H01 질문의 답이다 (업종 · 시총 · 직접 지정).

    돌려주는 것에 **왜 이렇게 골랐는지**가 들어 있다 (`method` · `note` · `dropped`).
    리포트 slot 10 이 그 문장을 그대로 싣는다.
    """
    me = snapshot_store.get(code)
    if not me:
        return {"available": False, "reason": "시장 스냅샷에 이 종목이 없다", "peers": [],
                "method": mode}

    my_cap = me.get("market_cap") or 0

    if manual:
        rows = [snapshot_store.get(c.strip()) for c in manual if c.strip()]
        peers = [r for r in rows if r and r.get("code") != code]
        missing = [c for c, r in zip(manual, rows) if not r]
        return {
            "available": bool(peers),
            "peers": peers[:max_peers],
            "method": "직접 지정",
            "note": f"사용자가 지정한 {len(peers)}곳을 그대로 쓴다",
            "dropped": [f"{c} — 스냅샷에 없다" for c in missing],
            "target": me,
        }

    # 1) 업종 후보
    industry = industry_store.peers_by_industry(code)
    candidates: List[Dict] = []
    dropped: List[str] = []
    if mode.startswith("업종") and industry.get("available"):
        for peer_code in industry["peers"]:
            row = snapshot_store.get(peer_code)
            if row:
                candidates.append(row)
        method = f"업종({industry['matched_prefix']}) + 시총 밴드"
        note = industry.get("note", "")
    else:
        candidates = [r for r in snapshot_store.rows("KOSPI") + snapshot_store.rows("KOSDAQ")
                      if r.get("code") != code]
        method = "시총 밴드만"
        note = ("업종을 쓰지 않았다 — 같은 크기일 뿐 같은 사업이 아닐 수 있다"
                if not industry.get("available") else "사용자가 시총 기준을 골랐다")
        if not industry.get("available"):
            dropped.append(industry.get("reason", "업종을 못 찾았다"))

    # 2) 시총 밴드
    if my_cap:
        in_band = [r for r in candidates
                   if r.get("market_cap")
                   and CAP_BAND_LOW * my_cap <= r["market_cap"] <= CAP_BAND_HIGH * my_cap]
        if len(in_band) < 3 and candidates:
            # 밴드가 너무 좁아 비교가 안 되면 넓힌다 — **넓혔다는 사실을 적는다**
            in_band = sorted(
                [r for r in candidates if r.get("market_cap")],
                key=lambda r: abs((r["market_cap"] or 0) - my_cap))[:max_peers]
            dropped.append(f"시총 {CAP_BAND_LOW}~{CAP_BAND_HIGH}배 밴드에 3곳이 안 돼 "
                           f"시총이 가까운 순으로 {len(in_band)}곳을 골랐다")
    else:
        in_band = candidates
        dropped.append("이 종목의 시총을 몰라 밴드를 적용하지 못했다")

    # 3) 시총 큰 순으로 자른다 (같은 업종·비슷한 크기 안에서는 대표성이 큰 쪽부터)
    peers = sorted(in_band, key=lambda r: -(r.get("market_cap") or 0))[:max_peers]
    return {
        "available": bool(peers),
        "peers": peers,
        "method": method,
        "note": note,
        "industry_code": industry.get("industry_code", ""),
        "matched_digits": industry.get("matched_digits", 0),
        "candidate_count": len(candidates),
        "band": [CAP_BAND_LOW, CAP_BAND_HIGH],
        "dropped": dropped,
        "target": me,
        "reason": "" if peers else "업종·시총 어느 쪽으로도 비교 대상을 못 찾았다",
    }


def normalize_periods(code: str, years: int = 5) -> Dict:
    """H03 1단 — 회계기간 정규화.

    결산월이 12월이 아닌 회사가 있다. 그때 '2025년' 은 회사마다 다른 기간을 뜻한다.
    피어와 나란히 놓기 전에 **어떤 기간인지**를 먼저 못박는다 (명세 §3 P5 · normalize.fiscal_period).
    """
    entry = industry_store.entry_of(code)
    fiscal_month = entry.get("fiscal_month") or "12"
    from datetime import datetime, timedelta, timezone

    this_year = datetime.now(timezone(timedelta(hours=9))).year
    periods = []
    for year in range(this_year - years, this_year):
        periods.append(normalize.fiscal_period(year, "11011", fiscal_month))
    comparable = normalize.compare_periods(periods)
    return {
        "fiscal_month": fiscal_month,
        "periods": periods,
        "comparable": comparable,
        "note": ("결산월이 12월이 아니다 — 피어와 기간이 어긋난다는 사실을 밝혀야 한다"
                 if fiscal_month != "12" else "결산 12월 — 일반적인 회계연도다"),
    }


def load_financials(code: str, years: int = 5) -> Dict:
    """H03 2단 — 재무 정규화. 여러 해를 모아 한 표로 만든다.

    ⚠️ 한 번 호출하면 **3개년**이 온다 (`value` 당기 · `prev` 전기 · `prev2` 전전기).
    그래서 5년치를 받겠다고 5번 부르면 네 번은 헛일이다. 3년 간격으로 두 번만 부른다.
    (삼성전자 실측 0.14초/회 — 5회 0.7초 vs 2회 0.28초)
    """
    from datetime import datetime, timedelta, timezone

    this_year = datetime.now(timezone(timedelta(hours=9))).year
    anchors = [this_year - 1]
    while len(anchors) * 3 < years:
        anchors.append(anchors[-1] - 3)

    by_year: Dict[int, Dict] = {}
    failures: List[str] = []
    basis = ""
    for anchor in anchors:
        try:
            result = dart_data.fetch_financials(code, anchor, "11011")
        except Exception as error:
            failures.append(f"{anchor} — {str(error)[:60]}")
            continue
        accounts = result.get("accounts") or {}
        if not accounts:
            failures.append(f"{anchor} — 계정이 비어 있다")
            continue
        basis = basis or result.get("fs_div_name") or result.get("fs_div") or ""
        # 한 응답에서 3개년을 펼친다
        for offset, field in enumerate(("value", "prev", "prev2")):
            year = anchor - offset
            snapshot = {key: row.get(field) for key, row in accounts.items()
                        if isinstance(row, dict)}
            if any(v is not None for v in snapshot.values()):
                by_year.setdefault(year, {"year": year, "basis": basis, "accounts": snapshot})

    rows = [by_year[year] for year in sorted(by_year)][-years:]
    return {"available": bool(rows), "rows": rows, "failures": failures,
            "basis": basis, "calls": len(anchors),
            "years_requested": years, "years_loaded": len(rows)}


def _account(accounts: Dict, *keys) -> Optional[float]:
    """계정에서 값 하나를 꺼낸다.

    `dart_data` 가 이미 표준 키(`revenue`·`operating_income` …)로 정규화해 두었다.
    한글 계정명으로 찾지 않는다 — 회사마다 이름이 달라서 그쪽은 못 믿는다 (명세 §2.2).
    """
    for key in keys:
        value = accounts.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def ratios_of(accounts: Dict, industry_code: str = "", name: str = "") -> Dict:
    """한 해 계정 → 08강 판정 묶음.

    `industry_code` 를 주면 **금융업 예외**를 씌운다 (U8 결정 ②).
    은행은 예금이 부채라 부채비율 1,000% 가 정상인데, 그걸 그대로 계산해
    `🔴 200% 이상 주의` 로 찍으면 틀린 사실을 싣는 것이다 (08강 06.md 421행).
    """
    revenue = _account(accounts, "revenue")
    operating = _account(accounts, "operating_income")
    net = _account(accounts, "net_income")
    assets = _account(accounts, "assets")
    equity = _account(accounts, "equity")
    liabilities = _account(accounts, "liabilities")
    current_assets = _account(accounts, "current_assets")
    current_liabilities = _account(accounts, "current_liabilities")
    ocf = _account(accounts, "cfo")

    result = {
        "inputs": {"revenue": revenue, "operating_income": operating, "net_income": net,
                   "assets": assets, "equity": equity, "liabilities": liabilities,
                   "current_assets": current_assets, "current_liabilities": current_liabilities,
                   "operating_cash_flow": ocf},
        "operating_margin": financials.operating_margin(operating, revenue),
        "roe": financials.roe(net, equity),
        "roa": financials.roa(net, assets),
        "debt_ratio": financials.debt_ratio(liabilities, equity),
        "current_ratio": financials.current_ratio(current_assets, current_liabilities),
        "dupont": financials.dupont(net, revenue, assets, equity),
        "earnings_quality": financials.earnings_quality(ocf, operating),
    }

    if industry_code:
        adjusted = macro.stability_verdicts(result, industry_code, name)
        if adjusted.get("applied"):
            result = adjusted["ratios"]
            result["financial_exception"] = {
                "applied": True,
                "changed": adjusted["changed"],
                "note": adjusted["note"],
                "basis": adjusted["basis"],
            }
    return result


def peer_table(target: Dict, peers: List[Dict]) -> Dict:
    """H03 3단 — 피어 정규화. 같은 지표를 같은 기준일로 나란히 놓는다.

    스냅샷은 **한 기준일**로 만들어져 있어 날짜가 어긋날 일이 없다 (명세 §2.3).
    빠진 값은 채우지 않고 비운 채로 둔다.
    """
    def row_of(row: Dict) -> Dict:
        return {
            "code": row.get("code"), "name": row.get("name"),
            "market": row.get("market"), "market_cap": row.get("market_cap"),
            "per": row.get("per"), "pbr": row.get("pbr"), "roe": row.get("roe"),
            "r250": row.get("r250"),
        }

    rows = [row_of(p) for p in peers]
    missing_per = [r["name"] for r in rows if not r.get("per")]
    return {
        "as_of": snapshot_store.as_of(),
        "target": row_of(target),
        "rows": rows,
        "per_values": [r.get("per") for r in rows],
        "pbr_values": [r.get("pbr") for r in rows],
        "roe_values": [r.get("roe") for r in rows],
        "missing_per": missing_per,
        "note": (f"PER 이 없는 피어 {len(missing_per)}곳은 비운 채로 뒀다 (적자이거나 자료가 없다)"
                 if missing_per else "피어 전부 PER 이 있다"),
    }


def valuation_view(target: Dict, table: Dict, growth: Optional[float],
                   industry_code: str, debt: Optional[float]) -> Dict:
    """밸류에이션 판단 묶음 — 어떤 멀티플로 볼지부터 정하고 시작한다."""
    per = target.get("per")
    roe_value = target.get("roe")
    choice = valuation.choose_multiple(
        net_income=1 if per else None, revenue_growth=growth,
        industry_code=industry_code, debt_ratio=debt)
    position = valuation.peer_position(per, table.get("per_values", []), "PER")
    pbr_position = valuation.peer_position(target.get("pbr"), table.get("pbr_values", []), "PBR")
    screened = valuation.screen(per, position, growth, roe_value)
    return {
        "sector_guide": valuation.sector_guide(industry_code),
        "multiple_choice": choice,
        "per_position": position,
        "pbr_position": pbr_position,
        "quadrant": valuation.quadrant(per, growth),
        "screen": screened,
        "blocked": not screened.get("allow_valuation"),
        "blocked_reason": ("근거가 둘 이상 모이지 않아 밸류에이션 결론을 내지 않는다 (명세 §5.4)"
                           if not screened.get("allow_valuation") else ""),
    }
