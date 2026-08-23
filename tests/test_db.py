"""DB 엔진 계층 (저장계층 전환 S2 · ADR-DS-0011).

`test_settings.py` 가 **무엇으로 붙을지**(포트·풀·캐시·이름)를 얼려 뒀다면, 이 파일은
그 값이 **엔진까지 그대로 도착하는지**를 본다. 값을 정해 놓고 옮기다 흘리면
설정 검사는 초록인데 실물은 틀린 상태가 된다 — 그 틈을 막는 것이 여기 목적이다.

⚠️ **실 DB 를 열지 않는다.** `create_async_engine()` 은 이 시점에 접속하지 않으므로
(첫 `connect()` 에서 붙는다) 엔진을 만들어 놓고 속성만 본다. `conftest.py` 에 DB 격리
픽스처가 없어서, 여기서 실 DB 를 잡으면 `invoke check` 가 사람마다 다른 색을 낸다.

실제로 붙는 검증은 `python3 scripts/check_db_connection.py` 가 따로 맡는다 —
그쪽은 상대(로컬 직결·transaction 모드 풀러·Supabase)를 골라 대 볼 수 있어야 해서
자동 검사에 넣지 않는다.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool

from app.core import db, settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

VERCEL_URL = "postgresql+asyncpg://u:p@host:6543/db"
LOCAL_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/data_service"


@pytest.fixture
def clean_env(monkeypatch):
    """환경을 비우고, 엔진 캐시도 함께 비운다.

    캐시를 안 비우면 앞 테스트가 만든 엔진이 다음 테스트의 환경으로 오인된다 —
    `get_engine()` 이 싱글턴이라 그렇다.
    """
    for name in ("APP_ENV", "VERCEL", "VERCEL_ENV", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    db.forget_engine()
    yield monkeypatch
    db.forget_engine()


# ==================================================
# 1. 설정이 엔진 인자까지 그대로 도착하는가
# ==================================================
def test_vercel_kwargs_carry_the_whole_set(clean_env):
    """⭐ 배포본 전략 **넷이 한 벌**로 엔진 인자에 도착했는지 함께 본다.

    NullPool · 캐시 두 손잡이 0 · **준비구문 이름 유일화**. 하나만 빠져도 transaction
    모드 뒤에서 깨지므로(ADR-DS-0003 rev.2 실측표) 따로 검사하면 뜻이 없다.

    ⚠️ 네 번째(이름)가 뒤늦게 들어왔다. 캐시 둘만으로 충분해 보였던 이유는 **갓 띄운
    풀러에서는 정말로 0건이기 때문**이다 — 잔여물이 쌓인 뒤에야 100% 실패로 드러난다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)

    kwargs = db.engine_kwargs()
    connect_args = kwargs["connect_args"]

    assert kwargs["poolclass"] is NullPool
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0
    assert callable(connect_args["prepared_statement_name_func"])
    assert kwargs["url"] == VERCEL_URL
    # 서버리스는 매번 새 커넥션이라 사전 검사가 왕복만 늘린다.
    assert "pool_pre_ping" not in kwargs


def test_local_does_not_pay_for_unique_names(clean_env):
    """직결에는 이름 충돌이 없다. 기본 이름이 더 싸므로 켜지 않는다."""
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", LOCAL_URL)

    assert "prepared_statement_name_func" not in db.engine_kwargs()["connect_args"]


def test_unique_statement_names_never_repeat():
    """⭐ 이름이 실제로 겹치지 않아야 뜻이 있다.

    카운터를 카운터로 바꿔 놓으면(예: 프로세스 전역 증가값) 서버리스에서는 인스턴스마다
    다시 1부터 시작해 **원래 문제로 되돌아간다.** 그래서 값의 성질을 검사한다.
    """
    names = {db.unique_statement_name() for _ in range(2000)}

    assert len(names) == 2000, "이름이 겹친다 — 커넥션 사이에서 부딪히게 된다"
    assert all(n.startswith("__asyncpg_") and n.endswith("__") for n in names)


def test_local_kwargs_keep_the_pool_and_add_pre_ping(clean_env):
    """로컬은 풀을 그대로 쓰되, 죽은 소켓을 꺼내 쓰지 않도록 사전 검사를 켠다."""
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", LOCAL_URL)

    kwargs = db.engine_kwargs()

    assert "poolclass" not in kwargs          # 기본 풀을 쓴다
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["connect_args"] == {}


