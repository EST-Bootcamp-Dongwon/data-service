"""읽기 어댑터와 `STORE_BACKEND` 스위치 (전환 S4 · ADR-DS-0015).

**이 파일은 DB 없이 돈다.** `invoke check` 가 Postgres 없이도 초록이어야 하기 때문이다.
실제로 붙여서 재는 것은 pytest 밖이다 — `scripts/check_db_connection.py`(접속) 와
`scripts/load_pg.py --verify-only`(자료), 그리고 두 백엔드를 나란히 돌리는 손 검증이다.

여기서 검사하는 것은 셋이다.

1. **값 되돌리기** — Postgres 가 주는 타입을 SQLite 와 같은 모양으로 되돌리는 순수 함수들
2. **스위치** — 어휘·기본값·읽는 시점
3. **이음매** — `krx_store` 의 어느 함수가 어느 백엔드로 가는가 (가짜 어댑터로 확인한다)
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core import settings
from app.repositories import krx_pg, krx_store

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ==================================================
# 1. 값 되돌리기 — 순수 함수라 DB 가 필요 없다
# ==================================================
def test_bas_dd_and_trade_date_round_trip():
    """`YYYYMMDD` 문자열과 `date` 가 서로를 정확히 되돌린다."""
    assert krx_pg.to_trade_date("20260731") == date(2026, 7, 31)
    assert krx_pg.to_bas_dd(date(2026, 7, 31)) == "20260731"


def test_trade_date_is_a_date_object_not_a_string():
    """asyncpg 는 `date` 컬럼에 문자열을 받지 않는다.

    `DataError: 'str' object has no attribute 'toordinal'` 로 **첫 질의부터** 죽는다.
    그래서 바인딩 전에 반드시 `date` 로 바꾼다.
    """
    assert isinstance(krx_pg.to_trade_date("20260731"), date)


def test_decimal_becomes_float():
    """`change_rate` 는 `numeric` 이라 `Decimal` 로 오는데, SQLite 는 `float` 였다.

    ⚠️ 이걸 안 내리면 **가장 조용하게** 깨진다 — `tmp_cache.write()` 의 `json.dumps` 가
    `TypeError` 를 내는데 그 함수가 예외를 삼킨다(tmp_cache.py:102). 캐시가 영원히
    안 써지고 로그도 안 남는다. 화면은 느려질 뿐 정상으로 보인다.
    """
    assert krx_pg.to_float(Decimal("26.8100")) == 26.81
    assert isinstance(krx_pg.to_float(Decimal("26.8100")), float)
    assert krx_pg.to_float(None) is None
    assert krx_pg.to_float(3) == 3          # 이미 숫자면 그대로 둔다


def test_row_has_exactly_the_sqlite_key_set():
    """어댑터 한 줄의 키가 `krx_store` 의 것과 **정확히** 같다.

    `krx_store._rows_to_dicts()` 는 `bas_dd` 를 pop 하고 `date` 를 맨 뒤에 붙인다.
    무심코 `trade_date` 를 남기면 응답에 없던 키가 하나 늘어나는데, pydantic 이 여분
    필드를 잘라 내므로 **화면만 봐서는 아무도 못 알아챈다.**
    """
    record = ("005930", "삼성전자", "KOSPI", "코스피", 1, 2, 3, 4, 5,
              Decimal("1.2300"), 6, 7, 8, 9, date(2026, 7, 31))
    row = krx_pg.to_row(record)

    # SQLite 쪽 계약: COLUMNS 에서 bas_dd 를 빼고 date 를 더한 것
    expected = set(krx_store.COLUMNS) - {"bas_dd"} | {"date"}
    assert set(row) == expected
    assert row["date"] == "2026-07-31"       # `date` 키는 ISO 다
    assert "bas_dd" not in row
    assert "trade_date" not in row
    assert isinstance(row["change_rate"], float)


def test_row_select_column_count_matches_row_columns():
    """SELECT 목록과 `ROW_COLUMNS` 가 어긋나면 `to_row` 가 조용히 밀린다.

    `zip(strict=True)` 가 실행 시점에 잡지만, 그때는 이미 요청 중이다. 여기서 미리 센다.
    """
    selected = [part.strip() for part in krx_pg._ROW_SELECT.replace("\n", " ").split(",")]
    selected = [s for s in selected if s]
    # 마지막 하나(`o.trade_date`)는 `date` 키가 되므로 ROW_COLUMNS 에 없다.
    assert len(selected) == len(krx_pg.ROW_COLUMNS) + 1
    assert selected[-1].endswith("trade_date")


def test_window_columns_cover_every_name_callers_pass():
    """실제 호출처 세 곳이 넘기는 컬럼이 전부 매핑에 있다.

    빠지면 그 화면만 예외로 죽는다. 호출처는 `market_data.py:70` ·
    `market_chart.py:407` · `scripts/build_market_snapshot.py:118` 셋뿐이다.
    """
    used = {
        "code", "bas_dd", "close", "value", "volume",   # market_data
        "change_rate",                                  # market_chart
    }
    assert used <= set(krx_pg.WINDOW_COLUMNS)


def test_window_rejects_unknown_columns():
    """모르는 컬럼은 빈 목록이 아니라 **예외**다.

    빈 목록으로 돌려주면 화면이 조용히 비고, 원인이 저장소 전환으로 보인다.
    SQLite 경로도 같은 경우에 `OperationalError` 로 죽는다(실측) — 같이 죽는 편이 맞다.
    """
    with pytest.raises(ValueError, match="모르는 컬럼"):
        krx_pg.window(days=5, columns=("code", "존재하지않는컬럼"))


def test_window_listed_shares_comes_from_ohlcv_not_securities():
    """그 거래일의 상장주식수여야 한다 (ADR-DS-0010).

    `securities.listed_shares` 는 **최신값**이라, 그쪽을 쓰면 예외 없이 숫자가 나오고
    화면도 정상으로 보이는데 액면분할 종목의 과거 회전율만 10배 틀린다.
    실측으로 780,484행 중 247,267행(31.7%)에서 두 값이 다르다.
    """
    assert krx_pg.WINDOW_COLUMNS["listed_shares"].startswith("o.")
    assert "o.listed_shares" in krx_pg._ROW_SELECT
    assert "s.listed_shares" not in krx_pg._ROW_SELECT


def test_join_excludes_delisted_rows():
    """`NOT is_delisted` 가 JOIN 에 걸려 있다.

    `securities_code_active_uq` 는 **부분** 유니크(`WHERE NOT is_delisted`)라,
    S8 이 폐지 종목을 채우기 시작하면 조건 없는 JOIN 은 행을 두 배로 낸다.
    오늘은 `is_delisted` 가 전부 false 라 안 터진다 — 그래서 지금 걸어 둔다.
    """
    assert "NOT s.is_delisted" in krx_pg._JOIN


# ==================================================
# 2. DDL 금지 — 어댑터는 읽기만 한다
# ==================================================
DDL_PREFIXES = ("create ", "drop ", "alter ", "truncate ", "grant ", "revoke ")


def _executable_strings(source: str) -> list[str]:
    """모듈에서 **실행되는** 문자열만 모은다. docstring 은 뺀다.

    `tests/test_db.py` 와 같은 방식이다 — 파일 텍스트에 정규식을 걸면 **설명을 고쳤다는
    이유로** 검사가 빨개진다. 이 파일의 docstring 에도 DDL 낱말이 설명으로 들어 있다.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_adapter_emits_no_ddl():
    """Postgres 에서 DDL 은 asyncpg 의 타입 OID 캐시를 무효화한다.

    `krx_store` 는 조회마다 `init_db()` 를 부르지만(호출 9곳) 어댑터는 그러면 안 된다.
    스키마는 `sql/init/*.sql` 이 빈 볼륨에서 한 번 세운다.
    """
    source = (PROJECT_ROOT / "app" / "repositories" / "krx_pg.py").read_text(encoding="utf-8")
    offenders = [
        value for value in _executable_strings(source)
        if value.lstrip().lower().startswith(DDL_PREFIXES)
    ]
    assert not offenders, f"어댑터에 DDL 문자열이 있다: {offenders}"


