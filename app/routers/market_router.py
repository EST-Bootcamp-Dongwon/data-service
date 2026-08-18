"""시장 분석 라우터 (컨트롤러 계층)

계산 로직은 `app/services/market_data.py`(서비스 계층)에 두고,
여기서는 **요청 검증 + 응답 형식(DTO)** 만 담당한다.

화면(`static/pages/quant.html`)은 이 엔드포인트들이 주는 값을 그대로 그리기만 한다.
모든 값은 `data/krx_cache.db` 에 쌓인 **실제 KRX 일별 시세**에서 계산된다.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.repositories import krx_store as store  # 저장소 (SQLite 캐시)
from app.services import market_data as service  # 서비스 (분석 계산)

router = APIRouter(prefix="/api", tags=["시장 분석"])


def _require_cache() -> None:
    """DB 가 비어 있으면 안내와 함께 503 을 돌려준다.

    화면에 빈 차트를 보여주는 것보다, 무엇을 해야 하는지 알려주는 편이 낫다.
    """
    if not store.latest_date():
        raise HTTPException(
            status_code=503,
            detail="시세 캐시가 비어 있습니다. 터미널에서 `python3 scripts/fetch_krx.py` 를 먼저 실행하세요.",
        )


# ==================================================
# 1. 종목 목록
# ==================================================
class StockSummary(BaseModel):
    """종목 목록 항목"""

    code: str = Field(..., description="종목코드", examples=["005930"])
    name: str = Field(..., description="종목명", examples=["삼성전자"])
    market: str = Field(..., description="시장 구분 (KOSPI · KOSDAQ)", examples=["KOSPI"])
    price: Optional[int] = Field(None, description="최근 종가 (원)", examples=[207000])
    change_rate: Optional[float] = Field(None, description="전일 대비 등락률 (%)", examples=[-0.72])
    value: Optional[int] = Field(None, description="거래대금 (원)", examples=[9915591555814])
    market_cap: Optional[int] = Field(None, description="시가총액 (원)", examples=[1236000000000000])


# ==================================================
# 2. 일봉 시세 (캔들 + 거래량 + 이동평균)
# ==================================================
class Candle(BaseModel):
    date: str = Field(..., description="거래일 (YYYY-MM-DD)", examples=["2026-07-30"])
    open: Optional[int] = Field(None, description="시가 (원)", examples=[214000])
    high: Optional[int] = Field(None, description="고가 (원)", examples=[226000])
    low: Optional[int] = Field(None, description="저가 (원)", examples=[202000])
    close: Optional[int] = Field(None, description="종가 (원)", examples=[207000])
    volume: int = Field(..., description="거래량 (주)", examples=[46694193])
    value: int = Field(..., description="거래대금 (원)", examples=[9915591555814])
    change: Optional[int] = Field(None, description="전일 대비 (원)", examples=[-1500])
    change_rate: Optional[float] = Field(None, description="등락률 (%)", examples=[-0.72])


class MovingAverage(BaseModel):
    period: int = Field(..., description="이동평균 기간 (일)", examples=[20])
    values: List[Optional[int]] = Field(
        ...,
        description="캔들과 같은 길이의 배열. 계산할 수 없는 앞쪽 구간은 `null`.",
    )


class OhlcvResponse(BaseModel):
    code: str = Field(..., description="종목코드", examples=["005930"])
    name: Optional[str] = Field(None, description="종목명", examples=["삼성전자"])
    market: Optional[str] = Field(None, description="시장 구분", examples=["KOSPI"])
    currency: str = Field(..., description="통화", examples=["KRW"])
    count: int = Field(..., description="캔들 개수 (거래일 수)", examples=[120])
    first_date: Optional[str] = Field(None, description="가장 오래된 거래일", examples=["2025-11-21"])
    last_date: Optional[str] = Field(None, description="가장 최근 거래일", examples=["2026-07-30"])
    candles: List[Candle] = Field(..., description="날짜 오름차순 일봉 배열")
    moving_averages: List[MovingAverage] = Field(..., description="요청한 기간별 단순이동평균")


# ==================================================
# 3. 스크리닝 깔때기
# ==================================================
class FunnelStep(BaseModel):
    step: int = Field(..., description="단계 번호 (1부터)", examples=[4])
    name: str = Field(..., description="단계 이름", examples=["시가총액 1,000억 이상"])
    criteria: str = Field(..., description="적용한 조건 설명", examples=["유동성·안정성 확보"])
    count: int = Field(..., description="이 단계를 통과한 종목 수", examples=[1338])
    dropped: int = Field(..., description="이 단계에서 탈락한 종목 수", examples=[1153])
    ratio: float = Field(..., description="전체 대비 잔존 비율 (%)", examples=[48.41])
    pass_rate: float = Field(..., description="직전 단계 대비 통과율 (%)", examples=[53.71])


class FunnelPick(BaseModel):
    """최종 편입 종목"""

    code: str = Field(..., description="종목코드", examples=["005930"])
    name: Optional[str] = Field(None, description="종목명", examples=["삼성전자"])
    market: Optional[str] = Field(None, description="시장 구분", examples=["KOSPI"])
    close: Optional[int] = Field(None, description="최근 종가", examples=[207000])
    score: Optional[float] = Field(None, description="종합점수 (모멘텀·안정성·유동성 백분위 평균)", examples=[87.4])
    momentum: float = Field(..., description="관측기간 수익률 (%)", examples=[18.32])
    volatility: float = Field(..., description="연율 변동성 (%)", examples=[31.5])
    avg_value: int = Field(..., description="평균 거래대금 (원)", examples=[512000000000])
    market_cap: int = Field(..., description="시가총액 (원)", examples=[1236000000000000])


class FunnelResponse(BaseModel):
    as_of: Optional[str] = Field(None, description="기준 거래일", examples=["2026-07-30"])
    universe: str = Field(..., description="스크리닝 모집단", examples=["KOSPI + KOSDAQ"])
    window: int = Field(..., description="지표 관측 기간 (거래일)", examples=[60])
    total: int = Field(..., description="시작 종목 수", examples=[2764])
    selected: int = Field(..., description="최종 편입 종목 수", examples=[20])
    steps: List[FunnelStep] = Field(..., description="단계별 결과")
    picks: List[FunnelPick] = Field(..., description="최종 통과 종목")
    note: Optional[str] = Field(None, description="안내 문구 (없으면 null)")


# ==================================================
# 4. 효율적 투자선 (몬테카를로)
# ==================================================
class FrontierAsset(BaseModel):
    code: str = Field(..., description="종목코드", examples=["005930"])
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
    # ⚠️ M5 (U8) 부터 **상수가 아니다.** ECOS 국고채 3년을 실측해 쓰고, 금융감독원
    #    정기예금 금리로 교차검증한다. 예전 하드코딩 0.032 는 실측(3.758%)과 0.56%p
    #    어긋나 있었고, 샤프지수가 통째로 그 위에 얹혀 있었다.
    #    `response_model` 이 모르는 필드를 잘라 내므로 출처도 **여기 선언해야** 응답에 실린다.
    risk_free_rate: float = Field(..., description="무위험수익률 — ECOS 국고채 3년 실측 (U8)",
                                  examples=[0.03758])
    risk_free_source: Optional[dict] = Field(
        None, description="무위험수익률의 출처·기준일·교차검증 후보 (없으면 대비값을 쓴 것)")
    samples: int = Field(..., description="시뮬레이션한 포트폴리오 개수", examples=[1200])
    window: Optional[int] = Field(None, description="수익률 계산에 쓴 거래일 수", examples=[250])
    observations: Optional[int] = Field(None, description="실제로 사용한 일간 수익률 개수", examples=[249])
    as_of: Optional[str] = Field(None, description="기준 거래일", examples=["2026-07-30"])
    assets: List[FrontierAsset] = Field(..., description="구성 자산 (실제 종가로 계산)")
    portfolios: List[Portfolio] = Field(..., description="무작위 비중 포트폴리오 (산점도용)")
    frontier: List[FrontierPoint] = Field(..., description="효율적 투자선 (변동성 오름차순)")
    max_sharpe: Optional[Portfolio] = Field(None, description="샤프지수가 가장 높은 포트폴리오")
    min_variance: Optional[Portfolio] = Field(None, description="변동성이 가장 낮은 포트폴리오")
    note: Optional[str] = Field(None, description="안내 문구 (없으면 null)")


# ==================================================
# 5. 팩터 점수 (방사형)
# ==================================================
class RadarStock(BaseModel):
    code: str = Field(..., description="종목코드", examples=["005930"])
    name: Optional[str] = Field(None, description="종목명", examples=["삼성전자"])
    market: Optional[str] = Field(None, description="시장 구분", examples=["KOSPI"])
    scores: List[int] = Field(..., description="팩터 백분위 점수 (0~100). factors 순서와 같다.")
    total: float = Field(..., description="종합점수 (팩터 점수 단순평균)", examples=[68.0])
    raw: dict = Field(..., description="백분위로 바꾸기 전 원자료")


class RadarResponse(BaseModel):
    factors: List[str] = Field(
        ..., description="팩터 축 이름",
        examples=[["모멘텀", "안정성", "유동성", "규모", "추세", "회전율"]],
    )
    max_score: int = Field(..., description="점수 상한", examples=[100])
    window: Optional[int] = Field(None, description="지표 관측 기간 (거래일)", examples=[60])
    as_of: Optional[str] = Field(None, description="기준 거래일", examples=["2026-07-30"])
    universe_size: Optional[int] = Field(None, description="백분위 기준이 된 종목 수", examples=[2764])
    stocks: List[RadarStock] = Field(..., description="선택한 종목들의 점수")
    note: Optional[str] = Field(None, description="안내 문구")


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/stocks", response_model=List[StockSummary], summary="종목 목록")
def get_stocks(
    limit: int = Query(30, ge=1, le=500, description="가져올 종목 수 (거래대금 상위부터)"),
    market: Optional[str] = Query(None, description="시장 구분으로 거르기 — `KOSPI` · `KOSDAQ`"),
    q: str = Query("", description="종목명·종목코드 부분 검색"),
):
    """차트에서 고를 수 있는 종목 목록을 **거래대금 순**으로 반환한다.

    가장 최근 거래일(`krx_cache.db` 기준) 스냅샷에서 만든다.
    """
    _require_cache()
    return service.list_stocks(limit=limit, market=market, q=q)


@router.get(
    "/stocks/{code}/ohlcv",
    response_model=OhlcvResponse,
    summary="일봉 시세(OHLCV) + 이동평균",
    responses={404: {"description": "캐시에 없는 종목코드"}},
)
def get_ohlcv(
    code: str = Path(..., description="종목코드", examples=["005930"]),
    count: int = Query(
        service.DEFAULT_CANDLE_COUNT, ge=5, le=250,
        description="가져올 거래일 수 (최근부터). 캐시에 있는 만큼만 반환된다.",
    ),
    ma: List[int] = Query(
        list(service.DEFAULT_MA_PERIODS),
        description="이동평균 기간. 반복 지정 가능 — 예: `?ma=5&ma=20&ma=60`",
    ),
):
    """일봉과 이동평균을 함께 반환한다.

    - 값은 전부 **실제 KRX 일별매매정보**이며 이미 호가단위에 맞는 가격이다.
    - 캐시에 있는 거래일만 반환하므로, 기간이 짧으면 `python3 scripts/fetch_krx.py --days 250` 을 실행한다.
    - `moving_averages[].values` 는 캔들과 길이가 같고, 앞쪽 계산 불가 구간은 `null` 이다.
    """
    _require_cache()

    # 거래일 수보다 긴 이동평균은 계산할 수 없으므로 조용히 걸러낸다.
    # (기간 버튼으로 30일치만 볼 때 기본값 MA60 때문에 요청 전체가 실패하면 곤란하다)
    periods = tuple(p for p in ma if 2 <= p <= count)
    if not periods:
        raise HTTPException(
            status_code=422,
            detail=f"계산할 수 있는 이동평균 기간이 없습니다. 2 이상 {count} 이하로 지정하세요.",
        )

    result = service.get_ohlcv(code, count=count, ma_periods=periods)
    if not result["count"]:
        raise HTTPException(status_code=404, detail=f"캐시에 없는 종목코드입니다: {code}")
    return result


@router.get("/screening/funnel", response_model=FunnelResponse, summary="종목 스크리닝 깔때기")
def get_screening_funnel(
    window: int = Query(service.DEFAULT_WINDOW, ge=20, le=250,
                        description="지표를 계산할 거래일 수"),
):
    """조건을 단계별로 걸어 후보를 좁혀 나가는 과정을 **실데이터로** 계산한다.

    KRX 일별매매정보에는 재무제표가 없으므로 PER·PBR·ROE 대신
    시가총액 · 거래대금 · 변동성 · 모멘텀 · 이동평균 조건을 사용한다.
    같은 거래일에 대해서는 결과가 항상 같다.
    """
    _require_cache()
    return service.screening_funnel(window=window)


@router.get("/portfolio/frontier", response_model=FrontierResponse,
            summary="효율적 투자선 (몬테카를로 시뮬레이션)")
def get_frontier(
    samples: int = Query(service.DEFAULT_FRONTIER_SAMPLES, ge=100, le=5000,
                         description="시뮬레이션할 무작위 포트폴리오 개수"),
    codes: List[str] = Query(list(service.DEFAULT_FRONTIER_CODES),
                             description="구성 종목. 반복 지정 — 예: `?codes=005930&codes=000660`"),
    window: int = Query(service.FRONTIER_WINDOW, ge=60, le=250,
                        description="수익률·공분산을 계산할 거래일 수"),
):
    """무작위 비중 포트폴리오를 뿌려 위험-수익 평면과 효율적 투자선을 만든다.

    - 기대수익률·변동성·상관계수는 **실제 KRX 종가**에서 계산한다.
    - `portfolios` — 산점도용. 각 점은 하나의 포트폴리오(비중 조합)다.
    - `frontier` — 같은 수익률에서 변동성이 가장 낮은 지점을 이은 상단 경계.
    - `max_sharpe` · `min_variance` — 강조 표시용 특이점.

    난수는 비중 추첨에만 쓰고 시드가 고정이라, 같은 조건이면 항상 같은 결과가 나온다.
    """
    _require_cache()
    if len(codes) < 2:
        raise HTTPException(status_code=422, detail="종목을 2개 이상 지정해야 합니다.")
    if len(codes) > 10:
        raise HTTPException(status_code=422, detail="종목은 최대 10개까지 지정할 수 있습니다.")

    # 중복은 제거하고 요청 순서는 유지한다
    ordered = list(dict.fromkeys(codes))
    return service.monte_carlo_frontier(samples=samples, codes=ordered, window=window)


@router.get("/factors/radar", response_model=RadarResponse, summary="팩터 점수 (방사형 차트)",
            responses={404: {"description": "캐시에 없는 종목코드"}})
def get_factor_radar(
    codes: List[str] = Query(list(service.DEFAULT_RADAR_CODES),
                             description="비교할 종목코드. 반복 지정 — 예: `?codes=005930&codes=000660`"),
    window: int = Query(service.DEFAULT_WINDOW, ge=20, le=250,
                        description="지표를 계산할 거래일 수"),
):
    """선택한 종목들의 팩터 점수(0~100)를 반환한다.

    모멘텀 · 안정성 · 유동성 · 규모 · 추세 · 회전율 6개 축이며,
    각 점수는 **전 종목 대비 백분위**다. (유동성 98 = 거래대금 상위 2%)

    가치·성장·수익성 같은 재무 팩터는 KRX 일별매매정보로 계산할 수 없어 제외했다.
    """
    _require_cache()
    if not codes:
        raise HTTPException(status_code=422, detail="종목코드를 1개 이상 지정해야 합니다.")

    ordered = list(dict.fromkeys(codes))
    unknown = [c for c in ordered if not service.has_metrics(c, window)]
    if len(unknown) == len(ordered):
        raise HTTPException(
            status_code=404,
            detail=f"캐시에 없는 종목코드입니다: {', '.join(unknown)}",
        )
    return service.factor_radar(ordered, window=window)
