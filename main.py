from fastapi import FastAPI, HTTPException, Path, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List
from pathlib import Path as FilePath
import random

from market_router import router as market_router

# --------------------------------------------------
# API 문서(Swagger) 메타데이터
# --------------------------------------------------
# Swagger UI 좌측에 그룹(태그)으로 묶여 표시된다
TAGS_METADATA = [
    {
        "name": "기본",
        "description": "서버 상태 확인용 엔드포인트",
    },
    {
        "name": "사용자",
        "description": "사용자 조회·생성 API. 서버 시작 시 **Mock 데이터 30명**이 자동 생성된다.",
    },
    {
        "name": "차트 데이터",
        "description": (
            "화면(`/`)의 차트 4종에 데이터를 공급하는 API. "
            "생성 로직은 `market_data.py`(서비스), 응답 형식은 `market_router.py`(컨트롤러)에 있다. "
            "**시드가 고정이라 여러 번 호출해도 같은 값**이 나온다."
        ),
    },
]

# /docs 상단에 마크다운으로 렌더링되는 API 개요
API_DESCRIPTION = """
FastAPI로 만든 백엔드 API 서버입니다.

## 개요

| 항목 | 내용 |
| --- | --- |
| 데이터 저장소 | 메모리 리스트 (`db_users`) — **서버 재시작 시 초기화** |
| 초기 데이터 | Mock 사용자 30명 (`user1` ~ `user30`) |
| 응답 형식 | `application/json` |
| 인증 | 없음 (실습용) |

## 사용 순서

1. `GET /health` — 서버가 살아있는지 확인
2. `GET /users` — Mock 사용자 30명 전체 조회
3. `GET /users/{user_id}` — 단건 조회 (없는 ID면 `404`)
4. `POST /users` — 사용자 생성 후 생성된 1명 반환 (`201`)

## 차트 데이터 API

화면의 차트 4종은 아래 엔드포인트에서 데이터를 받아온다. **목업이며 실제 시세가 아니다.**

| 차트 | 엔드포인트 |
| --- | --- |
| 시세(캔들+거래량) | `GET /api/stocks`, `GET /api/stocks/{code}/ohlcv` |
| 스크리닝 깔때기 | `GET /api/screening/funnel` |
| 효율적 투자선 | `GET /api/portfolio/frontier` |
| 팩터 방사형 | `GET /api/factors/radar` |

## 테스트 화면

브라우저에서 [`/`](/) 로 접속하면 차트 4종과 사용자 API 테스트 화면이 나온다.
(기존 [`/ui`](/ui) 경로도 같은 화면을 반환한다.)

## 참고

`age` 필드는 `random.randint(18, 60)` 으로 생성되므로
**서버를 재시작하면 값이 달라진다.** (`--reload` 로 코드 저장 시에도 재생성)
"""

app = FastAPI(
    title="My FastAPI Backend",
    description=API_DESCRIPTION,
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    license_info={"name": "MIT License", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",      # Swagger UI 경로
    redoc_url="/redoc",    # ReDoc 경로
)

# --------------------------------------------------
# 정적 화면(static/index.html) 서빙
# --------------------------------------------------
# 같은 오리진에서 서빙하므로 화면(JS fetch)에서 CORS 문제가 발생하지 않는다.
STATIC_DIR = FilePath(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 실습용이라 모든 오리진을 허용한다.
# index.html 을 file:// 로 직접 열어도 API 호출이 되도록 하기 위한 설정이며,
# 실제 서비스에서는 allow_origins 에 도메인을 명시해야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 차트 데이터 API (/api/...) 등록 — 목업 생성은 market_data.py 에 있다
app.include_router(market_router)


@app.get(
    "/",
    tags=["기본"],
    summary="테스트 화면 열기",
    include_in_schema=False,
)
def read_index():
    """API 를 눌러볼 수 있는 HTML 화면을 반환한다."""
    return FileResponse(STATIC_DIR / "index.html")


# 기존 /ui 링크 호환용 — 같은 화면을 반환한다.
@app.get("/ui", include_in_schema=False)
def read_ui():
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------
# 데이터 모델 정의
# --------------------------------------------------
class UserCreate(BaseModel):
    username: str = Field(..., description="사용자 이름", examples=["hong"])
    email: str = Field(..., description="이메일 주소", examples=["hong@example.com"])
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
    id: int = Field(..., description="사용자 고유 ID (자동 부여)", examples=[1])
    username: str = Field(..., description="사용자 이름", examples=["user1"])
    email: str = Field(..., description="이메일 주소", examples=["user1@example.com"])
    age: Optional[int] = Field(default=None, description="나이", examples=[23])


class ErrorResponse(BaseModel):
    """에러 응답 공통 형식 (FastAPI 의 HTTPException 기본 형태)"""

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
    users = []
    for i in range(1, 31):
        user = {
            "id": i,
            "username": f"user{i}",
            "email": f"user{i}@example.com",
            "age": random.randint(18, 60)
        }
        users.append(user)
    return users

# 서버 시작 시 mock 데이터 생성
db_users = generate_mock_users()

# --------------------------------------------------
# API 엔드포인트
# --------------------------------------------------

@app.get(
    "/health",
    tags=["기본"],
    summary="서버 상태 확인",
    response_model=HealthResponse,
    responses={
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
    return {"status": "ok", "message": "FastAPI 백엔드 서버가 작동 중입니다."}


@app.get(
    "/users",
    tags=["사용자"],
    summary="사용자 목록 조회",
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
    return db_users


@app.get(
    "/users/{user_id}",
    tags=["사용자"],
    summary="사용자 단건 조회",
    response_model=UserResponse,
    responses={
        200: {"description": "조회 성공"},
        404: {
            "model": ErrorResponse,
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
    user_id: int = Path(..., description="조회할 사용자 ID", examples=[1]),
):
    """`user_id` 로 사용자 1명을 조회한다.

    - 존재하지 않는 ID → `404 Not Found`
    - 정수가 아닌 값 → `422 Unprocessable Entity` (경로 파라미터 타입 검증)
    """
    for user in db_users:
        if user["id"] == user_id:
            return user
    raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")


@app.post(
    "/users",
    tags=["사용자"],
    summary="사용자 생성",
    response_model=UserResponse,
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
    new_id = len(db_users) + 1

    new_user = {
        "id": new_id,
        "username": user.username,
        "email": user.email,
        "age": user.age
    }

    db_users.append(new_user)
    return new_user
