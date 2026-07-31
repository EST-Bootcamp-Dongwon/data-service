"""FRED 거시경제 지표 라우터 (컨트롤러 계층)

    GET /api/fred/indicators              화면에 띄울 큐레이션 지표 목록
    GET /api/fred/series/{series_id}      지표 하나의 시계열
    GET /api/fred/search?q=unemployment   FRED 전체에서 지표 검색
    GET /api/fred/status                  인증키 상태 진단

호출 코드는 `app/clients/fred_data.py` 에 있고, 여기서는 요청 검증과 응답 형식만 담당한다.
**인증키는 응답 어디에도 실려 나가지 않는다** (`/status` 도 길이만 알려준다).
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.clients import fred_data as api

router = APIRouter(prefix="/api/fred", tags=["FRED 거시지표"])


# ==================================================
# DTO
# ==================================================
class Indicator(BaseModel):
    id: str = Field(..., description="FRED 시리즈 ID", examples=["DGS10"])
    label: str = Field(..., description="지표 이름(한국어)", examples=["미 국채 10년 금리"])
    unit: str = Field(..., description="단위", examples=["%"])
    group: str = Field(..., description="분류", examples=["금리"])
    note: str = Field(..., description="주가와 함께 보는 이유")


class SeriesResponse(BaseModel):
    series_id: str = Field(..., description="시리즈 ID", examples=["DGS10"])
    label: str = Field(..., description="지표 이름(한국어). 큐레이션에 없으면 FRED 원문 제목")
    title: str = Field(..., description="FRED 원문 제목", examples=["Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity"])
    unit: str = Field("", description="단위(축약)", examples=["%"])
    units: str = Field("", description="단위(원문)", examples=["Percent"])
    frequency: str = Field("", description="발표 주기", examples=["Daily"])
    frequency_short: str = Field("", description="발표 주기(축약)", examples=["D"])
    note: str = Field("", description="주가와 함께 보는 이유")
    dates: List[str] = Field(..., description="발표일 배열 (오름차순)")
    values: List[float] = Field(..., description="`dates` 와 같은 순서의 값")
    count: int = Field(..., description="관측치 수", examples=[128])
    latest: Optional[float] = Field(None, description="가장 최근 값", examples=[4.67])
    latest_date: str = Field(..., description="가장 최근 발표일", examples=["2026-07-29"])
    first: Optional[float] = Field(None, description="구간 첫 값")
    change: Optional[float] = Field(None, description="구간 변화 (마지막 - 처음)")
    change_rate: Optional[float] = Field(None, description="구간 변화율(%)")
    min: Optional[float] = Field(None, description="구간 최솟값")
    max: Optional[float] = Field(None, description="구간 최댓값")
    fetched_at: str = Field(..., description="조회 시각(KST)")
    elapsed_ms: int = Field(..., description="FRED 응답에 걸린 시간(ms)")


class SearchRow(BaseModel):
    series_id: str = Field(..., description="시리즈 ID")
    title: str = Field(..., description="지표 제목")
    unit: str = Field("", description="단위")
    frequency: str = Field("", description="발표 주기(축약)")
    start: str = Field("", description="관측 시작일")
    end: str = Field("", description="관측 종료일")
    popularity: int = Field(0, description="FRED 인기도 (0~100)")


class SearchResponse(BaseModel):
    query: str = Field(..., description="검색어")
    count: int = Field(..., description="결과 수")
    rows: List[SearchRow] = Field(..., description="검색 결과")
    fetched_at: str = Field(..., description="조회 시각(KST)")


class StatusResponse(BaseModel):
    key_loaded: bool = Field(..., description="인증키를 찾았는지")
    key_source: str = Field(..., description="키를 읽은 곳 — `환경변수 ...` · `.env` · `.key` · `none`")
    key_length: int = Field(..., description="키 길이 (값 자체는 노출하지 않는다)", examples=[32])
    indicator_count: int = Field(..., description="큐레이션 지표 개수")
    last_result: Optional[str] = Field(None, description="마지막 호출 결과")
    last_detail: Optional[str] = Field(None, description="마지막 실패 사유")


# ==================================================
# 공통 — FRED 예외를 HTTP 응답으로 바꾼다
# ==================================================
def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except api.FredError as error:
        raise HTTPException(status_code=error.status, detail=str(error))


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/indicators", response_model=List[Indicator], summary="큐레이션 지표 목록")
def indicators():
    """주가와 겹쳐 보기 좋은 지표만 골라 둔 목록. 화면의 지표 칩을 이 값으로 만든다.

    FRED 에는 시리즈가 80만 개라 그대로 보여 주면 고를 수가 없다.
    여기 없는 지표는 `GET /api/fred/search` 로 찾아서 ID 를 직접 넣으면 된다.
    """
    return list(api.INDICATORS)


@router.get("/status", response_model=StatusResponse, summary="인증키 상태 진단")
def status():
    """인증키를 읽었는지와 마지막 호출 결과를 알려준다. FRED 를 다시 부르지는 않는다."""
    return api.get_status()


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="지표 검색",
    responses={422: {"description": "검색어가 비었음"}, 502: {"description": "FRED 응답 실패"}},
)
def search(
    q: str = Query(..., description="검색어 (영어)", examples=["unemployment"]),
    limit: int = Query(20, ge=1, le=100, description="가져올 개수"),
):
    """FRED 전체에서 이름으로 지표를 찾는다. 인기순으로 정렬해서 돌려준다."""
    return _guard(api.search_series, q, limit)


@router.get(
    "/series/{series_id}",
    response_model=SeriesResponse,
    summary="지표 시계열 조회",
    responses={
        404: {"description": "없는 시리즈 ID 이거나 해당 기간 데이터가 없음"},
        422: {"description": "시리즈 ID 형식이 FRED 규칙에 맞지 않음"},
        503: {"description": "FRED 인증키가 설정되지 않음"},
        502: {"description": "FRED 응답 실패"},
    },
)
def series(
    series_id: str = Path(..., description="FRED 시리즈 ID", examples=["DGS10"]),
    start: str = Query("", description="조회 시작일 `YYYY-MM-DD`. 비우면 전 구간", examples=["2026-01-01"]),
    end: str = Query("", description="조회 종료일 `YYYY-MM-DD`", examples=["2026-07-31"]),
):
    """지표 하나의 시계열을 날짜 오름차순으로 돌려준다.

    결측치(FRED 가 `"."` 로 주는 미발표·휴장 구간)는 빼고 내려주므로 차트에 구멍이 나지 않는다.
    """
    return _guard(api.fetch_series, series_id, start, end)
