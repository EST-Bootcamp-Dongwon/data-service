"""목업 시장 데이터 생성 (서비스 계층)

원래 화면(static/index.html)의 JS 가 만들던 목업 데이터를 서버로 옮긴 모듈이다.
FastAPI 에 의존하지 않는 순수 함수 모음이라 단독으로 실행·테스트할 수 있다.

모든 난수는 **고정 시드**를 쓰므로 호출할 때마다 같은 값이 나온다.
(서버를 재시작하면 값이 달라지는 `/users` 의 `age` 와 대비되는 부분)

⚠️ 전부 목업이며 실제 시세·재무 데이터가 아니다.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# 한국 시간 기준으로 거래일을 계산한다
KST = timezone(timedelta(hours=9))


def today_kst() -> date:
    return datetime.now(KST).date()


def _round_half_up(value: float) -> int:
    """파이썬 round() 는 은행가 반올림이라 0.5 가 짝수로 붙는다.

    JS 의 Math.round 와 같게 맞추기 위해 항상 위로 올린다.
    """
    return math.floor(value + 0.5)


# ==================================================
# 1. 종목 마스터
# ==================================================
# price      : 최근 종가로 쓰이는 기준가 (대략적인 값, 실제 시세 아님)
# volatility : 일간 변동성(표준편차)
# drift      : 일간 추세 (양수면 우상향)
# base_volume: 기준 거래량
STOCKS: List[Dict] = [
    {"code": "005930", "name": "삼성전자", "market": "KOSPI",
     "price": 71200, "volatility": 0.016, "drift": 0.0006, "base_volume": 14_000_000},
    {"code": "000660", "name": "SK하이닉스", "market": "KOSPI",
     "price": 248000, "volatility": 0.024, "drift": 0.0014, "base_volume": 3_600_000},
    {"code": "005380", "name": "현대차", "market": "KOSPI",
     "price": 254000, "volatility": 0.017, "drift": 0.0005, "base_volume": 1_100_000},
    {"code": "035420", "name": "NAVER", "market": "KOSPI",
     "price": 168500, "volatility": 0.021, "drift": -0.0002, "base_volume": 900_000},
    {"code": "035720", "name": "카카오", "market": "KOSPI",
     "price": 41300, "volatility": 0.024, "drift": -0.0006, "base_volume": 2_400_000},
    {"code": "247540", "name": "에코프로비엠", "market": "KOSDAQ",
     "price": 158900, "volatility": 0.034, "drift": -0.0009, "base_volume": 1_800_000},
]

STOCK_BY_CODE: Dict[str, Dict] = {s["code"]: s for s in STOCKS}

DEFAULT_CANDLE_COUNT = 500  # 약 2년치 거래일
DEFAULT_MA_PERIODS = (5, 20, 60)


def list_stocks() -> List[Dict]:
    """차트에서 고를 수 있는 종목 목록"""
    return [
        {"code": s["code"], "name": s["name"], "market": s["market"], "price": s["price"]}
        for s in STOCKS
    ]


# ==================================================
# 2. 일봉 시세 (OHLCV)
# ==================================================
def tick_size(price: int, market: str) -> int:
    """KRX 호가단위 (2023년 개정 기준) — 가격대별 최소 주문 단위"""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if market == "KOSDAQ":
        return 100  # 코스닥은 5만원 이상 전부 100원
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def round_tick(price: float, market: str) -> int:
    """가격을 해당 가격대의 호가단위에 맞춰 반올림한다."""
    unit = tick_size(int(price), market)
    return max(unit, _round_half_up(price / unit) * unit)


def trading_days(count: int, end: Optional[date] = None) -> List[date]:
    """end(기본: 오늘 KST)부터 거꾸로 주말을 건너뛰며 거래일을 모은다.

    공휴일은 반영하지 않으므로 실제 KRX 영업일과는 다르다.
    """
    day = end or today_kst()
    days: List[date] = []
    while len(days) < count:
        if day.weekday() < 5:  # 월(0) ~ 금(4)
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def generate_candles(code: str, count: int = DEFAULT_CANDLE_COUNT) -> List[Dict]:
    """기하 브라운 운동에 가까운 랜덤워크로 일봉을 만든다.

    종목코드를 난수 시드로 쓰기 때문에 같은 종목은 항상 같은 시계열이 나온다.
    """
    stock = STOCK_BY_CODE[code]
    market = stock["market"]
    rng = random.Random(int(code))

    raw: List[Dict] = []
    price = float(stock["price"])

    for day in trading_days(count):
        open_ = price
        close = open_ * (1 + stock["drift"] + rng.gauss(0, 1) * stock["volatility"])
        high = max(open_, close) * (1 + abs(rng.gauss(0, 1)) * stock["volatility"] * 0.6)
        low = min(open_, close) * (1 - abs(rng.gauss(0, 1)) * stock["volatility"] * 0.6)

        # 변동이 큰 날에 거래량이 늘어나도록 등락폭을 섞는다
        move = abs(close / open_ - 1)
        volume = _round_half_up(
            stock["base_volume"] * (0.5 + abs(rng.gauss(0, 1)) * 0.45 + move * 22)
        )

        raw.append({"date": day, "open": open_, "high": high, "low": low,
                    "close": close, "volume": volume})
        price = close

    # 마지막 종가가 기준가(= 최근 종가)와 정확히 일치하도록 전체를 비례 보정한다
    factor = stock["price"] / raw[-1]["close"]

    candles: List[Dict] = []
    for row in raw:
        open_ = round_tick(row["open"] * factor, market)
        close = round_tick(row["close"] * factor, market)
        # 호가단위로 반올림하면 "고가 < 종가" 같은 모순이 생길 수 있어 다시 맞춘다
        high = max(round_tick(row["high"] * factor, market), open_, close)
        low = min(round_tick(row["low"] * factor, market), open_, close)
        candles.append({
            "date": row["date"].isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(1_000, row["volume"]),
        })
    return candles


def moving_average(candles: List[Dict], window: int) -> List[Optional[int]]:
    """단순이동평균(SMA). 앞쪽 window-1 개는 계산할 수 없어 None 으로 채운다.

    전체 구간으로 계산해 두면 화면에서 구간을 잘라 봐도 값이 정확하다.
    """
    values: List[Optional[int]] = []
    total = 0
    for i, candle in enumerate(candles):
        total += candle["close"]
        if i >= window:
            total -= candles[i - window]["close"]
        values.append(_round_half_up(total / window) if i >= window - 1 else None)
    return values


# 같은 요청이 반복되면 재계산하지 않는다. 거래일이 바뀌면(자정 이후) 자동으로 새 키가 된다.
_candle_cache: Dict[Tuple[str, int, date], List[Dict]] = {}


def get_ohlcv(code: str, count: int = DEFAULT_CANDLE_COUNT,
              ma_periods: Tuple[int, ...] = DEFAULT_MA_PERIODS) -> Dict:
    """일봉 + 이동평균을 함께 돌려준다. 화면은 받은 값을 그대로 그리기만 한다."""
    stock = STOCK_BY_CODE[code]

    key = (code, count, today_kst())
    if key not in _candle_cache:
        _candle_cache[key] = generate_candles(code, count)
    candles = _candle_cache[key]

    return {
        "code": stock["code"],
        "name": stock["name"],
        "market": stock["market"],
        "currency": "KRW",
        "count": len(candles),
        "candles": candles,
        "moving_averages": [
            {"period": p, "values": moving_average(candles, p)} for p in ma_periods
        ],
    }


# ==================================================
# 3. 종목 스크리닝 깔때기 (Funnel)
# ==================================================
# 단계별로 조건을 걸어 후보를 좁혀 나가는 과정. 난수가 아닌 고정 시나리오다.
SCREENING_STEPS: List[Dict] = [
    {"name": "전체 상장 종목", "criteria": "KOSPI + KOSDAQ", "count": 2612},
    {"name": "관리종목·거래정지 제외", "criteria": "감사의견 적정 · 정상 거래", "count": 2491},
    {"name": "시가총액 1,000억 이상", "criteria": "유동성 확보", "count": 1338},
    {"name": "PER 5~15배", "criteria": "이익 대비 저평가", "count": 512},
    {"name": "PBR 1.0배 이하", "criteria": "자산 대비 저평가", "count": 264},
    {"name": "ROE 10% 이상", "criteria": "자본 수익성", "count": 118},
    {"name": "부채비율 100% 이하", "criteria": "재무 안정성", "count": 63},
    {"name": "최종 포트폴리오 편입", "criteria": "종합점수 상위 20", "count": 20},
]


def screening_funnel() -> Dict:
    """스크리닝 단계별 잔존 종목 수와 통과율"""
    total = SCREENING_STEPS[0]["count"]
    steps: List[Dict] = []

    for i, step in enumerate(SCREENING_STEPS):
        prev_count = SCREENING_STEPS[i - 1]["count"] if i else step["count"]
        steps.append({
            "step": i + 1,
            "name": step["name"],
            "criteria": step["criteria"],
            "count": step["count"],
            "dropped": prev_count - step["count"],                  # 이 단계에서 탈락한 종목 수
            "ratio": round(step["count"] / total * 100, 2),         # 전체 대비 잔존 비율(%)
            "pass_rate": round(step["count"] / prev_count * 100, 2),  # 직전 단계 대비 통과율(%)
        })

    return {
        "as_of": today_kst().isoformat(),
        "universe": "KOSPI + KOSDAQ",
        "total": total,
        "selected": SCREENING_STEPS[-1]["count"],
        "steps": steps,
    }


# ==================================================
# 4. 효율적 투자선 (몬테카를로 포트폴리오 시뮬레이션)
# ==================================================
# expected_return / volatility 는 연율 기준 목업 값이다.
FRONTIER_ASSETS: List[Dict] = [
    {"code": "005930", "name": "삼성전자", "expected_return": 0.112, "volatility": 0.254},
    {"code": "000660", "name": "SK하이닉스", "expected_return": 0.186, "volatility": 0.381},
    {"code": "005380", "name": "현대차", "expected_return": 0.094, "volatility": 0.272},
    {"code": "035420", "name": "NAVER", "expected_return": 0.071, "volatility": 0.333},
    {"code": "148070", "name": "KOSEF 국고채10년", "expected_return": 0.034, "volatility": 0.061},
]

# 자산 간 상관계수 (대칭 행렬).
# 채권은 주식과 상관이 낮거나 음수라 분산투자 효과가 나오도록 잡았다.
CORRELATION: List[List[float]] = [
    [1.00, 0.62, 0.41, 0.45, -0.12],
    [0.62, 1.00, 0.35, 0.40, -0.15],
    [0.41, 0.35, 1.00, 0.31, -0.05],
    [0.45, 0.40, 0.31, 1.00, -0.08],
    [-0.12, -0.15, -0.05, -0.08, 1.00],
]

RISK_FREE_RATE = 0.032  # 무위험수익률 (국고채 3년 수준)
FRONTIER_SEED = 20260730
DEFAULT_FRONTIER_SAMPLES = 1200


def _covariance_matrix() -> List[List[float]]:
    """상관계수 × 각 자산 변동성 → 공분산 행렬"""
    vols = [a["volatility"] for a in FRONTIER_ASSETS]
    n = len(vols)
    return [[CORRELATION[i][j] * vols[i] * vols[j] for j in range(n)] for i in range(n)]


def _random_weights(rng: random.Random, n: int) -> List[float]:
    """비중 합이 1인 무작위 벡터.

    지수분포 난수를 합으로 나누면 단체(simplex) 위에 고르게 퍼진다.
    (Dirichlet(1,...,1) 과 같은 분포. 균등난수를 그냥 정규화하면 가운데로 쏠린다.)
    """
    draws = [rng.expovariate(1.0) for _ in range(n)]
    total = sum(draws)
    return [d / total for d in draws]


_frontier_cache: Dict[int, Dict] = {}


def monte_carlo_frontier(samples: int = DEFAULT_FRONTIER_SAMPLES) -> Dict:
    """무작위 비중 포트폴리오를 뿌려 위험-수익 평면과 효율적 투자선을 만든다.

    - 기대수익률 : w · μ
    - 변동성     : sqrt(wᵀ Σ w)
    - 샤프지수   : (기대수익률 - 무위험수익률) / 변동성

    효율적 투자선은 수익률 구간별 최소 변동성 지점을 이어 상단 경계로 근사한다.
    (2차 계획법을 쓰지 않는 대신 표본이 충분히 많아야 매끄럽게 나온다.)
    """
    if samples in _frontier_cache:
        return _frontier_cache[samples]

    rng = random.Random(FRONTIER_SEED)
    mu = [a["expected_return"] for a in FRONTIER_ASSETS]
    cov = _covariance_matrix()
    n = len(mu)

    portfolios: List[Dict] = []
    for _ in range(samples):
        weights = _random_weights(rng, n)
        ret = sum(weights[i] * mu[i] for i in range(n))
        variance = sum(
            weights[i] * weights[j] * cov[i][j] for i in range(n) for j in range(n)
        )
        vol = math.sqrt(max(variance, 1e-12))
        portfolios.append({
            "return": round(ret, 5),
            "volatility": round(vol, 5),
            "sharpe": round((ret - RISK_FREE_RATE) / vol, 4),
            "weights": [round(w * 100, 1) for w in weights],  # 퍼센트
        })

    # --- 효율적 투자선 근사 ---
    # 1) 수익률을 40구간으로 나눠 구간별 최소 변동성 포트폴리오를 뽑는다
    lo = min(p["return"] for p in portfolios)
    hi = max(p["return"] for p in portfolios)
    span = (hi - lo) or 1e-9
    buckets: Dict[int, Dict] = {}
    for p in portfolios:
        idx = min(39, int((p["return"] - lo) / span * 40))
        if idx not in buckets or p["volatility"] < buckets[idx]["volatility"]:
            buckets[idx] = p

    ordered = [buckets[k] for k in sorted(buckets)]  # 수익률 오름차순

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
        "assets": FRONTIER_ASSETS,
        "portfolios": portfolios,
        "frontier": frontier,
        "max_sharpe": max(portfolios, key=lambda p: p["sharpe"]),
        "min_variance": min(portfolios, key=lambda p: p["volatility"]),
    }
    _frontier_cache[samples] = result
    return result


# ==================================================
# 5. 팩터 점수 (방사형 차트)
# ==================================================
FACTORS = ["가치", "성장", "수익성", "안정성", "모멘텀", "배당"]
FACTOR_MAX_SCORE = 100

# 종목별 팩터 점수(0~100). 난수가 아니라 종목 성격에 맞춰 손으로 잡은 값이다.
FACTOR_SCORES: Dict[str, List[int]] = {
    "005930": [72, 55, 68, 88, 61, 64],  # 삼성전자   — 안정성 높음
    "000660": [48, 86, 79, 62, 91, 31],  # SK하이닉스 — 성장·모멘텀 강함
    "005380": [84, 47, 58, 76, 54, 78],  # 현대차     — 가치·배당 강함
    "035420": [57, 72, 63, 69, 43, 22],  # NAVER
    "035720": [41, 64, 38, 45, 29, 12],  # 카카오     — 전반적으로 부진
    "247540": [22, 93, 44, 34, 68, 8],   # 에코프로비엠 — 성장 극단, 안정성 낮음
}

DEFAULT_RADAR_CODES = ("005930", "000660", "005380")


def factor_radar(codes: List[str]) -> Dict:
    """선택한 종목들의 팩터 점수. 방사형 차트에서 겹쳐 비교한다."""
    stocks: List[Dict] = []
    for code in codes:
        stock = STOCK_BY_CODE[code]
        scores = FACTOR_SCORES[code]
        stocks.append({
            "code": code,
            "name": stock["name"],
            "market": stock["market"],
            "scores": scores,
            "total": round(sum(scores) / len(scores), 1),  # 종합점수(단순평균)
        })

    return {"factors": FACTORS, "max_score": FACTOR_MAX_SCORE, "stocks": stocks}


def has_factor_scores(code: str) -> bool:
    return code in FACTOR_SCORES
