"""08강 09·10 기술적분석을 판정 함수로 옮긴 것 (명세 §5.6)

강의의 식과 기준선을 **그대로** 상수로 둔다. 내가 정한 숫자가 하나도 없어야
리포트가 "왜 이 기준인가" 를 강의로 되짚을 수 있다.

    RSI      = 100 - 100/(1+RS) · RS = 14일 평균 상승폭 / 14일 평균 하락폭   (09.md 383행)
    MACD     = EMA12 - EMA26 · Signal = MACD 의 9일 EMA                     (09.md 415행)
    볼린저    = MA20 ± 2σ                                                     (09.md 449행)
    이동평균  5·20·60·120·240 — 단기/중기/장기                                (09.md 205행)
    골든크로스 단기선이 장기선을 위로 돌파 · 데드크로스는 반대                (09.md 238행)
    5신호     RSI<30 +2 · MACD>Signal +2 · 볼린저 하단 +1 · 거래량 +1 · 정배열 +1
              5점 이상 BUY · 3~4 HOLD · 2 이하 SELL                          (10.md Tab7)

**numpy·pandas 를 쓰지 않는다.** 이 파일은 순수 파이썬만 쓴다 —
리스트 몇 백 개짜리 계산에 배열 라이브러리를 부르면 서버리스 콜드스타트만 무거워진다.

지키는 것
--------
· 관측이 모자라면 **0 이나 50 같은 그럴듯한 값을 만들지 않는다.** `available=False` 와 사유를 낸다.
· 신호는 '점수' 가 아니라 '점수 + 왜' 로 낸다.
· 5신호 총점은 **매매 신호가 아니라 학습용 요약**임을 판정 문구에 박아 둔다.
"""
from __future__ import annotations

from statistics import fmean, pstdev
from typing import Dict, List, Optional, Sequence

from .financials import GRADE_GOOD, GRADE_SERIOUS, GRADE_UNKNOWN, GRADE_WARNING, verdict

# 09.md 205행 이동평균 기간
MA_WINDOWS = (5, 20, 60, 120, 240)

RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

BOLLINGER_WINDOW = 20
BOLLINGER_SIGMA = 2.0

# U4 선행결정(M3)과 같은 창 — 시나리오 경계도 60거래일 고·저였다. 규약을 갈라 두지 않는다.
LEVEL_WINDOW = 60


def _clean(values: Sequence[Optional[float]]) -> List[float]:
    return [float(v) for v in values if isinstance(v, (int, float))]


def sma(values: Sequence[Optional[float]], window: int) -> List[Optional[float]]:
    """단순이동평균. 창이 안 차는 앞부분은 **None 으로 둔다** (0 으로 채우지 않는다)."""
    out: List[Optional[float]] = []
    bucket: List[float] = []
    for value in values:
        if isinstance(value, (int, float)):
            bucket.append(float(value))
        elif bucket:
            bucket.append(bucket[-1])            # 중간 결측은 직전 값으로 이어 붙인다
        else:
            out.append(None)                     # 맨 앞 결측은 0 으로 채우지 않고 비워 둔다
            continue
        out.append(fmean(bucket[-window:]) if len(bucket) >= window else None)
    return out


def ema(values: Sequence[Optional[float]], window: int) -> List[Optional[float]]:
    """지수이동평균. 첫 값은 단순평균으로 씨를 뿌리고 α=2/(n+1) 로 이어 간다."""
    clean = _clean(values)
    if len(clean) < window:
        return [None] * len(values)
    alpha = 2 / (window + 1)
    out: List[Optional[float]] = [None] * (window - 1)
    current = fmean(clean[:window])
    out.append(current)
    for value in clean[window:]:
        current = value * alpha + current * (1 - alpha)
        out.append(current)
    return out


