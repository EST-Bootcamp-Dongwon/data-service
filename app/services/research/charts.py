"""차트 명세 → 그릴 수 있는 계열 (M8 · 변경노트 N84)

왜 이 파일이 생겼나
    `h05_visualize` 가 만드는 `C4_visual` 은 **무엇을 그릴지**만 적는다 —
    질문 · 차트 유형 · 축 · `data_ids` · 출처 · 금지 오해. 그릴 **값은 없다.**

    그래서 M7 까지 리포트의 차트는 글자 한 줄이었다.

        export_md.py:842    *차트: 부문별 매출 막대*
        research.js:775     <span>차트: 부문별 매출 막대</span>

    화면에는 차트가 아예 없었다 — `research.js` 1,114행에 차트를 그리는 코드가
    한 줄도 없고 `research.html` 은 ApexCharts 를 불러오지도 않았다.
    다른 화면 일곱 개(`market`·`krx`·`kosis`·`quant`·`yf`·`stock`·`timeseries`)는
    전부 그리고 있었으니, 리서치만 빠져 있던 것이다.

    **값은 이미 팩 안에 다 있다.** `CX_workstream.analysis` 아래에 들어 있고,
    브라우저는 매 요청 그 팩을 들고 다닌다. 이 파일은 그것을 **한 가지 모양**으로
    정리해서 세 곳이 같은 것을 쓰게 한다.

        charts.build(pack, analysis)  →  CX_workstream.charts
              │
              ├─ static/assets/research.js   ApexCharts 로 화면에 그린다
              ├─ export_md.py                숫자 표로 싣는다 (마크다운은 그림을 못 담는다)
              └─ export_html.py              인라인 SVG 로 그린다 (인쇄용 · CDN 없음)

    ⚠️ **계열을 만드는 규칙은 여기 한 곳에만 둔다.** `linkcheck.py` ↔ `research.js`
       처럼 같은 규칙을 두 곳에 두면 어긋날 때 어느 쪽이 거짓말인지 알 수 없게 된다
       (요약본 §9.3). 렌더러 세 개는 **그리는 방법**만 다르고 **값은 안 만든다.**

지키는 규칙
    · **차트는 H05 가 선언한 명세에만 붙는다.** 명세 없는 차트를 만들지 않는다 —
      그러면 질문·출처·금지 오해가 없는 그림이 리포트에 실리게 된다.
    · **못 그리면 못 그린다고 적는다.** `drawable=False` + `reason`. 조용히 빼면
      화면이 "차트 4개" 라고 말하면서 3개만 그리는 일이 생긴다 (불변원칙 §2-3).
    · **값이 없는 자리를 0 으로 채우지 않는다.** 그 점을 빼고 뺐다는 사실을 적는다.
    · **자르면 자른 것을 밝힌다.** 상위 N 개만 그릴 때 몇 개를 잘랐는지 `note` 에 쓴다.

U3 표시 규칙 (`app.css:11~33`)
    · **Y축 두 개(이중축) 금지** (`app.css:30`). 단위가 다르면 차트를 나누거나
      100 기준으로 지수화한다. 그래서 `bar-line` 은 두 계열의 **단위가 같을 때만** 쓴다.
    · 범주형 색은 슬롯 순서대로. 상태색을 계열 색으로 재사용하지 않는다.
    · 값만으로 방향을 말하지 않는다 — 부호·라벨을 함께 낸다.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# 렌더러 세 개가 아는 유형. `C4_visual.chart_type` 은 한국어 산문이라
# (`"선 + 막대 조합"` · `"신호 3단 패널"`) 그대로는 어느 렌더러에도 못 넘긴다.
KINDS = (
    "bar-h",      # 가로 막대 — 부문별 매출 · 산업 시총 · 후보 점수
    "bar",        # 세로 막대 — 시나리오별 1위 빈도
    "bar-line",   # 막대 + 선 (**같은 단위 · 한 축**) — 매출 + 영업이익
    "line",       # 선 — 생산지수 · 업종지수
    "scatter",    # 산점도 — 피어 PER × ROE
    "range",      # 팬차트 — 예측 점추정 + 95% 구간
    "radar",      # 레이더 — Quick Score 6차원 (기회/부담 분리)
    "timeline",   # 타임라인 — 공시 이벤트 (x 날짜 · y 성격)
    "signal",     # 신호 판 — 사이클 3표 (차트가 아니라 방향 표시판이다)
    "stat",       # 값 하나 — 막대 하나짜리 차트는 **차트가 아니다** (아래 `_maybe_stat`)
)

TOP_N_BAR = 10          # 가로 막대에 몇 개까지 세울지. 넘치면 **자른 수를 밝힌다**
TIMELINE_MAX = 40       # 타임라인 점 상한. 최근 것부터 남긴다


# ─────────────────────────────────────────────────────────────
# 표기 — 세 렌더러가 **같은 글자**를 찍게 하려고 여기서 문자열까지 만든다
# ─────────────────────────────────────────────────────────────
def _num(value, digits: int = 1) -> str:
    """숫자 → 표기. 값이 없으면 **0 이 아니라 물결표**다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:,.{digits}f}"


