"""P4 액면·배당 조정 · P5 단위·통화·회계기간 정규화 (전처리 계층) — 명세서 §3.1

숫자를 비교하려면 **같은 자로 잰 값**이어야 한다. 이 모듈은 서로 다른 자로 잰 값을
한 자에 맞춘다.

    P4 액면·배당 조정   수정주가를 쓰는지 밝히고 D- 레코드에 남긴다
    P5 통화·단위        KRW/USD 를 명시한다 (억·조 환산은 화면에서만)
    P5b 회계기간        결산월이 다른 회사를 같은 축에 놓는다

왜 억·조 환산을 여기서 하지 않는가
--------------------------------
"3,008,709억원" 과 "300.87조원" 은 **같은 값의 다른 표기**다. 계산 계층에서 단위를 바꾸면
어느 배율이 곱해졌는지 추적해야 하고, 반올림 오차가 계산에 섞여 든다.
그래서 **저장·계산은 원 단위 그대로 하고, 사람이 읽는 순간에만 바꾼다.**
그 변환기(`humanize_krw`)도 여기 두어 화면·리포트가 같은 규칙을 쓰게 한다.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# 통화 기호·이름. 응답에 통화를 반드시 실어 보내기 위한 표다.
CURRENCIES: Dict[str, Dict[str, str]] = {
    "KRW": {"symbol": "₩", "name": "원", "decimals": "0"},
    "USD": {"symbol": "$", "name": "달러", "decimals": "2"},
    "JPY": {"symbol": "¥", "name": "엔", "decimals": "0"},
    "EUR": {"symbol": "€", "name": "유로", "decimals": "2"},
}

# 한국식 큰 수 단위. 큰 것부터 둔다 (조 → 억 → 만).
KRW_UNITS = ((1_000_000_000_000, "조"), (100_000_000, "억"), (10_000, "만"))

# 보고서 코드 → 그 보고서가 담는 **누적 개월 수**.
# DART 분기보고서는 누적 기준이라 3분기 보고서에는 1~9월 합계가 들어 있다.
REPORT_MONTHS: Dict[str, int] = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}


def infer_currency(ticker: str) -> str:
    """티커에서 통화를 추정한다. 국내(`.KS`·`.KQ`·6자리 숫자)는 KRW, 나머지는 USD.

    추정이라는 점이 중요하다. 야후가 통화를 함께 주면 그 값을 우선 쓰고,
    없을 때만 이 함수를 쓴다.
    """
    code = (ticker or "").strip().upper()
    if code.endswith((".KS", ".KQ")) or (len(code) == 6 and code.isdigit()):
        return "KRW"
    if code.endswith(".T"):
        return "JPY"
    return "USD"


def mark_adjusted(rows: Sequence[Dict], auto_adjust: bool, source: str) -> Dict:
    """P4 — 액면분할·배당 조정 여부를 기록한다. → `D-` 레코드에 실을 조각.

    **조정 여부를 밝히는 것이 왜 중요한가** — 액면분할이 있었던 종목의 원본 종가는
    분할일에 절반으로 뚝 떨어진다. 그걸 수익률로 계산하면 하루에 -50% 가 찍힌다.
    수정주가(`auto_adjust=True`)를 쓰면 과거 값이 소급 조정돼 그 계단이 사라진다.

    다만 수정주가는 **과거 값이 계속 바뀐다.** 오늘 받은 3년 전 종가와 지난달에 받은
    3년 전 종가가 다를 수 있다. 그래서 언제 받은 값인지를 함께 남긴다.
    """
    return {
        "adjusted": bool(auto_adjust),
        "adjustment": "액면분할·배당 소급 반영 (yfinance auto_adjust=True)" if auto_adjust
                      else "원본 종가 (조정 없음) — 액면분할 구간에 계단이 생길 수 있습니다",
        "source": source,
        "rows": len(rows),
        # 수정주가는 받는 시점에 따라 과거 값이 달라진다. 재현하려면 이 시점이 필요하다.
        "note": "수정주가는 이후 배당·분할이 생기면 과거 값도 소급해 바뀝니다. "
                "같은 결과를 재현하려면 수집 시점을 함께 봐야 합니다." if auto_adjust else "",
    }


def normalize_series(rows: Sequence[Dict], ticker: str,
                     currency: str = "") -> Dict:
    """P5 — 시계열에 통화·단위를 명시해 돌려준다.

    값을 바꾸지 않는다. **무슨 단위인지 이름표를 붙일 뿐이다.**
    """
    code = (currency or "").strip().upper() or infer_currency(ticker)
    meta = CURRENCIES.get(code, {"symbol": "", "name": code, "decimals": "2"})
    return {
        "currency": code,
        "currency_symbol": meta["symbol"],
        "currency_name": meta["name"],
        "price_unit": f"{code} (1주당)",
        "volume_unit": "주",
        "value_unit": code,
        "rows": len(rows),
    }


def humanize_krw(amount: Optional[float], digits: int = 2) -> str:
    """원 단위 금액을 사람이 읽는 표기로 바꾼다. **표시 계층 전용.**

        300_870_903_000_000  →  "300.87조"
        6_566_976_000_000    →  "6.57조"
        45_000_000           →  "4,500만"

    계산에는 절대 쓰지 않는다 (모듈 설명 참고).
    """
    if amount is None:
        return ""
    negative = amount < 0
    value = abs(float(amount))

    for size, name in KRW_UNITS:
        if value >= size:
            scaled = value / size
            # 조 단위는 소수점을 남기고, 억·만은 정수로 읽는 편이 자연스럽다
            text = f"{scaled:,.{digits}f}{name}" if name == "조" else f"{scaled:,.0f}{name}"
            return f"-{text}" if negative else text

    text = f"{value:,.0f}"
    return f"-{text}" if negative else text


def fiscal_period(bsns_year: int, reprt_code: str, fiscal_month: str = "12") -> Dict:
    """P5b — 회계기간을 같은 축에 놓는다.

    **결산월이 12월이 아닌 회사가 있다.** 3월 결산이면 "2024 사업보고서" 가 담는 기간은
    2024년 4월 ~ 2025년 3월이다. 이걸 12월 결산 회사의 2024년과 나란히 놓고
    "같은 해" 라고 하면 경기 국면이 한 분기 어긋난다.

    또 **분기보고서는 누적이다.** 3분기 보고서에는 1~9월 합계가 들어 있어서,
    3분기 단독 실적을 보려면 반기 값을 빼야 한다. 그 사실을 여기서 밝혀 둔다.
    """
    months = REPORT_MONTHS.get(reprt_code, 12)
    try:
        end_month = int(fiscal_month or "12")
    except ValueError:
        end_month = 12
    end_month = end_month if 1 <= end_month <= 12 else 12

    # 결산월이 12월이면 회계연도와 달력연도가 같다.
    # 아니면 회계연도는 결산월 다음 달에 시작해 다음 해 결산월에 끝난다.
    if end_month == 12:
        start_label = f"{bsns_year}-01"
        end_label = f"{bsns_year}-{months:02d}"
        aligned = True
    else:
        start_month = end_month + 1 if end_month < 12 else 1
        start_year = bsns_year if end_month < 12 else bsns_year
        # 시작월부터 `months` 개월 뒤가 이 보고서의 끝이다
        total = start_month + months - 1
        end_year = start_year + (total - 1) // 12
        end_label = f"{end_year}-{((total - 1) % 12) + 1:02d}"
        start_label = f"{start_year}-{start_month:02d}"
        aligned = False

    return {
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "fiscal_month": end_month,
        "months_covered": months,
        "period_start": start_label,
        "period_end": end_label,
        # 12월 결산 회사와 그대로 비교해도 되는지
        "calendar_aligned": aligned,
        "cumulative": months < 12,
        "caveat": None if aligned else (
            f"결산월이 {end_month}월이라 회계연도가 달력연도와 어긋납니다 "
            f"({start_label} ~ {end_label}). 12월 결산 기업과 같은 해로 묶어 비교하면 "
            "경기 국면이 어긋납니다."),
        "cumulative_note": (
            f"{REPORT_MONTHS.get(reprt_code, 12)}개월 **누적** 값입니다. "
            "해당 분기 단독 실적을 보려면 직전 보고서 값을 빼야 합니다."
        ) if months < 12 else None,
    }


def compare_periods(periods: Sequence[Dict]) -> Dict:
    """여러 회사의 회계기간이 서로 비교 가능한지 판정한다. (피어 비교 전에 부른다)

    하나라도 달력연도와 어긋나면 `G-DATA` 를 발행할 근거를 돌려준다.
    """
    misaligned = [p for p in periods if not p.get("calendar_aligned", True)]
    mixed_months = {p.get("months_covered") for p in periods}

    gaps: List[Dict] = []
    if misaligned:
        gaps.append({
            "code": "G-DATA",
            "message": f"결산월이 다른 기업 {len(misaligned)}곳이 섞여 있습니다.",
            "detail": "회계연도가 달력연도와 어긋나 같은 해로 묶어 비교하면 "
                      "경기 국면이 한 분기 이상 어긋납니다.",
        })
    if len(mixed_months) > 1:
        gaps.append({
            "code": "G-DATA",
            "message": "누적 개월 수가 다른 보고서가 섞여 있습니다.",
            "detail": f"{sorted(m for m in mixed_months if m)}개월이 함께 있습니다. "
                      "연간과 분기 누적을 그대로 비교하면 규모가 왜곡됩니다.",
        })

    return {
        "comparable": not gaps,
        "misaligned_count": len(misaligned),
        "months_covered": sorted(m for m in mixed_months if m),
        "gaps": gaps,
    }
