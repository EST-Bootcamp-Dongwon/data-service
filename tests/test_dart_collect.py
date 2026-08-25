"""DART 공시 자동수집 검사 — ADR-DS-0020.

**실 DB·네트워크 없이 도는 것만 담는다.** 표를 실제로 왕복하는 검증은 `invoke collect` 로
사람이 돌린다 — 검증 명령(`invoke check`)이 외부 API 를 부르고 표에 쓰면 그 명령을 더는
신뢰할 수 없다(이 레포의 방침).

여기서 붙드는 것은 다섯이다.

1. **매핑이 계약대로인가** — `payload` 열쇠 이름과 `screen`·`url` 규칙은 멱등성의 근거다.
2. **①공시와 ②정기보고서를 가르는 규칙이 실제 제목에서 맞는가** — 정정본·유사 제목.
3. **한 종목 실패가 배치를 죽이지 않고, 계통 오류는 죽이는가.**
4. **막는 것이 환경이 아니라 능력인가** — 세 사유가 갈라져 있고 처방이 각각 다른가.
5. **수집기가 시세 읽기 경로를 타지 않는가** — AST 로 본다(산문 검색이 아니다).
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.clients import dart_data
from app.core.paths import PROJECT_ROOT
from app.repositories import clip_store
from app.routers import clip_router, collect_router
from app.services import dart_collector as dc

DDL = (PROJECT_ROOT / "sql" / "init" / "03-clip.sql").read_text(encoding="utf-8")
KST = timezone(timedelta(hours=9))


# ==================================================
# 0. 네트워크 차단 — **이 파일에서 DART 를 실제로 부르면 실패다**
# ==================================================
# ⚠️ `tests/conftest.py` 의 `isolate_env` 는 이것을 막아 주지 못한다. `DANGEROUS_ENV` 에
#    `DART_API_KEY` 가 없고, 있다 해도 `secrets.load_key` 가 `.key` **파일**까지 보므로
#    환경변수를 지우는 것으로는 소용이 없다 — 개발 기계에는 그 파일이 실재한다.
#    그래서 환경이 아니라 **함수**를 막는다.
@pytest.fixture(autouse=True)
def _no_dart(monkeypatch):
    def boom(*_args, **_kwargs):
        pytest.fail("DART 를 실제로 불렀다 — 이 검사는 네트워크를 타면 안 된다")

    monkeypatch.setattr(dart_data, "_call", boom)


def code_only(obj) -> str:
    """설명문과 주석을 뺀 **실제 코드**만. 소스 검사는 이것으로 한다.

    ⚠️ 이 레포의 설명문에는 금지 낱말이 잔뜩 들어 있다 — `capability()` 의 docstring 은
    "`APP_ENV` 를 보지 않는다" 라고 적어 두었고, `filing_kind()` 는 "`category` 를 쓸 수
    없다" 를 설명한다. 원문을 그대로 grep 하면 **설명을 위반으로 읽는다**(실측 2건).
    같은 이유로 import 검사는 AST 로 본다.
    """
    tree = ast.parse(inspect.getsource(obj))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def row(**over):
    """DART 공시 한 줄의 기본 모양. `dart_data._fetch_disclosures_uncached` 가 만드는 것과 같다."""
    base = {
        "rcept_no": "20260821000616",
        "report_name": "주요사항보고서(자기주식취득결정)",
        "filer": "삼성전자",
        "date": "2026-08-21",
        "remark": "",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260821000616",
        "public_type": "B",
        "public_type_name": "주요사항보고",
        "category": "자본",
    }
    base.update(over)
    return base


# ==================================================
# 1. 매핑 — 계약대로인가
# ==================================================
def test_a_disclosure_becomes_a_clip_row():
    got = dc.to_clip(row(), code="005930")
    assert got["kind"] == "filing"
    assert got["screen"] == "stock"
    assert got["source"] == "DART"
    assert got["code"] == "005930"
    assert got["occurred_at"] == date(2026, 8, 21)
    assert got["url"] == row()["url"]
    assert got["payload"]["rcept_no"] == "20260821000616"
    assert got["payload"]["collected_by"] == dc.COLLECTOR_TAG


def test_the_requested_code_wins_over_what_dart_returns():
    """⚠️ DART 가 주는 `stock_code` 는 그 법인의 **보통주** 코드다.

    우선주로 물었을 때 그것을 쓰면 공시가 딴 종목에 붙는데, 화면에는 정상으로 보인다.
    """
    got = dc.to_clip(row(stock_code="005930"), code="005935")
    assert got["code"] == "005935"


def test_the_industry_is_left_for_automatic_derivation():
    """⚠️ 수집기가 `industry_code` 를 주면 `industry_source='manual'` 이 되어
    `reassign_industries()` 가 영영 못 고친다 — 그 컬럼이 있는 이유가 통째로 사라진다.
    """
    assert "industry_code" not in dc.to_clip(row(), code="005930")


def test_the_note_is_left_empty_for_a_person():
    """메모는 사람이 붙이는 자리다. 목록 검색(`q`)이 제목과 메모를 함께 보므로
    자동 문구가 들어가면 사람이 쓴 것이 그 안에 묻힌다."""
    assert dc.to_clip(row(), code="005930")["note"] is None


@pytest.mark.parametrize("bad", [
    {"rcept_no": ""},          # 접수번호가 없으면 원문을 되찾을 수 없다
    {"url": ""},               # 링크형이라 URL 이 필수다 (DDL 도 막는다)
    {"report_name": "   "},    # 제목이 비면 목록에서 아무것도 못 읽는다
])
def test_unusable_rows_are_dropped_not_raised(bad):
    """⚠️ **예외를 던지지 않는다.** 한 줄 때문에 그 종목 전체가 사라지면 안 된다."""
    assert dc.to_clip(row(**bad), code="005930") is None


@pytest.mark.parametrize("bad_date", ["2026-02-30", "0000-00-00", "2026-13-01"])
def test_a_date_that_looks_right_but_is_not_does_not_kill_the_batch(bad_date):
    """⚠️ **모양과 유효성은 다른 축이다.** `ISO_DATE_RE` 를 통과하는 `2026-02-30` 이
    `date.fromisoformat` 에서 `ValueError` 를 내는데, 그것을 안 잡으면 **한 줄 때문에
    배치 전체가 죽는다** — 이 함수에는 예외를 잡는 자리가 없다.
    """
    got = dc.to_clip(row(date=bad_date), code="005930")
    assert got is not None and got["occurred_at"] is None


def test_a_broken_date_does_not_throw_away_the_row():
    """⚠️ `_format_date` 는 8자리 숫자가 아니면 **원문을 그대로 흘린다.**

    날짜를 못 읽어도 접수번호와 링크는 살아 있으므로 담는다. 날짜만 비운다.
    """
    got = dc.to_clip(row(date="알 수 없음"), code="005930")
    assert got is not None and got["occurred_at"] is None


def test_the_payload_key_matches_the_ddl_comment():
    """⚠️ 클라이언트는 `report_name` 이고 DDL 주석은 `report_nm` 이다. **표 쪽이 계약이다.**

    갈리면 나중에 그 열쇠로 찾는 코드가 조용히 빈 값을 얻는다.
    """
    assert "report_nm" in DDL, "DDL 주석이 바뀌었다 — 어느 쪽이 정본인지 다시 정한다"
    assert "report_nm" in dc.to_clip(row(), code="005930")["payload"]


# ==================================================
# 1-b. 보안 — 인증키가 표에 저장되지 않는가
# ==================================================
def test_an_opendart_api_url_is_refused_twice():
    """⚠️⚠️ OpenDART **API** 주소에는 인증키(`crtfc_key`)가 질의로 붙는다.

    `normalize_url()` 은 그것을 추적 파라미터로 보지 않아 **지우지 않는다.** 담기면
    인증키가 `clip.url` 에 저장되고 `GET /api/clips` 응답으로 밖으로 나간다.
    수집기와 저장소 **양쪽**이 막아야 다음 수집기까지 안전하다.
    """
    leaky = "https://opendart.fss.or.kr/api/list.json?crtfc_key=abcd1234&corp_code=00126380"
    assert dc.to_clip(row(url=leaky), code="005930") is None
    assert clip_store.banned_host(leaky) == "opendart.fss.or.kr"
    problems = clip_store.validation_errors(
        kind="filing", screen="stock", title="x", url=leaky, payload={"rcept_no": "1"})
    assert problems and any("인증키" in p for p in problems)


@pytest.mark.parametrize("leaky", [
    # ⚠️ 스킴이 없으면 `urlsplit().hostname` 이 `None` 이라 그냥 통과했다
    "opendart.fss.or.kr/api/list.json?crtfc_key=SECRET",
    # ⚠️ 끝점(FQDN) 표기 — DNS 상 같은 곳인데 문자열이 달라 통과했다
    "https://opendart.fss.or.kr./api/list.json?crtfc_key=SECRET",
    "https://OpenDart.FSS.or.KR/api/x?crtfc_key=SECRET",
    "https://www.opendart.fss.or.kr/api/x?crtfc_key=SECRET",
    # 다른 원천도 인증키를 질의로 싣는다 — 한 호스트만 막으면 나머지가 뚫린다
    "https://api.stlouisfed.org/fred/series?api_key=SECRET",
    "https://kosis.kr/openapi/Param/statisticsParameterData.do?apiKey=SECRET",
    "https://finlife.fss.or.kr/finlifeapi/depositProductsSearch.json?auth=SECRET",
])
def test_every_credential_bearing_host_is_blocked(leaky):
    """차단은 **한 겹이 뚫리면 없는 것과 같다.** 담기면 인증키가 표에 저장되고
    `GET /api/clips` 응답으로 밖으로 나간다."""
    assert clip_store.banned_host(leaky), f"뚫린다: {leaky}"


def test_the_viewer_url_is_still_allowed():
    """뷰어 주소에는 키가 없다. 이쪽까지 막으면 담을 것이 없어진다."""
    assert clip_store.banned_host(row()["url"]) == ""


def test_the_url_is_never_assembled_here():
    """⚠️ URL 정본은 `dart_data` 한 곳이다. 두 벌이 되면 `url_key` 가 갈려 멱등성이
    무너지는데, `payload.rcept_no` 에는 유니크 제약이 **없어 DB 가 막지 않는다.**
    """
    assert "dart.fss.or.kr" not in code_only(dc.to_clip), "수집기가 URL 을 새로 조립하고 있다"


# ==================================================
# 2. ①공시 / ②정기보고서 — 실제 제목에서 갈리는가
# ==================================================
@pytest.mark.parametrize("public_type, title, expected", [
    ("A", "사업보고서 (2025.12)", "periodic"),
    # ⭐ 이 줄이 이 검사의 이유다 — `startswith("사업보고서 (")` 는 정정본을 통째로 놓친다
    ("A", "[기재정정]사업보고서 (2024.12)", "periodic"),
    ("A", "[첨부정정] [기재정정]반기보고서 (2025.06)", "periodic"),
    ("A", "분기보고서 (2026.03)", "periodic"),
    # 실재하는 제목이다 (`dart_report.py` 가 이미 기록해 둔 함정)
    ("A", "해외증권거래소등에신고한사업보고서등의국내신고", "event"),
    # 유형이 정기공시가 아니면 제목이 닮았어도 아니다
    ("I", "사업보고서 (2025.12)", "event"),
    ("B", "주요사항보고서(자기주식취득결정)", "event"),
])
def test_periodic_reports_are_told_apart_from_events(public_type, title, expected):
    assert dc.filing_kind(row(public_type=public_type, report_name=title)) == expected


def test_the_category_axis_is_not_used_as_the_divider():
    """⚠️ `category=='실적'` 은 「무슨 사건인가」이고 ①/②는 「무슨 서류인가」라 **직교한다.**

    실적에는 사업보고서(②)와 영업(잠정)실적(①)이 함께 들어온다.
    """
    assert "category" not in code_only(dc.filing_kind)


def test_the_periodic_tag_follows_the_judgement():
    """태그로도 걸러야 한다 — `payload` 는 jsonb 라 인덱스가 없고 `tags` 는 GIN 이 탄다."""
    periodic = dc.to_clip(row(public_type="A", report_name="사업보고서 (2025.12)",
                              category="실적"), code="005930")
    assert "정기보고서" in periodic["tags"]
    assert "정기보고서" not in dc.to_clip(row(), code="005930")["tags"]


# ==================================================
# 3. 어휘와 계층 — 표명을 사실로 바꾼다
# ==================================================
def test_one_screen_value_for_both_branches():
    """⚠️⚠️ 유니크가 `(kind, url_key)` 뿐이라 `screen` 을 포함하지 않는다.

    ①과 ②가 다른 `screen` 을 쓰면 나중에 온 쪽이 `created=false` 로 흡수되고
    **먼저 쓴 screen 만 남아** 화면 목록이 조용히 반쪽이 된다.
    """
    assert "screen" not in DDL.split("clip_kind_urlkey_uq")[1].split("\n")[0]
    assert dc.SCREEN in clip_store.SCREENS
    literals = set(re.findall(r"'screen':\s*(\w+)", code_only(dc.to_clip)))
    assert literals == {"SCREEN"}, f"screen 값이 여러 곳에서 정해진다: {literals}"


def test_the_kind_is_one_of_the_ddl_values():
    assert dc.KIND in clip_store.KINDS


def test_the_collector_never_uses_the_single_row_write():
    """⚠️ `clip_store.create()` 는 충돌 뒤 재조회가 실패하면 **빈 dict** 를 돌려준다.

    배치가 그것을 쓰면 계수가 조용히 틀린다. 그리고 왕복이 한 줄당 셋이라 느리다.
    """
    assert "clip_store.create(" not in code_only(dc)


COLLECTOR_FILES = (
    PROJECT_ROOT / "app" / "services" / "dart_collector.py",
    PROJECT_ROOT / "scripts" / "collect_dart.py",
)

# 수집기가 **타면 안 되는** 것들.
#   krx_* · snapshot_store  — 시세 읽기 경로. `STORE_BACKEND` 스위치를 타므로 저장소
#                             상태에 따라 같은 명령이 다른 대상을 돌게 된다.
#   app.core.db             — 커넥션·트랜잭션·처방은 저장소 계층에 한 벌만 있어야 한다.
#   app.services.research   — ADR-DS-0007 이 그은 경계 밖이다.
FORBIDDEN_IMPORTS = ("krx_store", "krx_pg", "krx_bundle", "snapshot_store",
                     "app.core.db", "research")


@pytest.mark.parametrize("path", COLLECTOR_FILES, ids=lambda p: p.name)
def test_the_collector_does_not_reach_into_the_price_read_path(path):
    """⚠️ **산문이 아니라 AST 로 본다.** 주석이나 문자열에 이름이 들어 있는 것과
    실제로 import 하는 것은 다르다 — 이 파일의 설명문에는 그 이름들이 잔뜩 있다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imported.append(base)
            imported += [f"{base}.{alias.name}" for alias in node.names]
    bad = [name for name in imported
           if any(forbidden in name for forbidden in FORBIDDEN_IMPORTS)]
    assert not bad, f"{path.name} 이 타면 안 되는 것을 import 한다: {bad}"


