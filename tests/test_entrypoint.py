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
import re
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


def _vercel_entrypoint() -> str | None:
    """`pyproject.toml` 의 `[tool.vercel] entrypoint` 값. 없으면 None."""
    import tomllib

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return None
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return config.get("tool", {}).get("vercel", {}).get("entrypoint")


def _entrypoint_file(module: str) -> Path:
    """`app.main` → `<루트>/app/main.py`. 패키지면 `__init__.py` 로 떨어진다."""
    base = PROJECT_ROOT / Path(*module.split("."))
    return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")


def test_vercel_entrypoint_is_module_object_form():
    """`[tool.vercel] entrypoint` 는 **`module:object`** 다. 파일 경로가 아니다.

    ⚠️ **이 테스트는 실제 사고를 겪고 다시 쓴 것이다** (2026-08-18).
    예전 판은 `(PROJECT_ROOT / entrypoint).exists()` 로 "파일이 있는가"만 봤다.
    그래서 `"app/main.py"` 라고 적어도 초록불이었고, Vercel 은 그 값을 거부했다.

        Error: "tool.vercel.entrypoint" in "pyproject.toml" is "app/main.py" but no
        matching module file was found. Use "module:object" format (e.g. "main:app")

    빌드가 시작 0.1초 만에 죽으므로 배포 목록에는 **2초짜리 Error** 로만 남고,
    프로덕션 URL 은 마지막 성공본을 계속 서빙한다. 화면을 열어 보는 것으로는 못 잡는다.
    그래서 형식 자체를 여기서 검사한다.

    근거: https://vercel.com/docs/functions/runtimes/python#python-entrypoints
    """
    entrypoint = _vercel_entrypoint()
    if entrypoint is None:
        pytest.skip("[tool.vercel] entrypoint 가 아직 없다")

    assert re.fullmatch(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*", entrypoint), (
        f"entrypoint '{entrypoint}' 가 module:object 형식이 아니다. "
        "'app/main.py' 같은 파일 경로를 적으면 Vercel 빌드가 즉시 실패한다 "
        "(정답은 'app.main:app')."
    )


def test_vercel_entrypoint_resolves_to_a_real_object():
    """entrypoint 가 가리키는 모듈 파일이 있고, 그 안에 그 이름의 객체가 실제로 있다.

    형식만 맞고 대상이 없으면 Vercel 이 같은 자리에서 죽는다("no matching module file").
    """
    entrypoint = _vercel_entrypoint()
    if entrypoint is None:
        pytest.skip("[tool.vercel] entrypoint 가 아직 없다")

    module_path, _, attr = entrypoint.partition(":")

    target = _entrypoint_file(module_path)
    assert target.exists(), f"entrypoint 의 모듈 '{module_path}' 에 해당하는 {target} 가 없다"

    module = importlib.import_module(module_path)
    assert hasattr(module, attr), (
        f"{module_path} 에 '{attr}' 가 없다. Vercel 은 이 이름의 ASGI 앱을 찾는다."
    )


def test_vercel_json_functions_key_is_the_resolved_entrypoint_file():
    """`vercel.json` 의 함수 키는 **해석된 엔트리포인트 파일 경로**여야 한다.

    두 파일이 서로 다른 표기를 쓴다는 점이 함정이다 —
    `pyproject.toml` 은 `app.main:app`(모듈), `vercel.json` 은 `app/main.py`(파일)다.
    Vercel 문서가 `functions` 를 "keyed by your resolved entrypoint file" 로 규정한다.

    키가 아무 함수와도 매칭되지 않으면 빌드가 멈춘다. 로컬에서는 아무 증상이 없고
    push 한 뒤에야 드러나므로 여기서 잡는다.

    근거: https://vercel.com/docs/frameworks/backend/fastapi
    """
    import json

    vercel_json = PROJECT_ROOT / "vercel.json"
    entrypoint = _vercel_entrypoint()
    if not vercel_json.exists() or entrypoint is None:
        pytest.skip("vercel.json 또는 entrypoint 설정이 없다")

    functions = json.loads(vercel_json.read_text(encoding="utf-8")).get("functions", {})
    if not functions:
        pytest.skip("vercel.json 에 functions 설정이 없다")

    module_path, _, _ = entrypoint.partition(":")
    expected = _entrypoint_file(module_path).relative_to(PROJECT_ROOT).as_posix()

    assert expected in functions, (
        f"vercel.json 의 함수 키 {list(functions)} 가 해석된 엔트리포인트 파일 "
        f"'{expected}' 와 다르다. 매칭되는 함수가 없으면 Vercel 빌드가 실패한다."
    )
