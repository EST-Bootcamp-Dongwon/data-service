"""실행 환경 설정을 한 곳에서 읽는다 (ADR-DS-0003).

`paths.py` 가 **경로**의 기준점인 것과 같은 자리다. 이 모듈은 **환경**의 기준점이다.
새 코드는 `os.getenv` 를 직접 부르지 않고 여기를 거친다.

## `secrets.py` 와 무엇이 다른가

이름이 둘 다 "설정"처럼 보이지만 **묻는 질문이 다르다.**

| | `secrets.py` | `settings.py` (이 파일) |
|---|---|---|
| 무엇을 읽나 | 외부 API 인증키 (KRX·DART·FRED…) | 이 프로세스가 **어디서 도는가**와 그로부터 갈리는 값 |
| 어디서 찾나 | 환경변수 → `.env` → `.key` | **환경변수만** |
| 왜 그 순서인가 | 강의 실습이 키를 파일에 두고 쓴다 | 실행 환경은 파일로 알 수 없다 — 같은 파일이 로컬과 배포본에 함께 실린다 |
| 없으면 | 그 API 만 503, 서버는 뜬다 | 기본값으로 떨어지거나 **즉시** 실패한다 |
| 값의 성격 | 전부 비밀 (`mask()` 로 가림) | 대부분 비밀이 아니다 — `DATABASE_URL` 만 예외 |

`DATABASE_URL` 은 비밀번호를 품고 있지만 여기 있다. **접속 전략의 일부**이고,
`.key` 처럼 파일로 흘리면 안 되기 때문이다. 로그·화면에 실을 때는 `safe_url()` 을 쓴다.

## 왜 상수가 아니라 함수인가

`paths.py` 는 모듈 상수(`PROJECT_ROOT` 등)다. 경로는 **파일 위치가 정하므로** import 시점에
확정되고 그 뒤로 변할 일이 없다. 환경은 다르다 — 프로세스마다 다르고, 테스트가
`monkeypatch.setenv` 로 바꿔 가며 분기를 확인한다. 상수로 두면 **첫 import 에 얼어붙어**
테스트가 두 갈래 중 한쪽만 보게 된다. 그래서 읽을 때마다 환경을 다시 본다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

# ==================================================
# 1. APP_ENV — 실행 환경 어휘
# ==================================================
# 둘뿐이다. staging 이 생기면 ADR-DS-0003 을 개정하고 여기에 더한다.
LOCAL = "local"
VERCEL = "vercel"
APP_ENVS: tuple[str, ...] = (LOCAL, VERCEL)

# 플랫폼이 항상 넣어 주는 표식. `APP_ENV` 를 빼먹어도 배포본을 배포본으로 알아본다.
# (app/services/research/stages.py:287 이 이미 같은 두 변수를 보고 있다 — 언젠가 이리로 모은다.)
VERCEL_MARKERS: tuple[str, ...] = ("VERCEL", "VERCEL_ENV")

# ==================================================
# 2. 커넥션 전략 상수 (ADR-DS-0003 §4)
# ==================================================
# 로컬은 Postgres 에 직접 붙는다. 배포본은 Supabase 의 **transaction 모드 풀러**를 거친다.
# 포트가 곧 모드다 — 6543 으로 붙는 순간 prepared statement 를 쓸 수 없다.
LOCAL_DB_PORT = 5432
VERCEL_DB_PORT = 6543

# transaction 모드에서 prepared statement 를 끄는 두 손잡이. **둘 다** 꺼야 한다.
#   statement_cache_size            asyncpg 자신의 LRU 캐시
#   prepared_statement_cache_size   SQLAlchemy asyncpg 방언이 그 위에 하나 더 두는 캐시
# 하나만 끄면 **빈도만 줄고 사라지지 않는다.** 그게 "산발적으로 난다"의 정체다.
VERCEL_CONNECT_ARGS: Mapping[str, int] = MappingProxyType({
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
})

# 로컬은 아무것도 끄지 않는다 — 직결이라 prepared statement 가 정상 동작하고, 그게 더 빠르다.
LOCAL_CONNECT_ARGS: Mapping[str, int] = MappingProxyType({})

# compose.yaml:22 의 기본값과 같은 문자열. 로컬은 이것만으로 뜬다.
DEFAULT_LOCAL_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@db:5432/data_service"


def env(name: str, default: str = "") -> str:
    """환경변수 한 개를 읽는다 — **새 코드가 환경을 만지는 유일한 통로.**

    앞뒤 공백을 떼고 돌려준다. 빈 문자열은 "없음"과 같게 본다 —
    compose 의 `${KRX_API_KEY:-}` 처럼 **키를 빈 값으로 넣는 구성**이 흔해서,
    "정의는 됐지만 비어 있다"를 따로 다루면 호출자마다 조건이 갈린다.

    인증키는 여기가 아니라 `app/core/secrets.py` 로 읽는다 (파일 폴백이 필요하다).
    """
    return os.getenv(name, default).strip()


def app_env() -> str:
    """이 프로세스의 실행 환경. `local` 또는 `vercel`.

    **읽는 순서가 곧 결정이다** (ADR-DS-0003 §3).

    1. `APP_ENV` 가 명시돼 있으면 그것을 쓴다 — 사람이 적은 값이 언제나 이긴다.
    2. 없으면 `VERCEL`·`VERCEL_ENV` 를 보고 배포본인지 스스로 알아본다.
    3. 그래도 아니면 `local`.

    2번이 없으면 배포본에서 `APP_ENV` 를 한 번 빠뜨리는 것만으로 **로컬 전략으로 조용히**
    뜬다. 그 결과는 즉사가 아니라 산발적 실패라 원인을 찾기가 매우 어렵다.

    어휘 밖 값이면 예외를 던진다. 오타(`production`·`prod`)를 조용히 `local` 로
    떨어뜨리면 1번과 2번을 모두 무력화한다.
    """
    explicit = env("APP_ENV")
    if explicit:
        value = explicit.lower()
        if value not in APP_ENVS:
            # 막다른 길로 만들지 않는다 — 무엇을 해야 하는지까지 알려준다.
            raise ValueError(
                f"APP_ENV 값 '{explicit}' 을 모른다. 쓸 수 있는 값은 {', '.join(APP_ENVS)} 다.\n"
                f"  로컬 개발·도커  : APP_ENV={LOCAL}\n"
                f"  Vercel 배포본   : APP_ENV={VERCEL}\n"
                "APP_ENV 를 아예 지우면 VERCEL 환경변수를 보고 자동으로 고른다."
            )
        return value

    if any(env(marker) for marker in VERCEL_MARKERS):
        return VERCEL
    return LOCAL


def is_vercel() -> bool:
    """배포본(Vercel 서버리스)인가."""
    return app_env() == VERCEL


@dataclass(frozen=True)
class DatabaseSettings:
    """DB 접속에 필요한 값 묶음. **엔진을 만들지는 않는다** (ADR-DS-0003 §6).

    이 모듈은 SQLAlchemy 를 import 하지 않는다. 풀 클래스를 직접 들고 있으면
    설정을 읽는 것만으로 무거운 의존성이 딸려 오고, 아직 없는 계층에 이 파일이 묶인다.
    그래서 `use_null_pool` 이라는 **사실**만 내놓고 해석은 접속 코드에 맡긴다.
    """

    app_env: str
    url: str
    expected_port: int                  # 이 환경에서 정상인 포트. 검증·안내용이다
    use_null_pool: bool                 # True 면 접속 코드가 NullPool 을 쓴다
    connect_args: Mapping[str, int]     # asyncpg 로 그대로 넘어갈 값

    def safe_url(self) -> str:
        """로그·화면에 실어도 되는 형태. **비밀번호를 가린다.**

        `/health` 같은 곳에 접속 문자열을 그대로 내보내는 사고가 흔하다.
        `postgresql+asyncpg://postgres:postgres@db:5432/x` → `...://postgres:***@db:5432/x`
        """
        return mask_url(self.url)


def mask_url(url: str) -> str:
    """접속 문자열에서 비밀번호만 `***` 로 바꾼다. 나머지는 그대로 둔다.

    호스트·포트·DB 이름은 남겨야 "어디에 붙으려 했는가"를 로그로 읽을 수 있다.
    """
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" not in rest:                  # 자격증명이 없는 형태 (host:port/db)
        return url
    credentials, _, host_part = rest.rpartition("@")
    user, sep, _password = credentials.partition(":")
    if not sep:                          # 비밀번호 없이 사용자만 있는 형태
        return url
    return f"{scheme}://{user}:***@{host_part}"


def database_url() -> str:
    """`DATABASE_URL`. 로컬에서만 기본값으로 떨어진다.

    배포본에서 이 값이 비면 **기본값으로 때우지 않는다.** 로컬 기본값은 `@db` 라는
    compose 안에서만 뜻이 있는 호스트라, 배포본이 그걸 물고 뜨면 정체 모를
    이름 해석 실패가 된다. 무엇을 해야 하는지 말하고 멈추는 편이 낫다.
    """
    url = env("DATABASE_URL")
    if url:
        return url

    if is_vercel():
        raise RuntimeError(
            "DATABASE_URL 이 없다. 배포본에서는 기본값으로 대신하지 않는다.\n"
            "  Vercel 프로젝트 설정 → Environment Variables 에 DATABASE_URL 을 넣는다.\n"
            f"  포트는 {VERCEL_DB_PORT} (transaction 모드 풀러)여야 한다 — "
            f"{LOCAL_DB_PORT} 로 적으면 서버리스에서 커넥션이 남아돈다."
        )
    return DEFAULT_LOCAL_DATABASE_URL


def database_settings() -> DatabaseSettings:
    """지금 환경에 맞는 DB 설정 묶음 (ADR-DS-0003 §4).

    | | `local` | `vercel` |
    |---|---|---|
    | 포트 | 5432 직결 | 6543 transaction 풀러 |
    | 풀 | 정상 풀 | `NullPool` |
    | `statement_cache_size` | 그대로 | `0` |
    | `prepared_statement_cache_size` | 그대로 | `0` |

    ⚠️ **배포본 쪽 셋은 한 벌이다.** 하나만 빠져도 prepared statement 충돌이
    산발적으로 난다. 근거는 ADR-DS-0003 의 근거 절에 있다.
    """
    current = app_env()
    if current == VERCEL:
        return DatabaseSettings(
            app_env=current,
            url=database_url(),
            expected_port=VERCEL_DB_PORT,
            use_null_pool=True,           # 서버리스는 호출 사이에 얼었다 녹는다. 풀을 들고 있을 수 없다
            connect_args=VERCEL_CONNECT_ARGS,
        )
    return DatabaseSettings(
        app_env=current,
        url=database_url(),
        expected_port=LOCAL_DB_PORT,
        use_null_pool=False,              # 직결이라 풀이 그대로 이득이다
        connect_args=LOCAL_CONNECT_ARGS,
    )


def url_port(url: str) -> int | None:
    """접속 문자열에서 포트만 뽑는다. 못 찾으면 None.

    `database_settings().expected_port` 와 맞춰 보는 용도다. **자동으로 고치지 않는다** —
    포트를 말없이 바꾸면 사용자가 적은 값과 실제로 붙는 곳이 갈린다.
    """
    if "://" not in url:
        return None
    _, _, rest = url.partition("://")
    host_part = rest.rpartition("@")[2] if "@" in rest else rest
    host_part = host_part.split("/", 1)[0]      # 뒤의 /dbname 을 떼어낸다
    if host_part.startswith("["):               # IPv6 리터럴 [::1]:5432
        host_part = host_part.partition("]")[2]
    _, sep, port = host_part.rpartition(":")
    if not sep or not port.isdigit():
        return None
    return int(port)
