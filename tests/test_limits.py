"""응답 상한 로직 (ADR-DS-0004).

외부 API 를 부르지 않는 순수 함수만 검사한다.

세 곳이 같은 규칙을 쓴다 — FRED 시계열 · 야후 일봉 · 차트 계열.
규칙은 하나다. **최근 것을 남기고, 통계는 남긴 구간에서 다시 내고, 원본은 건드리지 않는다.**
"""

from __future__ import annotations

from app.clients.fred_data import MAX_SERIES_POINTS, _limit_points
from app.clients.yf_data import MAX_HISTORY_ROWS, _limit_rows
from app.services.market_chart import MAX_POINTS, _limit_series


# ==================================================
# 1. FRED 시계열 — `/api/fred/series/{id}`
# ==================================================
def _fred_payload(n: int) -> dict:
    """관측치 n 개짜리 FRED 응답 흉내. 값은 1..n 이라 통계를 눈으로 검산할 수 있다."""
    values = [float(i) for i in range(1, n + 1)]
    return {
        "series_id": "TEST",
        "dates": [f"2020-01-{i:02d}" for i in range(1, n + 1)],
        "values": values,
        "count": n,
        "first": values[0],
        "latest": values[-1],
        "latest_date": f"2020-01-{n:02d}",
        "change": values[-1] - values[0],
        "change_rate": (values[-1] - values[0]) / values[0] * 100,
        "min": min(values),
        "max": max(values),
    }


def test_under_limit_is_untouched():
    """상한 이하면 배열을 건드리지 않고 truncated=False 만 붙인다."""
    result = _limit_points(_fred_payload(10), max_points=100)
    assert result["truncated"] is False
    assert result["total_count"] == 10
    assert result["count"] == 10
    assert result["values"][0] == 1.0


def test_over_limit_keeps_most_recent():
    """상한을 넘으면 **최근 것**을 남긴다. 앞이 아니라 뒤다."""
    result = _limit_points(_fred_payload(100), max_points=10)
    assert result["truncated"] is True
    assert result["total_count"] == 100
    assert result["count"] == 10
    # 1..100 중 마지막 10개 = 91..100
    assert result["values"] == [float(i) for i in range(91, 101)]
    assert result["dates"][0] == "2020-01-91"


def test_statistics_are_recomputed_for_the_kept_window():
    """자른 뒤 통계는 **자른 구간 기준**이어야 한다.

    화면이 그리는 구간과 옆에 적히는 숫자가 어긋나면 그게 더 나쁘다.
    """
    result = _limit_points(_fred_payload(100), max_points=10)
    assert result["first"] == 91.0        # 자른 구간의 첫 값
    assert result["latest"] == 100.0
    assert result["min"] == 91.0          # 전체 최솟값 1.0 이 아니다
    assert result["max"] == 100.0
    assert result["change"] == 9.0        # 100 - 91


def test_original_payload_is_not_mutated():
    """★ 캐시 오염 방지 — 원본을 제자리에서 고치면 안 된다.

    `fetch_series` 는 캐시에 든 전체 응답을 `_limit_points` 에 넘긴다.
    여기서 원본을 변형하면 **다음 호출이 이미 잘린 데이터를 받는다.**
    상한을 크게 줘도 짧은 시계열만 돌아오는, 재현하기 까다로운 버그가 된다.
    """
    original = _fred_payload(100)
    before_len = len(original["values"])
    before_first = original["first"]

    _limit_points(original, max_points=10)

    assert len(original["values"]) == before_len, "원본 배열이 잘렸다 — 캐시가 오염된다"
    assert len(original["dates"]) == before_len
    assert original["first"] == before_first
    assert "truncated" not in original, "원본에 새 키가 끼어들었다"


def test_zero_or_negative_limit_disables_truncation():
    """상한을 0 이하로 주면 자르지 않는다 — 상한을 끄는 탈출구."""
    result = _limit_points(_fred_payload(50), max_points=0)
    assert result["truncated"] is False
    assert result["count"] == 50


