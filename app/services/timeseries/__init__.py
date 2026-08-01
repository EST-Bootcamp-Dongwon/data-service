"""시계열 엔진 (서비스 계층) — 명세서 §4

`numpy` + `pandas` 만으로 시계열 분석 한 벌을 직접 구현한 곳이다.
`scipy` · `statsmodels` · `torch` 를 쓰지 않는다 (기획서 D3).

    from app.services import timeseries as ts

    ts.stationarity.adf(x)                  정상성 검정
    ts.correlogram.acf(x) / .pacf(x)        상관도
    ts.models.fit_best(prices)              차수 선택 + 적합 + 확률보행 벤치마크
    ts.forecast.interval(model, 20)         점예측 + 신뢰구간
    ts.backtest.walk_forward(prices)        확장창 검증

모듈 구성 (명세서 §4.1 모듈별 계약 표)
------------------------------------
| 모듈 | 하는 일 | 근거 강의 |
|---|---|---|
| `numerics`    | 정규·카이제곱 분포 함수 (scipy 대체) | — |
| `transform`   | 로그수익률 · 차분 · 역차분 · 이동평균 | 05-6 · 05-10 |
| `decompose`   | 추세 · 계절 · 잔차 (중심이동평균) | 05-4 |
| `stationarity`| ADF (MacKinnon 임계값 하드코딩) | 05-10 |
| `correlogram` | ACF(Bartlett) · PACF(Durbin-Levinson) | 05-7 · 05-8 |
| `models`      | AR(OLS) · ARIMA(Hannan-Rissanen) · 확률보행 · AIC 격자탐색 | 05-8 · 05-9 |
| `forecast`    | 점예측 · ψ-weight 신뢰구간 · 상승확률 · 시나리오 3단 | 05-5 · 05-10 · 08-14 |
| `backtest`    | walk-forward (rmse · mae · 적중률 · 확률보행 대비) | 04 |

이 패키지가 지키는 두 가지
------------------------
1. **근사는 근사라고 말한다.** ADF 임계값·Ljung-Box p값·신뢰구간의 가정은 각 함수의
   `limitation` 으로 나가 리포트까지 그대로 실린다.
2. **확률보행과 늘 겨룬다.** `models.fit_best` 는 벤치마크를 함께 돌려주고,
   `backtest.walk_forward` 는 이기지 못하면 그 사실을 `verdict` 에 적는다 (기획서 D6).
"""

from app.services.timeseries import (backtest, correlogram, decompose, forecast,
                                     models, numerics, stationarity, transform)

__all__ = ["backtest", "correlogram", "decompose", "forecast",
           "models", "numerics", "stationarity", "transform"]