# ==================================================
# 4. 두 벌 방지 — 검증표가 한 곳인가
# ==================================================
def test_the_router_re_exports_the_store_table():
    """⚠️ 표가 라우터에만 있으면 **사람이 담는 길만 막히고 수집기가 담는 길은 뚫린다.**"""
    assert clip_router.REQUIRED_PAYLOAD is clip_store.REQUIRED_PAYLOAD


def test_a_filing_without_a_receipt_number_is_refused_at_the_store_too():
    """수집기는 라우터를 거치지 않는다. 저장소가 같은 규칙을 지켜야 한다."""
    problems = clip_store.validation_errors(
        kind="filing", screen="stock", title="공시",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1", payload={})
    assert problems and any("rcept_no" in p for p in problems)


# ==================================================
# 5. 잘림 — 조용히 넘기지 않는가
# ==================================================
def test_per_type_truncation_is_visible_where_the_total_hides_it():
    """⚠️ 최상위 `truncated` 로는 판정할 수 없다 — 유형 셋을 합친 뒤라 A 하나가 100건에서
    잘려도 `False` 가 나온다. 그리고 정렬이 최신순이라 잘리는 것은 **항상 오래된 쪽**이고
    watermark 는 그대로 전진하므로, 못 받은 구간이 **영영 안 메워진다.**
    """
    # ⚠️ `rows` 를 실제 모양대로 채운다 — 받은 것이 그대로 살아남은 상태(합쳐서 자르지 않음).
    #    그래야 여기서 걸리는 것이 **①(DART 쪽 잘림)뿐**임이 드러난다.
    payload = {
        "rows": [{"public_type": "A"}] * 100 + [{"public_type": "B"}] * 3
                + [{"public_type": "I"}] * 7,
        "by_type": [
            {"code": "A", "count": 100, "total_count": 412, "truncated": True},
            {"code": "B", "count": 3, "total_count": 3, "truncated": False},
            {"code": "I", "count": 7, "total_count": 7, "truncated": False},
        ],
    }
    assert dc.truncated_types(payload) == ("A",)


def test_a_merge_cut_is_seen_even_when_no_type_was_truncated():
    """⚠️⚠️ **이 검사가 실제 자료 손실을 잡았다.**

    유형별로는 아무도 안 잘렸는데 **합계가 `limit` 을 넘으면** `dart_data` 가 최신순으로
    정렬한 뒤 앞에서 잘라 낸다. 그것을 못 보면 수집기가 `ok` 로 기록하고 watermark 를
    전진시켜 **오래된 쪽이 영구히 사라진다.**

    실측(2026-08-25) — 상위 350종목 첫 회차에서 **정확히 100건에 멈춘 종목이 9개**였고
    전부 `status='ok'` 였다. 고려아연은 12개월을 물었는데 가장 오래된 공시가 2025-12-15 로,
    **4개월치가 오류 없이 없어졌다.**
    """
    payload = {
        # 받은 것은 230건인데 합쳐서 자른 뒤 100건만 남았다
        "rows": [{"public_type": "A"}] * 40 + [{"public_type": "B"}] * 25
                + [{"public_type": "I"}] * 35,
        "by_type": [
            {"code": "A", "count": 80, "total_count": 80, "truncated": False},
            {"code": "B", "count": 60, "total_count": 60, "truncated": False},
            {"code": "I", "count": 90, "total_count": 90, "truncated": False},
        ],
    }
    assert dc.truncated_types(payload) == ("A", "B", "I")


def test_nothing_is_flagged_when_nothing_was_lost():
    """멀쩡한 응답에 잘림 딱지를 붙이면 창을 쓸데없이 쪼개 호출만 배로 든다."""
    payload = {
        "rows": [{"public_type": "A"}] * 3 + [{"public_type": "B"}] * 2,
        "by_type": [{"code": "A", "count": 3, "total_count": 3, "truncated": False},
                    {"code": "B", "count": 2, "total_count": 2, "truncated": False}],
    }
    assert dc.truncated_types(payload) == ()


def test_a_window_that_cannot_narrow_does_not_recurse(monkeypatch):
    """⚠️ `split_window` 는 더 못 쪼갤 때 **부모 창을 그대로** 돌려준다. 그것을 좁아진
    창으로 믿고 재귀하면 **같은 창을 15번 더 받는다**(3단계 × 유형 3개 · 실측).
    """
    calls = []

    def stub(code, **kw):
        calls.append((kw["bgn_de"], kw["end_de"]))
        return {"rows": [], "begin": "2026-08-25",
                "by_type": [{"code": "A", "count": 5, "total_count": 999, "truncated": True}]}

    monkeypatch.setattr(dart_data, "fetch_disclosures", stub)
    _rows, count, left = dc._fetch_window(
        "005930", "20260825", "20260825", ("A",), 5, 12, depth=0)
    assert len(calls) == 1, f"좁힐 여지가 없는데 {len(calls)}번 불렀다"
    assert count == 1 and left == ("A",)


def test_a_window_is_split_in_half():
    left, right = dc.split_window("20260101", "20260131")
    assert left[0] == "20260101" and right[1] == "20260131"
    assert left[1] < right[0], "두 구간이 겹치면 같은 공시를 두 번 받는다"


def test_a_one_day_window_does_not_recurse_forever():
    assert dc.split_window("20260101", "20260101") == (("20260101", "20260101"),) * 2


def test_the_split_limit_keeps_the_call_estimate_true():
    """⚠️ 상한이 없으면 종목마다 호출 수가 달라져 `--dry-run` 의 예상 호출이 거짓이 된다."""
    assert dc.MAX_WINDOW_SPLITS >= 1


def test_leftover_truncation_is_reported_not_swallowed(monkeypatch):
    """⚠️⚠️ **자식이 못 메운 잘림을 버리면** 그 구간이 `ok` 로 기록되고 watermark 가
    전진한다 — 영영 안 메워지는데 아무 데도 안 뜬다. 실측으로 이 구멍을 한 번 만들었다.
    """
    always_cut = {
        "rows": [], "begin": "2026-01-01",
        "by_type": [{"code": "A", "count": 5, "total_count": 999, "truncated": True}],
    }
    monkeypatch.setattr(dart_data, "fetch_disclosures", lambda *a, **k: always_cut)
    _rows, _calls, left_over = dc._fetch_window(
        "005930", "20260101", "20260131", ("A",), 5, 12, depth=0)
    assert left_over == ("A",), "상한에 걸린 잘림이 사라졌다"


# ==================================================
# 6. 증분 — 호출을 줄이는 것은 재방문 억제뿐이다
# ==================================================
def test_a_recent_visit_is_skipped_entirely():
    """⭐ 창을 좁히는 것은 호출 **수**를 줄이지 않는다 — 종목당 유형 수만큼 그대로다.
    줄어드는 것은 돌려받는 줄 수뿐이다. 방문 자체를 건너뛰는 이쪽이 유일한 레버다.
    """
    just_now = datetime.now(timezone.utc).isoformat()
    assert dc._seen_recently({"last_synced_at": just_now, "status": "ok"})


def test_an_old_visit_is_not_skipped():
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=dc.REVISIT_HOURS + 1)).isoformat()
    assert not dc._seen_recently({"last_synced_at": long_ago, "status": "ok"})


