"""야후 파이낸스 시세 라우터 (컨트롤러 계층)

화면(`static/pages/yf.html`)과 스크립트(`scripts/yf.py`)가 함께 쓰는 API 다.

    GET /api/yf/quote?ticker=005930.KS              당일 가격 지표 5종 + 차트 데이터
    GET /api/yf/history?ticker=005930.KS&period=3mo 기간별 일봉 (캔들·거래량)
    GET /api/yf/periods                             선택 가능한 조회 구간 목록

설계 원칙
--------
- **계산은 서버에서 한다.** Y축 범위(±1%)·등락률·구간 수익률까지 계산해서 내려주므로
  화면은 받은 값을 그리기만 하면 된다. matplotlib 으로 그리든 ApexCharts 로 그리든 같은 그림이 나온다.
- **인증키가 필요 없다.** 야후 파이낸스는 공개 데이터라 KRX·KOSIS 와 달리 키 설정이 없다.
- **잘못된 티커는 404 로 알린다.** yfinance 는 없는 종목이어도 예외를 내지 않으므로
  클라이언트 계층(`app/clients/yf_data.py`)에서 판단해 예외로 바꾼 것을 여기서 HTTP 로 옮긴다.
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.clients import yf_data as api           # 외부 연동 (야후 파이낸스 호출)

router = APIRouter(prefix="/api/yf", tags=["야후 파이낸스 시세"])


# ==================================================
# DTO
# ==================================================
class Prices(BaseModel):
    """당일 가격 지표 5종. 값을 못 받은 항목은 `null` 이다."""

    prev_close: Optional[float] = Field(None, description="전일종가 — `previousClose`", examples=[207000.0])
    open: Optional[float] = Field(None, description="시가 — `open`", examples=[257000.0])
    day_low: Optional[float] = Field(None, description="당일 저가 — `dayLow`", examples=[243000.0])
    day_high: Optional[float] = Field(None, description="당일 고가 — `dayHigh`", examples=[261500.0])
    current: Optional[float] = Field(None, description="현재가 — `currentPrice` 또는 `regularMarketPrice`", examples=[261000.0])


class Change(BaseModel):
    diff: Optional[float] = Field(None, description="전일 대비 등락 폭", examples=[54000.0])
    rate: Optional[float] = Field(None, description="전일 대비 등락률(%)", examples=[26.09])


class QuoteChart(BaseModel):
    """차트에 그대로 넣는 형태. 막대·꺾은선이 같은 값을 가리킨다."""

    categories: List[str] = Field(..., description="가로축 라벨(한국어)", examples=[["전일종가", "시가", "저가", "고가", "현재가"]])
    categories_en: List[str] = Field(..., description="가로축 라벨(영어) — matplotlib 한글 폰트가 없을 때 쓴다")
    values: List[Optional[float]] = Field(..., description="라벨 순서대로의 가격")
    y_min: float = Field(..., description="Y축 최소 (최저가 -1%)", examples=[204930.0])
    y_max: float = Field(..., description="Y축 최대 (최고가 +1%)", examples=[264115.0])


class QuoteResponse(BaseModel):
    ticker: str = Field(..., description="정규화된 티커", examples=["005930.KS"])
    name: str = Field(..., description="종목명", examples=["Samsung Electronics Co., Ltd."])
    currency: str = Field("", description="통화 코드", examples=["KRW"])
    exchange: str = Field("", description="거래소 코드", examples=["KSC"])
    quote_type: str = Field("", description="종목 종류", examples=["EQUITY"])
    market_state: str = Field("", description="장 상태 — REGULAR(장중) · CLOSED(장마감) 등", examples=["REGULAR"])
    prices: Prices = Field(..., description="당일 가격 지표")
    change: Change = Field(..., description="전일 대비 등락")
    volume: Optional[float] = Field(None, description="당일 거래량", examples=[42532531.0])
    market_cap: Optional[float] = Field(None, description="시가총액")
    week52_low: Optional[float] = Field(None, description="52주 최저가")
    week52_high: Optional[float] = Field(None, description="52주 최고가")
    chart: QuoteChart = Field(..., description="차트용 데이터")
    fetched_at: str = Field(..., description="조회 시각(KST)", examples=["2026-07-31 15:04:11 KST"])
    elapsed_ms: int = Field(..., description="야후 응답에 걸린 시간(ms)", examples=[1240])


class HistoryRow(BaseModel):
    date: str = Field(..., description="거래일", examples=["2026-07-31"])
    open: Optional[float] = Field(None, description="시가")
    high: Optional[float] = Field(None, description="고가")
    low: Optional[float] = Field(None, description="저가")
    close: Optional[float] = Field(None, description="종가")
    volume: Optional[float] = Field(None, description="거래량")


class HistoryResponse(BaseModel):
    ticker: str = Field(..., description="정규화된 티커")
    period: str = Field(..., description="조회 구간 코드", examples=["3mo"])
    period_label: str = Field(..., description="조회 구간 이름", examples=["3개월"])
    rows: List[HistoryRow] = Field(..., description="날짜 오름차순 일봉")
    count: int = Field(..., description="행 수", examples=[62])
    change_rate: Optional[float] = Field(None, description="구간 수익률(%) — 첫 종가 대비 마지막 종가")
    fetched_at: str = Field(..., description="조회 시각(KST)")
    elapsed_ms: int = Field(..., description="야후 응답에 걸린 시간(ms)")


class PeriodItem(BaseModel):
    code: str = Field(..., description="구간 코드", examples=["3mo"])
    label: str = Field(..., description="구간 이름", examples=["3개월"])


# ==================================================
# 공통 — 야후 조회 예외를 HTTP 응답으로 바꾼다
# ==================================================
def _guard(fn, *args, **kwargs):
    """`YahooError` 를 그 안에 담긴 상태 코드로 바꿔 던진다.

    화면은 상태 코드와 `detail` 만 보고 사용자에게 안내하면 된다.
    """
    try:
        return fn(*args, **kwargs)
    except api.YahooError as error:
        raise HTTPException(status_code=error.status, detail=str(error))


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/periods", response_model=List[PeriodItem], summary="조회 구간 목록")
def periods() -> List[Dict[str, str]]:
    """기간별 시세에서 고를 수 있는 구간을 돌려준다. 화면의 기간 버튼을 이 값으로 만든다."""
    return [{"code": code, "label": label} for code, label in api.PERIODS.items()]


@router.get(
    "/quote",
    response_model=QuoteResponse,
    summary="당일 가격 지표 5종",
    responses={
        404: {"description": "티커가 잘못됐거나 가격을 받지 못함"},
        422: {"description": "티커를 비워서 요청함"},
        502: {"description": "야후 파이낸스 응답 실패"},
    },
)
def quote(
    ticker: str = Query("005930.KS", description="야후 티커. 국내는 코스피 `.KS` · 코스닥 `.KQ`", examples=["005930.KS"]),
):
    """**전일종가 · 시가 · 저가 · 고가 · 현재가** 를 한 번에 조회한다.

    - 숫자 6자리만 주면(`005930`) 코스피 종목으로 보고 `.KS` 를 붙인다.
    - 같은 티커를 60초 안에 다시 물으면 서버 메모리 캐시로 돌려준다.
    - `chart` 를 그대로 막대+꺾은선으로 그리면 `scripts/yf.py` 가 그리는 그림과 같아진다.
    """
    return _guard(api.fetch_quote, ticker)


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="기간별 일봉",
    responses={
        404: {"description": "해당 구간의 시세가 없음"},
        422: {"description": "허용하지 않는 period"},
        502: {"description": "야후 파이낸스 응답 실패"},
    },
)
def history(
    ticker: str = Query("005930.KS", description="야후 티커", examples=["005930.KS"]),
    period: str = Query("3mo", description=f"조회 구간 — {' · '.join(api.PERIODS)}", examples=["3mo"]),
):
    """기간별 일봉을 조회한다. 캔들 차트와 거래량 막대에 바로 쓸 수 있는 형태로 돌려준다."""
    return _guard(api.fetch_history, ticker, period)
