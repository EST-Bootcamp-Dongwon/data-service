"""리포트 표 — 다년 재무 · 비율 · 피어 비교 (M8 · 변경노트 N87)

왜 이 파일이 생겼나
    목표 양식(GIC 리서치 리포트)은 재무와 피어를 **표로** 낸다. 우리 리포트는 같은
    숫자를 `page.body` 의 불릿 문장으로 냈다.

        - 매출액 CAGR 4.5% (2021→2025) · 성장 둔화
        - 영업이익률 13.07% — 본업에서 이익이 난다
        - ROE 10.36% · 부채비율 29.94% · 유동비율 232.76%

    다섯 해치 손익을 견주려면 문장을 다섯 개 읽고 머리로 세워야 한다. 표면 한 번에 보인다.

★ **`page.body` 에 표를 넣지 않는다** (이 파일이 존재하는 진짜 이유)
    `linkcheck.py` 는 **본문(`page.body`)의 수치만** 세어 H10 '증거 추적성' 5점을 매긴다.
    표를 본문에 밀어 넣으면 수치 개수가 통째로 달라져 **데이터를 하나도 안 고쳤는데
    점수가 움직인다.** 그러면 이번 변경이 리포트를 좋게 만든 건지 분모를 바꾼 건지
    구분할 수 없게 된다 (M7 에서 루브릭과 데이터를 한 커밋에 넣어 중간 상태를 잃었다).

    그래서 표는 `page.tables` 라는 **별도 자리**로 간다. 본문은 그대로다.

★ 새 숫자를 만들지 않는다
    표에 싣는 값은 **전부 이미 장부에 D- 로 있는 것**이다 (`financial.series` 의 재무제표
    항목과 `latest_ratios` 의 비율은 H03·H04 가 이미 등재했다). 표를 만들려고 여기서
    연도별 비율을 새로 계산하지 않는다 — 그러면 장부에 없는 수치가 리포트에 실린다.

        표에 있다  매출액 · 영업이익 · 순이익 · 자산 · 자본 · 부채 · 영업현금흐름 · 연도별 성장률
        표에 없다  연도별 영업이익률 · 연도별 ROE  ← 계산한 적이 없다. 만들지 않는다

    대신 셀마다 D- 를 붙여 되짚을 수 있게 한다 (`ledger.find_data`).

    `charts.py` 와 같은 규칙이다 — **여기서 한 번 만들고 세 렌더러가 그리기만 한다.**
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import ledger

# 다년 재무표에 세울 줄. **장부에 D- 가 있는 항목만** 적는다 (위 규칙 참고).
FINANCIAL_ROWS = (
    ("revenue", "매출액", "조원", 1e12, 1),
    ("operating_income", "영업이익", "조원", 1e12, 1),
    ("net_income", "순이익", "조원", 1e12, 1),
    ("assets", "자산총계", "조원", 1e12, 1),
    ("equity", "자본총계", "조원", 1e12, 1),
    ("liabilities", "부채총계", "조원", 1e12, 1),
    ("operating_cash_flow", "영업현금흐름", "조원", 1e12, 1),
)

# 최신 연도 비율. `latest_ratios` 가 이미 계산해 둔 것만 쓴다.
RATIO_ROWS = ("operating_margin", "roe", "roa", "debt_ratio", "current_ratio")

MAX_PEERS = 12          # 피어 표에 몇 줄까지. 넘치면 **자른 수를 밝힌다**


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value, digits: int = 1) -> str:
    """값이 없으면 **0 이 아니라 물결표**다 — 0 과 '모른다' 는 다르다."""
    if not _is_num(value):
        return "—"
    return f"{value:,.{digits}f}"


def _table(key: str, title: str, head: List[str], rows: List[List],
           note: str = "", data_ids: Optional[List[str]] = None,
           basis: str = "", align_right_from: int = 1) -> Dict:
    return {
        "key": key, "title": title, "head": head, "rows": rows,
        "note": note, "basis": basis,
        "data_ids": list(data_ids or []),
        # 첫 칸은 이름이라 왼쪽, 나머지는 숫자라 오른쪽. 렌더러 셋이 같은 정렬을 쓴다.
        "align_right_from": align_right_from,
        "drawable": True, "reason": "",
    }


def _undrawable(key: str, title: str, reason: str) -> Dict:
    """만들 수 없으면 **빼지 않고 사유와 함께 남긴다** (불변원칙 §2-3)."""
    empty = _table(key, title, [], [])
    empty["drawable"] = False
    empty["reason"] = reason
    return empty


# ─────────────────────────────────────────────────────────────
# 1. 다년 재무표
# ─────────────────────────────────────────────────────────────
def financial_years(pack: Dict, analysis: Dict) -> Dict:
    """연도를 열로, 재무항목을 행으로 세운다.

    **연도를 열에 둔다.** 읽는 사람이 하는 일은 "이 항목이 해마다 어떻게 변했나" 라
    가로로 훑는 것이 자연스럽다. 항목을 열에 두면 다섯 줄을 세로로 견줘야 한다.
    """
    block = analysis.get("financial") or {}
    series = [row for row in (block.get("series") or []) if row.get("year")]
    if not series:
        return _undrawable("financial_years", "연도별 재무",
                           "연결재무제표를 받지 못했다")

    years = [str(row["year"]) for row in series]
    head = ["항목 (조원)"] + years
    body: List[List] = []
    data_ids: List[str] = []
    for key, label, _unit, scale, digits in FINANCIAL_ROWS:
        values = [row.get(key) for row in series]
        if not any(_is_num(v) for v in values):
            continue            # 한 해도 없는 항목은 줄을 만들지 않는다 (빈 줄을 두지 않는다)
        body.append([label] + [_num(v / scale, digits) if _is_num(v) else "—"
                               for v in values])
        for row in series:
            found = ledger.find_data(pack, key, str(row["year"]))
            if found:
                data_ids.append(found["id"])

    # 성장률은 이미 계산돼 있다 (`growth.growths`). 여기서 다시 나누지 않는다.
    growth = block.get("growth") or {}
    if growth.get("available"):
        by_period = {str(g.get("period", "")): g.get("growth")
                     for g in (growth.get("growths") or [])}
        line = ["매출 성장률 (%)", "—"]      # 첫 해는 앞이 없어 성장률이 없다
        for index in range(1, len(series)):
            key = f"{series[index - 1]['year']}→{series[index]['year']}"
            value = by_period.get(key)
            line.append(f"{value:+,.1f}" if _is_num(value) else "—")
        body.append(line)

    if not body:
        return _undrawable("financial_years", "연도별 재무",
                           "연결재무제표에 쓸 수 있는 항목이 없다")

    note = (f"CAGR {_num(growth.get('cagr'), 1)}% ({growth.get('years', '—')}년) · "
            f"{growth.get('trend', '')}" if growth.get("available") else "")
    return _table("financial_years", f"연도별 재무 ({years[0]}~{years[-1]})",
                  head, body, note=note, data_ids=data_ids,
                  basis=str(growth.get("basis") or "DART 연결재무제표"))


def financial_ratios(pack: Dict, analysis: Dict) -> Dict:
    """최신 연도 비율표. **연도별로 늘리지 않는다** — 계산한 적이 없기 때문이다."""
    ratios = (analysis.get("financial") or {}).get("latest_ratios") or {}
    body: List[List] = []
    data_ids: List[str] = []
    for key in RATIO_ROWS:
        item = ratios.get(key) or {}
        if not _is_num(item.get("value")):
            continue
        body.append([str(item.get("label") or key), _num(item.get("value"), 2),
                     str(item.get("unit") or ""), str(item.get("why") or "")])
        found = ledger.find_data(pack, key)
        if found:
            data_ids.append(found["id"])
    if not body:
        return _undrawable("financial_ratios", "재무비율",
                           "비율을 계산할 재무항목이 없다")

    dupont = ratios.get("dupont") or {}
    note = ""
    if dupont.get("available"):
        # 세 조각을 곱해 ROE 가 나온다는 것을 **숫자로** 보인다 — 어디서 왔는지가 보인다
        note = (f"{dupont.get('formula', '')} = {_num(dupont.get('net_margin'), 2)}% × "
                f"{_num(dupont.get('asset_turnover'), 3)} × "
                f"{_num(dupont.get('leverage'), 3)} = "
                f"{_num(dupont.get('roe_reconstructed'), 2)}% · "
                f"주 동인 {dupont.get('main_driver', '')}")
    quality = ratios.get("earnings_quality") or {}
    if not quality.get("available") and quality.get("reason"):
        note = (note + " / " if note else "") + f"이익의 질은 판정하지 못했다 — {quality['reason']}"
    return _table("financial_ratios", "재무비율 (최신 연도)",
                  ["지표", "값", "단위", "판단 근거"], body,
                  note=note, data_ids=data_ids,
                  basis="08강 06.md 수익성·안전성 지표", align_right_from=1)


# ─────────────────────────────────────────────────────────────
# 2. 피어 비교표
# ─────────────────────────────────────────────────────────────
def peer_compare(pack: Dict, analysis: Dict) -> Dict:
    """피어 비교. **대상을 맨 위에 두고 표시한다** — 견주는 기준이 어디인지가 먼저다."""
    block = (analysis.get("peers") or {}).get("table") or {}
    rows = block.get("rows") or []
    target = block.get("target") or {}
    if not rows and not target:
        return _undrawable("peer_compare", "피어 비교",
                           (analysis.get("peers") or {}).get("reason")
                           or "피어를 고르지 못했다")

    ordered = sorted(rows, key=lambda r: -(r.get("market_cap") or 0))
    shown = ordered[:MAX_PEERS]
    cut = len(ordered) - len(shown)

    def line(row: Dict, mark: str = "") -> List:
        return [f"{row.get('name', '')}{mark}",
                _num((row.get("market_cap") or 0) / 1e12, 1),
                _num(row.get("per"), 2), _num(row.get("pbr"), 2),
                _num(row.get("roe"), 2), _num(row.get("r250"), 1)]

    body = ([line(target, " (대상)")] if target else []) + [line(r) for r in shown]
    data_ids: List[str] = []
    for row in ([target] if target else []) + shown:
        code = str(row.get("code") or "")
        for metric in ("market_cap", "per", "pbr", "roe", "r250"):
            found = ledger.find_data(pack, f"{code}.{metric}")
            if found:
                data_ids.append(found["id"])

    notes = []
    if cut:
        notes.append(f"피어 {len(ordered)}곳 중 시총 상위 {len(shown)}곳만 실었다 (뺀 것 {cut}곳)")
    if block.get("note"):
        notes.append(str(block["note"]))
    # `missing_per` 는 **개수가 아니라 이름 목록**이다 (실측 `["케이엠더블유","빛과전자"]`).
    # 개수처럼 찍으면 `['케이엠더블유','빛과전자']곳` 이 나온다. 누가 빠졌는지를 적는다.
    missing = block.get("missing_per") or []
    if missing:
        notes.append("PER 이 빈 곳: " + " · ".join(str(m) for m in missing))
    peers = analysis.get("peers") or {}
    if peers.get("method"):
        notes.append(f"피어 선정: {peers['method']}")
    return _table("peer_compare", "피어 비교",
                  ["종목", "시가총액 (조원)", "PER (배)", "PBR (배)", "ROE (%)", "1년 수익률 (%)"],
                  body, note=" · ".join(notes), data_ids=data_ids,
                  basis=f"시장 스냅샷 {block.get('as_of', '')}")


# ─────────────────────────────────────────────────────────────
# 3. 산업 계열 — 구성 종목표
# ─────────────────────────────────────────────────────────────
def industry_members(pack: Dict, analysis: Dict) -> Dict:
    """산업 구성 종목 (시총 순). 기업 계열의 피어 표와 같은 자리에 놓는다."""
    market = analysis.get("market") or {}
    rows = [r for r in (market.get("rows") or []) if _is_num(r.get("market_cap"))]
    if not rows:
        return _undrawable("industry_members", "구성 종목",
                           "구성 종목의 시가총액을 찾지 못했다")
    ordered = sorted(rows, key=lambda r: -r["market_cap"])
    shown = ordered[:MAX_PEERS]
    cut = len(ordered) - len(shown)
    total = sum(r["market_cap"] for r in ordered) or 1
    body = [[r.get("name", ""), _num(r["market_cap"] / 1e12, 1),
             _num(r["market_cap"] / total * 100, 1),
             _num(r.get("per"), 2), _num(r.get("pbr"), 2), _num(r.get("roe"), 2)]
            for r in shown]
    notes = []
    if cut:
        notes.append(f"상장 {len(ordered)}곳 중 상위 {len(shown)}곳만 실었다 (뺀 것 {cut}곳)")
    if market.get("limitation"):
        notes.append(str(market["limitation"]).replace("**", ""))
    return _table("industry_members", "구성 종목 (시가총액 순)",
                  ["종목", "시가총액 (조원)", "산업 내 비중 (%)", "PER (배)", "PBR (배)", "ROE (%)"],
                  body, note=" · ".join(notes),
                  basis=f"시장 스냅샷 {market.get('as_of', '')}")


def candidate_universe(pack: Dict, analysis: Dict) -> Dict:
    """IND-TP 후보 Universe (시총 순).

    산업 전체가 아니라 **후보군**이다. 무엇을 넣고 무엇을 뺐는지가 결론을 좌우하므로
    `method`(어떻게 골랐나) 와 `bias`(그래서 무엇을 놓치나) 를 표 밑에 함께 싣는다
    (설계서 ITP-T04 후보군 완전성).
    """
    universe = analysis.get("universe") or {}
    rows = [r for r in (universe.get("rows") or []) if _is_num(r.get("market_cap"))]
    if not rows:
        return _undrawable("candidate_universe", "후보 Universe",
                           str(universe.get("reason") or "후보군을 만들지 못했다"))
    ordered = sorted(rows, key=lambda r: -r["market_cap"])
    shown = ordered[:MAX_PEERS]
    body = [[r.get("name", ""), _num(r["market_cap"] / 1e12, 1),
             _num(r.get("per"), 2), _num(r.get("pbr"), 2),
             _num(r.get("roe"), 2), _num(r.get("r250"), 1)]
            for r in shown]
    # 셀을 되짚을 수 있게 D- 를 단다 — 산업 계열 팩은 지표를 `코드.지표` 로 등재한다
    data_ids: List[str] = []
    for row in shown:
        for metric in ("market_cap", "per", "pbr", "roe", "r250"):
            found = ledger.find_data(pack, f"{row.get('code', '')}.{metric}")
            if found:
                data_ids.append(found["id"])
    notes = [str(universe.get("method") or ""),
             f"업종 {universe.get('universe_total', '—')}곳 중 시총의 "
             f"{_num((universe.get('universe_cap_share') or 0) * 100, 1)}% 를 덮는다",
             str(universe.get("bias") or "").replace("**", ""),
             str(universe.get("range_note") or "")]
    if universe.get("unmatched"):
        notes.append(f"스냅샷에 없어 뺀 곳 {universe['unmatched']}곳")
    return _table("candidate_universe", "후보 Universe (시가총액 순)",
                  ["종목", "시가총액 (조원)", "PER (배)", "PBR (배)", "ROE (%)", "1년 수익률 (%)"],
                  body, note=" · ".join(n for n in notes if n), data_ids=data_ids,
                  basis="GIC v15 산업TopPick 하네스설계서 ITP-T04")


def candidate_scores(pack: Dict, analysis: Dict) -> Dict:
    """IND-TP 후보 점수표 — 총점만 보이던 것을 **차원별로** 편다.

    `adjusted` 만 보면 왜 그 순위인지 알 수 없다. 차원 점수를 나란히 두면
    "성장성은 1위인데 밸류에이션이 깎았다" 가 표에서 바로 읽힌다.
    """
    ranking = [r for r in (analysis.get("ranking") or []) if r.get("used")]
    if not ranking:
        return _undrawable("candidate_scores", "후보 점수",
                           "차원별 점수를 매긴 후보가 없다")
    dimensions: List = []
    for row in ranking:                      # 등장 순서를 지킨다 (가중치 순으로 이미 정렬돼 있다)
        for used in row["used"]:
            pair = (used.get("key"), used.get("name"), used.get("weight"))
            if pair not in dimensions:
                dimensions.append(pair)
    head = (["순위", "종목"]
            + [f"{name} ({weight}%)" for _key, name, weight in dimensions]
            + ["보정 점수", "커버리지 (%)"])
    body = []
    for row in ranking[:MAX_PEERS]:
        scores = {u.get("key"): u.get("score") for u in row["used"]}
        body.append([_num(row.get("rank"), 0), str(row.get("name", ""))]
                    + [_num(scores.get(key), 0) for key, _n, _w in dimensions]
                    + [_num(row.get("adjusted_score"), 1),
                       _num(row.get("coverage_display"), 0)])
    # ⚠️ `analysis.weights` 는 **리스트**다 (차원별 가중치 목록). dict 로 보고 `.get` 을
    #    부르면 AttributeError 로 표가 통째로 사라진다 — 실측에서 실제로 겪었다.
    weights = analysis.get("weights") or []
    total = sum(w.get("weight") or 0 for w in weights if isinstance(w, dict))
    return _table("candidate_scores", "후보 점수 (차원별)", head, body,
                  note="점수는 후보군 안 백분위(0/3/5 앵커)라 다른 산업과 견줄 수 없다 · "
                       "커버리지가 낮은 후보는 penalty 로 낮아진다 — '나쁘다' 와 다르다",
                  basis=f"GIC v15 산업TopPick 하네스설계서 · 가중치 합 {total}%")


# ─────────────────────────────────────────────────────────────
# 분배
# ─────────────────────────────────────────────────────────────
_CORP = (financial_years, financial_ratios, peer_compare)
_INDUSTRY = (industry_members,)
# IND-TP 에는 `analysis.market` 이 **없다** (분석이 후보군 중심이라 산업 전체 시세를 안 든다).
# 그래서 구성 종목표 대신 후보 Universe 표를 쓴다.
_IND_TP = (candidate_universe, candidate_scores)


def build(pack: Dict, analysis: Dict) -> List[Dict]:
    """워크스트림에 맞는 표를 만든다. 못 만든 것도 사유와 함께 남긴다."""
    workstream = str((pack.get("C0_charter") or {}).get("workstream_id") or "")
    builders = (_IND_TP if workstream == "IND-TP"
                else _INDUSTRY if workstream.startswith("IND") else _CORP)
    out: List[Dict] = []
    for builder in builders:
        try:
            out.append(builder(pack, analysis or {}))
        except Exception as error:           # 표 하나가 리서치를 죽이지 않는다 (§2-2)
            out.append(_undrawable(builder.__name__, builder.__name__,
                                   f"표를 만들다 실패했다 — {type(error).__name__}: {error}"))
    return out


def summary(built: List[Dict]) -> Dict:
    drawable = [t for t in built if t.get("drawable")]
    return {
        "total": len(built),
        "drawable": len(drawable),
        "skipped": [{"key": t.get("key", ""), "title": t.get("title", ""),
                     "reason": t.get("reason", "")}
                    for t in built if not t.get("drawable")],
    }
