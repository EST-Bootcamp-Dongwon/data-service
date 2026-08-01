"""시장 분석 서비스 (서비스 계층)

`krx_store` 에 쌓인 **실제 KRX 일별 시세**로 화면의 차트 4종을 계산한다.
FastAPI 에 의존하지 않는 순수 함수 모음이라 단독으로 실행·테스트할 수 있다.

    KRX OpenAPI  →  clients/krx_data(호출·정규화)  →  repositories/krx_store(SQLite)
                 →  services/market_data(분석)  →  routers(컨트롤러)  →  화면

예전에는 이 파일이 난수로 목업 시세를 만들었지만, KRX API 승인 후
**전부 실데이터 기반 계산**으로 바꿨다. 난수는 효율적 투자선의 비중 추첨에만 남아 있다.

KRX OpenAPI 로 알 수 없는 것
---------------------------
KRX 일별매매정보에는 **재무제표가 없다.** PER·PBR·ROE·부채비율을 쓸 수 없다는 뜻이다.
그래서 스크리닝과 팩터를 **가격·거래량으로 계산 가능한 지표**로 다시 정의했다.
무위험수익률만 상수로 두었고(`RISK_FREE_RATE`), 나머지는 전부 DB 에서 계산한다.
"""

from __future__ import annotations

import math                                              # sqrt 등 수학 함수
import random                                            # 포트폴리오 비중 추첨
import statistics                                        # 평균·표준편차
from typing import Dict, List, Optional, Sequence, Tuple

from app.repositories import krx_store as store          # SQLite 캐시 (데이터 공급원)
from app.core.trading_calendar import round_half_up, to_iso, today_kst

# 분석에 쓰는 기본 관측 기간(거래일). 60일 ≒ 3개월.
DEFAULT_WINDOW = 60
DEFAULT_CANDLE_COUNT = 120        # 차트가 기본으로 요청하는 일봉 개수
DEFAULT_MA_PERIODS = (5, 20, 60)  # 기본 이동평균 기간
TRADING_DAYS_PER_YEAR = 252       # 연율 환산에 쓰는 연간 거래일 수 (업계 관행)


# ==================================================
# 0. 종목 지표 — 스크리닝·팩터·프론티어가 공유하는 계산 결과
# ==================================================
# 같은 거래일에 대해서는 결과가 바뀌지 않으므로 캐싱한다.
# 키는 (기준일, 관측기간) 이라 새 거래일이 들어오면 자동으로 새로 계산된다.
_metrics_cache: Dict[Tuple[str, int], List[Dict]] = {}


def _stdev(values: Sequence[float]) -> float:
    """표준편차. 표본이 2개 미만이면 계산할 수 없어 0 을 돌려준다."""
    return statistics.stdev(values) if len(values) > 1 else 0.0


def stock_metrics(window: int = DEFAULT_WINDOW) -> List[Dict]:
    """전 종목의 최근 `window` 거래일 지표를 한 번에 계산한다.

    반환 필드
        code · name · market · close · market_cap
        days        : 관측된 거래일 수 (상장 직후 종목은 짧다)
        avg_value   : 평균 거래대금 — 유동성 지표
        momentum    : 기간 수익률(%) — 첫날 종가 대비 마지막 종가
        volatility  : 연율 변동성(%) — 일간 수익률의 표준편차 × √252
        turnover    : 평균 거래량 / 상장주식수(%) — 손바뀜 정도
        above_ma20  : 종가가 20일 이동평균 위에 있으면 True — 추세 판단
    """
    latest = store.latest_date()
    if not latest:
        return []                        # DB 가 비어 있으면 계산할 것이 없다

    key = (latest, window)
    if key in _metrics_cache:
        return _metrics_cache[key]

    # 1) 최근 window 거래일치 원자료를 한 번에 읽는다 (종목별 질의를 2,700번 하지 않기 위함)
    rows = store.window(window, columns=("code", "bas_dd", "close", "value", "volume"))

    # 2) 종목별로 묶는다.
    #    SQL 이 날짜 오름차순으로 주므로, 앞에서부터 담기만 해도 각 묶음이 시간순이 된다.
    grouped: Dict[str, List[Dict]] = {}
    for row in rows:
        grouped.setdefault(row["code"], []).append(row)

    # 3) 최근 거래일 스냅샷에서 종목명·시장·시가총액 같은 "그날의 속성"을 가져온다
    snapshot = {item["code"]: item for item in store.snapshot(latest)}

    metrics: List[Dict] = []
    for code, series in grouped.items():
        head = snapshot.get(code)
        if not head:
            continue                     # 최근 거래일에 없는 종목(상장폐지 등)은 제외

        closes = [r["close"] for r in series if r["close"]]
        if len(closes) < 2:
            continue                     # 수익률을 계산할 수 없다

        # 일간 수익률 = 오늘 종가 / 어제 종가 - 1
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]

        listed = head.get("listed_shares") or 0
        avg_volume = statistics.fmean(r["volume"] or 0 for r in series)

        metrics.append({
            "code": code,
            "name": head.get("name"),
            "market": head.get("market"),
            "close": head.get("close"),
            "market_cap": head.get("market_cap") or 0,
            "change_rate": head.get("change_rate"),
            "days": len(series),
            "avg_value": round(statistics.fmean(r["value"] or 0 for r in series)),
            "momentum": round((closes[-1] / closes[0] - 1) * 100, 2),
            # 일간 표준편차를 연율로 환산한다 (√252 를 곱하는 것이 관행)
            "volatility": round(_stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2),
            "turnover": round(avg_volume / listed * 100, 3) if listed else 0.0,
            # 20일 이동평균 위에 있으면 단기 상승 추세로 본다
            "above_ma20": bool(len(closes) >= 20 and closes[-1] > statistics.fmean(closes[-20:])),
        })

    _metrics_cache[key] = metrics
    return metrics