def test_default_limit_is_sane():
    """기본 상한이 화면이 그릴 만한 크기여야 한다.

    FRED 일별 시리즈는 2만 행이 넘는다(DFF 는 1954년부터). 기본값이 그것보다
    훨씬 작아야 상한을 두는 의미가 있다.
    """
    assert 100 <= MAX_SERIES_POINTS <= 5000


# ==================================================
# 2. 야후 일봉 — `/api/yf/history`
# ==================================================
def _history_payload(n: int) -> dict:
    """일봉 n 개짜리 야후 응답 흉내. 종가는 1..n 이라 수익률을 눈으로 검산할 수 있다."""
    rows = [{
        "date": f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}",
        "open": float(i), "high": float(i) + 1, "low": float(i) - 1,
        "close": float(i), "volume": 100.0 * i,
    } for i in range(1, n + 1)]
    return {
        "ticker": "TEST.KS",
        "period": "max",
        "period_label": "전체",
        "interval": "1d",
        "rows": rows,
        "count": n,
        "change_rate": (rows[-1]["close"] - rows[0]["close"]) / rows[0]["close"] * 100,
    }


def test_history_under_limit_is_untouched():
    """상한 이하면 봉을 건드리지 않고 truncated=False 만 붙인다."""
    result = _limit_rows(_history_payload(10), max_rows=100)
    assert result["truncated"] is False
    assert result["total_count"] == 10
    assert result["count"] == 10


def test_history_over_limit_keeps_most_recent():
    """상한을 넘으면 **최근 봉**을 남긴다. 차트는 오른쪽 끝이 지금이다."""
    result = _limit_rows(_history_payload(100), max_rows=10)
    assert result["truncated"] is True
    assert result["total_count"] == 100
    assert result["count"] == 10
    assert [r["close"] for r in result["rows"]] == [float(i) for i in range(91, 101)]


def test_history_change_rate_is_recomputed_for_the_kept_window():
    """구간 수익률은 **남긴 구간 기준**이어야 한다.

    자르기 전 값을 그대로 두면 화면 상단 타일이 "1부터 100까지 올랐다"(+9900%)고
    말하는데 차트는 91~100 만 그린다. 숫자와 그림이 다른 화면이 된다.
    """
    result = _limit_rows(_history_payload(100), max_rows=10)
    # 91 → 100 이므로 (100-91)/91*100
    assert round(result["change_rate"], 6) == round(9 / 91 * 100, 6)


def test_history_original_payload_is_not_mutated():
    """★ 캐시 오염 방지 — `fetch_history` 는 캐시에 든 전체본을 넘긴다."""
    original = _history_payload(100)
    before = len(original["rows"])

    _limit_rows(original, max_rows=10)

    assert len(original["rows"]) == before, "원본이 잘렸다 — 다음 호출이 잘린 값을 받는다"
    assert "truncated" not in original, "원본에 새 키가 끼어들었다"


def test_history_zero_limit_disables_truncation():
    """0 이하는 상한을 끄는 탈출구. `market_chart.series` 가 이 경로로 전 구간을 받는다."""
    result = _limit_rows(_history_payload(50), max_rows=0)
    assert result["truncated"] is False
    assert result["count"] == 50


def test_history_default_limit_covers_ten_years():
    """기본 상한이 **기간 목록에 있는 구간을 자르면 안 된다.**

    `PERIODS` 의 최장은 `max` 이고 그 앞이 `10y`(약 2,470거래일)다.
    상한이 그보다 작으면 "10년" 버튼이 10년을 안 보여 주는 거짓말이 된다.
    """
    assert MAX_HISTORY_ROWS >= 2600
    assert MAX_HISTORY_ROWS <= 10000        # 그렇다고 상한이 없는 것과 같아도 곤란하다


