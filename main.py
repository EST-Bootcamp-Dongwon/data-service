# FastAPI 애플리케이션 진입점.
# uvicorn main:app --reload 로 실행하면 이 파일의 `app` 객체를 서버가 읽어간다.

# FastAPI: 앱 본체 / HTTPException: 에러 응답을 예외로 던지는 도구
# Path: 경로 파라미터(/users/{user_id})에 설명·검증을 붙이는 도구
# status: 200, 201 같은 상태 코드를 이름으로 쓰게 해 주는 상수 모음
from fastapi import FastAPI, HTTPException, Path, status
# 브라우저의 교차 출처(CORS) 정책을 완화해 주는 미들웨어
from fastapi.middleware.cors import CORSMiddleware
# 파일 하나를 그대로 응답으로 돌려주는 클래스 (index.html 반환용)
from fastapi.responses import FileResponse
# 디렉터리 전체를 정적 파일로 서빙하는 클래스 (/static/... 경로용)
from fastapi.staticfiles import StaticFiles
# BaseModel: 요청·응답 데이터 모델의 부모 클래스 / Field: 필드별 설명·예시·기본값 지정
from pydantic import BaseModel, Field
# Optional[X] = X 또는 None / List[X] = X 의 배열
from typing import Optional, List
# 파일 경로 계산용. fastapi 의 Path 와 이름이 겹쳐서 FilePath 로 별칭을 준다.
from pathlib import Path as FilePath
# Mock 사용자의 나이를 무작위로 만들기 위해 사용
import random

# 차트 데이터 API 묶음(/api/...)을 가져온다. router 라는 이름이 흔해서 별칭을 붙인다.
from market_router import router as market_router   # 분석 API (/api/...)
from krx_router import router as krx_router         # KRX 일별 시세 API (/api/krx/...)

# --------------------------------------------------
# API 문서(Swagger) 메타데이터
# --------------------------------------------------
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
        "description": "사용자 조회·생성 API. 서버 시작 시 **Mock 데이터 30명**이 자동 생성된다.",
    },
    {
        "name": "KRX 일별 시세",
        # 괄호로 감싸면 여러 줄 문자열을 자동으로 이어 붙일 수 있다 (줄바꿈은 들어가지 않는다)
        "description": (
            "한국거래소 OpenAPI의 **유가증권·코스닥 일별매매정보**를 조회한다. "
            "받은 데이터는 `krx_cache.db`(SQLite)에 쌓아 두고 여기서 읽는다. "
            "호출 코드는 `krx_data.py`, 저장은 `krx_store.py` 에 있다."
        ),
    },
    {
        "name": "시장 분석",
        "description": (
            "캐시에 쌓인 실제 시세로 계산하는 분석 API. "
            "계산 로직은 `market_data.py`(서비스), 응답 형식은 `market_router.py`(컨트롤러)에 있다. "
            "**같은 거래일에 대해서는 항상 같은 값**이 나온다."
        ),
    },
]

# /docs 상단에 마크다운으로 렌더링되는 API 개요.
# 삼중 따옴표(""" """)는 줄바꿈을 그대로 유지하는 문자열이다.
API_DESCRIPTION = """
FastAPI로 만든 백엔드 API 서버입니다. **한국거래소(KRX) OpenAPI의 실제 시세**를 다룹니다.

## 계층 구조

```
KRX OpenAPI → krx_data(호출·정규화) → krx_store(SQLite 캐시) → market_data(분석) → 라우터 → 화면
```

| 파일 | 역할 |
| --- | --- |
| `krx_data.py` | KRX 와 HTTP 통신, 대문자 축약 필드를 snake_case 로 정규화 |
| `krx_store.py` | 받은 일별 데이터를 `krx_cache.db` 에 쌓고 꺼냄 |
| `market_data.py` | 쌓인 데이터로 스크리닝·포트폴리오·팩터 계산 |
| `fetch_krx.py` | 캐시를 채우는 수집 스크립트 (`python3 fetch_krx.py`) |

## 화면

| 주소 | 화면 |
| --- | --- |
| [`/`](/) | 홈 · 사용자 API 테스트 |
| [`/krx`](/krx) | KRX 일별 시세 — 전 종목 표, 거래대금·등락률 차트, 종목별 캔들 |
| [`/quant`](/quant) | 퀀트 분석 — 스크리닝 깔때기, 효율적 투자선, 팩터 방사형 |
| [`/tetris`](/tetris) | Canvas 테트리스 |

## 사용 순서

1. 터미널에서 `python3 fetch_krx.py` 로 시세 캐시를 채운다 (최초 1회, 약 7분)
2. `GET /health` — 서버가 살아있는지 확인
3. `GET /api/krx/status` — 인증키·캐시 상태 확인
4. `GET /api/krx/stocks` — 최근 거래일 전 종목 조회
5. `GET /users` — Mock 사용자 30명 조회

## 참고

- 사용자 데이터는 **메모리 리스트**라 서버를 끄면 사라지고, `age` 는 재시작마다 재생성됩니다.
- KRX 일별매매정보에는 **재무제표가 없습니다.** PER·PBR·ROE 대신 가격·거래량 지표를 씁니다.
- 인증키는 `.key` · `.env` · 환경변수에서 읽으며, **응답에 값이 노출되지 않습니다.**
"""

