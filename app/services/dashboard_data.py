"""대시보드 집계 (서비스 계층)

대시보드 카드 그리드와 상단 티커바에 필요한 값을 **한 번에** 모아 준다.
화면은 이 결과를 그리기만 하고 계산하지 않는다.

    ticker_bar()  지수·환율 4종 — 값 + 등락 (모든 화면 상단 티커바)
    summary()     카드 그리드 — 값 + 스파크라인 + 상태등급 + 데이터 상태

설계 메모
---------
1. **한 카드의 실패가 다른 카드를 죽이지 않는다.** 야후 요청 한도(429)·인증키 없음·캐시 없음은
   흔한 상황이라, 카드마다 `ok` 를 따로 두고 실패 사유(`error`)를 그 카드 안에서 말한다.
   (명세서 §6.4 — 리서치 API 는 오류로 중단하지 않는다. 대시보드도 같은 원칙을 쓴다)
2. **여러 지수를 동시에 부른다.** 야후 호출이 하나에 0.3~1초라 순서대로 부르면 카드가 늦게 뜬다.
   스레드 풀로 함께 부르고, 하나가 실패해도 나머지는 그대로 온다.
3. **`/tmp` 캐시는 속도만 담당한다** (명세서 §8.1). 서버리스는 인스턴스가 바뀌면 사라지므로
   있으면 쓰고 없으면 다시 부르는 best-effort 다. 정합성은 캐시가 아니라 원본 호출이 보장한다.
"""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.clients import fred_data
from app.repositories import krx_store, snapshot_store, tmp_cache

KST = timezone(timedelta(hours=9))

# 티커바·카드에 쓰는 지수·환율. 순서가 그대로 화면 순서가 된다.
#   digits — 표기 소수 자리수 (지수는 2자리, 환율은 1자리)
#   tick   — 상단 티커바에도 올릴지
INDICES = (
    {"key": "kospi",  "label": "코스피",   "ticker": "^KS11",  "digits": 2, "tick": True,  "unit": "p"},
    {"key": "kosdaq", "label": "코스닥",   "ticker": "^KQ11",  "digits": 2, "tick": True,  "unit": "p"},
    {"key": "nasdaq", "label": "나스닥",   "ticker": "^IXIC",  "digits": 2, "tick": True,  "unit": "p"},
    {"key": "sp500",  "label": "S&P 500",  "ticker": "^GSPC",  "digits": 2, "tick": False, "unit": "p"},
    {"key": "usdkrw", "label": "원/달러",  "ticker": "KRW=X",  "digits": 1, "tick": True,  "unit": "원"},
)

# 스파크라인용 기간. 3개월이면 60거래일 안팎이라 추세가 보이면서도 응답이 가볍다.
SPARK_PERIOD = "3mo"
SPARK_POINTS = 40          # 카드 폭이 좁아 40점이면 충분하다 (넘으면 고르게 솎아낸다)

TICKER_TTL = 180           # 티커바 캐시 3분 — 지수는 15분 이상 지연된 값이라 더 자주 부를 이유가 없다
SUMMARY_TTL = 300          # 카드 그리드 캐시 5분
CACHE_DIR = Path(tempfile.gettempdir()) / "api-test-dashboard"


# ==================================================
# /tmp best-effort 캐시
# ==================================================
def _cache_read(name: str, ttl: int) -> Optional[dict]:
    """`ttl` 초 안에 저장한 값이 있으면 돌려준다. 없거나 읽기 실패면 None."""
    try:
        path = CACHE_DIR / f"{name}.json"
        if not path.exists() or time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # 캐시는 있으면 좋고 없어도 그만이다. 어떤 실패도 요청을 막지 않는다.
        return None


