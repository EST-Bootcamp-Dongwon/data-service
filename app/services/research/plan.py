"""12상태 목록과 진행률 가중치 (명세 §5.1)

진행률은 **12등분이 아니다.** H02 근거 수집과 H04 분석이 실제 시간의 절반을 먹는데
12등분하면 그 두 칸에서 바가 멈춘 것처럼 보인다. 그래서 상태마다 가중치를 두고
누적한 값을 쓴다 (합 100).
"""
from __future__ import annotations

from typing import Dict, List

# (상태, GIC 역할, 하는 일, 사용자 질문 유무, 가중치) — 명세 §5.1 표 그대로
STATES: List[Dict] = [
    {"state_id": "H00", "name": "Initialize", "role": "ORCH",
     "does": "run_id 발급 · 워크스트림 확정 · 버전 기록", "asks": False, "weight": 2},
    {"state_id": "H01", "name": "Scope", "role": "SCOPE",
     "does": "대상 식별 · 기준일 · 피어 후보 · 제외 범위", "asks": True, "weight": 5},
    {"state_id": "H02", "name": "Evidence", "role": "EVID",
     "does": "DART 공시 · KRX · FRED · KOSIS 수집 → E- 발급", "asks": False, "weight": 20},
    {"state_id": "H03", "name": "Normalize", "role": "DATA",
     "does": "전처리 + 회계기간·통화 정규화 → D- 발급", "asks": False, "weight": 18},
    {"state_id": "H04", "name": "Analyze", "role": "ANLY",
     "does": "시계열 + 재무비율 + 워크스트림 판단 → CALC-", "asks": True, "weight": 20},
    {"state_id": "H05", "name": "Visualize", "role": "VIZ",
     "does": "차트 명세 생성 (질문 · 유형 · 축 · data_id)", "asks": False, "weight": 6},
    {"state_id": "H06", "name": "Interpret", "role": "INTP",
     "does": "해석카드 5문장 조립", "asks": False, "weight": 8},
    {"state_id": "H07", "name": "Red Team", "role": "RED",
     "does": "반대 근거 · 대안 가설 · 반전 조건", "asks": False, "weight": 8},
    {"state_id": "H08", "name": "Human Review", "role": "HUMAN",
     "does": "누적 질문 일괄 확인 · 승인", "asks": True, "weight": 3},
    {"state_id": "H09", "name": "Assemble", "role": "ORCH",
     "does": "최대 15장 페이지 구성", "asks": False, "weight": 4},
    {"state_id": "H10", "name": "Evaluate", "role": "EVAL",
     "does": "100점 루브릭 채점 · 회귀검사", "asks": False, "weight": 4},
    {"state_id": "H11", "name": "Complete", "role": "ORCH",
     "does": "Run Summary · 미해결 항목 · handoff", "asks": False, "weight": 2},
]

ORDER = [s["state_id"] for s in STATES]
BY_ID = {s["state_id"]: s for s in STATES}

# 워크스트림별로 더 잘게 쪼개지는 상태 (명세 §5.4)
SUBSTAGES = {
    "CORP-R": {"H03": ["회계기간 정규화", "재무 정규화", "피어 정규화"]},
    "CORP-TP": {"H03": ["회계기간 정규화", "재무 정규화", "피어 정규화"],
                "H04": ["12개월 이벤트 창", "Quick Score 6차원", "Proceed/Watch/Drop"]},
    # 산업 계열은 대상이 종목이 아니라 산업이라 H01~H03 도 하는 일이 다르다.
    # 상태를 더하지는 않고 **같은 상태 안에서** 다른 일을 한다 (stages.`_target_kind`).
    "IND-R": {"H01": ["산업 경계 확정 (자릿수)"],
              "H02": ["KSIC 구성종목", "KOSIS 생산지수", "ECOS 경기지수"],
              "H03": ["구성종목 정규화", "생산지수 CAGR 검산"],
              "H04": ["밸류체인·이익풀", "시장·수급", "사이클·시나리오"]},
    "IND-TP": {"H01": ["산업 경계 확정 (자릿수)", "후보 수"],
               "H02": ["KSIC 구성종목", "시장 스냅샷"],
               "H03": ["후보 지표 정규화"],
               "H04": ["후보군·Red Team", "가중 점수", "missing penalty", "민감도·순위 안정성"]},
}


def weight_done(state_id: str) -> int:
    """그 상태까지 **끝냈을 때**의 누적 진행률."""
    if state_id not in ORDER:
        return 0
    return sum(s["weight"] for s in STATES[: ORDER.index(state_id) + 1])


def next_state(state_id: str) -> str:
    """다음 상태 ID (마지막이면 빈 문자열)."""
    if state_id not in ORDER:
        return ORDER[0]
    index = ORDER.index(state_id)
    return ORDER[index + 1] if index + 1 < len(ORDER) else ""


def plan_for(workstream_id: str) -> Dict:
    """`/api/research/plan/{workstream_id}` 응답."""
    subs = SUBSTAGES.get(workstream_id, {})
    states = []
    for state in STATES:
        row = dict(state)
        row["weight_done"] = weight_done(state["state_id"])
        row["substages"] = subs.get(state["state_id"], [])
        states.append(row)
    return {
        "workstream_id": workstream_id,
        "total_weight": sum(s["weight"] for s in STATES),
        "states": states,
        "asks_at": [s["state_id"] for s in STATES if s["asks"]],
        "note": "진행률 바는 weight 누적값이다 (12등분이 아니다 — 명세 §5.1)",
    }