def test_postgres_branch_never_calls_init_db():
    """분기는 `init_db()` **앞**에 있어야 한다.

    뒤에 두면 Postgres 로 읽으면서도 SQLite 표를 만들게 되고, 그 DDL 이 헛되이 돈다.
    각 이음매 함수의 소스에서 `_postgres()` 가 `init_db()` 보다 먼저 나오는지 본다.
    """
    for name in ("_cache_is_empty", "latest_date", "available_dates",
                 "snapshot_tiered", "series_tiered", "window", "stats"):
        source = inspect.getsource(getattr(krx_store, name))
        if "init_db()" not in source:
            continue
        assert source.index("_postgres()") < source.index("init_db()"), (
            f"{name}: 분기가 init_db() 뒤에 있다"
        )


# ==================================================
# 3. 스위치 — 어휘 · 기본값 · 읽는 시점
# ==================================================
def test_default_is_sqlite(monkeypatch):
    """**기본은 sqlite 다.** 뒤집는 것은 S5 의 일이다 (ADR-DS-0011 §2)."""
    monkeypatch.delenv("STORE_BACKEND", raising=False)
    assert settings.store_backend() == "sqlite"
    assert settings.uses_postgres_store() is False


@pytest.mark.parametrize("value, expected", [
    ("sqlite", "sqlite"),
    ("postgres", "postgres"),
    ("POSTGRES", "postgres"),     # 대소문자는 받아 준다
    ("  postgres  ", "postgres"), # env() 가 공백을 뗀다
])
def test_known_values(monkeypatch, value, expected):
    monkeypatch.setenv("STORE_BACKEND", value)
    assert settings.store_backend() == expected


