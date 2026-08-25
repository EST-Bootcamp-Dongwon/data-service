"""S3 적재기가 옮기면서 뜻을 잃지 않는가 (`scripts/load_pg.py` · ADR-DS-0011 S3).

**실 DB 도 SQLite 원본도 열지 않는다.** 순수 함수와 SQL 문자열, 그리고 `sql/init/*.sql`
텍스트만 본다. `tests/test_schema_fitness.py` 가 같은 자리를 지키는 이유와 같다 —
로컬 DB 가 있어야 도는 검사는 사람마다 다른 색을 내고, 그러면 `invoke check` 가
정본 노릇을 못 한다.

⚠️ 여기서 지키려는 것은 "적재가 성공하는가"가 아니다. 그건 적재기 스스로 원본과
대조해 판정한다(`--verify-only`). 이 파일이 지키는 것은 **옮기는 동안 뜻이 사라지지
않는가**다 — 특히 `rows=0` 이 휴장일 마커라는 규칙이다.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "sql" / "init" / "01-schema.sql"
CLIP_SQL = PROJECT_ROOT / "sql" / "init" / "03-clip.sql"
LOADER_PATH = PROJECT_ROOT / "scripts" / "load_pg.py"


def _load_module():
    """`scripts/load_pg.py` 를 모듈로 가져온다 (`scripts/` 는 패키지가 아니다).

    `test_schema_fitness.py` 가 `check_migration_fitness.py` 를 가져오는 방식과 같다.
    """
    spec = importlib.util.spec_from_file_location("load_pg", LOADER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loader = _load_module()


# ==================================================
# 1. rows=0 은 휴장일 마커다  ★ 이 걸음에서 가장 중요한 이식물
# ==================================================
def test_zero_rows_is_a_holiday_marker_not_a_gap():
    """`rows=0` 은 "아직 안 받았다"가 아니라 "받아 봤더니 없었다"다.

    `krx_store.fetched_dates()`(:160-173)가 이 값으로 재요청을 억제한다. 옮기면서
    뜻이 사라지면 **확정된 휴장일을 매 수집마다 KRX 에 다시 물어보게 된다** —
    공휴일이 연 15일 안팎이므로 10년이면 매 수집마다 150번의 헛된 왕복이다.
    """
    assert loader.sync_log_status(0) == "empty"
    assert loader.sync_log_status(1) == "ok"
    assert loader.sync_log_status(2761) == "ok"


def test_sync_log_status_words_exist_in_the_ddl():
    """적재기가 쓰는 `status` 어휘가 목표 DDL 의 CHECK 안에 있는가.

    베껴 둔 문자열은 조용히 갈라진다. DDL 쪽 어휘를 고치면 적재가 **첫 배치에서**
    롤백되는데, 그 오류 메시지는 원인에서 멀다.
    """
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    match = re.search(r"status\s+text\s+NOT NULL DEFAULT 'ok'\s*CHECK \(status IN \(([^)]*)\)\)",
                      text, re.S)
    assert match is not None, "ohlcv_sync_log.status 의 CHECK 을 찾지 못했다"
    allowed = set(re.findall(r"'(\w+)'", match.group(1)))
    for rows in (0, 1, 999):
        assert loader.sync_log_status(rows) in allowed


# ==================================================
# 2. 값 변환 — 그릇이 바뀌어도 값이 바뀌면 안 된다
# ==================================================
def test_trade_date_parses_the_sqlite_string_form():
    assert loader.to_trade_date("20250609") == date(2025, 6, 9)
    assert loader.to_trade_date("20261231") == date(2026, 12, 31)


def test_trade_date_refuses_a_shape_it_does_not_understand():
    """조용히 통과시키지 않는다. 날짜가 틀어지면 **파티션이 엉뚱하게 갈린다.**"""
    for bad in ("2025-06-09", "20250632", "", "오늘"):
        with pytest.raises(ValueError):
            loader.to_trade_date(bad)


def test_change_rate_does_not_leak_binary_float_noise():
    """`Decimal(float)` 을 쓰면 2진 근사가 그대로 실린다. `str()` 을 거쳐야 한다.

    `Decimal(1.23)` 은 `Decimal('1.229999999999999982236431605997495353221893310546875')` 다.
    numeric(12,4) 에 넣으면 반올림돼 결과는 같지만, 파이썬 쪽 대조 합계가 어긋난다.
    """
    assert loader.to_change_rate(1.23) == Decimal("1.2300")
    assert loader.to_change_rate(-0.5) == Decimal("-0.5000")
    assert loader.to_change_rate(0) == Decimal("0.0000")
    assert loader.to_change_rate(None) is None


def test_change_rate_keeps_the_measured_extreme():
    """실측 최대 절댓값 29948.08 (20260209 · 052670)이 numeric(12,4) 안에 들어간다.

    780,484행 중 **딱 1행**이라 소량 시험으로는 절대 걸리지 않는다
    (`tests/test_schema_fitness.py` 가 DDL 쪽에서 같은 못을 박는다).
    """
    converted = loader.to_change_rate(29948.08)
    assert converted == Decimal("29948.0800")
    digits = converted.as_tuple()
    assert len(digits.digits) <= 12, "numeric(12,4) 의 전체 자릿수를 넘는다"
    assert -digits.exponent == 4, "소수부가 4자리로 맞춰져야 대조 합계가 맞는다"


def test_fetched_at_gets_kst_because_the_source_has_no_timezone():
    """`fetch_log.fetched_at` 은 `datetime.now().isoformat()` 이 남긴 naive 문자열이다
    (krx_store.py:193). timestamptz 로 옮기려면 시간대를 정해야 하고, 기준시는 KST 다."""
    got = loader.to_fetched_at("2026-08-01T16:50:08")
    assert got == datetime(2026, 8, 1, 16, 50, 8, tzinfo=timezone(timedelta(hours=9)))


def test_fetched_at_keeps_a_timezone_that_is_already_there():
    got = loader.to_fetched_at("2026-08-01T16:50:08+00:00")
    assert got.utcoffset() == timedelta(0)


def test_fetched_at_never_invents_a_time():
    """빈 값을 지금 시각으로 때우지 않는다. 대장의 시각을 지어내면 신선도가 거짓이 된다."""
    for bad in ("", None):
        with pytest.raises(ValueError):
            loader.to_fetched_at(bad)


def test_fiscal_month_leaves_unknowns_empty_instead_of_guessing():
    assert loader.to_fiscal_month("12") == 12
    assert loader.to_fiscal_month(3) == 3
    for bad in ("0", "13", "", None, "12월", {}):
        assert loader.to_fiscal_month(bad) is None


# ==================================================
# 3. 어디에 붓는가 — 원격 사고를 막는 문지기
# ==================================================
@pytest.mark.parametrize(
    "url, expected",
    [
        ("postgresql+asyncpg://postgres:postgres@localhost:5432/data_service", "localhost"),
        ("postgresql+asyncpg://postgres:postgres@db:5432/data_service", "db"),
        ("postgresql+asyncpg://u:p@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres",
         "aws-0-ap-northeast-2.pooler.supabase.com"),
        ("postgresql+asyncpg://u:p@[::1]:5432/db", "::1"),
        ("postgresql+asyncpg://host/db", "host"),
        ("사람이 잘못 붙여넣은 값", ""),
    ],
)
def test_url_host_reads_the_host_without_rewriting_it(url, expected):
    assert loader.url_host(url) == expected


def test_supabase_is_not_a_local_target():
    """⚠️ `APP_ENV` 로는 못 막는다 — 개발자 셸에는 보통 그 값이 없어 `local` 로 떨어지는데,
    그 셸의 `DATABASE_URL` 은 Supabase 를 가리키고 있을 수 있다. 그러면 full 유니버스
    780,484행이 core 만 두기로 한 데모 DB 로 쏟아진다 (ADR-CT-0007 · ADR-CT-0010)."""
    remote = "postgresql+asyncpg://u:p@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
    assert not loader.is_local_target(remote)
    assert loader.is_local_target("postgresql+asyncpg://postgres:postgres@localhost:5432/x")
    assert loader.is_local_target("postgresql+asyncpg://postgres:postgres@db:5432/x")


def test_a_password_that_looks_like_a_host_does_not_fool_the_guard():
    """자격증명 안에 `@` 나 `localhost` 가 들어 있어도 호스트는 **마지막** `@` 뒤다."""
    tricky = "postgresql+asyncpg://user:p@localhost@real-remote.example.com:6543/db"
    assert loader.url_host(tricky) == "real-remote.example.com"
    assert not loader.is_local_target(tricky)


# ==================================================
# 4. 접기 — 무엇을 잃는지 알고 잃는다
# ==================================================
def _row(code, bas_dd, name, shares, market="KOSPI", sector="일반"):
    return (code, bas_dd, name, market, sector, shares)


def test_fold_takes_the_latest_trading_day_regardless_of_row_order():
    """`securities` 는 종목당 한 줄이고 그 값은 **가장 최근 거래일** 것이다.

    입력 순서에 기대면 안 된다 — SQLite 가 돌려주는 순서는 계약이 아니다.
    """
    folded = loader.fold_securities(
        [
            _row("005930", "20260731", "삼성전자", 5_919_637_922),
            _row("005930", "20250609", "옛이름", 100),
            _row("000660", "20250609", "SK하이닉스", 728_002_365),
        ]
    )
    assert set(folded) == {"005930", "000660"}
    assert folded["005930"]["name"] == "삼성전자"
    assert folded["005930"]["listed_shares"] == 5_919_637_922

    reversed_order = loader.fold_securities(
        [
            _row("005930", "20250609", "옛이름", 100),
            _row("005930", "20260731", "삼성전자", 5_919_637_922),
        ]
    )
    assert reversed_order["005930"]["name"] == "삼성전자"


def test_folding_drops_name_history_on_purpose():
    """접으면 옛 이름이 사라진다. 실측으로 101종목이 기간 안에 이름을 바꿨다.

    이 검사는 **그 손실을 막지 않는다** — 손실이 결정이었다는 사실을 얼려 둔다
    (ADR-DS-0014). 이력 표를 세우게 되면 이 검사가 먼저 빨개져야 한다.
    """
    folded = loader.fold_securities(
        [
            _row("123456", "20250609", "옛이름", 10),
            _row("123456", "20260731", "새이름", 10),
        ]
    )
    assert folded["123456"]["name"] == "새이름"
    assert "옛이름" not in str(folded)


def test_a_code_the_master_json_does_not_know_still_gets_a_row():
    """⚠️ 마스터를 **원천**으로 쓰면 안 되는 이유.

    실측으로 마스터가 못 덮는 시세 종목이 3개 있다(000075 · 000885 · 45014K).
    마스터를 원천으로 삼으면 그 셋이 `securities` 에서 빠지고, `ohlcv` 가 참조할
    `security_id` 가 없어 **그 종목의 시세가 통째로 사라진다.** 원천은 daily_price 다.
    """
    folded = loader.fold_securities([_row("000075", "20260731", "삼양홀딩스우", 100)])
    enriched = loader.enrich_security(folded["000075"], industry={}, corp={}, core_codes=set())
    assert enriched["code"] == "000075"
    assert enriched["name"] == "삼양홀딩스우"
    assert enriched["industry_code"] is None      # 모르는 것은 비운다. 지어내지 않는다
    assert enriched["corp_code"] is None
    assert enriched["fiscal_month"] is None
    assert enriched["universe_tier"] == "full"    # 목록에 없으면 full 이다


def test_master_values_are_paint_not_source():
    folded = loader.fold_securities([_row("000020", "20260731", "동화약품", 27_931_470)])
    enriched = loader.enrich_security(
        folded["000020"],
        industry={"000020": {"industry_code": "212", "fiscal_month": "12"}},
        corp={"000020": {"corp_code": "00119195"}},
        core_codes=set(),
    )
    assert enriched["name"] == "동화약품"          # 이름은 여전히 시세 쪽 값이다
    assert enriched["industry_code"] == "212"
    assert enriched["fiscal_month"] == 12
    assert enriched["corp_code"] == "00119195"


# ==================================================
# 4-1. 유니버스 딱지 — 목록 파일이 정본이다 (ADR-DS-0021)
# ==================================================
def test_core_membership_comes_from_the_list_not_from_a_proxy():
    """⚠️ `universe_tier` 를 시가총액 같은 **대용**으로 정하지 않는다.

    대용으로 찍으면 컬럼이 거짓말을 하고 다음 사람이 구성종목이라고 믿는다.
    ADR-DS-0014 가 "추정을 사실로 굳히지 않는다" 로 비워 두었고, ADR-DS-0020 은
    같은 이유로 `core` 라는 낱말 자체를 피했다. 이제 실제 목록이 있으므로 그것만 본다.
    """
    folded = loader.fold_securities([
        _row("005930", "20260731", "삼성전자", 5_969_782_550),
        _row("000075", "20260731", "삼양홀딩스우", 100),
    ])
    core = {"005930"}
    assert loader.enrich_security(folded["005930"], {}, {}, core)["universe_tier"] == "core"
    assert loader.enrich_security(folded["000075"], {}, {}, core)["universe_tier"] == "full"


def test_universe_vocabulary_matches_the_ddl():
    """어휘가 DDL 의 CHECK 와 갈리면 적재가 제약 위반으로 죽는다.

    산문이 아니라 **DDL 파일을 읽어** 대조한다 — `tests/test_clip.py` 가
    `clip_kind_ck` 를 다루는 방식과 같다. 두 벌이 되지 않게 하는 것이 요점이다.
    """
    schema = (Path(__file__).resolve().parents[1] / "sql" / "init" / "01-schema.sql").read_text(
        encoding="utf-8")
    match = re.search(r"CHECK \(universe_tier IN \(([^)]*)\)\)", schema)
    assert match, "01-schema.sql 에서 universe_tier CHECK 를 찾지 못했다"
    allowed = {value.strip().strip("'") for value in match.group(1).split(",")}
    assert set(loader.UNIVERSES) == allowed


# ==================================================
# 5. 배치 — 원본을 통째로 메모리에 올리지 않는다
# ==================================================
def test_batched_splits_and_keeps_the_last_short_chunk():
    assert list(loader.batched(range(7), 3)) == [[0, 1, 2], [3, 4, 5], [6]]
    assert list(loader.batched([], 3)) == []


def test_batched_is_lazy():
    """780,484행이 배치 크기에서 평평해지려면 **소비한 만큼만** 읽어야 한다."""
    consumed = 0

    def counting():
        nonlocal consumed
        for i in range(1_000):
            consumed += 1
            yield i

    first = next(loader.batched(counting(), 10))
    assert first == list(range(10))
    assert consumed <= 11, f"게으르지 않다 — {consumed}개를 읽었다"


def test_batched_refuses_a_size_that_would_spin_forever():
    with pytest.raises(ValueError):
        list(loader.batched(range(3), 0))


# ==================================================
# 6. 대조 — 무엇을 못 잡으면 안 되는가
# ==================================================
def _pair(**overrides):
    """원본·적재본 한 쌍. 기본은 완전히 일치하고, 넘긴 값만 적재본 쪽을 어긋뜨린다."""
    source = {
        "ohlcv_rows": 780_484, "codes": 2_870, "dates": 282,
        "min_date": date(2025, 6, 9), "max_date": date(2026, 7, 31),
        "sum_open": 1, "sum_high": 1, "sum_low": 1, "sum_close": 1, "sum_change": 1,
        "sum_volume": 1, "sum_value": 1, "sum_market_cap": 1, "sum_listed_shares": 1,
        "sum_change_rate": Decimal("1.0000"),
        "zero_bars": 31_153,
        "log_rows": 300, "log_sum_rows": 1, "log_zero_rows": 18,
    }
    target = dict(source)
    target["securities_rows"] = source["codes"]
    target["default_partition_rows"] = 0
    target.update(overrides)
    return source, target


def test_a_perfect_load_reports_nothing():
    source, target = _pair()
    assert loader.compare(source, target) == []


def test_a_lost_holiday_marker_is_caught():
    """★ 휴장일 마커가 사라지면 행수는 그대로여도 대조가 걸려야 한다.

    `rows=0` 행을 "빈 행이니 건너뛰자"고 생략하면 `log_rows` 와 `log_zero_rows` 가 함께
    줄어든다. 둘 다 재기 때문에 잡힌다.
    """
    source, target = _pair(log_zero_rows=0, log_rows=282)
    problems = loader.compare(source, target)
    assert any("휴장일 마커" in p for p in problems)


def test_rows_landing_in_the_catch_all_partition_are_caught():
    """⚠️ 행수 대조만으로는 **절대** 안 잡히는 고장이다.

    파티션이 없는 연도의 행은 `ohlcv_default` 로 들어간다. 행은 다 들어갔으므로
    `COUNT(*)` 도 합계도 전부 맞는다. 그물을 따로 보지 않으면 조용히 통과한다.
    """
    source, target = _pair(default_partition_rows=1)
    problems = loader.compare(source, target)
    assert any("ohlcv_default" in p for p in problems)


def test_a_rounding_drift_in_change_rate_is_caught():
    source, target = _pair(sum_change_rate=Decimal("1.0001"))
    assert loader.compare(source, target)


def test_missing_securities_are_caught():
    source, target = _pair(securities_rows=2_869)
    problems = loader.compare(source, target)
    assert any("securities" in problem for problem in problems)


def test_every_compared_key_exists_on_both_sides():
    """대조표에 적힌 열쇠가 실제로 양쪽 dict 에 있는가.

    없는 열쇠를 적으면 `compare()` 가 KeyError 로 죽는다 — 적재를 다 끝낸 뒤에.
    """
    source, target = _pair()
    for key, _ in loader.COMPARED:
        assert key in source, f"원본 지문에 {key} 가 없다"
        assert key in target, f"적재본 지문에 {key} 가 없다"


# ==================================================
# 7. 적재기가 넘지 않아야 할 선
# ==================================================
DDL_PREFIXES = ("create ", "drop ", "alter ", "truncate ", "grant ", "revoke ")


def _executable_strings(module_source: str) -> list[str]:
    """실행되는 문자열 상수만. docstring 은 뺀다 (`tests/test_db.py` 와 같은 방식).

    이 파일의 docstring 과 적재기의 docstring 에 DDL 낱말이 설명으로 들어 있어서,
    텍스트에 정규식을 걸면 **설명을 고쳤다는 이유로** 빨개진다.
    """
    tree = ast.parse(module_source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_loader_emits_no_ddl():
    """적재기는 표를 만들지 않는다.

    `sql/init/*.sql` 이 스키마의 정본이다. 적재기가 없는 표를 만들어 주기 시작하면
    정본이 두 곳이 되고, 그때부터 "이 컬럼은 어느 쪽이 맞나"를 사람이 기억해야 한다.
    표가 없으면 **무엇을 해야 하는지 말하고 멈추는** 것이 이 스크립트의 계약이다.
    """
    offenders = [
        value
        for value in _executable_strings(LOADER_PATH.read_text(encoding="utf-8"))
        if value.lstrip().lower().startswith(DDL_PREFIXES)
    ]
    assert not offenders, f"적재기에 DDL 문자열이 있다: {offenders}"


def test_the_ddl_guard_actually_catches_ddl():
    """⭐ 위 검사가 장식이 아닌지. 일부러 위반을 만들어 잡히는지 본다."""
    violating = 'from sqlalchemy import text\nq = text("CREATE TABLE x (n int)")\n'
    assert [
        v for v in _executable_strings(violating) if v.lstrip().lower().startswith(DDL_PREFIXES)
    ]


def test_the_loader_reads_sqlite_read_only():
    """원본을 그냥 열면 WAL 파일을 만들며 **원본 폴더에 쓴다.**

    전환 도중 원본이 바뀌면 대조가 뜻을 잃는다.
    """
    source = LOADER_PATH.read_text(encoding="utf-8")
    assert "mode=ro" in source and "uri=True" in source


def test_the_clip_guard_vocabulary_matches_the_ddl():
    """적재기가 요구하는 `clip.kind` 값이 실제 DDL 에 서 있는가.

    ⚠️ 이 짝이 어긋나면 가드가 **거짓으로 걸리거나**(영영 적재 못 함) **거짓으로
    통과한다**(넓히지 않은 볼륨에 780,484행을 붓고 나서 발견). 마이그레이션 러너가
    없으므로 후자는 전면 재적재다 (ADR-DS-0012 §2).
    """
    definition = CLIP_SQL.read_text(encoding="utf-8")
    match = re.search(r"CHECK \(kind IN \(([^)]*)\)\)", definition)
    assert match is not None, "clip_kind_ck 의 CHECK 을 찾지 못했다"
    allowed = set(re.findall(r"'(\w+)'", match.group(1)))
    for kind in loader.CLIP_KIND_REQUIRED:
        assert kind in allowed, f"적재기는 {kind} 를 요구하는데 DDL 에 없다"


def test_required_tables_all_exist_in_the_ddl():
    """적재기가 확인하는 표 이름이 DDL 에 실재하는가. 오타 하나면 가드가 영영 걸린다."""
    ddl = SCHEMA_SQL.read_text(encoding="utf-8") + CLIP_SQL.read_text(encoding="utf-8")
    for table in loader.REQUIRED_TABLES:
        assert re.search(rf"CREATE TABLE {table}\b", ddl), f"{table} 표 정의가 DDL 에 없다"