def test_a_failed_visit_is_always_retried():
    """⚠️ `status='error'` 는 "볼 수 없었다" 다. 건너뛰면 그 종목은 영영 안 채워진다."""
    just_now = datetime.now(timezone.utc).isoformat()
    assert not dc._seen_recently({"last_synced_at": just_now, "status": "error"})


# ==================================================
# 7. 실패 — 중단할지 건너뛸지가 갈리는가
# ==================================================
@pytest.mark.parametrize("dart_status, http, expected", [
    ("020", 429, "aborted"),    # 한도 초과 — 계속 돌면 남은 한도만 태운다
    ("010", 502, "aborted"),    # 인증키 거부 — 종목을 바꿔도 안 낫는다
    ("800", 502, "aborted"),    # 시스템 점검
    ("900", 502, "failed"),     # 정의되지 않은 오류 — 그 종목만 건너뛴다
    ("", 502, "failed"),        # 네트워크
    ("", 404, "skipped"),       # 고유번호 없음
])
def test_systemic_errors_stop_the_batch_and_local_ones_do_not(
        dart_status, http, expected, monkeypatch):
    """⚠️ HTTP 상태만 보면 `800`(중단)과 `900`(건너뜀)이 **둘 다 502** 라 구별되지 않는다.
    한글 메시지를 파싱하는 방법밖에 없어지고, 그러면 문구를 고치는 날 조용히 깨진다.
    """
    monkeypatch.setattr(dc, "_mark", lambda *a, **k: None)
    error = dart_data.DartError("무엇이든", status=http, dart_status=dart_status)
    got = dc._from_dart_error(dc.Outcome(code="005930"), error)
    assert got.state == expected


