"""차트 데이터 라우터 (컨트롤러 계층)

목업 생성 로직은 `market_data` 모듈(서비스 계층)에 두고,
여기서는 **요청 검증 + 응답 형식(DTO)** 만 담당한다.

화면(static/charts.js)은 이 엔드포인트들이 주는 값을 그대로 그리기만 한다.
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

import market_data as service

router = APIRouter(prefix="/api", tags=["차트 데이터"])


# ==================================================
# 공통 DTO
# ==================================================
class StockSummary(BaseModel):
    """종목 목록 항목"""

    code: str = Field(..., description="종목코드", examples=["005930"])
    name: str = Field(..., description="종목명", examples=["삼성전자"])
    market: str = Field(..., description="시장 구분 (KOSPI · KOSDAQ)", examples=["KOSPI"])
    price: int = Field(..., description="최근 종가 (원)", examples=[71200])


# ==================================================
# 1. 일봉 시세 (캔들 + 거래량 + 이동평균)
# ==================================================
class Candle(BaseModel):
    date: str = Field(..., description="거래일 (YYYY-MM-DD)", examples=["2026-07-30"])
    open: int = Field(..., description="시가 (원)", examples=[73900])
    high: int = Field(..., description="고가 (원)", examples=[74900])
    low: int = Field(..., description="저가 (원)", examples=[70800])
    close: int = Field(..., description="종가 (원)", examples=[71200])
    volume: int = Field(..., description="거래량 (주)", examples=[22307270])


class MovingAverage(BaseModel):
    period: int = Field(..., description="이동평균 기간 (일)", examples=[20])
    values: List[Optional[int]] = Field(
        ...,
        description="캔들과 같은 길이의 배열. 계산할 수 없는 앞쪽 구간은 `null`.",
    )


class OhlcvResponse(BaseModel):
    code: str = Field(..., description="종목코드", examples=["005930"])
    name: str = Field(..., description="종목명", examples=["삼성전자"])
    market: str = Field(..., description="시장 구분", examples=["KOSPI"])
    currency: str = Field(..., description="통화", examples=["KRW"])
    count: int = Field(..., description="캔들 개수 (거래일 수)", examples=[500])
    candles: List[Candle] = Field(..., description="날짜 오름차순 일봉 배열")
    moving_averages: List[MovingAverage] = Field(..., description="요청한 기간별 단순이동평균")


# ==================================================
# 2. 스크리닝 깔때기
# ==================================================
class FunnelStep(BaseModel):
    step: int = Field(..., description="단계 번호 (1부터)", examples=[3])
    name: str = Field(..., description="단계 이름", examples=["시가총액 1,000억 이상"])
    criteria: str = Field(..., description="적용한 조건 설명", examples=["유동성 확보"])
    count: int = Field(..., description="이 단계를 통과한 종목 수", examples=[1338])
    dropped: int = Field(..., description="이 단계에서 탈락한 종목 수", examples=[1153])
    ratio: float = Field(..., description="전체 대비 잔존 비율 (%)", examples=[51.22])
    pass_rate: float = Field(..., description="직전 단계 대비 통과율 (%)", examples=[53.71])


class FunnelResponse(BaseModel):
    as_of: str = Field(..., description="기준일 (KST)", examples=["2026-07-30"])
    universe: str = Field(..., description="스크리닝 모집단", examples=["KOSPI + KOSDAQ"])
    total: int = Field(..., description="시작 종목 수", examples=[2612])
    selected: int = Field(..., description="최종 편입 종목 수", examples=[20])
    steps: List[FunnelStep] = Field(..., description="단계별 결과")


# ==================================================
# 3. 효율적 투자선 (몬테카를로)
# ==================================================
class FrontierAsset(BaseModel):
    code: str = Field(..., description="종목·ETF 코드", examples=["005930"])
    name: str = Field(..., description="이름", examples=["삼성전자"])
    expected_return: float = Field(..., description="연 기대수익률 (0.112 = 11.2%)", examples=[0.112])
    volatility: float = Field(..., description="연 변동성 (표준편차)", examples=[0.254])


class Portfolio(BaseModel):
    return_: float = Field(..., alias="return", description="연 기대수익률", examples=[0.1284])
    volatility: float = Field(..., description="연 변동성", examples=[0.2013])
    sharpe: float = Field(..., description="샤프지수 = (수익률 - 무위험수익률) / 변동성", examples=[0.4789])
    weights: List[float] = Field(..., description="자산별 비중 (%). assets 순서와 같다.")

    # `return` 은 파이썬 예약어라 필드명으로 쓸 수 없어 alias 로 처리한다.
    # FastAPI 는 기본적으로 alias 로 응답을 직렬화하므로 JSON 키는 "return" 이 된다.
    model_config = {"populate_by_name": True}


class FrontierPoint(BaseModel):
    volatility: float = Field(..., description="연 변동성", examples=[0.1102])
    return_: float = Field(..., alias="return", description="연 기대수익률", examples=[0.0641])

    model_config = {"populate_by_name": True}


class FrontierResponse(BaseModel):
    risk_free_rate: float = Field(..., description="무위험수익률", examples=[0.032])
    samples: int = Field(..., description="시뮬레이션한 포트폴리오 개수", examples=[1200])
    assets: List[FrontierAsset] = Field(..., description="구성 자산")
    portfolios: List[Portfolio] = Field(..., description="무작위 비중 포트폴리오 (산점도용)")
    frontier: List[FrontierPoint] = Field(..., description="효율적 투자선 (변동성 오름차순)")
    max_sharpe: Portfolio = Field(..., description="샤프지수가 가장 높은 포트폴리오")
    min_variance: Portfolio = Field(..., description="변동성이 가장 낮은 포트폴리오")


# ==================================================
# 4. 팩터 점수 (방사형)
# ==================================================
class RadarStock(BaseModel):
    code: str = Field(..., description="종목코드", examples=["005930"])
    name: str = Field(..., description="종목명", examples=["삼성전자"])
    market: str = Field(..., description="시장 구분", examples=["KOSPI"])
    scores: List[int] = Field(..., description="팩터 점수 (0~100). factors 순서와 같다.")
    total: float = Field(..., description="종합점수 (팩터 점수 단순평균)", examples=[68.0])


class RadarResponse(BaseModel):
    factors: List[str] = Field(..., description="팩터 축 이름", examples=[["가치", "성장", "수익성", "안정성", "모멘텀", "배당"]])
    max_score: int = Field(..., description="점수 상한", examples=[100])
    stocks: List[RadarStock] = Field(..., description="선택한 종목들의 점수")


# ==================================================
# 엔드포인트
# ==================================================
@router.get(
    "/stocks",
    response_model=List[StockSummary],
    summary="종목 목록",
)
def get_stocks():
    """차트에서 고를 수 있는 목업 종목 목록을 반환한다."""
    return service.list_stocks()


@router.get(
    "/stocks/{code}/ohlcv",
    response_model=OhlcvResponse,
    summary="일봉 시세(OHLCV) + 이동평균",
    responses={404: {"description": "등록되지 않은 종목코드"}},
)
def get_ohlcv(
    code: str = Path(..., description="종목코드", examples=["005930"]),
    count: int = Query(
        service.DEFAULT_CANDLE_COUNT, ge=60, le=1000,
        description="가져올 거래일 수 (최근부터)",
    ),
    ma: List[int] = Query(
        list(service.DEFAULT_MA_PERIODS),
        description="이동평균 기간. 반복 지정 가능 — 예: `?ma=5&ma=20&ma=60`",
    ),
):
    """일봉과 이동평균을 함께 반환한다.

    - 종목코드를 난수 시드로 쓰므로 **여러 번 호출해도 같은 값**이 나온다.
    - 가격은 KRX 호가단위에 맞춰져 있고, 주말은 제외하지만 **공휴일은 반영하지 않는다.**
    - `moving_averages[].values` 는 캔들과 길이가 같고, 앞쪽 계산 불가 구간은 `null` 이다.
    """
    if code not in service.STOCK_BY_CODE:
        raise HTTPException(status_code=404, detail="등록되지 않은 종목코드입니다.")

    periods = tuple(p for p in ma if p >= 2)
    if not periods:
        raise HTTPException(status_code=422, detail="이동평균 기간은 2 이상이어야 합니다.")
    if any(p > count for p in periods):
        raise HTTPException(status_code=422, detail="이동평균 기간이 거래일 수보다 클 수 없습니다.")

    return service.get_ohlcv(code, count=count, ma_periods=periods)


@router.get(
    "/screening/funnel",
    response_model=FunnelResponse,
    summary="종목 스크리닝 깔때기",
)
def get_screening_funnel():
    """조건을 단계별로 걸어 후보를 좁혀 나가는 과정을 반환한다.

    난수가 아닌 **고정 시나리오**이므로 항상 같은 값이다.
    """
    return service.screening_funnel()


@router.get(
    "/portfolio/frontier",
    response_model=FrontierResponse,
    summary="효율적 투자선 (몬테카를로 시뮬레이션)",
)
def get_frontier(
    samples: int = Query(
        service.DEFAULT_FRONTIER_SAMPLES, ge=100, le=5000,
        description="시뮬레이션할 무작위 포트폴리오 개수",
    ),
):
    """무작위 비중 포트폴리오를 뿌려 위험-수익 평면과 효율적 투자선을 만든다.

    - `portfolios` — 산점도용. 각 점은 하나의 포트폴리오(비중 조합)다.
    - `frontier` — 같은 수익률에서 변동성이 가장 낮은 지점을 이은 상단 경계.
    - `max_sharpe` · `min_variance` — 강조 표시용 특이점.

    시드가 고정이라 `samples` 가 같으면 항상 같은 결과가 나온다.
    """
    return service.monte_carlo_frontier(samples)


@router.get(
    "/factors/radar",
    response_model=RadarResponse,
    summary="팩터 점수 (방사형 차트)",
    responses={404: {"description": "팩터 점수가 없는 종목코드"}},
)
def get_factor_radar(
    codes: List[str] = Query(
        list(service.DEFAULT_RADAR_CODES),
        description="비교할 종목코드. 반복 지정 — 예: `?codes=005930&codes=000660`",
    ),
):
    """선택한 종목들의 팩터 점수(0~100)를 반환한다.

    가치·성장·수익성·안정성·모멘텀·배당 6개 축이며, 종목 성격에 맞춰 고정한 값이다.
    """
    if not codes:
        raise HTTPException(status_code=422, detail="종목코드를 1개 이상 지정해야 합니다.")

    unknown = [c for c in codes if not service.has_factor_scores(c)]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"팩터 점수가 없는 종목코드입니다: {', '.join(unknown)}",
        )

    # 중복은 제거하고 요청 순서는 유지한다
    ordered = list(dict.fromkeys(codes))
    return service.factor_radar(ordered)