def test_connect_args_is_a_copy_not_the_frozen_mapping(clean_env):
    """`settings` 쪽 원본을 엔진에 그대로 넘기지 않는다.

    원본은 `MappingProxyType` 이라 SQLAlchemy 가 병합하면서 실패한다. 사본이어야 하고,
    사본을 고쳐도 다음 호출이 오염되지 않아야 한다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)

    first = db.engine_kwargs()["connect_args"]
    first["statement_cache_size"] = 999                      # 사본이라 고쳐진다

    second = db.engine_kwargs()["connect_args"]
    assert second["statement_cache_size"] == 0               # 원본은 그대로다


def test_prepared_statement_cache_size_is_a_dbapi_argument(clean_env):
    """⭐ **`connect_args` 밖으로 꺼내면 안 된다는 사실 자체를 얼려 둔다.**

    이름이 방언 설정처럼 생겨서 `create_async_engine(url, prepared_statement_cache_size=0)`
    으로 "정리"하고 싶어진다. SQLAlchemy 는 이것을 DBAPI 인자로 다루므로 그러면 그냥
    `TypeError` 다. 그 사실이 여기 적혀 있지 않으면, 다음 사람이 옮겨 보고 원인을 찾느라
    시간을 쓴다.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    with pytest.raises(TypeError) as excinfo:
        create_async_engine(VERCEL_URL, prepared_statement_cache_size=0)

    assert "prepared_statement_cache_size" in str(excinfo.value)

    # 반대로 connect_args 안에 있으면 정상이다.
    engine = create_async_engine(
        VERCEL_URL, connect_args={"prepared_statement_cache_size": 0}
    )
    assert engine is not None


def test_cache_values_are_ints_not_strings(clean_env):
    """⭐ URL 쿼리로 옮기면 **문자열**이 되어 죽는다는 사실을 타입으로 막는다.

    `?statement_cache_size=0` 은 그럴듯해 보이지만, 방언이 int 로 강제하는 키는
    `prepared_statement_cache_size` 하나뿐이다. 맨 `statement_cache_size` 는 `"0"` 인 채로
    asyncpg 에 닿고, 거기서 `"0" < 0` 을 시도해 `TypeError` 로 죽는다.
    `connect_args` 에 파이썬 `int` 로 넣는 것만이 안전하다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)

    connect_args = db.engine_kwargs()["connect_args"]

    for key in ("statement_cache_size", "prepared_statement_cache_size"):
        value = connect_args[key]
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{key} 가 int 가 아니다: {value!r}. URL 쿼리로 옮기면 문자열이 된다."
        )
    # URL 에 캐시 설정이 섞여 있지 않아야 한다 — 자리가 둘로 갈리면 어느 쪽이 이겼는지 모른다.
    assert "statement_cache_size" not in db.engine_kwargs()["url"]


# ==================================================
# 2. 엔진 — 만들기만 하고 붙지 않는다
# ==================================================
def test_building_an_engine_does_not_connect(clean_env):
    """붙을 수 없는 주소로도 엔진은 만들어진다.

    이 성질 덕분에 이 파일 전체가 실 DB 없이 돈다. 만약 생성 시점에 붙는다면
    `invoke check` 가 DB 유무에 따라 색이 갈린다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/nope")

    engine = db.build_engine()
    assert type(engine.pool) is NullPool


def test_get_engine_is_a_singleton_until_forgotten(clean_env):
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", LOCAL_URL)

    first = db.get_engine()
    assert db.get_engine() is first

    db.forget_engine()
    assert db.get_engine() is not first


def test_engine_follows_the_environment_after_forget(clean_env):
    """환경을 바꾸고 캐시를 비우면 새 환경의 전략으로 만들어진다."""
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", LOCAL_URL)
    assert type(db.get_engine().pool) is not NullPool

    db.forget_engine()
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)
    assert type(db.get_engine().pool) is NullPool


# ==================================================
# 3. 포트가 어긋나면 말은 하되 고치지는 않는다
# ==================================================
def test_port_mismatch_warns_with_both_numbers(clean_env):
    """무엇이 적혀 있고 무엇이 기대값인지 **둘 다** 말해야 고칠 수 있다."""
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db")

    warning = db.port_warning()

    assert warning is not None
    assert "5432" in warning and "6543" in warning
    assert "p@" not in warning                # 비밀번호가 섞이지 않는다


