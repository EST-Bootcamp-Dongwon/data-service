# api-test — FastAPI + KRX OpenAPI 실습

강사님 배포 자료(`lecture/`)의 **KRX 일별 시세 조회 예제**를, 내 방식대로 다시 설계해 합친 저장소다.
사용자 CRUD API 실습에서 시작해 **한국거래소 실제 시세**를 다루는 백엔드로 확장했다.

| 항목 | 내용 |
|------|------|
| 프레임워크 | FastAPI 0.141 + Uvicorn 0.52 |
| 데이터 출처 | **KRX OpenAPI** (유가증권·코스닥 일별매매정보) · **KOSIS OpenAPI** · **야후 파이낸스** · **FRED** (미국 거시지표) |
| 시세 저장소 | SQLite (`data/krx_cache.db`) — 약 232거래일 · 64만 행 · 96MB |
| 사용자 저장소 | 메모리 리스트 (`app/repositories/user_store.py`) — **서버 재시작 시 초기화** |
| 외부 라이브러리 | KRX·KOSIS·FRED·DB는 표준 라이브러리만. **주가 화면·스크립트만** `yfinance` · `matplotlib` |

> ⚠️ 목업이 아니다. 화면에 보이는 시세·거래대금·시가총액은 전부 KRX가 준 실제 값이다.

---

## 1. 저장소 구성 ★

**내 실습 코드**와 **강사님 배포 원본**을 같은 저장소 안에서 분리해 둔다.
원본을 건드리지 않으므로 강의 자료가 갱신돼도 충돌이 나지 않는다.