def test_the_fatal_set_matches_the_dart_status_table():
    """어휘 밖 코드를 치명적이라고 적어 두면 그 규칙은 영원히 안 걸린다."""
    unknown = [c for c in dc.FATAL_DART_STATUS if c not in dart_data.DART_STATUS]
    assert not unknown, f"DART 가 쓰지 않는 코드가 치명 목록에 있다: {unknown}"


# ==================================================
# 8. 능력 — 막는 것이 환경이 아니라 능력인가
# ==================================================
def test_capability_does_not_look_at_the_environment_name():
    """⚠️ `APP_ENV=vercel` 이라서 막으면 Supabase 를 붙인 날(S6) 이 함수를 고쳐야 한다.
    표에 붙어 보고 판단하면 고치지 않아도 살아난다 — ADR-DS-0017·0019 와 같은 원칙이다.
    """
    body = code_only(dc.capability)
    assert "APP_ENV" not in body and "app_env" not in body


def test_the_kill_switch_is_told_apart_from_the_other_two(monkeypatch):
    """세 사유의 **처방이 전혀 다르다** — 환경변수를 되돌리거나 · `.key` 에 한 줄을
    넣거나 · S6 을 기다린다. 뭉치면 사람이 엉뚱한 일을 한다."""
    monkeypatch.setenv("COLLECT_API", "off")
    state = dc.capability()
    assert not state["available"]
    assert "COLLECT_API" in " ".join(state["hints"])