def test_matching_port_is_silent(clean_env):
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)
    assert db.port_warning() is None


def test_missing_port_is_not_a_mismatch(clean_env):
    """`host/db` 처럼 포트를 생략한 표기는 기본값을 쓰겠다는 정상적인 형태다."""
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host/db")
    assert db.port_warning() is None


def test_port_is_never_rewritten(clean_env):
    """경고만 하고 **적힌 대로** 붙는다 (ADR-DS-0003 §근거)."""
    written = "postgresql+asyncpg://u:p@host:5432/db"
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", written)

    assert db.engine_kwargs()["url"] == written


# ==================================================
# 4. 진단 — 실패해도 예외를 던지지 않고 할 일을 알려 준다
# ==================================================
def test_ping_reports_failure_without_raising(clean_env):
    """붙지 못하는 주소를 줘도 예외가 아니라 `Probe` 가 온다.

    포트 1 은 즉시 거절되므로 검사가 빨리 끝난다(DNS 를 타지 않는다).
    """
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:hunter2@127.0.0.1:1/x")

    probe = asyncio.run(db.ping(timeout=5.0))

    assert probe.ok is False
    assert probe.error                                   # 무엇이 틀렸는지
    assert "docker compose" in probe.hint                # 무엇을 해야 하는지
    assert "hunter2" not in probe.safe_url               # 비밀번호를 흘리지 않는다
    assert "hunter2" not in "\n".join(probe.as_lines())


def test_prepared_statement_error_gets_its_own_hint(clean_env):
    """준비구문 충돌은 "DB 가 안 떴다"와 할 일이 다르다.

    안내를 뭉뚱그리면 멀쩡한 컨테이너를 재시작하며 시간을 버린다.
    """
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", LOCAL_URL)
    db_settings = settings.database_settings()

    hint = db._failure_hint(db_settings, 'prepared statement "__asyncpg_stmt_1__" already exists')

    assert "transaction" in hint
    assert "docker compose" not in hint                  # 엉뚱한 안내가 섞이지 않는다
    assert "빠진 것" in hint                              # 로컬 전략이니 빠진 것을 짚어야 한다