def _percentile_ranks(values: Sequence[float], higher_is_better: bool = True) -> List[float]:
    """값들을 0~100 백분위로 바꾼다. (가장 낮은 값이 0, 가장 높은 값이 100)

    단위가 제각각인 지표(원 단위 거래대금, % 단위 변동성)를 한 그래프에 그리려면
    같은 척도로 맞춰야 한다. 순위를 백분위로 바꾸면 이상치에도 흔들리지 않는다.
    """
    n = len(values)
    if n < 2:
        return [50.0] * n

    # 값에 원래 위치(인덱스)를 붙여 정렬하면, 정렬 후에도 원래 자리를 찾아갈 수 있다
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    for rank, idx in enumerate(order):
        pct = rank / (n - 1) * 100
        ranks[idx] = pct if higher_is_better else 100 - pct
    return ranks


# ==================================================
# 1. 종목 목록
# ==================================================
def list_stocks(limit: int = 30, market: Optional[str] = None, q: str = "") -> List[Dict]:
    """차트에서 고를 수 있는 종목 목록. 거래대금이 큰 순서로 돌려준다."""
    rows = store.universe(market=market)          # 최근 거래일 기준, 거래대금 내림차순
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows
                if needle in (r.get("name") or "").lower() or needle in (r.get("code") or "")]

    return [
        {"code": r["code"], "name": r["name"], "market": r["market"],
         "price": r["close"], "change_rate": r.get("change_rate"),
         "value": r.get("value"), "market_cap": r.get("market_cap")}
        for r in rows[:limit]
    ]


# ==================================================
# 2. 일봉 시세 (OHLCV) + 이동평균
# ==================================================
def moving_average(candles: List[Dict], window: int) -> List[Optional[int]]:
    """단순이동평균(SMA). 앞쪽 window-1 개는 계산할 수 없어 None 으로 채운다.

    합계를 굴리면서(sliding window) 계산하므로 기간이 길어도 한 번만 훑으면 된다.
    """
    values: List[Optional[int]] = []
    total = 0
    for i, candle in enumerate(candles):
        total += candle["close"] or 0            # 새 값을 더하고
        if i >= window:
            total -= candles[i - window]["close"] or 0   # 창 밖으로 나간 값을 뺀다
        values.append(round_half_up(total / window) if i >= window - 1 else None)
    return values


