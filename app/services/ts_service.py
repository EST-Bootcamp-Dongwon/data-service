"""시계열 API 조립 (서비스 계층) — 명세서 §4.4 · §6.2

`timeseries` 패키지는 **순수 계산**만 한다 (배열이 들어가고 배열이 나온다).
이 모듈이 그 앞뒤를 맡는다 — 어디서 자료를 가져올지 정하고, 전처리를 걸고,
명세서 §6.2 의 네 엔드포인트가 그대로 내보낼 모양으로 묶는다.

    시세 소스 선택  →  preprocess.run()  →  timeseries.*  →  §4.4 3단 산출

가격 소스 — 국내는 KRX · 해외는 야후 (M3 결정)
--------------------------------------------
| 시장 | 소스 | 왜 |
|---|---|---|
| 국내 (`005930`) | `krx_store` (배포본은 축약본) | 거래소 원본이고 **실제 개장일 캘린더가 함께 온다** |
| 해외 (`AAPL`)   | `yf_data` | KRX 는 국내만 다룬다 |

**KRX 캘린더를 미국 종목에 쓰면 안 된다.** 추석에 나스닥은 열리고 추수감사절에 KRX 는 연다.
남의 달력으로 결측을 세면 있지도 않은 구멍을 만들고 진짜 구멍은 놓친다.
그래서 국내에만 캘린더를 넘기고, 해외는 근사(주말 + 연휴 5일 이하 허용)로 간다.
어느 쪽을 썼는지는 품질 리포트의 `calendar_source` 가 밝힌다 (변경 노트 N16).

⚠️ 캘린더는 **덮는 구간 안에서만** 넘긴다
--------------------------------------
`preprocess.find_gaps` 는 캘린더에 없는 날을 "장이 안 선 날" 로 본다. 그래서 캘린더보다
앞선 구간에 캘린더를 들이대면 **그 구간의 결측을 통째로 못 본다.** 국내 자료는 KRX 에서
오므로 범위가 캘린더 안에 있는 것이 보장되지만, 그래도 아래에서 한 번 더 확인한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from app.core.trading_calendar import to_iso
from app.repositories import krx_store, tmp_cache
from app.services import preprocess, search_service, stock_service
from app.services import timeseries as ts

# 야후는 **해외 종목에만** 필요하다. 없어도 국내 시계열은 그대로 돌아야 하므로
# import 실패를 흡수한다 (`main.py` 가 yf 계열 라우터를 끄는 것과 같은 방식).
try:
    from app.clients import yf_data
except ModuleNotFoundError:                     # pip install yfinance 를 안 한 환경
    yf_data = None

KST = timezone(timedelta(hours=9))

# 한 해를 몇 거래일로 볼 것인가. 국내·미국 모두 250 안팎이다.
TRADING_DAYS_PER_YEAR = 250

# 기간 선택지 (화면 토글과 맞춘다)
YEAR_CHOICES = (1, 2, 3, 5)

# 야후에서 받을 때 쓰는 기간 문자열 — 연 단위를 가장 가까운 것으로 옮긴다
YEARS_TO_YF_PERIOD = {1: "1y", 2: "5y", 3: "5y", 5: "5y"}

# 모델링에 필요한 최소 표본. 이보다 짧으면 ADF·ARIMA 가 의미를 잃는다.
MIN_ROWS_FOR_MODEL = 60

DEFAULT_HORIZON = 20
MAX_HORIZON = 60


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


# ==================================================
# 1. 시세 불러오기 + 전처리
# ==================================================
def _krx_calendar(rows: Sequence[Dict]) -> Optional[List[str]]:
    """국내 거래일 캘린더. **자료 구간을 전부 덮을 때만** 돌려준다 (머리말 ⚠️).

    덮지 못하면 `None` 을 주어 근사 방식으로 넘어가게 한다. 반쪽짜리 캘린더를 넘기면
    앞 구간의 결측이 조용히 사라진다 — 품질 리포트가 실제보다 좋게 나온다.
    """
    calendar = [to_iso(d) for d in krx_store.available_dates(limit=2000)]
    if not calendar or not rows:
        return None

    calendar.sort()
    first_row = min((r.get("date") or "") for r in rows if r.get("date"))
    if not first_row or first_row < calendar[0]:
        return None                     # 자료가 캘린더보다 앞선다 → 쓰지 않는다
    return calendar


def load_series(ticker: str, years: int = 2, use_cache: bool = True) -> Dict:
    """정제된 일봉 + 품질 리포트 (명세서 §6.2 `/api/ts/series`).

    돌려주는 것
      `rows`      전처리를 마친 시계열 (`filled` 표시 포함)
      `quality`   명세서 §3.2 품질 리포트 (`verdict` · `calendar_source` 포함)
      `modelable` 시계열 모델링을 해도 되는지 — `False` 면 뒤 단계는 건너뛴다
      `source`    `krx-cache` · `krx-bundle` · `yfinance`
    """
    years = years if years in YEAR_CHOICES else 2
    resolved = stock_service.resolve(ticker)
    cache_key = f"{resolved['symbol']}_{years}"

    if use_cache:
        hit = tmp_cache.read("ts", cache_key, tmp_cache.TTL_TIMESERIES)
        if hit:
            hit["cached"] = True
            return hit

    if resolved["market"] == "KR":
        payload = _load_domestic(resolved, years)
    else:
        payload = _load_overseas(resolved, years)

    tmp_cache.write("ts", cache_key, payload)
    payload["cached"] = False
    return payload


def _load_domestic(resolved: Dict, years: int) -> Dict:
    """국내 — KRX 시세 + **실제 개장일 캘린더**."""
    days = years * TRADING_DAYS_PER_YEAR
    raw = krx_store.series(resolved["code"], days=days)
    source = f"krx-{krx_store.source()}"

    if not raw:
        # KRX 에 없으면(신규 상장·축약본 구간 밖) 야후로 물러난다.
        # 조용히 빈 결과를 주는 것보다 값을 주고 출처를 밝히는 편이 낫다.
        fallback = _load_overseas(resolved, years)
        fallback["notes"] = (fallback.get("notes") or []) + [
            f"KRX 시세({source})에 {resolved['code']} 자료가 없어 야후로 대체했습니다. "
            "야후는 수정주가라 KRX 원본과 값이 다를 수 있습니다."
        ]
        return fallback

    rows = [
        {"date": r.get("date"), "open": r.get("open"), "high": r.get("high"),
         "low": r.get("low"), "close": r.get("close"),
         "volume": r.get("volume"), "value": r.get("value")}
        for r in raw
    ]
    calendar = _krx_calendar(rows)
    result = preprocess.run(rows, ticker=resolved["code"], currency="KRW",
                            auto_adjust=False, source="krx",
                            trading_days=calendar)

    name = next((r.get("name") for r in reversed(raw) if r.get("name")), "") or resolved["code"]
    return _envelope(result, resolved, name=name, source=source, currency="KRW",
                     years=years,
                     notes=[] if calendar else [
                         "거래일 캘린더가 자료 구간을 덮지 못해 근사(주말 제외)로 결측을 셌습니다."])


def _overseas_name(symbol: str) -> str:
    """미국 종목명. `us_master.json` 색인을 뒤진다 — **외부 호출을 늘리지 않는다.**

    야후에 이름을 따로 물으면 종목마다 요청이 한 번 더 늘어 429 에 가까워진다.
    자동완성이 이미 메모리에 올려 둔 색인이 있으므로 그걸 쓴다.
    """
    try:
        for entry in search_service.search(symbol, limit=5):
            if (entry.get("ticker") or "").upper() == symbol.upper():
                return entry.get("name") or symbol
    except Exception:
        pass
    return symbol


class SourceUnavailable(Exception):
    """가격 소스를 쓸 수 없다 (야후 미설치 등). 라우터가 503 으로 옮긴다."""


def _load_overseas(resolved: Dict, years: int) -> Dict:
    """해외 — 야후 일봉. **KRX 캘린더를 넘기지 않는다** (머리말 참고)."""
    if yf_data is None:
        raise SourceUnavailable(
            f"{resolved['symbol']} 는 해외 종목이라 야후 파이낸스가 필요한데 "
            "`yfinance` 가 설치돼 있지 않습니다. `pip install yfinance` 후 다시 시도하세요. "
            "(국내 종목은 KRX 시세를 쓰므로 이 라이브러리 없이도 됩니다.)")

    period = YEARS_TO_YF_PERIOD.get(years, "5y")
    history = yf_data.fetch_history(resolved["symbol"], period=period)

    # `fetch_history` 는 `rows`(딕셔너리 목록)로 준다. 전처리가 바라는 모양 그대로다.
    rows = [
        {"date": r.get("date"), "open": r.get("open"), "high": r.get("high"),
         "low": r.get("low"), "close": r.get("close"), "volume": r.get("volume")}
        for r in (history.get("rows") or [])
    ]
    # 야후는 요청 기간이 커도 다 주므로, 원하는 연수만큼만 뒤에서 잘라 쓴다
    wanted = years * TRADING_DAYS_PER_YEAR
    if len(rows) > wanted:
        rows = rows[-wanted:]

    currency = "KRW" if resolved["market"] == "KR" else "USD"
    result = preprocess.run(rows, ticker=resolved["symbol"], currency=currency,
                            auto_adjust=True, source="yfinance",
                            trading_days=None)      # ← 남의 달력을 쓰지 않는다

    name = resolved.get("krx_name") or _overseas_name(resolved["symbol"])
    return _envelope(result, resolved, name=name,
                     source="yfinance", currency=currency, years=years,
                     notes=["해외 종목이라 거래소 캘린더 없이 근사(주말 + 연휴 5일 이하)로 "
                            "결측을 셌습니다. KRX 캘린더를 쓰면 휴장일 판정이 틀립니다."])


def _envelope(result: Dict, resolved: Dict, name: str, source: str,
              currency: str, years: int, notes: Sequence[str] = ()) -> Dict:
    """전처리 결과를 API 응답 모양으로 감싼다."""
    quality = result.get("quality") or {}
    rows = result.get("rows") or []
    modelable = bool(result.get("modelable")) and len(rows) >= MIN_ROWS_FOR_MODEL

    reason = ""
    if not result.get("modelable"):
        reason = (f"전처리 판정이 `{quality.get('verdict')}` 라 시계열 모델링을 건너뜁니다 "
                  "(명세서 §3.2 · GIC 불변원칙 §2-2 partial-continue).")
    elif len(rows) < MIN_ROWS_FOR_MODEL:
        reason = (f"정제 후 {len(rows)}행뿐이라 모델링을 건너뜁니다 "
                  f"(최소 {MIN_ROWS_FOR_MODEL}행 필요).")

    return {
        "ticker": resolved["symbol"],
        "code": resolved["code"],
        "name": name,
        "market": resolved["market"],
        "currency": currency,
        "years": years,
        "source": source,
        "rows": rows,
        "dates": [r.get("date") for r in rows],
        "close": [r.get("close") for r in rows],
        "volume": [r.get("volume") for r in rows],
        "value": [r.get("value") for r in rows],
        "quality": quality,
        "summary": preprocess.summarize(result),
        "verdict": result.get("verdict"),
        "modelable": modelable,
        "skip_reason": reason,
        "gaps": result.get("gaps") or [],
        "units": result.get("units") or {},
        "adjustment": result.get("adjustment") or {},
        "notes": list(notes),
        "fetched_at": _now_kst(),
    }


def _skipped(series: Dict, stage: str) -> Dict:
    """모델링을 건너뛸 때의 응답 (`partial-continue`).

    **오류가 아니다.** 자료가 모자란다는 사실을 200 으로 알리고, 부른 쪽이
    다음 단계로 넘어가게 한다 (명세서 §6.4 · GIC 불변원칙 §2-2).
    """
    return {
        "ticker": series["ticker"],
        "name": series["name"],
        "status": "partial-continue",
        "available": False,
        "stage": stage,
        "reason": series.get("skip_reason") or "자료가 모자랍니다.",
        "quality": series.get("quality"),
        "verdict": series.get("verdict"),
        "fetched_at": _now_kst(),
    }


# ==================================================
# 2. 분해
# ==================================================
def decompose(ticker: str, years: int = 2, period: int = 5,
              model: str = "additive") -> Dict:
    """추세 · 계절 · 잔차 (명세서 §6.2 `/api/ts/decompose`)."""
    series = load_series(ticker, years)
    if not series["modelable"]:
        return _skipped(series, "decompose")

    result = ts.decompose.classical(series["close"], period=period, model=model)
    return {
        "ticker": series["ticker"], "name": series["name"],
        "status": "ok" if result.get("available") else "partial-continue",
        "currency": series["currency"], "source": series["source"],
        "dates": series["dates"],
        **result,
        "quality_verdict": series["verdict"],
        "fetched_at": _now_kst(),
    }


# ==================================================
# 3. 진단 — ADF · ACF · PACF · 추천 차수
# ==================================================
def diagnostics(ticker: str, years: int = 2, nlags: int = 30,
                on_returns: bool = True) -> Dict:
    """정상성·상관도·추천 차수 (명세서 §6.2 `/api/ts/diagnostics`).

    ACF·PACF 는 **로그수익률** 위에서 본다 (`on_returns=True`). 가격 수준에서 보면
    ACF 가 1 에 붙어 서서히 줄어드는 그림만 나와 차수를 읽을 수 없다 — 추세 때문이지
    자기상관 때문이 아니다.
    """
    series = load_series(ticker, years)
    if not series["modelable"]:
        return _skipped(series, "diagnostics")

    prices = series["close"]
    returns = ts.transform.log_returns(prices)
    target = returns if on_returns else prices

    adf_price = ts.stationarity.adf(prices)
    adf_return = ts.stationarity.adf(returns)
    order = ts.models.select_order(prices)
    reading = ts.correlogram.suggest_order(target, nlags=nlags)

    return {
        "ticker": series["ticker"], "name": series["name"],
        "status": "ok",
        "currency": series["currency"], "source": series["source"],
        "n": len(prices),
        "analysed_on": "log_returns" if on_returns else "price",
        "adf_price": adf_price,
        "adf_returns": adf_return,
        "acf": reading["acf"],
        "pacf": reading["pacf"],
        "p_hint": reading["p_hint"],
        "q_hint": reading["q_hint"],
        "reading": reading["reading"],
        "suggested_order": {"p": order["p"], "d": order["d"], "q": order["q"],
                            "label": order.get("label"),
                            "reason": order.get("reason"),
                            "d_reason": order.get("d_reason")},
        "candidates": order.get("candidates", []),
        "returns_stats": ts.transform.summarize(returns),
        "quality_verdict": series["verdict"],
        "limitations": [adf_price.get("limitation")],
        "fetched_at": _now_kst(),
    }


# ==================================================
# 4. 예측 3단 (명세서 §4.4)
# ==================================================
def forecast(ticker: str, years: int = 2, horizon: int = DEFAULT_HORIZON,
             folds: int = 12, alpha: float = 0.05) -> Dict:
    """예측 3단 산출 — 통계 · 확률 · 시나리오 (명세서 §4.4 · §6.2 `/api/ts/forecast`).

    셋을 **한 번에** 낸다. 점예측만 떼어 보여 주면 독자가 그 숫자를 믿어 버리기 때문이다.
    구간·백테스트·시나리오가 함께 있어야 "얼마나 못 믿을 것인가" 가 같이 읽힌다.
    """
    series = load_series(ticker, years)
    if not series["modelable"]:
        return _skipped(series, "forecast")

    horizon = max(1, min(int(horizon), MAX_HORIZON))
    prices = series["close"]

    fitted = ts.models.fit_best(prices)
    model = fitted["model"]
    if model is None:
        return {**_skipped(series, "forecast"),
                "reason": "어떤 차수로도 모형을 적합하지 못했습니다."}

    band = ts.forecast.interval(model, horizon, alpha=alpha)
    direction = ts.forecast.direction_prob(model, horizon)
    scenario = ts.forecast.scenarios(model, horizon,
                                     prices=prices, values=series.get("value") or series.get("volume"))
    checked = ts.backtest.walk_forward(
        prices, order=(model["p"], model["d"], model["q"]),
        horizon=min(horizon, 10), folds=folds)

    # 예측 날짜 축 — 달력 평일로 잇는다 (휴장일이 섞일 수 있어 근사임을 밝힌다)
    future_dates = _future_dates(series["dates"], horizon)

    caveats = _caveats(series, model, band, checked, scenario)

    return {
        "ticker": series["ticker"], "name": series["name"],
        "status": "ok",
        "currency": series["currency"], "source": series["source"],
        "horizon_days": horizon,
        "last_date": series["dates"][-1] if series["dates"] else None,
        "last_close": prices[-1] if prices else None,
        "future_dates": future_dates,

        # ── 1단: 통계 ──
        "statistical": {
            "model": model.get("label"),
            "p": model["p"], "d": model["d"], "q": model["q"],
            "point": band["point"],
            "lower95": band["lower"],
            "upper95": band["upper"],
            "se": band["se"],
            "aic": model.get("aic"),
            "bic": model.get("bic"),
            "sigma2": model.get("sigma2"),
            "stable": model.get("stable"),
            "method": model.get("method", "ols"),
            "order_reason": fitted["order"].get("reason"),
            "ljung_box": model.get("ljung_box"),
        },

        # ── 2단: 확률 ──
        "probability": {
            "up_prob": direction.get("up_prob"),
            "path": direction.get("path"),
            "backtest": {
                "folds": checked.get("folds"),
                "points": checked.get("points"),
                "hit_rate": checked.get("hit_rate"),
                "rmse": checked.get("rmse"),
                "mae": checked.get("mae"),
                "mape": checked.get("mape"),
                "vs_random_walk": checked.get("vs_random_walk"),
                "beats_random_walk": checked.get("beats_random_walk"),
                "verdict": checked.get("verdict"),
                "random_walk_rmse": (checked.get("random_walk") or {}).get("rmse"),
                "available": checked.get("available", False),
                "reason": checked.get("reason"),
            },
        },

        # ── 3단: 시나리오 ──
        "scenarios": scenario.get("scenarios", []),
        "scenario_meta": {
            "rule": scenario.get("rule"),
            "levels": scenario.get("levels"),
            "available": scenario.get("available", False),
            "reason": scenario.get("reason"),
        },

        "caveats": caveats,
        "quality": series["quality"],
        "quality_verdict": series["verdict"],
        "fetched_at": _now_kst(),
    }


def _future_dates(dates: Sequence[str], horizon: int) -> List[str]:
    """예측 구간의 날짜 축. 마지막 거래일부터 **평일**을 세어 나간다.

    공휴일은 반영하지 않는다 — 미래 휴장일 목록이 없기 때문이다. 축이 며칠 밀릴 수 있고,
    그 사실은 `caveats` 에 적는다. 없는 달력을 지어내지는 않는다.
    """
    if not dates:
        return []
    try:
        cursor = datetime.strptime(dates[-1], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return []

    out: List[str] = []
    while len(out) < horizon:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
    return out


def _caveats(series: Dict, model: Dict, band: Dict,
             checked: Dict, scenario: Dict) -> List[str]:
    """명세서 §4.4 의 `caveats` — 이 예측을 읽을 때 알아야 할 것들.

    **좋은 소식만 담지 않는다.** 확률보행을 못 이겼으면 그 문장이 맨 앞에 온다.
    """
    out: List[str] = []
    quality = series.get("quality") or {}

    # 1) 백테스트 결과 — 가장 중요한 한 줄이므로 맨 앞
    if checked.get("available") and not checked.get("beats_random_walk"):
        out.append(checked.get("verdict") or "확률보행을 이기지 못했습니다.")
    elif checked.get("available"):
        out.append(checked.get("verdict"))
    else:
        out.append(f"백테스트를 하지 못했습니다 — {checked.get('reason')}")

    # 2) 차분 여부
    if model.get("d"):
        out.append(f"ADF 검정 결과 원계열이 비정상이라 {model['d']}차 차분 후 모델링했습니다.")

    # 3) 잔차 진단
    lb = model.get("ljung_box") or {}
    if lb.get("white_noise") is False:
        out.append(f"잔차에 자기상관이 남아 있습니다 (Ljung-Box p={lb.get('p_value'):.4f}) — "
                   "모형이 못 걷어낸 구조가 있습니다.")

    # 4) 이상치 (명세서 §3.3)
    outliers = (quality.get("outliers") or {}).get("count") or 0
    if outliers:
        out.append(f"표본에 이상치로 표시된 날이 {outliers}건 포함돼 있습니다 "
                   "(제거하지 않았습니다 — 대부분 실제 사건이기 때문입니다).")

    # 5) 전처리 판정
    if series.get("verdict") == "usable-with-caveat":
        out.append(f"전처리 판정이 `usable-with-caveat` 입니다 — {series.get('summary')}")

    # 6) 캘린더 근사 (변경 노트 N16)
    if (quality.get("calendar_source") or "") == "weekday-approx":
        out.append("거래일 캘린더 없이 근사(주말 제외)로 결측을 셌습니다 — 짧은 결측을 놓쳤을 수 있습니다.")

    # 7) 구간·시나리오의 가정
    if band.get("limitation"):
        out.append(band["limitation"])
    if scenario.get("limitation"):
        out.append(scenario["limitation"])
    if model.get("limitation"):
        out.append(model["limitation"])

    out.append("예측 날짜 축은 평일 기준이라 공휴일이 반영되지 않았습니다 (며칠 밀릴 수 있습니다).")
    return [line for line in out if line]