def test_a_missing_key_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr(dart_data, "load_dart_key", lambda: ("", "none"))
    state = dc.capability()
    assert not state["available"] and ".key" in " ".join(state["hints"])


def test_an_unreachable_store_says_when_it_will_work(monkeypatch):
    """⚠️ 처방이 "무엇을 하라" 로만 끝나면, 그 무엇을 할 수 없는 사람에게는 막다른 길이다."""
    monkeypatch.setattr(dart_data, "load_dart_key", lambda: ("k" * 40, ".key"))
    state = dc.capability()
    assert not state["available"], "검사 환경은 표에 붙을 수 없어야 한다"
    assert any(clip_store.WHEN_IT_WORKS in h for h in state["hints"])


def test_an_unknown_collect_api_value_raises(monkeypatch):
    """⚠️ `COLLECT_API=false` 를 조용히 `on` 으로 떨어뜨리면 "껐다고 믿었는데 열려 있는"
    상태가 된다. 이 손잡이에서 그 거짓 음성은 외부 API 를 태우는 쪽이라 방향이 나쁘다.
    """
    from app.core import settings

    monkeypatch.setenv("COLLECT_API", "false")
    with pytest.raises(ValueError, match="COLLECT_API"):
        settings.collect_api()


# ==================================================
# 9. 범위 — "core" 라고 부르지 않는가
# ==================================================
def test_the_universe_is_honest_about_being_a_stand_in():
    """⚠️ `securities.universe_tier` 는 실측 전부 `full` 이고 KOSPI200·KOSDAQ150 구성종목
    목록이 레포에 없다 (ADR-DS-0014 — 추정하지 않는다). 낱말을 `core` 로 쓰면 다음 사람이
    구성종목이라고 믿는다.
    """
    codes, note = dc.universe(10)
    assert len(codes) == 10
    assert "대용" in note and "core" in note
    assert "core" not in dc.SCOPES