# ─────────────────────────────────────────────────────────────
# 1. 이동평균 · 배열 · 크로스
# ─────────────────────────────────────────────────────────────
def moving_averages(closes: Sequence[float], windows: Sequence[int] = MA_WINDOWS) -> Dict:
    """이동평균 묶음과 정배열/역배열 판정 (09.md)."""
    clean = _clean(closes)
    if len(clean) < 2:
        return {"available": False, "reason": "종가가 둘 미만이라 이동평균을 못 낸다"}

    lines = {}
    for window in windows:
        line = sma(clean, window)
        lines[window] = {"window": window, "values": line, "last": line[-1]}

    usable = [w for w in windows if lines[w]["last"] is not None]
    order = ""
    if len(usable) >= 3:
        lasts = [lines[w]["last"] for w in usable]
        if all(lasts[i] > lasts[i + 1] for i in range(len(lasts) - 1)):
            order = "정배열"
        elif all(lasts[i] < lasts[i + 1] for i in range(len(lasts) - 1)):
            order = "역배열"
        else:
            order = "혼조"

    price = clean[-1]
    return {
        "available": True,
        "price": price,
        "lines": {str(w): lines[w]["last"] for w in windows},
        "series": {str(w): lines[w]["values"] for w in windows},
        "usable_windows": usable,
        "order": order,
        "above": {str(w): (price > lines[w]["last"]) for w in usable},
        "why": ("짧은 선이 위에 있는 정배열이다 — 상승 추세로 읽는다" if order == "정배열"
                else "긴 선이 위에 있는 역배열이다 — 하락 추세로 읽는다" if order == "역배열"
                else "선들이 얽혀 있다 — 방향이 정해지지 않았다"),
        "basis": "08강 09.md 이동평균선 구분표",
        "limitation": (f"관측 {len(clean)}일이라 {[w for w in windows if w not in usable]} 일선은 "
                       "만들지 못했다" if len(usable) < len(windows) else ""),
    }


def cross(short_line: Sequence[Optional[float]], long_line: Sequence[Optional[float]],
          lookback: int = 20) -> Dict:
    """골든크로스·데드크로스 (09.md 238행).

    "지금 위에 있다" 가 아니라 **최근에 뚫었는가**를 본다. 위치만 보면
    반년 전에 뚫고 계속 위에 있는 것도 '골든크로스' 가 되어 신호가 무의미해진다.
    """
    pairs = [(s, l) for s, l in zip(short_line, long_line)
             if isinstance(s, (int, float)) and isinstance(l, (int, float))]
    if len(pairs) < 2:
        return {"available": False, "reason": "두 이동평균이 겹치는 구간이 없다"}

    window = pairs[-(lookback + 1):]
    events = []
    for index in range(1, len(window)):
        before_short, before_long = window[index - 1]
        after_short, after_long = window[index]
        if before_short <= before_long and after_short > after_long:
            events.append({"kind": "골든크로스", "bars_ago": len(window) - 1 - index})
        elif before_short >= before_long and after_short < after_long:
            events.append({"kind": "데드크로스", "bars_ago": len(window) - 1 - index})

    latest = events[-1] if events else None
    above = pairs[-1][0] > pairs[-1][1]
    return {
        "available": True,
        "position": "단기선이 장기선 위" if above else "단기선이 장기선 아래",
        "event": latest,
        "events": events,
        "lookback": lookback,
        "why": (f"{latest['kind']} 가 {latest['bars_ago']}거래일 전에 났다" if latest
                else f"최근 {lookback}거래일 안에 교차가 없었다 — 추세가 이어지는 중이다"),
        "basis": "08강 09.md — 골든크로스는 상승 전환, 데드크로스는 하락 전환 신호",
        "limitation": "이동평균은 후행지표다. 교차가 확인될 때는 이미 움직인 뒤다.",
    }


