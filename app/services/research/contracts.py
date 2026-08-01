"""GIC 하네스 공통계약 — Context Pack · 단계 결과 봉투 · 코드표 (명세 §5.2 · §5.3)

이 파일은 **계약**이다. 계산이 아니라 모양을 정한다.

지킬 것 두 가지.

1. **필드명을 바꾸지 않는다.** `GIC_v15_하네스_공통계약.md` §7 의 YAML 을 JSON 으로 옮긴 것이라,
   여기서 이름을 하나라도 바꾸면 나중에 4차 루프 엔지니어링을 붙일 때 계약이 깨진다.
   필드를 더하지도 빼지도 않는다.

2. **흔적 없이 덮어쓰지 않는다.** 어떤 값을 고치든 `C6_decisions.changes` 에
   무엇을·왜·누가·언제 바꿨는지 남긴다 (공통계약 §6). 그래서 값을 직접 대입하지 말고
   이 파일의 `record_change()` 를 거친다.

서버는 상태를 갖지 않는다 (명세 §1.3). Context Pack 은 브라우저가 들고 다니고,
서버는 받은 것을 고쳐 돌려줄 뿐이다.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GIC-HARNESS-1.0"
HARNESS_VERSION = "api-test-M4"
KST = timezone(timedelta(hours=9))

# 워크스트림 4종 — 명세 §5.4
WORKSTREAMS = {
    "CORP-R": {
        "id": "CORP-R",
        "name": "기업 리서치",
        "target_kind": "corp",
        "needs": ["종목코드 또는 종목명", "분석 기준일"],
        "sufficiency": "high",
        "sufficiency_label": "🟢 높음 (DART + yfinance)",
        "note": "H03 을 회계기간·재무·피어 정규화 3단으로 나눈다",
    },
    "CORP-TP": {
        "id": "CORP-TP",
        "name": "기업 Top Pick",
        "target_kind": "corp",
        "needs": ["종목코드 또는 종목명", "분석 기준일"],
        "sufficiency": "high",
        "sufficiency_label": "🟢 높음 (DART 공시)",
        "note": "H04 를 12개월 이벤트·Quick Score 로 나눈다 (M5)",
    },
    "IND-R": {
        "id": "IND-R",
        "name": "산업 리서치",
        "target_kind": "industry",
        "needs": ["업종코드 또는 산업명", "분석 기준일"],
        "sufficiency": "medium",
        "sufficiency_label": "🟡 중간 (KRX 업종 + KOSIS)",
        "note": "밸류체인 자료가 공개 API 에 없어 부족분은 G-SCOPE 로 밝힌다 (M5)",
    },
    "IND-TP": {
        "id": "IND-TP",
        "name": "산업 Top Pick",
        "target_kind": "industry",
        "needs": ["업종코드 또는 산업명", "분석 기준일"],
        "sufficiency": "medium",
        "sufficiency_label": "🟡 중간",
        "note": "H04 를 점수·penalty·민감도로 나눈다 (M5)",
    },
}

# Gap · Conflict · 결함 코드 — 공통계약 §9 를 그대로 옮긴다
CODES = {
    "G-SCOPE": "범위 결측 — 시장 정의·법인·후보군 경계 불명",
    "G-SOURCE": "출처 결측 — 핵심 주장의 원문이 없음",
    "G-DATE": "날짜 결측·노후 — 기준일 불명 또는 오래된 자료",
    "G-DATA": "수치 결측 — 값·단위·기간 일부 없음",
    "C-SOURCE": "출처 충돌 — 공식 통계와 다른 수치",
    "C-METHOD": "방법론 충돌 — 산정 방식 차이",
    "E-ENTITY": "대상 오류 — 다른 법인·티커·산업 혼합",
    "E-UNIT": "단위·통화 오류",
    "E-PERIOD": "기간 오류 — 연간·분기, 실적·전망 혼합",
    "E-CALC": "계산 오류",
    "E-CAUSAL": "인과 비약 — 상관을 원인으로 단정",
    "E-VIZ": "시각화 오류",
    "E-INTERP": "해석 오류 — 사실·전망·가설 혼합",
    "E-FEEDBACK": "의견 추적 오류",
    "R-REGRESSION": "회귀 — 수정 후 이전 통과 항목이 나빠짐",
}

# 진행 상태 · 평가 판정 — 공통계약 §5
STATUSES = ["ready", "in-progress", "partial-continue", "review-needed",
            "revision-needed", "accepted", "complete"]
VERDICTS = ["pass", "conditional-pass", "revise", "human-decision"]
CONFIDENCES = ["high", "medium", "low"]

# 출처 등급 — 공통계약 §8.1
GRADES = ["1차공식", "회사원문", "2차전문", "기타"]


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────
# Run Header — 공통계약 §15
# ─────────────────────────────────────────────────────────────
def new_run_header(workstream_id: str, as_of: str = "", sequence: int = 1,
                   user_objective: str = "", requested_output: str = "fixed") -> Dict:
    """`RUN-{workstream}-{date}-{seq}` 형식의 실행 헤더를 만든다."""
    as_of = as_of or today_kst()
    stamp = as_of.replace("-", "")
    return {
        "run_id": f"RUN-{workstream_id}-{stamp}-{sequence:03d}",
        "workstream_id": workstream_id,
        "parent_artifact": "GIC v15.0 " + WORKSTREAMS.get(workstream_id, {}).get("name", ""),
        "parent_schema_version": SCHEMA_VERSION,
        "harness_version": HARNESS_VERSION,
        "analysis_as_of": as_of,
        "requested_output": requested_output,
        "max_pages": 15,
        "user_objective": user_objective,
        "provided_materials": [],
        "prior_feedback_ids": [],
        "human_reviewer": "",
    }


# ─────────────────────────────────────────────────────────────
# Context Pack — 명세 §5.2
# ─────────────────────────────────────────────────────────────
def new_context_pack(workstream_id: str, target: Dict, as_of: str = "",
                     audience: str = "", questions: Optional[List[str]] = None) -> Dict:
    """빈 Context Pack 을 만든다. 블록 이름과 순서는 명세 §5.2 그대로다."""
    return {
        "schema_version": SCHEMA_VERSION,
        "C0_charter": {
            "task_name": f"{WORKSTREAMS.get(workstream_id, {}).get('name', '')} · {target.get('name', '')}",
            "workstream_id": workstream_id,
            "target": target,
            "as_of": as_of or today_kst(),
            "audience": audience,
            "questions": questions or [],
            "exclusions": [],
            "requested_output": "fixed",
            "max_pages": 15,
        },
        "C1_evidence": [],
        "C2_data": [],
        "C3_hypothesis": [],
        "C4_visual": [],
        "C5_interpretation": [],
        "C6_decisions": {"feedback_log": [], "changes": [], "approvals": []},
        "logs": {"gaps": [], "conflicts": [], "calculations": []},
        # 작업별 확장 필드 — 공통계약 §4 가 H04 의 쓰기 대상으로 인정한 자리다.
        #
        # 서버가 상태를 갖지 않으므로(§1.3) H03 이 만든 재무 시계열·피어를 H04~H09 가 다시 쓰려면
        # **팩에 실려 브라우저를 한 번 다녀와야** 한다. 여기가 그 자리다.
        # C0~C6 은 GIC 계약이 정한 모양이라 손대지 않고, 중간 산출물만 이 블록에 둔다.
        "CX_workstream": {},
    }


def ensure_pack(pack: Optional[Dict], workstream_id: str = "CORP-R") -> Dict:
    """받은 Context Pack 에 빠진 블록이 있으면 채워 넣는다.

    단계 실행은 **필요한 블록만** 보내도 되게 돼 있다 (명세 §1.3 전송량 최소화).
    그래서 없는 블록을 당연하게 여기지 않고 여기서 한 번 메운다.
    """
    base = new_context_pack(workstream_id, {}, "")
    if not isinstance(pack, dict):
        return base
    merged = copy.deepcopy(pack)
    for key, value in base.items():
        if key not in merged or merged[key] is None:
            merged[key] = copy.deepcopy(value)
    for key, value in base["C6_decisions"].items():
        merged["C6_decisions"].setdefault(key, copy.deepcopy(value))
    for key, value in base["logs"].items():
        merged["logs"].setdefault(key, copy.deepcopy(value))
    return merged


# ─────────────────────────────────────────────────────────────
# 단계 결과 봉투 — 공통계약 §7 (필드명 그대로 · 더하지도 빼지도 않는다)
# ─────────────────────────────────────────────────────────────
def new_stage_result(run_id: str, workstream_id: str, stage_id: str,
                     active_role: str, status: str = "in-progress") -> Dict:
    """§7 의 YAML 을 그대로 옮긴 빈 봉투.

    필드 순서까지 문서와 맞춰 뒀다. 사람이 응답 JSON 을 문서와 나란히 놓고 볼 수 있어야 한다.
    """
    return {
        "run_id": run_id,
        "workstream_id": workstream_id,
        "stage_id": stage_id,
        "active_role": active_role,
        "status": status,
        "input_versions": {
            "parent_artifact": "",
            "context_version": SCHEMA_VERSION,
            "prior_feedback_ids": [],
        },
        "context_io": {"read_fields": [], "written_fields": [], "change_ids": []},
        "verified_result": [],
        "provisional_interpretation": [],
        "unavailable_or_unverifiable": [],
        "evidence_ids": [],
        "data_ids": [],
        "calculation_records": [],
        "gap_ids": [],
        "conflict_ids": [],
        "confidence": "medium",
        "evaluation": {"verdict": "pass", "reasons": [], "next_revision_state": ""},
        "visualization_and_interpretation": {
            "chart_or_table": "",
            "observation": "",
            "meaning": "",
            "causal_hypothesis": "",
            "alternative_explanation": "",
            "limitation": "",
            "next_check": "",
        },
        "human_questions": [],
        "feedback_log": [],
        "next_state_input": [],
    }


def downgrade(result: Dict, reason: str) -> None:
    """자료가 모자랄 때 부르는 함수.

    **오류로 중단하지 않는다** (명세 §6.4 · GIC 불변원칙 §2-2).
    확인한 것은 그대로 두고 상태만 `partial-continue` 로 낮춘 뒤 사유를 적는다.
    """
    result["status"] = "partial-continue"
    if reason and reason not in result["unavailable_or_unverifiable"]:
        result["unavailable_or_unverifiable"].append(reason)
    if result["evaluation"]["verdict"] == "pass":
        result["evaluation"]["verdict"] = "conditional-pass"
    if reason and reason not in result["evaluation"]["reasons"]:
        result["evaluation"]["reasons"].append(reason)


def lower_confidence(result: Dict, reason: str = "") -> None:
    """신뢰도를 한 단계 내린다 (Red Team 검사가 실패했을 때 — 명세 §5.5)."""
    order = {"high": "medium", "medium": "low", "low": "low"}
    result["confidence"] = order.get(result.get("confidence", "medium"), "low")
    if reason and reason not in result["evaluation"]["reasons"]:
        result["evaluation"]["reasons"].append(reason)


# ─────────────────────────────────────────────────────────────
# 변경 이력 — 공통계약 §6 "흔적 없이 덮어쓰지 않는다"
# ─────────────────────────────────────────────────────────────
def record_change(pack: Dict, field_path: str, old_value: Any, new_value: Any,
                  reason: str, evidence_or_feedback_id: str = "",
                  changed_by: str = "ORCH") -> str:
    """값을 바꾼 사실을 C6 에 남기고 변경 ID 를 돌려준다.

    **이 함수를 거치지 않고 Context Pack 필드를 고치면 계약 위반이다.**
    나중에 "이 숫자가 왜 바뀌었나" 를 되짚을 수 없게 되기 때문이다.
    """
    changes = pack["C6_decisions"]["changes"]
    change_id = f"CH-{len(changes) + 1:04d}"
    changes.append({
        "change_id": change_id,
        "field_path": field_path,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "evidence_or_feedback_id": evidence_or_feedback_id,
        "changed_by": changed_by,
        "changed_at": now_kst(),
    })
    return change_id


def set_field(pack: Dict, block: str, key: str, value: Any, reason: str,
              changed_by: str = "ORCH", evidence_or_feedback_id: str = "") -> str:
    """C0 처럼 dict 인 블록의 값을 **이력을 남기며** 바꾼다."""
    target = pack.get(block)
    if not isinstance(target, dict):
        raise ValueError(f"{block} 은 dict 블록이 아니다")
    old = target.get(key)
    if old == value:
        return ""                                    # 안 바뀌었으면 이력도 만들지 않는다
    target[key] = value
    return record_change(pack, f"{block}.{key}", old, value, reason,
                         evidence_or_feedback_id, changed_by)


# ─────────────────────────────────────────────────────────────
# Gap · Conflict 로그 — 공통계약 §9 (id · severity · affected … 7필드)
# ─────────────────────────────────────────────────────────────
def add_gap(pack: Dict, code: str, affected: str, treatment: str,
            decision_impact: str, next_input: str = "", severity: str = "medium",
            owner: str = "ORCH") -> str:
    """Gap 을 발행하고 ID 를 돌려준다. 코드는 §9 표에 있는 것만 쓴다."""
    gaps = pack["logs"]["gaps"]
    gap_id = f"{code}-{len(gaps) + 1:03d}"
    gaps.append({
        "id": gap_id,
        "code": code,
        "meaning": CODES.get(code, ""),
        "severity": severity,
        "affected_claim_or_field": affected,
        "current_treatment": treatment,
        "decision_impact": decision_impact,
        "next_input": next_input,
        "owner": owner,
    })
    return gap_id


def add_conflict(pack: Dict, code: str, affected: str, treatment: str,
                 decision_impact: str, sources: Optional[List[str]] = None,
                 severity: str = "medium", owner: str = "EVID") -> str:
    """출처가 엇갈릴 때 **하나를 임의로 고르지 않고** 둘 다 적어 둔다 (불변원칙 §2-4)."""
    conflicts = pack["logs"]["conflicts"]
    conflict_id = f"{code}-{len(conflicts) + 1:03d}"
    conflicts.append({
        "id": conflict_id,
        "code": code,
        "meaning": CODES.get(code, ""),
        "severity": severity,
        "affected_claim_or_field": affected,
        "current_treatment": treatment,
        "decision_impact": decision_impact,
        "sources": sources or [],
        "owner": owner,
    })
    return conflict_id


# ─────────────────────────────────────────────────────────────
# 사용자 질문 · 의견 — 공통계약 §10
# ─────────────────────────────────────────────────────────────
def question(qid: str, text: str, options: List[str], recommended: int = 0,
             why: str = "") -> Dict:
    """질문 카드 하나.

    "괜찮나요?" 대신 **선택지**를 준다 (§10.1). 추천안을 앞에 두되 최종 선택인 척하지 않는다.
    """
    return {
        "id": qid,
        "text": text,
        "options": options,
        "recommended": recommended,
        "why": why,
    }


def apply_feedback(pack: Dict, feedbacks: Optional[List[Dict]]) -> List[str]:
    """받은 답변을 C6 feedback_log 에 적고 반영한 feedback_id 목록을 돌려준다.

    답이 없으면 **`의견 미입력`** 으로 남긴다. 선호를 추정하지 않는다 (불변원칙 §2-7).
    """
    applied = []
    for item in feedbacks or []:
        answer = (item.get("answer") or "").strip()
        entry = {
            "feedback_id": item.get("feedback_id") or f"F-{len(pack['C6_decisions']['feedback_log']) + 1:04d}",
            "stage_id": item.get("stage_id", ""),
            "question": item.get("question", ""),
            "question_id": item.get("question_id", ""),
            "user_opinion": answer or "의견 미입력",
            "applied_field_path": item.get("field_path", ""),
            "before": item.get("before"),
            "after": answer or None,
            "state": "반영됨" if answer else "의견 미입력",
            "not_applied_reason": "" if answer else "사용자가 답하지 않았다 — 추정하지 않는다",
            "recorded_at": now_kst(),
        }
        pack["C6_decisions"]["feedback_log"].append(entry)
        applied.append(entry["feedback_id"])
    return applied


def find_answer(pack: Dict, question_id: str) -> str:
    """이전 단계에서 받은 답을 찾아 준다 (없으면 빈 문자열)."""
    for entry in reversed(pack.get("C6_decisions", {}).get("feedback_log", [])):
        if entry.get("question_id") == question_id and entry.get("state") == "반영됨":
            return entry.get("user_opinion", "")
    return ""
