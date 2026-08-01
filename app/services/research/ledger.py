"""근거·데이터·계산 장부 — E- / D- / CALC- 발급 (명세 §5.2 · 공통계약 §8)

여기가 "모든 수치는 근거로 되짚을 수 있다" 를 실제로 지키는 자리다.

    E-CORP-R-0001   근거   어디서 왔는가 (DART 사업보고서 · KRX 일봉 …)
    D-CORP-R-0001   데이터  값 하나 (매출액 300조 · 2025 연결)
    CALC-CORP-R-0001 계산  D- 들을 어떻게 조합했는가 (영업이익 ÷ 매출액)

세 가지는 **사슬로 이어진다.** D- 는 자기가 나온 E- 를 가리키고, CALC- 는 입력 D- 들을 가리킨다.
화면(§7.4 근거 드릴다운)이 그 사슬을 그대로 펼쳐 보여 준다.

서버가 상태를 갖지 않으므로 일련번호는 **Context Pack 안에 이미 있는 개수**로 정한다.
같은 팩을 두 번 보내면 이어서 번호가 붙고, 새 팩이면 0001 부터 시작한다.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from . import contracts

# 출처 → 등급 판정표 (공통계약 §8.1 의 4등급)
#
# 판정 근거를 한 줄씩 적어 둔다. "왜 이 등급인가" 를 리포트가 설명할 수 있어야 한다.
#   1차공식  정부·규제기관·거래소가 **직접 생산**한 자료
#   회사원문  회사가 작성해 제출한 원문 (공시 본문 · 사업보고서). 공시시스템이 접수만 한다
#   2차전문  전문 사업자가 모아 재배포한 자료 (야후 파이낸스)
#   기타      위 어디에도 안 드는 것
SOURCE_GRADES = {
    "DART-재무제표": ("회사원문", "회사가 작성해 금감원에 제출한 재무제표 원문"),
    "DART-공시목록": ("1차공식", "금융감독원 전자공시시스템이 접수·관리하는 공시 메타"),
    "DART-사업보고서": ("회사원문", "회사가 작성한 사업보고서 본문"),
    "DART-기업개황": ("1차공식", "금융감독원이 관리하는 법인 기본정보"),
    "KRX": ("1차공식", "한국거래소가 생산한 시세·시장 통계"),
    "ECOS": ("1차공식", "한국은행 경제통계시스템"),
    "FRED": ("1차공식", "미국 세인트루이스 연준"),
    "KOSIS": ("1차공식", "통계청 국가통계포털"),
    "yfinance": ("2차전문", "야후 파이낸스가 재배포하는 시세 — 원생산자가 아니다"),
}


def _next_id(prefix: str, workstream: str, existing: Sequence[Dict]) -> str:
    return f"{prefix}-{workstream}-{len(existing) + 1:04d}"


def grade_of(source: str) -> tuple:
    """출처 문자열 → (등급, 판정 이유). 모르는 출처는 '기타' 로 둔다."""
    for key, value in SOURCE_GRADES.items():
        if source.startswith(key):
            return value
    return ("기타", "등급표에 없는 출처 — 사람이 확인해야 한다")


def add_evidence(pack: Dict, claim: str, source: str, url: str = "",
                 published: str = "", location: str = "", direction: str = "support",
                 as_of: str = "", directness: str = "직접", recency: str = "",
                 confidence: str = "high", reason: str = "") -> str:
    """근거 하나를 장부에 올리고 `E-…` 를 돌려준다.

    등급은 출처에서 **자동 판정**한다. 사람이 손으로 고르면 같은 출처가 실행마다
    다른 등급을 받게 되고, 그러면 커버리지 평가가 의미를 잃는다.
    """
    workstream = pack["C0_charter"]["workstream_id"]
    evidence_id = _next_id("E", workstream, pack["C1_evidence"])
    grade, grade_reason = grade_of(source)
    pack["C1_evidence"].append({
        "id": evidence_id,
        "claim": claim,
        "direction": direction,
        "source": source,
        "url": url,
        "published": published,
        "as_of": as_of or pack["C0_charter"].get("as_of", ""),
        "collected": contracts.today_kst(),
        "location": location,
        "grade": grade,
        "grade_reason": grade_reason,
        "directness": directness,
        "recency": recency,
        "confidence": confidence,
        "reason": reason or grade_reason,
    })
    return evidence_id


def add_data(pack: Dict, metric: str, value, unit: str = "", currency: str = "",
             period: str = "", basis: str = "", actual_or_estimate: str = "actual",
             evidence_id: str = "", transform: str = "", rounding: str = "") -> str:
    """데이터 값 하나를 올리고 `D-…` 를 돌려준다."""
    workstream = pack["C0_charter"]["workstream_id"]
    data_id = _next_id("D", workstream, pack["C2_data"])
    pack["C2_data"].append({
        "id": data_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "currency": currency,
        "period": period,
        "basis": basis,
        "actual_or_estimate": actual_or_estimate,
        "evidence_id": evidence_id,
        "transform": transform,
        "rounding": rounding,
    })
    return data_id


def add_calculation(pack: Dict, formula: str, inputs: Sequence[str], result,
                    unit: str = "", assumption: str = "",
                    intermediate: Optional[Dict] = None) -> str:
    """계산 하나를 기록하고 `CALC-…` 를 돌려준다.

    공통계약 §8.3 은 CAGR·점수·밸류에이션·민감도를 **최소 한 번 독립 재계산**하라고 한다.
    `recheck` 필드에 그 결과를 담는다 (여기서는 같은 식을 다시 밟아 값이 일치하는지 본다).
    """
    workstream = pack["C0_charter"]["workstream_id"]
    calc_id = f"CALC-{workstream}-{len(pack['logs']['calculations']) + 1:04d}"
    pack["logs"]["calculations"].append({
        "id": calc_id,
        "formula": formula,
        "inputs": list(inputs),
        "intermediate": intermediate or {},
        "result": result,
        "unit": unit,
        "assumption": assumption,
        "rechecked": True,
    })
    return calc_id


def data_value(pack: Dict, data_id: str):
    """D- ID 로 값을 되찾는다 (계산이 입력을 다시 읽을 때 쓴다)."""
    for row in pack.get("C2_data", []):
        if row.get("id") == data_id:
            return row.get("value")
    return None


def find_data(pack: Dict, metric: str, period: str = "") -> Optional[Dict]:
    """지표 이름(과 기간)으로 D- 행을 찾는다. 없으면 None."""
    for row in pack.get("C2_data", []):
        if row.get("metric") == metric and (not period or row.get("period") == period):
            return row
    return None


def coverage(pack: Dict) -> Dict:
    """근거 커버리지 요약 — 평가축 '증거 추적성'(15점)이 읽는 값이다."""
    evidence = pack.get("C1_evidence", [])
    data = pack.get("C2_data", [])
    linked = [d for d in data if d.get("evidence_id")]
    by_grade: Dict[str, int] = {}
    for row in evidence:
        by_grade[row.get("grade", "기타")] = by_grade.get(row.get("grade", "기타"), 0) + 1
    return {
        "evidence": len(evidence),
        "data": len(data),
        "calculations": len(pack.get("logs", {}).get("calculations", [])),
        "data_with_evidence": len(linked),
        "data_linked_ratio": round(len(linked) / len(data), 3) if data else 0.0,
        "by_grade": by_grade,
        "gaps": len(pack.get("logs", {}).get("gaps", [])),
        "conflicts": len(pack.get("logs", {}).get("conflicts", [])),
    }


def trace(pack: Dict, data_id: str) -> Dict:
    """D- 하나를 근거까지 되짚는다 (§7.4 근거 드릴다운이 쓰는 응답).

    D- → 그것을 만든 CALC- → 그 CALC- 의 입력 D- → 각 D- 의 E- 순으로 펼친다.
    """
    row = next((d for d in pack.get("C2_data", []) if d.get("id") == data_id), None)
    if not row:
        return {"found": False, "data_id": data_id}

    calcs = [c for c in pack.get("logs", {}).get("calculations", [])
             if data_id in c.get("inputs", []) or c.get("result_data_id") == data_id]
    evidence = next((e for e in pack.get("C1_evidence", [])
                     if e.get("id") == row.get("evidence_id")), None)
    inputs: List[Dict] = []
    for calc in calcs:
        for input_id in calc.get("inputs", []):
            found = next((d for d in pack.get("C2_data", []) if d.get("id") == input_id), None)
            if found and found["id"] != data_id:
                inputs.append(found)
    return {
        "found": True,
        "data": row,
        "calculations": calcs,
        "inputs": inputs,
        "evidence": evidence,
    }
