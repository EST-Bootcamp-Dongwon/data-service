"""종목 통합 조회 서비스 (서비스 계층)

`/api/stock/{ticker}` 하나로 **한국 주식과 미국 주식을 모두** 조회하고,
원하면 FRED 거시지표까지 같은 응답에 얹어 준다. 화면은 받은 값을 그리기만 한다.

이 계층이 하는 판단
------------------
1. **입력이 무엇인지 알아낸다.** `005930` · `삼성전자` · `AAPL` · `005930.KS` 를 모두 받는다.
2. **어디서 받아올지 고른다.** 야후 파이낸스가 우선이고, 국내 종목인데 야후가 실패하면
   이미 쌓아 둔 `data/krx_cache.db` 로 되돌아간다.
3. **거시지표를 주가 날짜에 맞춰 붙인다.** 달력이 다른 두 데이터를 겹쳐 그릴 수 있게 맞추고,
   변화율 기준 상관계수까지 계산해서 내려준다.

왜 KRX 캐시로 티커를 해석하는가
------------------------------
야후에서 국내 종목은 코스피 `.KS` · 코스닥 `.KQ` 로 접미사가 갈린다.
**접미사를 잘못 붙여도 야후는 오류를 내지 않고 엉뚱한 값을 준다.**
실제로 코스닥 종목 `247540`(에코프로비엠)을 `.KS` 로 물으면 하루 묵은 96,500원이,
`.KQ` 로 물으면 당일 103,500원이 온다. 사용자는 틀린 줄도 모른다.

`data/krx_cache.db` 에는 2,800여 종목의 **시장 구분과 한글 종목명**이 들어 있으므로,
여기서 시장을 확인하고 접미사를 정한다. 덤으로 `삼성전자` 같은 **한글 이름 검색**도 된다.

DB 가 없는 환경(Codespaces·배포 서버)을 위한 대비
------------------------------------------------
그 96MB DB 는 저장소에 올릴 수 없다(`.gitignore` 대상). 그래서 판별에 꼭 필요한 셋만
— 종목코드·종목명·시장 구분 — 뽑아 둔 **`data/stock_master.json`(98KB)** 을 함께 올린다.
찾는 순서는 **DB → 종목 마스터** 이고, 둘 다 없으면 두 접미사를 모두 조회해
**더 최근 데이터가 있는 쪽**을 고른다(느리지만 동작은 한다).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from app.clients import fred_data as fred
from app.clients import yf_data as yahoo
from app.repositories import krx_store as store

KST = timezone(timedelta(hours=9))

CODE_PATTERN = re.compile(r"^\d{6}$")          # 국내 종목코드 6자리
HANGUL_PATTERN = re.compile(r"[가-힣]")        # 한글이 섞여 있으면 종목명 검색으로 본다

# 조회 기간(개월) → yfinance period 코드.
# 과제 요구는 3~6개월이고, 비교용으로 1개월·1년을 함께 열어 뒀다.
MONTH_PERIODS: Dict[int, str] = {1: "1mo", 3: "3mo", 6: "6mo", 12: "1y"}
DEFAULT_MONTHS = 6

# KRX 시장 구분 → 야후 접미사
MARKET_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ", "KONEX": ".KN"}

# 이동평균 기간(일). 5=1주, 20=1개월, 60=3개월 거래일에 해당한다.
MA_WINDOWS = (5, 20, 60)

# 티커 해석 결과를 기억해 둔다. 같은 종목을 다시 물을 때 캐시 조회를 반복하지 않는다.
_resolve_memo: Dict[str, dict] = {}

# 종목 마스터 (DB 가 없을 때 쓰는 대체 자료). 처음 찾을 때 한 번만 읽어 둔다.
# (parents[0]=services, [1]=app, [2]=프로젝트 루트)
MASTER_PATH = Path(__file__).resolve().parents[2] / "data" / "stock_master.json"
_master: Optional[Dict[str, dict]] = None      # {"by_code": {...}, "by_name": {...}}


class StockError(Exception):
    """종목 조회 실패. `status` 는 라우터가 그대로 HTTP 상태 코드로 쓴다."""

    def __init__(self, message: str, status: int = 404):
        super().__init__(message)
        self.status = status


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


# ==================================================
# 1. 티커 해석 — 무엇을 입력했는가
# ==================================================
def _load_master() -> Dict[str, dict]:
    """`data/stock_master.json` 을 읽어 코드·이름 두 방향으로 색인해 둔다.

    파일이 없어도 조회 전체가 실패하면 안 되므로, 없으면 빈 색인을 돌려주고
    호출한 쪽이 접미사 탐색(`_probe_suffix`)으로 넘어가게 한다.
    """
    global _master
    if _master is not None:
        return _master

    by_code: Dict[str, dict] = {}
    by_name: Dict[str, dict] = {}
    try:
        raw = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        # 값은 [종목명, 시장, 시총순위] — 순위는 자동완성 정렬용이라 여기서는 안 쓴다.
        # 원소 개수가 늘어도 깨지지 않도록 앞 두 개만 꺼낸다.
        for code, value in raw.items():
            name, market = (list(value) + ["", ""])[:2]
            item = {"code": code, "name": name, "market": market}
            by_code[code] = item
            # 같은 이름이 여러 개면 먼저 나온(코드가 작은) 쪽을 남긴다.
            # 우선주("삼성전자우")는 이름이 달라 본주와 겹치지 않는다.
            by_name.setdefault(name, item)
    except (OSError, ValueError):
        pass          # 파일이 없거나 깨졌으면 빈 색인으로 둔다

    _master = {"by_code": by_code, "by_name": by_name}
    return _master


def _lookup_master(needle: str) -> Optional[dict]:
    """종목 마스터에서 종목코드 또는 한글 종목명으로 찾는다. (DB 가 없을 때의 대체 경로)"""
    master = _load_master()
    hit = master["by_code"].get(needle) or master["by_name"].get(needle)
    if hit:
        return dict(hit)

    # 앞부분만 일치하는 이름도 받아 준다 ("에코프로비" → "에코프로비엠")
    for name, item in master["by_name"].items():
        if name.startswith(needle):
            return dict(item)
    return None


def _lookup_krx(code_or_name: str) -> Optional[dict]:
    """종목코드 또는 한글 종목명으로 종목 하나를 찾는다.

    찾는 순서는 **KRX 캐시(DB) → 종목 마스터(JSON)** 다.
    DB 가 더 최신이고 거래대금까지 있어 이름이 겹칠 때 대표 종목을 고를 수 있으므로 먼저 본다.
    DB 가 없는 환경(Codespaces·배포 서버)에서는 마스터가 같은 일을 한다.

    어느 쪽도 못 읽어도 조회 전체가 실패하면 안 되므로,
    예외는 삼키고 `None` 을 돌려준다 (야후 단독으로도 동작해야 한다).
    """
    needle = code_or_name.strip()
    if not needle:
        return None

    try:
        with store.connect() as conn:
            if CODE_PATTERN.fullmatch(needle):
                row = conn.execute(
                    "SELECT code, name, market FROM daily_price WHERE code = ? "
                    "ORDER BY bas_dd DESC LIMIT 1", (needle,)).fetchone()
            else:
                # 이름 검색 — 정확히 일치하는 것을 먼저 찾고, 없으면 앞부분이 같은 종목을 쓴다.
                # 거래대금이 큰 순으로 골라 "삼성" 처럼 여러 개 걸리는 입력에서
                # 가장 대표적인 종목이 나오게 한다.
                row = conn.execute(
                    "SELECT code, name, market FROM daily_price WHERE name = ? "
                    "ORDER BY bas_dd DESC, value DESC LIMIT 1", (needle,)).fetchone()
                if row is None:
                    row = conn.execute(
                        "SELECT code, name, market FROM daily_price WHERE name LIKE ? "
                        "ORDER BY bas_dd DESC, value DESC LIMIT 1", (f"{needle}%",)).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass             # 캐시를 못 읽어도 마스터·야후 경로로 계속 간다

    return _lookup_master(needle)


def _probe_suffix(code: str) -> str:
    """KRX 캐시가 없을 때 `.KS` · `.KQ` 를 모두 조회해 **최신 데이터가 있는 쪽**을 고른다.

    접미사가 틀려도 야후가 값을 주기는 하지만, 상장 시장이 아닌 쪽은 갱신이 멈춰 있어
    마지막 거래일이 뒤처진다. 그 차이로 판별한다.
    """
    best, best_date = "", ""
    for suffix in (".KS", ".KQ"):
        try:
            data = yahoo.fetch_history(f"{code}{suffix}", "1mo")
        except yahoo.YahooError:
            continue
        last_date = data["rows"][-1]["date"] if data.get("rows") else ""
        if last_date > best_date:
            best, best_date = suffix, last_date
    if not best:
        raise StockError(
            f"알 수 없는 종목입니다. '{code}' 를 국내 증시에서 찾지 못했습니다.", status=404)
    return best


def resolve(ticker: str) -> dict:
    """입력을 **야후 티커 + 시장 구분** 으로 해석한다.

    받아들이는 입력
      - `005930`      국내 종목코드 → 시장을 확인해 `.KS` / `.KQ` 를 붙인다
      - `삼성전자`     한글 종목명   → KRX 캐시에서 코드를 찾는다
      - `005930.KS`   이미 접미사가 붙은 티커 → 그대로 쓴다
      - `AAPL`        그 외는 미국(해외) 티커로 본다
    """
    raw = (ticker or "").strip()
    if not raw:
        raise StockError("종목 코드나 티커를 입력해 주세요. (예: 005930 · 삼성전자 · AAPL)",
                         status=422)

    memo_key = raw.upper()
    if memo_key in _resolve_memo:
        return _resolve_memo[memo_key]

    upper = raw.upper()
    krx: Optional[dict] = None

    if HANGUL_PATTERN.search(raw):
        # (1) 한글 종목명 — KRX 캐시에서만 찾을 수 있다
        krx = _lookup_krx(raw)
        if not krx:
            raise StockError(
                f"알 수 없는 종목입니다. '{raw}' 라는 이름의 국내 종목을 찾지 못했습니다. "
                "종목코드 6자리(005930)로 입력해 보세요.",
                status=404)
        code = krx["code"]
        suffix = MARKET_SUFFIX.get(krx.get("market") or "", ".KS")
        symbol, market = f"{code}{suffix}", "KR"

    elif CODE_PATTERN.fullmatch(upper):
        # (2) 숫자 6자리 — 국내 종목코드. 시장을 알아야 접미사를 정할 수 있다
        krx = _lookup_krx(upper)
        suffix = MARKET_SUFFIX.get((krx or {}).get("market") or "", "") or _probe_suffix(upper)
        symbol, market, code = f"{upper}{suffix}", "KR", upper

    elif upper.endswith((".KS", ".KQ", ".KN")):
        # (3) 접미사가 이미 붙은 국내 티커 — 그대로 쓰고 한글 이름만 캐시에서 보강한다
        code = upper.split(".")[0]
        krx = _lookup_krx(code) if CODE_PATTERN.fullmatch(code) else None
        symbol, market = upper, "KR"

    else:
        # (4) 그 외 — 미국(해외) 티커. `^GSPC` 같은 지수도 여기로 온다
        symbol, market, code = upper, "US", upper

    resolved = {
        "symbol": symbol,
        "market": market,                                   # KR · US
        "code": code,
        "krx_name": (krx or {}).get("name") or "",          # 한글 종목명 (있을 때만)
        "krx_market": (krx or {}).get("market") or "",      # KOSPI · KOSDAQ · KONEX
    }
    _resolve_memo[memo_key] = resolved
    return resolved


# ==================================================
# 2. 주가 조회 — 야후 우선, 국내는 KRX 캐시로 대체
# ==================================================
def _moving_average(values: Sequence[Optional[float]], window: int) -> List[Optional[float]]:
    """단순이동평균. 앞쪽 `window-1` 칸은 계산할 수 없어 `None` 이다.

    누적합을 굴려 O(n) 으로 계산한다 (매 칸마다 다시 더하면 O(n·window)).
    """
    out: List[Optional[float]] = []
    total = 0.0
    for i, value in enumerate(values):
        total += value or 0
        if i >= window:
            total -= values[i - window] or 0
        out.append(round(total / window, 2) if i >= window - 1 else None)
    return out


def _from_krx_cache(resolved: dict, months: int) -> Optional[dict]:
    """야후가 실패했을 때 쓰는 국내 종목 대체 경로 (`data/krx_cache.db`).

    캐시에 없으면 `None` 을 돌려주고, 호출한 쪽이 원래의 야후 오류를 그대로 알린다.
    """
    if resolved["market"] != "KR" or not CODE_PATTERN.fullmatch(resolved["code"]):
        return None
    try:
        rows = store.series(resolved["code"], days=months * 22)   # 한 달 ≒ 22거래일
    except Exception:
        return None
    if not rows:
        return None

    return {
        "source": "krx-cache",
        "name": rows[-1].get("name") or resolved["code"],
        "currency": "KRW",
        "exchange": rows[-1].get("market") or "",
        "market_state": "",
        "rows": [{"date": r["date"], "open": r.get("open"), "high": r.get("high"),
                  "low": r.get("low"), "close": r.get("close"), "volume": r.get("volume")}
                 for r in rows if r.get("close") is not None],
    }


def _from_yahoo(resolved: dict, months: int) -> dict:
    """야후 파이낸스에서 일봉을 받아 온다. 실패하면 `YahooError` 가 그대로 올라간다."""
    period = MONTH_PERIODS[months]
    history = yahoo.fetch_history(resolved["symbol"], period)

    # 종목명·통화는 `quote` 에만 있다. 여기서 실패해도 차트는 그릴 수 있으므로 없으면 넘어간다.
    info: Dict = {}
    try:
        info = yahoo.fetch_quote(resolved["symbol"])
    except yahoo.YahooError:
        pass

    return {
        "source": "yfinance",
        "name": info.get("name") or resolved["symbol"],
        "currency": info.get("currency") or ("KRW" if resolved["market"] == "KR" else "USD"),
        "exchange": info.get("exchange") or "",
        "market_state": info.get("market_state") or "",
        "rows": history["rows"],
        "quote": info,
    }


def fetch_stock(ticker: str, months: int = DEFAULT_MONTHS) -> dict:
    """종목 하나의 **최근 `months` 개월 일별 종가**와 요약 지표를 돌려준다.

    화면이 차트를 그리는 데 필요한 것(`dates`·`prices`)을 배열 두 개로 내려주고,
    캔들·거래량·이동평균처럼 더 자세히 그릴 때 쓸 값도 함께 담는다.
    """
    started = time.monotonic()

    if months not in MONTH_PERIODS:
        raise StockError(
            f"months 는 {', '.join(str(m) for m in MONTH_PERIODS)} 중 하나여야 합니다. "
            f"(받은 값: {months})", status=422)

    resolved = resolve(ticker)

    try:
        data = _from_yahoo(resolved, months)
    except yahoo.YahooError as error:
        # 국내 종목이면 이미 받아 둔 KRX 시세로 되돌아간다 (야후 장애·해외망 차단 대비)
        fallback = _from_krx_cache(resolved, months)
        if fallback is None:
            if error.status == 404:
                raise StockError(
                    f"알 수 없는 종목입니다. '{ticker}' 에 해당하는 시세를 찾지 못했습니다. "
                    "국내 종목은 6자리 코드(005930)나 한글 이름, "
                    "미국 종목은 티커(AAPL)로 입력해 주세요.", status=404) from error
            raise StockError(str(error), status=error.status) from error
        data = fallback

    rows = data["rows"]
    if not rows:
        raise StockError(f"알 수 없는 종목입니다. '{ticker}' 의 시세가 비어 있습니다.", status=404)

    dates = [r["date"] for r in rows]
    prices = [r["close"] for r in rows]

    first, last = prices[0], prices[-1]
    prev = prices[-2] if len(prices) > 1 else None

    # 전일 대비는 야후 `quote` 값이 있으면 그쪽이 정확하다 (장중 실시간 반영).
    # 없으면 직전 종가와 비교해 직접 계산한다.
    quote = data.get("quote") or {}
    change = quote.get("change") or {}
    diff = change.get("diff")
    rate = change.get("rate")
    if diff is None and prev:
        diff = last - prev
        rate = diff / prev * 100

    # 한글 종목명이 있으면 그쪽을 쓴다 ("Samsung Electronics Co., Ltd." 보다 "삼성전자" 가 낫다)
    name = resolved["krx_name"] or data["name"]

    return {
        "ticker": resolved["symbol"],
        "input": ticker,
        "code": resolved["code"],
        "market": resolved["market"],
        "market_detail": resolved["krx_market"] or data.get("exchange") or "",
        "name": name,
        "name_en": data["name"] if data["name"] != name else "",
        "currency": data["currency"],
        "source": data["source"],
        "market_state": data.get("market_state") or "",
        "months": months,
        # ── 차트용 배열 두 개 (요구사항의 핵심) ──
        "dates": dates,
        "prices": prices,
        # ── 더 자세히 그릴 때 쓰는 값 ──
        "opens": [r.get("open") for r in rows],
        "highs": [r.get("high") for r in rows],
        "lows": [r.get("low") for r in rows],
        "volumes": [r.get("volume") for r in rows],
        "moving_averages": [
            # 데이터가 기간보다 짧으면 전부 None 이라 그릴 게 없다. 그런 이동평균은 뺀다.
            {"period": w, "values": _moving_average(prices, w)}
            for w in MA_WINDOWS if len(prices) >= w
        ],
        # ── 요약 ──
        "count": len(rows),
        "current": last,
        "prev_close": prev,
        "change": {"diff": diff, "rate": rate},
        "period_open": first,
        "period_low": min(prices),
        "period_high": max(prices),
        "period_change_rate": (last - first) / first * 100 if first else None,
        "volume": rows[-1].get("volume"),
        "market_cap": quote.get("market_cap"),
        "week52_low": quote.get("week52_low"),
        "week52_high": quote.get("week52_high"),
        "fetched_at": _now_kst(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


# ==================================================
# 3. 거시지표 붙이기 (FRED 융합)
# ==================================================
def attach_macro(stock: dict, series_ids: Sequence[str]) -> List[dict]:
    """FRED 지표를 **주가 날짜에 맞춰** 붙이고 상관계수까지 계산해 돌려준다.

    화면이 바로 겹쳐 그릴 수 있도록 세 가지를 함께 준다.
      - `values`     주가와 같은 길이로 맞춘 원래 값 (툴팁에 그대로 쓴다)
      - `rebased`    시작을 100 으로 맞춘 값 — 단위가 다른 둘(원 vs %)을 한 그림에 겹치기 위함
      - `correlation` 일간 변화율 기준 상관계수 — "같이 움직이는가" 에 대한 답

    지표 하나가 실패해도 나머지는 살린다. 화면 일부가 비는 것이 전체 실패보다 낫다.
    """
    dates = stock.get("dates") or []
    prices = stock.get("prices") or []
    if not dates:
        return []

    start = dates[0]
    attached: List[dict] = []

    for sid in series_ids:
        sid = (sid or "").strip().upper()
        if not sid:
            continue
        try:
            # 시작일보다 넉넉히 앞에서부터 받는다. 월별 지표는 구간 안에 발표가 없을 수도 있어
            # 앞쪽을 못 채우면 차트가 통째로 비기 때문이다.
            series = fred.fetch_series(sid, start=_shift_months(start, -14), end=dates[-1])
        except fred.FredError as error:
            attached.append({"series_id": sid, "ok": False, "error": str(error)})
            continue

        aligned = fred.align_to_dates(series, dates)
        attached.append({
            "series_id": sid,
            "ok": True,
            "label": series["label"],
            "title": series["title"],
            "unit": series["unit"],
            "frequency": series["frequency"],
            "note": series["note"],
            "values": aligned,
            "rebased": _rebase(aligned),
            "latest": series["latest"],
            "latest_date": series["latest_date"],
            "correlation": fred.correlation(prices, aligned),
        })

    return attached


def _rebase(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """첫 유효값을 100 으로 놓고 나머지를 비율로 바꾼다.

    금리(4.67%)와 주가(207,000원)는 자릿수가 달라 한 축에 그리면 한쪽이 바닥에 깔린다.
    둘 다 100 에서 출발시키면 "얼마나 움직였는가" 를 같은 눈금으로 비교할 수 있다.
    """
    base = next((v for v in values if v not in (None, 0)), None)
    if base is None:
        return [None] * len(values)
    return [None if v is None else round(v / base * 100, 3) for v in values]


def _shift_months(date_text: str, months: int) -> str:
    """`YYYY-MM-DD` 를 `months` 개월 옮긴다. (음수면 과거로)"""
    try:
        year, month, day = (int(p) for p in date_text.split("-"))
    except ValueError:
        return date_text
    total = year * 12 + (month - 1) + months
    year, month = divmod(total, 12)
    # 말일 문제(3/31 에서 한 달 전 → 2/31)를 피하려고 1일로 내린다. 시작 경계라 문제없다.
    return f"{year:04d}-{month + 1:02d}-01"


# ==================================================
# 4. 화면 보조 — 예시 종목 목록
# ==================================================
def sample_tickers(limit: int = 6) -> List[dict]:
    """예시 버튼에 쓸 종목 목록. 국내는 캐시의 거래대금 상위, 미국은 고정 목록이다."""
    korean: List[dict] = []
    try:
        for row in store.universe()[:limit]:
            korean.append({"ticker": row["code"], "label": row["name"], "market": "KR"})
    except Exception:
        pass

    if not korean:
        # DB 가 없으면 거래대금 순위를 알 수 없다. 잘 알려진 종목을 코드로 지정하고
        # 이름만 종목 마스터에서 채운다 (코스닥 종목을 하나 섞어 접미사 판별을 보여 준다).
        master = _load_master()["by_code"]
        for code in ("005930", "000660", "035420", "005380", "247540", "196170"):
            item = master.get(code)
            korean.append({"ticker": code,
                           "label": (item or {}).get("name") or code,
                           "market": "KR"})

    american = [
        {"ticker": "AAPL", "label": "Apple", "market": "US"},
        {"ticker": "NVDA", "label": "NVIDIA", "market": "US"},
        {"ticker": "MSFT", "label": "Microsoft", "market": "US"},
        {"ticker": "TSLA", "label": "Tesla", "market": "US"},
    ]
    return korean + american