# ─────────────────────────────────────────────────────────────
# 2. RSI (09.md 383행)
# ─────────────────────────────────────────────────────────────
def rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> Dict:
    """RSI = 100 - 100/(1+RS).

    첫 구간은 단순평균, 이후는 Wilder 평활(이전평균×(n-1)+오늘)/n 을 쓴다.
    강의 식이 "14일 평균" 이라고만 하지만, 원저자(Wilder)의 정의가 평활이고
    HTS 들이 그 값을 그린다. **어느 쪽을 썼는지 밝히는 것이 중요하다.**
    """
    clean = _clean(closes)
    # 값 하나를 만들려면 변화량 `period` 개로 씨를 뿌린 뒤 **한 번 더** 평활해야 한다.
    # 그래서 필요한 종가는 period+1 이 아니라 period+2 다 (실측 — 15개로는 결과가 비었다).
    if len(clean) < period + 2:
        return {"available": False,
                "reason": f"종가 {len(clean)}개로는 RSI({period})를 못 낸다 (최소 {period + 2}개)"}

    gains, losses = [], []
    for index in range(1, len(clean)):
        change = clean[index] - clean[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    series: List[Optional[float]] = [None] * period
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
        if avg_loss == 0:
            series.append(100.0)
        else:
            series.append(100 - 100 / (1 + avg_gain / avg_loss))
    series = [None] + series                                  # 종가 개수와 길이를 맞춘다

    value = series[-1]
    if value is None:
        return {"available": False, "reason": "RSI 계산 구간이 비었다"}
    if value >= RSI_OVERBOUGHT:
        grade, zone, why = GRADE_WARNING, "과매수", f"{RSI_OVERBOUGHT:.0f} 이상 — 조정 가능성을 본다"
    elif value <= RSI_OVERSOLD:
        grade, zone, why = GRADE_WARNING, "과매도", f"{RSI_OVERSOLD:.0f} 이하 — 반등 가능성을 본다"
    else:
        grade, zone, why = GRADE_GOOD, "중립", "30~70 구간 — RSI 단독 신호는 없다"
    return {
        "available": True,
        "value": round(value, 2),
        "series": series,
        "period": period,
        "zone": zone,
        "grade": grade,
        "why": why,
        "method": "Wilder 평활 (HTS 표시값과 같은 정의)",
        "basis": "08강 09.md — RSI = 100 - 100/(1+RS)",
        "limitation": ("강한 추세에서는 과매수·과매도가 오래 유지된다. "
                       "09.md 도 추세 강도를 함께 보라고 한다 — RSI 만으로 판단하지 않는다."),
    }


# ─────────────────────────────────────────────────────────────
# 3. MACD (09.md 415행)
# ─────────────────────────────────────────────────────────────
def macd(closes: Sequence[float], fast: int = MACD_FAST, slow: int = MACD_SLOW,
         signal_period: int = MACD_SIGNAL) -> Dict:
    """MACD · Signal · 히스토그램."""
    clean = _clean(closes)
    if len(clean) < slow + signal_period:
        return {"available": False,
                "reason": f"종가 {len(clean)}개로는 MACD({fast},{slow},{signal_period})를 못 낸다 "
                          f"(최소 {slow + signal_period}개)"}

    fast_line = ema(clean, fast)
    slow_line = ema(clean, slow)
    macd_line = [None if (f is None or s is None) else f - s
                 for f, s in zip(fast_line, slow_line)]
    body = [v for v in macd_line if v is not None]
    signal_body = ema(body, signal_period)
    signal_line: List[Optional[float]] = [None] * (len(macd_line) - len(signal_body)) + signal_body
    histogram = [None if (m is None or s is None) else m - s
                 for m, s in zip(macd_line, signal_line)]

    last_macd, last_signal, last_hist = macd_line[-1], signal_line[-1], histogram[-1]
    if last_macd is None or last_signal is None:
        return {"available": False, "reason": "MACD 마지막 값이 비었다"}

    # 히스토그램의 0선 교차 — 09.md 는 이것을 매수·매도 신호로 본다
    crossed = ""
    recent = [h for h in histogram[-6:] if h is not None]
    if len(recent) >= 2 and recent[-2] <= 0 < recent[-1]:
        crossed = "히스토그램이 0선을 상향 돌파했다 (음→양)"
    elif len(recent) >= 2 and recent[-2] >= 0 > recent[-1]:
        crossed = "히스토그램이 0선을 하향 돌파했다 (양→음)"

    above = last_macd > last_signal
    return {
        "available": True,
        "macd": round(last_macd, 4),
        "signal": round(last_signal, 4),
        "histogram": None if last_hist is None else round(last_hist, 4),
        "series": {"macd": macd_line, "signal": signal_line, "histogram": histogram},
        "params": {"fast": fast, "slow": slow, "signal": signal_period},
        "grade": GRADE_GOOD if above else GRADE_WARNING,
        "state": "MACD 가 Signal 위" if above else "MACD 가 Signal 아래",
        "why": crossed or ("상승 모멘텀이 유지되는 중이다" if above else "하락 모멘텀이 유지되는 중이다"),
        "basis": "08강 09.md — MACD = EMA12 - EMA26 · Signal = MACD 의 9일 EMA",
        "limitation": "추세추종 지표라 횡보장에서는 신호가 자주 뒤집힌다.",
    }


# ─────────────────────────────────────────────────────────────
# 4. 볼린저밴드 (09.md 449행)
# ─────────────────────────────────────────────────────────────
def bollinger(closes: Sequence[float], window: int = BOLLINGER_WINDOW,
              sigma: float = BOLLINGER_SIGMA) -> Dict:
    """중심선 MA20 · 상·하단 ±2σ · 밴드폭(수축 여부)."""
    clean = _clean(closes)
    if len(clean) < window:
        return {"available": False,
                "reason": f"종가 {len(clean)}개로는 볼린저({window})를 못 낸다"}

    middle: List[Optional[float]] = []
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    width: List[Optional[float]] = []
    for index in range(len(clean)):
        if index + 1 < window:
            middle.append(None); upper.append(None); lower.append(None); width.append(None)
            continue
        chunk = clean[index + 1 - window: index + 1]
        center = fmean(chunk)
        deviation = pstdev(chunk)                # 모표준편차 — HTS 관행과 같다
        middle.append(center)
        upper.append(center + sigma * deviation)
        lower.append(center - sigma * deviation)
        width.append((sigma * 2 * deviation / center * 100) if center else None)

    price = clean[-1]
    band_width = width[-1]
    history = [w for w in width if w is not None]
    squeeze = (band_width is not None and len(history) >= window
               and band_width <= sorted(history)[max(0, len(history) // 5)])

    if upper[-1] is not None and price >= upper[-1]:
        grade, zone, why = GRADE_WARNING, "상단 접촉", "상단을 터치했다 — 과열 여부를 함께 본다"
    elif lower[-1] is not None and price <= lower[-1]:
        grade, zone, why = GRADE_WARNING, "하단 접촉", "하단을 터치했다 — 반등 여부를 함께 본다"
    else:
        grade, zone, why = GRADE_GOOD, "밴드 안", "밴드 안에서 움직이고 있다"
    if squeeze:
        why += " · 밴드폭이 하위 20% 로 좁아졌다 — 곧 큰 움직임이 나올 수 있다"

    return {
        "available": True,
        "price": price,
        "middle": round(middle[-1], 2) if middle[-1] is not None else None,
        "upper": round(upper[-1], 2) if upper[-1] is not None else None,
        "lower": round(lower[-1], 2) if lower[-1] is not None else None,
        "width_pct": None if band_width is None else round(band_width, 2),
        "squeeze": squeeze,
        "series": {"middle": middle, "upper": upper, "lower": lower, "width": width},
        "zone": zone,
        "grade": grade,
        "why": why,
        "basis": "08강 09.md — 중심선 MA20 · 상·하단 ±2σ",
        "limitation": "밴드 접촉은 '지금 극단' 이라는 뜻이지 방향을 말해 주지 않는다.",
    }


# ─────────────────────────────────────────────────────────────
# 5. 지지·저항 (09.md 174행)
# ─────────────────────────────────────────────────────────────
def levels(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
           window: int = LEVEL_WINDOW) -> Dict:
    """최근 `window` 거래일의 고점·저점을 저항·지지로 본다.

    09.md 는 "한 가격이 아니라 여러 번 반응한 **가격대 범위**로 보라" 고 한다.
    그래서 고점·저점 하나만 주지 않고 상위·하위 구간 폭도 함께 낸다.
    """
    high_clean = _clean(highs)[-window:]
    low_clean = _clean(lows)[-window:]
    close_clean = _clean(closes)
    if len(high_clean) < 10 or len(low_clean) < 10 or not close_clean:
        return {"available": False,
                "reason": f"관측 {min(len(high_clean), len(low_clean))}일로는 지지·저항을 못 잡는다"}

    resistance = max(high_clean)
    support = min(low_clean)
    price = close_clean[-1]
    span = resistance - support
    # 위·아래 10% 구간을 '반응 가격대' 로 본다 (한 점이 아니라 띠로 보라는 09.md 조언)
    band = span * 0.1 if span else 0.0

    if span <= 0:
        position = None
    else:
        position = (price - support) / span * 100

    return {
        "available": True,
        "window": len(high_clean),
        "resistance": round(resistance, 2),
        "support": round(support, 2),
        "resistance_band": [round(resistance - band, 2), round(resistance, 2)],
        "support_band": [round(support, 2), round(support + band, 2)],
        "price": price,
        "position_pct": None if position is None else round(position, 1),
        "to_resistance_pct": round((resistance / price - 1) * 100, 2) if price else None,
        "to_support_pct": round((support / price - 1) * 100, 2) if price else None,
        "why": (f"최근 {len(high_clean)}거래일 범위의 "
                f"{'상단' if (position or 0) > 66 else '하단' if (position or 0) < 33 else '중간'}에 있다"),
        "basis": "08강 09.md — 지지·저항은 한 가격이 아니라 반복 반응한 가격대다",
        "limitation": "돌파된 저항은 지지로 역할이 바뀐다. 거래량 급증을 함께 봐야 돌파를 신뢰할 수 있다.",
    }


def box_range(closes: Sequence[float], window: int = LEVEL_WINDOW,
              threshold_pct: float = 15.0) -> Dict:
    """박스권인가 — 최근 구간의 고저 폭이 좁으면 횡보로 본다."""
    clean = _clean(closes)[-window:]
    if len(clean) < 10:
        return {"available": False, "reason": "관측이 모자라 박스권 판정을 못 한다"}
    high, low = max(clean), min(clean)
    span_pct = (high / low - 1) * 100 if low else None
    if span_pct is None:
        return {"available": False, "reason": "저점이 0이라 폭을 못 잰다"}
    boxed = span_pct <= threshold_pct
    return {
        "available": True,
        "span_pct": round(span_pct, 2),
        "high": high, "low": low, "window": len(clean),
        "boxed": boxed,
        "grade": GRADE_WARNING if boxed else GRADE_GOOD,
        "why": (f"최근 {len(clean)}거래일 고저 폭이 {span_pct:.1f}% 로 {threshold_pct:.0f}% 이하다 "
                "— 박스권으로 본다" if boxed
                else f"고저 폭 {span_pct:.1f}% — 방향성 있는 움직임이다"),
        "basis": "08강 09.md 추세분석 — 횡보 구간에서는 추세 지표가 잘 듣지 않는다",
    }


# ─────────────────────────────────────────────────────────────
# 6. 5신호 스코어링 (10.md Tab 7)
# ─────────────────────────────────────────────────────────────
# 점수와 문턱은 10.md 표 그대로다. 내가 정하지 않는다.
SIGNAL_RULES = [
    ("RSI 과매도", 2, "RSI < 30"),
    ("MACD 골든크로스", 2, "MACD > Signal"),
    ("볼린저 하단 터치", 1, "종가 ≤ 하단밴드"),
    ("거래량 평균 초과", 1, "거래량 > 20일 평균"),
    ("정배열", 1, "MA20 > MA60"),
]
SIGNAL_MAX = sum(points for _, points, _ in SIGNAL_RULES)      # 7
SIGNAL_BUY = 5
SIGNAL_HOLD = 3


def signal_score(closes: Sequence[float], volumes: Optional[Sequence[float]] = None) -> Dict:
    """5신호 총점 (10.md Tab 7).

    ⚠️ **매매 신호가 아니다.** 강의가 웹앱 실습으로 만든 학습용 요약이고,
    이 프로젝트는 투자 권유를 하지 않는다 (모든 리포트에 붙는 고지와 같은 자리다).
    그래서 판정 문구에 그 사실을 함께 싣는다.
    """
    clean = _clean(closes)
    if len(clean) < 60:
        return {"available": False,
                "reason": f"종가 {len(clean)}개로는 5신호(MA60 필요)를 못 낸다"}

    rsi_row = rsi(clean)
    macd_row = macd(clean)
    band_row = bollinger(clean)
    ma20 = sma(clean, 20)[-1]
    ma60 = sma(clean, 60)[-1]
    vol_clean = _clean(volumes or [])

    signals = []

    def add(name: str, points: int, condition: str, met: Optional[bool], detail: str) -> None:
        signals.append({"name": name, "points": points if met else 0, "max": points,
                        "condition": condition,
                        "met": met, "detail": detail,
                        # 판정불가를 '충족 안 함' 과 갈라 둔다 (H07 과 같은 원칙)
                        "status": "판정불가" if met is None else ("충족" if met else "미충족")})

    add("RSI 과매도", 2, "RSI < 30",
        None if not rsi_row.get("available") else rsi_row["value"] < RSI_OVERSOLD,
        f"RSI {rsi_row.get('value')}" if rsi_row.get("available") else rsi_row.get("reason", ""))
    add("MACD 골든크로스", 2, "MACD > Signal",
        None if not macd_row.get("available") else macd_row["macd"] > macd_row["signal"],
        (f"MACD {macd_row.get('macd')} vs Signal {macd_row.get('signal')}"
         if macd_row.get("available") else macd_row.get("reason", "")))
    add("볼린저 하단 터치", 1, "종가 ≤ 하단밴드",
        None if not band_row.get("available") else clean[-1] <= (band_row["lower"] or 0),
        (f"종가 {clean[-1]:,.0f} vs 하단 {band_row.get('lower')}"
         if band_row.get("available") else band_row.get("reason", "")))
    if len(vol_clean) >= 20:
        vol_ma = fmean(vol_clean[-20:])
        add("거래량 평균 초과", 1, "거래량 > 20일 평균", vol_clean[-1] > vol_ma,
            f"거래량 {vol_clean[-1]:,.0f} vs 20일 평균 {vol_ma:,.0f}")
    else:
        add("거래량 평균 초과", 1, "거래량 > 20일 평균", None, "거래량 자료가 없다")
    add("정배열", 1, "MA20 > MA60",
        None if (ma20 is None or ma60 is None) else ma20 > ma60,
        f"MA20 {ma20:,.0f} vs MA60 {ma60:,.0f}" if (ma20 and ma60) else "이동평균이 모자라다")

    total = sum(s["points"] for s in signals)
    unknown = [s["name"] for s in signals if s["status"] == "판정불가"]
    if total >= SIGNAL_BUY:
        label, grade = "BUY", GRADE_GOOD
    elif total >= SIGNAL_HOLD:
        label, grade = "HOLD", GRADE_WARNING
    else:
        label, grade = "SELL", GRADE_SERIOUS

    return {
        "available": True,
        "total": total,
        "max": SIGNAL_MAX,
        "label": label,
        "grade": grade,
        "signals": signals,
        "unscored": unknown,
        "why": f"{len(signals)}신호 중 {total}/{SIGNAL_MAX}점 — {label}",
        "basis": "08강 10.md Tab 7 — 5신호 스코어링 (5점↑ BUY · 3~4 HOLD · 2↓ SELL)",
        "disclaimer": ("학습용 요약이지 매매 신호가 아니다. 판정불가를 0점으로 세므로 "
                       "자료가 없으면 점수가 낮게 나온다 — 낮은 점수를 곧바로 매도로 읽지 않는다."),
        "limitation": (f"판정불가 {len(unknown)}건({', '.join(unknown)})이 0점으로 들어갔다"
                       if unknown else ""),
    }


def summarize(closes: Sequence[float], highs: Optional[Sequence[float]] = None,
              lows: Optional[Sequence[float]] = None,
              volumes: Optional[Sequence[float]] = None) -> Dict:
    """기술적 판정 한 묶음 — H04 가 이걸 통째로 받는다."""
    clean = _clean(closes)
    ma = moving_averages(clean)
    short_line = sma(clean, 20)
    long_line = sma(clean, 60)
    return {
        "moving_averages": ma,
        "cross": cross(short_line, long_line),
        "rsi": rsi(clean),
        "macd": macd(clean),
        "bollinger": bollinger(clean),
        "levels": levels(highs or clean, lows or clean, clean),
        "box": box_range(clean),
        "signal_score": signal_score(clean, volumes),
        "observations": len(clean),
        "basis": "08강 09·10 기술적분석",
    }


def trend_verdict(summary: Dict) -> Dict:
    """묶음 판정을 한 문장으로 (해석카드 관찰란용)."""
    ma = summary.get("moving_averages", {})
    rsi_row = summary.get("rsi", {})
    score = summary.get("signal_score", {})
    if not ma.get("available"):
        return verdict(GRADE_UNKNOWN, "기술적 추세", ma.get("reason", "판정 자료가 없다"),
                       basis="09.md")
    order = ma.get("order") or "판정불가"
    grade = (GRADE_GOOD if order == "정배열" else
             GRADE_SERIOUS if order == "역배열" else GRADE_WARNING)
    why = (f"{order} · RSI {rsi_row.get('value', '—')}({rsi_row.get('zone', '—')}) "
           f"· 5신호 {score.get('total', '—')}/{score.get('max', 7)}")
    return verdict(grade, "기술적 추세", why, basis="08강 09·10")


__all__ = ["sma", "ema", "moving_averages", "cross", "rsi", "macd", "bollinger",
           "levels", "box_range", "signal_score", "summarize", "trend_verdict",
           "MA_WINDOWS", "SIGNAL_RULES", "SIGNAL_MAX"]
