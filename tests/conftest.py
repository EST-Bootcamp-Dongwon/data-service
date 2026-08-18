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