def get_ohlcv(code: str, count: int = DEFAULT_CANDLE_COUNT,
              ma_periods: Tuple[int, ...] = DEFAULT_MA_PERIODS) -> Dict:
    """일봉 + 이동평균을 함께 돌려준다. 화면은 받은 값을 그대로 그리기만 한다."""
    rows = store.series(code, days=count)        # DB 에서 날짜 오름차순으로 읽어온다
    if not rows:
        return {"code": code, "name": None, "market": None, "currency": "KRW",
                "count": 0, "candles": [], "moving_averages": []}

    head = rows[-1]                              # 가장 최근 행에서 종목 정보를 가져온다
    candles = [
        {"date": r["date"], "open": r["open"], "high": r["high"],
         "low": r["low"], "close": r["close"], "volume": r["volume"] or 0,
         "value": r.get("value") or 0,
         "change": r.get("change"), "change_rate": r.get("change_rate")}
        for r in rows
    ]

    return {
        "code": code,
        "name": head.get("name"),
        "market": head.get("market"),
        "currency": "KRW",
        "count": len(candles),
        "first_date": candles[0]["date"],
        "last_date": candles[-1]["date"],
        "candles": candles,
        "moving_averages": [
            {"period": p, "values": moving_average(candles, p)} for p in ma_periods
        ],
    }


# ==================================================
# 3. 종목 스크리닝 깔때기 (Funnel)
# ==================================================
# KRX 일별매매정보로 계산할 수 있는 조건만 사용한다.
# (PER·PBR·ROE·부채비율은 재무제표가 있어야 해서 이 API 로는 만들 수 없다.)
SCREENING_TARGET = 20        # 최종적으로 남길 종목 수


def screening_funnel(window: int = DEFAULT_WINDOW) -> Dict:
    """조건을 단계별로 걸어 후보를 좁혀 나가는 과정을 실데이터로 계산한다."""
    metrics = stock_metrics(window)
    latest = store.latest_date()
    if not metrics:
        return {"as_of": None, "universe": "KOSPI + KOSDAQ", "total": 0,
                "selected": 0, "steps": [], "picks": [],
                "note": "캐시가 비어 있습니다. python3 scripts/fetch_krx.py 를 먼저 실행하세요."}

    # 각 단계는 (이름, 조건 설명, 남길지 판단하는 함수) 로 정의한다.
    # 앞 단계를 통과한 종목만 다음 단계로 넘어간다.
    total = len(metrics)

    # 중앙값은 단계마다 "그 시점에 남은 종목" 기준으로 다시 구해야 의미가 있다
    def median_of(rows: List[Dict], field: str) -> float:
        values = [r[field] for r in rows if r.get(field) is not None]
        return statistics.median(values) if values else 0.0

    steps_def = [
        ("전체 상장 종목", "KOSPI + KOSDAQ 전 종목", lambda rows: rows),
        ("거래 정상 종목", "관측기간 내 거래량 > 0",
         lambda rows: [r for r in rows if r["avg_value"] > 0]),
        ("관측기간 충족", f"최근 {window}거래일 중 80% 이상 거래",
         lambda rows: [r for r in rows if r["days"] >= window * 0.8]),
        ("시가총액 1,000억 이상", "유동성·안정성 확보",
         lambda rows: [r for r in rows if (r["market_cap"] or 0) >= 100_000_000_000]),
        ("평균 거래대금 10억 이상", "매매가 실제로 가능한 수준",
         lambda rows: [r for r in rows if r["avg_value"] >= 1_000_000_000]),
        ("변동성 하위 50%", "가격이 과도하게 출렁이지 않을 것",
         lambda rows: [r for r in rows if r["volatility"] <= median_of(rows, "volatility")]),
        ("모멘텀 상위 50%", f"{window}거래일 수익률이 중앙값 이상",
         lambda rows: [r for r in rows if r["momentum"] >= median_of(rows, "momentum")]),
        ("20일선 위", "단기 추세가 살아 있을 것",
         lambda rows: [r for r in rows if r["above_ma20"]]),
    ]

    steps: List[Dict] = []
    rows = metrics
    prev_count = total

    for i, (name, criteria, rule) in enumerate(steps_def):
        rows = rule(rows)
        steps.append({
            "step": i + 1,
            "name": name,
            "criteria": criteria,
            "count": len(rows),
            "dropped": prev_count - len(rows),                       # 이 단계에서 탈락한 수
            "ratio": round(len(rows) / total * 100, 2),              # 전체 대비 잔존 비율(%)
            "pass_rate": round(len(rows) / prev_count * 100, 2) if prev_count else 0.0,
        })
        prev_count = len(rows)

    # 마지막 단계: 남은 종목을 종합점수로 줄 세워 상위 N 만 편입한다.
    # 점수 = 모멘텀 백분위 + 안정성 백분위(변동성 역순) + 유동성 백분위 의 평균
    if rows:
        mom = _percentile_ranks([r["momentum"] for r in rows], higher_is_better=True)
        vol = _percentile_ranks([r["volatility"] for r in rows], higher_is_better=False)
        liq = _percentile_ranks([r["avg_value"] for r in rows], higher_is_better=True)
        for idx, row in enumerate(rows):
            row["score"] = round((mom[idx] + vol[idx] + liq[idx]) / 3, 1)
        rows = sorted(rows, key=lambda r: r["score"], reverse=True)

    picks = rows[:SCREENING_TARGET]
    steps.append({
        "step": len(steps) + 1,
        "name": "최종 포트폴리오 편입",
        "criteria": f"종합점수(모멘텀·안정성·유동성) 상위 {SCREENING_TARGET}",
        "count": len(picks),
        "dropped": prev_count - len(picks),
        "ratio": round(len(picks) / total * 100, 2),
        "pass_rate": round(len(picks) / prev_count * 100, 2) if prev_count else 0.0,
    })

    return {
        "as_of": to_iso(latest) if latest else None,
        "universe": "KOSPI + KOSDAQ",
        "window": window,
        "total": total,
        "selected": len(picks),
        "steps": steps,
        # 최종 통과 종목도 함께 보내 화면에서 표로 보여줄 수 있게 한다
        "picks": [
            {"code": p["code"], "name": p["name"], "market": p["market"],
             "close": p["close"], "score": p.get("score"),
             "momentum": p["momentum"], "volatility": p["volatility"],
             "avg_value": p["avg_value"], "market_cap": p["market_cap"]}
            for p in picks
        ],
        "note": None,
    }


