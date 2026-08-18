"""출처 표기 어휘 (ADR-DS-0009).

`source` 필드는 **`<provider>-<tier>` 두 토막**이고, tier 는 다섯 값이 전부다.
화면이 이 값을 정확히 비교해 배지·안내 문구를 고르므로(`krx.html` · `stock.html`),
값이 하나만 어긋나도 화면이 조용히 틀린 말을 한다.

저장계층을 Postgres 로 옮길 때(ADR-DS-0002) `bundle` 이 사라지는데, 그때
**남는 값의 뜻이 바뀌지 않았는지**를 이 테스트가 잡는다.

외부 API 를 부르지 않는 것만 담는다 — 층 판정 함수와 화면이 비교하는 문자열이 대상이다.
"""

from __future__ import annotations

import re

import pytest

from app.repositories import krx_store as store

# ADR-DS-0009 §1 — tier 어휘. 이 다섯이 전부다.
TIERS = {"db", "bundle", "derived", "live", "live-memo"}

# ADR-DS-0009 §2 — `source` 필드는 예외 없이 두 토막이다.
SOURCE_PATTERN = re.compile(r"^(krx|yahoo)-(db|bundle|derived|live|live-memo)$")


# ==================================================
# 1. 저장소가 내놓는 층
# ==================================================
def test_tier_is_one_of_the_five():
    """`tier()` 는 저장소 전체 상태. 로컬이든 배포본이든 어휘 안에 있어야 한다."""
    assert store.tier() in TIERS


def test_source_tag_is_two_tokens():
    """`source_tag()` 는 그대로 `source` 필드에 실리는 값이다."""
    assert SOURCE_PATTERN.fullmatch(store.source_tag())


def test_provider_and_tier_split_cleanly():
    """`split("-", 1)` 로 항상 쪼개진다 — tier 안의 하이픈(`live-memo`)이 있어도."""
    provider, tier = store.source_tag().split("-", 1)
    assert provider == store.PROVIDER == "krx"
    assert tier in TIERS


def test_stats_mode_is_bare_tier():
    """ADR-DS-0009 §3 — `mode` 는 접두사 없는 맨 tier 다."""
    assert store.stats()["mode"] in TIERS


def test_status_mode_is_not_recomputed(client):
    """ADR-DS-0009 §4 — 라우터는 `stats()` 의 mode 를 **그대로** 올린다.

    예전에는 `days > 0` 으로 재계산했다. 원본이 비면 `stats()` 가 축약본의 days 를
    채워 주므로 번들 상태가 늘 `cache` 로 접혔고, 💾 캐시 배지에 번들 숫자가 찍혔다.
    """
    body = client.get("/api/krx/status").json()
    assert body["mode"] == store.stats()["mode"]
    assert body["mode"] in TIERS


def test_cache_stats_does_not_carry_mode(client):
    """ADR-DS-0009 §4 — mode 의 통로는 `StatusResponse.mode` 하나다. 두 곳에 실으면 갈라진다."""
    assert "mode" not in client.get("/api/krx/status").json()["cache"]


# ==================================================
# 2. 층을 함께 돌려주는 짝 (ADR-DS-0009 §5)
# ==================================================
# 폴백하는 조회 함수는 층을 호출자에게 알려야 한다. 호출자가 `tier()` 를 따로 부르면
# 틀린다 — 원본이 차 있어도 그 날짜·그 종목만 없으면 축약본으로 내려가기 때문이다.
@pytest.mark.parametrize("call", [
    lambda: store.snapshot_tiered("20260731"),
    lambda: store.series_tiered("005930", days=5),
])
def test_tiered_pairs_return_rows_and_tier(call):
    rows, tier = call()
    assert isinstance(rows, list)
    assert tier in TIERS


@pytest.mark.parametrize("plain, tiered", [
    (lambda: store.snapshot("20260731"), lambda: store.snapshot_tiered("20260731")),
    (lambda: store.series("005930", days=5), lambda: store.series_tiered("005930", days=5)),
])
def test_plain_wrappers_return_the_same_rows(plain, tiered):
    """`snapshot()`·`series()` 는 짝의 얇은 껍데기다. 행이 갈리면 둘 중 하나가 낡은 것이다."""
    assert plain() == tiered()[0]


def test_missing_code_does_not_claim_a_layer_that_has_no_data():
    """어디에도 없으면 "번들에서 왔다" 고 말하지 않는다 — 저장소가 선 층을 밝힌다."""
    rows, tier = store.series_tiered("000000", days=5)   # 존재하지 않는 종목코드
    assert rows == []
    assert tier in TIERS


# ==================================================
# 3. 화면이 정확히 비교하는 값 (ADR-DS-0009 근거 절)
# ==================================================
# 값과 화면이 갈리면 배지가 조용히 틀린 말을 한다. 화면 쪽 표를 여기에 얼려 둔다.
SCREEN_KEYS = {
    "static/pages/krx.html": ["'krx-db'", "'krx-bundle'", "'krx-live'", "'krx-live-memo'"],
    "static/pages/stock.html": ["'krx-db'", "'krx-bundle'", "'yahoo-live'"],
}


@pytest.mark.parametrize("page, keys", SCREEN_KEYS.items())
def test_screens_compare_the_current_vocabulary(page, keys):
    from app.core.paths import PROJECT_ROOT

    text = (PROJECT_ROOT / page).read_text(encoding="utf-8")
    for key in keys:
        assert key in text, f"{page} 가 {key} 를 더 이상 다루지 않는다"
    # 옛 어휘가 남아 있으면 그 분기는 영원히 안 탄다
    for stale in ("'krx-cache'", "'live-cache'", "'precomputed'"):
        assert stale not in text, f"{page} 에 옛 출처 값 {stale} 이 남아 있다"
