"""12상태 목록과 진행률 가중치 (명세 §5.1)

진행률은 **12등분이 아니다.** H02 근거 수집과 H04 분석이 실제 시간의 절반을 먹는데
12등분하면 그 두 칸에서 바가 멈춘 것처럼 보인다. 그래서 상태마다 가중치를 두고
누적한 값을 쓴다 (합 100).

M6 — 상태별 예상 시간 (`BASELINE`)
---------------------------------
진행률 모달이 "여기서 9초쯤 걸립니다" 를 미리 말하려면 기준이 있어야 한다.
아래 `BASELINE` 은 **어림값이 아니라 배포본에서 실제로 잰 값**이고, 어디서 언제
어떤 조건으로 쟀는지를 함께 싣는다. 화면은 이 값을 **첫 실행에만** 쓰고,
그 다음부터는 그 브라우저가 직접 잰 값으로 덮어쓴다 (환경마다 10배 넘게 다르다 —
로컬 전구간 0.8~2.6초 · 배포본 9.2~16.0초).
"""
from __future__ import annotations

from typing import Dict, List

from . import contracts

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


# ─────────────────────────────────────────────────────────────
# 상태별 예상 시간 — 배포본 실측 (M6)
# ─────────────────────────────────────────────────────────────
# ⚠️ **지어낸 숫자가 아니다.** 아래 값은 전부 2026-08-02 배포본에서 잰 것이고,
#    무엇을 어떻게 쟀는지는 `BASELINE["how"]` 에 적어 응답에 함께 싣는다.
#    쓰는 쪽이 출처를 볼 수 없으면 그 숫자는 하드코딩과 다를 게 없기 때문이다.
#
# 산업 계열은 12상태를 **하나씩 따로** 쟀다 (IND-R · 261 · 전구간 11.2초).
# 기업 계열은 H00 과 H02 만 따로 쟀고, 나머지 10상태는 "웜 전구간 6.9초에서 그 둘을
# 뺀 값"을 10으로 나눈 근사다. 그래서 어느 쪽이 직접 잰 값인지 `EXACT` 로 구분한다.
BASELINE_SECONDS: Dict[str, Dict[str, float]] = {
    "industry": {"H00": 0.21, "H01": 0.22, "H02": 1.65, "H03": 0.59, "H04": 2.38,
                 "H05": 0.80, "H06": 0.78, "H07": 0.78, "H08": 0.95, "H09": 0.94,
                 "H10": 0.95, "H11": 0.93},
    "corp": {"H00": 0.21, "H01": 0.63, "H02": 9.10, "H03": 0.63, "H04": 0.63,
             "H05": 0.63, "H06": 0.63, "H07": 0.63, "H08": 0.63, "H09": 0.63,
             "H10": 0.63, "H11": 0.63},
}

# 상태별로 **직접 잰** 것. 여기 없는 것은 나눠 만든 근사라 화면이 '≈' 를 붙인다.
EXACT: Dict[str, List[str]] = {
    "industry": list(BASELINE_SECONDS["industry"].keys()),
    "corp": ["H00", "H02"],
}

BASELINE = {
    "measured_at": "2026-08-02",
    "environment": "Vercel 배포본 · 함수 warm · 처음 보는 대상",
    "how": {
        "industry": ("IND-R · 261 반도체 제조업 전구간 11.2초를 상태별로 나눠 쟀다"),
        "corp": ("H02 는 병렬화 후 SK하이닉스 9.25 · 카카오 9.72 · 클래시스 8.32초의 평균이다 "
                 "(병렬화 전 평균 19.3초 → 9.1초). H00 은 워밍업된 함수의 값이고, "
                 "나머지 10상태는 웜 전구간 6.9초에서 H00·H02 를 뺀 값을 10으로 나눈 근사다"),
    },
    # 워밍업을 안 하면 첫 요청이 이만큼 더 걸린다 (실측 H00 콜드 5.78초 · 웜 0.21초)
    "cold_start_seconds": 5.8,
    "cold_start_note": ("함수가 잠들어 있으면 첫 요청이 5.8초 더 걸린다. "
                        "화면 진입 시 워밍업 요청(GET /api/research/warmup) 하나로 없앤다"),
    "why_h02_slow": ("H02 의 대기는 우리 코드가 아니라 DART 응답이다 — 배포본에서 회당 6~7초다. "
                     "동시 호출로 3회를 1회 대기로 줄였고(−53%) 그 이상은 줄일 수 없어 "
                     "모달이 미리 말한다"),
    # 예상은 예상일 뿐이다. `/tmp` 캐시가 비어 있으면 같은 상태가 몇 배로 뛴다 —
    # 실측 IND-R H04 웜 2.4초 · 처음 보는 산업 9.3초. 화면은 기다리는 동안 실제로 흐른
    # 시간을 계속 세고, 예상을 넘어서면 그 사실을 말한다.
    "miss_note": ("`/tmp` 캐시가 비어 있으면 더 걸린다 — 실측 IND-R H04 웜 2.4초 · "
                  "처음 보는 산업 9.3초. 예상값은 상한이 아니다"),
    "note": "환경마다 10배 넘게 다르다. 화면은 첫 실행에만 이 값을 쓰고 그 뒤엔 직접 잰 값을 쓴다",
}


def expected_seconds(workstream_id: str, state_id: str) -> float:
    """그 상태가 배포본에서 대략 몇 초 걸리는가 (실측 기준선)."""
    kind = contracts.WORKSTREAMS.get(workstream_id, {}).get("target_kind", "corp")
    return BASELINE_SECONDS.get(kind, BASELINE_SECONDS["corp"]).get(state_id, 0.63)


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
    kind = contracts.WORKSTREAMS.get(workstream_id, {}).get("target_kind", "corp")
    exact = set(EXACT.get(kind, []))
    states = []
    for state in STATES:
        row = dict(state)
        state_id = state["state_id"]
        row["weight_done"] = weight_done(state_id)
        row["substages"] = subs.get(state_id, [])
        # M6 진행률 모달의 '상태별 예상 시간'. 직접 잰 것과 나눠 만든 근사를 구분해 준다 —
        # 화면이 근사에 '≈' 를 붙일 수 있어야 숫자를 실제보다 믿지 않는다.
        row["expected_seconds"] = expected_seconds(workstream_id, state_id)
        row["expected_exact"] = state_id in exact
        states.append(row)
    return {
        "workstream_id": workstream_id,
        "target_kind": kind,
        "total_weight": sum(s["weight"] for s in STATES),
        "states": states,
        "asks_at": [s["state_id"] for s in STATES if s["asks"]],
        "expected_total_seconds": round(sum(s["expected_seconds"] for s in states), 1),
        "baseline": {**BASELINE, "how": BASELINE["how"].get(kind, "")},
        "note": "진행률 바는 weight 누적값이다 (12등분이 아니다 — 명세 §5.1)",
    }