# ==================================================
# 4. 효율적 투자선 (몬테카를로 포트폴리오 시뮬레이션)
# ==================================================
RISK_FREE_RATE = 0.032            # 무위험수익률 (국고채 3년 수준) — KRX API 에 없어 상수로 둔다
FRONTIER_SEED = 20260731          # 비중 추첨 시드 고정 → 같은 조건이면 같은 그림이 나온다
DEFAULT_FRONTIER_SAMPLES = 1200
DEFAULT_FRONTIER_CODES = ("005930", "000660", "005380", "035420", "035720")
FRONTIER_WINDOW = 250             # 수익률·변동성은 1년치로 계산한다


def _random_weights(rng: random.Random, n: int) -> List[float]:
    """비중 합이 1인 무작위 벡터.

    지수분포 난수를 합으로 나누면 단체(simplex) 위에 고르게 퍼진다.
    (Dirichlet(1,...,1) 과 같은 분포. 균등난수를 그냥 정규화하면 가운데로 쏠린다.)
    """
    draws = [rng.expovariate(1.0) for _ in range(n)]
    total = sum(draws)
    return [d / total for d in draws]


def _annualized_stats(closes: Dict[str, List[int]], codes: Sequence[str]):
    """종가 시계열에서 연율 기대수익률 벡터(μ)와 공분산 행렬(Σ)을 계산한다.

    - 일간 수익률 = 오늘 종가 / 어제 종가 - 1
    - 연율 기대수익률 = 일간 평균 × 252
    - 연율 공분산     = 일간 공분산 × 252
    """
    returns = {
        code: [closes[code][i] / closes[code][i - 1] - 1 for i in range(1, len(closes[code]))]
        for code in codes
    }
    n_obs = len(next(iter(returns.values()))) if returns else 0

    mu = [statistics.fmean(returns[c]) * TRADING_DAYS_PER_YEAR for c in codes]

    means = {c: statistics.fmean(returns[c]) for c in codes}
    cov: List[List[float]] = []
    for a in codes:
        row = []
        for b in codes:
            # 표본 공분산 = Σ(xᵢ-x̄)(yᵢ-ȳ) / (n-1)
            s = sum((returns[a][i] - means[a]) * (returns[b][i] - means[b]) for i in range(n_obs))
            row.append(s / (n_obs - 1) * TRADING_DAYS_PER_YEAR if n_obs > 1 else 0.0)
        cov.append(row)

    return mu, cov, n_obs


_frontier_cache: Dict[Tuple, Dict] = {}


