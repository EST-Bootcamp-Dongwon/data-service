"""pytest 공통 픽스처.

이 테스트 묶음의 목적은 하나다 — **리팩터링 전의 동작을 얼려 두는 것.**
엔트리포인트를 `app.main:app` 으로 옮기고 저장 계층을 Postgres 로 바꾸는 동안,
겉으로 드러나는 계약(경로·파라미터·정적 파일 마운트)이 그대로인지 확인한다.

외부 API(KRX·DART·FRED…)를 부르지 않는 것만 담는다. 네트워크에 기대면
"검증이 원래 가끔 빨간불"이라는 나쁜 습관이 생긴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 레포 루트를 import 경로에 넣는다. `pytest` 를 어느 폴더에서 돌리든 같게 동작한다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_app():
    """FastAPI 인스턴스를 가져온다.

    정본은 `app.main:app` 이고 루트 `main.py` 는 그것을 다시 내보내는 shim 이다.
    이동 작업 중에도 테스트가 돌아야 하므로 정본을 먼저 보고 없으면 루트로 물러난다.
    (둘이 같은 객체인지는 test_entrypoint.py 가 따로 검사한다.)
    """
    try:
        from app.main import app as fastapi_app
    except ModuleNotFoundError:
        from main import app as fastapi_app
    return fastapi_app


# ==================================================
# 환경 격리 — 검사가 실 DB 에 붙지 않게 한다 (ADR-DS-0011 §결과 · ADR-DS-0015)
# ==================================================
# S3 까지는 이것이 없어도 무사했다. `app/` 안에서 `settings.database_settings()` 를 부르는
# 파일이 `app/core/db.py` 하나뿐이었고, 그 파일을 **아무도 import 하지 않았기** 때문이다.
#
# S4 가 그 전제를 깬다. 어댑터가 엔진을 부르는 순간 개발자 셸의 값이 그대로 흘러든다.
# 실측(2026-08-25) — `APP_ENV=vercel DATABASE_URL=…supabase… pytest` 를 돌리면
# 그 세 값이 **테스트 안까지 그대로 도착한다.** 그 URL 이 배포 DB 면 `invoke check` 가
# 배포 DB 에 붙는다.
#
# ⚠️ **지우는 것만으로는 부족하다.** `APP_ENV` 를 지우면 `local` 로 떨어지고
# `database_url()` 이 기본값(`@db:5432`)을 주는데, compose 를 띄워 둔 기계에서는
# 그것이 **실재하는 DB** 다. 그래서 붙을 수 없는 주소로 **덮어쓴다.**
DANGEROUS_ENV = (
    "DATABASE_URL",     # 어디에 붙나 — 이것이 Supabase 면 배포 DB 에 붙는다
    "APP_ENV",          # 커넥션 전략을 가른다
    "VERCEL",           # APP_ENV 가 없을 때 배포본으로 판정하게 만든다
    "VERCEL_ENV",
    "STORE_BACKEND",    # 어느 저장소를 읽나 (S4)
    "KRX_DB_PATH",      # SQLite 원본 위치
)

# 즉시 거절되는 주소. DNS 를 타지 않아 실패가 빠르고, 어디에도 실재하지 않는다.
# (`tests/test_db.py` 가 이미 같은 이유로 같은 주소를 쓴다.)
UNREACHABLE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:1/pytest_never"


@pytest.fixture(autouse=True)
def isolate_env(request, monkeypatch):
    """모든 검사에서 환경을 씻는다. **autouse 여야 뜻이 있다.**

    보호가 필요한 쪽은 픽스처를 요청하지 않는 검사들이다 — `test_source_vocabulary` ·
    `test_contract` · `test_entrypoint` 처럼 `krx_store` 읽기 경로를 타는 것들이
    정확히 그쪽이다. 요청해야 도는 픽스처로 두면 그 96개가 그대로 노출된다.

    `test_db.py`·`test_settings.py` 의 `clean_env` 와 겹쳐도 안전하다 — 이쪽이 먼저 돌고
    그쪽 `monkeypatch` 가 나중에 쌓여 이긴다. "아무것도 없을 때" 를 보는 검사도 그대로 통과한다.

    실 DB 가 필요한 검사는 `@pytest.mark.realdb` 로 빠져나간다. 지금 그런 검사는 없다 —
    실 DB 검증은 일부러 pytest 밖에 있다(`scripts/check_db_connection.py`).
    """
    if request.node.get_closest_marker("realdb"):
        return

    for name in DANGEROUS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)

    yield

    # 엔진 싱글턴을 비운다. 앞 검사가 만든 엔진이 다음 검사의 환경으로 오인되면 안 된다.
    # ⚠️ `forget_engine()` 이 아니다 — 참조만 버리면 이미 붙은 커넥션이 남고, GC 가 죽은
    #    루프에서 그것을 닫으려다 `RuntimeError: Event loop is closed` 를 흘린다.
    from app.core import db

    db.shutdown_bridge()


@pytest.fixture(scope="session")
def app():
    return _load_app()


@pytest.fixture(scope="session")
def client(app):
    """TestClient — lifespan 을 실행하지 않는 가벼운 형태로 쓴다.

    with 문을 쓰면 lifespan 이 돌면서 자동완성 색인(15,414종목)을 메모리에 올린다.
    계약 검사에는 필요 없고 느리기만 하므로 생략한다.
    """
    from fastapi.testclient import TestClient

    return TestClient(app)