# FastAPI 앱 인스턴스 생성. 여기에 넘긴 값들은 전부 /docs 문서에 반영된다.
app = FastAPI(
    title="My FastAPI Backend",          # 문서 최상단 제목
    description=API_DESCRIPTION,         # 제목 아래 마크다운 설명
    version="1.0.0",                     # API 버전 (문서 표시용)
    openapi_tags=TAGS_METADATA,          # 위에서 정의한 태그 그룹 설명
    license_info={"name": "MIT License", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",      # Swagger UI 경로
    redoc_url="/redoc",    # ReDoc 경로
)

# --------------------------------------------------
# 정적 화면(static/index.html) 서빙
# --------------------------------------------------
# 같은 오리진에서 서빙하므로 화면(JS fetch)에서 CORS 문제가 발생하지 않는다.
# __file__ 은 이 파일(main.py)의 경로 → .parent 는 그 폴더 → / "static" 으로 하위 폴더를 가리킨다.
# 실행 위치(cwd)와 무관하게 항상 같은 폴더를 가리키므로 상대경로보다 안전하다.
STATIC_DIR = FilePath(__file__).parent / "static"
# /static/파일명 으로 요청하면 해당 폴더의 파일을 그대로 내려준다.
# name="static" 은 코드에서 url_for("static", ...) 로 URL 을 만들 때 쓰는 식별자다.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 실습용이라 모든 오리진을 허용한다.
# index.html 을 file:// 로 직접 열어도 API 호출이 되도록 하기 위한 설정이며,
# 실제 서비스에서는 allow_origins 에 도메인을 명시해야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 어떤 도메인에서 호출해도 허용
    allow_methods=["*"],   # GET, POST, PUT, DELETE ... 전부 허용
    allow_headers=["*"],   # 어떤 요청 헤더든 허용
)

# API 라우터 등록.
# include_router 를 호출하는 순간 router 에 정의된 모든 경로가 app 에 붙는다.
# 경로가 더 구체적인 KRX 라우터를 먼저 등록해 `/api/krx/...` 가 올바르게 매칭되게 한다.
app.include_router(krx_router)
app.include_router(market_router)


# --------------------------------------------------
# 화면 라우트
# --------------------------------------------------
# 화면은 기능별로 파일을 나눠 두었다. 파일명만 봐도 무슨 화면인지 알 수 있어 수정이 쉽다.
#   /        → static/index.html   홈 · 사용자 API 테스트
#   /krx     → static/krx.html     KRX 일별 시세
#   /quant   → static/quant.html   퀀트 분석 (스크리닝·투자선·팩터)
#   /tetris  → static/tetris.html  Canvas 테트리스
PAGES = {
    "/": "index.html",
    "/krx": "krx.html",
    "/quant": "quant.html",
    "/tetris": "tetris.html",
    "/ui": "index.html",       # 기존 링크 호환용
}


def _page(filename: str):
    """지정한 HTML 파일을 그대로 내려주는 처리기를 만든다.

    화면마다 똑같은 함수를 4번 쓰지 않기 위해, 함수를 만들어 주는 함수를 쓴다.
    (`filename` 을 인자로 받아 그 값을 기억하는 새 함수를 돌려준다 — 클로저)
    """
    def handler():
        # FileResponse 는 파일을 열어 스트리밍으로 내려주고, 확장자로 Content-Type 을 자동 판단한다.
        return FileResponse(STATIC_DIR / filename)
    return handler


# 반복문으로 라우트를 한 번에 등록한다.
# 데코레이터(@app.get) 대신 app.get(...)(함수) 형태로 직접 호출하는 방식이다.
for _path, _file in PAGES.items():
    app.get(_path, include_in_schema=False)(_page(_file))


