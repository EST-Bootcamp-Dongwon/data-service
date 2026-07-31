# api-test — FastAPI + KRX OpenAPI 실습

강사님 배포 자료(`lecture/`)의 **KRX 일별 시세 조회 예제**를, 내 방식대로 다시 설계해 합친 저장소다.
사용자 CRUD API 실습에서 시작해 **한국거래소 실제 시세**를 다루는 백엔드로 확장했다.

| 항목 | 내용 |
|------|------|
| 프레임워크 | FastAPI 0.141 + Uvicorn 0.52 |
| 데이터 출처 | **KRX OpenAPI** (유가증권·코스닥 일별매매정보) · **KOSIS OpenAPI** · **야후 파이낸스** |
| 시세 저장소 | SQLite (`data/krx_cache.db`) — 약 232거래일 · 64만 행 · 96MB |
| 사용자 저장소 | 메모리 리스트 (`app/repositories/user_store.py`) — **서버 재시작 시 초기화** |
| 외부 라이브러리 | KRX·KOSIS·DB는 표준 라이브러리만. **야후 파이낸스 화면·스크립트만** `yfinance` · `matplotlib` |

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
│   │   ├── market_router.py    분석 API       (/api/...)
│   │   └── page_router.py      화면(HTML) 라우트
│   ├── services/           ← 서비스   : 비즈니스 로직
│   │   └── market_data.py      스크리닝 · 투자선 · 팩터
│   ├── repositories/       ← 저장소   : 저장 · 조회
│   │   ├── krx_store.py        KRX 시세 (SQLite)
│   │   └── user_store.py       실습용 사용자 30명 (메모리 리스트)
│   ├── clients/            ← 외부 연동 : 외부 API 호출 · 응답 정규화
│   │   ├── krx_data.py         KRX OpenAPI
│   │   ├── kosis_data.py       KOSIS OpenAPI + 차트용 변환
│   │   └── yf_data.py          야후 파이낸스(yfinance) + 차트용 변환
│   └── core/               ← 공통 유틸 : 거래일 · KST · 인증키 · 문서
│       ├── trading_calendar.py
│       ├── secrets.py          KRX·KOSIS 인증키 로딩
│       └── api_docs.py         Swagger(/docs) 설명 글
├── scripts/
│   ├── fetch_krx.py        KRX 캐시를 채우는 CLI 수집 스크립트
│   ├── yf.py               야후 파이낸스 차트 스크립트 (matplotlib · 단독 실행)
│   └── kosis_rss.py        KOSIS 공지 크롤러 → RSS 2.0 변환
├── test.sh                 KOSIS 공지 범위 수집 실행 스크립트
├── static/
│   ├── pages/              화면 7종 (index · kosis · krx · yf · quant · users · tetris)
│   └── assets/             공통 app.css · app.js
├── data/
│   ├── krx_cache.db        시세 캐시 (.gitignore 대상)
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

> KOSIS는 **캐시를 두지 않는다.** 매번 다른 통계표를 실험하는 화면이라 미리 쌓아 둘 대상이
> 정해지지 않기 때문이다. 반대로 KRX는 전 종목 시세라 대상이 고정되어 캐시가 이득이다.

| 파일 | 줄 수 | 역할 |
|------|------|------|
| `app/clients/krx_data.py` | 338 | KRX HTTP 호출, 대문자 축약 필드 → snake_case 정규화, 집계·정렬 |
| `app/clients/kosis_data.py` | 419 | KOSIS 호출·재시도, 평평한 응답 → 차트용 `series`/`categories` 변환 |
| `app/routers/kosis_router.py` | 227 | KOSIS 실험 3단계의 DTO 와 엔드포인트 |
| `app/clients/yf_data.py` | 274 | yfinance 호출, 가격 5종 정규화·Y축 범위 계산, 60초 메모리 캐시 |
| `app/routers/yf_router.py` | 159 | 야후 시세 API 의 DTO 와 엔드포인트 |
| `scripts/yf.py` | 202 | 야후 가격 지표를 matplotlib 막대+꺾은선으로 그리는 CLI 스크립트 |
| `app/core/secrets.py` | 85 | KRX·KOSIS 인증키 로딩 (환경변수 → `.env` → `.key`) |
| `app/repositories/krx_store.py` | 349 | `data/krx_cache.db` 스키마·수집·조회. 병렬 수집과 쓰기 직렬화 |
| `app/services/market_data.py` | 532 | 종목 지표 계산 → 스크리닝 깔때기 · 효율적 투자선 · 팩터 점수 |
| `app/routers/market_router.py` | 316 | 분석 API 의 DTO 와 엔드포인트 |
| `app/routers/krx_router.py` | 272 | KRX 시세 API 의 DTO 와 엔드포인트 |
| `app/core/trading_calendar.py` | 61 | 거래일·KST 유틸 (순환 import 방지용 공통 모듈) |
| `scripts/fetch_krx.py` | 92 | 캐시를 채우는 CLI 수집 스크립트 |
| `scripts/kosis_rss.py` | 439 | KOSIS 공지 크롤링 → RSS 2.0 변환 (표준 라이브러리만 사용) |
| `app/routers/user_router.py` | 163 | 사용자 CRUD 의 DTO 와 엔드포인트 |
| `app/repositories/user_store.py` | 67 | 실습용 사용자 30명을 메모리 리스트로 보관 |
| `app/routers/page_router.py` | 67 | 화면(HTML) 라우트. 라이브러리가 없는 화면은 빼고 등록 |
| `app/core/api_docs.py` | 113 | Swagger 태그 설명·API 개요 (동작에는 영향 없음) |
| `main.py` | 131 | **앱 조립만** — 라우터 등록 · 정적 서빙 · CORS · `/health` |

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

