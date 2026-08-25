"""자료 수집 라우터 (컨트롤러 계층) — ADR-DS-0020

    GET  /api/collect/dart/status      수집할 수 있나 · 못 하면 왜인가 · 오늘 남은 예산
    POST /api/collect/dart/security    종목 하나의 공시를 받아 보관함에 담는다 (동기)

수집 정본은 `app/services/dart_collector.py` 이고, 여기서는 응답 형식(DTO)과
**거절의 뜻**만 담당한다.

## ② 일괄 경로를 만들지 않는 이유

일괄 수집(유니버스 350종목 ≈ 3분 반)의 실행처는 **셸 하나**다 —
`python3 scripts/collect_dart.py --top 350`. HTTP 로 열면 진행률·중단·폴링 DTO 가
따라오고, 그것은 `refresh_job` 의 실행기를 두 번째로 베끼는 일이 된다. 화면에 붙일
진행 패널이 **아직 하나도 없으므로** 지금 빼면 쓰는 곳이 하나뿐인 추상이 된다
(ADR-DS-0019 가 담기 버튼에서 세운 규칙과 같다). 두 번째 수집기(네이버)가 생기는 날 뺀다.

그리고 웹 프로세스에서 긴 배치를 돌리면 안 되는 이유가 셋 더 있다 —
`dart_data._last_attempt` 는 잠금 없는 전역이라 대시보드의 "마지막 DART 결과" 를 오염시키고,
동기 다리의 루프가 하나라 배치가 화면 조회를 밀어내며, 응답 캐시에 퇴출이 없다.

## 거절의 뜻 — 처방이 다르므로 뭉치지 않는다

| 코드 | 뜻 | 화면이 할 일 |
|---|---|---|
| `503` | 못 한다 — 껐거나 · **키가 없거나** · **보관함에 못 붙는다**(배포본의 기본 상태) | 버튼을 잠그고 이유를 보여 준다 |
| `429` | 오늘 예산을 다 썼다 — **"지금은 안 되지만 내일은 된다"** | 언제 풀리는지 보여 준다 |
| `422` | 인자가 어휘 밖이다 | 입력을 고치게 한다 |
| `404` | 그 종목의 DART 고유번호가 없다 | 우선주이거나 매핑이 낡았다고 알린다 |

⚠️ **`429` 는 이 레포의 넷째 거절이다.** 기존 셋(`503` 못 한다 · `409` 하나가 돌고 있다 ·
`422` 어휘 밖) 중 어느 것도 "능력도 있고 도는 것도 없고 인자도 맞는데 오늘은 못 한다" 를
말하지 못한다. `dart_data._call` 이 DART 의 `020` 을 이미 429 로 옮기고 있어 축도 같다.

## 등록은 조건부가 아니다

배포본에서도 등록하고 실행만 거절한다 — ADR-DS-0017·0019 와 같은 원칙이다.
⚠️ 단 **`/status` 만은 `503` 을 내지 않는다.** 화면이 버튼을 잠글지 정하려면 이유를
200 으로 받아 볼 수 있어야 한다. 거기까지 503 이면 화면은 **이유 없이** 잠긴다.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.clients import dart_data
from app.repositories import clip_store
from app.services import dart_collector as collector

router = APIRouter(prefix="/api/collect/dart", tags=["자료 수집"])

# DART 본문 코드 → HTTP. **한글 메시지를 파싱하지 않기 위해 존재한다** — 문구를 고치는 날
# 조용히 깨지는 것을 막는다. 여기 없는 코드는 전부 `502`(바깥쪽 사정)다.
_HTTP_FOR_DART = {
    "020": status.HTTP_429_TOO_MANY_REQUESTS,   # 한도 초과 — 내일은 된다
    "800": status.HTTP_503_SERVICE_UNAVAILABLE,  # 점검 중 — 지금은 못 한다
}


# ==================================================
# 1. DTO
# ==================================================
class SecurityRequest(BaseModel):
    """종목 하나를 받아 담는다."""

    code: str = Field(..., pattern=r"^[0-9A-Z]{6}$",
                      description="종목코드 6자리. 신형 코드(0001A0)도 받는다")
    months: int = Field(12, ge=1, le=60, description="최근 몇 개월을 볼 것인가")
    incremental: bool = Field(
        False,
        description=("지난 회차 이후만 볼 것인가. **기본이 false 인 것이 뜻을 가진다** — "
                     "사람이 버튼을 누른 것은 '지금 확인하고 싶다' 이고, 재방문 억제로 "
                     "건너뛰면 화면에는 아무 일도 안 일어난 것처럼 보인다"))


class CollectResult(BaseModel):
    """한 회차의 결말. 중복은 오류가 아니므로 **오류 칸에 넣지 않는다.**"""

    scope: str
    scope_note: str = ""
    codes_total: int = 0
    codes_done: int = 0
    fetched: int = 0
    created: int = 0
    duplicate: int = 0
    invalid: int = 0
    skipped: int = 0
    failed: int = 0
    empty: int = 0
    calls: int = 0
    stopped: str = ""
    reason: str = ""
    truncated_codes: List[str] = Field(default_factory=list)
    problems: List[str] = Field(default_factory=list)
    failures: List[Dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: int = 0


class CollectStatus(BaseModel):
    """수집할 수 있나 · 예산 · 매핑 나이 · 지금까지 담긴 것."""

    available: bool
    reason: str = ""
    hints: List[str] = Field(default_factory=list)
    key: Dict[str, Any] = Field(default_factory=dict)
    clip: Dict[str, Any] = Field(default_factory=dict)
    budget: Dict[str, Any] = Field(default_factory=dict)
    corp_code: Dict[str, Any] = Field(default_factory=dict)
    universe: Dict[str, Any] = Field(default_factory=dict)
    filings: Dict[str, Any] = Field(default_factory=dict)
    last_run: Optional[Dict[str, Any]] = None
    batch_command: str = ""


# ==================================================
# 2. 상태 — 이 경로는 절대 503 을 내지 않는다
# ==================================================
@router.get("/status", response_model=CollectStatus, summary="수집할 수 있나")
def collect_status() -> CollectStatus:
    """⚠️ **못 쓸 때도 200 이다.** 화면이 버튼을 잠글지 정하려면 "왜" 를 받아야 한다.

    ⚠️ **예산·보관함 조회가 실패해도 200 이다.** 진단 경로가 진단 대신 트레이스백으로
    죽으면 존재 이유가 없다 — `db.ping()` 이 같은 이유로 같은 일을 한다.
    """
    # ⚠️ **`capability()` 도 try 안이다.** `COLLECT_API` 가 어휘 밖 값이면 `settings` 가
    #    `ValueError` 를 던지는데, 그것이 밖으로 나가면 "절대 실패하지 않는다" 고 못 박은
    #    이 경로가 **정확히 500** 을 낸다(실측). 진단이 진단 대신 트레이스백으로 죽으면
    #    존재 이유가 없다 — `db.ping()` 이 같은 이유로 설정 읽기까지 try 안에 둔다.
    try:
        state = collector.capability()
    except Exception as error:  # noqa: BLE001
        state = {"available": False, "reason": f"설정을 읽지 못했다 — {error}",
                 "hints": [str(error)], "key": {}, "clip": {}}
    _, note = _universe_note()

    budget: Dict[str, Any] = {}
    filings: Dict[str, Any] = {}
    last_run: Optional[Dict[str, Any]] = None
    try:
        budget = collector.calls_left(reserve=collector.ONDEMAND_RESERVE)
        filings = clip_store.count_filings()
        from app.repositories import watermark_store

        last_run = watermark_store.get("dart_filing")
    except Exception as error:  # noqa: BLE001  — 표에 못 붙으면 여기는 비어 있는 것이 맞다
        budget = budget or {"error": str(error).splitlines()[0]}

    return CollectStatus(
        available=state["available"], reason=state["reason"], hints=state["hints"],
        key=state.get("key", {}), clip=state.get("clip", {}),
        budget=budget, corp_code=collector.corp_code_state(),
        universe={"note": note, "default_top": collector.TOP_N_DEFAULT},
        filings=filings, last_run=last_run,
        batch_command=f"python3 scripts/collect_dart.py --top {collector.TOP_N_DEFAULT}",
    )


def _universe_note():
    """대상 목록의 정체를 한 문장으로. **파일이 없어도 상태 조회는 살아야 한다.**"""
    try:
        return collector.universe(collector.TOP_N_DEFAULT)
    except Exception as error:  # noqa: BLE001
        return [], f"대상 목록을 읽지 못했다: {error}"


# ==================================================
# 3. 실행 — 종목 하나
# ==================================================
@router.post("/security", response_model=CollectResult, summary="종목 하나의 공시를 담는다")
def collect_security(body: SecurityRequest, response: Response) -> CollectResult:
    """그 종목의 최근 공시를 받아 보관함에 담는다. **3호출 · 1초 안팎이라 동기다.**

    ⚠️ **같은 공시를 다시 담아도 오류가 아니다.** `created` 와 `duplicate` 로 나누어
    말한다 — 사람이 버튼을 두 번 누르는 것은 "이미 담았나" 를 확인하는 행동이다.
    """
    state = collector.capability()
    if not state["available"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason": state["reason"], "hints": state["hints"]},
        )

    budget = collector.calls_left()
    if budget["left"] <= 0:
        # ⚠️ 넷째 거절 — 능력은 있고 도는 것도 없고 인자도 맞다. 오늘이 안 될 뿐이다.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"reason": "오늘 이 수집기의 호출 예산을 다 썼다",
                    "hints": [f"쓴 호출 {budget['used_by_collector']} / "
                              f"예산 {budget['batch_budget']} (DART 일 한도 "
                              f"{budget['daily_limit']}).",
                              "KST 자정에 다시 열린다."]},
            headers={"Retry-After": str(budget["retry_after"])},
        )

    plan = collector.Plan(scope="one", codes=(body.code,), months=body.months,
                          incremental=body.incremental)
    try:
        result = collector.collect(plan)
    except dart_data.DartError as error:
        raise HTTPException(error.status, str(error)) from error

    # 한 종목짜리라 결말이 곧 그 종목의 결말이다. 404·502 를 삼키지 않고 그대로 올린다.
    for failure in result.get("failures", []):
        if failure["state"] == "skipped" and "고유번호" in failure["reason"]:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{body.code} 의 DART 고유번호를 찾지 못했다. 우선주이거나 "
                "고유번호 매핑이 낡았다 — python3 scripts/build_corp_code.py")

    # ⚠️ **응답 직전에 응답 캐시를 씻는다.** 같은 프로세스의 리서치 화면이 방금 담은 공시를
    #    24시간짜리 낡은 사본으로 보여 주는 것을 막는다 (`CACHE_TTL` 열쇠에 날짜가 없다).
    dart_data.clear_cache()

    # ⚠️ **계통 중단을 200 으로 내보내지 않는다.** `collect()` 는 DART 의 `020`(한도 초과)·
    #    `010`(키 거부)·`800`(점검)을 삼켜 `stopped` 에 담는데, 그대로 200 을 내면 화면은
    #    "새로 0건" 을 성공으로 그린다. 이 라우터가 문서로 못 박은 거절 표를 스스로 우회하는
    #    셈이다. **어느 코드로 옮길지는 `dart_status` 가 정한다** — 한글 메시지를 파싱하지 않는다.
    stopped = result.get("stopped", "")
    if stopped == "budget":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"reason": "오늘 이 수집기의 호출 예산을 다 썼다",
                    "hints": [f"쓴 호출 {budget['used_by_collector']} / "
                              f"예산 {budget['batch_budget']}.", "KST 자정에 다시 열린다."]},
            headers={"Retry-After": str(budget["retry_after"])})
    if stopped:
        fatal = next((f for f in result.get("failures", []) if f["state"] == "aborted"), None)
        code = (fatal or {}).get("dart_status", "")
        raise HTTPException(
            _HTTP_FOR_DART.get(code, status.HTTP_502_BAD_GATEWAY),
            detail={"reason": f"DART 쪽 사정으로 멈췄다 — {(fatal or {}).get('reason', stopped)}",
                    "hints": ["잠시 뒤에 다시 시도한다.",
                              "계속되면 python3 scripts/collect_dart.py --check 로 상태를 본다."]},
            headers={"Retry-After": str(budget["retry_after"])} if code == "020" else None)
    return CollectResult(**{k: v for k, v in result.items()
                            if k in CollectResult.model_fields})
