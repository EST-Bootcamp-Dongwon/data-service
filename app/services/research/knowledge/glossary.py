"""08강 용어집을 툴팁 사전으로 (명세 §5.6 glossary)

`scripts/build_glossary.py` 가 `voca.md` 를 파싱해 만든 `data/glossary.json` 을 읽는다.
런타임은 강의 자료를 보지 않는다 — 그 폴더는 서브모듈이라 배포 번들에 없다.

    실측 — 용어 427개 (본문 용어 334 · 한자 어원 93) · 180KB · 20개 절

리포트에서 쓰는 방법은 두 가지다.

    lookup("PER")        하나 찾기 (정확일치 → 괄호 제거 일치 → 영문 일치 → 부분일치 순)
    annotate(문장)        문장에서 아는 용어를 찾아 `{term, meaning}` 목록으로 (M6 툴팁이 쓴다)

지키는 것
--------
· **뜻을 지어내지 않는다.** 사전에 없으면 없다고 답한다.
· 같은 용어가 여러 절에 나오면 (실측 17건) **하나를 고르지 않고 둘 다 돌려준다.**
  절마다 맥락이 다르기 때문이다 (예: '상관계수' 가 매크로 절과 퀀트 절에 각각 있다).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

DATA_FILE = Path(__file__).resolve().parents[4] / "data" / "glossary.json"

# 문장에서 용어를 찾을 때 **너무 짧은 낱말은 건너뛴다.** 'A'·'예' 같은 것이 걸리면
# 문장마다 툴팁이 수십 개 붙어 읽기가 더 어려워진다.
MIN_TERM_LENGTH = 2

_cache: Optional[Dict] = None
_index: Optional[Dict[str, List[Dict]]] = None
_lock = threading.Lock()

# 용어 표기에서 괄호 부분을 떼기 위한 것 — `물가상승률(CPI)` → `물가상승률` + `CPI`
PAREN_RE = re.compile(r"[(（]([^)）]*)[)）]")


def load() -> Dict:
    """사전을 메모리에 한 번만 올린다. 파일이 없으면 **빈 사전**을 돌려준다.

    없다고 예외를 던지지 않는다 — 툴팁이 안 뜰 뿐 리서치는 그대로 돌아야 한다.
    """
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            _cache = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {"terms": [], "total": 0, "sections": [], "generated_at": "",
                      "source": "", "unavailable": True}
    return _cache


def available() -> bool:
    return bool(load().get("terms"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _build_index() -> Dict[str, List[Dict]]:
    """찾기용 색인 — 표기·괄호 안 약어·영문을 전부 열쇠로 넣는다."""
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is not None:
            return _index
        index: Dict[str, List[Dict]] = {}

        def put(key: str, row: Dict) -> None:
            key = _norm(key)
            if len(key) < MIN_TERM_LENGTH:
                return
            bucket = index.setdefault(key, [])
            if row not in bucket:
                bucket.append(row)

        for row in load().get("terms", []):
            term = row.get("term", "")
            put(term, row)
            inner = PAREN_RE.search(term)
            if inner:
                put(inner.group(1), row)                      # `물가상승률(CPI)` → `CPI`
                put(PAREN_RE.sub("", term), row)              # → `물가상승률`
            if row.get("english"):
                # 영문 칸에 `Limited Partner (유한책임조합원)` 처럼 설명이 붙는 경우가 있다
                english = PAREN_RE.sub("", row["english"]).strip()
                put(english, row)
        _index = index
    return _index


def lookup(term: str) -> Dict:
    """용어 하나를 찾는다. 없으면 `{"found": False}`.

    같은 표기가 여러 절에 있으면 **전부** 돌려준다 (`entries`). 하나를 임의로 고르면
    맥락이 다른 뜻이 조용히 선택된다.
    """
    key = _norm(term)
    if len(key) < MIN_TERM_LENGTH:
        return {"found": False, "query": term, "reason": "너무 짧아 찾지 않는다"}

    index = _build_index()
    hits = index.get(key)
    match_kind = "정확일치"

    if not hits:
        # 부분일치는 **앞부분이 겹칠 때만** 인정한다.
        #
        # ⚠️ 처음에 `key in k or k in key` 로 짰다가 실측에서 크게 틀렸다 —
        #    `lookup("ROE")` 가 "roe" ⊂ "macroeconomics" 로 걸려 **거시경제**를 돌려줬다.
        #    아무 데나 끼어 있는 글자를 같은 낱말로 보면 없는 뜻을 만들어 낸다.
        candidates = [(k, v) for k, v in index.items()
                      if k.startswith(key) or key.startswith(k)]
        if candidates:
            candidates.sort(key=lambda kv: abs(len(kv[0]) - len(key)))
            hits = candidates[0][1]
            match_kind = f"부분일치 ({candidates[0][0]})"

    if not hits:
        return {"found": False, "query": term,
                "reason": "08강 용어집에 없는 낱말이다 — 뜻을 지어내지 않는다"}

    return {
        "found": True,
        "query": term,
        "match": match_kind,
        "entries": hits,
        "meaning": hits[0].get("meaning", ""),
        "english": hits[0].get("english", ""),
        "hanja": hits[0].get("hanja", ""),
        "example": hits[0].get("example", ""),
        "section": hits[0].get("section", ""),
        "ambiguous": len(hits) > 1,
        "source": load().get("source", ""),
    }


def annotate(text: str, limit: int = 12) -> List[Dict]:
    """문장에서 아는 용어를 찾아 툴팁 목록을 만든다 (M6 리포트가 쓴다).

    긴 낱말부터 찾아 **겹치지 않게** 고른다. 'PER' 과 'PER 밴드' 가 같이 걸리면
    툴팁이 두 겹으로 뜨기 때문이다.
    """
    body = str(text or "")
    if not body or not available():
        return []

    found: List[Dict] = []
    used: List[tuple] = []
    terms = sorted((row.get("term", "") for row in load().get("terms", [])),
                   key=len, reverse=True)
    for term in terms:
        plain = PAREN_RE.sub("", term).strip()
        if len(plain) < MIN_TERM_LENGTH:
            continue
        start = body.find(plain)
        if start < 0:
            continue
        end = start + len(plain)
        if any(not (end <= s or start >= e) for s, e in used):
            continue                                  # 이미 잡힌 구간과 겹친다
        entry = lookup(plain)
        if not entry.get("found"):
            continue
        used.append((start, end))
        found.append({"term": plain, "start": start, "end": end,
                      "meaning": entry["meaning"], "english": entry.get("english", ""),
                      "section": entry.get("section", "")})
        if len(found) >= limit:
            break
    return sorted(found, key=lambda row: row["start"])


def search(keyword: str, limit: int = 20) -> List[Dict]:
    """뜻·영문까지 훑는 자유 검색 (사전 화면용)."""
    needle = _norm(keyword)
    if len(needle) < MIN_TERM_LENGTH:
        return []
    rows = []
    for row in load().get("terms", []):
        haystack = _norm(f"{row.get('term','')}{row.get('english','')}{row.get('meaning','')}")
        if needle in haystack:
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def sections() -> List[Dict]:
    """절별 용어 수 (사전 화면 목차)."""
    counts: Dict[str, int] = {}
    for row in load().get("terms", []):
        key = row.get("section") or "미분류"
        counts[key] = counts.get(key, 0) + 1
    return [{"section": key, "count": value} for key, value in sorted(counts.items())]


def stats() -> Dict:
    data = load()
    return {
        "available": available(),
        "path": str(DATA_FILE),
        "total": data.get("total", 0),
        "sections": len(data.get("sections", [])),
        "generated_at": data.get("generated_at", ""),
        "source": data.get("source", ""),
        "skipped_tables": data.get("skipped_tables", []),
        "note": "없으면 `python3 scripts/build_glossary.py` 로 만든다 (08강 서브모듈 필요)",
    }


__all__ = ["lookup", "annotate", "search", "sections", "stats", "available", "load"]