def monte_carlo_frontier(samples: int = DEFAULT_FRONTIER_SAMPLES,
                         codes: Sequence[str] = DEFAULT_FRONTIER_CODES,
                         window: int = FRONTIER_WINDOW) -> Dict:
    """무작위 비중 포트폴리오를 뿌려 위험-수익 평면과 효율적 투자선을 만든다.

    - 기대수익률 : w · μ
    - 변동성     : sqrt(wᵀ Σ w)
    - 샤프지수   : (기대수익률 - 무위험수익률) / 변동성

    μ 와 Σ 는 **실제 KRX 종가**에서 계산한다. 난수는 비중 조합을 뽑는 데만 쓴다.
    효율적 투자선은 수익률 구간별 최소 변동성 지점을 이어 상단 경계로 근사한다.
    """
    codes = tuple(codes)
    key = (codes, samples, window, store.latest_date())
    if key in _frontier_cache:
        return _frontier_cache[key]

    # 상관계수를 구하려면 모든 종목의 날짜가 정확히 같아야 한다 (공통 거래일만 사용)
    closes = store.closes_matrix(codes, days=window)
    usable = [c for c in codes if len(closes.get(c, [])) > 2]
    if len(usable) < 2:
        return {"risk_free_rate": RISK_FREE_RATE, "samples": 0, "assets": [],
                "portfolios": [], "frontier": [], "max_sharpe": None, "min_variance": None,
                "note": "종가 데이터가 부족합니다. python3 scripts/fetch_krx.py 로 캐시를 채우세요."}

    mu, cov, n_obs = _annualized_stats(closes, usable)
    names = {r["code"]: r for r in store.universe()}

    assets = [
        {"code": c,
         "name": (names.get(c) or {}).get("name") or c,
         "expected_return": round(mu[i], 4),
         "volatility": round(math.sqrt(max(cov[i][i], 0)), 4)}
        for i, c in enumerate(usable)
    ]

    rng = random.Random(FRONTIER_SEED)
    n = len(usable)
    portfolios: List[Dict] = []

    for _ in range(samples):
        weights = _random_weights(rng, n)
        ret = sum(weights[i] * mu[i] for i in range(n))
        # 포트폴리오 분산 = ΣΣ(wᵢ × wⱼ × σᵢⱼ) — 모든 자산 쌍을 더한다
        variance = sum(weights[i] * weights[j] * cov[i][j] for i in range(n) for j in range(n))
        vol = math.sqrt(max(variance, 1e-12))     # 부동소수 오차로 음수가 되는 것을 막는다
        portfolios.append({
            "return": round(ret, 5),
            "volatility": round(vol, 5),
            "sharpe": round((ret - RISK_FREE_RATE) / vol, 4),
            "weights": [round(w * 100, 1) for w in weights],   # 퍼센트
        })

    # --- 효율적 투자선 근사 ---
    # 1) 수익률을 40구간으로 나눠 구간별 최소 변동성 포트폴리오를 뽑는다
    lo = min(p["return"] for p in portfolios)
    hi = max(p["return"] for p in portfolios)
    span = (hi - lo) or 1e-9                      # 0 으로 나누는 것을 막는 방어 코드
    buckets: Dict[int, Dict] = {}
    for p in portfolios:
        idx = min(39, int((p["return"] - lo) / span * 40))
        if idx not in buckets or p["volatility"] < buckets[idx]["volatility"]:
            buckets[idx] = p

    ordered = [buckets[k] for k in sorted(buckets)]           # 수익률 오름차순

    # 2) 최소분산점보다 위쪽(수익률이 높은 쪽)만 남기면 효율적 구간이 된다
    gmv_idx = min(range(len(ordered)), key=lambda i: ordered[i]["volatility"])

    # 3) 변동성이 단조 증가하는 점만 남겨 경계를 매끄럽게 만든다
    frontier: List[Dict] = []
    peak_vol = 0.0
    for p in ordered[gmv_idx:]:
        if p["volatility"] >= peak_vol:
            peak_vol = p["volatility"]
            frontier.append({"volatility": p["volatility"], "return": p["return"]})

    result = {
        "risk_free_rate": RISK_FREE_RATE,
        "samples": samples,
        "window": window,
        "observations": n_obs,                    # 실제로 쓴 일간 수익률 개수
        "as_of": to_iso(store.latest_date() or ""),
        "assets": assets,
        "portfolios": portfolios,
        "frontier": frontier,
        "max_sharpe": max(portfolios, key=lambda p: p["sharpe"]),
        "min_variance": min(portfolios, key=lambda p: p["volatility"]),
        # 창을 요청한 만큼 못 채웠으면 그대로 밝힌다. 배포본은 축약본(150거래일)을 보므로
        # 250일을 물어도 149개 수익률로 계산된다 — 변동성 추정이 그만큼 거칠어진다.
        "note": (f"요청한 {window}거래일 중 {n_obs + 1}일치만 있어 일간수익률 {n_obs}개로 계산했습니다. "
                 f"자료 출처: {store.source()}. 관측이 짧으면 변동성·상관 추정이 거칠어집니다."
                 if n_obs + 1 < window * 0.9 else None),
    }
    _frontier_cache[key] = result
    return result


