"""화면(HTML) 라우터 (컨트롤러 계층)

`static/pages/` 의 HTML 파일을 주소에 연결하기만 하는 얇은 라우터다.
화면은 기능별로 파일을 나눠 두었고, 파일명만 봐도 무슨 화면인지 알 수 있다.

    /        → index.html   랜딩 (화면 안내 · 상태 요약)
    /users   → users.html   사용자 CRUD API 테스트
    /krx     → krx.html     KRX 일별 시세
    /kosis   → kosis.html   KOSIS 통계 실험실
    /yf      → yf.html      야후 파이낸스 시세 (scripts/yf.py 와 같은 차트)
    /stock   → stock.html   종목 통합 조회 (국내·미국 + FRED 거시지표)
    /quant   → quant.html   퀀트 분석 (스크리닝·투자선·팩터)
    /tetris  → tetris.html  Canvas 테트리스

화면 주소를 추가할 때는 아래 `PAGES` 에 한 줄만 넣으면 된다.
(화면 사이의 내비게이션 목록은 `static/assets/app.js` 의 `PAGES` 배열에 있다.)
"""

from pathlib import Path
from typing import Callable, Iterable

from fastapi import APIRouter
from fastapi.responses import FileResponse

# 이 파일은 app/routers/ 안에 있다. parents[0]=routers, [1]=app, [2]=프로젝트 루트.
# 실행 위치(cwd)와 무관하게 항상 같은 폴더를 가리키므로 상대경로보다 안전하다.
PAGES_DIR = Path(__file__).resolve().parents[2] / "static" / "pages"

PAGES = {
    "/": "index.html",
    "/users": "users.html",
    "/krx": "krx.html",
    "/kosis": "kosis.html",
    "/yf": "yf.html",
    "/stock": "stock.html",
    "/quant": "quant.html",
    "/tetris": "tetris.html",
    "/ui": "index.html",       # 기존 링크 호환용
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
    return router