| 위치 | 내용 | 수정 |
|------|------|------|
| 저장소 루트 | 내 실습 코드 (`main.py` · [`app/`](app) · [`static/`](static) · [`scripts/`](scripts)) | 자유롭게 |
| [`lecture/`](lecture) | 강사님 원본 [edumgt/api-test2](https://github.com/edumgt/api-test2) — **서브모듈** | ❌ 읽기 전용 |

```bash
# 최초 clone — 서브모듈까지 함께 받는다
git clone --recurse-submodules https://github.com/EST-Bootcamp-Dongwon/api-test.git

# 이미 clone 했다면
git submodule update --init --recursive

# 강의 자료 최신화
git submodule update --remote lecture
git add lecture && git commit -m "chore: 강의 자료(api-test2) 갱신" && git push
```

---

## 2. 폴더 구조 · 계층 ★

강의 원본은 `main.py` 한 파일에 KRX 호출·검증·응답이 모두 들어 있다.
이 저장소는 **Controller → Service → Repository** 로 나누고, **폴더도 계층대로** 뒀다.

```
api-test/
├── main.py                 FastAPI 앱 조립만 담당 (uvicorn main:app)
├── app/
│   ├── routers/            ← 컨트롤러 : 요청 검증 · DTO · 엔드포인트
│   │   ├── user_router.py      사용자 CRUD    (/api/users)
│   │   ├── krx_router.py       KRX 시세 API   (/api/krx/...)
│   │   ├── kosis_router.py     KOSIS 통계 API (/api/kosis/...)
│   │   ├── yf_router.py        야후 시세 API  (/api/yf/...)
│   │   ├── stock_router.py     종목 통합 조회 (/api/stock/...)
│   │   ├── search_router.py    종목 자동완성  (/api/search)
│   │   ├── fred_router.py      FRED 거시지표  (/api/fred/...)
│   │   ├── market_router.py    분석 API       (/api/...)
│   │   └── page_router.py      화면(HTML) 라우트
│   ├── services/           ← 서비스   : 비즈니스 로직
│   │   ├── market_data.py      스크리닝 · 투자선 · 팩터
│   │   ├── stock_service.py    티커 판별(국내/미국) · 주가+거시지표 융합
│   │   └── search_service.py   자동완성 색인 (메모리 15,414종목)
│   ├── repositories/       ← 저장소   : 저장 · 조회
│   │   ├── krx_store.py        KRX 시세 (SQLite)
│   │   └── user_store.py       실습용 사용자 30명 (메모리 리스트)
│   ├── clients/            ← 외부 연동 : 외부 API 호출 · 응답 정규화
│   │   ├── krx_data.py         KRX OpenAPI
│   │   ├── kosis_data.py       KOSIS OpenAPI + 차트용 변환
│   │   ├── yf_data.py          야후 파이낸스(yfinance) + 차트용 변환
│   │   └── fred_data.py        FRED 미국 거시지표 + 날짜 정렬·상관계수
│   └── core/               ← 공통 유틸 : 거래일 · KST · 인증키 · 문서
│       ├── trading_calendar.py
│       ├── secrets.py          KRX·KOSIS·FRED 인증키 로딩
│       └── api_docs.py         Swagger(/docs) 설명 글
├── vercel.json             Vercel 배포 설정 (함수 실행시간)
├── .vercelignore           배포 번들에서 뺄 파일
├── .devcontainer/          GitHub Codespaces 설정 (→ 15장 배포·공유)
│   ├── devcontainer.json   파이썬 3.12 컨테이너 · 8000 포트 전달
│   ├── setup.sh            의존성 설치 + 인증키·캐시 상태 안내 (최초 1회)
│   └── start.sh            서버 자동 실행 + 포트 Public 전환 + 주소 출력
├── scripts/
│   ├── fetch_krx.py        KRX 캐시를 채우는 CLI 수집 스크립트
│   ├── build_stock_master.py  국내 종목 마스터 (티커 판별 + 시총순위)
│   ├── build_us_master.py     미국 종목 마스터 (나스닥 심볼 + S&P500)
│   ├── yf.py               야후 파이낸스 차트 스크립트 (matplotlib · 단독 실행)
│   └── kosis_rss.py        KOSIS 공지 크롤러 → RSS 2.0 변환
├── test.sh                 KOSIS 공지 범위 수집 실행 스크립트
├── static/
│   ├── pages/              화면 7종 (dashboard · stock · kosis · krx · yf · quant · index)
│   └── assets/             공통 app.css(디자인 토큰) · app.js(유틸) · shell.js(사이드바·티커바)
├── 실습/                    수업 자료 아카이브 — M1 이전 화면 8종 원본 (`/practice/`)
├── data/
│   ├── stock_master.json   국내 2,764종목 — 코드·이름·시장·시총순위 (111KB, **포함**)
│   ├── us_master.json      미국 12,650종목 — 티커·이름·거래소·S&P500 (751KB, **포함**)
│   ├── krx_cache.db        시세 캐시 96MB (.gitignore 대상)
│   ├── yf/                 scripts/yf.py 가 저장한 차트 PNG (.gitignore 대상)
│   └── kosis_rss/          KOSIS 공지 RSS 산출물 (.gitignore 대상)
├── docs/                   todo · 작업 기록
└── lecture/                강사님 원본 (서브모듈, 읽기 전용)
```

데이터가 흐르는 방향은 **한쪽뿐**이다. 아래 계층은 위 계층을 import 하지 않는다.
이 방향만 지키면 순환 import 가 생기지 않는다.

```
KRX OpenAPI                          KOSIS OpenAPI                야후 파이낸스
    ↓  HTTP (AUTH_KEY 헤더)              ↓  HTTP (apiKey 쿼리)        ↓  yfinance (키 불필요)
app/clients/krx_data.py              app/clients/kosis_data.py    app/clients/yf_data.py   ← 외부 연동
  호출 + 응답 정규화                    호출 + 차트용 변환            호출 + 차트용 변환
    ↓                                    │                            ├──────────────┐
app/repositories/krx_store.py            │                            │              │  ← 저장소(Repository)
  SQLite 저장 · 조회                     │                            │              │
    ↓                                    │                            │              │
app/services/market_data.py              │                            │              │  ← 서비스(Service)
  스크리닝 · 포트폴리오 · 팩터            │                            │              │
    ↓                                    ↓                            ↓              │
app/routers/*.py                     app/routers/kosis_router.py  app/routers/       │  ← 컨트롤러(Controller)
                                                                   yf_router.py      │
    ↓                                    ↓                            ↓              ↓
static/pages/*.html                  static/pages/kosis.html      static/pages/    scripts/yf.py
                                                                   yf.html          (matplotlib)
```

> `scripts/yf.py` 는 서버를 거치지 않고 **클라이언트 계층을 직접 호출**한다. 화면과 스크립트가 같은
> `app/clients/yf_data.py` 를 쓰므로, 브라우저 차트와 터미널 차트의 값·Y축 눈금이 항상 일치한다.

`/stock` 화면만 **서비스 계층에서 세 갈래를 합친다.** 티커를 판별하려면 KRX 캐시가 필요하고,
거시지표를 겹치려면 FRED 가 필요하기 때문이다. 계층 방향은 그대로 지킨다(아래→위 import 없음).

```
야후 파이낸스              KRX 캐시(SQLite)              FRED
    ↓ yfinance                 ↓ 시장구분·한글명            ↓ HTTP (api_key 쿼리)
app/clients/yf_data.py    app/repositories/          app/clients/fred_data.py   ← 외부 연동·저장소
                            krx_store.py               날짜 정렬 · 상관계수
    └───────────────┬───────────┴──────────────┬──────────┘
                    ↓                          ↓
            app/services/stock_service.py                                       ← 서비스
              ① 티커 판별  005930 → .KS / .KQ,  삼성전자 → 005930,  AAPL → 그대로
              ② 시세 조회  야후 우선, 실패하면 KRX 캐시로 대체
              ③ 지표 융합  주가 거래일에 맞춰 정렬 → 100 기준 환산 → 상관계수
                    ↓
            app/routers/stock_router.py   GET /api/stock/{ticker}                ← 컨트롤러
                    ↓
            static/pages/stock.html                                              ← 화면
```

> KOSIS는 **캐시를 두지 않는다.** 매번 다른 통계표를 실험하는 화면이라 미리 쌓아 둘 대상이
> 정해지지 않기 때문이다. 반대로 KRX는 전 종목 시세라 대상이 고정되어 캐시가 이득이다.

| 파일 | 줄 수 | 역할 |
|------|------|------|
| `app/clients/krx_data.py` | 338 | KRX HTTP 호출, 대문자 축약 필드 → snake_case 정규화, 집계·정렬 |
| `app/clients/kosis_data.py` | 419 | KOSIS 호출·재시도, 평평한 응답 → 차트용 `series`/`categories` 변환 |
| `app/routers/kosis_router.py` | 227 | KOSIS 실험 3단계의 DTO 와 엔드포인트 |
| `app/clients/yf_data.py` | 274 | yfinance 호출, 가격 5종 정규화·Y축 범위 계산, 60초 메모리 캐시 |
| `static/pages/stock.html` | 714 | 종목 통합 조회 화면 — 검색·차트·거시지표 오버레이·상관계수 |
| `app/services/stock_service.py` | 444 | **티커 판별**(국내/미국) · 시세 조회 대체 경로 · 거시지표 융합 |
| `app/clients/fred_data.py` | 398 | FRED 호출, 결측치 처리, 주가 날짜 정렬(계단식 보간), 상관계수 |
| `app/routers/stock_router.py` | 168 | 종목 통합 조회 API 의 DTO 와 엔드포인트 |
| `app/routers/fred_router.py` | 145 | FRED 거시지표 API 의 DTO 와 엔드포인트 |
| `app/routers/yf_router.py` | 159 | 야후 시세 API 의 DTO 와 엔드포인트 |
| `scripts/yf.py` | 202 | 야후 가격 지표를 matplotlib 막대+꺾은선으로 그리는 CLI 스크립트 |
| `app/core/secrets.py` | 85 | KRX·KOSIS·FRED 인증키 로딩 (환경변수 → `.env` → `.key`) |
| `app/repositories/krx_store.py` | 349 | `data/krx_cache.db` 스키마·수집·조회. 병렬 수집과 쓰기 직렬화 |
| `app/services/market_data.py` | 532 | 종목 지표 계산 → 스크리닝 깔때기 · 효율적 투자선 · 팩터 점수 |
| `app/routers/market_router.py` | 316 | 분석 API 의 DTO 와 엔드포인트 |
| `app/routers/krx_router.py` | 272 | KRX 시세 API 의 DTO 와 엔드포인트 |
| `app/core/trading_calendar.py` | 61 | 거래일·KST 유틸 (순환 import 방지용 공통 모듈) |
| `scripts/fetch_krx.py` | 92 | 캐시를 채우는 CLI 수집 스크립트 |
| `scripts/build_stock_master.py` | 94 | 종목 마스터 생성 — DB 없는 환경에서도 티커를 판별하게 한다 |
| `scripts/kosis_rss.py` | 439 | KOSIS 공지 크롤링 → RSS 2.0 변환 (표준 라이브러리만 사용) |
| `app/routers/user_router.py` | 163 | 사용자 CRUD 의 DTO 와 엔드포인트 |
| `app/repositories/user_store.py` | 67 | 실습용 사용자 30명을 메모리 리스트로 보관 |
| `app/routers/page_router.py` | 96 | 화면(HTML) 라우트. 라이브러리가 없는 화면은 빼고 등록 |
| `app/core/api_docs.py` | 113 | Swagger 태그 설명·API 개요 (동작에는 영향 없음) |
| `main.py` | 175 | **앱 조립만** — 라우터 등록 · 정적 서빙 · CORS · `/health` |
| `static/assets/app.css` | 489 | **디자인 토큰** — 색·타이포·간격 + 셸·카드·표 공통 스타일 |
| `static/assets/shell.js` | 297 | **셸** — 사이드바·티커바 조립, 스파크라인(SVG), 상태등급 배지 |
| `app/services/dashboard_data.py` | 353 | 대시보드 집계 — 지수·금리·시장온도 병렬 수집, `/tmp` 캐시 |
| `app/routers/dashboard_router.py` | 122 | 대시보드 API 의 DTO 와 엔드포인트 |
| `static/pages/dashboard.html` | 195 | 대시보드 화면 — 카드 그리드 · 시장 온도 · 데이터 상태 |

> 루트에는 `main.py` 만 둔다. 강의에서 쓰는 `uvicorn main:app` 명령을 그대로 쓰기 위해서이고,
> 나머지 실행 스크립트는 전부 `scripts/` 안에 있다. `main.py` 는 **앱을 조립하기만** 하고
> 엔드포인트·DTO·문서 글은 각 계층 파일이 갖는다.
> DB·인증키 경로는 파일 위치를 기준으로 계산하므로, 어느 폴더에서 실행해도 같은 파일을 찾는다.

---

## 3. 환경 구성

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt      # 또는: pip install fastapi uvicorn yfinance matplotlib
```

> `yfinance` · `matplotlib` 은 **주가 화면(`/stock` · `/yf`)과 스크립트(`scripts/yf.py`) 전용**이다.
> 이 둘을 안 쓸 거면 `fastapi uvicorn` 만 있어도 나머지 화면은 전부 동작한다.
> **FRED 거시지표는 새 라이브러리가 필요 없다** — 표준 라이브러리 `urllib` 로 직접 호출한다.

> 우분투에서 `ensurepip is not available` 오류가 나면 `sudo apt install -y python3.12-venv` 를 먼저 설치한다.
> `externally-managed-environment` 오류는 **가상환경 활성화를 안 한 것**이 원인이다.

### KRX 인증키 설정

KRX OpenAPI에서 아래 API 이용을 **신청하고 승인**받아야 한다.

| 시장 | API ID | 이름 |
|------|--------|------|
| KOSPI | `stk_bydd_trd` | 유가증권 일별매매정보 |
| KOSDAQ | `ksq_bydd_trd` | 코스닥 일별매매정보 |

키는 아래 **세 곳 중 아무 데나** 두면 된다. (우선순위: 환경변수 → `.env` → `.key`)

```text
# .key  — 아래 세 형식 모두 인식한다
발급받은_인증키
KRX_API_KEY = 발급받은_인증키
KRX_AUTH_KEY="발급받은_인증키"
```

```bash
export KRX_API_KEY='발급받은_인증키'    # 환경변수로 주는 방법
```

`.key` · `.env` 는 `.gitignore` 에 있으므로 GitHub에 올라가지 않는다.
**인증키 값은 API 응답에도 절대 담기지 않는다** — `GET /api/krx/status` 는 길이만 알려준다.

### KOSIS 인증키 설정

[kosis.kr/openapi](https://kosis.kr/openapi) 에서 발급받는다. KRX 키와 **같은 `.key` 파일에 나란히** 둘 수 있다.

```text
# .key  — 두 키를 함께 둔다 (이름이 붙은 줄만 인정하므로 서로 섞이지 않는다)
KRX_API_KEY = 발급받은_KRX_인증키
KOSIS_API_KEY = 발급받은_KOSIS_인증키
```

```bash
export KOSIS_API_KEY='발급받은_인증키'   # 환경변수로 주는 방법
```

두 키의 로딩 규칙은 `app/core/secrets.py` 한 곳에 모여 있다.
KRX 키만 강의 원본 호환을 위해 **값만 한 줄** 적는 형식도 계속 지원한다.

### FRED 인증키 설정

미국 거시지표(`/stock` 화면의 겹쳐 보기, `/api/fred/...`)에 쓴다.
[fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys) 에서 **무료로 즉시** 발급된다.

```text
# .key  — 키 세 개를 나란히 둘 수 있다
KRX_API_KEY = 발급받은_KRX_인증키
KOSIS_API_KEY = 발급받은_KOSIS_인증키
FRED_API_KEY = 발급받은_FRED_인증키
```

- 키가 없으면 **`/api/fred/...` 만 `503`** 이고 나머지 화면·API 는 그대로 동작한다.
  `/stock` 의 주가 조회도 FRED 키 없이 된다 (거시지표 겹쳐 보기만 안 된다).
- 키가 제대로 읽혔는지는 `curl localhost:8000/api/fred/status` 로 확인한다.
  **값은 절대 응답에 싣지 않고 길이와 출처만** 알려준다.

---

## 4. 시세 캐시 채우기 (최초 1회)

KRX 일별매매정보는 **하루치 전 종목 스냅샷**만 준다. 캔들 차트나 수익률 계산처럼
"한 종목의 여러 날"이 필요하면 거래일 수만큼 호출해야 하는데, 1회에 2~3초가 걸린다.
그래서 받은 날짜를 SQLite에 쌓아 두고 다음부터는 DB에서 읽는다.

```bash
python3 scripts/fetch_krx.py                 # 최근 250거래일 (없는 날짜만) — 약 7분
python3 scripts/fetch_krx.py --days 60       # 최근 60거래일만 — 약 100초
python3 scripts/fetch_krx.py --days 1        # 장 마감 후 하루치 추가 — 약 3초
python3 scripts/fetch_krx.py --status        # 받지 않고 현재 캐시 상태만 확인
```

> **저장소 루트에서** 실행한다. 스크립트가 알아서 루트를 찾아 `app` 패키지를 불러온다.

- 이미 받은 날짜는 건너뛴다. **휴장일(0건)도 기록**해 두므로 다시 요청하지 않는다.
- 단, 최근 7일 이내의 0건은 다시 확인한다 (당일 데이터는 장 마감 후 올라오기 때문).
- 서버를 껐다 켜도 캐시는 남는다. `--reload` 로 코드를 저장해도 마찬가지다.
- `data/krx_cache.db` 는 `.gitignore` 대상이다 (약 96MB, 언제든 재생성 가능).
- `data/` 폴더가 없으면 처음 실행할 때 자동으로 만들어진다.

---

## 5. 실행

```bash
uvicorn main:app --reload
```

| 주소 | 설명 |
|------|------|
| http://127.0.0.1:8000 | **대시보드** — 지수·환율·금리·시장온도 카드 + 데이터 상태 |
| http://127.0.0.1:8000/stock | 종목 통합 조회 (국내·미국 + FRED) |
| http://127.0.0.1:8000/krx | KRX 일별 시세 화면 |
| http://127.0.0.1:8000/yf | 야후 파이낸스 시세 화면 |
| http://127.0.0.1:8000/quant | 퀀트 분석 화면 |
| http://127.0.0.1:8000/guide | 프로젝트 안내 (계층 데이터 흐름도) |
| http://127.0.0.1:8000/practice/ | 실습 아카이브 (사용자 API · 테트리스 포함) |
| http://127.0.0.1:8000/docs | Swagger UI (자동 생성 문서) |
| http://127.0.0.1:8000/redoc | ReDoc 문서 |

WSL·원격에서 실행하고 내 PC 브라우저로 붙으려면:

```bash
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
hostname -I            # 표시된 IP 로 http://서버_IP:8000/ 접속
```

`127.0.0.1` 은 **접속을 시도한 그 컴퓨터 자신**을 뜻하므로, 브라우저와 서버가 다른 컴퓨터면 쓸 수 없다.

---

## 6. 화면

기능별로 파일을 나눴다. 파일명만 봐도 무슨 화면인지 알 수 있고, 각 화면은 자기 API만 호출한다.
화면(HTML)은 `static/pages/`, 공통 스타일·유틸은 `static/assets/` 로 분리해 중복을 없앴다.

**M1(2026-08-01)부터 모든 화면이 같은 셸을 쓴다** — 왼쪽 사이드바 + 상단 티커바.
페이지는 `Shell.render('키')` 한 줄만 부르면 되고, 셸은 페이지의 기존 내용을 그대로 감싼다.

| 주소 | 파일 | 화면 |
|------|------|------|
| `/` | `static/pages/dashboard.html` | **대시보드** — 지수·환율·금리·시장온도 카드 + 데이터 상태 |
| `/stock` | `static/pages/stock.html` | **종목 통합 조회** — 국내·미국 주가 + FRED 거시지표 |
| `/yf` | `static/pages/yf.html` | 야후 파이낸스 시세 |
| `/krx` | `static/pages/krx.html` | KRX 일별 시세 |
| `/kosis` | `static/pages/kosis.html` | KOSIS 통계 실험실 |
| `/quant` | `static/pages/quant.html` | 퀀트 분석 |
| `/guide` | `static/pages/index.html` | 프로젝트 안내 (예전 랜딩 · 계층 데이터 흐름도) |
| `/practice/` | `실습/` | 실습 아카이브 — M1 이전 화면 8종 원본 |
| — | `static/assets/app.css` | 디자인 토큰 + 공통 스타일 (라이트/다크) |
| — | `static/assets/app.js` | API 호출 · 숫자 표기 · 차트 기본값 |
| — | `static/assets/shell.js` | 사이드바 · 티커바 · 스파크라인 · 상태등급 |

> 화면 목록(사이드바)은 `static/assets/shell.js` 의 `NAV` 배열 **한 곳**에만 있다.
> 새 화면을 추가하면 여기 한 줄만 넣으면 모든 화면의 사이드바에 반영된다.
> (주소 등록은 `app/routers/page_router.py` 의 `PAGES` 에 한 줄)

### `/` — 대시보드

시장 상황을 카드 그리드로 본다. 카드마다 **값 + 스파크라인 + 상태등급**이 함께 있다.

- 지수 4종(코스피·코스닥·나스닥·S&P 500) · 원/달러 — 야후 파이낸스, 등급은 3개월 수익률 기준
- 미 국채 10년 금리 — FRED, 등급은 4개월 변화폭(%p) 기준
- **시장 온도** — KRX 일별매매정보의 상승 종목 비율. 지수는 대형주에 좌우되지만 등락 종목 수는 시장 전체를 센다
- **데이터 상태** — 예전 화면 상단 배지를 카드로 올렸다. 인증키 값은 응답에 담기지 않고 출처·길이만 나온다

카드 하나가 실패해도 나머지는 그대로 뜬다. 실패한 카드는 그 자리에서 사유를 말한다.

### `/users` · `/tetris` — 실습 아카이브로 이동

M1 리팩토링에서 **앱 메뉴에서는 내렸다**(미결정 항목 U6 결정). 수업에서 만든 결과물이라 지우지 않고
`실습/` 폴더에 원본 그대로 얼려 두었고, 예전 주소로 들어오면 그쪽으로 이동시킨다.

| 예전 주소 | 이동 위치 |
|---|---|
| `/users` | `/practice/pages/users.html` |
| `/tetris` | `/practice/pages/tetris.html` |

**사용자 CRUD API(`/api/users`) 자체는 그대로 살아 있다.** 화면만 아카이브로 옮긴 것이다.
강사님 원본(`lecture/`)은 서브모듈이라 내 파일을 넣으면 pull 때 충돌하므로, 아카이브는 별도 폴더에 둔다.

### `/kosis` — KOSIS 통계 실험실 ★

국가통계포털 OpenAPI를 **직접 실험하는** 화면. 어떤 통계표든 골라 바로 시각화한다.
KRX처럼 미리 정해둔 지표를 보여주는 게 아니라, **파라미터를 조립해 호출해 보는** 것이 목적이다.

```
1단계 찾기          2단계 조립                     3단계 호출·시각화
검색어 입력    →    orgId · tblId 자동 채움   →    ApexCharts (꺾은선/영역/막대/도넛)
GET /search        itmId 는 메타에서 선택         + 데이터 표 + 원본 JSON
                   prdSe · 기간 · 최대 계열       GET /data
                   GET /meta
```

| 단계 | 하는 일 |
|------|---------|
| **1. 통계표 찾기** | 이름으로 검색해 `orgId`(기관) + `tblId`(통계표) 두 코드를 얻는다. 행을 클릭하면 2단계가 자동으로 채워진다 |
| **2. 파라미터 조립** | 항목(`itmId`)은 통계표 메타에서 받아 **선택지로 채운다**. 기간은 *최근 N개* 또는 *시작~종료* 중 하나. 나갈 요청 URL을 실시간으로 보여준다 |
| **3. 결과·시각화** | 응답 시간·행 수·계열 수·갱신일 타일 + 차트 4종 전환 + 표 + 원본 JSON |

설계에서 중요한 점 세 가지.

- **인증키는 서버 밖으로 나가지 않는다.** 화면에 보여주는 KOSIS 요청 URL에서도 `apiKey` 를 뺀다.
  브라우저는 언제나 내 서버(`/api/kosis/...`)만 부른다.
- **차트 변환은 서버가 한다.** KOSIS 응답은 (기간 × 분류 × 항목)이 한 줄씩 평평하게 늘어선 형태라
  그대로는 그릴 수 없다. `app/clients/kosis_data.py` 의 `build_chart()` 가
  `categories`(가로축) + `series`(계열)로 뒤집어 준다. 화면은 받은 값을 그리기만 한다.
- **자른 것은 반드시 알린다.** 지역별 통계처럼 계열이 250개가 넘으면 차트가 읽히지 않으므로
  최근 값이 큰 순으로 남기고, **몇 개를 생략했는지 화면에 경고로 표시**한다.
  조용히 자르면 전부 본 것으로 오해하게 된다.

### `/krx` — KRX 일별 시세

강의 원본 예제에 해당하는 화면. **전 종목 2,764개**를 다룬다.

| 구성 | 내용 |
|------|------|
| 조회 조건 | 기준일(데이터가 있는 거래일만 선택 가능) · 시장 · 검색 · 정렬 · 방향 |
| 요약 타일 | 상승/보합/하락 종목 수와 비율, 거래대금·거래량 합계 |
| 차트 ① | **거래대금 상위 15종목** 가로 막대 — 막대를 클릭하면 캔들이 열린다 |
| 차트 ② | **등락률 분포** 히스토그램 (11구간, 보합은 별도 칸) |
| 차트 ③ | **시장별 등락** 누적 막대 (KOSPI·KOSDAQ) |
| 표 | 전 종목 페이지네이션. 행을 클릭하면 그 종목 캔들이 열린다 |
| 캔들 | 캔들 + MA5·MA20·MA60 + 거래량 브러시, 기간 버튼(1주~1년), 크로스헤어 |

- 상승 = **빨강**, 하락 = **파랑** (국내 증시 관행, 미국과 반대)
- 검색어는 차트에도 반영된다 — 표와 차트가 같은 모집단을 본다
- 날짜 선택 박스에는 **실제 데이터가 있는 거래일만** 담아, 휴장일을 골라 빈 화면을 보는 일이 없다

### `/stock` — 종목 통합 조회 (국내·미국 + FRED) ★

**엔드포인트 하나로 한국 주식과 미국 주식을 모두** 조회하고, 미국 거시지표를 같은 차트에 겹쳐 보는 화면.

| 구성 | 내용 |
|------|------|
| 검색 | **HTS 스타일 자동완성** — 두 글자만 쳐도 연관 종목이 드롭다운으로 뜬다. 방향키 ↑↓ · Enter · 클릭으로 선택 |
| 입력 형식 | 종목코드(`005930`) · **한글 종목명**(`삼성전자`) · 미국 티커(`AAPL`) · 야후 티커(`005930.KS`) |
| 기간 | 1개월 · 3개월 · **6개월**(기본) · 1년 |
| 요약 | 종목명 · 시장 배지(🇰🇷/🇺🇸) · 현재가 · 전일 대비 · **데이터 출처** |
| 요약 타일 | 구간 수익률 · 구간 최저/최고 종가 · 최근 거래량 · 거래일 수 · 52주 범위 |
| 차트 ① | **일별 종가** 꺾은선 + 이동평균 5·20·60일 |
| 차트 ② | **거래량** 막대 (상승일 빨강 · 하락일 파랑) |
| 거시지표 | FRED 12종을 칩으로 켜고 끈다. 켜면 **시작 100 기준**으로 같은 그림에 얹힌다 |
| 상관계수 | 주가와 각 지표의 **일간 변화율 상관계수** + 말로 푼 해석 |
| 지표 검색 | FRED 80만 시리즈를 이름으로 검색해 차트에 바로 추가 |

**티커 판별이 이 화면의 핵심이다.**

- 야후에서 국내 종목은 코스피 `.KS` · 코스닥 `.KQ` 로 접미사가 갈리는데,
  **접미사를 잘못 붙여도 야후는 오류를 내지 않고 엉뚱한 값을 준다.**
  실제로 코스닥 종목 `247540`(에코프로비엠)을 `.KS` 로 물으면 하루 묵은 96,500원이,
  `.KQ` 로 물으면 당일 103,500원이 온다. **사용자는 틀린 줄도 모른다.**
- 그래서 `data/krx_cache.db` 에 쌓인 **시장 구분을 보고** 접미사를 정한다.
  덤으로 한글 종목명 검색과 한글 이름 표시도 여기서 나온다.
  (캐시가 비어 있으면 두 접미사를 모두 조회해 **최신 데이터가 있는 쪽**을 고른다.)
- 야후 조회가 실패하면 국내 종목은 **KRX 캐시로 되돌아가** 차트를 그린다.
  이때는 요약줄에 `출처: KRX 캐시` 라고 밝힌다.

**거시지표를 겹칠 때 지킨 것 두 가지.**

- 발표 주기가 다르다(금리는 일별, 물가·실업률은 월별). 주가 거래일에 맞추려고
  **직전 발표치를 다음 발표 전까지 이어 쓴다**(계단식 보간).
  뒤의 값을 끌어오면 **아직 발표되지 않은 값**을 쓰는 셈이라 미래 정보가 새어 들기 때문에,
  항상 과거 방향으로만 채운다.
- 금리 4.67% 와 주가 262,500원은 자릿수가 달라 그대로는 겹칠 수 없다.
  둘 다 **시작을 100 으로 맞춰**(rebase) 같은 눈금에서 비교한다.
- 상관계수는 가격 수준이 아니라 **일간 변화율**로 계산한다.
  수준끼리 비교하면 둘 다 우상향한다는 이유만으로 상관이 높게 나오기 때문이다(허위 상관).

화면 상태는 주소에 담기므로 **지금 보는 화면을 그대로 링크로 넘길 수 있다.**

```text
/stock?ticker=AAPL&months=3&macro=DGS10,VIXCLS
```

### `/yf` — 야후 파이낸스 시세 ★

**인증키 없이** 종목 하나의 당일 가격 움직임을 보는 화면. 터미널 스크립트 `scripts/yf.py` 와 짝을 이룬다.

| 구성 | 내용 |
|------|------|
| 조회 조건 | 야후 티커 입력 + 예시 버튼(삼성전자·SK하이닉스·NAVER·에코프로비엠·Apple·NVIDIA·코스피 지수) |
| 요약 | 종목명 · 현재가 · 전일 대비 · **장 상태**(장중이면 초록 점이 깜빡인다) |
| 요약 타일 | 전일종가 · 당일 범위(저가~고가) · 거래량 · 시가총액 · 52주 범위 |
| 차트 ① | **당일 가격 움직임** — 전일종가·시가·저가·고가·현재가를 연파랑 막대 + 빨간 꺾은선으로 겹쳐 그림 |
| 차트 ② | **기간별 일봉** 캔들 + 거래량 브러시, 기간 버튼(5일·1개월·3개월·6개월·1년·5년) |
| 원문 | 화면이 실제로 받은 JSON 응답 그대로 |

- **국내 종목은 코스피 `.KS` · 코스닥 `.KQ`** 를 붙인다. 숫자 6자리(`005930`)만 넣으면 `.KS` 를 자동으로 붙여 준다.
- Y축은 0이 아니라 **실제 값 구간 ±1%** 로 좁힌다. 0부터 그리면 다섯 값이 다 비슷해 보여 변화가 묻힌다.
  범위 계산(`y_min`·`y_max`)은 서버가 해서 내려주므로 **터미널 차트와 눈금이 같다.**
- 잘못된 티커는 `404`. yfinance 는 없는 종목에도 예외를 내지 않고 값이 전부 `None` 인 응답을 주기 때문에,
  `app/clients/yf_data.py` 가 "유효한 가격이 하나도 없으면 실패" 로 직접 판단한다.
- 같은 티커를 60초 안에 다시 조회하면 **서버 메모리 캐시**로 돌려준다. `.info` 호출이 1~3초씩 걸려서다.

#### `scripts/yf.py` — 같은 값을 터미널에서 그리기

```bash
python3 scripts/yf.py                          # 삼성전자(005930.KS)
python3 scripts/yf.py 000660.KS                # 종목 지정
python3 scripts/yf.py 005930                   # 숫자 6자리면 .KS 자동
python3 scripts/yf.py AAPL --save chart.png    # 창 대신 PNG 저장
python3 scripts/yf.py --english                # 축·제목을 영어로 (한글 폰트가 없을 때)
```

- 한글 폰트(맑은 고딕·나눔고딕)를 찾아 자동 등록한다. 없으면 영어 라벨로 그린다.
- **GUI 가 없는 환경**(WSL·서버)에서는 `plt.show()` 가 아무것도 하지 않으므로,
  `data/yf/티커_시각.png` 로 자동 저장하고 경로를 알려 준다.
- 가격 5종은 터미널에도 표로 찍어 주므로 차트를 못 띄워도 값은 확인할 수 있다.

### `/quant` — 퀀트 분석

캐시에 쌓인 실제 시세로 계산하는 화면. 세 가지 분석이 한 페이지에 있다.

**① 스크리닝 깔때기** — 조건을 8단계로 걸어 2,764종목에서 20종목까지 좁힌다.

```
전체 상장 종목        2,764   →  거래 정상        2,686  (97.2%)
관측기간 충족         2,674   →  시총 1,000억↑    1,213  (45.4%)
평균 거래대금 10억↑     900   →  변동성 하위 50%    450  (50.0%)
모멘텀 상위 50%        225   →  20일선 위           75  (33.3%)
                             →  종합점수 상위 20    20  (26.7%)
```

최종 편입 종목은 표로 함께 보여준다. 종합점수 = 모멘텀·안정성·유동성 **백분위의 평균**.

**② 효율적 투자선** — 무작위 비중 포트폴리오를 뿌려 위험–수익 평면을 그린다.
기대수익률(μ)·변동성·상관계수(Σ)를 **실제 종가에서 계산**하고, 난수는 비중 추첨에만 쓴다.
최대 샤프·최소 분산 지점을 강조 표시한다.

**③ 팩터 방사형** — 모멘텀 · 안정성 · 유동성 · 규모 · 추세 · 회전율 6축.
각 점수는 **전 종목 대비 백분위(0~100)** 다. 유동성 98 이면 거래대금 상위 2% 라는 뜻이다.

### `/timeseries` — 시계열 분석 ★

**`numpy` 로 직접 구현한** 시계열 엔진을 실제 시세에 돌리는 화면 (M3).
`scipy`·`statsmodels` 를 쓰지 않는다 — 분포 함수까지 직접 만들었다.

가격 소스가 시장마다 다르다. **국내는 KRX, 해외는 야후**다.
국내는 KRX 의 실제 개장일 캘린더를 전처리에 함께 넘겨 연휴를 결측으로 오판하지 않는다.
해외에 KRX 캘린더를 쓰면 휴장일 판정이 틀리므로 근사로 센다 — 어느 쪽을 썼는지는 화면 상단 배너가 밝힌다.

**① 진단** — ADF 정상성 검정(가격·로그수익률 양쪽) · ACF(Bartlett) · PACF(Durbin-Levinson) · AIC 격자탐색 추천 차수.
ACF·PACF 는 **유의한 시차만 색을 주고 나머지는 회색**이다. 이 차트가 말하려는 것은 "어느 시차가 신뢰띠를 벗어났나" 하나이기 때문이다.

**② 분해** — 관측 · 추세(중심이동평균) · 계절 · 잔차를 **네 칸으로 나눠** 그린다.
관측(수십만 원)과 계절(±몇 원)은 축이 달라 한 차트에 못 겹치고, 이중축은 쓰지 않는 것이 이 프로젝트의 규칙이다.
계절 성분이 나온다고 계절성이 있다는 뜻은 아니므로 **계절 진폭 ÷ 잔차 표준편차**를 함께 낸다 (1보다 작으면 잡음이다).

**③ 예측 3단** — 점예측 + 95% 신뢰구간(ψ-weight) · 상승확률 + walk-forward 백테스트 · Bear/Base/Bull 시나리오.

```
ARIMA(2,1,0)  20일 점예측 263,175원   95% [196,509 ~ 329,842]
백테스트 12폴드 120점 · 방향 적중률 62.7% · RMSE 21,742 vs 확률보행 22,533 → +3.5%
Bull  372,325원 (+41.8%) 확률  0.2%   저항 362,500 상향 돌파 + 거래대금 20일 평균의 1.5배
Base  266,630원 ( +1.6%) 확률 94.9%   207,000~362,500 구간 안에서 등락
Bear  192,818원 (-26.6%) 확률  4.9%   지지 207,000 하향 이탈 + 20일선 아래
```

**ARIMA 는 언제나 확률보행과 겨룬다.** 못 이기면 `caveats` 맨 앞 줄에 그대로 적힌다.
주가는 효율시장에 가까워 못 이기는 것이 정상에 가깝고, 이 화면은 그것을 감추지 않는다.

시나리오 규칙 — **경계는 기술적, 확률·목표가는 통계**다.
σ 배수로 영역을 나누면 확률이 언제나 31/38/31% 로 고정돼 정보가 없다. 그래서 경계를
최근 60거래일 고점·저점(저항·지지)에 두고, 그 선을 넘을 확률을 예측분포에서 읽는다.
목표가는 각 영역의 **조건부 기대값**(절단정규 평균)이다.

### `/practice/pages/tetris.html` — Canvas 테트리스 (아카이브)

HTML5 `<canvas>` 2D 컨텍스트만으로 만든 게임. 외부 라이브러리 없음.

7-bag 랜덤 · SRS 월킥 · 고스트 · 홀드 · NEXT 4개 · 락 딜레이 · DAS/ARR ·
콤보/백투백 점수 · 레벨별 낙하 속도 · 터치 조작 지원.

| 조작 | 키 |
|------|-----|
| 이동 / 소프트 드롭 | `←` `→` / `↓` |
| 하드 드롭 | `Space` |
| 회전 (시계 / 반시계) | `↑` `X` / `Z` |
| 홀드 / 일시정지 / 재시작 | `C` `Shift` / `P` `Esc` / `R` |

---

## 7. API 목록

### 기본 · 사용자

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/health` | 서버 상태 확인 | 200 |
| GET | `/api/users` | 사용자 전체 목록 (Mock 30명) | 200 |
| GET | `/api/users/{user_id}` | 사용자 단건 조회 | 200 |
| POST | `/api/users` | 사용자 생성 | 201 |

> 강의 원본은 `/users` 였지만 **`/api` 를 붙였다.** 같은 주소를 화면(`/users`)이 쓰고 있어
> 둘 다 `GET /users` 로 두면 먼저 등록된 쪽이 이기고 나머지는 호출되지 않는다.
> 다른 API 가 전부 `/api/...` 인 것과도 규칙이 맞는다.
> (M1 에서 `/users` 화면은 아카이브로 옮겼지만, **API 규칙은 그대로 둔다.**)

### 대시보드 — `/api/dashboard/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/dashboard/ticker` | 티커바 — 지수 3종 + 환율 (값·등락만) | 200 |
| GET | `/api/dashboard/summary` | 카드 그리드 — 값 + 스파크라인 + 상태등급 + 데이터 상태 | 200 |

> **이 둘은 500 을 내지 않는다.** 야후 요청 한도·인증키 없음·캐시 없음은 흔한 상황이라
> 카드마다 `ok` 와 `error` 를 두고 200 으로 돌려준다. 화면은 실패한 카드만 접는다.
> 결과는 `/tmp` 에 3~5분 캐시한다 (있으면 쓰고 없으면 다시 부르는 best-effort).

### KRX 일별 시세 — `/api/krx/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/krx/status` | 인증키·캐시 상태 (**키 값은 노출 안 함**) | 200 |
| GET | `/api/krx/dates?limit=400` | 조회 가능한 거래일 목록 (최근순) | 200 |
| GET | `/api/krx/stocks` | 전 종목 스냅샷 + 집계 (**강의 원본과 같은 경로**) | 200 |
| GET | `/api/krx/stocks/{code}/ohlcv?days=120` | 종목 일봉 (캔들 차트용) | 200 |
| POST | `/api/krx/sync?days=3` | 최근 거래일 수집 (장 마감 후 추가용) | 200 |

`GET /api/krx/stocks` 파라미터: `bas_dd`(YYYYMMDD) · `market`(KOSPI·KOSDAQ) · `q`(검색) ·
`sort`(value·volume·change_rate·close·market_cap·code·name) · `order`(desc·asc) · `page` · `size`

### KOSIS 통계 — `/api/kosis/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/kosis/status` | 인증키 상태 (**키 값은 노출 안 함**) | 200 |
| GET | `/api/kosis/search?q=소비자물가&size=10` | 1단계 — 통계표 검색 | 200 |
| GET | `/api/kosis/meta?org_id=101&tbl_id=DT_1J22042` | 2단계 — 항목(ITM)·기간(PRD) 메타 | 200 |
| GET | `/api/kosis/data?org_id=101&tbl_id=DT_1J22042&itm_id=T03&prd_se=M&period_count=12` | 3단계 — 수치 + 차트 데이터 | 200 |

`GET /api/kosis/data` 파라미터:
`org_id`(기관) · `tbl_id`(통계표) · `itm_id`(항목, 기본 `ALL`) · `obj_l1`(분류1, 기본 `ALL`) ·
`prd_se`(주기 Y·H·Q·M·D) · `period_count`(최근 N개) 또는 `start_period`+`end_period` ·
`max_series`(차트 최대 계열, 기본 12)

응답의 `chart.series` · `chart.categories` 는 **ApexCharts에 그대로 넣을 수 있는 형태**이고,
`meta` 에는 응답 시간·전체 행 수·잘라낸 계열 수와 **`apiKey` 를 뺀 실제 KOSIS 요청 URL** 이 들어 있다.

### 야후 파이낸스 시세 — `/api/yf/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/yf/periods` | 조회 가능한 기간 목록 (5d·1mo·3mo·6mo·1y·5y) | 200 |
| GET | `/api/yf/quote?ticker=005930.KS` | 당일 가격 지표 5종 + 차트 데이터 | 200 |
| GET | `/api/yf/history?ticker=005930.KS&period=3mo` | 기간별 일봉 (캔들·거래량) | 200 |

**인증키가 필요 없다.** 잘못된 티커는 `404`, 허용하지 않는 `period` 는 `422`, 야후 응답 실패는 `502`.
`quote` 응답의 `chart` 는 `categories`(한국어) · `categories_en`(영어) · `values` · `y_min` · `y_max` 로,
ApexCharts(화면)와 matplotlib(`scripts/yf.py`)이 **같은 그림**을 그릴 수 있는 형태다.

### 종목 통합 조회 — `/api/stock/...` ★

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/stock/samples` | 예시 종목 목록 (국내는 거래대금 상위) | 200 |
| GET | `/api/stock/{ticker}` | **국내·미국 통합** 일별 종가 (기본 6개월) | 200 |
| GET | `/api/stock/{ticker}?months=3` | 기간 지정 — `1` · `3` · `6` · `12` | 200 |
| GET | `/api/stock/{ticker}?macro=DGS10,DEXKOUS` | FRED 거시지표를 주가 날짜에 맞춰 함께 반환 | 200 |

`{ticker}` 는 **종목코드(`005930`) · 한글 종목명(`삼성전자`) · 미국 티커(`AAPL`) · 야후 티커(`005930.KS`)** 를 모두 받는다.
응답의 `dates` · `prices` 두 배열이 차트에 그대로 들어가고, `moving_averages` 에 5·20·60일 이동평균이 함께 온다.

- 알 수 없는 종목은 `404` — **"알 수 없는 종목입니다"** 로 시작하는 안내 문구를 준다.
- 허용하지 않는 `months` 는 `422`, 야후 응답 실패는 `502`.
- `macro` 로 넘긴 지표 중 **일부만 실패해도 주가 응답은 살린다.**
  실패한 지표는 `macro[].ok = false` 와 `error` 로 표시된다 (화면 일부가 비는 편이 전체 실패보다 낫다).

### 종목 검색 (자동완성) — `/api/search`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/search?q=삼성` | HTS 스타일 자동완성 (최대 10건) | 200 |
| GET | `/api/search?q=AAP&limit=5&market=US` | 개수·시장 지정 | 200 |
| GET | `/api/search/stats` | 색인 현황 (국내 2,764 + 미국 12,650) | 200 |

**입력할 때마다 호출되는 API 라 속도가 전부다.** 종목 목록을 서버가 뜰 때
(`lifespan`) 메모리에 한 번만 올려 두고, 검색은 파일·DB·외부 API 를 전혀 건드리지 않는다.
**실측 4~6ms** (첫 요청만 색인 구축에 0.8초라 미리 올려 둔다).

관련도 순으로 정렬한다 — ① 티커 정확일치 ② 티커가 검색어로 시작 ③ 종목명이 시작
④ 종목명에 포함 ⑤ 티커에 포함. 여기에 **중요도**를 더한다.

| | 중요도 신호 | 출처 |
|---|---|---|
| 국내 | 시가총액 순위 | KRX 캐시 → `data/stock_master.json` |
| 미국 | S&P 500 편입 여부 | 위키백과 → `data/us_master.json` |

**ETF 는 항상 뒤로 민다.** ETF 이름에 유명 종목 티커가 그대로 들어가서
(`2x Long TSLA Daily ETF`), 그냥 두면 `TES` 검색에 테슬라보다 ETF 가 먼저 나온다.

```text
삼성  → 삼성전자 · 삼성전자우 · 삼성바이오로직스
AAP   → AAP(정확일치) · AAPL · AAPG          (ETF 6종은 아래로)
TES   → TSLA
전자  → 삼성전자 · 삼성전자우 · LG전자
```

### FRED 거시지표 — `/api/fred/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/fred/indicators` | 주가와 겹쳐 보기 좋은 **큐레이션 12종** | 200 |
| GET | `/api/fred/series/{series_id}` | 지표 시계열 (`?start=`·`?end=` 로 구간 지정) | 200 |
| GET | `/api/fred/search?q=unemployment` | FRED 80만 시리즈 이름 검색 (인기순) | 200 |
| GET | `/api/fred/status` | 인증키 상태 진단 (**값은 노출하지 않는다**) | 200 |

큐레이션 12종: 미 국채 10년·2년 금리, 장단기 금리차, 연방기금금리, VIX,
**원/달러 환율**, 달러 지수, S&P 500, 나스닥, 기대 인플레이션, 미국 CPI, 미국 실업률.

- 인증키가 없으면 `503` (다른 기능은 영향 없음).
- 없는 시리즈 ID 는 `404`, ID 형식이 FRED 규칙에 안 맞으면 `422`, 그 밖의 실패는 `502`.
  FRED 는 이 셋을 **전부 HTTP 400** 으로 주기 때문에, `app/clients/fred_data.py` 가
  응답 본문의 `error_message` 를 보고 갈라 준다.
- 결측치(FRED 가 `"."` 로 주는 미발표·휴장 구간)는 빼고 내려주므로 차트에 구멍이 나지 않는다.

### 시장 분석 — `/api/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/stocks?limit=30` | 종목 목록 (거래대금 상위순) | 200 |
| GET | `/api/stocks/{code}/ohlcv?count=120&ma=5&ma=20` | 일봉 + 이동평균 | 200 |
| GET | `/api/screening/funnel?window=60` | 스크리닝 깔때기 | 200 |
| GET | `/api/portfolio/frontier?samples=1200&codes=005930&codes=000660` | 효율적 투자선 | 200 |
| GET | `/api/factors/radar?codes=005930&codes=000660` | 팩터 점수 | 200 |

### 시계열 엔진 — `/api/ts/...` ★

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/ts/series?ticker=005930&years=2` | 정제 일봉 + 품질 리포트 | 200 |
| GET | `/api/ts/decompose?ticker=005930&period=5` | 추세 · 계절 · 잔차 | 200 |
| GET | `/api/ts/diagnostics?ticker=005930` | ADF · ACF · PACF · 추천 차수 | 200 |
| GET | `/api/ts/forecast?ticker=005930&horizon=20` | 예측 3단 + 백테스트 | 200 |

**자료 부족은 오류가 아니다.** 전처리 판정이 `insufficient` 면 모델링을 건너뛰지만 **200** 으로 답하고,
`status: "partial-continue"` 와 사유를 실어 보낸다. "자료가 모자라 못 했다" 와 "고장났다" 는
다른 말이라, 같은 코드로 답하면 화면이 둘을 구분해 안내할 수 없다.

응답에는 늘 `limitation` 문장이 함께 실린다 — ADF 임계값은 하드코딩한 근사값이고,
신뢰구간은 계수 추정오차가 빠져 실제보다 좁으며, Ljung-Box p값은 Wilson–Hilferty 근사다.

### GIC 리서치 하네스 — `/api/research/...` ★

12상태(H00~H11)를 한 번에 하나씩 실행한다. **서버는 상태를 갖지 않는다** — 브라우저가
Context Pack 을 들고 다니고 서버는 받은 것을 고쳐 돌려준다 (명세 §1.3).

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/research/workstreams` | 작업 4종 메타 (**넷 다 구현**) | 200 |
| GET | `/api/research/plan/{workstream_id}` | 12상태 · 가중치 · 갈라지는 자리 · 질문 지점 | 200 |
| GET | `/api/research/industries` | 산업 목록 (`?digits=2~5`) — IND-* 대상 선택 | 200 |
| GET | `/api/research/industries/resolve` | `?q=반도체` · `?q=261` · `?q=삼성전자` → 업종 확정 | 200 |
| GET | `/api/research/glossary` | 08강 용어 사전 427개 (`?term=` 툴팁 · `?q=` 검색) | 200 |
| POST | `/api/research/runs` | H00 실행 — run_header + 초기 Context Pack | 200 |
| POST | `/api/research/runs/steps/{state_id}` | H01~H11 단일 상태 실행 | 200 |
| POST | `/api/research/export/md` | Context Pack → GIC 양식 마크다운 | 200 |

**네 작업이 같은 12상태를 돈다.** 갈라지는 자리만 다르다 (명세 §5.4).

| 작업 | 대상 | 갈라지는 상태 | 리포트 양식 |
|---|---|---|---|
| CORP-R 기업 리서치 | 종목 | `H03` 회계기간 · 재무 · 피어 | 고정 15슬롯 |
| CORP-TP 기업 Top Pick | 종목 | `H04` 12개월 이벤트 · Quick Score 6차원 · P/W/D | 자유양식 |
| IND-R 산업 리서치 | 산업 | `H01`~`H04` 경계 · 근거 · 정규화 · (밸류체인·시장/수급·사이클) | 고정 15슬롯 (**구성이 다르다**) |
| IND-TP 산업 Top Pick | 산업 | `H01`~`H04` (후보군 · 점수 · penalty · 민감도) | 자유양식 |

산업 계열은 대상이 종목이 아니라 `H01`~`H03` 도 갈래가 다르다 —
`snapshot_store.get("261")` 은 종목이 아니므로 애초에 답이 없다.
**상태를 더하거나 빼지는 않았다.**

**리서치 API 는 오류로 중단하지 않는다.** 자료가 없으면 200 으로 답하되
`stage_result.status = "partial-continue"` 와 Gap Log 를 싣는다. 4xx 는 요청 자체가
말이 안 될 때만 낸다 (모르는 워크스트림·모르는 상태).

`stage_result` 는 GIC 공통계약 §7 의 봉투를 **필드명 그대로** 쓴다 (21개 필드).
더하지도 빼지도 않는다 — 나중에 4차 루프 엔지니어링을 붙일 때 계약이 깨지지 않게 하기 위함이다.

전구간 실측 (로컬 · 2026-08-02)

| 작업 | 대상 | 전구간 | 장수 | 최대 팩 | 근거/데이터/계산/Gap |
|---|---|---:|---:|---:|---|
| CORP-R | 005930 삼성전자 | 1.5초 | 14장 | 90.5KB | 22 / 45 / 2 / 2 |
| CORP-TP | 005930 삼성전자 | 4.5초 | 11장 | 127.1KB | 22 / 45 / 3 / 2 |
| IND-R | 261 반도체 제조업 | 5.9초 | 14장 | 147.8KB | 4 / 321 / 2 / 11 |
| IND-TP | 261 반도체 제조업 | 0.4초 | 8장 | 150.5KB | 4 / 321 / 6 / 4 |

**IND-R 의 Gap 11건은 실패가 아니다.** 이익풀 · TAM/SAM/SOM · 생산능력 · ASP ·
Porter 4힘 · 시장점유율이 공개 API 에 없다는 사실을 그대로 밝힌 것이다.
없는 것을 만들지 않는 쪽이 명세 §5.4 다.

배포본 실측 (2026-08-02)

| 작업 | 대상 | 전구간 | 장수 | 최대 팩 |
|---|---|---:|---:|---:|
| CORP-R | 005930 | 26.9초 | 11장 | 77.0KB |
| CORP-TP | 005930 | 11.5초 | 10장 | 116.8KB |
| IND-R | 261 | 16.0초 | 14장 | 145.0KB |
| IND-TP | 261 | 9.2초 | 8장 | 150.5KB |

기업 계열이 배포본에서 장수가 하나씩 적은 것은 사업보고서 원문 파싱을 껐기 때문이다.
**산업 계열은 로컬과 장수가 같다** — 원문을 안 쓴다.

**배포본에서 3장이 적은 이유 ★** — 사업보고서 원문 파싱을 기본으로 껐기 때문이다.
서버리스 함수 상한이 60초인데 원문(0.8MB ZIP) 내려받기가 그것을 넘겼다.

| | 로컬 | 배포본 |
|---|---|---|
| DART 재무제표 (H03) | 0.006초(캐시) | 콜드 15.2초 · 웜 0.3초 |
| 사업보고서 원문 | 0.28초 · 파싱 0.4초 | **60초 초과 → 타임아웃** |
| slot 4·5·7 (부문·운영·점유율) | 채워진다 | `G-DATA` 로 비운다 |

원문을 켠 채 두면 H02 가 통째로 죽어 **앞서 모은 근거까지 전부 날아간다.**
부분 결과라도 계속 내는 쪽이 GIC 원칙에 맞다 (불변원칙 §2-2).
배포본에서도 굳이 켜려면 `options: {"include_report_document": true}` 를 실어 보낸다.

검사

```bash
node tests/run_harness.js                 # CORP-R · 삼성전자
node tests/run_harness.js all             # 네 작업 전부
node tests/run_harness.js IND-R 261       # 산업 리서치 · 반도체 제조업
node tests/run_harness.js all https://…   # 배포본에서 네 작업
```

### 에러 응답

| 코드 | 발생 조건 |
|------|-----------|
| 404 | 없는 사용자 ID · 캐시에 없는 종목코드 · 캐시에 없는 거래일 · 모르는 워크스트림/상태 |
| 422 | 타입/형식 불일치, 정렬 불가 필드, 이동평균 기간 > 거래일 수, 프론티어 종목 2개 미만 |
| 503 | **시세 캐시가 비어 있음** → `python3 scripts/fetch_krx.py` 안내 |

---

## 8. 강의 원본과 달라진 점

| | 강의 원본 (`lecture/main.py`) | 이 저장소 |
|---|---|---|
| 구조 | 단일 파일 89줄 | `app/` 14개 모듈 · 레이어드 (`main.py` 는 조립만) |
| 데이터 출처 | 요청마다 KRX 직접 호출 (2~3초) | SQLite 캐시 조회 (수십 ms) |
| 시장 | 유가증권만 | 유가증권 + 코스닥 |
| 응답 형식 | KRX 원본 (`TDD_CLSPRC` = `"71,200"`) | snake_case + 숫자형 (`close` = `71200`) |
| 인증키 위치 | `.key` 또는 `KRX_AUTH_KEY` | `.key`(3형식) · `.env` · 환경변수 |
| 시계열 | 없음 (하루치만) | 거래일을 모아 캔들·수익률 계산 |
| 부가 기능 | 없음 | 집계·검색·정렬·페이지·병렬 수집·차단기 |

정규화 매핑은 실제 응답으로 검증했다 (14개 필드 전부 일치).

| 정규화 | KRX 원본 | | 정규화 | KRX 원본 |
|---|---|---|---|---|
| `code` | `ISU_CD` | | `volume` | `ACC_TRDVOL` |
| `name` | `ISU_NM` | | `value` | `ACC_TRDVAL` |
| `market` | `MKT_NM` | | `market_cap` | `MKTCAP` |
| `close` | `TDD_CLSPRC` | | `listed_shares` | `LIST_SHRS` |
| `change` / `change_rate` | `CMPPREVDD_PRC` / `FLUC_RT` | | `open`/`high`/`low` | `TDD_OPNPRC`/`HGPRC`/`LWPRC` |

---

## 9. KRX OpenAPI로 알 수 없는 것 ★

**KRX 일별매매정보에는 재무제표가 없다.** PER·PBR·ROE·부채비율을 쓸 수 없다는 뜻이다.
그래서 스크리닝과 팩터를 **가격·거래량으로 계산 가능한 지표**로 다시 정의했다.

| 예전 (손으로 넣은 값) | 지금 (실데이터 계산) |
|---|---|
| PER 5~15배, PBR 1.0 이하, ROE 10%↑ | 시가총액 · 평균 거래대금 · 변동성 · 모멘텀 · 20일선 |
| 가치 · 성장 · 수익성 · 안정성 · 모멘텀 · 배당 | 모멘텀 · 안정성 · 유동성 · 규모 · 추세 · 회전율 |

무위험수익률(3.2%)만 상수로 두었고, 나머지는 전부 DB에서 계산한다.

> ⚠️ 효율적 투자선의 기대수익률은 **과거 실적을 단순 연율화**한 값이다.
> 상승장 구간을 담으면 비현실적으로 커진다(예: 250거래일 기준 연 100%↑). 미래 예측이 아니다.

---

## 10. 동작 확인 예시

```bash
# 0) 종목 통합 조회 — 입력이 무엇이든 알아서 판별한다
curl "http://127.0.0.1:8000/api/stock/005930"                    # 국내 코스피 → 005930.KS
curl "http://127.0.0.1:8000/api/stock/247540"                    # 국내 코스닥 → 247540.KQ (자동)
curl -G http://127.0.0.1:8000/api/stock/삼성전자                  # 한글 종목명 → 005930.KS
curl "http://127.0.0.1:8000/api/stock/AAPL?months=3"             # 미국 · 3개월
curl "http://127.0.0.1:8000/api/stock/ZZZZ999"                   # → 404 "알 수 없는 종목입니다"

# 0-1) FRED 거시지표 융합 · 단독 조회
curl "http://127.0.0.1:8000/api/stock/005930?macro=DGS10,DEXKOUS"  # 주가+금리+환율+상관계수
curl http://127.0.0.1:8000/api/fred/status                         # 인증키 상태 (값은 안 나온다)
curl http://127.0.0.1:8000/api/fred/indicators                     # 큐레이션 12종
curl "http://127.0.0.1:8000/api/fred/series/DGS10?start=2026-01-01"
curl "http://127.0.0.1:8000/api/fred/search?q=unemployment&limit=5"

# 1) 서버·인증키·캐시 상태
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/krx/status

# 2) 최근 거래일 전 종목 (거래대금 상위 5)
curl "http://127.0.0.1:8000/api/krx/stocks?size=5"

# 3) 종목 검색 (한글은 --data-urlencode 로 인코딩)
curl -G http://127.0.0.1:8000/api/krx/stocks --data-urlencode "q=삼성전자"

# 4) 삼성전자 최근 20거래일 일봉
curl "http://127.0.0.1:8000/api/krx/stocks/005930/ohlcv?days=20"

# 5) 분석 3종
curl "http://127.0.0.1:8000/api/screening/funnel?window=60"
curl "http://127.0.0.1:8000/api/portfolio/frontier?samples=500"
curl "http://127.0.0.1:8000/api/factors/radar?codes=005930&codes=000660"

# 6) 사용자 CRUD
curl http://127.0.0.1:8000/api/users/1
curl http://127.0.0.1:8000/api/users/999      # → 404
curl -X POST http://127.0.0.1:8000/api/users \
  -H "Content-Type: application/json" \
  -d '{"username":"hong","email":"hong@example.com","age":30}'

# 7) KOSIS 실험 3단계 — 화면이 하는 일을 그대로 따라간다
curl http://127.0.0.1:8000/api/kosis/status                    # 인증키 확인
curl -G http://127.0.0.1:8000/api/kosis/search \
  --data-urlencode "q=소비자물가" --data "size=3"               # 1단계: tblId 얻기
curl "http://127.0.0.1:8000/api/kosis/meta?org_id=101&tbl_id=DT_1J22042"   # 2단계: itmId 후보
curl "http://127.0.0.1:8000/api/kosis/data?org_id=101&tbl_id=DT_1J22042&itm_id=T03&prd_se=M&period_count=12"
```

---

## 11. 문제 해결

### KRX가 `401` 을 준다

KRX는 **두 가지를 다른 메시지로** 구분해 준다. 이걸 보면 원인을 바로 알 수 있다.

| 응답 | 뜻 | 해야 할 일 |
|------|-----|-----------|
| `{"respMsg":"Unauthorized Key"}` | 키 자체가 무효 | 키 값 오타·미발급 확인 |
| `{"respMsg":"Unauthorized API Call"}` | **키는 유효**, 해당 API 이용신청 미승인 | 마이페이지에서 API 이용신청·승인 상태와 **이용기간** 확인 |

키 발급과 API 이용신청은 **별개 절차**다. 키를 받았어도 API마다 따로 신청해야 한다.

직접 확인하려면:

```bash
# 키 없이 호출 → "Unauthorized Key" 가 나오는지
curl -s "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260730"

# 내 키로 호출 → "Unauthorized API Call" 이면 키는 유효한 것
curl -s -H "AUTH_KEY: $KRX_API_KEY" \
  "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260730"
```

인증이 한 번 거부되면 **차단기**가 걸려 이후 호출을 네트워크 없이 즉시 실패시킨다
(250거래일 수집 시 실패가 확정된 요청을 500번 반복하지 않기 위함).
승인 후에는 `POST /api/krx/sync` 를 호출하면 차단기가 풀린다.

### 화면이 비어 있고 `503` 이 뜬다

시세 캐시가 없다는 뜻이다. `python3 scripts/fetch_krx.py` 를 먼저 실행한다.

### 수집 중 `database is locked`

여러 스레드가 동시에 쓸 때 발생한다. 현재는 쓰기를 자물쇠로 직렬화하고
연결을 반드시 닫도록 고쳐 두었다. 그래도 나면 `--workers 2` 로 낮춘다.

### 브라우저에서 화면이 안 열린다 (WSL)

WSL과 Windows 호스트 간 네트워크가 분리돼 있을 수 있다.
`--host 0.0.0.0` 으로 띄우고 `hostname -I` 로 나온 WSL IP 로 접속한다.

---

## 12. 알려진 한계

- **공휴일 미반영** — 주말만 제외하고 요청한다. 휴장일은 KRX가 0건을 주고, 그 사실을 `fetch_log` 에 기록해 다시 요청하지 않는다.
- **당일 데이터** — 장 마감 후에야 올라온다. 최근 7일 이내의 0건은 다시 확인하도록 해 두었다.
- **사용자 저장소가 메모리** — 서버를 내리면 생성한 사용자가 사라진다. `age` 는 재시작마다 재생성된다.
- **ID 채번이 `len(사용자 리스트) + 1`** — 삭제 기능이 생기면 ID가 중복될 수 있다.
- **ApexCharts를 CDN에서 로드** — 오프라인이면 차트 자리에 안내 문구가 뜬다.
- **지표 계산 최초 3.4초** — 전 종목 × 60거래일을 훑는다. 이후에는 캐싱되어 0.1초 미만이다.
- **ETF·채권·선물옵션 미사용** — API 승인은 받았지만 이 저장소는 주식만 쓴다.
- **`/yf` 화면은 6자리 코드에 `.KS` 를 고정으로 붙인다** — 코스닥 종목을 코드만으로 넣으면
  엉뚱한 값이 나올 수 있다(야후가 오류 대신 다른 값을 준다). 이 화면에서는 `.KQ` 를 직접 붙여야 한다.
  **`/stock` 화면은 KRX 시장 구분을 보고 접미사를 정하므로 이 문제가 없다.**
- **종목명 검색은 KRX 캐시에 의존한다** — `python3 scripts/fetch_krx.py` 를 한 번도 안 돌렸으면
  `삼성전자` 같은 한글 입력이 `404` 다. 종목코드·티커 입력은 캐시 없이도 동작한다.
- **FRED 는 미국 지표만 있다** — 원/달러 환율(`DEXKOUS`)도 **미국 쪽 집계**라 한국 공휴일과
  달력이 어긋나고, 발표가 하루이틀 늦다. 국내 종목에 겹칠 때는 이 시차를 감안해서 본다.
- **월별 지표의 상관계수는 참고용** — CPI·실업률은 한 달간 값이 그대로여서 일간 변화율이
  대부분 0 이 된다. 그래서 상관계수가 0 근처로 눌린다. 화면에도 그렇게 안내한다.

### 성능 메모

`window()` 질의에서 `ORDER BY code, bas_dd` 를 쓰면 SQLite가 `idx_code_date` 를 타면서
64만 건을 훑고 행마다 임의 접근을 해 **18.7초**가 걸렸다. 기본키가 `(bas_dd, code)` 라
`ORDER BY bas_dd` 는 이미 정렬된 순서여서 **0.8초**로 끝난다. 종목별 묶음은 파이썬에서 한다.

---

## 13. 다음 단계

- **사용자도 DB로** — 시세는 이미 SQLite를 쓴다. `app/repositories/user_store.py` 를 같은 방식으로 옮기고 `Depends(get_db)` 로 주입
- **자동 수집** — cron 또는 GitHub Actions로 장 마감 후 `scripts/fetch_krx.py --days 1` 실행
- **ETF·지수 확장** — `etp/etf_bydd_trd` · `idx/kospi_dd_trd` 를 `MARKET_APIS` 에 추가하면 같은 구조로 붙는다
- **`/yf` 화면도 통합 판별 쓰기** — `app/services/stock_service.py` 의 `resolve()` 를 `yf_data.normalize_ticker()`
  대신 쓰면 `/yf` 의 코스닥 접미사 문제도 사라진다
- **종목 간 비교** — 지금은 한 번에 한 종목이다. `?compare=000660` 처럼 종목을 하나 더 받아
  같은 100 기준 축에 겹치면 상대 강도를 볼 수 있다
- **거시지표 시차 분석** — 금리가 오른 뒤 **며칠 후** 주가가 반응하는지 보려면
  상관계수를 시차별(lag 1~10일)로 계산해 가장 높은 시차를 찾으면 된다
- **배포** — Docker(`python:3.12-slim`) 또는 Render/Fly.io/Cloud Run

---

## 14. KOSIS 공지사항 RSS 수집 ★

국가통계포털(KOSIS) 공지사항을 긁어 **RSS 2.0 파일**로 저장하는 부속 도구다.
KRX 파이프라인과는 별개로 도는 독립 스크립트이며, **표준 라이브러리만** 쓰므로 설치가 필요 없다.

### 수집 경로 두 가지

| 경로 | 주소 | 범위 |
|------|------|------|
| RSS 피드 | `https://kosis.kr/rss/notice_rss.jsp` | 최신 10여 건만 제공 |
| 상세 페이지 크롤링 | `https://kosis.kr/serviceInfo/noticeDetail.do?boardIdx=N` | 과거 글까지 번호로 직접 수집 |

피드는 최신 글만 주기 때문에, **실질적인 수집은 상세 페이지 크롤링** 쪽이다.
상세 페이지에서 제목 · 작성기관 · 게시일 · 본문 HTML · 첨부파일 목록을 뽑아 RSS `<item>` 으로 만든다.

### 사용법

```bash
# 최신 공지 목록만 보기 (저장 안 함)
python3 scripts/kosis_rss.py --list

# 게시물 번호 하나를 RSS 로 저장 → data/kosis_rss/2200_제목.xml
python3 scripts/kosis_rss.py --board-idx 2200

# 범위 수집 (요청 사이 1초 대기) → 한 파일로 합쳐 저장
python3 scripts/kosis_rss.py --board-range 2200-2220 --delay 1

# 게시물마다 파일 하나씩 + 첨부파일까지 내려받기
python3 scripts/kosis_rss.py --board-range 2200-2220 --split --with-files

# 최신 피드 전체를 한 파일로
python3 scripts/kosis_rss.py --feed --rss-output kosis-latest.xml
```

`test.sh` 는 위 범위 수집을 감싼 실행 스크립트다.

```bash
./test.sh                    # 기본 범위 2200~2220, 5초 간격
./test.sh 2300 2320 3        # 범위·간격 직접 지정
SPLIT=1 ./test.sh            # 게시물마다 파일 하나씩
WITH_FILES=1 ./test.sh       # 첨부파일도 함께
OUT_DIR=./tmp ./test.sh      # 저장 폴더 변경
```

### 출력 형식

폴더가 없으면 자동으로 만든다 (기본 `data/kosis_rss/`). 첨부파일은 `data/kosis_rss/files/<번호>/` 아래에 쌓인다.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:kosis="https://kosis.kr/ns/notice">
  <channel>
    <title>[ KOSIS ] 공지사항</title>
    ...
    <item>
      <title>2020년 11월말 기준 KOSIS 수록자료 현행화율 공개</title>
      <link>https://kosis.kr/serviceInfo/noticeDetail.do?boardIdx=2200</link>
      <description><![CDATA[... 본문 HTML ...]]></description>
      <category>통계청</category>
      <pubDate>Fri, 04 Dec 2020 00:00:00 +0900</pubDate>
      <guid isPermaLink="true">https://kosis.kr/serviceInfo/noticeDetail.do?boardIdx=2200</guid>
      <kosis:attachment name="KOSIS 수록자료 현행화율.xlsx" docId="2200" program="news/news_01Form.jsp" />
    </item>
  </channel>
</rss>
```

- **`pubDate`** — KOSIS 는 `2020-12-04` 처럼 RSS 표준을 지키지 않는 형식으로 주므로, RFC 822 로 변환해 넣는다.
- **`<kosis:attachment>`** — 첨부파일은 RSS 표준에 없는 정보라 별도 네임스페이스로 확장했다.

### 크롤링 시 주의

- **없는 게시물도 HTTP 200** — KOSIS 는 존재하지 않는 `boardIdx` 에도 200 과 빈 껍데기 페이지를 준다.
  제목(`div.b_title`) 유무로 판별해 "없음" 처리하고 건너뛴다. (2202 · 2203 · 2204 처럼 번호가 비어 있는 구간이 있다)
- **요청 간격** — 기본 1초(`--delay`)를 둔다. 범위를 크게 잡을 때 간격을 줄이지 말 것.
- **User-Agent** — 기본 파이썬 UA 는 막힐 수 있어 브라우저 UA 로 요청한다.

---

## 15. 배포 · 공유 ★

강사님께 보여드릴 주소를 만드는 방법.

> ⚠️ **GitHub Pages 로는 이 앱을 올릴 수 없다.** Pages 는 정적 파일만 서빙한다.
> `/api/stock`·`/api/fred` 는 파이썬이 돌아야 하므로 서버가 필요하다.
> (Pages 에 올리려면 데이터를 미리 JSON 으로 구워 두고 프론트가 그걸 읽도록 고쳐야 한다.)

### 15.1 Vercel — 현재 방식

`main.py` 에 `app = FastAPI(...)` 가 있으면 **Vercel 이 알아서 진입점으로 잡는다.**
따로 래퍼 파일을 만들 필요가 없어서, 이 저장소는 설정 파일 두 개만 추가하면 끝난다.

| 파일 | 역할 |
|------|------|
| `vercel.json` | 함수 최대 실행 시간 60초 (야후 호출이 느릴 때 대비) |
| `.vercelignore` | `lecture/`·`scripts/`·`docs/` 등 서버 구동에 없는 파일 제외 |

```bash
npm i -g vercel      # 이미 있으면 생략
vercel login         # 브라우저 인증 (1회)
vercel               # 미리보기 배포 — 주소가 바로 나온다
vercel --prod        # 운영 배포
```

WSL 에서 `node: not found` 가 나면 CLI 가 윈도우에 깔린 것이다. 윈도우 터미널(PowerShell)에서
프로젝트 폴더로 이동해 같은 명령을 쓰면 된다.

### 15.2 인증키 (Vercel 환경변수)

> 🚨 **함정 — `.gitignore` 는 Vercel 에 적용되지 않는다.**
> `vercel deploy` 는 git 이 아니라 **로컬 폴더를 그대로 올린다.** 그래서 `.gitignore` 에
> `.key` 가 있어도 **`.vercelignore` 에 적지 않으면 인증키가 배포 번들에 실려 간다.**
> 실제로 첫 배포에서 이 일이 있었고(`/api/fred/status` 의 `key_source` 가 `.key` 로 나왔다),
> `.vercelignore` 에 `.key`·`.env` 를 추가하고 다시 배포해 바로잡았다.
> HTTP 로 직접 읽히지는 않지만(라우트가 없어 404), **키는 파일이 아니라 환경변수로 넣는 것이 맞다.**

`app/core/secrets.py` 가 **환경변수를 가장 먼저** 보므로 코드는 고칠 필요가 없다.
제대로 들어갔는지는 `/api/fred/status` 의 `key_source` 로 확인한다 —
`환경변수 FRED_API_KEY` 로 나와야 정상이고, `.key` 로 나오면 파일이 함께 올라간 것이다.

```bash
vercel env add FRED_API_KEY production
vercel env add KOSIS_API_KEY production
vercel env ls                       # 등록 확인 (값은 Encrypted 로만 보인다)
```

또는 Vercel 대시보드 → 프로젝트 → **Settings → Environment Variables**.
넣은 뒤에는 **다시 배포해야** 반영된다 (`vercel --prod`).

| 이름 | 없으면 |
|------|--------|
| `FRED_API_KEY` | `/stock` 의 거시지표 겹쳐 보기와 `/api/fred/...` 가 `503` |
| `KOSIS_API_KEY` | `/kosis` 화면이 막힌다 |
| `KRX_API_KEY` | 배포본에서는 어차피 수집을 못 하므로 넣지 않아도 된다 |

### 15.3 서버리스에서 달라지는 것 ★

배포 환경은 **파일을 쓸 수 없고**(읽기 전용), **IP 를 남과 공유**한다. 그래서 두 가지를 손봤다.

**① 파일 쓰기 — DB 경로가 자동으로 옮겨진다**

`krx_store` 는 호출마다 `CREATE TABLE IF NOT EXISTS` 를 실행한다. 이것도 쓰기라서
읽기 전용 환경에서는 **곧바로 500** 이 난다. 그래서 `_resolve_db_path()` 가
쓰기 권한을 확인하고, 막혀 있으면 임시 폴더(`/tmp`)로 옮긴다.
거기 만들어진 DB 는 비어 있으므로 `/krx`·`/quant` 는 **의도한 대로 `503` 안내**를 준다.
(`KRX_DB_PATH` 환경변수로 직접 지정할 수도 있다.)

**② 야후 요청 한도 — `429` 를 구분해서 알려 준다**

`yfinance` 는 공식 API 가 아니라 야후 웹 엔드포인트를 긁어 온다.
야후는 **IP 단위로 한도**를 두는데, 클라우드는 여러 사용자가 IP 를 공유하므로
**내가 조금만 호출해도 이미 걸려 있을 수 있다.**
그래서 `_wrap_error()` 가 429 를 따로 잡아 "요청 한도에 걸렸습니다" 로 안내한다.
막연한 "조회 실패" 보다 원인이 분명해진다.

> ⚠️ **이건 코드로 못 막는 위험이다.** 발표 직전에 `/stock` 을 한 번 열어 확인하고,
> 429 가 뜨면 몇 분 뒤 다시 시도한다. 같은 티커는 60초간 캐시되므로 미리 한 번
> 조회해 두면 발표 중에는 캐시로 응답한다.

### 15.4 배포본에서 동작하는 범위 ★

> **M3 에서 바뀌었다.** 예전에는 `/krx` 캔들 · `/quant` 4종 · `/market` 시장의 폭이
> 배포본에서 전부 죽어 있었다(빈 차트 또는 `503`). 원본 캐시(`data/krx_cache.db`, 123MB)를
> 번들에 못 올리기 때문이었다. **축약본을 따로 만들어 실어 해결했다.**

| 화면 | 배포본 |
|------|--------|
| `/` **대시보드** | ✅ 지수·환율은 야후, 금리는 FRED 키가 있으면 |
| `/market` **시장 상세** | ✅ 캔들·겹쳐보기·비교는 야후, **시장의 폭은 사전집계** |
| `/timeseries` **시계열 분석** | ✅ 국내는 축약본, 해외는 야후 |
| `/stock` **종목 통합 조회** | ✅ 국내·미국 주가 전부 (야후는 인증키 불필요) |
| `/quant` **퀀트 분석** | ✅ 축약본으로 전종목 스크리닝·투자선·팩터 |
| `/krx` **KRX 시세** | ✅ 전종목 표는 KRX 라이브 조회, 캔들은 축약본 |
| `/yf` · `/guide` · `/practice/` · `/docs` | ✅ |
| `/kosis` | ✅ `KOSIS_API_KEY` 를 넣었다면 |

#### 축약본 두 가지 — `scripts/build_krx_bundle.py`

```bash
python3 scripts/build_krx_bundle.py          # 배포 전에 반드시 실행
python3 scripts/build_krx_bundle.py --check  # 지금 무엇이 실려 있는지 확인
```

| 산출물 | 크기 | 담는 것 | git |
|---|---|---|---|
| `data/krx_bundle.db` | ≈30MB | 전종목 최근 **150거래일** OHLCV + 종목 메타 | ❌ 제외 |
| `data/krx_derived.json` | 13KB | 거래일 캘린더 · 시장의 폭 일별 집계 (**282거래일**) | ✅ 포함 |

**왜 JSON 이 아니라 SQLite 인가.** 같은 내용을 JSON 으로 담으면 gzip 12.9MB 인데, 서버리스는
요청마다 인스턴스가 새로 뜰 수 있어 **콜드스타트마다 그걸 통째로 풀어 파싱**하게 된다.
SQLite 를 읽기 전용(`mode=ro`)으로 열면 인덱스로 필요한 행만 집으므로 실측 **0ms** 다.

**왜 150거래일인가.** `/quant` 세 API 는 전부 60거래일, `/krx` 캔들 기본값은 120거래일이다.
150이면 둘 다 손실 없이 덮는다. 기간을 줄이는 대신 **종목은 자르지 않는다** —
스크리닝 깔때기의 첫 단계가 "전체 상장 종목 2,763" 이라 상위 N개로 자르면 그 화면의 존재 이유가 사라진다.

**왜 시장의 폭·캘린더는 따로 빼는가.** 둘 다 날짜별 집계라 종목별 원본이 필요 없다.
13KB 면 git 에 올릴 수 있고, 150거래일이 아니라 **캐시 전 구간(282일)** 을 담을 수 있다.
이 캘린더가 M3 전처리의 `trading_days` 로 들어가 연휴를 결측으로 오판하는 것을 막는다.

#### 로컬과 배포본이 **다른 점** (숨기지 않는다)

| | 로컬 | 배포본 |
|---|---|---|
| 시세 구간 | 282거래일 | **150거래일** |
| 효율적 투자선 관측 | 249일 | **149일** — 응답 `note` 에 그대로 적힌다 |
| 시계열 학습 표본 | 282행 | 150행 — 백테스트 성적이 달라질 수 있다 |
| `/krx` 전종목 표 | 캐시 | KRX **라이브 조회** (하루치는 그 자리에서 받는다) |

대시보드 '데이터 상태' 카드가 지금 어느 모드인지 (`원본 캐시` · `배포 번들` · `라이브 조회`)
와 기준일을 밝힌다. 축약본은 **수동 갱신**이라, 시세를 새로 받은 뒤 스크립트를 다시 돌리고
배포해야 기준일이 따라온다.

> ⚠️ **`.gitignore` 와 `.vercelignore` 가 갈린다.** `krx_bundle.db` 는 git 에 올리지 않지만
> 배포 번들에는 반드시 실려야 한다. `vercel deploy` 는 git 이 아니라 로컬 폴더를 그대로
> 올리므로 이렇게 갈라 둘 수 있다. **빼먹고 배포하면 `/quant` 가 다시 죽는다.**

### 15.5 알아 둘 것

- **첫 요청이 느리다** — 콜드 스타트에 `pandas`·`yfinance` 를 불러오느라 3~10초 걸린다.
  발표 직전에 한 번 열어 두면 이후에는 빠르다.
- **번들 크기** — 의존성만 280MB 쯤 된다. [파이썬 함수 한도는 500MB](https://vercel.com/changelog/python-vercel-functions-bundle-size-limit-increased-to-500mb)라 여유가 있다.
- **정적 파일** — Vercel 은 `public/**` 을 CDN 으로 서빙하지만, 이 저장소는 기존 구조를 지키려고
  `/static` 마운트를 그대로 둔다. 함수를 거쳐 나가므로 조금 느릴 뿐 동작에는 문제가 없다.

### 15.6 대안 — GitHub Codespaces

`.devcontainer/` 설정을 함께 두었으므로 Codespace 를 만들 수 있는 계정이면 이쪽이 더 간단하다.
`Code ▾` → `Codespaces` → `Create codespace on main` 이면 의존성 설치와 서버 실행까지 자동이고,
`data/krx_cache.db` 를 직접 받을 수 있어 **`/krx`·`/quant` 까지 전부** 보여줄 수 있다.

```bash
python3 scripts/fetch_krx.py --days 60   # KRX_API_KEY 필요, 2~3분
```

다만 전달 포트는 **기본이 Private** 라, 그대로 두면 남이 열었을 때 로그인 화면이나 404 가 뜬다.
`.devcontainer/start.sh` 가 자동으로 Public 전환을 시도하고 실패하면 안내를 띄운다.
직접 바꾸려면 **[포트] 탭 → `8000` 우클릭 → 포트 공개 범위 → Public** 이다.

> `devcontainer.json` 만으로는 공개 범위를 지정할 수 없다
> (`"visibility": "public"` 은 아직 미지원). 조직 정책으로 Public 이 막혀 있으면
> **Org** 범위로 바꾸면 조직 구성원은 열 수 있다.

### 15.7 그 밖의 방법

| 방법 | 특징 |
|------|------|
| 로컬 + Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8000` — 계정 없이 임시 공개 주소. **내 IP 를 쓰므로 야후 429 위험이 없고 96MB 캐시도 그대로 쓴다.** 발표용으로는 사실 이게 가장 안전하다 |
| Hugging Face Spaces | GitHub 계정과 무관. Docker SDK 로 FastAPI 구동, Secrets 기능 있음 |
| Fly.io · Google Cloud Run | `flyctl launch` · `gcloud run deploy --source .` 로 로컬에서 바로 배포 |

어느 쪽이든 인증키는 **환경변수**로 넣으면 코드 수정 없이 그대로 동작한다.

---

## 라이선스

[MIT License](LICENSE)