def test_the_universe_comes_back_in_code_order():
    """⚠️ 시총 순은 매일 바뀐다. 그 순서로 돌면 `--resume-from` 이 그날그날 다른 곳을 가리킨다."""
    codes, _ = dc.universe(20)
    assert codes == sorted(codes)


def test_an_unknown_scope_raises():
    with pytest.raises(ValueError, match="scope"):
        dc._targets(dc.Plan(scope="everything"))


# ==================================================
# 10. 고유번호 매핑 — 낡으면 막지 않고 알리는가
# ==================================================
def test_a_stale_mapping_warns_but_does_not_block(monkeypatch, tmp_path):
    """낡으면 신형 종목코드로 새로 상장한 회사가 404 로 떨어지는데, 그 실패는
    "DART 가 이상한가" 로 보인다. 원인은 파일이다."""
    old = (datetime.now(KST) - timedelta(days=dc.CORP_CODE_STALE_DAYS + 3))
    stale = tmp_path / "corp_code.json"
    stale.write_text(
        '{"generated_at": "%s KST", "map": {"005930": {"corp_code": "00126380"}}}'
        % old.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    monkeypatch.setattr(dc, "CORP_CODE_FILE", stale)
    state = dc.corp_code_state()
    assert state["stale"] and "build_corp_code" in state["hint"]


def test_a_missing_mapping_does_not_throw(monkeypatch, tmp_path):
    """매핑이 없어도 상태 조회는 살아야 한다 — 진단이 트레이스백으로 죽으면 존재 이유가 없다."""
    monkeypatch.setattr(dc, "CORP_CODE_FILE", tmp_path / "없는파일.json")
    assert dc.corp_code_state()["stale"] is True


# ==================================================
# 11. 예산 — 이름이 거짓말을 막는가
# ==================================================
def test_the_batch_budget_leaves_a_margin_under_the_daily_limit():
    """⚠️ 이 계수는 **이 수집기가 쓴 양**이지 DART 잔량이 아니다 — 리서치 화면이 부르는
    호출은 안 세어진다. 정확한 예약 표를 만들 수 없으므로(마이그레이션 러너가 없다)
    **마진**을 산다. 마진이 사라지면 그 부정확함이 곧바로 한도 초과가 된다.
    """
    assert dc.BATCH_CALL_BUDGET < dart_data.DAILY_CALL_LIMIT
    assert dart_data.DAILY_CALL_LIMIT - dc.BATCH_CALL_BUDGET >= dc.ONDEMAND_RESERVE


def test_the_budget_is_not_an_environment_variable():
    """⚠️ 예산은 환경이 아니라 이 서비스의 정책이다. 환경변수로 뚫어 두면
    "왜 오늘 한도가 찼지" 의 답이 셸 히스토리에 숨는다."""
    from app.core import settings

    assert not hasattr(settings, "BATCH_CALL_BUDGET")


def test_the_daily_limit_lives_with_the_client():
    """DART 가 정한 **사실**과 우리 **정책**을 한 파일에 두면
    "한도가 올랐다" 와 "예산을 늘렸다" 가 같은 diff 로 보인다."""
    assert dart_data.DAILY_CALL_LIMIT == 20_000


# ==================================================
# 12. 클라이언트 — 증분의 선행 조건이 서 있는가
# ==================================================
def test_the_client_accepts_an_explicit_window():
    """⚠️ 없으면 표현할 수 있는 가장 좁은 창이 **31일**이다(`months` 하한이 1).
    증분은 "지난번에 본 날 다음부터" 를 물어야 하는데 그 단위가 한 달이면 소용이 없다.
    """
    params = inspect.signature(dart_data.fetch_disclosures).parameters
    assert "bgn_de" in params and "end_de" in params
    assert "use_cache" in params


def test_the_cache_key_includes_the_window():
    """⚠️ 열쇠에 구간이 없으면 `bgn_de` 가 달라도 같은 칸을 친다 — 좁힌 창이 무시된다."""
    body = code_only(dart_data.fetch_disclosures)
    assert "begin_text" in body and "end_text" in body


def test_the_collector_never_takes_the_cache():
    """⚠️ TTL 이 24시간이라 캐시를 타면 그 날 두 번째 실행부터 네트워크를 안 타고
    같은 답을 준다 — **오류가 안 뜬다.** 가장 조용한 고장이다."""
    assert "use_cache=False" in code_only(dc._fetch_window)


def test_the_error_carries_the_dart_code():
    error = dart_data.DartError("점검 중", status=502, dart_status="800")
    assert error.dart_status == "800" and error.status == 502


# ==================================================
# 13. HTTP — 배포본에서도 등록하고 실행만 거절하는가
# ==================================================
@pytest.fixture
def client(app):
    return TestClient(app)


def test_paths_are_registered_even_where_they_cannot_run():
    """조건부 등록을 하면 계약 스냅샷이 환경마다 갈리고 **왜 안 되는지 설명할 자리가 사라진다.**"""
    registered = {r.path for r in collect_router.router.routes}
    assert registered == {"/api/collect/dart/status", "/api/collect/dart/security"}


def test_status_answers_200_even_when_nothing_works(client):
    """⚠️ **이 경로만은 503 을 내지 않는다.** 화면이 버튼을 잠글지 정하려면 이유를
    200 으로 받아야 한다. 거기까지 503 이면 화면은 **이유 없이** 잠긴다.
    """
    res = client.get("/api/collect/dart/status")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False
    assert body["reason"] and body["hints"]
    assert any(clip_store.WHEN_IT_WORKS in h for h in body["hints"])
    assert body["batch_command"].startswith("python3 scripts/collect_dart.py")


def test_running_is_refused_with_a_prescription(client):
    res = client.post("/api/collect/dart/security", json={"code": "005930"})
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert isinstance(detail, dict), "detail 이 문자열이면 화면이 처방을 못 읽는다"
    assert detail["reason"] and detail["hints"]


def test_status_survives_a_bad_switch_value(client, monkeypatch):
    """⚠️ "절대 실패하지 않는다" 고 못 박은 경로가 어휘 밖 `COLLECT_API` 에서 **500** 을 냈다.
    진단이 진단 대신 트레이스백으로 죽으면 존재 이유가 없다.
    """
    monkeypatch.setenv("COLLECT_API", "false")
    res = client.get("/api/collect/dart/status")
    assert res.status_code == 200
    assert res.json()["available"] is False


def test_a_systemic_stop_is_not_reported_as_success(client, monkeypatch):
    """⚠️ `collect()` 는 DART 의 `020`·`800` 을 삼켜 `stopped` 에 담는다. 그대로 200 을 내면
    화면이 **"새로 0건" 을 성공으로 그린다** — 라우터가 스스로 못 박은 거절 표를 우회한다.
    """
    monkeypatch.setattr(collect_router.collector, "capability",
                        lambda: {"available": True, "reason": "", "hints": []})
    monkeypatch.setattr(collect_router.collector, "calls_left", lambda **_k: {
        "left": 100, "used_by_collector": 0, "batch_budget": 12000,
        "daily_limit": 20000, "retry_after": 3600, "day": "2026-08-25"})
    monkeypatch.setattr(collect_router.collector, "collect", lambda *_a, **_k: {
        "available": True, "scope": "one", "stopped": "020 한도 초과", "created": 0,
        "failures": [{"code": "005930", "name": "", "state": "aborted",
                      "reason": "020 한도", "dart_status": "020"}]})
    res = client.post("/api/collect/dart/security", json={"code": "005930"})
    assert res.status_code == 429, f"계통 중단이 {res.status_code} 로 나갔다"
    assert res.headers.get("Retry-After")


@pytest.mark.parametrize("body", [
    {"code": "12345"},                     # 5자리
    {"code": "005930", "months": 0},       # 범위 밖
    {"code": "005930", "months": 999},
    {"code": "삼성전자"},                   # 한글
])
def test_bad_arguments_are_refused_before_anything_else(client, body):
    assert client.post("/api/collect/dart/security", json=body).status_code == 422