# ==================================================
# 5. 팩터 점수 (방사형 차트)
# ==================================================
# 예전에는 가치·성장·수익성 같은 재무 팩터를 손으로 넣어 두었지만,
# KRX 일별매매정보에는 재무제표가 없다. 그래서 **가격·거래량으로 계산되는 팩터**로 바꿨다.
# 점수는 전 종목 대비 백분위(0~100)라 "시장에서 몇 등쯤인가"로 읽으면 된다.
FACTORS = ["모멘텀", "안정성", "유동성", "규모", "추세", "회전율"]
FACTOR_MAX_SCORE = 100

# 각 축을 계산할 지표와 방향 (True = 값이 클수록 좋은 점수)
FACTOR_SPECS = [
    ("모멘텀", "momentum", True),        # 기간 수익률이 높을수록
    ("안정성", "volatility", False),     # 변동성이 낮을수록
    ("유동성", "avg_value", True),       # 평균 거래대금이 클수록
    ("규모", "market_cap", True),        # 시가총액이 클수록
    ("추세", "_trend", True),            # 20일선 위 + 최근 등락률 (아래에서 합성)
    ("회전율", "turnover", True),        # 손바뀜이 활발할수록
]

DEFAULT_RADAR_CODES = ("005930", "000660", "005380")


def factor_radar(codes: Sequence[str], window: int = DEFAULT_WINDOW) -> Dict:
    """선택한 종목들의 팩터 점수(0~100). 방사형 차트에서 겹쳐 비교한다.

    점수는 **전 종목 대비 백분위**다. 예를 들어 유동성 98 이면
    "거래대금이 전체 종목 중 상위 2%" 라는 뜻이다.
    """
    metrics = stock_metrics(window)
    if not metrics:
        return {"factors": FACTORS, "max_score": FACTOR_MAX_SCORE, "stocks": [],
                "note": "캐시가 비어 있습니다. python3 scripts/fetch_krx.py 를 먼저 실행하세요."}

    # 추세 축은 별도 지표가 없어 "20일선 위(50점) + 최근 등락률" 로 합성한다
    for row in metrics:
        row["_trend"] = (50 if row["above_ma20"] else 0) + (row.get("change_rate") or 0)

    # 전 종목을 한 번에 백분위로 바꿔 둔다 (선택한 종목만 계산하면 기준이 없다)
    ranks: Dict[str, List[float]] = {}
    for _label, field, higher in FACTOR_SPECS:
        ranks[field] = _percentile_ranks([r.get(field) or 0 for r in metrics], higher)

    index = {row["code"]: i for i, row in enumerate(metrics)}

    stocks: List[Dict] = []
    missing: List[str] = []
    for code in codes:
        i = index.get(code)
        if i is None:
            missing.append(code)
            continue
        row = metrics[i]
        scores = [round(ranks[field][i]) for _label, field, _h in FACTOR_SPECS]
        stocks.append({
            "code": code,
            "name": row["name"],
            "market": row["market"],
            "scores": scores,
            "total": round(sum(scores) / len(scores), 1),      # 종합점수(단순평균)
            # 백분위만 보면 원래 값이 안 보이므로 원자료도 함께 보낸다
            "raw": {"momentum": row["momentum"], "volatility": row["volatility"],
                    "avg_value": row["avg_value"], "market_cap": row["market_cap"],
                    "turnover": row["turnover"], "above_ma20": row["above_ma20"]},
        })

    return {
        "factors": FACTORS,
        "max_score": FACTOR_MAX_SCORE,
        "window": window,
        "as_of": to_iso(store.latest_date() or ""),
        "universe_size": len(metrics),
        "stocks": stocks,
        "note": f"백분위 기준 종목 수 {len(metrics):,}개" + (
            f" · 데이터 없음: {', '.join(missing)}" if missing else ""),
    }


def has_metrics(code: str, window: int = DEFAULT_WINDOW) -> bool:
    """해당 종목의 지표를 계산할 수 있는지 확인한다 (라우터의 404 판정용)."""
    return any(row["code"] == code for row in stock_metrics(window))
