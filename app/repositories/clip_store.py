"""자료 보관함 `clip` — Postgres 전용 저장소 (ADR-DS-0008 · ADR-DS-0012 · ADR-DS-0019).

화면에서 본 것을 그 자리에서 담아 두고, **기업과 산업**을 축으로 되찾는 표 하나다.
DDL 은 `sql/init/03-clip.sql` 이 정본이고 이 모듈은 그 표를 읽고 쓴다.

## 왜 Postgres 전용인가 — 그래서 배포본에서는 아직 안 된다

`clip` 표는 Postgres 에만 있다. SQLite 쪽에 같은 표를 파는 선택지는 **레포 규칙이 막는다** —
"지금 SQLite 에 표를 새로 파면 나중에 두 번 옮긴다"(AGENTS.md). 그래서 이 기능은
**로컬에서 먼저 산다.** 배포본은 `DATABASE_URL` 이 없어(S6 이 준다) 쓰기·읽기 모두
거절되고, 그 거절은 **왜 안 되는지와 언제 되는지**를 함께 말한다.

⚠️ **경로는 배포본에서도 등록한다. 실행만 거절한다.** 조건부 등록을 하면 계약 스냅샷이
환경에 따라 갈리고 설명할 자리가 사라진다 — ADR-DS-0017 이 갱신 API 에서 세운 원칙과 같다.

## 앱이 Postgres 에 **쓰는 첫 경로**다

지금까지 앱은 Postgres 를 읽기만 했다(S4·S5). 쓰기는 일회성 적재기(`scripts/load_pg.py`)와
수집(S8 전까지 SQLite)뿐이었다. 그래서 이 모듈이 `db.begin()` 을 쓰는 첫 자리다.

⚠️ `db.begin()` 은 블록이 정상 종료하면 **커밋**한다. 읽기에는 `db.connect()` 를 쓴다 —
읽기가 실수로 쓰기를 확정하는 일이 없어야 한다.

## 본문은 담지 않는다

링크·제목·출처·발행일·내 메모까지다 (ADR-DS-0008). 언론사 본문 저장은 저작권 문제가
실재한다. 이 모듈에는 본문을 받는 인자가 아예 없다 — 없는 것이 곧 규칙이다.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text

from app.core import db

# `clip.kind` 일곱 값 (ADR-DS-0014 §8). DDL 의 `clip_kind_ck` 와 **같아야 한다**.
# 갈래는 여섯인데 값이 일곱인 이유는 `dataset`·`report`·`memo` 가 갈래가 아니라
# **내가 담는 것**이기 때문이다 — 1:1 이 아니다.
KINDS: Tuple[str, ...] = ("news", "filing", "dataset", "report", "memo", "post", "video")

# URL 이 반드시 있어야 하는 종류. DDL 의 `clip_link_needs_url_ck` 와 같은 집합이다.
LINK_KINDS: Tuple[str, ...] = ("news", "filing", "post", "video")

# 담을 수 있는 화면. `app/routers/page_router.py` 의 `PAGES` 에서 온 이름이다.
SCREENS: Tuple[str, ...] = ("dashboard", "market", "research", "krx", "kosis",
                            "yf", "stock", "quant", "timeseries")

# 못 쓸 때 **언제 되는지**. 처방이 "무엇을 하라" 로만 끝나면, 그 무엇을 할 수 없는
# 사람(배포본을 보는 사람)에게는 여전히 막다른 길이다.
WHEN_IT_WORKS = "보관함은 Postgres 에만 있다 — 배포본은 S6(Supabase) 뒤에 쓸 수 있다."

# 응답에 싣는 컬럼과 **순서**. `SELECT *` 를 쓰지 않는 이유는 krx_pg 와 같다 —
# 컬럼을 명시하지 않으면 키 집합이 조용히 달라진다.
CLIP_COLUMNS: Tuple[str, ...] = (
    "clip_id", "kind", "screen", "title", "url", "source",
    "occurred_at", "saved_at", "security_id", "code", "name",
    "industry_code", "industry_source", "note", "tags", "payload",
)

_SELECT = """
  c.clip_id, c.kind, c.screen, c.title, c.url, c.source,
  c.occurred_at, c.saved_at, c.security_id, s.code, s.name,
  c.industry_code, c.industry_source, c.note, c.tags, c.payload
