"""화면(HTML) 라우터 (컨트롤러 계층)

`static/pages/` 의 HTML 파일을 주소에 연결하기만 하는 얇은 라우터다.
화면은 기능별로 파일을 나눠 두었고, 파일명만 봐도 무슨 화면인지 알 수 있다.

    /          → dashboard.html  대시보드 (시장 카드 그리드 · 데이터 상태)
    /market    → market.html     시장 상세 (큰 차트 · 기간 토글 · 겹쳐보기 · 시장의 폭)
    /research  → research.html   리서치 하네스 (4작업 공용 · 진행률 모달 · 근거 드릴다운)
    /krx       → krx.html        KRX 일별 시세
    /kosis     → kosis.html      KOSIS 통계 실험실
    /yf        → yf.html         야후 파이낸스 시세 (scripts/yf.py 와 같은 차트)
    /stock     → stock.html      종목 통합 조회 (국내·미국 + FRED 거시지표)
    /quant     → quant.html      퀀트 분석 (스크리닝·투자선·팩터)
    /timeseries → timeseries.html 시계열 분석 (분해·정상성·상관도·예측 3단)
    /guide     → index.html      프로젝트 안내 (예전 랜딩 · 계층 데이터 흐름도)

M1 에서 바뀐 것
--------------
- **`/` 가 대시보드가 됐다.** 예전 랜딩(화면 안내)은 `/guide` 로 옮겼다.
  사이드바가 화면 목록을 대신하므로, 첫 화면은 안내가 아니라 시장 상황이어야 맞는다.
- **`/users` · `/tetris` 는 앱에서 내렸다** (미결정 항목 U6 결정).
  리팩토링 방향과 맞지 않아 메뉴에서 빼고, 수업 자료로 `실습/` 아카이브에 원본 그대로 남겼다.
  예전 링크가 404 가 되지 않도록 **아카이브로 이동(redirect)** 시킨다.
  사용자 API 자체(`GET /api/users` 등)는 그대로 살아 있다.

화면 주소를 추가할 때는 아래 `PAGES` 에 한 줄만 넣으면 된다.
(사이드바 메뉴 목록은 `static/assets/shell.js` 의 `NAV` 배열에 있다.)
"""

from pathlib import Path
from typing import Callable, Iterable

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

# 이 파일은 app/routers/ 안에 있다. parents[0]=routers, [1]=app, [2]=프로젝트 루트.
# 실행 위치(cwd)와 무관하게 항상 같은 폴더를 가리키므로 상대경로보다 안전하다.
PAGES_DIR = Path(__file__).resolve().parents[2] / "static" / "pages"

PAGES = {
    "/": "dashboard.html",
    "/dashboard": "dashboard.html",
    "/market": "market.html",
    # M6 — 네 작업(CORP-R · CORP-TP · IND-R · IND-TP) 공용 화면.
    # 어느 작업인지는 `?ws=` 로 넘긴다 (12상태가 넷 다 같아 화면을 나눌 이유가 없다).
    "/research": "research.html",
    "/krx": "krx.html",
    "/kosis": "kosis.html",
    "/yf": "yf.html",
    "/stock": "stock.html",
    "/quant": "quant.html",
    "/timeseries": "timeseries.html",
    "/guide": "index.html",
    "/ui": "index.html",       # 기존 링크 호환용
}

# 앱에서 내린 화면 — 실습 아카이브의 같은 화면으로 보낸다 (예전 북마크·수업 노트 보호)
ARCHIVED = {
    "/users": "/practice/pages/users.html",
    "/tetris": "/practice/pages/tetris.html",
}


def _page(filename: str) -> Callable:
    """지정한 HTML 파일을 그대로 내려주는 처리기를 만든다.

    화면마다 똑같은 함수를 여러 번 쓰지 않기 위해, 함수를 만들어 주는 함수를 쓴다.
    (`filename` 을 인자로 받아 그 값을 기억하는 새 함수를 돌려준다 — 클로저)
    """
    def handler():
        # FileResponse 는 파일을 열어 스트리밍으로 내려주고, 확장자로 Content-Type 을 자동 판단한다.
        return FileResponse(PAGES_DIR / filename)
    return handler


def _redirect(target: str) -> Callable:
    """다른 주소로 보내는 처리기를 만든다.

    308 이 아니라 **302(임시)** 를 쓴다. 브라우저가 영구 이동을 캐시해 버리면
    나중에 화면을 되살릴 때 사용자 쪽에서 예전 주소가 계속 막힌다.
    """
    def handler():
        return RedirectResponse(target, status_code=302)
    return handler


def build_router(exclude: Iterable[str] = ()) -> APIRouter:
    """화면 라우트를 등록한 라우터를 만들어 돌려준다.

    `exclude` 에 넣은 주소는 등록하지 않는다. 필요한 라이브러리가 없어 API 를 못 붙인 화면을
    빼기 위한 장치다 (열어 봐야 조회가 전부 실패하므로 아예 없는 편이 낫다).

    화면은 API 가 아니므로 `include_in_schema=False` 로 Swagger 문서에서 감춘다.
    """
    router = APIRouter()
    skip = set(exclude)
    for path, filename in PAGES.items():
        if path in skip:
            continue
        # 데코레이터(@router.get) 대신 router.get(...)(함수) 형태로 직접 호출하는 방식이다.
        router.get(path, include_in_schema=False)(_page(filename))
    for path, target in ARCHIVED.items():
        router.get(path, include_in_schema=False)(_redirect(target))
    return router
