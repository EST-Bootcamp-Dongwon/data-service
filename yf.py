"""야후 파이낸스(yfinance) 일중 가격 변화 차트 — 독립 실행 스크립트

한 종목의 **전일종가 · 시가 · 저가 · 고가 · 현재가** 5개 값을 받아
막대(연파랑) + 꺾은선(빨강)을 겹쳐 그린다. 서버(`uvicorn`)와 상관없이 단독으로 돌아간다.

사용법
------
    python3 yf.py                          # 삼성전자(005930.KS)
    python3 yf.py 000660.KS                # 종목 지정 (SK하이닉스)
    python3 yf.py 005930                   # 숫자 6자리면 .KS 를 자동으로 붙인다
    python3 yf.py AAPL                     # 해외 종목도 된다 (통화는 응답 값을 따라간다)
    python3 yf.py AAPL --save chart.png    # 화면 대신 PNG 로 저장
    python3 yf.py --english                # 축·제목을 영어로 (한글 폰트가 없을 때)

같은 값을 브라우저에서 보려면 서버를 띄우고 <http://127.0.0.1:8000/yf> 로 들어가면 된다.
(화면은 `static/pages/yf.html`, API 는 `app/routers/yf_router.py`)

데이터 조회는 `app/clients/yf_data.py` 를 그대로 쓴다.
스크립트와 화면이 **같은 함수** 를 쓰므로 두 그림의 값이 항상 일치한다.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

# `app` 패키지를 import 할 수 있도록 이 파일이 있는 폴더(프로젝트 루트)를 검색 경로에 넣는다.
# 다른 폴더에서 `python3 /경로/yf.py` 로 실행해도 동작하게 하기 위함이다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.clients import yf_data  # noqa: E402  (경로 설정 후에 import 해야 한다)

# 한글 폰트 후보 — (보통 굵기, 굵은 글씨) 짝으로 둔다.
# 굵은 폰트를 함께 등록하지 않으면 제목(fontweight="bold")에서 폰트 경고가 뜬다.
# WSL 에서도 `/mnt/c/Windows/Fonts` 로 윈도우 폰트를 그대로 읽을 수 있다.
KOREAN_FONTS = (
    ("/mnt/c/Windows/Fonts/malgun.ttf", "/mnt/c/Windows/Fonts/malgunbd.ttf"),   # WSL — 맑은 고딕
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),          # 윈도우에서 바로 실행
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),                   # 리눅스 — 나눔고딕
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", ""),                        # macOS
)

BAR_COLOR = "#4A90E2"     # 막대 — 연한 파랑
LINE_COLOR = "#D0021B"    # 꺾은선 — 빨강
DEFAULT_TICKER = "005930.KS"


def setup_korean_font() -> bool:
    """한글 폰트를 찾아 matplotlib 에 등록한다. 찾았으면 True.

    등록하지 않으면 한글이 네모(□□□)로 깨진다. 폰트가 없는 환경도 있으므로
    실패하면 호출한 쪽에서 영어 라벨로 넘어간다.
    """
    from matplotlib import font_manager

    for regular, bold in KOREAN_FONTS:
        if not Path(regular).exists():
            continue
        try:
            font_manager.fontManager.addfont(regular)
            name = font_manager.FontProperties(fname=regular).get_name()
        except Exception:
            continue                                   # 폰트 파일이 깨졌으면 다음 후보로
        # 굵은 글씨용 파일이 있으면 함께 등록한다 (제목이 실제로 굵게 나온다)
        if bold and Path(bold).exists():
            try:
                font_manager.fontManager.addfont(bold)
            except Exception:
                pass
        matplotlib.rcParams["font.family"] = name
        # 한글 폰트에는 마이너스 기호가 없는 경우가 있어, 유니코드 마이너스를 끈다.
        matplotlib.rcParams["axes.unicode_minus"] = False
        return True
    return False


def can_show() -> bool:
    """지금 환경에서 창을 띄울 수 있는지 확인한다.

    WSL·서버처럼 GUI 가 없으면 matplotlib 백엔드가 `agg` 가 되고, 이때 `plt.show()` 는
    아무것도 하지 않는다. 그런 환경에서는 창 대신 PNG 로 저장해야 결과를 볼 수 있다.
    """
    return matplotlib.get_backend().lower() not in ("agg", "pdf", "ps", "svg", "template")


def format_price(value: float, currency: str) -> str:
    """가격을 통화에 맞게 표기한다. 원·엔은 정수, 나머지는 소수 둘째 자리까지."""
    if currency in ("KRW", "JPY"):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def plot_stock_movement(ticker_symbol: str, save_path: str = "", english: bool = False) -> int:
    """종목 하나의 당일 가격 움직임을 막대 + 꺾은선으로 그린다.

    반환값은 종료 코드다 (0 성공 · 1 실패). `main()` 이 그대로 프로세스 종료 코드로 쓴다.
    """
    # 1. 야후 파이낸스에서 가격 지표를 가져온다 (조회·정규화는 clients 계층이 담당)
    print(f"{ticker_symbol} 데이터를 불러오는 중입니다...")
    try:
        quote = yf_data.fetch_quote(ticker_symbol)
    except yf_data.YahooError as error:
        # 티커 오타·휴장·네트워크 장애 모두 여기로 온다. 메시지에 원인이 담겨 있다.
        print(f"[실패] {error}")
        return 1

    chart = quote["chart"]
    prices = chart["values"]
    currency = quote["currency"]

    # 2. 한글 폰트를 등록한다. 없으면(또는 --english) 영어 라벨로 그린다.
    use_korean = (not english) and setup_korean_font()
    categories = chart["categories_en"] if not use_korean else chart["categories"]

    # 3. 값을 못 받은 항목이 있으면 0 으로 두고 그린다 (막대 높이 0 = 데이터 없음)
    values = [p if p is not None else 0 for p in prices]

    import matplotlib.pyplot as plt                    # 폰트 설정을 끝낸 뒤 import 한다

    plt.figure(figsize=(10, 6))

    # 막대(연파랑) 위에 꺾은선(빨강)을 겹쳐 그린다. 둘은 같은 값을 가리킨다.
    plt.bar(categories, values, color=BAR_COLOR, alpha=0.5,
            label="가격 (막대)" if use_korean else "Price (Bar)")
    plt.plot(categories, values, color=LINE_COLOR, marker="o", linewidth=2, markersize=8,
             label="가격 (선)" if use_korean else "Price (Line)")

    # 4. Y축을 실제 값 구간(±1%)으로 좁힌다. 0 부터 그리면 변화가 보이지 않는다.
    #    범위 계산은 화면과 같은 함수를 쓰므로 웹 차트와 눈금이 일치한다.
    plt.ylim(chart["y_min"], chart["y_max"])

    # Y축 눈금을 `1.7e6` 같은 지수 표기 대신 `1,700,000` 으로 적는다 (값을 바로 읽을 수 있게)
    from matplotlib.ticker import FuncFormatter
    plt.gca().yaxis.set_major_formatter(FuncFormatter(lambda v, _: format_price(v, currency)))

    name = quote["name"]
    if use_korean:
        plt.title(f"{name} ({quote['ticker']}) 당일 가격 움직임", fontsize=16, pad=15, fontweight="bold")
        plt.xlabel("가격 종류", fontsize=12)
        plt.ylabel(f"가격 ({currency})" if currency else "가격", fontsize=12)
    else:
        plt.title(f"{quote['ticker']} Daily Price Movement", fontsize=16, pad=15, fontweight="bold")
        plt.xlabel("Price Type", fontsize=12)
        plt.ylabel(f"Price ({currency})" if currency else "Price", fontsize=12)

    # 5. 각 지점 위에 실제 가격을 적는다 (값이 없는 항목은 '-')
    for i, price in enumerate(prices):
        label = format_price(price, currency) if price is not None else "-"
        plt.text(i, values[i], label, ha="center", va="bottom", fontsize=10, fontweight="bold")

    plt.grid(axis="y", linestyle="--", alpha=0.6)
    plt.legend()
    plt.tight_layout()

    # 6. 터미널에도 같은 값을 찍어 둔다. 차트를 못 띄우는 환경에서도 값은 확인할 수 있다.
    print(f"\n{name} ({quote['ticker']}) · {quote['fetched_at']} · 장 상태 {quote['market_state'] or '-'}")
    # 터미널 출력은 폰트 문제가 없으므로 차트 언어와 무관하게 한국어 라벨로 적는다.
    for label, price in zip(chart["categories"], prices):
        print(f"  {label:<12} {format_price(price, currency) if price is not None else '-':>14} {currency}")
    change = quote["change"]
    if change["diff"] is not None:
        sign = "+" if change["diff"] > 0 else ""
        rate = f"{change['rate']:+.2f}%" if change["rate"] is not None else "-"
        print(f"  {'전일 대비':<12} {sign}{format_price(change['diff'], currency):>13} {currency}  ({rate})")

    # 7. 화면에 띄우거나 파일로 저장한다.
    #    GUI 가 없는 환경(WSL·서버)에서 --save 를 안 줬으면, 결과를 잃지 않도록 자동 저장한다.
    if not save_path and not can_show():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        auto_dir = Path(__file__).resolve().parent / "data" / "yf"
        auto_dir.mkdir(parents=True, exist_ok=True)
        save_path = str(auto_dir / f"{quote['ticker']}_{stamp}.png")
        print(f"\n창을 띄울 수 없는 환경입니다 (matplotlib backend={matplotlib.get_backend()}). 파일로 저장합니다.")

    if save_path:
        plt.savefig(save_path, dpi=130)
        print(f"차트 저장 완료 → {save_path}")
    else:
        plt.show()

    plt.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="야후 파이낸스에서 당일 가격 지표를 받아 막대+꺾은선 차트를 그린다",
    )
    parser.add_argument("ticker", nargs="?", default=DEFAULT_TICKER,
                        help=f"야후 티커 (기본 {DEFAULT_TICKER} · 코스피 .KS · 코스닥 .KQ)")
    parser.add_argument("--save", default="", metavar="경로",
                        help="차트를 창 대신 PNG 파일로 저장한다")
    parser.add_argument("--english", action="store_true",
                        help="축·제목을 영어로 그린다 (한글 폰트가 없을 때)")
    args = parser.parse_args()

    return plot_stock_movement(args.ticker, save_path=args.save, english=args.english)


if __name__ == "__main__":
    # 종료 코드를 그대로 넘겨 준다. 조회 실패면 1 이라 쉘 스크립트에서 확인할 수 있다.
    raise SystemExit(main())