"""
# ⚠️ **LEFT JOIN 이다.** `security_id` 가 NULL 인 clip(거시지표 메모 등)이 정상이고,
#    INNER JOIN 으로 두면 그것들이 목록에서 조용히 사라진다.
_FROM = "clip c LEFT JOIN securities s ON s.security_id = c.security_id"

# 추적 파라미터. 같은 기사를 두 번 담지 않으려면 이것들을 떼고 비교해야 한다.
# ⚠️ 접두사로도 본다 — `utm_` 은 종류가 계속 늘어난다(`utm_source_platform` 등).
TRACKING_PREFIXES: Tuple[str, ...] = ("utm_",)
TRACKING_KEYS: Tuple[str, ...] = (
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "ref", "ref_src",
    "spm", "scid", "cmpid", "trk", "trkid", "yclid", "mkt_tok",
)

_WWW = re.compile(r"^www\.")


# ==================================================
# 1. URL 정규화 — 순수 함수라 DB 없이 검사된다
# ==================================================
def normalize_url(url: Optional[str]) -> Optional[str]:
    """중복 판정에 쓸 `url_key` 를 만든다. 같은 기사를 두 번 담지 않기 위한 것이다.

    떼는 것 넷 — **스킴** · **`www.`** · **추적 파라미터** · **프래그먼트**.
    남기는 것은 호스트(소문자)·경로·나머지 질의다. 질의는 **정렬**한다 —
    같은 링크를 순서만 다르게 받는 일이 흔하고, 정렬하지 않으면 그때마다 새 행이 된다.

    ⚠️ **경로의 끝 슬래시는 뗀다.** `…/123` 과 `…/123/` 은 같은 기사다.
    다만 루트(`/`)는 남긴다 — 떼면 호스트만 남아 서로 다른 사이트가 뭉친다.

    ⚠️ **본문을 보지 않는다.** 정규화는 문자열 연산이고 네트워크를 타지 않는다 —
    링크를 따라가 리다이렉트를 푸는 순간 이 함수가 느려지고 실패할 수 있게 된다.
    """
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    host = _WWW.sub("", (parts.hostname or "").lower())
    if not host:
        return None
    if parts.port:
        host = f"{host}:{parts.port}"

    path = parts.path.rstrip("/") or "/"

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_KEYS
            and not any(k.lower().startswith(p) for p in TRACKING_PREFIXES)]
    query = urlencode(sorted(kept))

    return urlunsplit(("", host, path, query, "")).lstrip("/") or host


# ==================================================
# 2. 쓸 수 있는가 — 막는 것은 환경이 아니라 능력이다
# ==================================================
def availability() -> Dict[str, Any]:
    """지금 이 기능을 쓸 수 있는지와, 못 쓰면 **무엇을 해야 하는지**.

    ⚠️ **환경 이름으로 가르지 않는다** (ADR-DS-0017 과 같은 원칙). `APP_ENV=vercel` 이라서
    막는 것이 아니라 **표에 못 붙어서** 막는다. 그래야 Supabase 를 붙인 날(S6) 이 함수를
    고치지 않아도 배포본에서 그대로 살아난다.
    """
    try:
        rows = _fetch("SELECT to_regclass('clip') IS NOT NULL")
    except Exception as error:
        # ⚠️ **"언제 되는지" 를 여기서 붙인다.** `_fetch()` 는 `OSError` 에만 처방을 얹는데,
        #    배포본은 그 앞에서 죽는다 — `DATABASE_URL` 이 아예 없어 `settings` 가
        #    `RuntimeError` 를 던지므로 그 경로를 안 탄다(실측: 배포본 hints 에 이 줄이
        #    빠져 있었다). 그런데 "언제" 가 가장 필요한 곳이 바로 배포본이다.
        return {
            "available": False,
            "reason": "저장소에 못 붙었다",
            "hints": [*str(error).splitlines(), WHEN_IT_WORKS],
        }
    if not (rows and rows[0][0]):
        return {
            "available": False,
            "reason": "clip 표가 없다",
            "hints": ["빈 볼륨으로 다시 띄우면 sql/init 이 표를 세운다:",
                      "  docker compose --profile local-db down -v && "
                      "docker compose --profile local-db up -d"],
        }
    return {"available": True, "reason": "", "hints": []}


# ==================================================
# 3. 조회
# ==================================================
def _fetch(sql: str, params: Optional[Dict] = None) -> List[Any]:
    """읽기 질의 하나. **예외를 삼키지 않는다** — 처방만 붙여 다시 던진다."""
    async def go():
        async with db.connect() as conn:
            return (await conn.execute(text(sql), params or {})).fetchall()

    try:
        return db.run_sync(go())
    except OSError as exc:
        raise db.unreachable(
            exc, what="자료 보관함(Postgres)",
            # ⚠️ 시세와 달리 **되돌아갈 곳이 없다.** 축약본도 SQLite 표도 없다.
            extra=(WHEN_IT_WORKS,),
        ) from exc


def _write(sql: str, params: Optional[Dict] = None) -> List[Any]:
    """쓰기 질의 하나. 정상 종료하면 **커밋**한다 (`db.begin()`).

    ⚠️ 읽기에 이 함수를 쓰지 않는다. 읽기가 실수로 쓰기를 확정하는 일이 없어야 한다.
    """
    async def go():
        async with db.begin() as conn:
            return (await conn.execute(text(sql), params or {})).fetchall()

    try:
        return db.run_sync(go())
    except OSError as exc:
        raise db.unreachable(
            exc, what="자료 보관함(Postgres)",
            extra=(WHEN_IT_WORKS,),
        ) from exc


def to_row(record: Any) -> Dict:
    """조회 결과 한 줄을 응답 모양으로. 날짜·시각은 문자열로 내린다.

    ⚠️ **`date`·`datetime` 을 그대로 흘리지 않는다.** krx_pg 가 같은 이유로 같은 일을 한다 —
    pydantic `str` 필드는 `date` 를 coerce 하지 않아 500 이 되고, `json.dumps` 는
    `TypeError` 를 내는데 그것을 삼키는 자리가 이 레포에 실재한다(tmp_cache.py:102).
    """
    row = dict(zip(CLIP_COLUMNS, record[:len(CLIP_COLUMNS)], strict=True))
    occurred = row.get("occurred_at")
    row["occurred_at"] = occurred.isoformat() if occurred else None
    saved = row.get("saved_at")
    row["saved_at"] = saved.isoformat() if saved else None
    row["tags"] = list(row.get("tags") or [])
    row["payload"] = row.get("payload") or {}
    return row


def get(clip_id: int) -> Optional[Dict]:
    """한 건. 없으면 None."""
    rows = _fetch(f"SELECT {_SELECT} FROM {_FROM} WHERE c.clip_id = :clip_id",
                  {"clip_id": clip_id})
    return to_row(rows[0]) if rows else None


def _filters(screen: Optional[str], kind: Optional[str], code: Optional[str],
             industry: Optional[str], year: Optional[int], month: Optional[int],
             tag: Optional[str], query: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """`WHERE` 절과 바인딩을 함께 만든다. **두 질의(목록·개수)가 같은 것을 써야 한다.**

    따로 만들면 "3건이라는데 2건만 보인다" 가 되는데, 화면만 봐서는 어느 쪽이 틀렸는지
    알 수 없다. 그래서 한 함수가 만든다.

    ⚠️ **연·월은 `occurred_at`(자료 날짜) 기준이다.** `saved_at`(담은 시각)이 아니다 —
    ADR-DS-0008 이 두 축을 나눈 이유가 그것이고, "날짜별/월별 묶기" 는 자료 날짜 쪽이다.
    """
    where: List[str] = []
    params: Dict[str, Any] = {}
    if screen:
        where.append("c.screen = :screen")
        params["screen"] = screen
    if kind:
        where.append("c.kind = :kind")
        params["kind"] = kind
    if code:
        where.append("s.code = :code")
        params["code"] = code
    if industry:
        # KSIC 접두어로 본다 — '26' 이 '2610'·'2620' 을 함께 잡는다. 부분 인덱스가 그대로 탄다.
        where.append("c.industry_code LIKE :industry")
        params["industry"] = f"{industry}%"
    if year:
        where.append("EXTRACT(YEAR FROM c.occurred_at) = :year")
        params["year"] = year
    if month:
        where.append("EXTRACT(MONTH FROM c.occurred_at) = :month")
        params["month"] = month
    if tag:
        where.append(":tag = ANY(c.tags)")
        params["tag"] = tag
    if query:
        # 제목·메모를 함께 본다. 대소문자 무시.
        where.append("(c.title ILIKE :q OR coalesce(c.note, '') ILIKE :q)")
        params["q"] = f"%{query}%"
    return (" WHERE " + " AND ".join(where)) if where else "", params


def list_clips(*, screen: Optional[str] = None, kind: Optional[str] = None,
               code: Optional[str] = None, industry: Optional[str] = None,
               year: Optional[int] = None, month: Optional[int] = None,
               tag: Optional[str] = None, query: Optional[str] = None,
               page: int = 1, size: int = 50) -> Dict[str, Any]:
    """조건에 맞는 것들을 최신순으로. `{items, total, page, size}`.

    ⚠️ **정렬은 `occurred_at DESC NULLS LAST, clip_id DESC` 다.** 인덱스
    (`clip_screen_kind_idx` 등)가 그 모양으로 서 있고, `clip_id` 를 뒤에 두는 것은
    **같은 날짜 안에서 순서가 미정이 되지 않게** 하기 위해서다 — 페이지를 넘길 때
    같은 행이 두 번 나오거나 빠지는 것이 그렇게 생긴다.
    """
    clause, params = _filters(screen, kind, code, industry, year, month, tag, query)
    total = _fetch(f"SELECT count(*) FROM {_FROM}{clause}", params)[0][0]

    params = dict(params, limit=max(1, size), offset=max(0, (max(1, page) - 1) * max(1, size)))
    rows = _fetch(
        f"SELECT {_SELECT} FROM {_FROM}{clause} "
        "ORDER BY c.occurred_at DESC NULLS LAST, c.clip_id DESC "
        "LIMIT :limit OFFSET :offset",
        params,
    )
    return {"items": [to_row(r) for r in rows], "total": total, "page": page, "size": size}


def facets(*, screen: Optional[str] = None, code: Optional[str] = None) -> Dict[str, Any]:
    """필터 UI 가 쓸 값들 — 종류별 개수 · 연월 목록 · 태그.

    화면이 없는 값을 고르게 두지 않으려고 함께 낸다. "2024년" 을 골랐는데 0건이면
    필터가 고장난 것처럼 보인다.
    """
    clause, params = _filters(screen, None, code, None, None, None, None, None)
    kinds = _fetch(f"SELECT c.kind, count(*) FROM {_FROM}{clause} GROUP BY 1 ORDER BY 2 DESC",
                   params)
    months = _fetch(
        f"SELECT to_char(c.occurred_at, 'YYYY-MM') AS ym, count(*) FROM {_FROM}{clause}"
        + (" AND" if clause else " WHERE") + " c.occurred_at IS NOT NULL "
        "GROUP BY 1 ORDER BY 1 DESC LIMIT 36",
        params,
    )
    # ⚠️ `unnest` 는 **`WHERE` 앞**에 와야 한다. 뒤에 쉼표로 붙이면 `FROM` 목록이 아니라
    #    조건 뒤가 되어 문법 오류다(실측). `CROSS JOIN` 으로 적으면 자리가 헷갈리지 않는다.
    tags = _fetch(
        f"SELECT t, count(*) FROM {_FROM} CROSS JOIN unnest(c.tags) AS t{clause} "
        "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 50",
        params,
    )
    return {
        "kinds": [{"kind": k, "count": n} for k, n in kinds],
        "months": [{"month": m, "count": n} for m, n in months],
        "tags": [{"tag": t, "count": n} for t, n in tags],
    }


# ==================================================
# 4. 쓰기
# ==================================================
def resolve_security(code: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    """종목코드로 `security_id` 와 **자동 유도된 산업코드**를 함께 찾는다.

    ADR-DS-0008 의 "산업은 종목에서 자동 유도 + 수동 보정" 에서 앞쪽이다.
    실측(2026-08-25) — `securities` 2,875종목 중 2,700(94%)이 `industry_code` 를 갖고 있다.
    나머지는 `None` 이 되고, 그것이 정상이다(추정하지 않는다 · ADR-DS-0014).
    """
    if not code:
        return None, None
    rows = _fetch(
        "SELECT security_id, industry_code FROM securities "
        "WHERE code = :code AND NOT is_delisted LIMIT 1",
        {"code": code.strip()},
    )
    return (rows[0][0], rows[0][1]) if rows else (None, None)


def create(*, kind: str, screen: str, title: str, url: Optional[str] = None,
           source: Optional[str] = None, occurred_at: Optional[date] = None,
           code: Optional[str] = None, industry_code: Optional[str] = None,
           note: Optional[str] = None, tags: Sequence[str] = (),
           payload: Optional[Dict] = None) -> Dict:
    """한 건 담는다. 같은 링크를 다시 담으면 **새로 만들지 않고 기존 것을 돌려준다.**

    ⚠️ **중복은 오류가 아니다.** 사람이 같은 기사를 두 번 누르는 것은 실수가 아니라
    "이미 담았나?" 를 확인하는 행동이다. 409 로 막으면 화면이 그것을 오류로 그린다.
    `ON CONFLICT DO NOTHING` 뒤에 기존 행을 찾아 돌려주고, `created` 로 어느 쪽인지 밝힌다.
    (`clip_kind_urlkey_uq` 는 `url_key IS NOT NULL` 부분 유니크라 메모·스냅샷은
     여러 번 담긴다 — DDL 이 의도한 동작이다.)

    ⚠️ **산업은 여기서 자동 유도한다.** 사용자가 직접 준 값이 있으면 그것을 쓰고
    `industry_source='manual'` 로 남긴다 — 나중에 재유도 배치가 덮지 않게 하는 표시다.
    """
    security_id, auto_industry = resolve_security(code)
    if industry_code:
        industry, industry_source = industry_code, "manual"
    else:
        industry, industry_source = auto_industry, "auto"

    params = {
        "kind": kind, "screen": screen, "title": title.strip(),
        "url": url, "url_key": normalize_url(url), "source": source,
        "occurred_at": occurred_at, "security_id": security_id,
        "industry_code": industry, "industry_source": industry_source,
        "note": note, "tags": list(tags), "payload": payload or {},
    }
    rows = _write(
        """
        INSERT INTO clip (kind, screen, title, url, url_key, source, occurred_at,
                          security_id, industry_code, industry_source, note, tags, payload)
        VALUES (:kind, :screen, :title, :url, :url_key, :source, :occurred_at,
                :security_id, :industry_code, :industry_source, :note, :tags,
                CAST(:payload AS jsonb))
        ON CONFLICT (kind, url_key) WHERE url_key IS NOT NULL DO NOTHING
        RETURNING clip_id
        """,
        dict(params, payload=_json(params["payload"])),
    )
    if rows:
        return dict(get(rows[0][0]) or {}, created=True)

    # 부딪혔다 — 이미 담긴 것을 찾아 돌려준다.
    existing = _fetch(
        f"SELECT {_SELECT} FROM {_FROM} WHERE c.kind = :kind AND c.url_key = :url_key",
        {"kind": kind, "url_key": params["url_key"]},
    )
    return dict(to_row(existing[0]), created=False) if existing else {}


def update(clip_id: int, *, note: Optional[str] = None,
           tags: Optional[Sequence[str]] = None,
           industry_code: Optional[str] = None) -> Optional[Dict]:
    """사람이 붙이는 것을 고친다 — 메모 · 태그 · 산업.

    ⚠️ **산업을 고치면 `industry_source='manual'` 이 된다.** 그 표시가 없으면 재유도 배치가
    사람이 고친 값을 덮는다. ADR-DS-0008 이 컬럼을 둔 이유가 이것 하나다.
    """
    sets, params = [], {"clip_id": clip_id}
    if note is not None:
        sets.append("note = :note")
        params["note"] = note
    if tags is not None:
        sets.append("tags = :tags")
        params["tags"] = list(tags)
    if industry_code is not None:
        sets.append("industry_code = :industry_code")
        sets.append("industry_source = 'manual'")
        params["industry_code"] = industry_code
    if not sets:
        return get(clip_id)

    rows = _write(f"UPDATE clip SET {', '.join(sets)} WHERE clip_id = :clip_id "
                  "RETURNING clip_id", params)
    return get(rows[0][0]) if rows else None


def delete(clip_id: int) -> bool:
    """한 건 지운다. 없었으면 False."""
    rows = _write("DELETE FROM clip WHERE clip_id = :clip_id RETURNING clip_id",
                  {"clip_id": clip_id})
    return bool(rows)


def reassign_industries() -> int:
    """산업코드를 종목에서 다시 유도한다. **`manual` 은 건드리지 않는다.**

    ⚠️ `WHERE industry_source = 'auto'` 를 빼면 사람이 고친 값이 조용히 사라진다.
    이 한 줄이 ADR-DS-0008 의 `industry_source` 컬럼이 존재하는 이유 전부다.
    """
    rows = _write(
        """
        UPDATE clip c SET industry_code = s.industry_code
        FROM securities s
        WHERE s.security_id = c.security_id
          AND c.industry_source = 'auto'
          AND c.industry_code IS DISTINCT FROM s.industry_code
        RETURNING c.clip_id
        """
    )
    return len(rows)


def _json(value: Any) -> str:
    """`payload` 를 jsonb 로 넘길 문자열. asyncpg 는 dict 를 jsonb 로 자동 변환하지 않는다."""
    return json.dumps(value, ensure_ascii=False)
