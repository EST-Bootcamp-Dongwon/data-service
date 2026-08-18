"""시장 상세 차트 라우터 (컨트롤러 계층) — `/market` 화면이 쓴다

    GET /api/chart/presets     선택 상자 목록 (지수·환율·원자재 + 겹쳐볼 거시지표)
    GET /api/chart/series      캔들 + 이동평균 + 거래량 (기간 토글)
    GET /api/chart/overlay     주가 위에 금리·환율·물가 겹쳐 보기
    GET /api/chart/breadth     국내 시장의 폭 — 상승 종목 수 추이
    GET /api/chart/compare     여러 종목 100 기준 지수화 비교

조립은 `app/services/market_chart.py` 가 하고, 여기서는 응답 형식(DTO)과
입력 검증만 담당한다.

대시보드(`/api/dashboard/*`)와 달리 **여기는 오류를 그대로 올린다.** 대시보드는 카드가
여러 장이라 하나가 실패해도 나머지를 보여 줘야 하지만, 이 화면은 차트 한 장이 주인공이라
실패를 숨기면 빈 화면만 남는다. 다만 `overlay` 안의 **개별 지표 실패**는 예외로,
나머지 계열은 그리도록 `errors` 에 모아 200 으로 돌려준다.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.clients import yf_data
from app.services import market_chart as service

router = APIRouter(prefix="/api/chart", tags=["시장 상세 차트"])


# ==================================================
# DTO
# ==================================================
class PresetItem(BaseModel):
    key: str = Field(..., description="야후 티커", examples=["^KS11"])
    label: str = Field(..., description="화면 표기", examples=["코스피"])
    group: str = Field(..., description="묶음", examples=["국내 지수"])
    currency: str = Field("", description="통화", examples=["KRW"])


class OverlayItem(BaseModel):
    id: str = Field(..., description="지표 식별자 (`fred:` 또는 `ecos:` 접두)",
                    examples=["ecos:base_rate"])
    label: str = Field(..., description="화면 표기", examples=["한은 기준금리"])
    unit: str = Field("", description="단위", examples=["%"])
    group: str = Field(..., description="묶음", examples=["금리"])
    source: str = Field(..., description="출처 기관", examples=["ECOS"])


class PeriodItem(BaseModel):
    key: str = Field(..., description="기간 코드", examples=["1y"])
    label: str = Field(..., description="화면 표기", examples=["1년"])


class PresetsResponse(BaseModel):
    presets: List[PresetItem] = Field(..., description="차트에 띄울 대상")
    overlays: List[OverlayItem] = Field(..., description="겹쳐 그릴 거시지표")
    periods: List[PeriodItem] = Field(..., description="고를 수 있는 기간")
    max_overlays: int = Field(..., description="한 번에 겹칠 수 있는 지표 수")
    max_compare: int = Field(..., description="한 번에 비교할 수 있는 종목 수")
    max_points: int = Field(..., description="한 계열이 내려보내는 점 상한 (넘으면 최근 구간만)")


class SeriesStats(BaseModel):
    first: Optional[float] = Field(None, description="구간 첫 종가")
    last: Optional[float] = Field(None, description="구간 마지막 종가")
    change: Optional[float] = Field(None, description="구간 변화 폭")
    change_rate: Optional[float] = Field(None, description="구간 수익률(%)")
    min: Optional[float] = Field(None, description="구간 최저")
    max: Optional[float] = Field(None, description="구간 최고")
    volatility_annual: Optional[float] = Field(
        None, description="연율화 변동성(%) — 일간 로그수익률 표준편차 × √252")
    mdd: Optional[float] = Field(None, description="최대 낙폭(%) — 고점 대비 최대 하락")


class MovingAverage(BaseModel):
    window: int = Field(..., description="기간(거래일)", examples=[20])
    label: str = Field(..., description="화면 표기", examples=["20일선"])
    values: List[Optional[float]] = Field(..., description="앞쪽 `window-1` 개는 null")


class SeriesResponse(BaseModel):
    ticker: str
    label: str
    period: str
    period_label: str
    interval: str = Field(..., description="봉 간격 — `1d`·`5m`·`30m`")
    dates: List[str]
    candles: List[List[Optional[float]]] = Field(..., description="[시가, 고가, 저가, 종가]")
    closes: List[float]
    volumes: List[float]
    count: int
    stats: SeriesStats
    moving_averages: List[MovingAverage]
    fetched_at: str
    source: str = Field(..., description="`live` · `tmp-cache`")
    # 잘렸는지를 숨기지 않는다. 화면이 "전 구간을 보고 있다"고 오해하면 판단이 틀어진다.
    truncated: bool = Field(
        False, description="`max_points` 를 넘어 최근 구간만 내려보냈는지", examples=[False])
    total_count: int = Field(
        0, description="자르기 전 전체 점 수. `truncated` 가 참일 때 의미가 있다", examples=[7412])


class OverlayLine(BaseModel):
    id: str
    label: str
    unit: str = Field(..., description="지수화된 축의 단위")
    raw_unit: str = Field("", description="원래 단위 (툴팁용)")
    source: str
    indexed: List[Optional[float]] = Field(..., description="구간 첫날 = 100")
    raw: List[Optional[float]] = Field(..., description="원래 값")
    correlation: Optional[float] = Field(
        None, description="주가와의 **일간 변화율** 상관계수 (수준 상관이 아니다)")
    correlation_note: Optional[str] = Field(
        None, description="상관을 못 낸 사유 (`correlation` 이 null 일 때만)")


class OverlayError(BaseModel):
    id: str
    label: str
    error: str


class OverlayResponse(BaseModel):
    ticker: str
    label: str
    period: str
    period_label: str
    dates: List[str]
    lines: List[OverlayLine]
    errors: List[OverlayError] = Field(..., description="실패한 지표 (나머지는 그대로 그린다)")
    note: str = Field(..., description="표시 규칙 안내. 잘렸다면 그 사실도 이 문장에 들어간다")
    truncated: bool = Field(
        False, description="`max_points` 를 넘어 최근 구간만 내려보냈는지", examples=[False])
    total_count: int = Field(
        0, description="자르기 전 전체 점 수", examples=[7412])
    fetched_at: str


class BreadthLatest(BaseModel):
    date: str
    up: int
    down: int
    flat: int
    ratio: float
    total: int


class BreadthResponse(BaseModel):
    available: bool = Field(..., description="캐시가 없으면 False (배포 환경)")
    reason: Optional[str] = Field(None, description="`available=false` 인 사유")
    days: int
    dates: List[str] = []
    up: List[int] = []
    down: List[int] = []
    flat: List[int] = []
    ratio: List[float] = Field([], description="상승 비율(%) — 보합 제외")
    advance_decline: List[int] = Field([], description="누적 등락선 — (상승−하락) 누적합")
    value: List[float] = Field([], description="일별 거래대금 합")
    latest: Optional[BreadthLatest] = None
    note: str = ""
    fetched_at: str
    source: str = ""


class CompareLine(BaseModel):
    ticker: str
    label: str
    indexed: List[Optional[float]] = Field(..., description="구간 첫날 = 100")
    raw: List[Optional[float]]
    stats: SeriesStats


class CompareError(BaseModel):
    ticker: str
    error: str


class CompareResponse(BaseModel):
    period: str
    period_label: str = ""
    dates: List[str]
    lines: List[CompareLine]
    errors: List[CompareError]
    note: str = ""
    truncated: bool = Field(
        False, description="한 종목이라도 `max_points` 를 넘어 잘렸는지", examples=[False])
    total_count: int = Field(
        0, description="자르기 전 점 수 (가장 긴 종목 기준)", examples=[7412])
    fetched_at: str


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/presets", response_model=PresetsResponse, summary="선택 상자 목록")
def presets():
    """차트 화면이 처음 뜰 때 한 번 부른다. 고를 수 있는 것들을 전부 알려 준다.

    목록을 서버가 쥐고 있는 이유 — 화면과 API 가 같은 목록을 보게 하기 위함이다.
    화면에 하드코딩하면 지표를 추가할 때 두 곳을 고쳐야 하고, 어긋나면 404 가 난다.
    """
    return {
        "presets": service.presets(),
        "overlays": service.overlays(),
        "periods": [{"key": k, "label": v} for k, v in yf_data.PERIODS.items()],
        "max_overlays": service.MAX_OVERLAYS,
        "max_compare": service.MAX_COMPARE,
        "max_points": service.MAX_POINTS,
    }


@router.get("/series", response_model=SeriesResponse, summary="캔들 + 이동평균 + 거래량")
def series(
    ticker: str = Query("^KS11", description="야후 티커 (국내 6자리 숫자는 `.KS` 가 자동으로 붙는다)",
                        examples=["^KS11"]),
    period: str = Query("1y", description="조회 기간", examples=["1y"]),
    ma: bool = Query(True, description="이동평균(20·60·120일)을 함께 계산할지"),
    max_points: int = Query(
        service.MAX_POINTS, ge=10, le=20000,
        description="내려받을 점 상한. 넘으면 **최근 구간만** 오고 `truncated` 가 참이 된다"),
):
    """지수·종목 하나의 캔들 차트에 필요한 값을 돌려준다.

    **짧은 구간은 분봉으로 온다.** `1d` 는 5분봉, `5d` 는 30분봉이다 —
    일봉으로 받으면 점이 한두 개뿐이라 차트가 그려지지 않기 때문이다.
    실제로 어떤 간격을 썼는지는 `interval` 에 담아 보낸다.

    이동평균은 구간이 짧으면 아예 빼고 보낸다. 120일선을 60점짜리 차트에 얹으면
    계열 전체가 null 이라 범례만 남는다.

    **`period=max` 는 상장 이후 전 구간이라** 코스피는 7,000봉이 넘는다.
    `max_points`(기본 3,000)를 넘으면 최근 구간만 오고 `truncated` 가 참이 된다.
    이동평균은 **자르기 전 전체에서 계산한 값**이라 왼쪽 끝이 비지 않는다.
    """
    try:
        return service.series(ticker, period, with_ma=ma, max_points=max_points)
    except yf_data.YahooError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.get("/overlay", response_model=OverlayResponse, summary="거시지표 겹쳐 보기")
def overlay(
    ticker: str = Query("^KS11", description="기준이 될 지수·종목"),
    period: str = Query("1y", description="조회 기간"),
    ids: str = Query("", description="겹칠 지표 id 를 쉼표로 (`/presets` 의 `overlays` 참고)",
                     examples=["ecos:base_rate,fred:DGS10"]),
    max_points: int = Query(
        service.MAX_POINTS, ge=10, le=20000,
        description="내려받을 점 상한. 주가에 걸면 거시지표도 그 날짜에 맞춰 짧아진다"),
):
    """주가 위에 금리·환율·물가를 겹쳐 그릴 값을 돌려준다.

    **Y축을 두 개 쓰지 않는다** (U3 결정). 주가는 원, 금리는 % 라 단위가 달라
    한 축에 그대로 올리면 눈금이 거짓말을 한다. 모든 계열을 **구간 첫날 = 100** 으로
    지수화해 보내고, 원래 값은 `raw` 에 함께 실어 툴팁이 실제 수치를 보여 준다.

    상관계수는 **수준이 아니라 일간 변화율** 기준이다. 수준끼리 재면 둘 다 우상향한다는
    이유만으로 상관이 높게 나온다(허위 상관).

    지표 하나가 실패해도 나머지는 그린다 — 실패한 것만 `errors` 에 담아 200 으로 돌려준다.

    구간이 `max_points` 보다 길면 최근 구간만 오고, 그 사실이 `truncated` 와 `note`
    양쪽에 담긴다.
    """
    wanted = [i.strip() for i in (ids or "").split(",") if i.strip()]
    try:
        return service.overlay(ticker, period, wanted, max_points=max_points)
    except yf_data.YahooError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.get("/breadth", response_model=BreadthResponse, summary="시장의 폭 — 상승 종목 수 추이")
def breadth(
    days: int = Query(120, ge=20, le=300, description="며칠치를 볼지 (거래일)"),
):
    """국내 시장에서 **몇 종목이 올랐는지**를 날짜별로 센다.

    지수는 시가총액 가중이라 대형주 몇 개에 끌려간다. "지수는 올랐는데 내 종목은 다 빠졌다"
    가 그래서 생긴다. 상승 종목 수는 종목마다 한 표씩 세므로 그 착시가 없다.

    누적 등락선(`advance_decline`)이 지수와 **반대로 움직이면** 상승이 소수 종목에
    쏠렸다는 신호다.

    로컬 캐시(`krx_cache.db`)로만 만든다. 배포 환경에는 캐시가 없으므로
    `available=false` 와 사유를 담아 **200 으로** 돌려준다 (오류가 아니라 그 환경의 한계다).
    """
    return service.breadth(days)


@router.get("/compare", response_model=CompareResponse, summary="종목 비교 (100 기준 지수화)")
def compare(
    tickers: str = Query(..., description="비교할 티커를 쉼표로", examples=["005930.KS,000660.KS,AAPL"]),
    period: str = Query("1y", description="조회 기간"),
    max_points: int = Query(
        service.MAX_POINTS, ge=10, le=20000,
        description="**종목 하나당** 점 상한. 비교는 계열 수만큼 응답이 커진다"),
):
    """여러 종목을 **구간 첫날 = 100** 으로 맞춰 한 차트에 올린다.

    절대가격 비교는 뜻이 없다 — 7만원짜리와 300달러짜리를 나란히 그리면
    비싼 쪽이 위에 있을 뿐 누가 더 올랐는지는 안 보인다. 지수화하면 상승률이 보인다.

    티커 하나가 실패해도 나머지는 그린다 (`errors` 에 담아 200).
    """
    wanted = [t.strip() for t in (tickers or "").split(",") if t.strip()]
    if not wanted:
        raise HTTPException(status_code=422, detail="비교할 티커를 하나 이상 넣어 주세요.")
    return service.compare(wanted, period, max_points=max_points)
