#!/usr/bin/env python3
"""시장 스냅샷(`data/market_snapshot.json.gz`) 생성 스크립트 — 명세서 §2.3

스크리닝은 "전 종목을 훑어 조건에 맞는 것을 고르는" 일이다. 그런데 그때마다
2,764종목의 수익률을 계산하려면 96MB 캐시 DB 가 있어야 하고, 그 DB 는 배포 번들에 못 올린다.
그래서 **미리 계산해 압축해 둔다.** 수백 KB 면 저장소·번들에 함께 올릴 수 있다.

담는 것 (U2 결정: 국내 전종목 + 미국 S&P 500)
--------------------------------------------
| 구분 | 대상 | 출처 | 비용 |
|---|---|---|---|
| 국내 가격·시총 | 상장 전종목 ≈2,764 | `data/krx_cache.db` | 외부 호출 **0회** |
| 국내 PER·PBR | 같음 | DART 다중회사 주요계정 | 100개씩 묶어 ≈28회 |
| 미국 가격·시총 | S&P 500 ≈503 | yfinance 배치 | 배치 다운로드 + fast_info |

국내는 이미 받아 둔 캐시(232거래일)로 계산하므로 외부 호출이 아예 없다.
미국 유니버스는 `data/us_master.json` 의 **S&P 500 표시**를 그대로 쓴다
(`build_us_master.py` 가 이미 붙여 둔 값이라 따로 받을 필요가 없다).

    python3 scripts/build_market_snapshot.py                # 전부 만든다
    python3 scripts/build_market_snapshot.py --skip-us      # 국내만 (빠름)
    python3 scripts/build_market_snapshot.py --skip-dart    # PER·PBR 없이
    python3 scripts/build_market_snapshot.py --check        # 현재 파일 상태만 확인

솔직하게 남기는 것들
------------------
- **250일 수익률은 캐시가 232거래일뿐이라 종목에 따라 비어 있다.** 없는 값은 `null` 로 두고
  `meta.notes` 에 사유를 적는다. 있는 척 채우면 백테스트가 조용히 틀어진다.
- **미국은 PER·PBR 이 비어 있다.** DART 는 국내 공시만 다루고, 야후에서 종목마다 받으면
  503회 호출이라 요청 한도(429)에 걸린다. 이 역시 `null` + 사유를 남긴다.
- 스냅샷은 수동 갱신이라 기준일이 뒤처질 수 있다. 그 판정은 `app/repositories/snapshot_store.py`
  의 `staleness()` 가 하고, 5거래일을 넘으면 `G-DATE` 를 발행한다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# 이 스크립트는 scripts/ 안에 있다. 프로젝트 루트를 import 경로에 넣어야
# `app.*` 를 찾을 수 있다 (`python3 scripts/build_market_snapshot.py` 로 바로 실행하기 위함).
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.clients import dart_data                    # noqa: E402  (경로 설정 뒤에 import)
from app.repositories import krx_store               # noqa: E402

OUTPUT_PATH = BASE_DIR / "data" / "market_snapshot.json.gz"
US_MASTER_PATH = BASE_DIR / "data" / "us_master.json"
KST = timezone(timedelta(hours=9))

# 수익률을 재는 구간(거래일). 명세서 §2.3 이 정한 세 개다.
RETURN_WINDOWS = (60, 120, 250)

# DART 다중회사 API 한도. 실측으로 100개까지 받고 200개는 `021`(회사 수 초과)로 거부한다.
DART_BATCH = 100

# 야후 배치 크기. 한 번에 너무 많이 물으면 응답이 잘리거나 한도에 걸린다.
YF_BATCH = 120


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _iso_date(yyyymmdd: str) -> str:
    """`20260730` → `2026-07-30`. 이미 하이픈이 있거나 형식이 다르면 그대로 둔다."""
    text = (yyyymmdd or "").strip()
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """나눗셈. 분모가 없거나 0 이하면 `None`.

    **분모가 음수일 때도 `None` 을 준다.** 적자 기업의 PER 은 수학적으로는 음수가 나오지만
    "싸다" 는 뜻이 전혀 아니라서, 숫자로 두면 정렬·필터에서 저평가 상위로 올라온다.
    비어 있는 편이 정직하다.
    """
    if numerator is None or denominator is None or denominator <= 0:
        return None
    value = numerator / denominator
    return round(value, 4) if value == value else None      # NaN 방어


# ==================================================
# 1. 국내 — KRX 캐시에서 가격·시총·수익률
# ==================================================
def build_domestic() -> List[Dict]:
    """캐시의 **가장 최근 거래일** 기준으로 국내 전종목 행을 만든다. (외부 호출 없음)"""
    latest = krx_store.latest_date()
    if not latest:
        print("  [건너뜀] KRX 캐시가 비어 있습니다. `python3 scripts/fetch_krx.py` 를 먼저 돌리세요.")
        return []

    base_rows = krx_store.snapshot(latest)
    print(f"  기준일 {latest} · {len(base_rows):,}종목")

    max_window = max(RETURN_WINDOWS)

    # 수익률을 재려면 과거 종가가 필요하다.
    #
    # **`closes_matrix` 를 쓰면 안 된다.** 그 함수는 모든 종목이 **공통으로 가진 날짜만**
    # 교집합으로 남긴다 — 상관행렬을 만들 때는 그래야 맞지만, 종목별 수익률에는 치명적이다.
    # 실측: 400종목을 넣으면 축이 22일로, 2,763종목 전부를 넣으면 **5일**로 줄어
    # 60·120·250일 수익률이 전부 null 이 됐다. 신규 상장 종목 하나가 전체 축을 자른다.
    #
    # 그래서 `window()` 로 전 종목 × N거래일을 한 번에 읽고 **종목별로 따로** 나눈다.
    # 종목마다 자기 데이터로 계산하므로 상장 기간이 짧은 종목은 그 종목만 비게 된다.
    # (개별 질의 2,763번보다 훨씬 빠르기도 하다 — 276초 → 수 초)
    raw = krx_store.window(days=max_window + 1, columns=("code", "bas_dd", "close"))
    by_code: Dict[str, List] = {}
    for row in raw:
        if row.get("close"):
            by_code.setdefault(row["code"], []).append((row["bas_dd"], row["close"]))

    available_days = len(krx_store.available_dates(limit=max_window + 10))
    print(f"  캐시 거래일 {available_days}일 · 수익률 구간 {RETURN_WINDOWS}")

    rows: List[Dict] = []
    for base in base_rows:
        code = base["code"]
        # 날짜 오름차순으로 세워 둔다 (과거 → 현재)
        history = sorted(by_code.get(code) or [])
        closes = [close for _, close in history]

        returns: Dict[str, Optional[float]] = {}
        for window in RETURN_WINDOWS:
            # 구간만큼 과거 종가가 있어야 잴 수 있다. 없으면 `None` 으로 둔다.
            # (상장한 지 얼마 안 된 종목·캐시가 짧은 구간이 여기 해당한다)
            if len(closes) > window and closes[-(window + 1)]:
                past = closes[-(window + 1)]
                returns[f"r{window}"] = round((closes[-1] - past) / past * 100, 2)
            else:
                returns[f"r{window}"] = None

        rows.append({
            "code": code,
            "name": base.get("name") or "",
            "market": base.get("market") or "",
            "sector": base.get("sector") or "",
            "close": base.get("close"),
            "change_rate": base.get("change_rate"),
            "market_cap": base.get("market_cap"),
            "value": base.get("value"),               # 거래대금
            "volume": base.get("volume"),
            "shares": base.get("listed_shares"),
            **returns,
            "per": None, "pbr": None, "roe": None,    # 아래 DART 단계에서 채운다
            "fiscal_year": None,
        })

    return rows


# ==================================================
# 2. 국내 — DART 로 PER · PBR · ROE
# ==================================================
# 다중회사 주요계정이 주는 계정명. 회사마다 표기가 조금씩 달라 후보를 나열한다.
MULTI_ACCOUNT_NAMES = {
    "net_income": ("당기순이익(손실)", "당기순이익", "당기순손실"),
    "equity": ("자본총계",),
}


def enrich_with_dart(rows: List[Dict], year: int) -> Dict:
    """DART 다중회사 주요계정으로 PER·PBR·ROE 를 채운다.

    한 번에 100개까지 물을 수 있어서 2,764종목이 **28회 호출**로 끝난다.
    개별 조회(`fnlttSinglAcntAll`)로 하면 2,764회라 자릿수가 다르다.

    지정한 연도에 사업보고서가 없는 회사가 꽤 있다(상장 직후·결산월 변경 등).
    그런 회사는 값을 비워 두고 몇 곳인지만 센다 — 없는 값을 지어내지 않는다.
    """
    # 종목코드 → 고유번호. 매핑에 없는 종목(신규 상장 등)은 조회할 수 없다.
    pairs = [(r["code"], dart_data.get_corp_code(r["code"])) for r in rows]
    resolvable = [(code, corp) for code, corp in pairs if corp]
    unmapped = len(pairs) - len(resolvable)

    by_code = {r["code"]: r for r in rows}
    corp_to_code = {corp: code for code, corp in resolvable}

    matched = 0
    failed_batches = 0
    batches = [resolvable[i:i + DART_BATCH] for i in range(0, len(resolvable), DART_BATCH)]

    for index, batch in enumerate(batches, start=1):
        try:
            items = dart_data.fetch_multi_accounts(
                [corp for _, corp in batch], year, reprt_code="11011")   # 사업보고서(연간)
        except dart_data.DartError as error:
            failed_batches += 1
            print(f"    배치 {index}/{len(batches)} 실패 — {error}")
            continue

        # 회사별로 계정을 모은다. 연결(CFS)이 있으면 연결을, 없으면 별도(OFS)를 쓴다.
        grouped: Dict[str, Dict[str, Dict]] = {}
        for item in items:
            corp = item.get("corp_code", "")
            name = (item.get("account_nm") or "").strip()
            slot = grouped.setdefault(corp, {})
            existing = slot.get(name)
            # 같은 계정이 CFS·OFS 둘 다 오면 연결을 남긴다
            if existing is None or (item.get("fs_div") == "CFS" and existing.get("fs_div") != "CFS"):
                slot[name] = item

        for corp, accounts in grouped.items():
            code = corp_to_code.get(corp)
            row = by_code.get(code) if code else None
            if not row:
                continue

            net_income = _pick_amount(accounts, MULTI_ACCOUNT_NAMES["net_income"])
            equity = _pick_amount(accounts, MULTI_ACCOUNT_NAMES["equity"])
            market_cap = row.get("market_cap")

            row["per"] = _ratio(market_cap, net_income)
            row["pbr"] = _ratio(market_cap, equity)
            # ROE 는 백분율로 둔다 (자본 대비 순이익)
            roe = _ratio(net_income, equity)
            row["roe"] = round(roe * 100, 2) if roe is not None else None
            row["fiscal_year"] = year
            matched += 1

        print(f"    배치 {index}/{len(batches)} · 누적 {matched:,}종목", end="\r")

    print(f"    DART 매칭 {matched:,}종목 완료" + " " * 20)
    return {
        "year": year,
        "matched": matched,
        "unmapped": unmapped,
        "failed_batches": failed_batches,
        "requested": len(resolvable),
    }


def _pick_amount(accounts: Dict[str, Dict], names: Sequence[str]) -> Optional[float]:
    """계정 후보 중 먼저 맞는 것의 당기 금액을 돌려준다."""
    for name in names:
        item = accounts.get(name)
        if item:
            value = _to_number(item.get("thstrm_amount"))
            if value is not None:
                return value
    return None


def _to_number(raw) -> Optional[float]:
    """`"1,234"` · `"-5,678"` → 숫자. 못 바꾸면 `None`."""
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if text in ("", "-", "null"):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value != value else value


# ==================================================
# 3. 미국 — S&P 500 (yfinance)
# ==================================================
def load_us_universe() -> List[Dict]:
    """`us_master.json` 에서 **S&P 500 표시가 붙은 종목**만 뽑는다.

    구조는 `{티커: [종목명, 거래소, ETF여부, S&P500여부]}` 다.
    `build_us_master.py` 가 위키피디아 편입 목록으로 이미 표시해 둔 값이라
    여기서 다시 받아 올 필요가 없다.
    """
    try:
        master = json.loads(US_MASTER_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"  [건너뜀] {US_MASTER_PATH.name} 을 읽지 못했습니다 — {error}")
        return []

    universe = [
        {"code": ticker, "name": info[0], "exchange": info[1]}
        for ticker, info in master.items()
        if len(info) >= 4 and info[3] == 1 and not info[2]      # S&P500 이고 ETF 가 아닌 것
    ]
    return sorted(universe, key=lambda u: u["code"])


def build_us() -> List[Dict]:
    """미국 S&P 500 의 가격·수익률·시총을 만든다."""
    universe = load_us_universe()
    if not universe:
        return []
    print(f"  S&P 500 유니버스 {len(universe)}종목 (us_master.json 의 표시 사용)")

    warnings.filterwarnings("ignore")
    try:
        import yfinance as yf
    except ModuleNotFoundError:
        print("  [건너뜀] yfinance 가 없습니다. `pip install yfinance` 하세요.")
        return []

    tickers = [u["code"] for u in universe]
    # 야후는 점(BRK.B)이 아니라 하이픈(BRK-B)을 쓴다
    yahoo_symbols = {t: t.replace(".", "-") for t in tickers}

    closes: Dict[str, List[float]] = {}
    volumes: Dict[str, List[float]] = {}

    batches = [tickers[i:i + YF_BATCH] for i in range(0, len(tickers), YF_BATCH)]
    for index, batch in enumerate(batches, start=1):
        symbols = [yahoo_symbols[t] for t in batch]
        try:
            frame = yf.download(symbols, period="2y", interval="1d", auto_adjust=True,
                                progress=False, threads=True, group_by="column")
        except Exception as error:
            print(f"    배치 {index}/{len(batches)} 실패 — {error}")
            continue

        for ticker in batch:
            symbol = yahoo_symbols[ticker]
            try:
                close = frame["Close"][symbol].dropna()
                volume = frame["Volume"][symbol].dropna()
            except Exception:
                continue
            if len(close) < 2:
                continue
            closes[ticker] = [float(v) for v in close.tolist()]
            volumes[ticker] = [float(v) for v in volume.tolist()]

        print(f"    배치 {index}/{len(batches)} · 누적 {len(closes)}종목", end="\r")

    print(f"    가격 수집 {len(closes)}종목 완료" + " " * 20)

    # 시총은 배치 다운로드가 주지 않는다. `fast_info` 는 `.info`(1~3초)보다 훨씬 가벼워
    # 병렬로 부르면 감당할 만하다. 실패하면 그 종목만 비워 둔다.
    caps: Dict[str, Optional[float]] = {}

    def fetch_cap(ticker: str) -> None:
        try:
            info = yf.Ticker(yahoo_symbols[ticker]).fast_info
            caps[ticker] = float(info["marketCap"]) if info.get("marketCap") else None
        except Exception:
            caps[ticker] = None

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_cap, list(closes)))
    print(f"    시총 수집 {sum(1 for v in caps.values() if v):,}종목 완료")

    rows: List[Dict] = []
    for item in universe:
        ticker = item["code"]
        series = closes.get(ticker)
        if not series:
            continue

        returns: Dict[str, Optional[float]] = {}
        for window in RETURN_WINDOWS:
            if len(series) > window and series[-(window + 1)]:
                past = series[-(window + 1)]
                returns[f"r{window}"] = round((series[-1] - past) / past * 100, 2)
            else:
                returns[f"r{window}"] = None

        last = series[-1]
        prev = series[-2] if len(series) >= 2 else None
        vol = volumes.get(ticker) or []

        rows.append({
            "code": ticker,
            "name": item["name"],
            "market": "US",
            "sector": item["exchange"],        # 업종 대신 거래소 (야후 업종은 종목별 호출이 필요하다)
            "close": round(last, 2),
            "change_rate": round((last - prev) / prev * 100, 2) if prev else None,
            "market_cap": caps.get(ticker),
            "value": round(last * vol[-1]) if vol else None,     # 거래대금 = 종가 × 거래량
            "volume": vol[-1] if vol else None,
            "shares": None,
            **returns,
            # 미국은 DART 가 없다. 야후에서 종목마다 받으면 503회라 한도에 걸린다.
            "per": None, "pbr": None, "roe": None, "fiscal_year": None,
        })

    return rows


# ==================================================
# 4. 조립 · 저장
# ==================================================
def build(skip_us: bool = False, skip_dart: bool = False,
          year: Optional[int] = None) -> Dict:
    started = time.monotonic()
    notes: List[str] = []
    sources: List[str] = []

    print("[1/3] 국내 — KRX 캐시에서 가격·시총·수익률")
    domestic = build_domestic()
    if domestic:
        sources.append("KRX 캐시 (data/krx_cache.db)")
        # 구간마다 따로 센다. 한 구간만 보면 다른 구간의 결손을 놓친다
        # (실제로 250일만 세다가 60·120일이 전부 비어 있던 것을 못 보고 지나쳤다).
        for window in RETURN_WINDOWS:
            missing = sum(1 for r in domestic if r.get(f"r{window}") is None)
            if missing:
                notes.append(
                    f"국내 {missing:,}종목은 {window}일 수익률이 비어 있습니다 "
                    f"(전체 {len(domestic):,}종목 중) — 상장 기간이 그만큼 안 되거나 "
                    "캐시 거래일이 모자랍니다. 없는 값은 null 로 두었습니다.")

    dart_report: Dict = {}
    if domestic and not skip_dart:
        # 사업보고서는 결산 후 3개월 안에 낸다. 오늘이 4월 이전이면 재작년이 최신이다.
        today = datetime.now(KST)
        target_year = year or (today.year - 1 if today.month >= 4 else today.year - 2)
        print(f"\n[2/3] 국내 — DART 로 PER·PBR·ROE ({target_year} 사업보고서)")
        dart_report = enrich_with_dart(domestic, target_year)
        sources.append(f"DART 다중회사 주요계정 ({target_year} 사업보고서)")
        gap = dart_report.get("requested", 0) - dart_report.get("matched", 0)
        if gap > 0:
            notes.append(
                f"국내 {gap:,}종목은 {target_year} 사업보고서가 없어 PER·PBR 이 비어 있습니다 "
                "(상장 직후·결산월 변경 등).")
        if dart_report.get("unmapped"):
            notes.append(
                f"국내 {dart_report['unmapped']:,}종목은 DART 고유번호 매핑에 없습니다 — "
                "`python3 scripts/build_corp_code.py` 를 다시 돌리면 줄어듭니다.")
    elif skip_dart:
        notes.append("`--skip-dart` 로 실행해 국내 PER·PBR·ROE 가 전부 비어 있습니다.")

    us: List[Dict] = []
    if not skip_us:
        print("\n[3/3] 미국 — S&P 500 (yfinance)")
        us = build_us()
        if us:
            sources.append("yfinance (S&P 500)")
            notes.append(
                "미국 종목은 PER·PBR 이 비어 있습니다 — DART 는 국내 공시만 다루고, "
                "야후에서 종목마다 받으면 500회 호출이라 요청 한도(429)에 걸립니다.")
    else:
        notes.append("`--skip-us` 로 실행해 미국 종목이 들어 있지 않습니다.")

    rows = domestic + us
    # KRX 캐시는 날짜를 `20260730` 으로 담는다. 스냅샷은 `YYYY-MM-DD` 로 통일한다 —
    # `snapshot_store.staleness()` 가 `date.fromisoformat()` 으로 읽고, 화면도 이 형식을 쓴다.
    as_of = _iso_date(krx_store.latest_date() or "")

    return {
        "as_of": as_of,
        "rows": rows,
        "available": bool(rows),
        "meta": {
            "generated_at": _now_kst(),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "sources": sources,
            "return_windows": list(RETURN_WINDOWS),
            "counts": {
                "total": len(rows),
                "domestic": len(domestic),
                "us": len(us),
                "with_per": sum(1 for r in rows if r.get("per") is not None),
                "with_market_cap": sum(1 for r in rows if r.get("market_cap")),
            },
            "dart": dart_report,
            # 무엇이 비어 있고 왜 비었는지를 파일 안에 남긴다.
            # 리포트가 "데이터가 다 있다" 고 오해하지 않게 하기 위함이다.
            "notes": notes,
        },
    }


def save(payload: Dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # gzip 은 mtime 을 헤더에 넣어 내용이 같아도 파일이 매번 달라진다.
    # `mtime=0` 으로 고정하면 내용이 그대로일 때 git diff 가 생기지 않는다.
    with gzip.GzipFile(OUTPUT_PATH, "wb", compresslevel=9, mtime=0) as archive:
        archive.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    counts = payload["meta"]["counts"]
    print(f"\n저장 완료 — {OUTPUT_PATH.relative_to(BASE_DIR)} ({size_kb:.0f}KB)")
    print(f"  기준일 {payload['as_of']} · 전체 {counts['total']:,}종목 "
          f"(국내 {counts['domestic']:,} · 미국 {counts['us']:,})")
    print(f"  PER 보유 {counts['with_per']:,} · 시총 보유 {counts['with_market_cap']:,}")
    for note in payload["meta"]["notes"]:
        print(f"  · {note}")


def check() -> int:
    """현재 파일 상태만 확인한다. 없으면 1 을 돌려준다(스크립트 종료 코드)."""
    from app.repositories import snapshot_store

    snapshot_store.reload()          # 방금 만든 파일을 읽도록 메모리 사본을 버린다
    stats = snapshot_store.stats()
    if not stats.get("available"):
        print(f"{OUTPUT_PATH.relative_to(BASE_DIR)} 가 없거나 비어 있습니다. "
              "`python3 scripts/build_market_snapshot.py` 로 만드세요.")
        return 1

    markets = " · ".join(f"{m['market']} {m['count']:,}" for m in stats["markets"])
    print(f"{OUTPUT_PATH.relative_to(BASE_DIR)}")
    print(f"  기준일     : {stats['as_of']}  (분석일 대비 약 {stats['trading_days_behind']}거래일 전)")
    print(f"  생성 시각  : {stats['generated_at']}")
    print(f"  종목 수    : {stats['count']:,}")
    print(f"  크기       : {stats['size_kb']}KB")
    print(f"  시장별     : {markets}")
    for note in (stats.get("sources") or []):
        print(f"  출처       : {note}")
    if stats.get("gap"):
        print(f"  ⚠ {stats['gap']['code']} — {stats['gap']['message']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="시장 스냅샷 생성 (명세서 §2.3)")
    parser.add_argument("--check", action="store_true", help="생성하지 않고 현재 파일 상태만 확인한다")
    parser.add_argument("--skip-us", action="store_true", help="미국 S&P 500 을 건너뛴다")
    parser.add_argument("--skip-dart", action="store_true", help="PER·PBR·ROE 계산을 건너뛴다")
    parser.add_argument("--year", type=int, default=None, help="DART 사업연도 (기본: 자동 판단)")
    args = parser.parse_args()

    if args.check:
        return check()

    payload = build(skip_us=args.skip_us, skip_dart=args.skip_dart, year=args.year)
    if not payload["rows"]:
        print("\n만들어진 행이 없습니다. KRX 캐시와 인증키를 확인하세요.", file=sys.stderr)
        return 1

    save(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