def test_the_hint_does_not_dead_end_when_everything_is_already_on(clean_env):
    """⭐ **이미 다 켜 놓은 사람에게 "켜라"고 하면 그게 막다른 길이다.**

    배포본 전략을 제대로 켰는데도 준비구문 충돌이 나는 경우가 실재한다 — 앞서 틀린
    설정으로 붙은 커넥션이 풀러 쪽 서버 커넥션에 이름을 남겨 둔 상황이다. 그때 안내가
    "APP_ENV=vercel 로 두라"고 말하면, 이미 vercel 인 사람은 다음에 할 일이 없다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)
    db_settings = settings.database_settings()

    hint = db._failure_hint(db_settings, 'prepared statement "__asyncpg_stmt_1__" already exists')

    assert "빠진 것" not in hint                          # 빠진 것이 없으니 그렇게 말하면 안 된다
    assert "RECONNECT" in hint                            # 실제로 할 수 있는 다음 동작
    assert "APP_ENV=vercel 로 두면" not in hint            # 이미 그렇다


@pytest.mark.parametrize("url, must_not_appear", [
    ("정체불명 문자열 hunter2", "hunter2"),               # 접속 문자열 모양이 아니다
    ("postgresql://u:hunter2@host:5432/db", "hunter2"),  # 드라이버 누락 — 그래도 가려야 한다
    ("postgresql+asyncpg://u:hunter2@host:6543/db", "hunter2"),
])
def test_displayable_url_never_leaks_the_password(url, must_not_appear):
    """⭐ `mask_url()` 보다 한 겹 더 보수적이어야 한다.

    `settings.mask_url()` 은 `scheme://user:pw@host` 모양이 아니면 **원문을 그대로**
    돌려준다(호스트·포트를 읽을 수 있어야 하니 맞는 계약이다). 그런데 진단 출력은 그대로
    화면·로그·이슈에 복사되므로, 알아볼 수 없는 값은 아예 찍지 않는 편이 낫다.
    """
    shown = db.displayable_url(url)
    assert must_not_appear not in shown


@pytest.mark.parametrize("env, url", [
    ({"APP_ENV": "local"}, "postgresql://u:hunter2@host:5432/db"),        # 드라이버 누락
    ({"APP_ENV": "local"}, "정체불명 문자열 hunter2"),                      # URL 이 아니다
    ({"APP_ENV": "vercel"}, None),                                        # DATABASE_URL 없음
    ({"APP_ENV": "production"}, None),                                    # 어휘 밖 값
])
def test_ping_never_raises_and_never_leaks(clean_env, env, url):
    """⭐ 진단 도구가 진단 대신 **트레이스백으로 죽으면** 존재 이유가 없다.

    설정 읽기와 엔진 생성도 실패할 수 있다 — 특히 Supabase 대시보드가 주는 문자열은
    `postgresql://` 로 시작해서(드라이버 접미사가 없다) 엔진 생성에서 psycopg2 를 찾다 죽는다.
    ADR-DS-0011 의 S6 에서 정확히 이 순간을 만나게 된다.
    """
    for key, value in env.items():
        clean_env.setenv(key, value)
    if url is not None:
        clean_env.setenv("DATABASE_URL", url)

    probe = asyncio.run(db.ping(timeout=3.0))          # 예외가 나면 여기서 테스트가 실패한다

    assert probe.ok is False
    assert probe.error and probe.hint                  # 무엇이 틀렸고 무엇을 할지
    assert "hunter2" not in "\n".join(probe.as_lines())


def test_probe_lines_do_not_claim_safety_from_the_flag(clean_env):
    """`caches_disabled` 는 "손잡이가 꺼졌다"까지만 말한다.

    asyncpg 0.30 은 캐시를 꺼도 준비구문에 **이름을 붙인다**(connection.py:656).
    그러므로 이 플래그로 "transaction 모드에서 안전하다"고 단정하면 안 된다 —
    안전 여부는 `scripts/check_db_connection.py` 의 부하 검사가 잰다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", VERCEL_URL)

    probe = db.Probe(
        ok=True, app_env="vercel", safe_url="postgresql+asyncpg://u:***@host:6543/db",
        pool="NullPool", caches_disabled=True, server_version="PostgreSQL 16",
    )
    text = "\n".join(probe.as_lines())

    assert "두 손잡이 다 꺼짐" in text
    assert "안전" not in text


# ==================================================
# 5. 계층 — 이 모듈이 무엇에 묶여 있는가
# ==================================================
# 계층 검사는 **AST 로 한다.** 정규식으로 하면 표기마다 구멍이 난다 —
# `from ...core import db`(점 3개) · `from app.core import (\n    db,\n)`(여러 줄 괄호) ·
# `from app.core import parallel, db`(여러 이름) 이 전부 다른 모양이고, 이 레포는 셋 다 쓴다
# (`app/services/research/stages.py` 가 점 3개 표기를 쓰고, `app/main.py` 가 괄호 표기를 쓴다).
# 파서가 이미 하는 일을 정규식으로 흉내 내지 않는다.


def _imported_from(root: Path, path: Path) -> set[str]:
    """그 파일이 import 하는 모듈을 **절대 점표기**로 모은다. 상대 import 도 풀어 준다.

    `app/services/research/stages.py` 의 `from ...core import db` 는
    `app.core` · `app.core.db` 두 이름으로 돌아온다 — 어느 쪽으로 검사하든 걸리도록.

    `root` 를 인자로 받는 이유는 메타테스트 때문이다 — 가짜 파일을 임시 폴더에 같은
    상대 경로로 만들어 두고 같은 함수로 검사해야, 검사기가 검사되는 셈이 된다.
    """
    parts = path.relative_to(root).with_suffix("").parts
    package = parts[:-1] if path.name != "__init__.py" else parts

    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # 상대 import — 점 개수만큼 올라간다
                base = list(package[: len(package) - node.level + 1])
            else:
                base = []
            if node.module:
                base = base + node.module.split(".")
            if base:
                found.add(".".join(base))
                for alias in node.names:
                    found.add(".".join(base + [alias.name]))
    return found


# 위층 — 엔진 계층은 이쪽을 몰라야 한다. 반대 방향만 허용된다.
UPPER_LAYERS = ("app.repositories", "app.services", "app.routers")
ENGINE_LAYER = "app.core.db"


