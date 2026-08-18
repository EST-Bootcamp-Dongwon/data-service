"""종목 통합 조회 라우터 (컨트롤러 계층)

화면(`static/pages/stock.html`)이 쓰는 API 다. **엔드포인트 하나로 국내·미국 주식을 모두** 받는다.

    GET /api/stock/{ticker}                              최근 6개월 일별 종가
    GET /api/stock/{ticker}?months=3                     기간 지정
    GET /api/stock/{ticker}?macro=DGS10,DEXKOUS          FRED 거시지표 겹쳐 보기
    GET /api/stock/samples                               예시 종목 목록

판별·조회·계산은 전부 `app/services/stock_service.py` 가 하고,
여기서는 **요청 검증 + 응답 형식(DTO) + 예외 → HTTP 상태 코드** 만 담당한다.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.services import stock_service as service

router = APIRouter(prefix="/api/stock", tags=["종목 통합 조회"])


# ==================================================
# DTO
# ==================================================
class Change(BaseModel):
    diff: Optional[float] = Field(None, description="전일 대비 등락 폭", examples=[-1500.0])
    rate: Optional[float] = Field(None, description="전일 대비 등락률(%)", examples=[-0.72])


class MovingAverage(BaseModel):
    period: int = Field(..., description="이동평균 기간(일)", examples=[20])
    values: List[Optional[float]] = Field(
        ..., description="`prices` 와 같은 길이. 계산할 수 없는 앞 구간은 `null`.")


class MacroSeries(BaseModel):
    """주가 날짜에 맞춰 붙인 FRED 거시지표 하나."""

    series_id: str = Field(..., description="FRED 시리즈 ID", examples=["DGS10"])
    ok: bool = Field(..., description="조회 성공 여부. `false` 면 `error` 만 채워진다.")
    error: Optional[str] = Field(None, description="실패 사유")
    label: Optional[str] = Field(None, description="지표 이름(한국어)", examples=["미 국채 10년 금리"])
    title: Optional[str] = Field(None, description="FRED 원문 제목")
    unit: Optional[str] = Field(None, description="단위", examples=["%"])
    frequency: Optional[str] = Field(None, description="발표 주기", examples=["Daily"])
    note: Optional[str] = Field(None, description="주가와 함께 보는 이유")
    values: Optional[List[Optional[float]]] = Field(
        None, description="`dates` 에 맞춘 원래 값 (직전 발표치를 이어 쓴다)")
    rebased: Optional[List[Optional[float]]] = Field(
        None, description="시작을 100 으로 맞춘 값 — 주가와 한 그림에 겹칠 때 쓴다")
    latest: Optional[float] = Field(None, description="가장 최근 값")
    latest_date: Optional[str] = Field(None, description="가장 최근 발표일")
    correlation: Optional[float] = Field(
        None, description="주가와의 상관계수 (일간 변화율 기준, -1 ~ 1)", examples=[-0.31])


class StockResponse(BaseModel):
    ticker: str = Field(..., description="해석된 야후 티커", examples=["005930.KS"])
    input: str = Field(..., description="사용자가 입력한 원문", examples=["삼성전자"])
    code: str = Field(..., description="종목코드 또는 티커", examples=["005930"])
    market: str = Field(..., description="시장 구분 — `KR`(한국) · `US`(미국·해외)", examples=["KR"])
    market_detail: str = Field("", description="세부 시장", examples=["KOSPI"])
    name: str = Field(..., description="종목명 (국내는 한글)", examples=["삼성전자"])
    name_en: str = Field("", description="영문 종목명 (한글명과 다를 때만)")
    currency: str = Field(..., description="통화 코드", examples=["KRW"])
    source: str = Field(..., description="데이터 출처 — `yfinance` · `krx-cache`", examples=["yfinance"])
    market_state: str = Field("", description="장 상태 — REGULAR(장중) · CLOSED(장마감)")
    months: int = Field(..., description="조회 개월 수", examples=[6])

    dates: List[str] = Field(..., description="거래일 배열 (오름차순)", examples=[["2026-02-02", "2026-02-03"]])
    prices: List[Optional[float]] = Field(..., description="`dates` 와 같은 순서의 **종가** 배열")
    opens: List[Optional[float]] = Field(..., description="시가 배열")
    highs: List[Optional[float]] = Field(..., description="고가 배열")
    lows: List[Optional[float]] = Field(..., description="저가 배열")
    volumes: List[Optional[float]] = Field(..., description="거래량 배열")
    moving_averages: List[MovingAverage] = Field(..., description="단순이동평균 5·20·60일")

    count: int = Field(..., description="거래일 수", examples=[123])
    current: Optional[float] = Field(None, description="최근 종가", examples=[207000.0])
    prev_close: Optional[float] = Field(None, description="직전 거래일 종가")
    change: Change = Field(..., description="전일 대비 등락")
    period_open: Optional[float] = Field(None, description="구간 첫 종가")
    period_low: Optional[float] = Field(None, description="구간 최저 종가")
    period_high: Optional[float] = Field(None, description="구간 최고 종가")
    period_change_rate: Optional[float] = Field(None, description="구간 수익률(%)")
    volume: Optional[float] = Field(None, description="최근 거래일 거래량")
    market_cap: Optional[float] = Field(None, description="시가총액 (야후 조회 시에만)")
    week52_low: Optional[float] = Field(None, description="52주 최저가")
    week52_high: Optional[float] = Field(None, description="52주 최고가")

    macro: List[MacroSeries] = Field([], description="함께 요청한 FRED 거시지표")
    fetched_at: str = Field(..., description="조회 시각(KST)")
    elapsed_ms: int = Field(..., description="조회에 걸린 시간(ms)")


class SampleTicker(BaseModel):
    ticker: str = Field(..., description="입력에 넣을 값", examples=["005930"])
    label: str = Field(..., description="버튼에 표시할 이름", examples=["삼성전자"])
    market: str = Field(..., description="시장 구분", examples=["KR"])


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/samples", response_model=List[SampleTicker], summary="예시 종목 목록")
def samples():
    """화면의 예시 버튼을 만들 목록. 국내는 거래대금 상위, 미국은 대표 종목이다.

    경로 순서에 주의 — `/{ticker}` 보다 **먼저** 선언해야 `samples` 가 티커로 해석되지 않는다.
    """
    return service.sample_tickers()


@router.get(
    "/{ticker}",
    response_model=StockResponse,
    summary="종목 시세 조회 (국내·미국 통합)",
    responses={
        404: {"description": "알 수 없는 종목 — 티커·종목코드·종목명을 찾지 못함"},
        422: {"description": "입력이 비었거나 허용하지 않는 months"},
        502: {"description": "야후 파이낸스 응답 실패"},
    },
)
def stock(
    ticker: str = Path(
        ...,
        description="종목코드(`005930`) · 한글 종목명(`삼성전자`) · 야후 티커(`AAPL` · `005930.KS`)",
        examples=["005930"],
    ),
    months: int = Query(
        service.DEFAULT_MONTHS,
        description=f"조회 개월 수 — {' · '.join(str(m) for m in service.MONTH_PERIODS)}",
        examples=[6],
    ),
    macro: str = Query(
        "",
        description=(
            "함께 볼 FRED 거시지표 ID 를 쉼표로 잇는다 (예: `DGS10,DEXKOUS`). "
            "지표 목록은 `GET /api/fred/indicators` 에 있다."
        ),
        examples=["DGS10,DEXKOUS"],
    ),
):
    """**국내·미국 주식을 엔드포인트 하나로** 조회한다.

    입력을 보고 알아서 판별한다.

    | 입력 | 해석 |
    |---|---|
    | `005930` | 국내 종목코드 → KRX 캐시에서 시장을 확인해 `.KS`/`.KQ` 를 붙인다 |
    | `삼성전자` | 한글 종목명 → KRX 캐시에서 코드를 찾는다 |
    | `AAPL` | 미국 티커 |
    | `005930.KS` | 이미 접미사가 붙은 티커는 그대로 |

    응답의 `dates`·`prices` 두 배열이 차트에 바로 들어간다.
    `macro` 를 주면 그 지표를 **주가 날짜에 맞춰** 붙이고 상관계수까지 계산해 준다.
    """
    try:
        data = service.fetch_stock(ticker, months)
    except service.StockError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error

    # 거시지표는 있으면 얹고, 실패해도 주가 응답 자체는 살린다
    ids = [s for s in (macro or "").split(",") if s.strip()]
    data["macro"] = service.attach_macro(data, ids) if ids else []
    return data