@pytest.mark.parametrize("typo", ["pg", "postgre", "postgresql", "sqlite3", "production"])
def test_typos_raise_instead_of_falling_back(monkeypatch, typo):
    """오타를 조용히 기본값으로 떨어뜨리지 않는다.

    떨어뜨리면 "스위치를 켰다고 믿었는데 실은 SQLite 를 재고 있었다"가 된다.
    그 거짓 음성이 이 전환에서 가장 비싼 실수다 — S2 가 같은 종류의 함정을 한 번 통과했다.
    """
    monkeypatch.setenv("STORE_BACKEND", typo)
    with pytest.raises(ValueError) as caught:
        settings.store_backend()
    # 막다른 길로 만들지 않는다 — 쓸 수 있는 값이 메시지에 있어야 한다.
    assert "sqlite" in str(caught.value) and "postgres" in str(caught.value)


def test_empty_value_is_the_default(monkeypatch):
    """`STORE_BACKEND=` 처럼 비워 두는 구성이 흔하다. 없는 것과 같게 본다."""
    monkeypatch.setenv("STORE_BACKEND", "   ")
    assert settings.store_backend() == "sqlite"


def test_switch_is_read_at_call_time_not_at_import(monkeypatch):
    """⭐ **모듈 상수로 굳으면 검사가 스위치를 뒤집을 방법이 없어진다.**

    `krx_store.DB_PATH` 가 실제로 그렇게 굳어 있어, `KRX_DB_PATH` 를
    `monkeypatch.setenv` 해도 아무 효과가 없다(실측). 그 함정을 되풀이하지 않는다.
    """
    monkeypatch.setenv("STORE_BACKEND", "postgres")
    assert settings.uses_postgres_store() is True
    monkeypatch.setenv("STORE_BACKEND", "sqlite")
    assert settings.uses_postgres_store() is False


# ==================================================
# 4. 이음매 — 어느 함수가 어느 백엔드로 가는가
# ==================================================
# 가짜 어댑터로 확인한다. DB 없이 "분기가 실제로 걸린다"만 본다.
SEAMS = [
    ("_cache_is_empty", "is_empty", lambda: krx_store._cache_is_empty()),
    ("latest_date", "latest_date", lambda: krx_store.latest_date()),
    ("available_dates", "available_dates", lambda: krx_store.available_dates(limit=5)),
    ("snapshot_tiered", "snapshot", lambda: krx_store.snapshot_tiered("20260731")),
    ("series_tiered", "series", lambda: krx_store.series_tiered("005930")),
    ("window", "window", lambda: krx_store.window(days=5)),
    ("stats", "stats", lambda: krx_store.stats()),
]


