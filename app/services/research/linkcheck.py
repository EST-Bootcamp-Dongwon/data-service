"""본문 수치 → 장부 연결 판정 (명세 §7.4 · 변경노트 N67 · M7 에서 서버로 옮김)

리포트 본문의 수치 중 **몇 개가 근거로 되짚어지는가**를 센다.
M6 까지 이 규칙은 브라우저(`static/assets/research.js` 의 `linkNumbers`)에만 있었다.
M7 에서 H10 이 이 비율을 점수로 재게 되면서(공통계약 §12.1 "핵심 주장→evidence_id"),
같은 규칙이 서버에도 필요해졌다.

⚠️ **같은 규칙이 두 곳에 있다.** 어긋나면 화면이 보여 주는 연결률과 점수가 달라진다.
   `tests/regress_ui.js` 6절이 같은 리포트·같은 팩을 양쪽에 넣고 (전체, 연결) 두 수가
   일치하는지 대조한다. 규칙을 고칠 때는 **반드시 양쪽을 함께** 고친다.

규칙 (보수적으로 — 확실할 때만 링크한다)
    · 날짜(2026-08-02)와 괄호 안 종목코드((000660))는 통째로 건너뛴다
    · 연도(19xx·20xx)는 세지 않는다
    · 뒤에 개수 단위(건·개·곳·장·회·차·위·명·종목·년·개월)가 오면 세지 않는다
    · 단위도 소수점도 자릿점도 없는 세 자리 이하 맨숫자는 개수·순위로 본다
    · 값이 D- 와 정확히 맞고 **같은 줄에 그 지표 이름이 있을 때만** 이었다고 센다
    · 값이 같은 D- 가 둘 이상이면 잇지 않는다 (아무 D- 나 갖다 붙이면 없는 사슬이 된다)
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Sequence

# D- 의 지표 이름은 영어다 (`revenue` · `operating_income` · `261.per` …).
# 본문은 한국어라 그대로는 못 맞춘다 — 본문에 이 낱말이 있을 때만 링크로 센다.
# ⚠️ `research.js` 의 `METRIC_LABELS` 와 **같은 표**다. 함께 고친다.
METRIC_LABELS: Dict[str, List[str]] = {
    "revenue": ["매출"],
    "operating_income": ["영업이익"],
    "net_income": ["순이익", "당기순이익"],
    "assets": ["자산"],
    "equity": ["자본"],
    "liabilities": ["부채"],
    "current_assets": ["유동자산"],
    "current_liabilities": ["유동부채"],
    "operating_cash_flow": ["영업현금흐름", "현금흐름"],
    "market_cap": ["시가총액", "시총"],
    "per": ["PER"],
    "pbr": ["PBR"],
    "roe": ["ROE"],
    "r250": ["수익률"],
    "생산지수": ["생산지수"],
    "close": ["종가", "주가"],
    "shares": ["주식수"],
    # ── 파생값 (M7 · 변경노트 N69) ──
    # 이름은 **본문에 실제로 쓰이는 낱말**이어야 한다. 지표 이름을 그대로 적으면
    # 한국어 본문과 영영 안 맞아서 링크가 걸리지 않는다.
    "revenue_cagr": ["CAGR"],
    "operating_margin": ["영업이익률"],
    "net_margin": ["순이익률"],
    "asset_turnover": ["자산회전율"],
    "equity_multiplier": ["재무레버리지"],
    "debt_ratio": ["부채비율"],
    "current_ratio": ["유동비율"],
    "cash_conversion": ["현금전환", "영업현금흐름/영업이익"],
    "eps": ["EPS"],
    "peer_median_per": ["피어 중앙값", "피어 PER"],
    "valuation_band_base": ["주당 가치", "주당가치", "기준"],
    "valuation_band_low": ["주당 가치", "주당가치"],
    "valuation_band_high": ["주당 가치", "주당가치"],
    "per_premium_pct": ["피어 대비"],
    "listed_market_cap": ["시가총액 합계", "상장 시가총액"],
    "cap_share_pct": ["시가총액 비중"],
    "cr3_pct": ["CR3"],
    "hhi": ["HHI"],
    "candidate_cap_share": ["업종 시총"],
    "coverage": ["coverage"],
}

_SKIP_ZONE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|\(\d{6}\)")
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_COUNT_SUFFIX = re.compile(r"^\s*(건|개|곳|장|회|차|위|명|종목|년|개월)")
_VALUE_SUFFIX = re.compile(r"^\s*(조|억|%|배|원|점|지수|p|pp)")


def labels_of(metric: str) -> List[str]:
    """`261.per` → `['PER']`. 표에 없는 지표는 이름 그대로 쓴다."""
    tail = str(metric or "").split(".")[-1]
    return METRIC_LABELS.get(tail) or ([tail] if tail else [])


def _to_fixed(value: float, digits: int) -> str:
    """자바스크립트 `Number.toFixed()` 와 같은 결과를 낸다.

    파이썬 기본 반올림은 '짝수로' 인데(2.5 → 2) 자바스크립트는 '올림' 이다(2.5 → 3).
    `Decimal(float)` 는 double 의 **정확한 이진값**을 주므로 그것을 ROUND_HALF_UP 으로
    자르면 자바스크립트와 같아진다.
    """
    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def _js_number(value: float) -> str:
    """자바스크립트 `String(number)` — 정수값이면 소수점을 붙이지 않는다."""
    if isinstance(value, bool):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def build_value_index(pack: Dict) -> Dict[str, List[Dict]]:
    """값 → D- 색인. 서버가 리포트에 쓴 **표기법 그대로** 열쇠를 만든다.

    `export_md._trillion` 이 원 단위를 조로 줄여 소수 1자리로 쓰므로(`80.1조`)
    그 표기도 열쇠에 넣는다. 넣지 않으면 본문의 `80.1조` 가 영영 안 이어진다.
    """
    index: Dict[str, List[Dict]] = {}

    def put(key: str, row: Dict) -> None:
        if not key:
            return
        bucket = index.setdefault(key, [])
        if row not in bucket:
            bucket.append(row)

    for row in pack.get("C2_data", []):
        value = row.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            continue
        put(_js_number(value), row)
        put(_to_fixed(value, 1), row)
        put(_to_fixed(value, 2), row)
        put(f"{math.floor(value + 0.5):,}", row)
        if abs(value) >= 1e12:
            put(_to_fixed(value / 1e12, 1), row)
    return index


def check_line(text: str, index: Dict[str, List[Dict]]) -> Dict:
    """본문 한 줄에서 (센 수치, 이어진 수치, 이어진 D- ID) 를 돌려준다.

    `research.js` 의 `linkNumbers` 와 **같은 판단**을 해야 한다 (HTML 만 안 만든다).
    """
    line = str(text or "")
    skip_zones = [(m.start(), m.end()) for m in _SKIP_ZONE.finditer(line)]

    total = 0
    linked: List[str] = []

    for match in _NUMBER.finditer(line):
        start, end = match.start(), match.end()
        if any(start >= s and end <= e for s, e in skip_zones):
            continue

        raw = match.group(0)
        after = line[end:]
        bare = raw.replace(",", "")
        if _YEAR.match(bare):
            continue                                    # 연도
        if _COUNT_SUFFIX.match(after):
            continue                                    # 개수 (건·개·곳 …)
        # 단위도 소수점도 자릿점도 없는 **작은 맨숫자**는 값이 아니라 개수·순위다
        # (`검사 5건 중 통과 4` 의 4). 장부에 있는 값이면 그대로 센다.
        if (not _VALUE_SUFFIX.match(after) and not re.search(r"[.,]", raw)
                and len(raw) <= 3 and raw not in index and bare not in index):
            continue

        total += 1
        candidates = index.get(raw) or index.get(bare) or []
        if not candidates:
            continue

        # 같은 줄에 지표 이름이 있는 것만 인정한다
        corroborated = [row for row in candidates
                        if any(label in line for label in labels_of(row.get("metric", "")))]
        pick: Optional[Dict] = None
        if len(corroborated) == 1:
            pick = corroborated[0]
        elif not corroborated and len(candidates) == 1 and _VALUE_SUFFIX.match(after):
            pick = candidates[0]                        # 단위가 붙은 유일한 후보
        if not pick:
            continue                                    # 여럿이면 잇지 않는다

        linked.append(pick.get("id", ""))

    return {"total": total, "linked": len(linked), "data_ids": linked}


def check_report(pack: Dict, report: Optional[Dict]) -> Dict:
    """리포트 전체의 본문 수치 연결률 (H10 증거 추적성이 읽는 값).

    `report` 가 없으면 0 을 돌려주되 그 사실을 `reason` 으로 밝힌다 —
    조용히 0% 를 내면 "본문이 근거와 안 이어진다" 로 잘못 읽힌다.
    """
    if not report or not report.get("pages"):
        return {"total": 0, "linked": 0, "ratio": 0.0, "data_ids": [],
                "reason": "리포트가 아직 조립되지 않아 본문 수치를 셀 수 없다"}

    index = build_value_index(pack)
    total = 0
    linked = 0
    ids: List[str] = []
    for page in report.get("pages", []):
        lines: Sequence[str] = [page.get("key_message", "")] + list(page.get("body", []))
        for line in lines:
            got = check_line(line, index)
            total += got["total"]
            linked += got["linked"]
            ids.extend(got["data_ids"])

    return {
        "total": total,
        "linked": linked,
        "ratio": round(linked / total, 3) if total else 0.0,
        "data_ids": sorted(set(ids)),
        "reason": "" if total else "본문에서 셀 수 있는 수치가 없다",
    }


if __name__ == "__main__":  # pragma: no cover
    # `tests/regress_ui.js` 6절이 부른다 — 브라우저 규칙과 같은 답을 내는지 대조하는 자리다.
    # **파일 경로로 직접 실행**하므로 패키지 import 사슬을 타지 않는다 (이 모듈은 표준
    # 라이브러리만 쓴다). 서버 없이 도는 회귀검사의 성질을 그대로 지키기 위해서다.
    #
    #     echo '{"C2_data": [...], "lines": ["..."]}' | python3 linkcheck.py
    import json
    import sys

    payload = json.load(sys.stdin)
    _index = build_value_index({"C2_data": payload.get("C2_data", [])})
    sys.stdout.write(json.dumps(
        [check_line(line, _index) for line in payload.get("lines", [])],
        ensure_ascii=False))
