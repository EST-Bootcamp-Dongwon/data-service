"""목표 스키마가 실제 자료를 받아 낼 수 있는 모양인지 (ADR-DS-0002 전환 준비).

`scripts/check_migration_fitness.py` 는 **실제 `data/krx_cache.db` 를 재는** 자다.
이 파일은 그 자가 잰 결과 중 **DDL 쪽에 굳어져야 하는 것**만 얼려 둔다.
그래서 여기서는 SQLite 도 Postgres 도 열지 않는다 — `sql/init/*.sql` 텍스트만 본다.
로컬 DB 없이 도는 것이 요건이다. 아니면 `invoke check` 가 사람마다 다른 색을 낸다.

⚠️ `sql/init/*.sql` 은 **빈 볼륨에서만 실행된다**(01-schema.sql:3-5). 이 레포에는
마이그레이션 러너가 없으므로, 적재를 시작한 뒤에 컬럼 하나를 고치는 것은 전면 재적재다.
지금 잡지 않으면 나중에는 비싸다.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "sql" / "init" / "01-schema.sql"
PARTITIONS_SQL = PROJECT_ROOT / "sql" / "init" / "02-partitions.sql"


def _schema_text() -> str:
    return SCHEMA_SQL.read_text(encoding="utf-8")


def _table_block(name: str, text: str) -> str:
    """`CREATE TABLE <name> ( ... )` 의 괄호 안쪽만 떼어 낸다.

    표 전체를 통으로 훑으면 안 된다 — `listed_shares` 는 securities 에도 ohlcv 에도
    나올 수 있고, 어느 쪽을 봤는지 모르는 검사는 검사가 아니다.
    """
    match = re.search(
        rf"CREATE TABLE {name}\s*\((.*?)\)\s*(?:PARTITION BY|;)", text, re.S
    )
    assert match is not None, f"{name} 표 정의를 찾지 못했다"
    return match.group(1)


# ==================================================
# 1. ohlcv — 수치 그릇이 실측값을 담는가
# ==================================================
def test_change_rate_holds_measured_extreme():
    """등락률 정수부가 실측 최대값(29948.08)을 담아야 한다.

    실측 원본에 `20260209 · 052670` 한 행이 있다. 780,484행 중 **딱 1행**이라
    샘플 이식이나 소량 테스트로는 절대 걸리지 않고, 배치 COPY 중 그 청크만 롤백돼
    "왜 2026-02-09만 비지?" 로 나타난다. 그래서 여기에 못을 박는다.
    """
    block = _table_block("ohlcv", _schema_text())
    match = re.search(r"change_rate\s+numeric\(\s*(\d+)\s*,\s*(\d+)\s*\)", block)
    assert match is not None, "ohlcv.change_rate 가 numeric(p, s) 로 선언돼 있어야 한다"

    precision, scale = int(match.group(1)), int(match.group(2))
    int_digits = precision - scale

    # 29948.08 → 정수부 5자리. 여유를 두되 근거는 실측값이다.
    assert 10**int_digits > 29948.08, (
        f"numeric({precision},{scale}) 은 정수부 {int_digits}자리라 실측 최대 등락률"
        " 29948.08 을 담지 못한다. scripts/check_migration_fitness.py 를 돌려 확인한다"
    )
    assert scale >= 2, "등락률은 소수 둘째 자리까지 원본에 있다"


@pytest.mark.parametrize("column", ["volume", "value", "market_cap"])
def test_wide_counters_are_bigint(column: str):
    """거래대금·시가총액은 int4 를 넘는다 — 실측 value 최대 21,934,538,087,990."""
    block = _table_block("ohlcv", _schema_text())
    assert re.search(rf"^\s*{column}\s+bigint", block, re.M), (
        f"ohlcv.{column} 은 bigint 여야 한다. integer(int4, 상한 21억)로 두면"
        " 첫 적재에서 바로 넘친다"
    )


@pytest.mark.parametrize("column", ["open", "high", "low", "close"])
def test_ohlc_is_integer_not_null(column: str):
    """OHLC 는 `integer NOT NULL` (AGENTS.md — 국내 주가는 원 단위 정수, 25% 절약).

    NOT NULL 이라는 사실이 중요하다. `krx_data._to_number` 가 `"-"` 를 None 으로
    만들기 때문에, 그런 행이 하나 오면 **그 하루치 배치 전체**가 롤백된다.
    지금 실측 NULL 은 0건이고, 그 전제가 깨지면 수집 쪽에서 먼저 막아야 한다.
    """
    block = _table_block("ohlcv", _schema_text())
    assert re.search(rf"^\s*{column}\s+integer[^,\n]*NOT NULL", block, re.M), (
        f"ohlcv.{column} 은 integer NOT NULL 이어야 한다"
    )


def test_ohlcv_is_range_partitioned_by_trade_date():
    """연 단위 RANGE 파티셔닝 + PK 는 (security_id, trade_date) — 확정 사항."""
    text = _schema_text()
    assert re.search(r"CREATE TABLE ohlcv\s*\(.*?\)\s*PARTITION BY RANGE \(trade_date\)", text, re.S)
    block = _table_block("ohlcv", text)
    assert re.search(r"PRIMARY KEY\s*\(\s*security_id\s*,\s*trade_date\s*\)", block)


def test_default_partition_exists():
    """`ohlcv_default` 가 있어야 범위 밖 날짜가 적재를 깨뜨리지 않는다.

    비어 있어야 정상이라는 점이 핵심이다 — 여기 행이 쌓이면 파티션 프루닝이
    조용히 죽는다. '비어 있는지'는 적재 후 불변식 테스트가 본다.
    """
    text = PARTITIONS_SQL.read_text(encoding="utf-8")
    assert "ohlcv_default" in text and "DEFAULT" in text


# ==================================================
# 2. ohlcv_sync_log — 휴장일 마커 규칙 (가장 중요한 이식물)
# ==================================================
def test_sync_log_keeps_zero_row_marker_rule():
    """`rows` 는 NOT NULL 이어야 한다 — 0 과 '행이 없음' 은 뜻이 다르다.

    0 = "받아 봤더니 없었다"(휴장), 행 없음 = "아직 안 받았다".
    NULL 을 허용하면 셋째 상태가 생기고 `fetched_dates()` 의 `rows > 0` 비교가
    조용히 거짓이 된다 — 확정 휴장일 18일을 매 수집마다 KRX 에 다시 묻게 된다.
    """
    block = _table_block("ohlcv_sync_log", _schema_text())
    assert re.search(r"^\s*rows\s+integer\s+NOT NULL", block, re.M)


def test_sync_log_status_vocabulary():
    """status CHECK 는 세 값이다. 적재기가 이 밖의 값을 쓰면 INSERT 가 거부된다."""
    block = _table_block("ohlcv_sync_log", _schema_text())
    match = re.search(r"status\s+text.*?CHECK \(status IN \((.*?)\)\)", block, re.S)
    assert match is not None, "ohlcv_sync_log.status 에 CHECK 제약이 있어야 한다"
    values = set(re.findall(r"'(\w+)'", match.group(1)))
    assert values == {"ok", "empty", "error"}, f"예상 밖 어휘: {values}"


# ==================================================
# 3. 자(尺) 자체가 스키마와 붙어 있는지
# ==================================================
def _load_fitness_module():
    """`scripts/check_migration_fitness.py` 를 모듈로 불러온다.

    `scripts/` 는 패키지가 아니라서 평범한 import 로는 안 잡힌다. 경로로 직접 연다.
    이 모듈은 import 만으로 DB 를 열지 않으므로 부작용이 없다.
    """
    spec = importlib.util.spec_from_file_location(
        "check_migration_fitness", PROJECT_ROOT / "scripts" / "check_migration_fitness.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fitness_script_parses_live_schema():
    """자가 한계값을 **DDL 파일에서 읽는지** — 소스에 베껴 두지 않았는지.

    베껴 두면 DDL 을 고쳤을 때 자만 옛 숫자를 들고 남아 **자는 통과했는데 적재가 깨진다.**
    "파일 이름이 소스에 있다"는 확인으로는 부족해서, 파서를 실제로 돌려 지금 DDL 과
    같은 값이 나오는지 본다. 둘이 어긋나면 여기서 빨개진다.
    """
    module = _load_fitness_module()
    parsed = module.read_target_schema()

    block = _table_block("ohlcv", _schema_text())
    match = re.search(r"change_rate\s+numeric\(\s*(\d+)\s*,\s*(\d+)\s*\)", block)
    assert match is not None

    assert parsed["rate_precision"] == int(match.group(1))
    assert parsed["rate_scale"] == int(match.group(2))
    assert parsed["rate_int_digits"] == int(match.group(1)) - int(match.group(2))
    # ohlcv 블록만 봐야 한다 — securities 의 listed_shares 를 주워 오면 결정 #2 판정이 뒤집힌다
    assert {"volume", "value", "market_cap"} <= parsed["bigint_cols"]
    assert {"open", "high", "low", "close"} <= parsed["not_null_cols"]


def test_fitness_script_opens_sqlite_readonly():
    """자가 원본에 쓰지 않는지 — 실제 연결이 전부 `mode=ro` 인가.

    읽기 전용 없이 열면 SQLite 가 WAL 파일을 만들면서 **원본 폴더에 쓴다.**
    전환 전 상태를 재는 도구가 그 상태를 바꾸면 안 된다.

    본문을 문자열로 grep 하지 않고 AST 로 본다 — 주석·독스트링에 적힌 설명문까지
    호출로 세면, 설명을 고쳤다는 이유로 테스트가 빨개진다.
    """
    source = (PROJECT_ROOT / "scripts" / "check_migration_fitness.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
    ]
    assert calls, "sqlite3.connect 호출을 찾지 못했다"

    for call in calls:
        rendered = ast.unparse(call)
        assert "mode=ro" in rendered, f"읽기 전용이 아닌 연결이 있다: {rendered}"
        assert any(kw.arg == "uri" for kw in call.keywords), (
            f"`file:...?mode=ro` 는 uri=True 없이는 그냥 파일명으로 읽힌다: {rendered}"
        )
