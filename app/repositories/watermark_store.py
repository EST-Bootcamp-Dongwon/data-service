"""수집 진행 지점 `watermark` — Postgres 전용 저장소 (ADR-DS-0002 · ADR-DS-0020).

`ohlcv_sync_log` 가 **거래일 단위** 대장이라면 이쪽은 **소스 단위**다. 날짜로 나뉘지 않는
수집물(DART 공시·재무·산업분류·종목마스터)이 여기 들어온다 — `sql/init/01-schema.sql` 의
표 주석이 그 용도를 직접 지목해 두었다.

⚠️ **표는 DDL 이 정본이고 이 모듈은 그 표를 읽고 쓴다.** 컬럼을 늘리지 않는다 —
이 레포에는 **마이그레이션 러너가 없어서**(`sql/init/03-clip.sql` 주석) 컬럼 하나를 더하는
값이 `docker compose down -v` + 780,484행 재적재이고, S6 뒤 배포본에서는 그 선택지가
아예 사라진다. 그래서 구조는 **`source` 이름 규약 + `last_key` + `note`** 에 인코딩한다.

## `source` 이름 규약 (ADR-DS-0020 §3)

`source` 는 자유 문자열 PK 라 **코드 말고는 아무도 지켜 주지 않는다.** 그래서 여기 적는다.

| `source` | `last_key` | `rows` | `status` |
|---|---|---|---|
| `dart_filing:<corp_code>` | 그 종목에서 본 최신 **`rcept_dt`**(`YYYYMMDD`) | 그 회차에 받은 줄 수 | `ok`·`partial`·`error` |
| `dart_filing` | `top:350` 처럼 그 회차의 범위 | 회차 전체 `created` | `ok`·`partial`·`error` |
| `dart_calls:<KST 날짜>` | — | 그 날 **이 수집기가** 태운 호출 수 | `ok` |

## ⚠️ `rows = 0` 은 "아직 안 받았다" 가 아니라 **"받아 봤더니 없었다"** 다

`sql/init/01-schema.sql` 이 `ohlcv_sync_log` 에서 못 박은 그 규칙과 **같은 축**인데,
`watermark` 표에는 그 문장이 어디에도 없다. 없으면 공시가 0건인 종목을 **매 회차 다시
묻는다** — 호출이 그만큼 그냥 사라지고, 그 사실은 아무 데도 안 뜬다.

    행이 있다        → 봤다
    rows=0 · ok     → 봤는데 없었다
    status='error'  → 볼 수 없었다 (고유번호 없음 · DART 장애)

세 번째는 **해소가 사람의 판단**이라 남겨 둔다. 지우면 "안 본 것" 과 구별되지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.core import db

KST = timezone(timedelta(hours=9))

# DDL 의 `watermark.status` CHECK 와 **같은 집합**이어야 한다. 갈리면 배치가 마지막 줄에서
# 제약 위반으로 죽고, 그때는 이미 clip 을 다 담은 뒤라 원인이 엉뚱한 곳으로 보인다.
STATUSES = ("ok", "partial", "error")

# 못 쓸 때 되돌아갈 곳이 없다는 사실은 보관함과 같다 — 처방의 마지막 줄로 함께 싣는다.
WHEN_IT_WORKS = "수집 진행 지점은 Postgres 에만 있다 — 배포본은 S6(Supabase) 뒤에 쓸 수 있다."

_COLUMNS = ("source", "last_synced_at", "last_key", "rows", "status", "note", "updated_at")


def _fetch(sql: str, params: Optional[Dict] = None) -> List[Any]:
    async def go():
        async with db.connect() as conn:
            return (await conn.execute(text(sql), params or {})).fetchall()

    try:
        return db.run_sync(go())
    except OSError as exc:
        raise db.unreachable(exc, what="수집 진행 지점(Postgres)",
                             extra=(WHEN_IT_WORKS,)) from exc


def _write(sql: str, params: Optional[Dict] = None) -> List[Any]:
    async def go():
        async with db.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.fetchall() if result.returns_rows else []

    try:
        return db.run_sync(go())
    except OSError as exc:
        raise db.unreachable(exc, what="수집 진행 지점(Postgres)",
                             extra=(WHEN_IT_WORKS,)) from exc


def _to_row(record: Any) -> Dict[str, Any]:
    """한 줄을 응답 모양으로. **시각은 문자열로 내린다** — `clip_store.to_row()` 와 같은 이유다
    (pydantic `str` 필드가 `datetime` 을 coerce 하지 않고, `json.dumps` 는 `TypeError` 를 낸다).
    """
    row = dict(zip(_COLUMNS, record[:len(_COLUMNS)], strict=True))
    for key in ("last_synced_at", "updated_at"):
        value = row.get(key)
        row[key] = value.isoformat() if value else None
    row["rows"] = int(row["rows"]) if row["rows"] is not None else None
    return row


def get(source: str) -> Optional[Dict[str, Any]]:
    """한 줄. 없으면 None — **그것이 "아직 안 봤다" 는 뜻이다.**"""
    rows = _fetch(f"SELECT {', '.join(_COLUMNS)} FROM watermark WHERE source = :source",
                  {"source": source})
    return _to_row(rows[0]) if rows else None


def get_prefix(prefix: str) -> Dict[str, Dict[str, Any]]:
    """접두어로 여러 줄을 **한 왕복에** 읽는다 — `{source: row}`.

    종목 350개의 진행 지점을 하나씩 물으면 왕복이 350번이고, 다리의 루프가 하나라
    (`db.py` §2-1) 그것이 그대로 시간이 된다.
    """
    rows = _fetch(
        f"SELECT {', '.join(_COLUMNS)} FROM watermark WHERE source LIKE :prefix",
        {"prefix": f"{prefix}%"},
    )
    return {r[0]: _to_row(r) for r in rows}


def upsert(source: str, *, last_key: Optional[str] = None, rows: Optional[int] = None,
           status: str = "ok", note: str = "",
           synced_at: Optional[datetime] = None) -> None:
    """한 줄을 쓰거나 덮는다.

    ⚠️ **`status` 를 어휘 밖 값으로 주지 않는다** — DDL 의 CHECK 가 막지만, 그 실패는
    배치의 **마지막 줄**에서 나기 때문에 원인이 멀리 보인다. 여기서 먼저 세운다.
    """
    if status not in STATUSES:
        raise ValueError(f"status 는 {' · '.join(STATUSES)} 중 하나다. 받은 값: {status!r}")
    _write(
        """
        INSERT INTO watermark (source, last_synced_at, last_key, rows, status, note)
        VALUES (:source, :synced_at, :last_key, :rows, :status, :note)
        ON CONFLICT (source) DO UPDATE SET
            last_synced_at = EXCLUDED.last_synced_at,
            last_key       = COALESCE(EXCLUDED.last_key, watermark.last_key),
            rows           = EXCLUDED.rows,
            status         = EXCLUDED.status,
            note           = EXCLUDED.note,
            updated_at     = now()
        """,
        {"source": source, "synced_at": synced_at or datetime.now(KST),
         "last_key": last_key, "rows": rows, "status": status, "note": note},
    )


def bump_calls(day_kst: str, n: int) -> int:
    """그 날 태운 호출 수를 `n` 만큼 **원자적으로** 늘리고 누적값을 돌려준다.

    ⚠️ **읽고-더하고-쓰지 않는다.** 배치와 화면 버튼이 겹치면 그 사이에 낀 증가분이
    사라지는데, 예산이 실제보다 적게 세어지는 쪽으로 틀린다 — 즉 **한도를 넘기는 쪽**이다.

    ⚠️ 이 값은 **「이 수집기가 쓴 양」이지 DART 잔량이 아니다.** 리서치 화면·대시보드가
    같은 키로 부르는 호출은 여기 안 세어진다. 그래서 예산에 여유(마진)를 크게 둔다 —
    정확한 계수 대신 마진을 사는 것이고, 화면 라벨도 그렇게 적어야 거짓말이 안 된다.
    """
    rows = _write(
        """
        INSERT INTO watermark (source, last_synced_at, rows, status, note)
        VALUES (:source, now(), :n, 'ok', :note)
        ON CONFLICT (source) DO UPDATE SET
            rows           = watermark.rows + EXCLUDED.rows,
            last_synced_at = now(),
            updated_at     = now()
        RETURNING rows
        """,
        {"source": f"dart_calls:{day_kst}", "n": max(0, int(n)),
         "note": "이 수집기가 태운 DART 호출 수 (KST 날짜별)"},
    )
    return int(rows[0][0]) if rows else 0


def calls_today(day_kst: str) -> int:
    """그 날 이 수집기가 태운 호출 수. 없으면 0."""
    row = get(f"dart_calls:{day_kst}")
    return int(row["rows"] or 0) if row else 0


def today_kst() -> str:
    """`2026-08-25` — 호출 계수의 날짜 열쇠. **KST 기준이다**(DART 한도도 그렇다)."""
    return datetime.now(KST).strftime("%Y-%m-%d")


def seconds_until_midnight_kst() -> int:
    """KST 자정까지 남은 초. 429 의 `Retry-After` 가 쓴다."""
    now = datetime.now(KST)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))
