# api-test — FastAPI 실습

강사님 공유 문서(`api연습.md`)의 FastAPI 기본 실습 코드.
사용자(User) 리소스를 다루는 간단한 백엔드 API 서버로, **Swagger 자동 문서화**를 함께 연습한다.

| 항목 | 내용 |
|------|------|
| 프레임워크 | FastAPI 0.141 + Uvicorn 0.52 |
| 데이터 저장소 | 메모리 리스트(`db_users`) — **서버 재시작 시 초기화** |
| 초기 데이터 | Mock 사용자 30명(`user1` ~ `user30`) 자동 생성 |
| 인증 | 없음 (실습용) |

## 저장소 구성 ★

**내 실습 코드**와 **강사님 배포 원본**을 같은 저장소 안에서 분리해 둔다.
원본을 건드리지 않으므로 강의 자료가 갱신돼도 충돌이 나지 않는다.

| 위치 | 내용 | 수정 |
|------|------|------|
| 저장소 루트 | 내 실습 코드 (`main.py` · `market_*.py` · `static/`) | 자유롭게 |
| [`lecture/`](lecture) | 강사님 원본 [edumgt/api-test2](https://github.com/edumgt/api-test2) — **서브모듈** | ❌ 읽기 전용 |

```bash
# 최초 clone — 서브모듈까지 함께 받는다
git clone --recurse-submodules https://github.com/EST-Bootcamp-Dongwon/api-test.git

# 이미 clone 했다면
git submodule update --init --recursive

# 강의 자료 최신화 (매일 아침)
git submodule update --remote lecture
git add lecture && git commit -m "chore: 강의 자료(api-test2) 갱신" && git push
```

> `lecture/` 는 커밋 포인터만 기록하므로 강사님 파일이 이 저장소에 복사되지 않는다.
> 강의 원본과 내 코드는 `main.py` 구조가 서로 다르다 — 원본은 KRX 프록시 단일 파일,
> 내 코드는 사용자 CRUD + 차트 API를 레이어드 구조로 분리했다. 그래서 병합하지 않고 분리 보관한다.

## 환경 구성

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt      # 또는: pip install fastapi uvicorn
```

> 우분투에서 `ensurepip is not available` 오류가 나면
> `sudo apt install -y python3.12-venv` 를 먼저 설치한다.
> `externally-managed-environment` 오류는 **가상환경 활성화를 안 한 것**이 원인이다.

## 실행

```bash
uvicorn main:app --reload
```

| 주소 | 설명 |
|------|------|
| http://127.0.0.1:8000 | 테스트 화면 (`static/index.html`) |
| http://127.0.0.1:8000/ui | 위와 같은 화면 (구 경로 호환) |
| http://127.0.0.1:8000/static/index.html | 정적 파일 직접 접근 |
| http://127.0.0.1:8000/health | 헬스 체크 (JSON) |
| http://127.0.0.1:8000/docs | Swagger UI (자동 생성 문서) |
| http://127.0.0.1:8000/redoc | ReDoc 문서 |

- `main:app` — `main.py` 안의 `app` 객체를 실행
- `--reload` — 코드 수정 시 서버 자동 재시작 (개발용)

## 웹 화면 서빙 구조

API(`main.py`)와 화면(`static/index.html`)을 **한 서버·한 오리진**에서 함께 서빙한다.

```
api-test/
├─ main.py              # FastAPI 앱 (API + 화면 라우트)
├─ market_router.py     # 차트 데이터 라우터 (컨트롤러 계층)
├─ market_data.py       # 목업 시세 생성 (서비스 계층)
├─ static/
│  └─ index.html        # 테스트 화면 (HTML·CSS·JS 단일 파일)
└─ lecture/             # 강사님 원본 (서브모듈 · 읽기 전용)
```

`main.py` 의 관련 설정은 3가지다.

| 설정 | 코드 | 역할 |
|------|------|------|
| 정적 디렉터리 마운트 | `app.mount("/static", StaticFiles(directory=STATIC_DIR))` | `static/` 하위 파일을 `/static/...` 으로 노출 |
| 루트 화면 라우트 | `GET /` → `FileResponse(STATIC_DIR / "index.html")` | 주소만 치면 바로 화면이 열린다 |
| 호환 라우트 | `GET /ui` | 같은 화면 반환 (기존 링크용) |

- 경로는 `FilePath(__file__).parent / "static"` 으로 계산하므로 **어느 디렉터리에서 실행해도** 파일을 찾는다.
- 두 화면 라우트 모두 `include_in_schema=False` — Swagger 문서에는 나오지 않는다.
- 화면의 JS 는 `API_BASE = ''`(같은 오리진 상대 경로)로 호출하므로 **CORS 문제가 없다.**
  `index.html` 을 `file://` 로 직접 열었을 때만 `http://127.0.0.1:8000` 을 붙이며,
  이 경우를 위해 `CORSMiddleware(allow_origins=["*"])` 를 열어두었다. (실습용 설정)
- `--reload` 는 `.py` 만 감시하지만, `index.html` 은 요청마다 디스크에서 읽으므로
  **브라우저 새로고침만으로** 수정이 반영된다.

## 국내 주식 시세 차트 (Mock · ApexCharts)

화면 맨 위에 있는 차트다. [ApexCharts 의 Advanced Stock Chart](https://apexcharts.com/apexstock/demos/advanced/)
구성을 참고했고, **백엔드 API 없이 `index.html` 안의 JS 가 데이터를 직접 만든다.**
(FastAPI 쪽에는 시세 엔드포인트가 없다.)

| 구성 | 내용 |
|------|------|
| 메인 차트 | 캔들스틱 + 이동평균선 `MA5`·`MA20`·`MA60` (mixed chart) |
| 하단 차트 | 거래량 막대 — **브러시**로 위 차트 구간을 조절 (`chart.brush.target`) |
| 종목 | 삼성전자·SK하이닉스·현대차·NAVER·카카오(KOSPI), 에코프로비엠(KOSDAQ) |
| 기간 | 1개월 / 3개월 / 6개월 / 1년 / 전체 (전체 500거래일 ≒ 2년 생성) |
| 색상 | **상승 빨강 · 하락 파랑** — 국내 증시 관행 (미국과 반대) |

### 목업 데이터를 만드는 방식

- **랜덤워크**: 일간 수익률을 `drift + 정규분포난수 × 변동성` 으로 누적한다(기하 브라운 운동에 가까운 형태).
- **시드 고정**: 난수 시드가 **종목코드**(`mulberry32(Number(code))`)라
  새로고침해도 같은 차트가 나온다. → `GET /users` 의 `age` 가 재시작마다 바뀌던 문제를 여기서는 피했다.
- **최근 종가 고정**: 생성한 시계열 전체를 비례 보정해 **마지막 종가가 기준가와 정확히 일치**하게 맞춘다.
- **KRX 호가단위**: 가격대별 최소 주문 단위(2023년 개정 기준)로 반올림한다.
  반올림 때문에 `고가 < 종가` 같은 모순이 생길 수 있어 사후에 다시 정합성을 맞춘다.
- **거래일**: 오늘(KST)부터 거꾸로 주말을 제외해 모은다.
- **이동평균**: 전체 구간으로 계산한 뒤 화면 구간만 잘라 쓰므로 구간 왼쪽 끝의 MA 도 정확하다.
- **날짜 축**: ApexCharts 의 `datetime` 축은 UTC 로 라벨을 찍기 때문에
  날짜가 하루 밀리지 않도록 **UTC 자정 타임스탬프**로 만든다.

> ⚠️ 값은 전부 목업이며 **실제 시세가 아니다.** 공휴일·거래정지·액면분할 등은 반영하지 않았다.

### 알려진 제약

- ApexCharts 를 **CDN**(`cdn.jsdelivr.net`)에서 불러온다. 오프라인이면 차트 자리에 안내 문구가 표시된다.
- 거래량 막대는 상승/하락 구분 없이 단색이다(브러시 영역이라 참고 사이트와 동일).
- 종목·기간을 바꿀 때 차트를 재생성한다. 연타로 렌더가 겹치지 않도록 재진입 가드를 두었다.

## API 목록

| Method | Path | 태그 | 설명 | 성공 코드 |
|--------|------|------|------|-----------|
| GET | `/` | 기본 | 테스트 화면(HTML) 반환 — 문서에는 미표시 | 200 |
| GET | `/ui` | — | `/` 와 같은 화면 (구 경로 호환) — 문서에는 미표시 | 200 |
| GET | `/static/{파일}` | — | 정적 파일 서빙 (`StaticFiles` 마운트) | 200 |
| GET | `/health` | 기본 | 서버 상태 확인 (헬스 체크) | 200 |
| GET | `/users` | 사용자 | 사용자 전체 목록 조회 | 200 |
| GET | `/users/{user_id}` | 사용자 | 사용자 단건 조회 | 200 |
| POST | `/users` | 사용자 | 사용자 생성 | 201 |

### 에러 응답

| 코드 | 발생 조건 |
|------|-----------|
| 404 | `GET /users/{user_id}` — 해당 ID의 사용자가 없음 (`{"detail": "사용자를 찾을 수 없습니다."}`) |
| 422 | `user_id` 가 정수가 아니거나, `POST` 요청의 필수 필드 누락·타입 불일치 (FastAPI/Pydantic 자동 검증) |

## 데이터 모델

`UserCreate` — 요청 본문(`POST /users`)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `username` | str | ✅ | 사용자 이름 |
| `email` | str | ✅ | 이메일 주소 |
| `age` | int \| None | ❌ | 나이 (선택) |

`UserResponse` — 응답 본문

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | int | 사용자 고유 ID (서버가 `len(db_users) + 1` 로 자동 부여) |
| `username` | str | 사용자 이름 |
| `email` | str | 이메일 주소 |
| `age` | int \| None | 나이 |

그 외 `ErrorResponse`(`detail`), `HealthResponse`(`status`·`message`) 는 Swagger 문서용 응답 스키마다.

## 동작 확인 예시

```bash
# 1) 서버 상태 확인
curl http://127.0.0.1:8000/health

# 2) 전체 조회 (Mock 30명)
curl http://127.0.0.1:8000/users

# 3) 단건 조회
curl http://127.0.0.1:8000/users/1
curl http://127.0.0.1:8000/users/999   # → 404

# 4) 생성
curl -X POST http://127.0.0.1:8000/users \
  -H "Content-Type: application/json" \
  -d '{"username": "hong", "email": "hong@example.com", "age": 30}'
```

## 실습 포인트 / 알려진 한계

의도적으로 단순화한 부분이며, 다음 단계에서 개선 대상이다.

- **저장소가 메모리** — 서버를 내리면 생성한 사용자가 모두 사라진다.
- **`age` 는 매번 달라진다** — Mock 데이터의 나이는 `random.randint(18, 60)` 이라 재시작(`--reload` 포함)마다 재생성된다.
- **ID 채번이 `len(db_users) + 1`** — 삭제 기능이 생기면 ID가 중복될 수 있다.
- **이메일 중복 검사 없음** — 같은 이메일로 여러 번 생성된다.
- **페이지네이션·필터 없음** — `GET /users` 는 항상 전체를 반환한다.

## Swagger 문서화 설정 (`main.py`)

`/docs` 를 읽기 좋게 만들기 위해 아래 옵션을 사용했다.

- `openapi_tags=TAGS_METADATA` — 엔드포인트를 `기본` · `사용자` 그룹으로 분류
- `description=API_DESCRIPTION` — `/docs` 상단에 마크다운 개요 렌더링
- 각 라우트의 `summary` · docstring · `responses` — 요약, 상세 설명, 상태 코드별 예시 응답
- `Field(..., description=..., examples=[...])`, `model_config["json_schema_extra"]` — "Try it out" 기본 요청값

## 다음 단계 (문서 5~7번)

- **DB 연동**: `pip install sqlalchemy` → `database.py`(engine·SessionLocal) + `Depends(get_db)`
- **라우터 분리**: `APIRouter` 로 `routers/users.py`, `routers/items.py` 분리 후 `include_router`
- **배포**: `pip freeze > requirements.txt` → Docker(`python:3.11-slim`) 또는 Render/Fly.io/Cloud Run

## 라이선스

[MIT License](LICENSE)