# ==================================================
# 3. 차트 계열 — `/api/chart/series` · `/overlay` · `/compare`
# ==================================================
def _series_payload(n: int, ma_window: int = 20) -> dict:
    """차트 계열 n 점짜리 응답 흉내.

    이동평균은 **전 구간에서** 계산한 것으로 넣는다 — 실제 `series()` 가 그렇게 만든다.
    앞쪽 `ma_window-1` 개만 null 이고 나머지는 값이 있다.
    """
    closes = [float(i) for i in range(1, n + 1)]
    return {
        "ticker": "^KS11",
        "label": "코스피",
        "period": "max",
        "period_label": "전체",
        "interval": "1d",
        "dates": [f"d{i:05d}" for i in range(1, n + 1)],
        "candles": [[c, c + 1, c - 1, c] for c in closes],
        "closes": closes,
        "volumes": [100.0] * n,
        "count": n,
        "stats": {"first": closes[0], "last": closes[-1]},
        "moving_averages": [{
            "window": ma_window,
            "label": f"{ma_window}일선",
            "values": [None] * (ma_window - 1) + [float(i) for i in range(ma_window, n + 1)],
        }],
        "fetched_at": "2026-08-17 00:00:00 KST",
        "source": "live",
    }


def test_series_under_limit_is_untouched():
    result = _limit_series(_series_payload(50), max_points=100)
    assert result["truncated"] is False
    assert result["total_count"] == 50
    assert result["count"] == 50


def test_series_over_limit_cuts_every_parallel_array():
    """배열이 여섯 갈래인데 하나라도 길이가 다르면 차트가 어긋난다."""
    result = _limit_series(_series_payload(200), max_points=50)
    assert result["truncated"] is True
    assert result["total_count"] == 200
    assert result["count"] == 50
    assert len(result["dates"]) == 50
    assert len(result["candles"]) == 50
    assert len(result["closes"]) == 50
    assert len(result["volumes"]) == 50
    assert len(result["moving_averages"][0]["values"]) == 50
    assert result["closes"] == [float(i) for i in range(151, 201)]


def test_series_moving_average_left_edge_is_not_hollow():
    """★ 자른 구간의 **왼쪽 끝에도 이동평균이 있어야 한다.**

    이것이 "시계열을 페이지로 자르면 안 된다"(ADR-DS-0004)는 말의 실체다.
    잘라 놓고 이동평균을 계산하면 앞 `window-1` 개가 null 이라 선이 늦게 시작한다.
    전 구간에서 계산한 뒤 잘라야 첫 점부터 값이 있다.
    """
    result = _limit_series(_series_payload(200, ma_window=120), max_points=50)
    values = result["moving_averages"][0]["values"]
    assert values[0] is not None, "이동평균 앞부분이 비었다 — 자른 뒤에 계산한 것이다"
    assert all(v is not None for v in values)


def test_series_stats_are_recomputed_for_the_kept_window():
    """통계는 반대로 **남긴 구간 기준**이다 (이동평균과 다루는 방향이 다르다)."""
    result = _limit_series(_series_payload(200), max_points=50)
    assert result["stats"]["first"] == 151.0     # 전체 첫 값 1.0 이 아니다
    assert result["stats"]["last"] == 200.0
    assert result["stats"]["min"] == 151.0
    assert result["stats"]["max"] == 200.0


def test_series_original_payload_is_not_mutated():
    """★ `/tmp` 캐시 오염 방지 — 캐시에는 자르기 전 전체본이 들어 있다."""
    original = _series_payload(200)
    before = len(original["dates"])

    _limit_series(original, max_points=50)

    assert len(original["dates"]) == before
    assert len(original["closes"]) == before
    assert len(original["moving_averages"][0]["values"]) == before, "이동평균 원본이 잘렸다"
    assert "truncated" not in original


def test_series_limit_matches_history_limit():
    """차트 계열과 야후 일봉의 상한이 같아야 한다.

    둘은 같은 야후 데이터를 본다. 상한이 다르면 `/api/yf/history` 와
    `/api/chart/series` 가 같은 티커·같은 기간에 다른 봉 수를 돌려준다.
    """
    assert MAX_POINTS == MAX_HISTORY_ROWS
