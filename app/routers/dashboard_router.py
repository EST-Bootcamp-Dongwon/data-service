"""대시보드 라우터 (컨트롤러 계층)

    GET /api/dashboard/ticker    상단 티커바 — 지수 3종 + 환율 (모든 화면이 부른다)
    GET /api/dashboard/summary   카드 그리드 — 값 + 스파크라인 + 상태등급 + 데이터 상태

집계는 `app/services/dashboard_data.py` 가 하고, 여기서는 응답 형식(DTO)만 담당한다.

**이 두 엔드포인트는 500 을 내지 않는다.** 야후 요청 한도·인증키 없음·캐시 없음은 흔한 상황이라
카드마다 `ok` 와 `error` 를 두고 200 으로 돌려준다. 화면은 실패한 카드만 회색으로 접는다.
(명세서 §6.4 의 `partial-continue` 원칙을 대시보드에 적용한 것이다)
"""

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import dashboard_data as service

router = APIRouter(prefix="/api/dashboard", tags=["대시보드"])


# ==================================================
# DTO
# ==================================================
class MarketCard(BaseModel):
    """지수·환율 카드 한 장. 실패해도 같은 형식으로 오고 `ok` 만 False 가 된다."""

    key: str = Field(..., description="카드 식별자", examples=["kospi"])
    label: str = Field(..., description="화면에 쓸 이름", examples=["코스피"])
    ticker: str = Field("", description="야후 티커", examples=["^KS11"])
    unit: str = Field("", description="단위", examples=["p"])
    digits: int = Field(2, description="표기 소수 자리수")
    ok: bool = Field(..., description="조회 성공 여부")
    error: Optional[str] = Field(None, description="실패 사유 (`ok=false` 일 때만)")
    value: Optional[float] = Field(None, description="최근 값", examples=[6595.20])
    diff: Optional[float] = Field(None, description="전일 대비 등락 폭", examples=[17.9])
    rate: Optional[float] = Field(None, description="전일 대비 등락률(%)", examples=[0.27])
    date: Optional[str] = Field(None, description="최근 값의 날짜", examples=["2026-07-31"])
    spark: List[float] = Field([], description="스파크라인용 종가 (오래된 → 최근, 최대 40점)")
    grade: Optional[str] = Field(
        None, description="상태등급 — `hot`·`warm`·`neutral`·`cool`·`cold`", examples=["warm"])
    grade_text: Optional[str] = Field(None, description="등급 이름 (색만으로 뜻을 전하지 않기 위함)",
                                      examples=["상승"])


class TemperatureCard(BaseModel):
    """시장 온도 — 상승 종목 비율로 잰다."""

    key: str = Field(..., description="카드 식별자", examples=["temperature"])
    label: str = Field(..., description="화면에 쓸 이름", examples=["시장 온도"])
    unit: str = Field("", description="단위", examples=["%"])
    digits: int = Field(1, description="표기 소수 자리수")
    ok: bool = Field(..., description="계산 성공 여부")
    error: Optional[str] = Field(None, description="실패 사유")
    value: Optional[float] = Field(None, description="상승 종목 비율(%) — 보합 제외", examples=[62.4])
    up: int = Field(0, description="상승 종목 수")
    down: int = Field(0, description="하락 종목 수")
    flat: int = Field(0, description="보합 종목 수")
    total: int = Field(0, description="집계한 종목 수")
    date: Optional[str] = Field(None, description="기준 거래일 (YYYYMMDD)", examples=["20260731"])
    source: Optional[str] = Field(None, description=(
        "출처 `<provider>-<tier>` (ADR-DS-0009) — `krx-db` · `krx-bundle` · "
        "`krx-live` · `krx-live-memo`"))
    grade: Optional[str] = Field(None, description="상태등급", examples=["warm"])
    grade_text: Optional[str] = Field(None, description="등급 이름", examples=["강세"])


class DataStatusRow(BaseModel):
    """데이터 상태 한 줄 — 인증키·캐시 현황. **키 값 자체는 절대 담지 않는다.**"""

    key: str = Field(..., description="항목 식별자", examples=["krx-cache"])
    label: str = Field(..., description="항목 이름", examples=["KRX 시세 캐시"])
    ok: bool = Field(..., description="확인 성공 여부")
    grade: str = Field(..., description="상태 — `good`·`warning`·`critical`", examples=["good"])
    grade_text: str = Field(..., description="상태 이름", examples=["정상"])
    detail: str = Field("", description="사람이 읽을 설명")


class TickerResponse(BaseModel):
    items: List[MarketCard] = Field(..., description="티커바 항목 (지수 3종 + 환율)")
    fetched_at: str = Field(..., description="조회 시각(KST)")
    source: str = Field(..., description="출처 — `live` · `tmp-cache`")


class SummaryResponse(BaseModel):
    markets: List[MarketCard] = Field(..., description="지수 4종 + 환율")
    rate: MarketCard = Field(..., description="미 국채 10년 금리 (FRED)")
    temperature: TemperatureCard = Field(..., description="시장 온도")
    data_status: List[DataStatusRow] = Field(..., description="인증키·캐시 현황")
    fetched_at: str = Field(..., description="조회 시각(KST)")
    source: str = Field(..., description="출처 — `live` · `tmp-cache`")
    notice: str = Field(..., description="교육·리서치 목적 고지")


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/ticker", response_model=TickerResponse, summary="티커바 — 지수·환율")
def ticker():
    """모든 화면 상단 티커바가 쓰는 값이다.

    화면마다 1분에 한 번 부르므로 **가볍게** 유지한다 — 스파크라인 없이 값과 등락만 담고,
    `/tmp` 에 3분 캐시를 둔다. 지수는 어차피 15분 이상 지연된 값이라 더 자주 부를 이유가 없다.
    """
    return service.ticker_bar()


@router.get("/summary", response_model=SummaryResponse, summary="대시보드 카드 집계")
def summary():
    """대시보드 카드 그리드에 필요한 값을 한 번에 돌려준다.

    카드마다 **값 · 스파크라인 · 상태등급**이 함께 온다. 등급 경계는 서버가 정하므로
    화면·리포트·MD 내보내기가 모두 같은 기준을 쓴다.

    | 카드 | 출처 | 등급 기준 |
    |---|---|---|
    | 코스피·코스닥·나스닥·S&P 500·원달러 | 야후 파이낸스 | 3개월 수익률 |
    | 미 국채 10년 금리 | FRED | 4개월 변화폭(%p) |
    | 시장 온도 | KRX 일별매매정보 | 상승 종목 비율 |

    데이터가 없는 카드는 `ok=false` 와 사유를 담아 **200 으로** 돌려준다.
    """
    return service.summary()
