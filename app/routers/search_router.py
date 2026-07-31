"""종목 자동완성 검색 라우터 (컨트롤러 계층)

    GET /api/search?q=삼성      국내·미국 통합 검색 (최대 10건)
    GET /api/search?q=AAP&limit=5&market=US
    GET /api/search/stats       색인 현황

HTS 처럼 **입력할 때마다** 부르는 API 라 응답이 빨라야 한다.
종목 목록은 `app/services/search_service.py` 가 서버 시작 시 메모리에 올려 두므로,
이 엔드포인트는 파일도 DB 도 외부 API 도 건드리지 않는다.
"""

from typing import List

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services import search_service as service

router = APIRouter(prefix="/api/search", tags=["종목 검색 (자동완성)"])


class SearchItem(BaseModel):
    ticker: str = Field(..., description="조회에 쓰는 티커 (국내는 `.KS`·`.KQ` 포함)",
                        examples=["005930.KS"])
    code: str = Field(..., description="화면에 보여 줄 코드 (국내 6자리 · 미국 티커)",
                      examples=["005930"])
    name: str = Field(..., description="종목명", examples=["삼성전자"])
    market: str = Field(..., description="시장 구분 — `KR` · `US`", examples=["KR"])
    exchange: str = Field(..., description="세부 거래소", examples=["KOSPI"])
    is_etf: bool = Field(False, description="ETF 여부 (미국 종목만 구분된다)")


class SearchStats(BaseModel):
    total: int = Field(..., description="색인된 전체 종목 수", examples=[15414])
    kr: int = Field(..., description="국내 종목 수", examples=[2764])
    us: int = Field(..., description="미국 종목 수", examples=[12650])


@router.get(
    "",
    response_model=List[SearchItem],
    summary="종목 자동완성 검색",
)
def search(
    q: str = Query(..., description="검색어 — 종목명 일부(`삼성`) 또는 티커 일부(`AAP`)",
                   examples=["삼성"]),
    limit: int = Query(10, ge=1, le=service.MAX_LIMIT, description="가져올 개수 (기본 10)"),
    market: str = Query("", description="시장으로 좁히기 — `KR` · `US` (비우면 전체)"),
):
    """검색어가 포함된 종목을 **관련도 순으로** 돌려준다.

    관련도는 이렇게 매긴다 (위일수록 먼저).

    1. 티커가 정확히 일치 — `AAPL` → AAPL
    2. 티커가 검색어로 시작 — `AAP` → **AAPL**
    3. 종목명이 검색어로 시작 — `삼성` → **삼성전자**
    4. 종목명에 포함 — `전자` → LG전자
    5. 티커 중간에 포함

    같은 순위 안에서는 ETF 가 아닌 것, 티커가 짧은 것을 먼저 준다.
    응답의 `ticker` 를 그대로 `GET /api/stock/{ticker}` 에 넘기면 시세를 받을 수 있다.
    """
    return service.search(q, limit=limit, market=market)


@router.get("/stats", response_model=SearchStats, summary="색인 현황")
def stats():
    """메모리에 올라간 종목이 몇 개인지 알려준다. (화면 배지·상태 확인용)"""
    return service.stats()