# --------------------------------------------------
# 데이터 모델 정의
# --------------------------------------------------
# BaseModel 을 상속하면 (1) 요청 JSON 자동 검증 (2) 응답 자동 직렬화
# (3) Swagger 스키마 자동 생성 이 한꺼번에 된다.
class UserCreate(BaseModel):
    """POST /users 의 요청 본문(Request Body) 형식"""

    # Field(...) 의 ... (Ellipsis) 은 "필수 항목"이라는 뜻이다. 없으면 422 에러.
    username: str = Field(..., description="사용자 이름", examples=["hong"])
    email: str = Field(..., description="이메일 주소", examples=["hong@example.com"])
    # Optional[int] + default=None → 요청에서 생략할 수 있는 선택 항목
    age: Optional[int] = Field(default=None, description="나이 (선택)", examples=[30])

    # Swagger UI 의 "Try it out" 에 채워지는 요청 예시
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"username": "hong", "email": "hong@example.com", "age": 30}
            ]
        }
    }


class UserResponse(BaseModel):
    """사용자 조회·생성의 응답 형식.

    response_model 로 지정하면 여기에 없는 필드는 응답에서 잘려 나간다.
    (비밀번호 같은 내부 값이 실수로 새어 나가는 것을 막아 준다.)
    """

    id: int = Field(..., description="사용자 고유 ID (자동 부여)", examples=[1])
    username: str = Field(..., description="사용자 이름", examples=["user1"])
    email: str = Field(..., description="이메일 주소", examples=["user1@example.com"])
    age: Optional[int] = Field(default=None, description="나이", examples=[23])


class ErrorResponse(BaseModel):
    """에러 응답 공통 형식 (FastAPI 의 HTTPException 기본 형태)"""

    # HTTPException(detail=...) 로 넘긴 값이 이 필드에 담겨 나간다.
    detail: str = Field(..., description="에러 메시지", examples=["사용자를 찾을 수 없습니다."])


class HealthResponse(BaseModel):
    """루트 엔드포인트 응답 형식"""

    status: str = Field(..., description="서버 상태", examples=["ok"])
    message: str = Field(
        ..., description="상태 설명", examples=["FastAPI 백엔드 서버가 작동 중입니다."]
    )


# --------------------------------------------------
# ✅ Mock 데이터 생성 (30명)
# --------------------------------------------------
def generate_mock_users():
    """user1 ~ user30 을 딕셔너리 리스트로 만들어 반환한다."""
    users = []                     # 결과를 담을 빈 리스트
    for i in range(1, 31):         # 1 부터 30 까지 (31 은 포함되지 않는다)
        user = {
            "id": i,                                   # ID 는 1부터 순서대로
            "username": f"user{i}",                    # f-string 으로 번호를 끼워 넣는다
            "email": f"user{i}@example.com",
            "age": random.randint(18, 60)              # 18~60 사이 정수 (양끝 포함)
        }
        users.append(user)         # 리스트 끝에 추가
    return users

# 서버 시작 시 mock 데이터 생성.
# 모듈이 import 될 때 딱 한 번 실행되며, 이후 요청들은 이 리스트를 공유한다.
# DB 가 아니라 메모리라서 서버를 끄면 추가한 사용자도 함께 사라진다.
db_users = generate_mock_users()

# --------------------------------------------------
# API 엔드포인트
# --------------------------------------------------

@app.get(
    "/health",
    tags=["기본"],
    summary="서버 상태 확인",
    response_model=HealthResponse,   # 응답을 이 모델로 검증·직렬화한다
    responses={
        # 상태 코드별 문서 보강. 실제 동작에는 영향이 없고 /docs 표시만 바뀐다.
        200: {
            "description": "서버 정상 동작",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "message": "FastAPI 백엔드 서버가 작동 중입니다.",
                    }
                }
            },
        }
    },
)
def read_root():
    """서버가 정상 동작 중인지 확인한다.

    별도의 파라미터 없이 호출하며, 헬스 체크 용도로 사용한다.
    """
    # 딕셔너리를 반환하면 FastAPI 가 자동으로 JSON 으로 바꿔 내려준다.
    return {"status": "ok", "message": "FastAPI 백엔드 서버가 작동 중입니다."}