def _trillion(value, digits: int = 1) -> str:
    """원 → 조 표기. `export_md._trillion` 과 같은 자리수를 쓴다 —
    리포트 본문과 차트 표가 같은 수를 다르게 적으면 읽는 사람이 둘을 못 맞춘다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value / 1e12:,.{digits}f}"


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _spec_index(spec: Dict) -> int:
    """`V-CORP-R-0002` → 2.

    `chart_type` 으로 갈라 쓸 수 없다 — `"가로 막대"` 가 기업(부문별 매출)과
    산업(시총 분포) **두 곳에 쓰인다** (`stages.py:1480` · `1539`).
    번호는 `h05_visualize` 의 `spec(n, …)` 호출 순서라 작업별로 고정돼 있다.
    """
    tail = str(spec.get("id", "")).rsplit("-", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def _chart(spec: Dict, kind: str, categories: Sequence, series: List[Dict],
           table: Optional[Dict] = None, unit: str = "", extra_note: str = "",
           axis_x: str = "", axis_y: str = "") -> Dict:
    """차트 한 개. 명세가 적어 둔 질문·출처·금지 오해를 **그대로 물려받는다.**"""
    axis = spec.get("axis") or {}
    note = str(spec.get("forbidden_misread") or "")
    if extra_note:
        note = f"{note} / {extra_note}" if note else extra_note
    return {
        "id": spec.get("id", ""),
        "kind": kind,
        "title": spec.get("research_question", ""),
        "chart_type": spec.get("chart_type", ""),
        "unit": unit or str(spec.get("unit") or ""),
        "axis": {"x": axis_x or axis.get("x", ""), "y": axis_y or axis.get("y", "")},
        "categories": list(categories),
        "series": series,
        "table": table or {"head": [], "rows": []},
        "note": note,
        "sources": list(spec.get("sources") or []),
        "data_ids": list(spec.get("data_ids") or []),
        "drawable": True,
        "reason": "",
    }


def _maybe_stat(chart: Dict) -> Dict:
    """막대 하나짜리 차트는 **값 하나로 낸다.**

    한 칸짜리 막대는 차트가 아니다 — 길이를 견줄 상대가 없으니 축과 격자가
    아무 일도 하지 않는다. 실제로 걸린다: IND-TP 민감도에서 후보가 한 곳이면
    `단독 1위 17회` 라는 막대 하나가 나온다 (261 반도체 제조업 실측).

    값·라벨·단위는 그대로 두고 `kind` 만 바꾼다. 표는 이미 만들어 둔 것을 쓴다.
    """
    if chart["kind"] not in ("bar", "bar-h"):
        return chart
    if len(chart["categories"]) != 1 or len(chart["series"]) != 1:
        return chart
    data = chart["series"][0].get("data") or []
    if len(data) != 1:
        return chart
    chart["kind"] = "stat"
    chart["series"][0]["kind"] = "stat"
    extra = "견줄 상대가 하나뿐이라 막대로 그리지 않고 값으로 낸다"
    chart["note"] = f"{chart['note']} / {extra}" if chart["note"] else extra
    return chart


def _undrawable(spec: Dict, reason: str) -> Dict:
    """명세는 있는데 그릴 값을 못 찾은 경우.

    **빼지 않고 남긴다.** 빼 버리면 화면이 "차트 4개" 라고 세면서 3개만 그리게 되고,
    왜 하나가 없는지 아무도 모른다.
    """
    empty = _chart(spec, "line", [], [])
    empty["drawable"] = False
    empty["reason"] = reason
    return empty


# ─────────────────────────────────────────────────────────────
# 기업 계열 (CORP-R · CORP-TP)
# ─────────────────────────────────────────────────────────────
_TOTAL_LABELS = ("총계", "총 계", "합계", "합 계", "계", "소계", "소 계")


def _segment_unit(rows: List[Dict], revenue) -> tuple:
    """부문 표의 단위를 **알아낸다** (억원? 백만원?).

    사업보고서는 표마다 `(단위: 백만원)` 처럼 캡션에 단위를 쓰는데 `_parse_segments`
    는 그 캡션을 읽지 않는다. 그래서 `h05` 의 명세는 단위를 `백만원` 이라고
    **단정해 두었지만 회사마다 다르다** — 삼성전자 2025 부문 합계는 3,336,059 이고
    같은 해 연결 매출은 333.6조라 이 표의 단위는 **억원**이다.

    우리는 두 가지 사실을 이미 갖고 있다 — 부문 합계와 연결 매출액.
    그 비(比)가 1e8 에 가까우면 억원, 1e6 이면 백만원이다. 어느 쪽도 아니면
    **모른다고 말한다.** 지어내지 않는다 (불변원칙 §2-1).
    """
    total = sum(r["values"][0] for r in rows if r.get("values") and _is_num(r["values"][0]))
    if not _is_num(revenue) or not total:
        return "", "부문 표의 단위를 확인하지 못했다 — 사업보고서 표기를 따른다"
    ratio = abs(revenue) / abs(total)
    for scale, label in ((1e8, "억원"), (1e6, "백만원"), (1e3, "천원"), (1.0, "원")):
        if 0.8 * scale <= ratio <= 1.25 * scale:
            return label, f"단위는 부문 합계와 연결 매출액의 비로 알아냈다 ({label})"
    return "", "부문 표의 단위를 확인하지 못했다 — 사업보고서 표기를 따른다"


def _segments(spec: Dict, analysis: Dict) -> Dict:
    """부문별 매출 가로 막대."""
    block = ((analysis.get("report_facts") or {}).get("segments") or {})
    rows = [r for r in block.get("rows", [])
            if r.get("values") and _is_num(r["values"][0])
            and str(r.get("segment", "")).strip() not in _TOTAL_LABELS]
    if not rows:
        return _undrawable(spec, "사업보고서에서 부문별 금액을 찾지 못했다")

    revenue = ((analysis.get("financial") or {}).get("series") or [{}])[-1].get("revenue")
    # ⚠️ 합계 행을 뺀 `rows` 를 넘긴다. 총계까지 더하면 합이 두 배가 되어
    #    단위 판정이 어긋난다 (삼성전자 실측에서 겪었다 — 3,336,059 가 6,672,118 이 됐다).
    unit, unit_note = _segment_unit(rows, revenue)

    labels = [str(r["segment"]) for r in rows]
    values = [r["values"][0] for r in rows]
    # 비중은 원문 표에 두 번째 숫자로 들어 있을 때만 쓴다. 없으면 우리가 계산하지 않는다
    # — 부문 매출은 내부거래 제거 전후가 달라 합이 100% 가 아닐 수 있다.
    shares = [r["values"][1] if len(r["values"]) > 1 and _is_num(r["values"][1]) else None
              for r in rows]
    head = ["부문", f"매출액{f' ({unit})' if unit else ''}"]
    if any(s is not None for s in shares):
        head.append("비중 (원문)")
    table = {"head": head,
             "rows": [[labels[i], _num(values[i], 0)]
                      + ([f"{_num(shares[i], 1)}%" if shares[i] is not None else "—"]
                         if len(head) == 3 else [])
                      for i in range(len(rows))]}
    # 단위를 못 알아냈으면 **명세의 단위를 그대로 쓰지 않는다.** `h05` 는 `백만원` 이라고
    # 적어 두었지만 그것은 회사마다 다른 값을 단정한 것이다 (삼성전자는 억원이었다).
    # 모를 때 "백만원" 이라고 찍으면 헤더가 사실과 다른 단위를 말하게 된다 (불변원칙 §2-1).
    return _chart(spec, "bar-h", labels,
                  [{"name": "매출액", "kind": "bar", "data": values}],
                  table=table, unit=unit or "원문 표기", extra_note=unit_note)


def _financial_series(spec: Dict, analysis: Dict) -> Dict:
    """매출(막대) + 영업이익(선). **둘 다 조원이라 축이 하나다** (U3 이중축 금지)."""
    rows = (analysis.get("financial") or {}).get("series") or []
    usable = [r for r in rows if _is_num(r.get("revenue"))]
    if not usable:
        return _undrawable(spec, "연도별 매출액을 찾지 못했다")

    years = [str(r.get("year", "")) for r in usable]
    revenue = [round(r["revenue"] / 1e12, 3) for r in usable]
    # 영업이익이 없는 해는 **0 이 아니라 빈 자리**로 둔다 (금융업은 계정이 다르다)
    profit = [round(r["operating_income"] / 1e12, 3) if _is_num(r.get("operating_income"))
              else None for r in usable]
    missing = [years[i] for i, v in enumerate(profit) if v is None]

    series = [{"name": "매출액", "kind": "bar", "data": revenue}]
    if any(v is not None for v in profit):
        series.append({"name": "영업이익", "kind": "line", "data": profit})
    table = {"head": ["회계연도", "매출액 (조원)", "영업이익 (조원)"],
             "rows": [[years[i], _trillion(usable[i].get("revenue")),
                       _trillion(usable[i].get("operating_income"))]
                      for i in range(len(usable))]}
    note = (f"영업이익을 찾지 못한 해가 있다 ({', '.join(missing)}) — 0 으로 채우지 않고 비웠다"
            if missing else "")
    return _chart(spec, "bar-line", years, series, table=table, unit="조원", extra_note=note)


def _peer_scatter(spec: Dict, analysis: Dict) -> Dict:
    """피어 PER × ROE 산점도. **적자 기업의 PER 은 찍지 않는다** (명세의 금지 오해)."""
    table_block = (analysis.get("peers") or {}).get("table") or {}
    rows = table_block.get("rows") or []
    target = table_block.get("target") or {}

    def point(row: Dict) -> Optional[Dict]:
        per, roe = row.get("per"), row.get("roe")
        if not (_is_num(per) and _is_num(roe)) or per <= 0:
            return None
        return {"x": round(roe, 3), "y": round(per, 3), "label": row.get("name", "")}

    peers = [p for p in (point(r) for r in rows) if p]
    if not peers:
        return _undrawable(spec, "PER·ROE 를 함께 가진 피어가 없다 (적자 기업의 PER 은 찍지 않는다)")

    dropped = len(rows) - len(peers)
    series = [{"name": "피어", "kind": "scatter", "data": peers}]
    self_point = point(target)
    if self_point:
        series.insert(0, {"name": target.get("name") or "대상", "kind": "scatter",
                          "data": [self_point]})

    head = ["종목", "PER (배)", "PBR (배)", "ROE (%)", "시가총액 (조원)"]
    body = []
    if target:
        body.append([f"{target.get('name','')} (대상)", _num(target.get("per"), 2),
                     _num(target.get("pbr"), 2), _num(target.get("roe"), 2),
                     _trillion(target.get("market_cap"))])
    body += [[r.get("name", ""), _num(r.get("per"), 2), _num(r.get("pbr"), 2),
              _num(r.get("roe"), 2), _trillion(r.get("market_cap"))] for r in rows]
    note = f"PER 이 없거나 적자인 피어 {dropped}곳은 찍지 않았다" if dropped else ""
    return _chart(spec, "scatter", [], series,
                  table={"head": head, "rows": body}, extra_note=note,
                  axis_x="ROE (%)", axis_y="PER (배)")


def _forecast_band(spec: Dict, analysis: Dict) -> Dict:
    """예측 팬차트 — 점추정 + 95% 구간."""
    forecast = analysis.get("forecast") or {}
    stat = forecast.get("statistical") or {}
    dates = forecast.get("future_dates") or []
    point = stat.get("point") or []
    low = stat.get("lower95") or []
    high = stat.get("upper95") or []
    span = min(len(dates), len(point), len(low), len(high))
    if span < 2:
        return _undrawable(spec, "예측 구간을 만들지 못했다 (모형이 서지 않았다)")

    labels = [str(d) for d in dates[:span]]
    series = [
        {"name": "95% 하한", "kind": "area", "data": [round(v) for v in low[:span]]},
        {"name": "95% 상한", "kind": "area", "data": [round(v) for v in high[:span]]},
        {"name": f"{stat.get('model', '예측')} 점추정", "kind": "line",
         "data": [round(v) for v in point[:span]]},
    ]
    table = {"head": ["거래일", "하한 (원)", "점추정 (원)", "상한 (원)"],
             "rows": [[labels[i], _num(low[i], 0), _num(point[i], 0), _num(high[i], 0)]
                      for i in range(span)]}
    last = forecast.get("last_close")
    note = (f"기준 종가 {_num(last, 0)}원 ({forecast.get('last_date', '')}) 에서 출발한다"
            if _is_num(last) else "")
    return _chart(spec, "range", labels, series, table=table, unit="원", extra_note=note)


def _event_timeline(spec: Dict, analysis: Dict) -> Dict:
    """공시 이벤트 타임라인. y 는 사건 성격(구조/일회성/예정)이다."""
    events = (analysis.get("events") or {}).get("events") or []
    usable = [e for e in events if e.get("date") and e.get("kind")]
    if not usable:
        return _undrawable(spec, "기간 안에 분류된 공시가 없다")

    ordered = sorted(usable, key=lambda e: str(e.get("date")))
    shown = ordered[-TIMELINE_MAX:]
    cut = len(ordered) - len(shown)
    kinds = ["구조", "일회성", "예정"]
    series = []
    for kind in kinds:
        picked = [e for e in shown if e.get("kind") == kind]
        if picked:
            series.append({"name": kind, "kind": "scatter",
                           "data": [{"x": str(e["date"]), "y": kind,
                                     "label": e.get("title", ""),
                                     "weight": e.get("weight", 1)} for e in picked]})
    counts = (analysis.get("events") or {}).get("counts") or {}
    table = {"head": ["날짜", "성격", "공시", "가중"],
             "rows": [[str(e.get("date", "")), str(e.get("kind", "")),
                       str(e.get("title", "")), _num(e.get("weight"), 0)]
                      for e in reversed(shown)]}
    note = (f"전체 {len(ordered)}건 중 최근 {len(shown)}건만 찍었다 (뺀 것 {cut}건) · "
            f"성격별 구조 {counts.get('구조', 0)} · 일회성 {counts.get('일회성', 0)} · "
            f"예정 {counts.get('예정', 0)}") if cut else \
        (f"성격별 구조 {counts.get('구조', 0)} · 일회성 {counts.get('일회성', 0)} · "
         f"예정 {counts.get('예정', 0)}")
    return _chart(spec, "timeline", [], series, table=table, extra_note=note,
                  axis_x="날짜", axis_y="사건 성격")


def _score_radar(spec: Dict, analysis: Dict) -> Dict:
    """Quick Score 6차원 레이더.

    **기회와 부담을 갈라서 그린다.** `direction=-1` 인 축(밸류에이션 부담·위험)은
    높을수록 불리하므로 같은 계열에 섞으면 "바깥으로 넓으면 좋다" 로 읽혀
    결론이 뒤집힌다 (명세의 금지 오해 그대로다).
    """
    rows = (analysis.get("scorecard") or {}).get("rows") or []
    scored = [r for r in rows if not r.get("unscored") and _is_num(r.get("score"))]
    if not scored:
        return _undrawable(spec, "점수가 매겨진 차원이 없다 (전부 Unscored 다)")

    labels = [str(r.get("name") or r.get("key")) for r in scored]
    good = [r["score"] if r.get("direction", 1) >= 0 else None for r in scored]
    load = [r["score"] if r.get("direction", 1) < 0 else None for r in scored]
    series = []
    if any(v is not None for v in good):
        series.append({"name": "기회 (높을수록 유리)", "kind": "radar", "data": good})
    if any(v is not None for v in load):
        series.append({"name": "부담 (높을수록 불리)", "kind": "radar", "data": load})

    unscored = [str(r.get("name") or r.get("key")) for r in rows if r.get("unscored")]
    table = {"head": ["차원", "점수 (0~5)", "방향"],
             "rows": [[str(r.get("name") or r.get("key")), _num(r.get("score"), 0),
                       str(r.get("direction_label", ""))] for r in rows]}
    note = (f"Unscored {len(unscored)}개는 찍지 않았다 ({', '.join(unscored)}) — "
            f"0 점과 다르다") if unscored else ""
    return _chart(spec, "radar", labels, series, table=table, unit="점", extra_note=note)


# ─────────────────────────────────────────────────────────────
# 산업 계열 (IND-R · IND-TP)
# ─────────────────────────────────────────────────────────────
def _industry_caps(spec: Dict, analysis: Dict) -> Dict:
    """산업 안 시가총액 분포 (가로 막대)."""
    market = analysis.get("market") or {}
    rows = [r for r in market.get("rows", []) if _is_num(r.get("market_cap"))]
    if not rows:
        return _undrawable(spec, "구성 종목의 시가총액을 찾지 못했다")

    ordered = sorted(rows, key=lambda r: -r["market_cap"])
    shown = ordered[:TOP_N_BAR]
    cut = len(ordered) - len(shown)
    total = sum(r["market_cap"] for r in ordered)
    labels = [str(r.get("name", "")) for r in shown]
    values = [round(r["market_cap"] / 1e12, 3) for r in shown]
    table = {"head": ["종목", "시가총액 (조원)", "산업 내 비중 (%)"],
             "rows": [[str(r.get("name", "")), _trillion(r.get("market_cap")),
                       _num(r["market_cap"] / total * 100, 1) if total else "—"]
                      for r in shown]}
    note = (f"상장 {len(ordered)}곳 중 상위 {len(shown)}곳만 세웠다 (뺀 것 {cut}곳 · "
            f"상위 {len(shown)}곳이 산업 시총의 "
            f"{_num(sum(r['market_cap'] for r in shown) / total * 100, 1)}%)"
            if cut and total else "")
    return _chart(spec, "bar-h", labels,
                  [{"name": "시가총액", "kind": "bar", "data": values}],
                  table=table, unit="조원", extra_note=note)


def _production_line(spec: Dict, analysis: Dict) -> Dict:
    """생산지수 추이 (선)."""
    production = ((analysis.get("market") or {}).get("production") or {})
    labels = production.get("labels") or []
    values = production.get("values") or []
    span = min(len(labels), len(values))
    if span < 2:
        return _undrawable(spec, "생산지수 시계열을 받지 못했다")

    name = str(production.get("series_name") or "생산지수")
    table = {"head": ["기간", name],
             "rows": [[str(labels[i]), _num(values[i], 1)] for i in range(span)]}
    note = str(production.get("substitute_note") or "") if production.get("substituted") else ""
    return _chart(spec, "line", [str(x) for x in labels[:span]],
                  [{"name": name, "kind": "line", "data": list(values[:span])}],
                  table=table, unit="지수", extra_note=note)


def _index_line(spec: Dict, analysis: Dict) -> Dict:
    """업종 주가지수 (100 기준 · 선)."""
    index = analysis.get("index") or {}
    dates = index.get("dates") or []
    values = index.get("values") or []
    span = min(len(dates), len(values))
    if span < 2:
        return _undrawable(spec, "업종 지수를 만들지 못했다 (구성 종목 시세가 없다)")

    # 표는 250행을 다 싣지 않는다 — 리포트가 읽히지 않는다. 월 단위로 솎되 **솎은 것을 밝힌다.**
    step = max(1, span // 24)
    picked = list(range(0, span, step))
    if picked[-1] != span - 1:
        picked.append(span - 1)
    table = {"head": ["거래일", "지수 (100 기준)"],
             "rows": [[str(dates[i]), _num(values[i], 1)] for i in picked]}
    note = (f"구성 {index.get('members_used', '—')}/{index.get('members_total', '—')}곳 "
            f"(커버리지 {_num(index.get('coverage_pct'), 1)}%) · "
            f"표는 {span}일 중 {len(picked)}개 시점만 실었다 (차트는 전부 그린다)")
    return _chart(spec, "line", [str(x) for x in dates[:span]],
                  [{"name": "업종 지수", "kind": "line", "data": list(values[:span])}],
                  table=table, unit="100 기준", extra_note=note)


def _cycle_signal(spec: Dict, analysis: Dict) -> Dict:
    """사이클 신호 3단 판.

    선그래프로 겹치지 않는다 — 세 신호의 x 축이 서로 다르다 (경기지수는 월,
    업종지수는 거래일). 다른 축을 억지로 한 그림에 겹치면 U3 이중축 금지를 어기고,
    맞추려고 리샘플링하면 없는 값을 만드는 것이 된다. **방향 표시판으로 낸다.**
    """
    cycle = analysis.get("cycle") or {}
    votes = cycle.get("votes") or []
    if not votes:
        return _undrawable(spec, "사이클 판정에 쓸 신호가 없다")

    rows = [{"label": str(v.get("source", "")), "value": str(v.get("signal", "")),
             "up": bool(v.get("up")), "detail": str(v.get("detail", ""))} for v in votes]
    table = {"head": ["신호", "판정", "근거"],
             "rows": [[r["label"], r["value"], r["detail"]] for r in rows]}
    note = (f"종합 국면 {cycle.get('phase', '—')} (신뢰도 {cycle.get('confidence', '—')}) · "
            f"{cycle.get('vote_summary', '')}")
    return _chart(spec, "signal", [r["label"] for r in rows],
                  [{"name": "신호", "kind": "signal", "data": rows}],
                  table=table, extra_note=note, axis_x="신호", axis_y="방향")


def _ranking_bar(spec: Dict, analysis: Dict) -> Dict:
    """후보별 점수 가로 막대. **coverage 를 함께 찍는다.**

    결측이 많은 후보는 penalty 로 낮게 나온다 — 그것은 '나쁘다' 와 다르다.
    그래서 adjusted 와 observed 를 나란히 두고 coverage 를 표에 싣는다.
    """
    ranking = analysis.get("ranking") or []
    rows = [r for r in ranking if _is_num(r.get("adjusted_score"))]
    if not rows:
        return _undrawable(spec, "점수를 매긴 후보가 없다")

    ordered = sorted(rows, key=lambda r: -r["adjusted_score"])
    shown = ordered[:TOP_N_BAR]
    cut = len(ordered) - len(shown)
    labels = [str(r.get("name", "")) for r in shown]
    series = [{"name": "adjusted (penalty 반영)", "kind": "bar",
               "data": [round(r["adjusted_score"], 3) for r in shown]}]
    if any(_is_num(r.get("observed_score")) for r in shown):
        series.append({"name": "observed (penalty 전)", "kind": "bar",
                       "data": [round(r["observed_score"], 3)
                                if _is_num(r.get("observed_score")) else None
                                for r in shown]})
    table = {"head": ["순위", "종목", "adjusted", "observed", "coverage (%)", "신뢰도"],
             "rows": [[_num(r.get("rank"), 0), str(r.get("name", "")),
                       _num(r.get("adjusted_score"), 1), _num(r.get("observed_score"), 1),
                       _num(r.get("coverage_display"), 0), str(r.get("confidence", ""))]
                      for r in shown]}
    note = f"후보 {len(ordered)}곳 중 상위 {len(shown)}곳만 세웠다 (뺀 것 {cut}곳)" if cut else ""
    return _chart(spec, "bar-h", labels, series, table=table, unit="점", extra_note=note)


def _sensitivity_bar(spec: Dict, analysis: Dict) -> Dict:
    """시나리오별 1위 빈도 (세로 막대). 동점은 공동 1위로 센다."""
    sensitivity = analysis.get("sensitivity") or {}
    freq = sensitivity.get("leader_frequency") or []
    if not freq:
        return _undrawable(spec, "민감도 시나리오를 돌리지 못했다")

    # 빈도표에는 종목명이 없다 — 순위표에서 찾아 붙인다 (없으면 코드를 그대로 쓴다)
    names = {str(r.get("code")): str(r.get("name") or r.get("code"))
             for r in (analysis.get("ranking") or [])}
    labels = [names.get(str(f.get("code")), str(f.get("code"))) for f in freq]
    total = sensitivity.get("scenario_count") or 0
    series = [{"name": "단독 1위", "kind": "bar", "data": [f.get("solo", 0) for f in freq]}]
    if any(f.get("shared") for f in freq):
        series.append({"name": "공동 1위", "kind": "bar",
                       "data": [f.get("shared", 0) for f in freq]})
    table = {"head": ["종목", "단독 1위", "공동 1위", "합계", f"전체 {total}회 중 비율 (%)"],
             "rows": [[labels[i], _num(f.get("solo"), 0), _num(f.get("shared"), 0),
                       _num(f.get("total"), 0),
                       _num((f.get("ratio") or 0) * 100, 0)] for i, f in enumerate(freq)]}
    note = (f"시나리오 {total}회 · 안정성 {sensitivity.get('stability', '—')} — "
            f"{sensitivity.get('stability_why', '')}")
    return _chart(spec, "bar", labels, series, table=table, unit="회", extra_note=note)


# ─────────────────────────────────────────────────────────────
# 분배 — 명세 번호가 곧 무엇을 그리는지다 (`h05_visualize` 의 `spec(n, …)` 순서)
# ─────────────────────────────────────────────────────────────
_CORP_BUILDERS = {
    1: _segments,
    2: _financial_series,
    3: _peer_scatter,
    4: _forecast_band,
    5: _event_timeline,      # CORP-TP 전용
    6: _score_radar,         # CORP-TP 전용
}

_INDUSTRY_BUILDERS = {
    1: _industry_caps,
    2: _production_line,
    3: _index_line,
    4: _cycle_signal,
    5: _ranking_bar,         # IND-TP 전용
    6: _sensitivity_bar,     # IND-TP 전용
}


def build(pack: Dict, analysis: Dict) -> List[Dict]:
    """`C4_visual` 명세마다 그릴 계열을 만든다.

    **명세를 늘리거나 줄이지 않는다.** 들어온 명세 수와 나가는 차트 수가 같다.
    그릴 값이 없으면 `drawable=False` 로 남겨 왜 못 그렸는지 밝힌다.
    """
    workstream = (pack.get("C0_charter") or {}).get("workstream_id", "")
    builders = _INDUSTRY_BUILDERS if workstream.startswith("IND") else _CORP_BUILDERS

    charts: List[Dict] = []
    for spec in pack.get("C4_visual", []):
        builder = builders.get(_spec_index(spec))
        if not builder:
            charts.append(_undrawable(spec, "이 명세를 그리는 규칙이 아직 없다"))
            continue
        try:
            built = builder(spec, analysis or {})
            charts.append(_maybe_stat(built) if built.get("drawable") else built)
        except Exception as error:            # 차트 하나가 리서치를 죽이지 않는다 (§2-2)
            charts.append(_undrawable(spec, f"계열을 만들다 실패했다 — {error}"))
    return charts


def summary(charts: Sequence[Dict]) -> Dict:
    """몇 개를 그릴 수 있는지. 화면·리포트가 **못 그린 수까지** 밝히는 데 쓴다."""
    drawable = [c for c in charts if c.get("drawable")]
    return {
        "total": len(charts),
        "drawable": len(drawable),
        "skipped": [{"id": c.get("id", ""), "title": c.get("title", ""),
                     "reason": c.get("reason", "")}
                    for c in charts if not c.get("drawable")],
        "kinds": sorted({c.get("kind", "") for c in drawable}),
    }