def test_db_does_not_import_upper_layers():
    """엔진 계층은 저장소·서비스·라우터를 몰라야 한다.

    반대 방향(어댑터가 이 모듈을 쓰는 것)만 허용된다. 여기서 위층을 부르면
    S4 에서 순환 import 가 된다.
    """
    imported = _imported_from(PROJECT_ROOT, PROJECT_ROOT / "app" / "core" / "db.py")
    forbidden = sorted(
        name for name in imported if any(name.startswith(u) for u in UPPER_LAYERS)
    )
    assert not forbidden, f"엔진 계층이 위층을 import 한다: {forbidden}"


# DDL 로 시작하는 SQL. 문자열 **앞의 공백만** 떼고 본다.
# ⚠️ 완벽한 검사가 아니다 — SQL 주석(`-- 설명\nCREATE …`)이나 여러 문장을 이어 붙인
#    문자열은 못 잡는다. 그런 형태를 다 잡으려면 SQL 파서가 필요하고, 그건 이 검사의
#    목적을 넘는다. 여기서 막으려는 것은 `krx_store` 처럼 **평범하게** DDL 을 발행하는
#    코드가 엔진 계층에 들어오는 것이다.
DDL_PREFIXES = ("create ", "drop ", "alter ", "truncate ", "grant ", "revoke ")


def _executable_strings(module_source: str) -> list[str]:
    """모듈에서 **실행되는** 문자열 상수만 모은다. docstring 은 뺀다.

    `test_schema_fitness.py` 가 같은 종류의 검사를 일부러 AST 로 하는 것과 같은 이유다 —
    파일 텍스트에 정규식을 걸면 **설명을 고쳤다는 이유로** 테스트가 빨개진다.
    이 파일의 docstring 에는 `CREATE TABLE IF NOT EXISTS` 가 설명으로 들어 있다.
    """
    tree = ast.parse(module_source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_engine_layer_emits_no_ddl():
    """엔진 계층이 조회 경로에서 표를 만들지 않는다.

    `krx_store` 는 `init_db()` 를 조회마다 부른다(krx_store.py:146 · 호출 9곳). SQLite 에서는
    무해했지만, Postgres 에서 DDL 은 asyncpg 의 타입 OID 캐시를 무효화해
    `InvalidCachedStatementError` 를 부른다 — SQLAlchemy asyncpg 방언 문서가 직접 경고한다.
    스키마는 `sql/init/*.sql` 소관이다.
    """
    source = (PROJECT_ROOT / "app" / "core" / "db.py").read_text(encoding="utf-8")

    offenders = [
        value
        for value in _executable_strings(source)
        if value.lstrip().lower().startswith(DDL_PREFIXES)
    ]
    assert not offenders, f"엔진 계층에 DDL 문자열이 있다: {offenders}"


def test_the_ddl_guard_actually_catches_ddl():
    """⭐ 위 검사가 **장식이 아닌지**를 검사한다.

    문자열 검사는 조용히 무력해지기 쉽다 — 서식이 조금만 달라져도 못 잡으면서
    계속 초록이라 아무도 눈치채지 못한다. 그래서 일부러 위반을 만들어 잡히는지 본다.
    """
    violating = 'from sqlalchemy import text\nq = text("CREATE TABLE IF NOT EXISTS x (n int)")\n'
    caught = [
        value
        for value in _executable_strings(violating)
        if value.lstrip().lower().startswith(DDL_PREFIXES)
    ]
    assert caught, "DDL 검사가 실제 위반을 못 잡는다 — 검사가 장식이 됐다"

    # docstring 안의 같은 문구는 잡히지 않아야 한다(설명을 고쳤다고 빨개지면 안 된다).
    documented = '"""설명: CREATE TABLE IF NOT EXISTS 를 발행하지 않는다."""\nx = 1\n'
    assert not [
        value
        for value in _executable_strings(documented)
        if value.lstrip().lower().startswith(DDL_PREFIXES)
    ]


# ==================================================
# 6. S2 의 경계 — 아직 아무도 부르지 않는다
# ==================================================
# ⚠️ **이 테스트는 S4(읽기 어댑터)에서 지운다.** 지우는 것이 곧 "이제 연결했다"는 표시다.
#    S2 를 이렇게 닫아 두는 이유는 진단 가능성이다 — 엔진과 어댑터를 한 번에 넣으면,
#    접속이 안 될 때 커넥션 설정 탓인지 질의 탓인지 가릴 수가 없다.
def test_nothing_in_app_imports_the_engine_layer_yet():
    # 자기 자신만 뺀다. `path.name != "db.py"` 로 하면 app/ 아래 **어디에 있든**
    # db.py 라는 이름의 파일이 전부 검사에서 빠진다 — 나중에 다른 db.py 가 생기면
    # 그 파일이 조용히 사각지대가 된다.
    engine_layer = PROJECT_ROOT / "app" / "core" / "db.py"
    importers = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "app").rglob("*.py")
        if path != engine_layer and ENGINE_LAYER in _imported_from(PROJECT_ROOT, path)
    )
    assert not importers, (
        f"app/ 안에서 엔진 계층을 import 하는 파일이 생겼다: {importers}. "
        "S4(읽기 어댑터)에 도달했다면 이 테스트를 지운다 (ADR-DS-0011)."
    )


