"""Swagger(/docs) 문서 메타데이터 (공통)

`/docs` 에 보이는 **설명 글**만 모아 둔 파일이다. 동작에는 영향을 주지 않는다.
분량이 길어 진입점(`main.py`)에 두면 앱을 조립하는 코드가 묻히기 때문에 여기로 뺐다.

- `TAGS_METADATA` : Swagger 좌측의 그룹(태그) 설명. 각 엔드포인트의 `tags=[...]` 값과 이름이 같아야 묶인다.
- `API_DESCRIPTION` : 문서 최상단에 마크다운으로 렌더링되는 개요.
"""

# Swagger UI 좌측에 그룹(태그)으로 묶여 표시된다
TAGS_METADATA = [
    {
        # name 은 각 엔드포인트의 tags=[...] 값과 정확히 일치해야 묶인다
        "name": "기본",
        # description 은 그룹 제목 아래에 마크다운으로 표시된다
        "description": "서버 상태 확인용 엔드포인트",
    },
    {
        "name": "사용자",
        "description": (
            "사용자 조회·생성 API. 서버 시작 시 **Mock 데이터 30명**이 자동 생성된다. "
            "엔드포인트는 `app/routers/user_router.py`, 저장은 "
            "`app/repositories/user_store.py`(메모리 리스트) 에 있다."
        ),
    },
    {
        "name": "KRX 일별 시세",
        # 괄호로 감싸면 여러 줄 문자열을 자동으로 이어 붙일 수 있다 (줄바꿈은 들어가지 않는다)
        "description": (
            "한국거래소 OpenAPI의 **유가증권·코스닥 일별매매정보**를 조회한다. "
            "받은 데이터는 `data/krx_cache.db`(SQLite)에 쌓아 두고 여기서 읽는다. "
            "호출 코드는 `app/clients/krx_data.py`, 저장은 `app/repositories/krx_store.py` 에 있다."
        ),
    },
    {
        "name": "KOSIS 통계 실험실",
        "description": (
            "국가통계포털(KOSIS) OpenAPI를 **검색 → 파라미터 조립 → 호출** 3단계로 실험한다. "
            "응답은 화면이 바로 그릴 수 있도록 `chart.series`·`chart.categories` 형태로 변환해 준다. "
            "호출 코드는 `app/clients/kosis_data.py`, 화면은 `/kosis` 에 있다. "
            "**인증키는 응답 어디에도 노출되지 않는다.**"
        ),
    },
    {
        "name": "야후 파이낸스 시세",
        "description": (
            "**yfinance** 로 종목 하나의 당일 가격 지표(전일종가·시가·저가·고가·현재가)와 "
            "기간별 일봉을 조회한다. **인증키가 필요 없다.** "
            "호출 코드는 `app/clients/yf_data.py`, 화면은 `/yf`, "
            "같은 값을 터미널에서 그리는 스크립트는 `scripts/yf.py` 다."
        ),
    },
    {
        "name": "종목 통합 조회",
        "description": (
            "**엔드포인트 하나로 한국·미국 주식을 모두** 조회한다. "
            "`005930`(종목코드) · `삼성전자`(한글 이름) · `AAPL`(미국 티커) 를 모두 받아, "
            "야후 접미사(`.KS`·`.KQ`)는 **KRX 캐시의 시장 구분을 보고** 서버가 붙인다. "
            "`macro` 파라미터를 주면 FRED 거시지표를 주가 날짜에 맞춰 얹고 상관계수까지 계산한다. "
            "판별·계산은 `app/services/stock_service.py`, 화면은 `/stock` 이다."
        ),
    },
    {
        "name": "FRED 거시지표",
        "description": (
            "미국 연준(세인트루이스 연은)의 **FRED** 에서 금리·환율·물가·고용 시계열을 조회한다. "
            "`.key` 의 `FRED_API_KEY` 를 쓰며 **키는 응답 어디에도 노출되지 않는다.** "
            "주가와 겹쳐 보기 좋은 지표를 큐레이션해 두었고(`/api/fred/indicators`), "
            "그 밖의 지표는 검색(`/api/fred/search`)으로 찾는다. "
            "호출 코드는 `app/clients/fred_data.py` 에 있다."
        ),
    },
    {
        "name": "시장 분석",
        "description": (
            "캐시에 쌓인 실제 시세로 계산하는 분석 API. "
            "계산 로직은 `app/services/market_data.py`(서비스), "
            "응답 형식은 `app/routers/market_router.py`(컨트롤러)에 있다. "
            "**같은 거래일에 대해서는 항상 같은 값**이 나온다."
        ),
    },
]

