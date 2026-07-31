"""사용자 CRUD 라우터 (컨트롤러 계층)

FastAPI 의 기본기를 익히는 실습용 API 다. 강의 원본의 `main.py` 예제를 계층에 맞춰 옮긴 것으로,
**요청 검증 · DTO · 상태 코드**만 여기서 다루고 데이터 보관은
저장소(`app/repositories/user_store.py`)에 맡긴다.

    GET  /api/users            전체 조회 (기본 Mock 30명)
    GET  /api/users/{user_id}  단건 조회 — 없으면 404, 정수가 아니면 422
    POST /api/users            생성 — 201 Created

강의 원본은 `/users` 를 썼지만 이 저장소에서는 **`/api` 를 붙인다.**
같은 주소를 화면(`/users` → `static/pages/users.html`)이 이미 쓰고 있어서,
둘 다 `GET /users` 로 두면 먼저 등록된 쪽이 이기고 나머지는 영영 호출되지 않는다.
KRX·KOSIS·야후 API 가 모두 `/api/...` 인 것과도 규칙이 맞는다.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from app.repositories import user_store as store   # 저장소 (메모리 리스트)

# prefix 를 주면 아래 경로 앞에 자동으로 붙는다 ("/users" → "/api/users")
router = APIRouter(prefix="/api", tags=["사용자"])


# ==================================================
# DTO
# ==================================================
# BaseModel 을 상속하면 (1) 요청 JSON 자동 검증 (2) 응답 자동 직렬화
# (3) Swagger 스키마 자동 생성 이 한꺼번에 된다.
class UserCreate(BaseModel):
    """POST /api/users 의 요청 본문(Request Body) 형식"""

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


# ==================================================
# 엔드포인트
# ==================================================
@router.get(
    "/users",
    summary="사용자 목록 조회",
    # List[UserResponse] → 배열 응답임을 문서와 검증에 함께 반영한다
    response_model=List[UserResponse],
    responses={
        200: {
            "description": "사용자 전체 목록 (기본 Mock 30명)",
            "content": {
                "application/json": {
                    "example": [
                        {"id": 1, "username": "user1", "email": "user1@example.com", "age": 23},
                        {"id": 2, "username": "user2", "email": "user2@example.com", "age": 41},
                    ]
                }
            },
        }
    },
)
def get_users():
    """저장된 사용자 **전체**를 배열로 반환한다.

    서버 시작 시 생성된 Mock 30명이 기본으로 들어있고,
    `POST /api/users` 로 만든 사용자가 뒤에 추가된다.
    페이지네이션·필터는 제공하지 않는다.
    """
    return store.list_users()


@router.get(
    # 중괄호로 감싼 부분이 경로 파라미터. 아래 함수의 user_id 인자와 이름이 같아야 한다.
    "/users/{user_id}",
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
    user = store.find_user(user_id)
    if user is None:
        # 못 찾았다는 뜻 → 404 에러 응답을 예외로 던진다.
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return user


@router.post(
    "/users",
    summary="사용자 생성",
    response_model=UserResponse,
    # 생성 성공은 200 이 아니라 201 Created 를 쓰는 것이 REST 관례다.
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "생성 성공 — 생성된 사용자 1명 반환",
            "content": {
                "application/json": {
                    "example": {"id": 31, "username": "hong", "email": "hong@example.com", "age": 30}
                }
            },
        },
        422: {"description": "필수 필드 누락 또는 타입 불일치 (Pydantic 검증)"},
    },
)
def create_user(user: UserCreate):
    """새 사용자를 생성한다.

    `id` 는 저장소가 자동으로 붙이므로 요청에 포함하지 않는다.

    **주의**: 이메일 중복 검사는 하지 않는다.
    """
    # 인자 타입이 BaseModel 이면 FastAPI 는 그 값을 "요청 본문(JSON)"에서 찾아 채운다.
    # 여기 도달했다는 것은 이미 검증을 통과했다는 뜻이다.
    return store.add_user(user.username, user.email, user.age)