# ⭐ 위 두 검사(§5 계층 · §6 경계)가 **장식이 아닌지**를 함께 못박는다.
#
# 처음에는 정규식으로 썼다가 구멍이 났다 — 점 3개 상대 import(`from ...core import db`)와
# 여러 줄 괄호 표기를 놓쳤다. 둘 다 이 레포가 실제로 쓰는 표기다. 그래서 표기법마다
# **가짜 파일을 만들어** 잡히는지 본다. 위치까지 흉내 내야 상대 import 가 제대로 풀린다.
IMPORT_SPELLINGS = [
    ("app/routers/x.py", "from app.core import db"),
    ("app/routers/x.py", "from app.core import db as db_module"),
    ("app/routers/x.py", "from app.core import settings, db"),
    ("app/routers/x.py", "from app.core.db import get_engine"),
    ("app/routers/x.py", "import app.core.db"),
    ("app/routers/x.py", "from app.core import (\n    settings,\n    db,\n)"),   # 여러 줄 괄호
    ("app/routers/x.py", "def f():\n    from app.core.db import connect"),        # 함수 안 지연 import
    ("app/core/x.py", "from .db import get_engine"),                             # 형제
    ("app/core/x.py", "from . import db"),
    ("app/repositories/x.py", "from ..core.db import get_engine"),               # 점 2개
    ("app/repositories/x.py", "from ..core import db"),
    ("app/services/research/x.py", "from ...core.db import get_engine"),         # 점 3개 ★
    ("app/services/research/x.py", "from ...core import db"),
    ("app/services/research/x.py", "from ...core import parallel, db"),
]


@pytest.mark.parametrize("where, source", IMPORT_SPELLINGS)
def test_the_import_guard_catches_every_spelling(tmp_path, where, source):
    target = tmp_path / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source + "\n", encoding="utf-8")

    imported = _imported_from(tmp_path, target)
    assert ENGINE_LAYER in imported, f"이 표기를 못 잡는다 ({where}): {source!r}"


@pytest.mark.parametrize("where, source", [
    ("app/routers/x.py", "from app.core import settings"),
    ("app/routers/x.py", "import sqlite3  # db 라는 낱말이 주석에 있어도 걸리지 않는다"),
    ("app/routers/x.py", "db_path = 'x'"),
    ("app/repositories/x.py", "from ..core import paths"),
])
def test_the_import_guard_does_not_over_match(tmp_path, where, source):
    """반대로 멀쩡한 줄을 잡으면 그것대로 못 쓰는 검사가 된다."""
    target = tmp_path / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source + "\n", encoding="utf-8")

    assert ENGINE_LAYER not in _imported_from(tmp_path, target)


@pytest.mark.parametrize("where, source", [
    ("app/core/db.py", "from app.repositories import krx_store"),
    ("app/core/db.py", "from app.services.market_chart import series"),
    ("app/core/db.py", "import app.routers.krx_router"),
    ("app/core/db.py", "from ..repositories import krx_store"),
    ("app/core/db.py", "from ..services.research import stages"),
])
def test_the_layer_guard_catches_relative_imports_too(tmp_path, where, source):
    """§5 의 계층 검사도 같은 방식으로 확인한다."""
    target = tmp_path / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source + "\n", encoding="utf-8")

    imported = _imported_from(tmp_path, target)
    assert any(name.startswith(u) for name in imported for u in UPPER_LAYERS), (
        f"이 표기를 못 잡는다: {source!r}"
    )