def _cache_write(name: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


# ==================================================
# 지수·환율
# ==================================================
def _thin(values: List[float], limit: int = SPARK_POINTS) -> List[float]:
    """점이 너무 많으면 고르게 솎아낸다. 마지막 값은 반드시 남긴다(최근값이 끝점이라서)."""
    if len(values) <= limit:
        return values
    step = len(values) / limit
    picked = [values[int(i * step)] for i in range(limit - 1)]
    picked.append(values[-1])
    return picked


def _fetch_index(spec: dict, with_spark: bool) -> dict:
    """지수 하나를 야후에서 받아 카드 한 장 분량으로 정리한다. 실패는 예외 대신 `ok=False` 로 돌려준다."""
    card = {
        "key": spec["key"], "label": spec["label"], "ticker": spec["ticker"],
        "digits": spec["digits"], "unit": spec["unit"],
        "ok": False, "error": None, "value": None, "diff": None, "rate": None,
        "date": None, "spark": [], "grade": None, "grade_text": None,
    }
    try:
        # yfinance 를 여기서 import 한다 — 미설치 환경에서도 나머지 카드는 떠야 하기 때문이다.
        from app.clients import yf_data

        history = yf_data.fetch_history(spec["ticker"], SPARK_PERIOD)
        rows = history.get("rows") or []
        if len(rows) < 2:
            card["error"] = "시세가 비어 있습니다."
            return card

        closes = [r["close"] for r in rows if r.get("close") is not None]
        last, prev = closes[-1], closes[-2]
        card.update({
            "ok": True,
            "value": last,
            "diff": last - prev,
            "rate": (last - prev) / prev * 100 if prev else None,
            "date": rows[-1]["date"],
            "spark": _thin(closes) if with_spark else [],
        })
        # 상태등급 — 3개월 구간 수익률로 판단한다 (하루 등락은 흔들림이 커서 등급으로 쓰지 않는다)
        card["grade"], card["grade_text"] = _trend_grade(history.get("change_rate"))
    except Exception as error:
        card["error"] = str(error)
    return card


def _trend_grade(change_rate: Optional[float]) -> tuple:
    """3개월 수익률 → 상태등급. 색만으로 뜻을 말하지 않도록 등급 이름을 함께 돌려준다.

    등급 이름에 **기간을 붙인다.** 카드에는 하루 등락(+1.2% 등)이 크게 찍히는데
    등급은 3개월 추세라서, 기간을 빼면 두 값이 어긋나 보인다.
    """
    if change_rate is None:
        return (None, None)
    if change_rate >= 10:
        return ("hot", "3개월 강한 상승")
    if change_rate >= 2:
        return ("warm", "3개월 상승")
    if change_rate > -2:
        return ("neutral", "3개월 횡보")
    if change_rate > -10:
        return ("cool", "3개월 하락")
    return ("cold", "3개월 강한 하락")


def _fetch_indices(specs, with_spark: bool) -> List[dict]:
    """지수 여러 개를 동시에 부른다. 순서는 `specs` 그대로 유지한다."""
    if not specs:
        return []
    with ThreadPoolExecutor(max_workers=min(5, len(specs))) as pool:
        return list(pool.map(lambda s: _fetch_index(s, with_spark), specs))


# ==================================================
# 금리 (FRED)
# ==================================================
def _fetch_rate() -> dict:
    """미 국채 10년 금리. 인증키가 없으면 그 사실을 카드 안에서 말한다."""
    card = {
        "key": "dgs10", "label": "미 국채 10년", "unit": "%", "digits": 2,
        "ok": False, "error": None, "value": None, "diff": None, "rate": None,
        "date": None, "spark": [], "grade": None, "grade_text": None,
    }
    try:
        start = (datetime.now(KST) - timedelta(days=120)).strftime("%Y-%m-%d")
        series = fred_data.fetch_series("DGS10", start, "")
        values = [v for v in series.get("values") or [] if v is not None]
        if len(values) < 2:
            card["error"] = "발표된 값이 부족합니다."
            return card
        card.update({
            "ok": True,
            "value": series.get("latest"),
            "diff": values[-1] - values[-2],
            "rate": None,                       # 금리는 등락률(%)이 아니라 %p 로 읽는 값이다
            "date": series.get("latest_date"),
            "spark": _thin(values),
        })
        # 금리는 '오르면 좋다/나쁘다'가 없다. 방향만 말하고 좋고 나쁨은 붙이지 않는다.
        change = series.get("change")
        if change is not None:
            if change >= 0.25:
                card["grade"], card["grade_text"] = "warm", "4개월 상승"
            elif change <= -0.25:
                card["grade"], card["grade_text"] = "cool", "4개월 하락"
            else:
                card["grade"], card["grade_text"] = "neutral", "4개월 보합"
    except Exception as error:
        card["error"] = str(error)
    return card


# ==================================================
# 시장 온도 (KRX 캐시)
# ==================================================
def _market_temperature() -> dict:
    """상승 종목 비율로 시장 온도를 잰다.

    "몇 % 올랐나"보다 "몇 종목이 올랐나"가 시장 전체 분위기를 더 잘 보여 준다.
    (지수는 대형주 몇 개에 좌우되지만, 등락 종목 수는 시장 전체를 센다)
    """
    card = {
        "key": "temperature", "label": "시장 온도", "unit": "%", "digits": 1,
        "ok": False, "error": None, "value": None, "up": 0, "down": 0, "flat": 0,
        "date": None, "source": None, "grade": None, "grade_text": None,
    }
    try:
        date = krx_store.latest_date()
        items = krx_store.snapshot(date) if date else []
        source = "cache"

        # 캐시가 비어 있으면(배포 환경) KRX 를 그 자리에서 부른다
        if not items:
            items, date, source = krx_store.snapshot_live()

        if not items:
            card["error"] = "시세 스냅샷이 비어 있습니다."
            return card

        up = sum(1 for i in items if (i.get("change_rate") or 0) > 0)
        down = sum(1 for i in items if (i.get("change_rate") or 0) < 0)
        flat = len(items) - up - down
        moved = up + down
        ratio = up / moved * 100 if moved else 50.0

        card.update({
            "ok": True, "value": ratio, "up": up, "down": down, "flat": flat,
            "total": len(items), "date": date, "source": source,
        })
        card["grade"], card["grade_text"] = _temperature_grade(ratio)
    except Exception as error:
        card["error"] = str(error)
    return card


def _temperature_grade(ratio: float) -> tuple:
    """상승 종목 비율 → 시장 온도 등급. 경계값은 화면·리포트가 같은 것을 쓰도록 여기서만 정한다."""
    if ratio >= 70:
        return ("hot", "과열")
    if ratio >= 55:
        return ("warm", "강세")
    if ratio > 45:
        return ("neutral", "중립")
    if ratio > 30:
        return ("cool", "약세")
    return ("cold", "침체")


# ==================================================
# 공개 함수
# ==================================================
def ticker_bar() -> dict:
    """상단 티커바용 — 지수 3종 + 환율. 스파크라인 없이 값과 등락만 담는다."""
    cached = _cache_read("ticker", TICKER_TTL)
    if cached:
        cached["source"] = "tmp-cache"
        return cached

    specs = [s for s in INDICES if s["tick"]]
    payload = {
        "items": _fetch_indices(specs, with_spark=False),
        "fetched_at": _now_kst(),
        "source": "live",
    }
    # 전부 실패한 응답을 캐시에 남기면 3분 동안 실패가 굳는다. 하나라도 성공했을 때만 저장한다.
    if any(item["ok"] for item in payload["items"]):
        _cache_write("ticker", payload)
    return payload


def summary() -> dict:
    """카드 그리드용 — 지수 4종 + 환율 + 금리 + 시장 온도 + 데이터 상태."""
    cached = _cache_read("summary", SUMMARY_TTL)
    if cached:
        cached["source"] = "tmp-cache"
        return cached

    # 야후(지수 5개) · FRED(금리) · KRX(시장 온도)는 서로 무관하므로 함께 부른다
    with ThreadPoolExecutor(max_workers=3) as pool:
        indices_task = pool.submit(_fetch_indices, list(INDICES), True)
        rate_task = pool.submit(_fetch_rate)
        temp_task = pool.submit(_market_temperature)
        indices, rate, temperature = indices_task.result(), rate_task.result(), temp_task.result()

    payload = {
        "markets": indices,
        "rate": rate,
        "temperature": temperature,
        "data_status": _data_status(),
        "fetched_at": _now_kst(),
        "source": "live",
        "notice": "교육·리서치 목적 자료이며 투자자문이 아닙니다. 데이터는 15분 이상 지연됩니다.",
    }
    if any(item["ok"] for item in indices) or temperature["ok"]:
        _cache_write("summary", payload)
    return payload


def _data_status() -> List[dict]:
    """데이터 상태 — 인증키·캐시·스냅샷 현황. 예전 화면 상단 배지를 대시보드 카드로 올린 것이다.

    M2 에서 **DART · ECOS 인증키**와 **시장 스냅샷 · corp_code 매핑 · /tmp 캐시**를 더했다.
    데이터 소스가 늘어난 만큼 "지금 무엇을 쓸 수 있는가" 를 한자리에서 봐야 하기 때문이다.
    """
    rows: List[dict] = []

    # ── KRX 시세 캐시 ────────────────────────
    try:
        stats = krx_store.stats()
        has_cache = bool(stats.get("days"))
        rows.append({
            "key": "krx-cache", "label": "KRX 시세 캐시",
            "ok": True,
            "grade": "good" if has_cache else "warning",
            "grade_text": "캐시 사용" if has_cache else "라이브 조회",
            "detail": (f"{stats.get('first_date')} ~ {stats.get('last_date')} · "
                       f"{stats.get('days')}거래일 · {stats.get('rows'):,}행")
            if has_cache else "캐시가 비어 있어 요청할 때 KRX 를 직접 부릅니다.",
        })
    except Exception as error:
        rows.append({"key": "krx-cache", "label": "KRX 시세 캐시", "ok": False,
                     "grade": "critical", "grade_text": "확인 실패", "detail": str(error)})

    # ── 시장 스냅샷 ─────────────────────────
    # 기준일이 뒤처지면 스크리닝 결과가 옛날 이야기가 된다. 그 판정은 저장소가 한다.
    try:
        snap = snapshot_store.stats()
        if not snap.get("available"):
            rows.append({
                "key": "snapshot", "label": "시장 스냅샷", "ok": True,
                "grade": "warning", "grade_text": "없음",
                "detail": "스크리닝용 사전계산 파일이 없습니다. "
                          "`python3 scripts/build_market_snapshot.py` 로 만들 수 있습니다.",
            })
        else:
            markets = " · ".join(f"{m['market']} {m['count']:,}" for m in snap["markets"])
            behind = snap.get("trading_days_behind")
            rows.append({
                "key": "snapshot", "label": "시장 스냅샷", "ok": True,
                "grade": "warning" if snap.get("stale") else "good",
                "grade_text": f"{behind}거래일 전" if snap.get("stale") else "최신",
                "detail": f"기준일 {snap['as_of']} · {snap['count']:,}종목 ({markets}) · "
                          f"{snap['size_kb']}KB"
                          + (f" — {snap['gap']['message']}" if snap.get("gap") else ""),
            })
    except Exception as error:
        rows.append({"key": "snapshot", "label": "시장 스냅샷", "ok": False,
                     "grade": "critical", "grade_text": "확인 실패", "detail": str(error)})

    # ── DART 고유번호 매핑 ───────────────────
    # 이게 없으면 DART 를 종목코드로 조회할 수 없다 (재무·공시 전부가 막힌다).
    try:
        status = _dart_status()
        loaded = bool(status.get("corp_code_loaded"))
        rows.append({
            "key": "corp-code", "label": "DART 고유번호 매핑", "ok": True,
            "grade": "good" if loaded else "warning",
            "grade_text": "정상" if loaded else "없음",
            "detail": (f"{status.get('corp_code_count'):,}개 상장사 · {status.get('corp_code_file')}"
                       if loaded else
                       "매핑이 없어 DART 를 종목코드로 조회할 수 없습니다. "
                       "`python3 scripts/build_corp_code.py` 로 만드세요."),
        })
    except Exception as error:
        rows.append({"key": "corp-code", "label": "DART 고유번호 매핑", "ok": False,
                     "grade": "critical", "grade_text": "확인 실패", "detail": str(error)})

    # ── 인증키 ──────────────────────────────
    # 값은 절대 싣지 않고 있는지 · 어디서 읽었는지만 알린다
    for key, label, loader in (
        ("krx", "KRX 인증키", _krx_status),
        ("kosis", "KOSIS 인증키", _kosis_status),
        ("fred", "FRED 인증키", fred_data.get_status),
        ("dart", "DART 인증키", _dart_status),
        ("ecos", "ECOS 인증키", _ecos_status),
    ):
        try:
            status = loader()
            loaded = bool(status.get("key_loaded"))
            rows.append({
                "key": key, "label": label, "ok": True,
                "grade": "good" if loaded else "warning",
                "grade_text": "정상" if loaded else "없음",
                "detail": (f"{status.get('key_source')} · {status.get('key_length')}자"
                           if loaded else ".key 또는 환경변수에 키를 넣어 주세요."),
            })
        except Exception as error:
            rows.append({"key": key, "label": label, "ok": False,
                         "grade": "critical", "grade_text": "확인 실패", "detail": str(error)})

    # ── /tmp 캐시 ───────────────────────────
    # 서버리스는 경로가 있어도 못 쓰는 경우가 있어 실제로 써 보고 판단한다.
    try:
        cache = tmp_cache.stats()
        writable = cache.get("writable")
        used = " · ".join(f"{n['namespace']} {n['count']}개" for n in cache["namespaces"])
        rows.append({
            "key": "tmp-cache", "label": "/tmp 캐시", "ok": True,
            "grade": "good" if writable else "warning",
            "grade_text": "쓰기 가능" if writable else "쓰기 불가",
            "detail": (f"{cache['root']} · {cache['total_files']}개 파일"
                       + (f" ({used})" if used else " (비어 있음)")) if writable else
                      f"{cache['root']} 에 쓸 수 없습니다. 속도만 느려지고 결과는 같습니다.",
        })
    except Exception as error:
        rows.append({"key": "tmp-cache", "label": "/tmp 캐시", "ok": False,
                     "grade": "critical", "grade_text": "확인 실패", "detail": str(error)})

    return rows


# 클라이언트를 늦게 부르는 이유 — 하나가 import 에 실패해도 나머지 카드는 떠야 한다.
def _krx_status() -> dict:
    from app.clients import krx_data
    return krx_data.get_status()


def _kosis_status() -> dict:
    from app.clients import kosis_data
    return kosis_data.get_status()


def _dart_status() -> dict:
    from app.clients import dart_data
    return dart_data.get_status()


def _ecos_status() -> dict:
    from app.clients import ecos_data
    return ecos_data.get_status()
