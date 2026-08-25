# FastAPI 애플리케이션 진입점 — **이것이 정본이다.**
#
#   uvicorn app.main:app --reload      정본
#   uvicorn main:app --reload          강의에서 쓰던 명령. 루트 main.py 가 이 앱을 그대로 내보낸다
#
# 두 명령은 **같은 객체**를 가리킨다. 갈라지면 로컬과 배포가 서로 다른 앱을 띄우게 되므로
# tests/test_entrypoint.py 가 동일성을 검사한다.
#
# 이 파일이 하는 일은 **앱을 조립하는 것뿐**이다.
#   - 앱 인스턴스 생성 (문서 메타는 app/core/api_docs.py)
#   - 정적 파일 서빙 · CORS 설정
#   - 라우터 등록 (API 는 app/routers/*, 화면은 app/routers/page_router.py)
#   - 헬스 체크 엔드포인트 하나
# 실제 기능은 전부 app/ 아래 계층에 있다. 새 기능을 붙일 때도 여기는 한 줄만 늘어난다.

# FastAPI: 앱 본체
# 시작·종료 시 한 번씩 실행할 작업을 정의하는 데 쓴다
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 브라우저의 교차 출처(CORS) 정책을 완화해 주는 미들웨어
from fastapi.middleware.cors import CORSMiddleware

# 디렉터리 전체를 정적 파일로 서빙하는 클래스 (/static/... 경로용)
from fastapi.staticfiles import StaticFiles

# BaseModel: 응답 데이터 모델의 부모 클래스 / Field: 필드별 설명·예시 지정
from pydantic import BaseModel, Field

# 정적 자산·실습 아카이브 폴더 위치. 기준점을 파일마다 따로 계산하지 않는다.
from app.core import paths

# /docs 에 표시할 설명 글 (분량이 길어 별도 파일로 뺐다)
from app.core.api_docs import API_DESCRIPTION, TAGS_METADATA
from app.routers import page_router  # 화면 (HTML)
from app.routers.dashboard_router import (
    router as dashboard_router,  # 대시보드 집계 (/api/dashboard/...)
)
from app.routers.fred_router import router as fred_router  # FRED 거시지표 (/api/fred/...)
from app.routers.kosis_router import router as kosis_router  # KOSIS 통계   (/api/kosis/...)
from app.routers.krx_router import router as krx_router  # KRX 일별 시세 (/api/krx/...)
from app.routers.market_router import router as market_router  # 분석 API     (/api/...)
from app.routers.refresh_router import router as refresh_router  # 자료 갱신 (/api/refresh/...)
from app.routers.research_router import router as research_router  # GIC 하네스  (/api/research/...)
from app.routers.search_router import router as search_router  # 종목 자동완성  (/api/search)
from app.routers.ts_router import router as ts_router  # 시계열 엔진   (/api/ts/...)

# 각 기능의 라우터. router 라는 이름이 흔해서 별칭을 붙인다.
from app.routers.user_router import router as user_router  # 사용자 CRUD  (/users)

# 자동완성 색인 — 서버가 뜰 때 메모리에 올려 둔다 (아래 lifespan 참고)
from app.services import search_service

# 야후 파이낸스를 쓰는 세 기능(야후 시세·종목 통합 조회·시장 상세 차트)은
# 외부 라이브러리(yfinance)에 기댄다.
# 설치돼 있지 않아도 나머지 화면·API 는 그대로 뜨도록 import 실패를 흡수한다.
# (설치: pip install yfinance matplotlib)
try:
    from app.routers.chart_router import router as chart_router  # 시장 상세 차트   (/api/chart/...)
    from app.routers.stock_router import router as stock_router  # 종목 통합 조회   (/api/stock/...)
    from app.routers.yf_router import router as yf_router  # 야후 파이낸스 시세 (/api/yf/...)
except ModuleNotFoundError as error:
    yf_router = stock_router = chart_router = None
    print(f"[안내] 야후 파이낸스 기능을 끕니다 — {error}. 쓰려면 `pip install yfinance` 하세요.")


