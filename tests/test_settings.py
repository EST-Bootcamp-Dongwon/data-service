"""실행 환경 분기 (ADR-DS-0003).

`APP_ENV` 하나로 DB 커넥션 전략이 갈린다. **그 분기가 실제로 갈리는지**를 여기서 검사한다.

이 테스트가 지키려는 사고는 조용한 쪽이다 — 배포본이 로컬 설정으로 뜨는 것.
포트·풀·캐시 셋 중 하나만 어긋나도 prepared statement 충돌이 **산발적으로** 나서,
재현이 안 되고 로그만 봐서는 원인을 못 찾는다. 그래서 세 값을 함께 얼려 둔다.

외부 API 도 DB 도 부르지 않는다 — 환경변수를 monkeypatch 해서 분기만 본다.
"""

from __future__ import annotations

import pytest

from app.core import settings

# ADR-DS-0003 §2 — 실행 환경 어휘. 이 둘이 전부다.
APP_ENVS = {"local", "vercel"}


@pytest.fixture
def clean_env(monkeypatch):
    """환경을 비운 상태에서 시작한다.

    개발 기계에 `APP_ENV` 나 `VERCEL` 이 떠 있으면 테스트가 그것에 끌려간다.
    분기를 검사하는 테스트가 환경에 의존하면 검사의 뜻이 사라진다.
    """
    for name in ("APP_ENV", "VERCEL", "VERCEL_ENV", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ==================================================
# 1. APP_ENV 를 고르는 순서 (ADR-DS-0003 §3)
# ==================================================
def test_default_is_local_when_nothing_is_set(clean_env):
    """아무 표식도 없으면 로컬이다. 개발 기계가 기본이다."""
    assert settings.app_env() == "local"


@pytest.mark.parametrize("marker, value", [
    ("VERCEL", "1"),
    ("VERCEL_ENV", "production"),
    ("VERCEL_ENV", "preview"),
])
def test_vercel_is_detected_without_app_env(clean_env, marker, value):
    """⭐ **APP_ENV 를 빼먹어도 배포본을 배포본으로 알아본다.**

    이 자동 감지가 없으면 배포 환경변수를 한 번 빠뜨리는 것만으로 배포본이
    로컬 전략(5432 직결 + 정상 풀)으로 뜬다. 즉사하지 않고 산발적으로만 실패해서
    가장 찾기 어려운 종류의 사고가 된다.
    """
    clean_env.setenv(marker, value)
    assert settings.app_env() == "vercel"
    assert settings.is_vercel() is True


def test_explicit_app_env_wins_over_detection(clean_env):
    """사람이 적은 값이 언제나 이긴다 — 배포본에서 로컬 전략을 일부러 쓰는 경우가 있다."""
    clean_env.setenv("VERCEL", "1")
    clean_env.setenv("APP_ENV", "local")
    assert settings.app_env() == "local"


def test_app_env_is_case_insensitive(clean_env):
    clean_env.setenv("APP_ENV", "VERCEL")
    assert settings.app_env() == "vercel"


def test_unknown_app_env_raises_with_instructions(clean_env):
    """오타를 조용히 local 로 떨어뜨리지 않는다.

    `production`·`prod` 는 사람이 자연스럽게 적을 법한 값이다. 그걸 받아 주는 순간
    명시 지정과 자동 감지가 **둘 다** 무력해진다. 멈추되, 무엇을 해야 하는지 말한다.
    """
    clean_env.setenv("APP_ENV", "production")
    with pytest.raises(ValueError) as excinfo:
        settings.app_env()

    message = str(excinfo.value)
    assert "production" in message               # 무엇이 틀렸는지
    assert "local" in message and "vercel" in message   # 무엇을 써야 하는지


def test_app_env_is_read_fresh_every_call(clean_env):
    """모듈 상수가 아니라 함수여야 하는 이유 그 자체.

    상수로 두면 첫 import 에 얼어붙어, 한 프로세스 안에서 두 갈래를 볼 수 없다.
    """
    assert settings.app_env() == "local"
    clean_env.setenv("APP_ENV", "vercel")
    assert settings.app_env() == "vercel"


def test_app_env_is_always_in_the_vocabulary(clean_env):
    assert settings.app_env() in APP_ENVS


# ==================================================
# 2. 커넥션 전략 — 셋이 한 벌이다 (ADR-DS-0003 §4·§5)
# ==================================================
def test_vercel_strategy_has_all_three(clean_env):
    """⭐ 배포본 설정 셋을 **함께** 검사한다.

    포트 6543 · NullPool · 캐시 두 개 0. 하나만 빠져도 산발적 충돌이 나므로
    따로 검사하면 뜻이 없다. 이 테스트가 그 한 벌을 얼려 둔다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:6543/db")

    db = settings.database_settings()

    assert db.expected_port == 6543
    assert db.use_null_pool is True
    assert db.connect_args["statement_cache_size"] == 0
    assert db.connect_args["prepared_statement_cache_size"] == 0


def test_local_strategy_keeps_the_normal_pool(clean_env):
    """로컬은 아무것도 끄지 않는다 — 직결이라 prepared statement 가 정상이고 더 빠르다."""
    clean_env.setenv("APP_ENV", "local")

    db = settings.database_settings()

    assert db.expected_port == 5432
    assert db.use_null_pool is False
    assert dict(db.connect_args) == {}


def test_the_two_caches_are_both_named(clean_env):
    """손잡이가 둘이라는 사실 자체를 고정한다.

    `statement_cache_size` 만 끄면 SQLAlchemy 방언이 그 위에 둔 두 번째 LRU 가 남아
    **빈도만 낮아진 채 같은 오류가 계속 난다.** 한쪽만 지우는 리팩터링을 막는다.
    """
    assert set(settings.VERCEL_CONNECT_ARGS) == {
        "statement_cache_size",
        "prepared_statement_cache_size",
    }
    assert all(v == 0 for v in settings.VERCEL_CONNECT_ARGS.values())


def test_connect_args_cannot_be_mutated_by_a_caller(clean_env):
    """접속 코드가 실수로 고쳐도 다음 호출이 오염되지 않는다."""
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:6543/db")

    with pytest.raises(TypeError):
        settings.database_settings().connect_args["statement_cache_size"] = 100  # type: ignore[index]


# ==================================================
# 3. DATABASE_URL — 배포본은 기본값으로 때우지 않는다
# ==================================================
def test_local_falls_back_to_the_compose_default(clean_env):
    """로컬은 아무 설정 없이도 떠야 한다. compose.yaml 의 기본값과 같은 문자열이다."""
    clean_env.setenv("APP_ENV", "local")
    assert settings.database_url() == settings.DEFAULT_LOCAL_DATABASE_URL


def test_vercel_without_database_url_raises_with_instructions(clean_env):
    """배포본에서 기본값으로 떨어지면 `@db` 라는 compose 전용 호스트를 물고 뜬다.

    정체 모를 이름 해석 실패가 되므로, 멈추고 무엇을 해야 하는지 말한다.
    """
    clean_env.setenv("APP_ENV", "vercel")
    with pytest.raises(RuntimeError) as excinfo:
        settings.database_url()

    message = str(excinfo.value)
    assert "DATABASE_URL" in message
    assert "6543" in message              # 어떤 포트를 써야 하는지까지


def test_explicit_url_is_used_as_written(clean_env):
    """포트가 어긋나도 **말없이 고치지 않는다** — 적은 값과 붙는 곳이 갈리면 안 된다."""
    clean_env.setenv("APP_ENV", "vercel")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db")

    db = settings.database_settings()
    assert settings.url_port(db.url) == 5432      # 적은 그대로
    assert db.expected_port == 6543               # 기대값은 따로 알려 준다


@pytest.mark.parametrize("url, port", [
    ("postgresql+asyncpg://u:p@host:6543/db", 6543),
    ("postgresql+asyncpg://u:p@host/db", None),
    ("postgresql+asyncpg://host:5432/db", 5432),
    ("postgresql+asyncpg://u:p@[::1]:5432/db", 5432),
    ("not-a-url", None),
])
def test_url_port_extraction(url, port):
    assert settings.url_port(url) == port


# ==================================================
# 4. 비밀번호를 로그에 흘리지 않는다
# ==================================================
@pytest.mark.parametrize("url, expected", [
    ("postgresql+asyncpg://postgres:secret@db:5432/x", "postgresql+asyncpg://postgres:***@db:5432/x"),
    ("postgresql+asyncpg://db:5432/x", "postgresql+asyncpg://db:5432/x"),   # 자격증명 없음
    ("not-a-url", "not-a-url"),
])
def test_mask_url_hides_only_the_password(url, expected):
    """호스트·포트·DB 이름은 남긴다 — "어디에 붙으려 했나"를 로그로 읽어야 한다."""
    assert settings.mask_url(url) == expected


def test_safe_url_does_not_leak_the_password(clean_env):
    clean_env.setenv("APP_ENV", "local")
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:hunter2@db:5432/x")

    safe = settings.database_settings().safe_url()
    assert "hunter2" not in safe
    assert "db:5432" in safe


# ==================================================
# 5. 환경을 읽는 통로가 하나로 남는가 (AGENTS.md · ADR-DS-0003 §1)
# ==================================================
# 새 코드가 `os.getenv` 를 다시 흩뿌리기 시작하면 이 모듈의 존재 이유가 사라진다.
# 지금 있는 세 곳은 **먼저 있던 것**이라 그대로 두고(각각 저장계층·리서치 경계 작업에서
# 함께 옮긴다), 여기 없는 새 호출이 생기면 이 테스트가 알려 준다.
KNOWN_ENV_READERS = {
    "app/core/settings.py",                  # 이 모듈이 통로다
    "app/core/secrets.py",                   # 인증키 — 파일 폴백이 필요해 역할이 다르다
    "app/repositories/krx_store.py",         # KRX_DB_PATH — 저장계층 전환 때 정리 (ADR-DS-0002)
    "app/services/research/stages.py",       # VERCEL 감지 — 리서치 경계 정리 때 (ADR-DS-0007)
}


def test_environment_is_not_read_outside_the_known_places():
    """`os.getenv` · `os.environ` 을 부르는 파일 목록을 얼려 둔다."""
    import re

    from app.core.paths import PROJECT_ROOT

    pattern = re.compile(r"os\.(getenv|environ)")
    found = set()
    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(PROJECT_ROOT).as_posix())

    new = found - KNOWN_ENV_READERS
    assert not new, (
        f"환경변수를 직접 읽는 곳이 새로 생겼다: {sorted(new)}. "
        "새 코드는 app/core/settings.py 의 env()·app_env() 를 거친다 (ADR-DS-0003)."
    )
