"""자료 갱신 라우터 (컨트롤러 계층) — ADR-DS-0017

    GET  /api/refresh/status   지금 무엇이 얼마나 낡았나 · 실행할 수 있나 · 도는 작업이 있나
    POST /api/refresh/run      갱신 사슬을 시작한다 (곧바로 돌아온다)
    POST /api/refresh/cancel   도는 작업을 중단한다

사슬 자체는 `app/services/refresh_job.py` 가 정의하고 돌린다. 여기서는 응답 형식(DTO)과
**거절의 뜻**만 담당한다.

## 왜 세 종류로 거절하나

| 코드 | 뜻 | 화면이 해야 할 일 |
|---|---|---|
| `503` | 이 프로세스가 **못 한다** (배포본 · `scripts/` 부재 · 쓰기 불가 · `REFRESH_API=off`) | 버튼을 잠그고 이유를 보여 준다 |
| `409` | 할 수 있는데 **지금은 하나가 돌고 있다** | 진행 상황을 보여 준다 |
| `422` | 인자가 어휘 밖이다 (`days` 범위) | 입력을 고치게 한다 |

셋을 하나로 뭉치면 화면이 무엇을 해야 할지 모른다 — 잠글 것인지, 기다릴 것인지,
고쳐서 다시 보낼 것인지가 전부 다르다.

## 등록은 조건부가 아니다

배포본에서도 이 세 경로는 **등록된다.** 환경에 따라 OpenAPI 모양이 달라지면 계약
스냅샷(`tests/test_contract.py`)이 어디서 돌리느냐에 따라 갈리고, 무엇보다
**배포본에서 왜 안 되는지 설명할 자리가 사라진다.** 실행만 `503` 으로 막고
상태 조회는 그대로 열어 둔다 (ADR-DS-0016 §4 — 따를 수 없는 처방을 띄우지 않는다).
"""

from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, Field

from app.core import settings
from app.services import refresh_job as service

router = APIRouter(prefix="/api/refresh", tags=["자료 갱신"])


# ==================================================
# DTO
# ==================================================
class ChainStep(BaseModel):
    """사슬 한 칸의 **설명**. 작업이 없어도 화면이 표를 그릴 수 있게 정적으로 내려준다."""

    key: str = Field(..., description="단계 식별자", examples=["fetch"])
    label: str = Field(..., description="단계 이름", examples=["KRX 시세 수집"])
    script: str = Field(..., description="실행하는 스크립트", examples=["scripts/fetch_krx.py"])
    outputs: List[str] = Field([], description="산출물")
    in_git: bool = Field(..., description="산출물이 git 에 올라가나 = 배포본에 닿나")
    needs_db: bool = Field(False, description="Postgres 에 붙어야 도는 단계인가")
    note: str = Field("", description="한 줄 설명")


class JobStep(BaseModel):
    """작업 안에서 그 단계가 지금 어떤 상태인가."""

    key: str = Field(..., description="단계 식별자")
    label: str = Field(..., description="단계 이름")
    state: str = Field(..., description=(
        "`wait`(대기) · `now`(진행중) · `done`(완료) · `skipped`(건너뜀) · "
        "`failed`(실패) · `cancelled`(중단)"), examples=["done"])
    seconds: float = Field(0.0, description="걸린 시간(초)")
    exit_code: Optional[int] = Field(None, description="자식 프로세스 종료코드")
    note: str = Field("", description="설명 — 건너뛴 이유·실패 원인이 여기 온다")
    in_git: bool = Field(..., description="산출물이 git 에 올라가나")
    outputs: List[str] = Field([], description="산출물")
    command: str = Field("", description="실제로 돌린 명령", examples=["scripts/fetch_krx.py --days 30"])


class Job(BaseModel):
    """갱신 한 번의 상태. **프로세스 메모리에만 있다** (워커가 여럿이면 어긋난다)."""

    id: str = Field(..., description="작업 식별자 (시작 시각)", examples=["20260825-153012"])
    mode: str = Field(..., description="`run`(갱신) · `check`(재기만)", examples=["run"])
    days: int = Field(..., description="수집할 거래일 수", examples=[30])
    skip_pg: bool = Field(..., description="Postgres 재적재를 건너뛰나")
    state: str = Field(..., description="`running` · `done` · `failed` · `cancelled`")
    running: bool = Field(..., description="지금 돌고 있나 (화면이 폴링을 계속할지 판단한다)")
    message: str = Field("", description="사람이 읽을 판정 한 줄")
    started_at: str = Field(..., description="시작 시각(KST)")
    ended_at: str = Field("", description="끝난 시각(KST). 도는 중이면 빈 문자열")
    seconds: float = Field(0.0, description="지금까지 걸린 시간(초)")
    done_steps: int = Field(0, description="끝난 단계 수 (건너뛴 것 포함)")
    total_steps: int = Field(..., description="전체 단계 수")
    steps: List[JobStep] = Field([], description="단계별 상태")
    log: List[str] = Field([], description=(
        f"실행 로그 꼬리 (최대 {service.LOG_LINES}줄). 앞부분은 버린다"))


class DataPoint(BaseModel):
    """사람이 실제로 보는 숫자 한 줄. `invoke refresh` 가 끝에 찍는 것과 같은 값이다."""

    ok: bool = Field(..., description="확인 성공 여부")
    text: str = Field(..., description="한 줄 요약")


