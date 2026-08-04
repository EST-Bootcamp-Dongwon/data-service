"""종목 자동완성 검색 라우터 (컨트롤러 계층)

    GET /api/search?q=삼성                  국내·미국 통합 검색 (최대 10건)
    GET /api/search?q=AAP&limit=5&market=US
    GET /api/search/stats                   색인 현황
    GET /api/search/semantic?q=메모리 반도체를 만드는 회사   자연어 검색 (M8 · N85)
    GET /api/search/semantic/stats          원문 색인 현황

**두 검색을 가른 이유** — 자동완성은 타이핑마다 날아가므로 수 ms 를 지켜야 하는데,
자연어 검색은 HuggingFace 왕복이라 그 계약에 못 들어간다 (실측 콜드 4.4초 · 웜 0.1초).
한 엔드포인트에 섞으면 빠른 쪽이 느린 쪽의 응답시간을 물려받는다.

HTS 처럼 **입력할 때마다** 부르는 API 라 응답이 빨라야 한다.
종목 목록은 `app/services/search_service.py` 가 서버 시작 시 메모리에 올려 두므로,
이 엔드포인트는 파일도 DB 도 외부 API 도 건드리지 않는다.
"""

from typing import List

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.repositories import report_index
from app.services import search_service as service
from app.services import semantic_search

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


# ══════════════════════════════════════════════════════════
# 자연어 검색 (M8 · 변경노트 N85)
# ══════════════════════════════════════════════════════════
#
# ⚠️ `response_model` 을 붙이지 않는다. 이 응답은 `mode` · `degraded_reason` ·
#    `similarity` · `cap_bonus` 처럼 **정직성을 담는 필드**가 많은데, 모델을 붙였다가
#    필드가 조용히 잘려 나간 적이 있다 (요약본 지뢰 8 — N30 · N58).
@router.get("/semantic", summary="자연어로 종목 찾기 (사업보고서 원문 검색)")
def semantic(
    q: str = Query(..., description="자연어 질의 — `메모리 반도체를 만드는 회사`",
                   examples=["메모리 반도체를 만드는 회사"]),
    limit: int = Query(10, ge=1, le=semantic_search.MAX_LIMIT, description="가져올 개수"),
):
    """**이름이 아니라 하는 일로** 종목을 찾는다.

    사업보고서 원문(사업개요 · 사업부문 · 주요 제품)을 빌드타임에 임베딩해 둔
    색인에서 질의와 가까운 종목을 준다.

    순위 = 코사인 유사도 + 규모 가중. 규모 가중은 **상위 유사도의 80% 이상인 후보에만**
    최대 0.2 를 더한다 — 그래야 무관한 대형주가 규모만으로 끼어들지 못한다.
    `similarity`(원값)와 `score`(보정값)를 둘 다 돌려주므로 가중이 순서를 얼마나
    바꿨는지 볼 수 있다.

    실측 (2026-08-04 · 색인 2,489곳 · 자연어 질의 20건) — top1 75% · top3 85% · top5 95%.
    **1위가 늘 맞지는 않는다.** 사업 설명이 비슷하면 역할이 달라도 가까이 놓인다
    (`자동차 완성차 제조` 에 부품사가 1위로 올라온다).

    HuggingFace 가 응답하지 않으면 **이름 검색으로 떨어지고** `degraded=true` 로 밝힌다.
    """
    return semantic_search.search(q, limit=limit)


@router.get("/semantic/stats", summary="원문 색인 현황")
def semantic_stats():
    """색인이 무엇을 담고 **무엇을 못 담았는지** 돌려준다.

    색인은 빌드 시점에 고정된다 — 언제 만든 것인지(`built_at`)와 못 담은 종목 수·사유를
    함께 낸다. 사업보고서는 연 1회(3월 집중) 나오므로 갱신은 수동이다.
    """
    return report_index.stats()
