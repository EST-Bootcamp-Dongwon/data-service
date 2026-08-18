"""HTTP 계약을 얼린다 — 리팩터링 회귀 안전망.

파이썬 테스트가 하나도 없는 상태에서 엔트리포인트 이동·저장 계층 교체를 하면
무엇이 깨졌는지 알 방법이 없다. 그래서 **겉으로 드러나는 것부터** 고정한다.

여기서 검사하는 것:
  1. 등록된 경로·메서드 목록      — 라우터를 옮기다 빠뜨리면 잡힌다
  2. 엔드포인트별 쿼리 파라미터    — 페이지네이션을 붙일 때 의도치 않은 변경을 잡는다
  3. OpenAPI 문서가 생성되는지     — 강사님 요건 1번(Swagger)의 최소 보증

검사하지 않는 것: 응답 **값**. 외부 API·SQLite 캐시에 의존하므로 여기서 다루지 않는다.
"""

from __future__ import annotations

# 문서화 대상이 아닌 프레임워크 기본 경로. 목록에서 뺀다.
_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

# 계약은 `app.routes` 가 아니라 **OpenAPI 스키마에서** 뽑는다.
# FastAPI 0.141 의 `include_router` 는 라우터를 `_IncludedRouter` 로 감싸 두고
# 개별 APIRoute 를 app.routes 에 평탄화하지 않는다. 그래서 app.routes 를 훑으면
# 직접 붙인 `/health` 하나만 잡힌다(실측). 스키마 쪽이 실제로 클라이언트에게
# 노출되는 계약 그 자체이기도 하므로 이쪽이 맞다.
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _api_routes(app):
    """문서화된 (메서드, 경로) 목록."""
    schema = app.openapi()
    routes = []
    for path, operations in schema["paths"].items():
        if path in _FRAMEWORK_PATHS:
            continue
        for method in operations:
            if method.lower() in _HTTP_METHODS:
                routes.append(f"{method.upper()} {path}")
    return sorted(routes)


def _query_params(app):
    """엔드포인트별 쿼리 파라미터 이름 목록. 경로 파라미터는 뺀다."""
    schema = app.openapi()
    table = {}
    for path, operations in schema["paths"].items():
        if path in _FRAMEWORK_PATHS:
            continue
        for method, operation in operations.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            names = sorted(
                param["name"]
                for param in operation.get("parameters", [])
                if param.get("in") == "query"
            )
            table[f"{method.upper()} {path}"] = names
    return dict(sorted(table.items()))


def test_route_list_is_frozen(app, snapshot):
    """등록된 경로 목록이 바뀌면 스냅샷이 어긋난다.

    의도한 변경이라면 `pytest --snapshot-update` 로 갱신하고, 그 diff 를 커밋에 남긴다.
    """
    assert _api_routes(app) == snapshot


def test_query_parameters_are_frozen(app, snapshot):
    """엔드포인트별 쿼리 파라미터를 얼린다.

    페이지네이션(`page`·`size`)을 추가하면 여기가 먼저 어긋난다 — 그게 목적이다.
    """
    assert _query_params(app) == snapshot


def test_openapi_document_builds(app):
    """Swagger 문서가 만들어진다 (강사님 요건 1번의 최소 보증).

    응답 모델에 직렬화 불가능한 타입이 섞이면 여기서 곧바로 터진다.
    """
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"]
    # 프레임워크 기본 경로를 뺀 실제 엔드포인트가 있어야 한다.
    documented = set(schema["paths"]) - _FRAMEWORK_PATHS
    assert len(documented) >= 40, f"문서화된 경로가 {len(documented)}개뿐이다"


def test_health_endpoint(client):
    """`/health` — 모든 화면이 상단 배지를 그릴 때 쓴다. 외부 의존이 없다."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