> `yfinance` · `matplotlib` 은 **야후 파이낸스 화면(`/yf`)과 스크립트(`scripts/yf.py`) 전용**이다.
> 이 둘을 안 쓸 거면 `fastapi uvicorn` 만 있어도 나머지 화면은 전부 동작한다.

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
| http://127.0.0.1:8000 | 홈 · 사용자 API 테스트 |
| http://127.0.0.1:8000/krx | KRX 일별 시세 화면 |
| http://127.0.0.1:8000/yf | 야후 파이낸스 시세 화면 |
| http://127.0.0.1:8000/quant | 퀀트 분석 화면 |
| http://127.0.0.1:8000/tetris | Canvas 테트리스 |
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

| 주소 | 파일 | 화면 |
|------|------|------|
| `/` | `static/pages/index.html` | **랜딩** — 화면 안내 · 서버/인증키/캐시 상태 |
| `/kosis` | `static/pages/kosis.html` | KOSIS 통계 실험실 |
| `/krx` | `static/pages/krx.html` | KRX 일별 시세 |
| `/yf` | `static/pages/yf.html` | 야후 파이낸스 시세 |
| `/quant` | `static/pages/quant.html` | 퀀트 분석 |
| `/users` | `static/pages/users.html` | 사용자 API 테스트 (CRUD) |
| `/tetris` | `static/pages/tetris.html` | Canvas 테트리스 |
| — | `static/assets/app.css` · `app.js` | 7개 화면 공통 스타일·유틸 |

> 화면 목록은 `static/assets/app.js` 의 `PAGES` 배열 **한 곳**에만 있다.
> 새 화면을 추가하면 여기 한 줄만 넣으면 모든 화면의 내비게이션에 반영된다.

### `/` — 랜딩

어디로 갈지 고르는 화면. 상단 배지로 **서버 · KRX 인증키 · 시세 캐시 · KOSIS 인증키** 상태를 한눈에 보여준다.
세 상태 요청은 서로 무관하므로 `Promise.allSettled` 로 한꺼번에 보내고, **하나가 실패해도 나머지는 표시**한다.

### `/users` — 사용자 API 테스트

FastAPI의 기본기를 눌러보는 화면.

- `GET /health` — 서버 상태 (초록/빨강 점으로 표시)
- `GET /api/users` — Mock 30명 조회, 화면 내 검색, 평균 나이 등 통계 타일
- `GET /api/users/{id}` — 단건 조회 + **`404`·`422` 를 일부러 내보는 버튼**
- `POST /api/users` — 생성 후 목록 자동 갱신, 방금 만든 행을 강조

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

### `/tetris` — Canvas 테트리스

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

### 시장 분석 — `/api/...`

| Method | Path | 설명 | 성공 |
|--------|------|------|------|
| GET | `/api/stocks?limit=30` | 종목 목록 (거래대금 상위순) | 200 |
| GET | `/api/stocks/{code}/ohlcv?count=120&ma=5&ma=20` | 일봉 + 이동평균 | 200 |
| GET | `/api/screening/funnel?window=60` | 스크리닝 깔때기 | 200 |
| GET | `/api/portfolio/frontier?samples=1200&codes=005930&codes=000660` | 효율적 투자선 | 200 |
| GET | `/api/factors/radar?codes=005930&codes=000660` | 팩터 점수 | 200 |

### 에러 응답

| 코드 | 발생 조건 |
|------|-----------|
| 404 | 없는 사용자 ID · 캐시에 없는 종목코드 · 캐시에 없는 거래일 |
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

### 성능 메모

`window()` 질의에서 `ORDER BY code, bas_dd` 를 쓰면 SQLite가 `idx_code_date` 를 타면서
64만 건을 훑고 행마다 임의 접근을 해 **18.7초**가 걸렸다. 기본키가 `(bas_dd, code)` 라
`ORDER BY bas_dd` 는 이미 정렬된 순서여서 **0.8초**로 끝난다. 종목별 묶음은 파이썬에서 한다.

---

## 13. 다음 단계

- **사용자도 DB로** — 시세는 이미 SQLite를 쓴다. `app/repositories/user_store.py` 를 같은 방식으로 옮기고 `Depends(get_db)` 로 주입
- **자동 수집** — cron 또는 GitHub Actions로 장 마감 후 `scripts/fetch_krx.py --days 1` 실행
- **ETF·지수 확장** — `etp/etf_bydd_trd` · `idx/kospi_dd_trd` 를 `MARKET_APIS` 에 추가하면 같은 구조로 붙는다
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

## 라이선스

[MIT License](LICENSE)
