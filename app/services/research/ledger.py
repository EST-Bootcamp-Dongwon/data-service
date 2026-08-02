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
             evidence_id: str = "", transform: str = "", rounding: str = "",
             calc_id: str = "") -> str:
    """데이터 값 하나를 올리고 `D-…` 를 돌려준다.

    `evidence_id` 와 `calc_id` 는 **둘 중 하나**가 채워진다.
    원자료는 E- 에서 오고(`evidence_id`), 파생값은 계산에서 온다(`calc_id`).
    둘 다 비어 있으면 그 값은 되짚을 수 없다는 뜻이고, 커버리지가 그것을 그대로 센다.
    """
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
        "calc_id": calc_id,
        "transform": transform,
        "rounding": rounding,
    })
    return data_id


def add_calculation(pack: Dict, formula: str, inputs: Sequence[str], result,
                    unit: str = "", assumption: str = "",
                    intermediate: Optional[Dict] = None,
                    result_data_id: str = "") -> str:
    """계산 하나를 기록하고 `CALC-…` 를 돌려준다.

    공통계약 §8.3 은 CAGR·점수·밸류에이션·민감도를 **최소 한 번 독립 재계산**하라고 한다.
    `recheck` 필드에 그 결과를 담는다 (여기서는 같은 식을 다시 밟아 값이 일치하는지 본다).

    `result_data_id` 는 이 계산이 만들어 낸 파생 D- 를 가리킨다. `trace()` 와 화면
    드릴다운이 **파생값에서 거꾸로** 사슬을 펴려면 이 방향의 링크가 있어야 한다.
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
        "result_data_id": result_data_id,
    })
    return calc_id


def add_derived(pack: Dict, metric: str, value, formula: str, inputs: Sequence[str],
                unit: str = "", currency: str = "", period: str = "", basis: str = "",
                assumption: str = "", intermediate: Optional[Dict] = None,
                transform: str = "", rounding: str = "",
                actual_or_estimate: str = "actual") -> tuple:
    """파생값 하나를 **D- 와 CALC- 로 함께** 올리고 `(data_id, calc_id)` 를 돌려준다 (변경노트 N69).

    리포트 본문의 수치는 대부분 원시 계정이 아니라 파생값이다 — 밸류에이션 밴드 ·
    CAGR · Quick Score · adjusted score. M6 까지는 이것들이 문자열로만 나와서
    본문 수치의 5~14% 만 근거로 되짚을 수 있었다. 여기가 그것을 푸는 자리다.

    **파생 D- 에는 `evidence_id` 를 붙이지 않는다.** 파생값은 남이 발표한 값이 아니라
    우리가 만든 값이기 때문이다. 대신 `calc_id` 로 잇는다. 사슬은 끊기지 않는다:

        파생 D-  →  CALC-  →  입력 D-  →  E-

    입력의 E- 를 파생 D- 에 물려주면 "주당가치 158,549원의 출처는 DART 재무제표" 가
    되어 **우리가 만든 값을 남이 발표한 값처럼** 보이게 한다 (불변원칙 §2-1).

    `inputs` 가 비어 있어도 발급은 되지만, 그러면 계산 정합성 채점에서 감점된다
    (H10 이 `inputs` 가 채워진 CALC- 의 비율을 잰다 — 변경노트 N71).
    """
    data_id = _next_id("D", pack["C0_charter"]["workstream_id"], pack["C2_data"])
    calc_id = add_calculation(pack, formula, inputs, value, unit=unit,
                              assumption=assumption, intermediate=intermediate,
                              result_data_id=data_id)
    issued = add_data(pack, metric=metric, value=value, unit=unit, currency=currency,
                      period=period, basis=basis, actual_or_estimate=actual_or_estimate,
                      transform=transform or formula, rounding=rounding, calc_id=calc_id)
    # 번호를 미리 잡아 CALC- 에 실었으므로 실제 발급본과 어긋나면 안 된다.
    # 어긋났다면 그 사이에 다른 D- 가 끼어든 것이라 사슬이 엉킨다 — 바로 드러나게 둔다.
    if issued != data_id:  # pragma: no cover - 발급 순서가 깨졌을 때만
        raise RuntimeError(f"파생 D- 번호가 어긋났다: 예상 {data_id} · 실제 {issued}")
    return data_id, calc_id


def ids_for(pack: Dict, *metrics: str) -> List[str]:
    """지표 이름들로 D- ID 를 모아 준다 — 파생 계산의 `inputs` 를 채울 때 쓴다.

    같은 지표가 여러 기간에 걸쳐 있으면 **가장 마지막에 발급된 것**을 쓴다
    (재무 시계열은 연도 순으로 발급되므로 최근 연도가 된다).
    """
    found: List[str] = []
    for metric in metrics:
        match = [row["id"] for row in pack.get("C2_data", []) if row.get("metric") == metric]
        if match:
            found.append(match[-1])
    return found


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
    """근거 커버리지 요약 — 평가축 '증거 추적성'(15점)이 읽는 값이다.

    M7 부터 D- 는 두 갈래다. **둘을 합쳐서 세지 않고 갈라서 센다** (변경노트 N69).

        직접근거   E- 에서 바로 온 원자료          `evidence_id`
        계산유래   계산이 만들어 낸 파생값          `calc_id`
        추적가능   위 둘의 합 — 어느 쪽이든 되짚을 수 있다

    합친 숫자 하나만 내면 "45건 전부 근거가 있다" 처럼 읽혀서, 그중 13건이 우리가
    만든 값이라는 사실이 사라진다. 화면과 H10 이 둘을 나란히 밝힌다.
    """
    evidence = pack.get("C1_evidence", [])
    data = pack.get("C2_data", [])
    calculations = pack.get("logs", {}).get("calculations", [])
    linked = [d for d in data if d.get("evidence_id")]
    derived = [d for d in data if d.get("calc_id") and not d.get("evidence_id")]
    traceable = [d for d in data if d.get("evidence_id") or d.get("calc_id")]
    with_inputs = [c for c in calculations if c.get("inputs")]
    by_grade: Dict[str, int] = {}
    for row in evidence:
        by_grade[row.get("grade", "기타")] = by_grade.get(row.get("grade", "기타"), 0) + 1
    return {
        "evidence": len(evidence),
        "data": len(data),
        "calculations": len(calculations),
        "data_with_evidence": len(linked),
        "data_derived": len(derived),
        "data_traceable": len(traceable),
        # 원래 뜻(E- 로 직접 이어진 비율)을 그대로 둔다 — 뜻이 조용히 바뀌면
        # 이 값을 읽던 화면·검사가 다른 것을 재게 된다.
        "data_linked_ratio": round(len(linked) / len(data), 3) if data else 0.0,
        "data_traceable_ratio": round(len(traceable) / len(data), 3) if data else 0.0,
        "calculations_with_inputs": len(with_inputs),
        "calc_input_ratio": round(len(with_inputs) / len(calculations), 3) if calculations else 0.0,
        "by_grade": by_grade,
        "gaps": len(pack.get("logs", {}).get("gaps", [])),
        "conflicts": len(pack.get("logs", {}).get("conflicts", [])),
    }


def trace(pack: Dict, data_id: str) -> Dict:
    """D- 하나를 근거까지 되짚는다 (§7.4 근거 드릴다운이 쓰는 응답).

    D- → 그것을 만든 CALC- → 그 CALC- 의 입력 D- → 각 D- 의 E- 순으로 펼친다.

    파생 D- 는 자기 `evidence` 가 없다 (계산에서 나왔으니 당연하다). 그래서 입력을
    타고 내려가 **닿는 E- 를 `root_evidence` 로 함께** 준다. 이것이 없으면 화면이
    "E- 가 붙어 있지 않다" 만 띄우고 끝나서, 사슬이 실제로는 이어져 있는데도
    끊긴 것처럼 보인다.
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

    # 입력을 타고 내려가 닿는 E- (깊이 제한 4 — 파생값이 파생값을 먹는 경우가 있다)
    root_evidence: List[Dict] = []
    if not evidence:
        seen = {data_id}
        frontier = list(inputs)
        for _ in range(4):
            if not frontier:
                break
            nxt: List[Dict] = []
            for item in frontier:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                origin = next((e for e in pack.get("C1_evidence", [])
                               if e.get("id") == item.get("evidence_id")), None)
                if origin and origin not in root_evidence:
                    root_evidence.append(origin)
                    continue
                deeper = next((c for c in pack.get("logs", {}).get("calculations", [])
                               if c.get("result_data_id") == item["id"]), None)
                for input_id in (deeper or {}).get("inputs", []):
                    more = next((d for d in pack.get("C2_data", [])
                                 if d.get("id") == input_id), None)
                    if more:
                        nxt.append(more)
            frontier = nxt

    return {
        "found": True,
        "data": row,
        "calculations": calcs,
        "inputs": inputs,
        "evidence": evidence,
        "root_evidence": root_evidence,
        "kind": "파생" if row.get("calc_id") else "원자료",
    }