# --------------------------------------------------
# 시작 시 준비 작업
# --------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버가 뜰 때 한 번 실행된다.

    자동완성(`/api/search`)은 입력할 때마다 호출되므로 파일을 그때그때 읽으면 느리다.
    종목 목록(국내 2,764 + 미국 12,650)을 **여기서 메모리에 한 번만** 올려 두고,
    이후 검색은 메모리만 훑는다.

    마스터 파일이 없어도 서버는 떠야 하므로 실패는 로그만 남기고 넘어간다.
    (그 경우 첫 검색 때 다시 시도한다 — `get_index()` 가 지연 로딩도 함께 지원한다.)
    """
    try:
        count = search_service.warm_up()
        print(f"[준비] 자동완성 색인 {count:,}종목을 메모리에 올렸습니다.")
    except Exception as error:      # 파일 손상 등 — 서버 기동을 막지는 않는다
        print(f"[안내] 자동완성 색인 준비 실패 — {error}")

    yield                            # 여기서부터 요청을 받는다

    # 종료 — Postgres 로 읽었다면 커넥션을 닫고 나간다 (ADR-DS-0015).
    # `STORE_BACKEND=sqlite`(기본)면 다리가 뜬 적이 없어 아무 일도 안 일어난다.
    # ⚠️ 이 자리는 **검사에서 한 번도 실행되지 않는다** — `conftest.py` 가 `TestClient` 를
    #    `with` 없이 만들어 lifespan 을 돌리지 않기 때문이다(색인 15,414종목이 느려서).
    #    커넥션 누수는 손으로 확인한다 (ADR-DS-0011 §결과).
    from app.core import db

    db.shutdown_bridge()


# --------------------------------------------------
# 앱 생성
# --------------------------------------------------
# 여기에 넘긴 값들은 전부 /docs 문서에 반영된다.
app = FastAPI(
    lifespan=lifespan,               # 위에서 정의한 시작 준비 작업
    title="data-service",                # 문서 최상단 제목 (ADR-DS-0012 §1)
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
# 경로 기준점은 app/core/paths.py 한 곳에서 정한다.
# 예전에는 여기서 `Path(__file__).parent` 로 계산했는데, 그건 이 파일이 루트에 있을 때만
# 맞는 식이었다. app/ 아래로 옮기면서 그대로 뒀으면 `app/static` 을 찾아 화면이 통째로
# 404 가 됐을 것이다 — 서버는 정상 기동하므로 배포하고 나서야 알게 되는 종류의 사고다.
#   static/pages/   HTML 화면
#   static/assets/  공통 app.css · app.js · shell.js
STATIC_DIR = paths.STATIC_DIR
# /static/파일명 으로 요청하면 해당 폴더의 파일을 그대로 내려준다.
# name="static" 은 코드에서 url_for("static", ...) 로 URL 을 만들 때 쓰는 식별자다.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 실습 아카이브 — M1 리팩토링 이전 화면 8개를 원본 그대로 얼려 둔 수업 자료다.
# 강사님 원본(`lecture/`)은 git 서브모듈이라 내 파일을 넣으면 pull 때 충돌한다.
# 그래서 저장소 루트의 별도 폴더(`실습/`)에 두고 여기서 통째로 서빙한다.
# html=True 면 폴더 주소(`/practice/`)로 들어왔을 때 그 폴더의 index.html 을 내려준다.
PRACTICE_DIR = paths.PRACTICE_DIR
if PRACTICE_DIR.exists():
    app.mount("/practice", StaticFiles(directory=PRACTICE_DIR, html=True), name="practice")

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
app.include_router(search_router)
app.include_router(dashboard_router)
app.include_router(ts_router)
app.include_router(research_router)
app.include_router(refresh_router)
if yf_router is not None:                # yfinance 가 없으면 이 세 라우터만 빠진다
    app.include_router(yf_router)
    app.include_router(stock_router)
    app.include_router(chart_router)
app.include_router(market_router)

# 화면 라우트. API 를 못 붙인 화면은 빼고 등록한다 (열어 봐야 조회가 전부 실패한다).
app.include_router(page_router.build_router(
    exclude=() if yf_router else ("/yf", "/stock", "/market")))


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
