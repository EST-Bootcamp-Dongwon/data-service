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
흩어져 있는 **국내 투자 정보를 한 곳에 모아 두는 수집·보관·조회 서비스**입니다.
시세·통계는 정형 API 로, 공시·뉴스·커뮤니티는 수집으로 모으고, **기업과 산업**을 축으로 되찾습니다.

리포트를 *만드는* 것은 이 서비스의 경계 밖입니다 (ADR-DS-0007). 모으는 것까지가 여기입니다.

## 무엇을 모으나 — 여섯 갈래 (ADR-DS-0012)

| 갈래 | 지금 상태 |
| --- | --- |
| 공시 (DART 정기·수시) | 클라이언트만 있음 · 화면 준비중 — **1차 착수** |
| 정기보고서 (사업·반기·분기 원문) | 클라이언트만 있음 · 화면 준비중 — **1차 착수** |
| 뉴스 | 인증키만 배치 · 미착수. 링크·제목·출처·발행일까지만 담습니다 |
| 커뮤니티 | 미착수 (이용약관 확인이 선행) |
| 동영상 | 미착수 (링크까지만) |
| **시세·통계** | **가동중** — 아래 9원천 |

## 원천 9종

`app/clients/` 가 바깥과 통신하는 **유일한 계층**입니다.

| 클라이언트 | 원천 |
| --- | --- |
| `krx_data.py` | 한국거래소 일별매매정보 |
| `yf_data.py` | 야후 파이낸스 (인증키 불필요) |
| `kosis_data.py` | 국가통계포털 KOSIS |
| `ecos_data.py` | 한국은행 경제통계 ECOS |
| `fred_data.py` | 미 세인트루이스 연준 FRED |
| `fss_data.py` | 금융감독원 금융상품통합비교공시 |
| `dart_data.py` | 금융감독원 전자공시 OpenDART |
| `dart_report.py` | DART 사업보고서 원문 (부문별 매출·점유율) |
| `hf_data.py` | HuggingFace 추론 API (감성·제로샷·임베딩 — 생성형 LLM 이 아닙니다) |

## 계층 구조

```
외부 API → clients(호출·정규화) → repositories(저장) → services(분석) → routers(엔드포인트) → 화면
```

| 파일 | 역할 |
| --- | --- |
| `app/repositories/krx_store.py` | 받은 일별 데이터를 `data/krx_cache.db` 에 쌓고 꺼냄 |
| `app/services/market_data.py` | 쌓인 데이터로 스크리닝·포트폴리오·팩터 계산 |
| `app/services/timeseries/` | numpy 로 직접 구현한 분해·정상성·예측 엔진 |
| `app/core/settings.py` | 환경 진입점. `APP_ENV` 로 DB 접속 전략을 가릅니다 (ADR-DS-0003) |
| `app/core/db.py` | 비동기 엔진 계층 (ADR-DS-0011 S2) |
| `scripts/fetch_krx.py` | 캐시를 채우는 수집 스크립트 (`python3 scripts/fetch_krx.py`) |

## 화면

| 주소 | 화면 |
| --- | --- |
| [`/`](/) | 첫 화면 — 수집 갈래 · 데이터 상태 · 시장 카드 · 시장 온도 |
| [`/market`](/market) | 시장 상세 — 큰 차트 · 기간 토글 · 겹쳐보기 · 시장의 폭 |
| [`/stock`](/stock) | 종목 통합 조회 — 국내·미국 + FRED 거시지표 |
| [`/krx`](/krx) | KRX 일별 시세 — 전 종목 표, 거래대금·등락률 차트, 종목별 캔들 |
| [`/yf`](/yf) | 야후 파이낸스 시세 |
| [`/kosis`](/kosis) | KOSIS 통계 실험실 — 검색 → 조립 → 호출 3단계 |
| [`/quant`](/quant) | 퀀트 분석 — 스크리닝 깔때기, 효율적 투자선, 팩터 방사형 |
| [`/timeseries`](/timeseries) | 시계열 분석 — 분해 · 정상성 · 상관도 · 예측 |
| [`/research`](/research) | 리서치 하네스 (4작업 공용) |
| [`/guide`](/guide) | 프로젝트 안내 — 계층 데이터 흐름도 |

> `/users` · `/tetris` 화면은 앱에서 내리고 `실습/` 아카이브로 보냅니다.
> **사용자 API(`GET /api/users` 등) 자체는 그대로 살아 있습니다.**

## 사용 순서

1. 터미널에서 `python3 scripts/fetch_krx.py` 로 시세 캐시를 채웁니다 (최초 1회, 약 7분)
2. `GET /health` — 서버가 살아있는지 확인
3. `GET /api/krx/status` — 인증키·캐시 상태 확인
4. `GET /api/krx/stocks` — 최근 거래일 전 종목 조회
5. `GET /api/yf/quote?ticker=005930.KS` — 야후 파이낸스 당일 가격 지표 (인증키 불필요)
6. `GET /api/dashboard/summary` — 첫 화면이 쓰는 집계

## 참고

- 인증키는 `.key` · `.env` · 환경변수에서 읽으며, **응답에 값이 노출되지 않습니다.**
- KRX 일별매매정보에는 **재무제표가 없습니다.** PER·PBR·ROE 대신 가격·거래량 지표를 씁니다.
- 시계열 응답에는 **구간 상한과 잘림 고지**가 있습니다 (ADR-DS-0004). 페이지로 자르지 않는 이유는
  이동평균이 페이지 경계에서 깨지기 때문입니다.
- 출처 표기는 `<provider>-<tier>` 두 토막입니다 (ADR-DS-0009). tier 는
  `db`·`bundle`·`derived`·`live`·`live-memo` 다섯이 전부입니다.
- 야후 파이낸스 API 는 `yfinance` 가 설치돼 있을 때만 붙습니다. 빠지면 라우터 3종
  (엔드포인트 10개)과 화면 3개가 함께 빠집니다.
- 사용자 데이터는 **메모리 리스트**라 서버를 끄면 사라집니다 (강의 실습 잔여물).
"""
