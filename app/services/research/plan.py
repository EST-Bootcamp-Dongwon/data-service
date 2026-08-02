"""12상태 목록과 진행률 가중치 (명세 §5.1)

진행률은 **12등분이 아니다.** H02 근거 수집과 H04 분석이 실제 시간의 절반을 먹는데
12등분하면 그 두 칸에서 바가 멈춘 것처럼 보인다. 그래서 상태마다 가중치를 두고
누적한 값을 쓴다 (합 100).

상태별 예상 시간 (`BASELINE`) — M6 신설 · M7 재측정
--------------------------------------------------
진행률 모달이 "여기서 9초쯤 걸립니다" 를 미리 말하려면 기준이 있어야 한다.
아래 `BASELINE` 은 **어림값이 아니라 배포본에서 실제로 잰 값**이고, 어디서 언제
어떤 조건으로 쟀는지를 함께 싣는다. 화면은 이 값을 **첫 실행에만** 쓰고,
그 다음부터는 그 브라우저가 직접 잰 값으로 덮어쓴다 (환경마다 10배 넘게 다르다 —
M7 실측 로컬 전구간 0.3~5.7초 · 배포본 11.1~22.0초).
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
     "does": "시계열 + 재무비율 + 워크스트림 판단 → CALC- · 파생 D-", "asks": True, "weight": 20},
    {"state_id": "H05", "name": "Visualize", "role": "VIZ",
     "does": "차트 명세 생성 (질문 · 유형 · 축 · data_ids)", "asks": False, "weight": 6},
    {"state_id": "H06", "name": "Interpret", "role": "INTP",
     "does": "해석카드 6칸 조립 (관찰·의미·인과·대안·한계·다음확인)", "asks": False, "weight": 8},
    {"state_id": "H07", "name": "Red Team", "role": "RED",
     "does": "반대 근거 · 대안 가설 · 반전 조건", "asks": False, "weight": 8},
    {"state_id": "H08", "name": "Human Review", "role": "HUMAN",
     "does": "누적 질문 일괄 확인 · 승인", "asks": True, "weight": 3},
    {"state_id": "H09", "name": "Assemble", "role": "ORCH",
     "does": "최대 15장 페이지 구성", "asks": False, "weight": 4},
    {"state_id": "H10", "name": "Evaluate", "role": "EVAL",
     "does": "100점 루브릭 채점 (회귀검사는 tests/*.js 가 따로 돈다)", "asks": False, "weight": 4},
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
# 상태별 예상 시간 — 배포본 실측 (M6 신설 · M7 재측정)
# ─────────────────────────────────────────────────────────────
# ⚠️ **지어낸 숫자가 아니다.** 아래 값은 전부 2026-08-02 배포본에서 잰 것이고,
#    무엇을 어떻게 쟀는지는 `BASELINE["how"]` 에 적어 응답에 함께 싣는다.
#    쓰는 쪽이 출처를 볼 수 없으면 그 숫자는 하드코딩과 다를 게 없기 때문이다.
#
# M7 에서 두 가지가 바뀌었다.
#
# ① **워크스트림별로 나눴다.** M6 은 `corp` / `industry` 둘로 묶었는데, 실측해 보니
#    같은 묶음 안에서 H04 가 **5~11배** 다르다 — 아래 기준선으로 CORP-R 0.61초 vs
#    CORP-TP 2.99초(4.9배), IND-TP 0.78초 vs IND-R 8.42초(10.8배).
#    묶어서 평균 내면 **둘 다 틀린 값**이 된다.
#    CORP-TP 는 H04 에서 DART 공시목록을 한 번 더 부르고, IND-R 은 H04 에서
#    밸류체인·사이클을 계산하는데 IND-TP 는 스냅샷만 보고 점수를 매기기 때문이다.
#
# ② **12상태를 넷 다 하나씩 따로 쟀다.** M6 은 기업 계열이 H00·H02 만 실측이고
#    나머지 10상태는 전구간에서 나눈 근사였다. 이제 전부 직접 잰 값이라 `EXACT` 가
#    비어 있지 않다 — 화면이 '≈' 를 붙일 자리가 없다.
BASELINE_SECONDS: Dict[str, Dict[str, float]] = {
    "CORP-R": {"H00": 0.21, "H01": 0.20, "H02": 8.54, "H03": 0.40, "H04": 0.61,
               "H05": 0.93, "H06": 0.78, "H07": 0.95, "H08": 0.70, "H09": 0.67,
               "H10": 0.97, "H11": 0.74},
    "CORP-TP": {"H00": 0.21, "H01": 0.21, "H02": 9.50, "H03": 0.32, "H04": 2.99,
                "H05": 0.80, "H06": 0.87, "H07": 0.86, "H08": 0.88, "H09": 0.98,
                "H10": 1.07, "H11": 0.71},
    "IND-R": {"H00": 0.48, "H01": 0.23, "H02": 2.67, "H03": 0.58, "H04": 8.42,
              "H05": 0.78, "H06": 1.00, "H07": 0.96, "H08": 0.97, "H09": 1.02,
              "H10": 0.96, "H11": 1.14},
    "IND-TP": {"H00": 0.23, "H01": 0.22, "H02": 1.71, "H03": 0.49, "H04": 0.78,
               "H05": 1.05, "H06": 0.72, "H07": 0.98, "H08": 0.95, "H09": 1.03,
               "H10": 1.07, "H11": 1.08},
}

# 같은 대상을 **다시** 볼 때 (`/tmp` 캐시가 차 있을 때) 실제로 걸린 시간.
# 기준선을 이쪽으로 잡지 않는 이유는 아래 `why_first_run` 에 적었다.
CACHED_SECONDS: Dict[str, Dict[str, float]] = {
    "CORP-R": {"H02": 0.21},
    "CORP-TP": {"H02": 0.20},
    "IND-R": {"H02": 1.93, "H04": 2.28},
    "IND-TP": {"H02": 1.62, "H03": 0.22},
}

# 상태별로 **직접 잰** 것. M7 부터는 넷 다 12상태 전부다.
EXACT: Dict[str, List[str]] = {
    workstream: list(states.keys()) for workstream, states in BASELINE_SECONDS.items()
}

BASELINE = {
    "measured_at": "2026-08-02",
    "environment": "Vercel 배포본 · 함수 warm · **처음 보는 대상** · `/tmp` 캐시 비어 있음",
    "how": {
        "CORP-R": ("000660 SK하이닉스 전구간 16.5초를 상태별로 하나씩 쟀다. "
                   "H02 8.54초는 DART 응답 대기다"),
        "CORP-TP": ("035720 카카오(22.0초) · 005930 삼성전자(18.2초) 두 번의 평균이다. "
                    "H04 가 2.99초인 것은 여기서 DART 공시목록을 한 번 더 부르기 때문이다 "
                    "(실측 1.38 · 4.60초 — DART 응답 편차가 크다)"),
        "IND-R": ("261 반도체 제조업 전구간 20.2초를 상태별로 하나씩 쟀다. "
                  "H04 8.42초는 `/tmp` 캐시가 비어 있을 때다 (차 있으면 2.28초)"),
        "IND-TP": ("261 반도체 제조업. **처음 보는 대상으로 두 번**(전구간 11.1 · 11.0초) "
                   "재서 상태별 값은 그 둘의 평균이다. 세 번째 실행 10.2초는 캐시가 차 "
                   "있던 것이라 **기준선 표본에 넣지 않았다** — 넣으면 '처음 보는 대상' 을 "
                   "기준으로 삼는다는 원칙이 그 자리에서 무너진다 (그 값은 "
                   "`cached_seconds` 쪽에 있다). H02 가 IND-R 보다 1초 빠른 것은 "
                   "KOSIS 생산지수·ECOS 경기지수를 부르지 않기 때문이다"),
    },
    # 워밍업을 안 하면 첫 요청이 이만큼 **더** 걸린다.
    # 실측 H00 콜드 5.78초 · 웜 0.21초 → 증분 5.57초. 5.8 은 콜드 요청의 전체 시간이지
    # 증분이 아니다 — 둘을 섞으면 워밍업이 실제보다 0.2초 더 일하는 것처럼 보인다.
    "cold_start_seconds": 5.6,
    "cold_start_note": ("함수가 잠들어 있으면 첫 요청이 5.6초쯤 더 걸린다 "
                        "(콜드 5.78초 − 웜 0.21초). 화면 진입 시 워밍업 요청"
                        "(GET /api/research/warmup) 하나로 없앤다"),
    "why_h02_slow": ("H02 의 대기는 우리 코드가 아니라 DART 응답이다 — 배포본에서 회당 6~7초다. "
                     "동시 호출로 3회를 1회 대기로 줄였고(−53%) 그 이상은 줄일 수 없어 "
                     "모달이 미리 말한다"),
    # M7 결정 — 기준선을 '처음 보는 대상' 쪽으로 잡는다.
    "why_first_run": ("같은 대상을 다시 보면 훨씬 빠르다 (CORP H02 8.5초 → 0.2초 · "
                      "IND-R H04 8.4초 → 2.3초). 그래도 **기준선은 처음 보는 대상 쪽**이다. "
                      "빠른 쪽을 기준으로 잡으면 처음 오는 사람이 정확히 M6 가 풀려던 문제"
                      "(\"왜 멈춰 있지?\")를 겪는다. 넘게 예상해 일찍 끝나는 편이 낫다"),
    "miss_note": ("예상값은 상한이 아니다. 화면은 기다리는 동안 실제로 흐른 시간을 세고, "
                  "예상을 넘어서면 넘겼다는 사실도 함께 말한다"),
    "note": "환경마다 10배 넘게 다르다. 화면은 첫 실행에만 이 값을 쓰고 그 뒤엔 직접 잰 값을 쓴다",
}


def expected_seconds(workstream_id: str, state_id: str) -> float:
    """그 상태가 배포본에서 대략 몇 초 걸리는가 (실측 기준선).

    워크스트림별로 찾고, 모르는 워크스트림이면 같은 `target_kind` 의 다른 것으로 대신한다.
    """
    states = BASELINE_SECONDS.get(workstream_id)
    if states is None:
        kind = contracts.WORKSTREAMS.get(workstream_id, {}).get("target_kind", "corp")
        fallback = "IND-R" if kind == "industry" else "CORP-R"
        states = BASELINE_SECONDS[fallback]
    return states.get(state_id, 0.8)


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
    exact = set(EXACT.get(workstream_id, []))
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
        "baseline": {**BASELINE,
                     "how": BASELINE["how"].get(workstream_id, ""),
                     # 같은 대상을 다시 볼 때의 값도 함께 준다 — 모달이 "다시 보면 빠르다" 를
                     # 말할 수 있어야 예상을 넘긴 것과 원래 느린 것을 구분해 준다.
                     "cached_seconds": CACHED_SECONDS.get(workstream_id, {})},
        "note": "진행률 바는 weight 누적값이다 (12등분이 아니다 — 명세 §5.1)",
    }