@pytest.mark.parametrize("store_name, adapter_name, call", SEAMS,
                         ids=[s[0] for s in SEAMS])
def test_postgres_backend_reaches_the_adapter(monkeypatch, store_name, adapter_name, call):
    """스위치를 켜면 이음매 일곱이 전부 어댑터를 부른다.

    **일곱은 한 벌이다.** 하나만 안 넘어가면, 로컬 SQLite 를 지운 개발자 셸에서
    Postgres 는 꽉 차 있는데 `tier()` 만 `bundle` 을 내는 어긋난 상태가 된다.
    """
    monkeypatch.setenv("STORE_BACKEND", "postgres")
    called = []

    def fake(*args, **kwargs):
        called.append(adapter_name)
        # 각 함수가 기대하는 최소 모양. 빈 값이면 폴백 가지로 내려가는데 그것도 정상이다.
        return {"stats": {"days": 0}, "is_empty": True}.get(adapter_name, [])

    monkeypatch.setattr(krx_pg, adapter_name, fake)
    call()
    assert called == [adapter_name], f"{store_name} 이 krx_pg.{adapter_name} 을 안 불렀다"


@pytest.mark.parametrize("store_name, adapter_name, call", SEAMS,
                         ids=[s[0] for s in SEAMS])
def test_sqlite_backend_never_touches_the_adapter(monkeypatch, store_name, adapter_name, call):
    """기본값에서는 어댑터가 **한 번도** 불리지 않는다.

    이것이 "되돌릴 수 있다"의 실질이다 — `STORE_BACKEND` 를 되돌리면 S4 이전과 같아진다.
    """
    monkeypatch.setenv("STORE_BACKEND", "sqlite")

    def explode(*args, **kwargs):
        raise AssertionError(f"sqlite 백엔드인데 krx_pg.{adapter_name} 이 불렸다")

    monkeypatch.setattr(krx_pg, adapter_name, explode)
    call()


def test_derived_readers_follow_without_their_own_branch():
    """`snapshot`·`series`·`universe`·`closes_matrix`·`source_tag` 는 분기하지 않는다.

    전부 모듈 전역 이름으로 이음매 함수를 부르므로 자동으로 따라온다.
    거기까지 분기를 넣으면 이중 분기가 되고, S7 에서 걷어낼 것이 늘어난다.
    """
    for name in ("snapshot", "series", "universe", "closes_matrix", "source_tag", "tier"):
        source = inspect.getsource(getattr(krx_store, name))
        assert "_postgres()" not in source, f"{name} 에 분기가 중복으로 들어갔다"


# ==================================================
# 5. 아직 SQLite 를 직접 읽는 곳 — S5 가 갚아야 할 빚
# ==================================================
# ⚠️ 이 둘은 읽기 표면을 **우회한다.** `krx_store.connect()` 를 열고 `daily_price` 에
#    생 SQL 을 던지므로, `STORE_BACKEND=postgres` 로 켜도 **계속 SQLite 를 읽는다.**
#    예외도 안 나고 화면도 정상으로 보인다 — 두 저장소가 같은 자료를 갖고 있는 동안은.
#
#    S4 의 완료 조건(계약 보존)은 그래서 지금 그대로 만족된다. 그러나 **S5 에서 기본값을
#    뒤집으면** SQLite 가 뒤처지는 순간 한글 종목명 검색만 조용히 옛 자료를 본다.
#    목록을 얼려 두어 **모르는 사이에 늘어나지 않게** 한다.
CONNECT_BYPASSERS = {
    "app/services/stock_service.py",        # :144-159 종목명·코드 해석 (생 SQL 3개)
    "scripts/build_stock_master.py",        # :44-54 자동완성 마스터 빌드
}


