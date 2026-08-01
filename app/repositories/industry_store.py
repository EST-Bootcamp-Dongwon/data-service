"""표준산업분류 맵 읽기 (M4)

`scripts/build_industry_map.py` 가 만든 `data/industry_map.json` 을 한 번만 올려 둔다.
없으면 **예외를 던지지 않는다** — 업종 기준 피어를 못 쓸 뿐, 나머지는 그대로 돌아야 한다.

접두어 매칭이 이 파일의 핵심이다. DART 업종코드는 자릿수가 섞여 있어서
(3자리 1,378 · 4자리 478 · 5자리 2,025 · 2자리 44 — 실측 3,925종목)
완전일치만 보면 삼성전자(264)와 SK하이닉스(2612)가 남남이 된다.
5→4→3→2 자리로 넓혀 가며 **후보가 찰 때까지** 찾는다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "industry_map.json"

_cache: Optional[Dict] = None
_lock = threading.Lock()


def load() -> Dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            _cache = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {"map": {}, "generated_at": "", "total": 0, "with_industry": 0}
    return _cache


def available() -> bool:
    return bool(load().get("map"))


def industry_of(code: str) -> str:
    """종목코드 → 표준산업분류 코드 (없으면 빈 문자열)."""
    entry = load().get("map", {}).get((code or "").strip())
    return str(entry.get("industry_code", "")) if entry else ""


def entry_of(code: str) -> Dict:
    return load().get("map", {}).get((code or "").strip(), {})


def peers_by_industry(code: str, min_peers: int = 5) -> Dict:
    """같은 업종 종목코드 목록.

    접두어를 5자리부터 좁게 잡아 보고, 후보가 `min_peers` 에 못 미치면 한 자리씩 줄인다.
    **어느 자릿수에서 찾았는지 함께 돌려준다** — 2자리까지 내려갔다면 "같은 대분류일 뿐"
    이라는 뜻이고, 리포트가 그 사실을 밝혀야 한다.
    """
    mine = industry_of(code)
    if not mine:
        return {"available": False, "reason": "이 종목의 업종코드가 맵에 없다",
                "peers": [], "industry_code": "", "matched_digits": 0}

    mapping = load().get("map", {})
    for digits in range(len(mine), 1, -1):
        prefix = mine[:digits]
        peers = [c for c, v in mapping.items()
                 if c != code and str(v.get("industry_code", "")).startswith(prefix)]
        if len(peers) >= min_peers or digits == 2:
            return {
                "available": bool(peers),
                "peers": peers,
                "industry_code": mine,
                "matched_prefix": prefix,
                "matched_digits": digits,
                # KSIC 는 2자리=대분류 · 3자리=소분류 · 4~5자리=세분류다.
                # 2자리까지 내려갔으면 "같은 업종" 이라 부르기 어려우므로 그 사실을 밝힌다.
                "note": (f"업종코드 {digits}자리({prefix})까지 넓혀서 {len(peers)}곳을 찾았다"
                         + (" — 세분류가 아니라 **대분류** 수준이라 업종이 꽤 넓다" if digits <= 2 else "")),
                "reason": "" if peers else "같은 대분류에도 다른 상장사가 없다",
            }
    return {"available": False, "reason": "같은 업종을 못 찾았다", "peers": [],
            "industry_code": mine, "matched_digits": 0}


def stats() -> Dict:
    data = load()
    return {
        "available": available(),
        "path": str(DATA_FILE),
        "generated_at": data.get("generated_at", ""),
        "total": data.get("total", 0),
        "with_industry": data.get("with_industry", 0),
        "source": data.get("source", ""),
    }
