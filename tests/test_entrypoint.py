"""엔트리포인트와 정적 파일 마운트를 지킨다.

**이 파일이 잡으려는 사고는 구체적이다.**

이동 전 `main.py` 는 레포 루트에 있어서 `Path(__file__).parent` 한 번이면 루트였다
(main.py:99 `STATIC_DIR`, main.py:108 `PRACTICE_DIR`).
그 파일을 `app/main.py` 로 옮기면 같은 표현이 **`app/` 를 가리킨다.**
그러면 `app/static`·`app/실습` 을 찾다 실패해 화면이 통째로 404 가 된다.
반면 `app/` 아래 다른 모듈들은 이미 `parents[2]` 기준이라 깊이가 파일마다 다르다.

정적 마운트는 라우터가 아니라서 OpenAPI 스냅샷에 잡히지 않는다. 그래서 따로 검사한다.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_static_assets_are_served(client):
    """`/static/assets/app.css` 가 내려온다 — 정적 마운트 기준 경로 검증."""
    response = client.get("/static/assets/app.css")
    assert response.status_code == 200, (
        "정적 파일 마운트가 깨졌다. app/main.py 로 옮긴 뒤 STATIC_DIR 의 "
        "기준 경로(Path(__file__).parent)를 함께 고치지 않았을 가능성이 크다."
    )


def test_static_pages_are_served(client):
    """화면 HTML 이 정적 경로로도 열린다."""
    response = client.get("/static/pages/dashboard.html")
    assert response.status_code == 200


def test_practice_archive_is_mounted(client):
    """`/practice/` 실습 아카이브 — 별도 마운트라 따로 확인한다.

    `실습/` 폴더는 한글 이름이고 `html=True` 로 마운트돼 index.html 을 내려준다.
    """
    if not (PROJECT_ROOT / "실습").exists():
        pytest.skip("실습/ 폴더가 없다")
    response = client.get("/practice/")
    assert response.status_code == 200


def test_page_routes_render(client):
    """대표 화면 라우트가 200 을 준다 — page_router 의 FileResponse 경로 검증."""
    response = client.get("/")
    assert response.status_code == 200


def test_root_main_is_a_shim_of_app_main():
    """루트 `main.py` 와 정본 `app.main` 이 **같은 앱 객체**여야 한다.

    이동이 끝나면 루트 main.py 는 `from app.main import app` 한 줄짜리 shim 이 되고,
    강의에서 쓰는 `uvicorn main:app` 과 정본 `uvicorn app.main:app` 이 같은 것을 가리킨다.
    둘이 갈라지면 배포와 로컬이 서로 다른 앱을 띄우게 된다 — 그걸 막는다.

    이동 전에는 `app/main.py` 가 없으므로 건너뛴다.
    """
    try:
        app_main = importlib.import_module("app.main")
    except ModuleNotFoundError:
        pytest.skip("app/main.py 가 아직 없다 — 엔트리포인트 이동 전")

    root_main = importlib.import_module("main")
    assert root_main.app is app_main.app, (
        "루트 main.py 가 app.main 의 앱을 그대로 내보내지 않는다. "
        "shim 이 아니라 두 번째 FastAPI 인스턴스를 만들고 있을 가능성이 크다."
    )


def test_vercel_entrypoint_matches_pyproject():
    """`pyproject.toml` 의 `[tool.vercel] entrypoint` 가 실제 파일을 가리킨다.

    README·Dockerfile·Vercel 세 곳이 어긋나면 배포가 실패한다(AGENTS.md).
    문서가 아니라 파일 존재로 확인한다.
    """
    import tomllib

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("pyproject.toml 이 아직 없다")

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    entrypoint = config.get("tool", {}).get("vercel", {}).get("entrypoint")
    if entrypoint is None:
        pytest.skip("[tool.vercel] entrypoint 가 아직 없다")

    target = PROJECT_ROOT / entrypoint
    assert target.exists(), f"entrypoint 가 가리키는 {entrypoint} 파일이 없다"


def test_vercel_json_functions_key_matches_entrypoint():
    """`vercel.json` 의 함수 키가 실제 엔트리포인트 파일과 같아야 한다.

    **이걸 놓치면 배포가 실패한다.** Vercel 은 `functions` 패턴이 아무 함수와도
    매칭되지 않으면 빌드를 멈춘다. 엔트리포인트를 `app/main.py` 로 옮기면서
    `vercel.json` 의 키를 `"main.py"` 로 두면 정확히 그 상태가 된다.

    로컬에서는 아무 증상이 없고 push 한 뒤에야 드러나므로 여기서 잡는다.
    """
    import json
    import tomllib

    vercel_json = PROJECT_ROOT / "vercel.json"
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not (vercel_json.exists() and pyproject.exists()):
        pytest.skip("vercel.json 또는 pyproject.toml 이 없다")

    functions = json.loads(vercel_json.read_text(encoding="utf-8")).get("functions", {})
    if not functions:
        pytest.skip("vercel.json 에 functions 설정이 없다")

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    entrypoint = config.get("tool", {}).get("vercel", {}).get("entrypoint")
    if entrypoint is None:
        pytest.skip("[tool.vercel] entrypoint 가 없다")

    assert entrypoint in functions, (
        f"vercel.json 의 함수 키 {list(functions)} 가 엔트리포인트 '{entrypoint}' 와 다르다. "
        "매칭되는 함수가 없으면 Vercel 빌드가 실패한다."
    )
