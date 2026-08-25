"""자료 보관함 검사 — ADR-DS-0008 · ADR-DS-0012 · ADR-DS-0019.

**실 DB 없이 도는 것만 담는다.** 표를 실제로 왕복하는 검증은 pytest 밖에 있다
(`invoke check` 가 외부 상태에 기대면 그 명령을 더는 신뢰할 수 없다 — 이 레포의 방침).
여기서 붙드는 것은 셋이다.

1. **어휘가 DDL 과 갈리지 않는가** — `kind` 일곱 값과 링크형 넷은 표와 코드 양쪽에 적혀 있다.
   두 벌이 되면 한쪽만 고쳐진 채로 오래 간다.
2. **`kind` 별 검증이 실제로 막는가** — `payload` 가 jsonb 라 표는 못 막는다.
3. **못 쓸 때 설명할 자리가 있는가** — 붙을 수 없는 환경에서 `status` 는 200 이어야 한다.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.paths import PROJECT_ROOT
from app.repositories import clip_store as store
from app.routers import clip_router

DDL = (PROJECT_ROOT / "sql" / "init" / "03-clip.sql").read_text(encoding="utf-8")


# ==================================================
# 1. 어휘 — 코드와 DDL 이 같은 말을 하는가
# ==================================================
def test_kinds_match_the_ddl_check_constraint():
    """`store.KINDS` 가 `clip_kind_ck` 와 **같은 집합**인가.

    갈리면 화면이 받아 준 값을 표가 거절한다 — 사용자에게는 500 으로 보이고,
    원인은 SQL 제약 위반 메시지 안쪽에 숨는다.
    """
    block = re.search(r"clip_kind_ck\s+CHECK \(kind IN \(([^)]+)\)\)", DDL, re.S)
    assert block, "DDL 에서 clip_kind_ck 를 못 찾았다"
    ddl_kinds = set(re.findall(r"'([a-z]+)'", block.group(1)))
    assert set(store.KINDS) == ddl_kinds, (
        f"코드와 DDL 의 kind 가 다르다.\n  코드만: {sorted(set(store.KINDS) - ddl_kinds)}\n"
        f"  DDL만 : {sorted(ddl_kinds - set(store.KINDS))}"
    )


def test_link_kinds_match_the_ddl_url_constraint():
    """URL 이 필수인 종류가 `clip_link_needs_url_ck` 와 같은가."""
    block = re.search(r"clip_link_needs_url_ck\s+CHECK \(kind NOT IN \(([^)]+)\)", DDL, re.S)
    assert block, "DDL 에서 clip_link_needs_url_ck 를 못 찾았다"
    ddl_link = set(re.findall(r"'([a-z]+)'", block.group(1)))
    assert set(store.LINK_KINDS) == ddl_link


def test_screens_are_real_pages():
    """담을 수 있는 화면이 실제로 있는 화면인가.

    없는 이름을 받아 두면 목록 필터가 영원히 0건인 값을 고르게 한다.
    """
    from app.routers.page_router import PAGES

    real = {name.removesuffix(".html") for name in PAGES.values() if name.endswith(".html")}
    # `/` 는 dashboard.html 이고 `/guide` 는 index.html 이다 — 파일명 기준으로 본다.
    unknown = [s for s in store.SCREENS if s not in real]
    assert not unknown, f"실제 화면이 아닌 screen 값이 있다: {unknown}"


def test_required_payload_keys_only_mention_known_kinds():
    """검증표가 어휘 밖 `kind` 를 말하고 있지 않은가. 말하면 그 규칙은 영원히 안 걸린다."""
    unknown = [k for k in clip_router.REQUIRED_PAYLOAD if k not in store.KINDS]
    assert not unknown, f"모르는 kind 에 검증 규칙이 걸려 있다: {unknown}"


# ==================================================
# 2. URL 정규화 — 같은 기사를 두 번 담지 않는다
# ==================================================
@pytest.mark.parametrize("a, b", [
    # 스킴 · www · 끝 슬래시 · 프래그먼트 · 질의 순서 — 전부 같은 것으로 본다
    ("https://www.example.com/n/1", "http://example.com/n/1/"),
    ("https://example.com/n/1?a=1&b=2", "https://example.com/n/1?b=2&a=1"),
    ("https://example.com/n/1#top", "https://example.com/n/1"),
    # 추적 파라미터는 떼고 본다
    ("https://example.com/n/1?utm_source=x&utm_campaign=y", "https://example.com/n/1"),
    ("https://example.com/n/1?fbclid=abc", "https://example.com/n/1"),
    # `utm_` 은 접두사로 본다 — 종류가 계속 늘어난다
    ("https://example.com/n/1?utm_source_platform=z", "https://example.com/n/1"),
])
def test_the_same_article_normalises_to_one_key(a, b):
    assert store.normalize_url(a) == store.normalize_url(b)


@pytest.mark.parametrize("a, b", [
    # 서로 다른 것을 뭉치면 안 된다
    ("https://example.com/n/1", "https://example.com/n/2"),
    ("https://example.com/", "https://other.com/"),
    ("https://example.com/n/1?id=1", "https://example.com/n/1?id=2"),
])
def test_different_articles_keep_different_keys(a, b):
    assert store.normalize_url(a) != store.normalize_url(b)


def test_root_path_survives():
    """⚠️ 루트의 `/` 는 떼지 않는다. 떼면 호스트만 남아 서로 다른 사이트가 뭉친다."""
    assert store.normalize_url("https://a.com/") == "a.com/"
    assert store.normalize_url("https://a.com/") != store.normalize_url("https://b.com/")


@pytest.mark.parametrize("value", [None, "", "   ", "그냥 글자", "mailto:a@b.c"])
def test_unusable_urls_become_none(value):
    """URL 이 아니면 `None` 이다 — 부분 유니크가 NULL 을 서로 다른 것으로 보므로
    메모·스냅샷은 여러 번 담긴다(DDL 이 의도한 동작)."""
    assert store.normalize_url(value) is None


# ==================================================
# 3. `kind` 별 검증 — 표가 못 보는 것을 라우터가 본다
# ==================================================
@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.mark.parametrize("body, why", [
    ({"kind": "tweet", "screen": "stock", "title": "x"}, "어휘 밖 kind"),
    ({"kind": "memo", "screen": "없는화면", "title": "x", "note": "y"}, "어휘 밖 screen"),
    ({"kind": "news", "screen": "stock", "title": "x"}, "링크형인데 url 이 없다"),
    ({"kind": "memo", "screen": "stock", "title": "x"}, "메모인데 note 가 없다"),
    ({"kind": "filing", "screen": "stock", "title": "x",
      "url": "https://a.com/1"}, "공시인데 rcept_no 가 없다"),
    ({"kind": "dataset", "screen": "stock", "title": "x",
      "payload": {"source": "krx"}}, "스냅샷인데 params 가 없다"),
    ({"kind": "memo", "screen": "stock", "title": ""}, "제목이 비었다"),
])
def test_bad_bodies_are_rejected_before_touching_the_store(client, body, why):
    """⚠️ **422 는 저장소에 붙기 전에 나야 한다.** 검사 환경은 DB 에 붙을 수 없으므로,
    여기서 503 이 나온다면 검증이 핸들러 안쪽으로 밀려난 것이다 — 그러면 배포본에서
    잘못된 입력이 "DB 가 없다" 로 보이고, 사람이 고칠 곳을 못 찾는다.
    """
    res = client.post("/api/clips", json=body)
    assert res.status_code == 422, f"{why} → {res.status_code} {res.text[:200]}"


def test_a_valid_body_gets_past_validation_and_is_stopped_by_the_store(client):
    """모양이 맞으면 검증을 통과하고 **저장소에서** 막힌다(503).

    이 검사가 위 묶음의 짝이다 — 전부 422 면 "검증이 다 막고 있다" 와
    "저장소 단계에 도달하지 못한다" 가 구별되지 않는다.
    """
    res = client.post("/api/clips", json={
        "kind": "memo", "screen": "stock", "title": "제대로 된 메모", "note": "내용",
    })
    assert res.status_code == 503


# ==================================================
# 4. 못 쓸 때 — 설명할 자리가 있는가
# ==================================================
def test_status_answers_200_even_when_the_store_is_unreachable(client):
    """⚠️ **`status` 만은 503 을 내지 않는다.** 화면이 버튼을 잠글지 정하려면
    "왜 못 쓰는지" 를 받아 볼 수 있어야 한다. 여기까지 503 이면 화면은 이유 없이 잠긴다.
    """
    res = client.get("/api/clips/status")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False
    assert body["reason"], "왜 못 쓰는지가 비어 있다"
    assert body["hints"], "무엇을 해야 하는지가 비어 있다 — 막다른 길이다"
    # 화면이 종류·화면 목록을 서버에서 받아야 어휘가 두 벌이 되지 않는다.
    assert set(body["kinds"]) == set(store.KINDS)
    # ⚠️ **"언제 되는지" 가 반드시 있다.** 처방이 "무엇을 하라" 로만 끝나면, 그 무엇을
    #    할 수 없는 사람(배포본을 보는 사람)에게는 여전히 막다른 길이다.
    #    실측 — 배포본은 `DATABASE_URL` 부재로 접속 **전에** 죽어서 이 줄이 빠져 있었다.
    assert any(store.WHEN_IT_WORKS in h for h in body["hints"]), (
        f"언제 쓸 수 있는지가 없다: {body['hints']}"
    )


@pytest.mark.parametrize("path", ["/api/clips", "/api/clips/facets", "/api/clips/1"])
def test_read_paths_refuse_with_a_prescription(client, path):
    """읽기도 못 하면 `503` 이고, **무엇을 해야 하는지**가 담긴다."""
    res = client.get(path)
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["reason"] and detail["hints"]


def test_paths_are_registered_even_where_they_cannot_run():
    """⚠️ 배포본에서도 경로는 **등록**된다. 조건부 등록을 하면 계약 스냅샷이 환경에 따라
    갈리고, 무엇보다 왜 안 되는지 설명할 자리가 사라진다 (ADR-DS-0017 과 같은 원칙).
    """
    registered = {r.path for r in clip_router.router.routes}
    assert registered == {"/api/clips", "/api/clips/status", "/api/clips/facets",
                          "/api/clips/{clip_id}"}


# ==================================================
# 5. 산업 보정 — 사람이 고친 값을 배치가 덮지 않는가
# ==================================================
def test_reassign_never_touches_manual_rows():
    """⚠️ `WHERE industry_source = 'auto'` 한 줄이 `industry_source` 컬럼이 존재하는
    이유 전부다 (ADR-DS-0008). 빠지면 사람이 고친 값이 조용히 사라진다.
    """
    import inspect

    source = inspect.getsource(store.reassign_industries)
    assert "industry_source = 'auto'" in source, "재유도가 manual 행까지 덮는다"


def test_manual_is_stamped_when_a_person_edits_the_industry():
    """산업을 고치면 `manual` 이 함께 찍히는가. 안 찍히면 다음 배치가 되돌린다."""
    import inspect

    source = inspect.getsource(store.update)
    assert "industry_source = 'manual'" in source
