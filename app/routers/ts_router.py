"""시계열 엔진 라우터 (컨트롤러 계층) — 명세서 §6.2 · `/timeseries` 화면이 쓴다

    GET /api/ts/series        정제 일봉 + §3.2 품질 리포트
    GET /api/ts/decompose     추세 · 계절 · 잔차
    GET /api/ts/diagnostics   ADF · ACF · PACF · 추천 차수
    GET /api/ts/forecast      §4.4 예측 3단 + 백테스트

조립은 `app/services/ts_service.py` 가 하고, 여기서는 입력 검증과 응답 형식만 맡는다.

오류 규약 — **자료 부족은 오류가 아니다** (명세서 §6.4)
--------------------------------------------------
전처리 판정이 `insufficient` 면 모델링을 건너뛰지만 **200 으로 답한다.**
`status: "partial-continue"` 와 사유를 실어 보내고, 부른 쪽이 다음 단계로 넘어가게 한다
(GIC 불변원칙 §2-2). 진짜 오류(티커를 못 찾음·야후 장애)만 4xx·5xx 로 올린다.

    "자료가 모자라 못 했다" 와 "고장났다" 는 다른 말이다.
    둘을 같은 코드로 답하면 화면이 둘을 구분해 안내할 수 없다.

응답을 왜 `response_model` 로 조이지 않는가
----------------------------------------
이 API 들은 진단 정보(계수·후보 목록·`limitation` 문장)를 폭넓게 싣는다. 필드를 하나하나
DTO 로 고정하면 엔진을 손볼 때마다 두 곳을 고쳐야 하고, 빠뜨리면 **조용히 잘려 나간다** —
근거를 숨기지 않겠다는 이 프로젝트의 원칙과 정면으로 어긋난다. 대신 `/docs` 에 응답 예시를
글로 적어 둔다.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import stock_service
from app.services import ts_service as service

# 야후는 해외 종목에만 쓴다. 없는 환경에서도 국내 시계열은 돌아야 하므로
# 예외 클래스를 조건부로 잡는다 (`ts_service` 도 같은 방식으로 흡수한다).
try:
    from app.clients.yf_data import YahooError
except ModuleNotFoundError:
    class YahooError(Exception):        # 자리만 채우는 대체 클래스
        status = 503

router = APIRouter(prefix="/api/ts", tags=["시계열 엔진"])


def _guard(call):
    """티커·야후 오류를 HTTP 로 옮긴다. **자료 부족은 여기로 오지 않는다** (200 으로 나간다)."""
    try:
        return call()
    except stock_service.StockError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error
    except service.SourceUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except YahooError as error:
        raise HTTPException(status_code=getattr(error, "status", 502),
                            detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# ==================================================
# 1. 정제 시계열 + 품질 리포트
# ==================================================
@router.get("/series", summary="정제 일봉 + 품질 리포트")
def series(
    ticker: str = Query("005930", description="종목코드·한글명·해외 티커",
                        examples=["005930"]),
    years: int = Query(2, ge=1, le=5, description="가져올 기간(년) — 1·2·3·5"),
):
    """전처리(명세서 §3)를 마친 일봉과 품질 리포트를 돌려준다.

    **가격 소스가 시장마다 다르다** — 국내는 KRX, 해외는 야후다 (M3 결정).
    국내는 KRX 의 실제 개장일 캘린더를 전처리에 함께 넘겨 연휴를 결측으로 오판하지 않는다.
    해외는 KRX 캘린더가 맞지 않으므로 근사로 센다. 어느 쪽을 썼는지는
    `quality.calendar_source` 가 밝힌다.

    `modelable` 이 `false` 면 뒤의 세 API 는 `partial-continue` 로 답한다.
    """
    return _guard(lambda: service.load_series(ticker, years=years))


# ==================================================
# 2. 분해
# ==================================================
@router.get("/decompose", summary="추세 · 계절 · 잔차")
def decompose(
    ticker: str = Query("005930", description="종목코드·한글명·해외 티커"),
    years: int = Query(2, ge=1, le=5, description="가져올 기간(년)"),
    period: int = Query(5, ge=2, le=60, description="계절 주기(거래일) — 5=주간 · 21=월간"),
    model: str = Query("additive", pattern="^(additive|multiplicative)$",
                       description="가법(additive) · 승법(multiplicative)"),
):
    """중심이동평균으로 추세를 뽑고 계절·잔차를 가른다 (명세서 §4.1 · 05-4).

    **계절 성분이 나온다고 계절성이 있다는 뜻은 아니다.** 일봉 주가의 요일 효과는 대체로
    아주 약하다. 그래서 `seasonal_strength`(계절 진폭 ÷ 잔차 표준편차)를 함께 낸다 —
    1보다 작으면 잡음을 계절이라 부르고 있는 것이다. `strength_text` 에 그 판정이 있다.
    """
    return _guard(lambda: service.decompose(ticker, years=years, period=period, model=model))


# ==================================================
# 3. 진단
# ==================================================
@router.get("/diagnostics", summary="ADF · ACF · PACF · 추천 차수")
def diagnostics(
    ticker: str = Query("005930", description="종목코드·한글명·해외 티커"),
    years: int = Query(2, ge=1, le=5, description="가져올 기간(년)"),
    nlags: int = Query(30, ge=5, le=60, description="상관도를 볼 시차 수"),
    on_returns: bool = Query(True, description="로그수익률 위에서 볼지 (끄면 가격 수준)"),
):
    """정상성·상관도·추천 차수를 한 번에 돌려준다 (명세서 §4.1).

    ADF 를 **가격과 로그수익률 양쪽에** 돌린다. 주가는 거의 언제나 가격에서 비정상,
    수익률에서 정상으로 나오는데, 그 대비가 "왜 차분하는가" 의 근거가 된다.

    ⚠️ ADF 임계값은 MacKinnon(1994) 계수를 하드코딩한 **근사값**이며 정확한 p값은 내지 않는다
    (명세서 §4.2). 그 사실이 응답의 `limitations` 에 실려 나간다.
    """
    return _guard(lambda: service.diagnostics(ticker, years=years, nlags=nlags,
                                              on_returns=on_returns))


# ==================================================
# 4. 예측 3단
# ==================================================
@router.get("/forecast", summary="예측 3단 (통계 · 확률 · 시나리오) + 백테스트")
def forecast(
    ticker: str = Query("005930", description="종목코드·한글명·해외 티커"),
    years: int = Query(2, ge=1, le=5, description="학습에 쓸 기간(년)"),
    horizon: int = Query(20, ge=1, le=60, description="예측 일수(거래일)"),
    folds: int = Query(12, ge=3, le=30, description="백테스트 폴드 수"),
):
    """명세서 §4.4 의 3단 산출을 그대로 만든다.

    | 단 | 담는 것 |
    |---|---|
    | `statistical` | 모형·점예측·95% 신뢰구간·AIC·잔차 진단 |
    | `probability` | 상승확률 + walk-forward 백테스트 (확률보행 대비 포함) |
    | `scenarios`   | Bear·Base·Bull 목표가·확률·트리거·무효화 |

    **`caveats` 를 반드시 함께 읽어야 한다.** 맨 앞 줄이 백테스트 결론이며, ARIMA 가
    확률보행을 이기지 못했으면 그 사실이 그대로 적힌다 (기획서 D6). 주가는 효율시장에
    가까워 못 이기는 것이 정상에 가깝고, 이 API 는 그것을 감추지 않는다.

    시나리오 규칙 (M3 확정)
      - **경계**는 최근 60거래일 고점·저점(기술적) — 차트에서 눈으로 확인할 수 있다
      - **확률·목표가**는 예측분포(통계) — 경계를 넘을 확률과 그 영역의 조건부 기대값
      - σ 배수로 영역을 나누면 확률이 언제나 31/38/31% 로 고정돼 정보가 없기 때문이다
    """
    return _guard(lambda: service.forecast(ticker, years=years, horizon=horizon,
                                           folds=folds))