class DataSummary(BaseModel):
    krx: DataPoint = Field(..., description="KRX 시세 — 층·최신 거래일·행 수")
    snapshot: DataPoint = Field(..., description="시장 스냅샷 — 기준일·종목 수·낡음")


class StatusResponse(BaseModel):
    available: bool = Field(..., description="이 프로세스에서 갱신을 **실행**할 수 있나")
    reason: str = Field("", description="못 하는 이유 (`available=false` 일 때만)")
    hints: List[str] = Field([], description="그럼 무엇을 해야 하나 — 따를 수 있는 처방만 담는다")
    app_env: str = Field(..., description="실행 환경 — `local` · `vercel`", examples=["local"])
    store_backend: str = Field(..., description="시세 읽기 저장소 — `sqlite` · `postgres`")
    postgres_reachable: bool = Field(..., description="Postgres 에 붙을 수 있나 (TCP 1초)")
    default_days: int = Field(..., description="수집 구간 기본값")
    min_days: int = Field(..., description="수집 구간 하한")
    max_days: int = Field(..., description="수집 구간 상한")
    chain: List[ChainStep] = Field(..., description="사슬 다섯 칸 — 순서가 뜻을 가진다")
    job: Optional[Job] = Field(None, description="지금 도는(또는 마지막) 작업. 없으면 null")
    data: DataSummary = Field(..., description="지금 무엇이 얼마나 낡았나")


class RunRequest(BaseModel):
    """갱신 요청. 전부 기본값이 있어 빈 본문으로도 보낼 수 있다."""

    days: int = Field(
        service.DEFAULT_DAYS, ge=service.MIN_DAYS, le=service.MAX_DAYS,
        description=f"수집할 거래일 수 ({service.MIN_DAYS}~{service.MAX_DAYS})", examples=[30])
    check: bool = Field(
        False, description="아무것도 바꾸지 않고 낡음만 잰다 (외부 호출·파일 변경 없음)")
    skip_pg: bool = Field(False, description="Postgres 재적재를 건너뛴다")


# ==================================================
# 엔드포인트
# ==================================================
def _status_payload() -> dict:
    ready = service.capability()
    return {
        **ready,
        "app_env": settings.app_env(),
        "store_backend": settings.store_backend(),
        # 폴링마다 1초를 새로 쓰지 않게 30초 기억을 쓴다. 사슬 자신은 기억을 안 쓴다.
        "postgres_reachable": service.postgres_reachable(
            max_age=service.REACHABLE_MEMO_SECONDS),
        "default_days": service.DEFAULT_DAYS,
        "min_days": service.MIN_DAYS,
        "max_days": service.MAX_DAYS,
        "chain": [
            {
                "key": step.key,
                "label": step.label,
                "script": step.script,
                "outputs": list(step.outputs),
                "in_git": step.in_git,
                "needs_db": step.needs_db,
                "note": step.note,
            }
            for step in service.STEPS
        ],
        "job": service.state(),
        "data": service.data_summary(),
    }


@router.get("/status", response_model=StatusResponse, summary="갱신 상태 — 낡음 · 실행 가능 여부")
def refresh_status():
    """대시보드 '자료 갱신' 패널이 2초마다 부른다.

    **실행할 수 없는 환경에서도 200 으로 답한다.** 못 하는 것과 고장난 것은 다르고,
    화면은 그 이유를 사람에게 보여 줘야 한다 (ADR-DS-0016 §4).
    """
    return _status_payload()


@router.post("/run", response_model=StatusResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="갱신 시작 — 수집 → 축약본 → 스냅샷 → 마스터 → Postgres")
def refresh_run(payload: Optional[RunRequest] = Body(default=None)):
    """사슬을 시작하고 **곧바로** 첫 상태를 돌려준다 (202).

    사슬은 30거래일 기준 4분, 250거래일이면 20분을 넘길 수 있어 요청 안에서 기다리지 않는다.
    진행 상황은 `GET /api/refresh/status` 로 본다.

    ⚠️ **커밋하지 않는다.** 산출물 셋이 git 에 올라가고 push 가 곧 Vercel 배포다 —
    그 시점은 사람이 정한다 (ADR-DS-0016 결정 1). 끝나면 커밋 절차를 로그에 적어 준다.
    """
    request = payload or RunRequest()
    try:
        service.start(days=request.days, check=request.check, skip_pg=request.skip_pg)
    except service.RefreshUnavailable as error:
        # 503 — 이 프로세스가 못 한다. 화면은 버튼을 잠그고 이유를 보여 준다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=" ".join([error.reason, *error.hints]),
        ) from error
    except service.RefreshBusy as error:
        # 409 — 할 수는 있는데 지금은 하나가 돌고 있다.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _status_payload()


@router.post("/cancel", response_model=StatusResponse, summary="갱신 중단")
def refresh_cancel():
    """도는 작업을 중단한다. 돌고 있지 않으면 409.

    ⚠️ **끊긴 단계의 산출물은 반쯤 쓰였을 수 있다** — 빌더 셋이 파일을 통째로 덮어쓰는데
    그 쓰기가 원자적이지 않다. 다행히 **다섯 단계 전부 다시 돌리면 고쳐지므로**
    처방은 "다시 누르세요" 하나다.
    """
    if service.cancel() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="지금 도는 갱신이 없습니다.",
        )
    return _status_payload()