@app.get(
    "/users",
    tags=["사용자"],
    summary="사용자 목록 조회",
    # List[UserResponse] → 배열 응답임을 문서와 검증에 함께 반영한다
    response_model=List[UserResponse],
    responses={
        200: {
            "description": "사용자 전체 목록 (기본 Mock 30명)",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "username": "user1",
                            "email": "user1@example.com",
                            "age": 23,
                        },
                        {
                            "id": 2,
                            "username": "user2",
                            "email": "user2@example.com",
                            "age": 41,
                        },
                    ]
                }
            },
        }
    },
)
def get_users():
    """저장된 사용자 **전체**를 배열로 반환한다.

    서버 시작 시 생성된 Mock 30명이 기본으로 들어있고,
    `POST /users` 로 만든 사용자가 뒤에 추가된다.
    페이지네이션·필터는 제공하지 않는다.
    """
    # 메모리 리스트를 그대로 반환 — response_model 이 각 항목을 검증한다.
    return db_users


@app.get(
    # 중괄호로 감싼 부분이 경로 파라미터. 아래 함수의 user_id 인자와 이름이 같아야 한다.
    "/users/{user_id}",
    tags=["사용자"],
    summary="사용자 단건 조회",
    response_model=UserResponse,
    responses={
        200: {"description": "조회 성공"},
        404: {
            "model": ErrorResponse,   # 에러 응답 형식도 문서에 명시한다
            "description": "해당 ID의 사용자가 없음",
            "content": {
                "application/json": {
                    "example": {"detail": "사용자를 찾을 수 없습니다."}
                }
            },
        },
        422: {"description": "`user_id` 가 정수가 아님 (FastAPI 자동 검증)"},
    },
)
def get_user(
    # 타입을 int 로 적어 두면 FastAPI 가 문자열 "3" 을 정수 3 으로 바꿔 주고,
    # "abc" 처럼 변환할 수 없으면 함수를 부르기도 전에 422 를 돌려준다.
    user_id: int = Path(..., description="조회할 사용자 ID", examples=[1]),
):
    """`user_id` 로 사용자 1명을 조회한다.

    - 존재하지 않는 ID → `404 Not Found`
    - 정수가 아닌 값 → `422 Unprocessable Entity` (경로 파라미터 타입 검증)
    """
    # 리스트를 처음부터 훑는 선형 탐색. 30건이라 문제없지만 실제 서비스라면 DB 인덱스를 쓴다.
    for user in db_users:
        if user["id"] == user_id:
            return user            # 찾는 순간 반환하고 함수 종료
    # 반복문을 다 돌았는데도 못 찾았다는 뜻 → 404 에러 응답을 예외로 던진다.
    raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")


@app.post(
    "/users",
    tags=["사용자"],
    summary="사용자 생성",
    response_model=UserResponse,
    # 생성 성공은 200 이 아니라 201 Created 를 쓰는 것이 REST 관례다.
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "생성 성공 — 생성된 사용자 1명 반환",
            "content": {
                "application/json": {
                    "example": {
                        "id": 31,
                        "username": "hong",
                        "email": "hong@example.com",
                        "age": 30,
                    }
                }
            },
        },
        422: {"description": "필수 필드 누락 또는 타입 불일치 (Pydantic 검증)"},
    },
)
def create_user(user: UserCreate):
    """새 사용자를 생성한다.

    `id` 는 서버가 `len(db_users) + 1` 로 자동 부여하므로 요청에 포함하지 않는다.

    **주의**: 이메일 중복 검사는 하지 않는다.
    """
    # 인자 타입이 BaseModel 이면 FastAPI 는 그 값을 "요청 본문(JSON)"에서 찾아 채운다.
    # 여기 도달했다는 것은 이미 검증을 통과했다는 뜻이다.

    # 개수 + 1 로 ID 를 만든다. 삭제 기능이 생기면 ID 가 겹칠 수 있는 방식이라
    # 실제 서비스에서는 DB 의 auto increment 나 UUID 를 쓴다.
    new_id = len(db_users) + 1

    # 저장소가 딕셔너리 리스트라서 모델을 딕셔너리로 풀어서 담는다.
    new_user = {
        "id": new_id,
        "username": user.username,   # 점 표기법으로 모델 필드에 접근
        "email": user.email,
        "age": user.age              # 요청에서 생략했다면 None 이 들어간다
    }

    db_users.append(new_user)        # 메모리 저장소에 추가 (서버 재시작 시 사라짐)
    return new_user                  # 생성된 1건을 그대로 응답 (201 과 함께 나간다)
