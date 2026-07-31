# FastAPI 애플리케이션 진입점.
# uvicorn main:app --reload 로 실행하면 이 파일의 `app` 객체를 서버가 읽어간다.
#
# 이 파일이 하는 일은 **앱을 조립하는 것뿐**이다.
#   - 앱 인스턴스 생성 (문서 메타는 app/core/api_docs.py)
#   - 정적 파일 서빙 · CORS 설정
#   - 라우터 등록 (API 는 app/routers/*, 화면은 app/routers/page_router.py)
#   - 헬스 체크 엔드포인트 하나
# 실제 기능은 전부 app/ 아래 계층에 있다. 새 기능을 붙일 때도 여기는 한 줄만 늘어난다.

# FastAPI: 앱 본체
from fastapi import FastAPI
# 브라우저의 교차 출처(CORS) 정책을 완화해 주는 미들웨어
from fastapi.middleware.cors import CORSMiddleware
# 디렉터리 전체를 정적 파일로 서빙하는 클래스 (/static/... 경로용)
from fastapi.staticfiles import StaticFiles
# BaseModel: 응답 데이터 모델의 부모 클래스 / Field: 필드별 설명·예시 지정
from pydantic import BaseModel, Field
# 파일 경로 계산용
from pathlib import Path

# /docs 에 표시할 설명 글 (분량이 길어 별도 파일로 뺐다)
from app.core.api_docs import API_DESCRIPTION, TAGS_METADATA

# 각 기능의 라우터. router 라는 이름이 흔해서 별칭을 붙인다.
from app.routers.user_router import router as user_router       # 사용자 CRUD  (/users)
from app.routers.krx_router import router as krx_router         # KRX 일별 시세 (/api/krx/...)
from app.routers.kosis_router import router as kosis_router     # KOSIS 통계   (/api/kosis/...)
from app.routers.market_router import router as market_router   # 분석 API     (/api/...)
from app.routers.fred_router import router as fred_router       # FRED 거시지표 (/api/fred/...)
from app.routers import page_router                             # 화면 (HTML)

# 야후 파이낸스를 쓰는 두 기능(야후 시세·종목 통합 조회)은 외부 라이브러리(yfinance)에 기댄다.
# 설치돼 있지 않아도 나머지 화면·API 는 그대로 뜨도록 import 실패를 흡수한다.
# (설치: pip install yfinance matplotlib)
try:
    from app.routers.yf_router import router as yf_router       # 야후 파이낸스 시세 (/api/yf/...)
    from app.routers.stock_router import router as stock_router # 종목 통합 조회   (/api/stock/...)
except ModuleNotFoundError as error:
    yf_router = stock_router = None
    print(f"[안내] 야후 파이낸스 기능을 끕니다 — {error}. 쓰려면 `pip install yfinance` 하세요.")


# --------------------------------------------------
# 앱 생성
# --------------------------------------------------
# 여기에 넘긴 값들은 전부 /docs 문서에 반영된다.
app = FastAPI(
    title="My FastAPI Backend",          # 문서 최상단 제목
    description=API_DESCRIPTION,         # 제목 아래 마크다운 설명
    version="1.0.0",                     # API 버전 (문서 표시용)
    openapi_tags=TAGS_METADATA,          # 태그(그룹) 설명
    license_info={"name": "MIT License", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",      # Swagger UI 경로
    redoc_url="/redoc",    # ReDoc 경로
)

# --------------------------------------------------
# 정적 파일(static/) 서빙 · CORS
# --------------------------------------------------
# __file__ 은 이 파일(main.py)의 경로 → .parent 는 그 폴더 → / "static" 으로 하위 폴더를 가리킨다.
# 실행 위치(cwd)와 무관하게 항상 같은 폴더를 가리키므로 상대경로보다 안전하다.
#   static/pages/   HTML 화면
#   static/assets/  공통 app.css · app.js
STATIC_DIR = Path(__file__).parent / "static"
# /static/파일명 으로 요청하면 해당 폴더의 파일을 그대로 내려준다.
# name="static" 은 코드에서 url_for("static", ...) 로 URL 을 만들 때 쓰는 식별자다.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 실습용이라 모든 오리진을 허용한다.
# 화면을 file:// 로 직접 열어도 API 호출이 되도록 하기 위한 설정이며,
# 실제 서비스에서는 allow_origins 에 도메인을 명시해야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 어떤 도메인에서 호출해도 허용
    allow_methods=["*"],   # GET, POST, PUT, DELETE ... 전부 허용
    allow_headers=["*"],   # 어떤 요청 헤더든 허용
)

# --------------------------------------------------
# 라우터 등록
# --------------------------------------------------
# include_router 를 호출하는 순간 router 에 정의된 모든 경로가 app 에 붙는다.
app.include_router(user_router)
app.include_router(krx_router)
app.include_router(kosis_router)
app.include_router(fred_router)
if yf_router is not None:                # yfinance 가 없으면 이 두 라우터만 빠진다
    app.include_router(yf_router)
    app.include_router(stock_router)
app.include_router(market_router)

# 화면 라우트. API 를 못 붙인 화면은 빼고 등록한다 (열어 봐야 조회가 전부 실패한다).
app.include_router(page_router.build_router(exclude=() if yf_router else ("/yf", "/stock")))


# --------------------------------------------------
# 헬스 체크
# --------------------------------------------------
class HealthResponse(BaseModel):
    """`GET /health` 응답 형식"""

    status: str = Field(..., description="서버 상태", examples=["ok"])
    message: str = Field(
        ..., description="상태 설명", examples=["FastAPI 백엔드 서버가 작동 중입니다."]
    )


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
def health():
    """서버가 정상 동작 중인지 확인한다.

    별도의 파라미터 없이 호출하며, 모든 화면이 상단 배지를 그릴 때 이 값을 쓴다.
    """
    # 딕셔너리를 반환하면 FastAPI 가 자동으로 JSON 으로 바꿔 내려준다.
    return {"status": "ok", "message": "FastAPI 백엔드 서버가 작동 중입니다."}
