"""시장 상세 차트 조립 (서비스 계층) — `/market` 화면이 쓴다

대시보드 카드는 "지금 어떤가" 를 한눈에 보여 준다. 그런데 카드만으로는
**기간을 바꿔 볼 수 없고, 카드가 작아 모양을 읽기 어렵다.** 그래서 카드를 눌러 들어오는
상세 화면을 따로 두고, 이 모듈이 그 화면에 필요한 값을 만든다.

네 가지 보기 (소스가 각각 다르다)
--------------------------------
| 보기 | 무엇을 보나 | 소스 |
|---|---|---|
| `series`  | 지수·종목의 캔들 + 거래량 + 이동평균 | yfinance |
| `overlay` | 주가 위에 금리·환율·물가를 겹쳐 보기 | FRED · ECOS |
| `breadth` | 국내 시장의 **폭** — 몇 종목이 올랐나 | krx_cache.db |
| `compare` | 여러 종목을 100 기준으로 지수화해 비교 | yfinance |

왜 네 개를 따로 두는가
--------------------
같은 "시장" 을 물어도 답하는 방법이 다르기 때문이다.
지수 하나만 보면 **대형주 몇 개에 가려 시장 전체가 안 보인다.** 그래서 `breadth` 가 필요하다.
단위가 다른 값(주가 원 · 금리 %)을 한 축에 그리면 거짓말이 되므로 `overlay` 는
**정규화해서** 보낸다. 종목끼리 비교할 때도 절대가격은 뜻이 없어 `compare` 가 지수화한다.

지켜야 할 표시 규칙 (U3 결정)
---------------------------
- **Y축 두 개(이중축) 금지.** 단위가 다르면 100 기준으로 지수화한다 → `overlay`·`compare` 가 그렇게 한다
- 색은 순위가 아니라 **항목**을 따라간다 → 계열 순서를 서버가 고정해 내려보낸다
- 상승은 빨강 `--up`, 하락은 파랑 `--down` (국내 증시 관행)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.clients import ecos_data, fred_data
from app.core.trading_calendar import to_iso
from app.repositories import krx_bundle, krx_store, tmp_cache

KST = timezone(timedelta(hours=9))

# 이동평균 기간. 20=한 달 · 60=한 분기 · 120=반년 (국내 차트의 관행)
MA_WINDOWS = (20, 60, 120)

# 화면 위쪽 선택 상자에 띄울 대상. 순서가 그대로 화면 순서가 된다.
PRESETS: Sequence[Dict] = (
    {"key": "^KS11", "label": "코스피", "group": "국내 지수", "currency": "KRW"},
    {"key": "^KQ11", "label": "코스닥", "group": "국내 지수", "currency": "KRW"},
    {"key": "^IXIC", "label": "나스닥 종합", "group": "해외 지수", "currency": "USD"},
    {"key": "^GSPC", "label": "S&P 500", "group": "해외 지수", "currency": "USD"},
    {"key": "^DJI", "label": "다우존스", "group": "해외 지수", "currency": "USD"},
    {"key": "^N225", "label": "닛케이 225", "group": "해외 지수", "currency": "JPY"},
    {"key": "KRW=X", "label": "원/달러", "group": "환율", "currency": "KRW"},
    {"key": "GC=F", "label": "금 선물", "group": "원자재", "currency": "USD"},
    {"key": "CL=F", "label": "WTI 원유", "group": "원자재", "currency": "USD"},
    {"key": "BTC-USD", "label": "비트코인", "group": "가상자산", "currency": "USD"},
)

# 겹쳐 그릴 거시지표. FRED(미국)와 ECOS(국내)를 한 목록으로 합친다.
# 화면은 어느 기관에서 왔는지 몰라도 되고, 이 모듈이 알아서 갈라 부른다.
OVERLAY_SOURCES: Sequence[Dict] = (
    {"id": "fred:DGS10", "label": "미 국채 10년", "unit": "%", "group": "금리", "source": "FRED"},
    {"id": "fred:DGS2", "label": "미 국채 2년", "unit": "%", "group": "금리", "source": "FRED"},
    {"id": "fred:T10Y2Y", "label": "장단기 금리차", "unit": "%p", "group": "금리", "source": "FRED"},
    {"id": "fred:VIXCLS", "label": "VIX 변동성", "unit": "p", "group": "위험", "source": "FRED"},
    {"id": "fred:DTWEXBGS", "label": "달러 지수", "unit": "지수", "group": "환율", "source": "FRED"},
    {"id": "fred:T10YIE", "label": "미 기대 인플레이션", "unit": "%", "group": "물가", "source": "FRED"},
    {"id": "ecos:base_rate", "label": "한은 기준금리", "unit": "%", "group": "금리", "source": "ECOS"},
    {"id": "ecos:ktb3y", "label": "국고채 3년", "unit": "%", "group": "금리", "source": "ECOS"},
    {"id": "ecos:ktb10y", "label": "국고채 10년", "unit": "%", "group": "금리", "source": "ECOS"},
    {"id": "ecos:usdkrw", "label": "원/달러 (한은)", "unit": "원", "group": "환율", "source": "ECOS"},
    {"id": "ecos:cpi", "label": "소비자물가지수", "unit": "지수", "group": "물가", "source": "ECOS"},
    {"id": "ecos:leading", "label": "경기선행지수", "unit": "지수", "group": "경기", "source": "ECOS"},
)

OVERLAY_BY_ID: Dict[str, Dict] = {item["id"]: item for item in OVERLAY_SOURCES}

# 한 번에 겹칠 수 있는 지표 수. 계열이 많아지면 색 8슬롯을 넘고 읽기도 어려워진다.
MAX_OVERLAYS = 4
# 비교 화면에 올릴 수 있는 종목 수 (같은 이유)
MAX_COMPARE = 6

# 한 계열이 내려보내는 점 상한 (ADR-DS-0004).
#
# 계열 **수**는 위 둘로 막았지만 계열 **길이**는 열려 있었다. `period=max` 를 고르면
# 코스피는 7,000점이 넘고, 비교 화면은 그것이 6배가 된다. Vercel 은 응답 본문을
# 4.5MB 로 제한하고 그 전에 `maxDuration: 60` 을 먼저 친다.
#
# `yf_data.MAX_HISTORY_ROWS` 와 같은 값으로 맞춘다 — 두 경로가 같은 야후 일봉을 보는데
# 상한이 다르면 `/api/yf/history` 와 `/api/chart/series` 의 봉 수가 어긋난다.
MAX_POINTS = 3000


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def presets() -> List[Dict]:
    """선택 상자에 띄울 대상 목록."""
    return [dict(item) for item in PRESETS]


def overlays() -> List[Dict]:
    """겹쳐 그릴 수 있는 거시지표 목록."""
    return [dict(item) for item in OVERLAY_SOURCES]


# ==================================================
# 1. 지수·종목 일봉 (캔들 + 이동평균 + 거래량)
# ==================================================
def _moving_average(values: Sequence[float], window: int) -> List[Optional[float]]:
    """단순이동평균. 앞쪽 `window-1` 개는 계산할 수 없어 `None` 이다.

    누적합으로 한 번에 계산한다 (구간마다 다시 더하면 O(n·w) 가 된다).
    """
    if len(values) < window:
        return [None] * len(values)

    array = np.asarray(values, dtype="float64")
    cumulative = np.cumsum(np.insert(array, 0, 0.0))
    averages = (cumulative[window:] - cumulative[:-window]) / window
    return [None] * (window - 1) + [round(float(v), 4) for v in averages]


def series(ticker: str, period: str = "1y", with_ma: bool = True,
           max_points: int = MAX_POINTS) -> Dict:
    """캔들 차트 한 장 분량 — OHLCV + 이동평균 + 구간 통계.

    `/tmp` 캐시를 쓴다. 같은 지수를 여러 사람이 볼 때 야후를 그만큼 두드리지 않기 위함이다.
    캐시가 없어도 결과는 같다 (속도만 담당한다 — 명세서 §1.3).

    `max_points` 를 넘으면 **최근 구간만** 남기고 `truncated` 로 알린다 (ADR-DS-0004).
    캐시에는 자르기 전 전체를 담으므로 상한을 바꿔 다시 물어도 야후를 다시 부르지 않는다.
    """
    from app.clients import yf_data  # yfinance 미설치 환경을 위해 늦게 부른다

    symbol = yf_data.normalize_ticker(ticker)
    cache_key = f"series_{symbol}_{period}_{int(with_ma)}"
    hit = tmp_cache.read("market", cache_key, tmp_cache.TTL_MARKET)
    if hit:
        hit["source"] = "tmp-cache"
        return _limit_series(hit, max_points)

    # 상한을 **여기서** 걸지 않고 전 구간을 받는다 (`max_rows=0`).
    # 이동평균을 먼저 계산해 두어야 잘라 낸 구간의 왼쪽 끝이 채워진다 — 자른 뒤에 계산하면
    # 120일선의 앞 119일이 통째로 null 이 되어 화면이 잘못 그려진다 (`_limit_series` 참고).
    history = yf_data.fetch_history(symbol, period, max_rows=0)
    rows = history["rows"]
    closes = [r["close"] for r in rows]

    payload = {
        "ticker": symbol,
        "label": next((p["label"] for p in PRESETS if p["key"] == symbol), symbol),
        "period": period,
        "period_label": history["period_label"],
        "interval": history.get("interval", "1d"),
        "dates": [r["date"] for r in rows],
        # ApexCharts 캔들은 [시가, 고가, 저가, 종가] 순서를 받는다
        "candles": [[r["open"], r["high"], r["low"], r["close"]] for r in rows],
        "closes": closes,
        "volumes": [r.get("volume") or 0 for r in rows],
        "count": len(rows),
        "stats": _series_stats(closes),
        "moving_averages": (
            # 구간이 짧으면 120일선이 통째로 비어 차트에 빈 계열이 생긴다. 그럴 땐 아예 뺀다.
            [{"window": w, "label": f"{w}일선", "values": _moving_average(closes, w)}
             for w in MA_WINDOWS if len(closes) >= w]
            if with_ma else []
        ),
        "fetched_at": _now_kst(),
        "source": "live",
    }
    tmp_cache.write("market", cache_key, payload)     # 캐시에는 자르기 전 전체를 담는다
    return _limit_series(payload, max_points)


def _limit_series(payload: Dict, max_points: int) -> Dict:
    """계열 배열을 **최근** `max_points` 개로 자르고 잘린 사실을 밝힌다 (ADR-DS-0004).

    두 가지를 다르게 다룬다.

    - **이동평균은 자르기 전 전체에서 계산한 값을 그대로 잘라 온다.** 시계열을 잘라
      놓고 계산하면 구간 첫날부터 119일치가 null 이라 120일선의 왼쪽이 비어 보인다.
      "시계열을 페이지로 자르면 안 되는" 이유가 바로 이것이라 순서를 지킨다.
    - **구간 통계는 남긴 구간 기준으로 다시 낸다.** 화면이 그리는 구간과 옆에 적히는
      수익률·MDD 가 어긋나면 그게 더 나쁘다.

    ⚠️ `payload` 는 `/tmp` 캐시에 담긴 전체본이다. 제자리에서 고치면 다음 요청이
    이미 잘린 것을 받는다. 반드시 복사해서 돌려준다.
    """
    total = len(payload.get("dates") or [])
    if max_points <= 0 or total <= max_points:
        # 자르지 않았다는 사실도 명시한다. 필드가 있다 없다 하면 화면이 분기해야 한다.
        return {**payload, "truncated": False, "total_count": total}

    closes = payload["closes"][-max_points:]
    return {
        **payload,
        "dates": payload["dates"][-max_points:],
        "candles": payload["candles"][-max_points:],
        "closes": closes,
        "volumes": payload["volumes"][-max_points:],
        "count": len(closes),
        "stats": _series_stats(closes),
        "moving_averages": [
            {**ma, "values": ma["values"][-max_points:]}
            for ma in (payload.get("moving_averages") or [])
        ],
        "truncated": True,
        "total_count": total,
    }


def _series_stats(closes: Sequence[float]) -> Dict:
    """구간 요약 — 화면 상단 타일에 쓴다."""
    if not closes:
        return {}
    first, last = closes[0], closes[-1]
    array = np.asarray(closes, dtype="float64")

    # 연율화 변동성 — 일간 로그수익률 표준편차 × √252 (거래일 수)
    volatility = None
    if len(closes) > 2:
        with np.errstate(divide="ignore", invalid="ignore"):
            returns = np.diff(np.log(np.where(array > 0, array, np.nan)))
        finite = returns[np.isfinite(returns)]
        if finite.size > 1:
            volatility = round(float(np.std(finite, ddof=1)) * (252 ** 0.5) * 100, 2)

    # 최대 낙폭(MDD) — 고점 대비 가장 크게 떨어진 폭
    running_max = np.maximum.accumulate(array)
    drawdown = (array - running_max) / np.where(running_max > 0, running_max, np.nan)

    return {
        "first": round(float(first), 4),
        "last": round(float(last), 4),
        "change": round(float(last - first), 4),
        "change_rate": round(float((last - first) / first * 100), 2) if first else None,
        "min": round(float(array.min()), 4),
        "max": round(float(array.max()), 4),
        "volatility_annual": volatility,
        "mdd": round(float(np.nanmin(drawdown)) * 100, 2) if drawdown.size else None,
    }


# ==================================================
# 2. 거시지표 겹쳐 보기
# ==================================================
def overlay(ticker: str, period: str = "1y",
            overlay_ids: Sequence[str] = (),
            max_points: int = MAX_POINTS) -> Dict:
    """주가 위에 거시지표를 겹쳐 그릴 값을 만든다.

    **두 축을 쓰지 않는다** (U3 결정). 주가는 원, 금리는 % 라 단위가 달라서
    한 축에 그대로 올리면 눈금이 거짓말을 한다. 그래서 모든 계열을
    **구간 첫날 = 100** 으로 지수화해 보낸다. 원래 값은 `raw` 에 함께 실어
    툴팁이 실제 수치를 보여 줄 수 있게 한다.

    거시지표는 주가와 달력이 다르다(월별 발표·미국 공휴일). 그래서 각 클라이언트의
    `align_to_dates` 로 **주가 날짜에 맞춰 계단식 보간**한다 — 그 시점에 시장이
    알고 있던 값이 직전 발표치이므로 앞의 값을 끌어오는 방향이 맞다.

    상한은 주가에만 걸면 된다. 거시지표는 **주가 날짜에 맞춰** 채우므로 주가가 잘리면
    같이 짧아진다 (FRED 조회도 잘린 구간의 시작·끝으로만 부른다).
    """
    base = series(ticker, period, with_ma=False, max_points=max_points)
    dates = base["dates"]
    wanted = [i for i in overlay_ids if i in OVERLAY_BY_ID][:MAX_OVERLAYS]

    lines: List[Dict] = []
    errors: List[Dict] = []

    # 주가를 첫 계열로 둔다 (색 1번 슬롯 고정 — 정렬이 바뀌어도 색이 안 흔들린다)
    lines.append({
        "id": f"price:{base['ticker']}",
        "label": base["label"],
        "unit": "지수(첫날=100)",
        "raw_unit": "",
        "source": "yfinance",
        "indexed": _index_to_100(base["closes"]),
        "raw": base["closes"],
        "correlation": None,          # 자기 자신과의 상관은 뜻이 없다
    })

    for overlay_id in wanted:
        spec = OVERLAY_BY_ID[overlay_id]
        kind, _, key = overlay_id.partition(":")
        try:
            if kind == "fred":
                # 상한을 끈다(`max_points=0`). 조회 구간이 이미 주가 구간으로 묶여 있어
                # 길어야 주가 점 수 남짓이고, 기본 상한(2,000)에 걸리면 **오래된 쪽이 잘려**
                # 구간 앞부분이 통째로 빈 선이 된다 — 자른 티도 안 나는 조용한 결손이다.
                fetched = fred_data.fetch_series(key, dates[0], dates[-1], max_points=0)
                aligned = fred_data.align_to_dates(fetched, dates)
            else:
                # ECOS 는 연 단위로 받는다. 구간 길이에서 햇수를 거꾸로 구한다.
                years = max(1, (len(dates) // 250) + 1)
                fetched = ecos_data.fetch_series(key, years=years)
                aligned = ecos_data.align_to_dates(fetched, dates)
        except Exception as error:
            # 한 지표가 실패해도 나머지는 그려야 한다 (대시보드와 같은 원칙 — 명세서 §6.4)
            errors.append({"id": overlay_id, "label": spec["label"], "error": str(error)})
            continue

        # 수준끼리 비교하면 둘 다 우상향한다는 이유로 상관이 높게 나온다(허위 상관).
        # 그래서 변화율 상관을 쓴다 — `fred_data.correlation` 이 그렇게 계산한다.
        correlation = fred_data.correlation(base["closes"], aligned)
        lines.append({
            "id": overlay_id,
            "label": spec["label"],
            "unit": "지수(첫날=100)",
            "raw_unit": fetched.get("unit") or spec["unit"],
            "source": spec["source"],
            "indexed": _index_to_100(aligned),
            "raw": aligned,
            "correlation": correlation,
            # 상관을 못 낸 이유를 함께 보낸다. 표에서 그냥 빠지면
            # "왜 이 지표만 없지?" 가 되고, 값이 0 인 것과 구분도 안 된다.
            "correlation_note": None if correlation is not None
                                else _why_no_correlation(aligned),
        })

    return {
        "ticker": base["ticker"],
        "label": base["label"],
        "period": period,
        "period_label": base["period_label"],
        "dates": dates,
        "lines": lines,
        "errors": errors,
        "note": "단위가 서로 달라 구간 첫날을 100 으로 맞춰 그립니다. "
                "상관계수는 수준이 아니라 **일간 변화율** 기준입니다 (허위 상관 방지)."
                + _truncation_note(base),
        "truncated": base.get("truncated", False),
        "total_count": base.get("total_count", len(dates)),
        "fetched_at": _now_kst(),
    }


def _truncation_note(payload: Dict) -> str:
    """잘렸을 때만 사람이 읽을 문장을 돌려준다. 안 잘렸으면 빈 문자열.

    `truncated` 플래그만으로는 화면이 분기 코드를 새로 써야 한다. `/market` 화면은
    `note` 를 그대로 찍고 있으므로 여기에 실어 보내면 고지가 바로 눈에 보인다.
    """
    if not payload.get("truncated"):
        return ""
    kept = len(payload.get("dates") or [])
    total = payload.get("total_count", kept)
    return (f" · 구간이 길어 **최근 {kept:,}개 지점**만 그립니다 "
            f"(전체 {total:,}개). 더 보려면 기간을 나눠서 조회하세요.")


def _why_no_correlation(values: Sequence[Optional[float]]) -> str:
    """상관계수를 못 낸 이유를 사람 말로 돌려준다.

    가장 흔한 경우가 **구간 내내 값이 그대로**인 것이다. 기준금리처럼 몇 달에 한 번
    바뀌는 지표를 1년 구간에서 보면 변화가 0회라 분산이 없고, 상관은 정의되지 않는다.
    "데이터가 없다" 와 "변하지 않아 잴 수 없다" 는 전혀 다른 말이라 갈라서 알린다.
    """
    present = [v for v in values if v is not None]
    if len(present) < 3:
        return "겹치는 구간의 값이 3개 미만이라 상관을 잴 수 없습니다."
    if len(set(present)) == 1:
        return (f"이 구간 내내 {present[0]} 로 값이 바뀌지 않아 상관이 정의되지 않습니다. "
                "기간을 늘리면 변동 구간이 들어올 수 있습니다.")
    return "변동이 거의 없어 상관을 잴 수 없습니다."


def _index_to_100(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """첫 유효값을 100 으로 맞춰 지수화한다. 단위가 다른 계열을 한 축에 놓기 위함이다."""
    base = next((v for v in values if v is not None and v != 0), None)
    if base is None:
        return [None] * len(values)
    return [round(v / base * 100, 3) if v is not None else None for v in values]


# ==================================================
# 3. 국내 시장의 폭 (breadth)
# ==================================================
def breadth(days: int = 120) -> Dict:
    """날짜별로 **몇 종목이 올랐는지** 센다. 지수가 못 보여 주는 것을 본다.

    지수는 시가총액 가중이라 삼성전자 하나가 크게 움직이면 그쪽으로 끌려간다.
    "지수는 올랐는데 내 종목은 다 빠졌다" 가 그래서 생긴다.
    상승 종목 수는 시장 전체를 한 표씩 세므로 그 착시가 없다.

    데이터를 얻는 길이 둘이고, **더 긴 구간을 덮는 쪽**을 쓴다.

    | | 어디서 | 덮는 구간 |
    |---|---|---|
    | 직접 세기 | 원본 캐시(`krx_cache.db`)의 전종목 × N일을 훑어 센다 | 로컬 282거래일 |
    | 사전집계 | `krx_derived.json` 의 날짜별 집계를 그대로 읽는다 | 어디서나 282거래일 |

    사전집계는 **날짜별 숫자 다섯 개**뿐이라 종목별 원본이 필요 없다. 그래서 13KB 로
    배포 번들에 실린다. 예전에는 배포본에서 이 차트가 통째로 비어 있었다.
    """
    days = max(20, min(int(days), 300))
    cache_key = f"breadth_{days}"
    hit = tmp_cache.read("market", cache_key, tmp_cache.TTL_MARKET)
    if hit:
        hit["source"] = "tmp-cache"
        return hit

    try:
        rows = krx_store.window(days=days, columns=("bas_dd", "change_rate", "value", "close"))
    except Exception:
        rows = []                              # 원본을 못 읽으면 사전집계로 넘어간다

    # 원본에서 직접 센다 (날짜별로 모은다)
    buckets: Dict[str, Dict] = {}
    for row in rows:
        day = buckets.setdefault(row["bas_dd"], {"up": 0, "down": 0, "flat": 0, "value": 0})
        rate = row.get("change_rate") or 0
        day["up" if rate > 0 else "down" if rate < 0 else "flat"] += 1
        day["value"] += row.get("value") or 0

    counted = [
        {"date": to_iso(d), "up": b["up"], "down": b["down"], "flat": b["flat"],
         "value": b["value"], "total": b["up"] + b["down"] + b["flat"]}
        for d, b in sorted(buckets.items())
    ]
    derived_rows = krx_bundle.breadth_series(days)      # 사전집계 — tier `derived`

    # 더 많은 거래일을 덮는 쪽을 쓴다. 배포본에서는 축약본 DB(150일)보다
    # 사전집계(282일)가 길고, 로컬에서는 원본이 길거나 같다.
    series_rows = counted if len(counted) >= len(derived_rows) else derived_rows
    # 직접 셌더라도 **무엇을 세었는지**를 그대로 밝힌다.
    # 축약본에서 센 것을 `db` 라고 부르면 화면이 원본을 본 것으로 오해한다.
    # tier 어휘는 ADR-DS-0009 — 사전집계는 `derived` 다.
    origin = krx_store.tier() if series_rows is counted else "derived"

    if not series_rows:
        return {
            "available": False,
            "reason": "KRX 시세 자료가 없습니다. 로컬에서 `python3 scripts/fetch_krx.py` 로 채우거나 "
                      "`python3 scripts/build_krx_bundle.py` 로 배포용 집계를 만들 수 있습니다.",
            "days": days, "dates": [], "rows": [], "fetched_at": _now_kst(),
        }

    dates = [r["date"] for r in series_rows]
    up = [r["up"] for r in series_rows]
    down = [r["down"] for r in series_rows]
    flat = [r["flat"] for r in series_rows]
    values = [r["value"] for r in series_rows]

    # 상승 비율 — 등락한 종목 중 오른 비율 (보합은 분모에서 뺀다)
    ratios = [round(u / (u + d) * 100, 2) if (u + d) else 50.0 for u, d in zip(up, down, strict=False)]

    # 누적 등락선(A/D Line) — (상승−하락)을 계속 더한 값.
    # 지수와 방향이 갈리면 시장의 힘이 소수 종목에 쏠렸다는 뜻이다.
    advance_decline: List[int] = []
    running = 0
    for u, d in zip(up, down, strict=False):
        running += u - d
        advance_decline.append(running)

    payload = {
        "available": True,
        "days": len(dates),
        "dates": dates,
        "up": up,
        "down": down,
        "flat": flat,
        "ratio": ratios,
        "advance_decline": advance_decline,
        "value": values,
        "latest": {
            "date": dates[-1],
            "up": up[-1], "down": down[-1], "flat": flat[-1],
            "ratio": ratios[-1],
            "total": series_rows[-1]["total"],
        },
        "note": "상승 종목 비율은 지수와 다르게 움직일 수 있습니다. "
                "지수는 시가총액 가중이라 대형주에 끌리지만, 여기서는 종목마다 한 표씩 셉니다."
                + {"db": "",
                   "bundle": " · 배포용 축약본(최근 150거래일)에서 셌습니다.",
                   "derived": " · 사전집계(`krx_derived.json`)를 읽었습니다 — 배포본에는 원본 캐시가 없습니다.",
                   "live": ""}.get(origin, ""),
        "fetched_at": _now_kst(),
        "source": f"{krx_store.PROVIDER}-{origin}",
    }
    tmp_cache.write("market", cache_key, payload)
    return payload


# ==================================================
# 4. 종목 비교 (100 기준 지수화)
# ==================================================
def compare(tickers: Sequence[str], period: str = "1y",
            max_points: int = MAX_POINTS) -> Dict:
    """여러 종목을 **구간 첫날 = 100** 으로 맞춰 한 차트에 올린다.

    절대가격으로 비교하면 뜻이 없다 — 7만원짜리와 300달러짜리를 나란히 그리면
    비싼 쪽이 위에 있을 뿐 누가 더 올랐는지는 안 보인다. 지수화하면 **상승률**이 보인다.

    날짜 축이 종목마다 다를 수 있다(휴장일이 다른 시장을 섞을 때). 그래서
    **가장 긴 계열의 날짜를 축으로 삼고** 나머지는 그 축에 맞춰 앞의 값을 끌어온다.
    """
    wanted = [t for t in tickers if (t or "").strip()][:MAX_COMPARE]
    if not wanted:
        return {"period": period, "dates": [], "lines": [], "errors": [],
                "fetched_at": _now_kst()}

    fetched: List[Dict] = []
    errors: List[Dict] = []
    for ticker in wanted:
        try:
            fetched.append(series(ticker, period, with_ma=False, max_points=max_points))
        except Exception as error:
            errors.append({"ticker": ticker, "error": str(error)})

    if not fetched:
        return {"period": period, "dates": [], "lines": [], "errors": errors,
                "fetched_at": _now_kst()}

    # 가장 점이 많은 계열을 날짜 축으로 삼는다
    axis = max(fetched, key=lambda f: len(f["dates"]))
    dates = axis["dates"]

    lines: List[Dict] = []
    for item in fetched:
        aligned = _align_closes(item["dates"], item["closes"], dates)
        lines.append({
            "ticker": item["ticker"],
            "label": item["label"],
            "indexed": _index_to_100(aligned),
            "raw": aligned,
            "stats": item["stats"],
        })

    return {
        "period": period,
        "period_label": axis["period_label"],
        "dates": dates,
        "lines": lines,
        "errors": errors,
        "note": "구간 첫날을 100 으로 맞춰 그립니다. 선의 높이는 가격이 아니라 **누적 상승률**입니다."
                + _truncation_note(axis),
        # 종목마다 상장일이 달라 하나만 잘릴 수 있다. **하나라도 잘리면** 잘린 것으로 알린다.
        "truncated": any(f.get("truncated") for f in fetched),
        "total_count": max((f.get("total_count", 0) for f in fetched), default=len(dates)),
        "fetched_at": _now_kst(),
    }


def _align_closes(source_dates: Sequence[str], values: Sequence[float],
                  target_dates: Sequence[str]) -> List[Optional[float]]:
    """한 계열을 다른 날짜 축에 맞춘다. 없는 날은 직전 값을 이어 쓴다(계단식).

    앞의 값을 끌어오는 이유는 `align_to_dates` 와 같다 — 뒤의 값을 쓰면
    아직 일어나지 않은 일을 미리 아는 셈이 된다.
    """
    aligned: List[Optional[float]] = []
    cursor = 0
    last: Optional[float] = None
    for target in target_dates:
        while cursor < len(source_dates) and source_dates[cursor] <= target:
            last = values[cursor]
            cursor += 1
        aligned.append(last)
    return aligned