# /docs 상단에 마크다운으로 렌더링되는 API 개요.
# 삼중 따옴표(""" """)는 줄바꿈을 그대로 유지하는 문자열이다.
API_DESCRIPTION = """
FastAPI로 만든 백엔드 API 서버입니다. **KRX·KOSIS·야후 파이낸스의 실제 데이터**를 다룹니다.

## 계층 구조

```
외부 API → clients(호출·정규화) → repositories(저장) → services(분석) → routers(엔드포인트) → 화면
```

| 파일 | 역할 |
| --- | --- |
| `app/clients/krx_data.py` | KRX 와 HTTP 통신, 대문자 축약 필드를 snake_case 로 정규화 |
| `app/clients/kosis_data.py` | KOSIS 호출, 평평한 응답을 차트용 `series`/`categories` 로 변환 |
| `app/clients/yf_data.py` | yfinance 호출, 가격 지표 정규화·Y축 범위 계산 (60초 메모리 캐시) |
| `app/repositories/krx_store.py` | 받은 일별 데이터를 `data/krx_cache.db` 에 쌓고 꺼냄 |
| `app/repositories/user_store.py` | 실습용 사용자 30명을 메모리 리스트로 보관 |
| `app/services/market_data.py` | 쌓인 데이터로 스크리닝·포트폴리오·팩터 계산 |
| `scripts/fetch_krx.py` | 캐시를 채우는 수집 스크립트 (`python3 scripts/fetch_krx.py`) |
| `scripts/yf.py` | 야후 가격 지표를 matplotlib 으로 그리는 스크립트 (`python3 scripts/yf.py`) |

## 화면

| 주소 | 화면 |
| --- | --- |
| [`/`](/) | 랜딩 — 화면 안내 · 서버/인증키/캐시 상태 |
| [`/users`](/users) | 사용자 API 테스트 (CRUD) |
| [`/kosis`](/kosis) | KOSIS 통계 실험실 — 검색 → 조립 → 호출 3단계 |
| [`/krx`](/krx) | KRX 일별 시세 — 전 종목 표, 거래대금·등락률 차트, 종목별 캔들 |
| [`/yf`](/yf) | 야후 파이낸스 시세 — 당일 가격 움직임(막대+꺾은선), 기간별 캔들 |
| [`/quant`](/quant) | 퀀트 분석 — 스크리닝 깔때기, 효율적 투자선, 팩터 방사형 |
| [`/tetris`](/tetris) | Canvas 테트리스 |

## 사용 순서

1. 터미널에서 `python3 scripts/fetch_krx.py` 로 시세 캐시를 채운다 (최초 1회, 약 7분)
2. `GET /health` — 서버가 살아있는지 확인
3. `GET /api/krx/status` — 인증키·캐시 상태 확인
4. `GET /api/krx/stocks` — 최근 거래일 전 종목 조회
5. `GET /api/yf/quote?ticker=005930.KS` — 야후 파이낸스 당일 가격 지표 (인증키 불필요)
6. `GET /api/users` — Mock 사용자 30명 조회

## 참고

- 사용자 데이터는 **메모리 리스트**라 서버를 끄면 사라지고, `age` 는 재시작마다 재생성됩니다.
- KRX 일별매매정보에는 **재무제표가 없습니다.** PER·PBR·ROE 대신 가격·거래량 지표를 씁니다.
- 인증키는 `.key` · `.env` · 환경변수에서 읽으며, **응답에 값이 노출되지 않습니다.**
- 야후 파이낸스 API 는 `yfinance` 가 설치돼 있을 때만 붙습니다. (`pip install yfinance matplotlib`)
"""