def test_the_list_of_sqlite_bypassers_has_not_grown():
    """읽기 표면을 우회해 `daily_price` 를 직접 읽는 파일이 늘어나지 않았는가.

    늘어났다면 그 파일도 S5 의 대상이다. 여기서 걸리면 목록에 더하고 **왜인지 적는다** —
    지우기만 하면 이 검사가 장식이 된다.
    """
    found = set()
    for folder in ("app", "scripts"):
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "daily_price" in text and "store.connect()" in text:
                found.add(path.relative_to(PROJECT_ROOT).as_posix())

    assert found == CONNECT_BYPASSERS, (
        f"SQLite 를 직접 읽는 파일 목록이 바뀌었다.\n"
        f"  새로 생긴 것: {sorted(found - CONNECT_BYPASSERS)}\n"
        f"  사라진 것  : {sorted(CONNECT_BYPASSERS - found)}"
    )


# ==================================================
# 6. 환경 격리가 장식이 아닌지 — conftest 의 autouse 픽스처
# ==================================================
def test_isolation_neutralises_the_developer_shell():
    """개발자 셸의 `DATABASE_URL` 이 검사 안까지 오면 안 된다.

    S3 까지는 이것이 없어도 무사했다 — 엔진 계층을 아무도 import 하지 않았기 때문이다.
    S4 가 그 전제를 깼으므로, 격리가 **실제로 동작하는지**를 여기서 못박는다.
    """
    from tests.conftest import UNREACHABLE_URL

    assert settings.app_env() == "local"
    assert settings.database_url() == UNREACHABLE_URL
    # 붙을 수 없는 주소여야 뜻이 있다 — 지우기만 하면 기본값(@db:5432)이 실재한다.
    assert ":1/" in UNREACHABLE_URL


def test_isolation_covers_every_dangerous_name():
    """씻는 목록에 빠진 것이 없는가.

    `STORE_BACKEND` 가 빠지면 개발자가 켜 둔 스위치로 검사가 돌고,
    `KRX_DB_PATH` 가 빠지면 SQLite 원본 위치가 셸을 따라간다.
    """
    from tests.conftest import DANGEROUS_ENV

    assert {"DATABASE_URL", "APP_ENV", "STORE_BACKEND", "KRX_DB_PATH"} <= set(DANGEROUS_ENV)


# ==================================================
# 7. 의존성이 빠졌을 때 — 조용히 강등되면 안 된다
# ==================================================
def test_missing_sqlalchemy_kills_startup_instead_of_dropping_ten_endpoints():
    """⚠️ ADR-DS-0011 이 지목한 함정을 실제로 밟아 본다.

    `app/main.py:58-64` 의 `try` 는 `ModuleNotFoundError` 를 흡수한다. 어댑터가 그 `try`
    **안쪽** import 체인에만 걸려 있으면, SQLAlchemy 가 빠졌을 때 엔드포인트 10개가
    조용히 사라지고 화면에는 **"야후 파이낸스 기능을 끕니다"** 라는 틀린 안내가 뜬다.
    그리고 `test_contract.py:85` 의 `>= 40` 가드는 51→41 을 **통과시킨다.**

    지금은 `krx_router`(그 `try` 밖)가 `krx_store` → `krx_pg` → `app.core.db` 를 먼저
    끌어오므로 그 자리에서 크게 죽는다. 누가 import 를 `try` 안쪽으로 옮기면 여기가 빨개진다.
    """
    import builtins
    import sys

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise ModuleNotFoundError("No module named 'sqlalchemy'")
        return real_import(name, *args, **kwargs)

    saved = {m: sys.modules[m] for m in list(sys.modules)
             if m.startswith(("app", "sqlalchemy"))}
    for name in saved:
        del sys.modules[name]

    builtins.__import__ = blocked
    try:
        with pytest.raises(ModuleNotFoundError, match="sqlalchemy"):
            import app.main  # noqa: F401
    finally:
        builtins.__import__ = real_import
        # 흉내 내는 동안 반쯤 들어온 모듈을 치우고 원래 것을 되돌린다.
        for name in [m for m in sys.modules if m.startswith(("app", "sqlalchemy"))]:
            del sys.modules[name]
        sys.modules.update(saved)
