"""자료 보관함 라우터 (컨트롤러 계층) — ADR-DS-0008 · ADR-DS-0019

    GET    /api/clips/status        쓸 수 있나 · 못 쓰면 왜인가
    GET    /api/clips/facets        필터 UI 가 쓸 값 — 종류별 개수 · 연월 · 태그
    GET    /api/clips               담은 것 목록 (화면·종류·종목·산업·연·월·태그·검색어)
    POST   /api/clips               한 건 담는다
    GET    /api/clips/{clip_id}     한 건
    PATCH  /api/clips/{clip_id}     메모·태그·산업을 고친다
    DELETE /api/clips/{clip_id}     한 건 지운다

표는 `sql/init/03-clip.sql`, 저장소는 `app/repositories/clip_store.py` 다.
여기서는 응답 형식(DTO)과 **거절의 뜻**, 그리고 `kind` 별 검증만 담당한다.

## `kind` 별 검증이 왜 여기 있나

`payload` 가 `jsonb` 라 **DB 차원의 타입 검증이 없다.** 공시를 담으면서 `rcept_no` 를
빠뜨려도 표는 받아 준다. 그러면 나중에 그 행으로 DART 원문을 되찾을 수 없는데,
빠졌다는 사실은 되찾으려는 순간에야 드러난다. 그래서 **넣는 자리에서** 막는다.

⚠️ **DDL 이 이미 거는 것은 다시 걸지 않는다** — `clip_kind_ck`(일곱 값)와
`clip_link_needs_url_ck`(링크형은 URL 필수)는 표가 지킨다. 여기서는 표가 볼 수 없는 것,
즉 `payload` 안쪽과 "메모인데 내용이 없다" 같은 것만 본다. 두 벌로 걸면 한쪽만
고쳐진 채로 오래 간다.

## 왜 두 종류로 거절하나

| 코드 | 뜻 | 화면이 해야 할 일 |
|---|---|---|
| `503` | 보관함을 **못 쓴다** (Postgres 에 못 붙는다 · 표가 없다). 배포본의 기본 상태다 | 담기 버튼을 잠그고 이유를 보여 준다 |
| `422` | 인자가 어휘 밖이거나 `kind` 가 요구하는 것이 빠졌다 | 입력을 고치게 한다 |

## 등록은 조건부가 아니다

배포본에서도 등록한다. 실행만 `503` 으로 거절하고 **언제 되는지**(S6)를 함께 말한다 —
ADR-DS-0017 이 갱신 API 에서 세운 원칙과 같다.
"""

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from app.repositories import clip_store as store

router = APIRouter(prefix="/api/clips", tags=["자료 보관"])

# `kind` 별로 `payload` 에 반드시 있어야 하는 열쇠. 표가 볼 수 없는 것만 적는다.
# ⚠️ **`news` 는 비어 있다.** 뉴스는 링크·제목·출처·발행일이면 충분하고 그 넷은
#    payload 가 아니라 컬럼이다. 본문은 애초에 담지 않는다 (ADR-DS-0008).
REQUIRED_PAYLOAD: Dict[str, tuple] = {
    "filing": ("rcept_no",),                      # 없으면 DART 원문을 되찾을 수 없다
    "dataset": ("source", "params"),              # 어떤 조건으로 뽑은 스냅샷인지
    "report": ("run_id",),                        # 어느 실행의 리포트인지
}


# ==================================================
# 1. DTO
# ==================================================
class ClipCreate(BaseModel):
    """담을 것 하나."""

    kind: str = Field(..., description="news · filing · dataset · report · memo · post · video")
    screen: str = Field(..., description="담은 화면 — stock · krx · market …")
    title: str = Field(..., min_length=1, max_length=500)
    url: Optional[str] = Field(None, max_length=2000)
    source: Optional[str] = Field(None, max_length=100, description="연합뉴스 · DART · KRX …")
    occurred_at: Optional[date] = Field(None, description="자료 자체의 날짜 (담은 시각이 아니다)")
    code: Optional[str] = Field(None, description="종목코드 6자리. 주면 산업을 자동 유도한다")
    industry_code: Optional[str] = Field(
        None, description="직접 주면 industry_source='manual' 이 되어 재유도가 덮지 않는다")
    note: Optional[str] = None
    tags: List[str] = Field(default_factory=list, max_length=20)
    payload: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_kind(self) -> "ClipCreate":
        """`kind` 가 요구하는 것이 갖춰졌는가. **DDL 이 보는 것은 여기서 안 본다.**"""
        if self.kind not in store.KINDS:
            raise ValueError(f"kind 는 {' · '.join(store.KINDS)} 중 하나다. 받은 값: {self.kind!r}")
        if self.screen not in store.SCREENS:
            raise ValueError(
                f"screen 은 {' · '.join(store.SCREENS)} 중 하나다. 받은 값: {self.screen!r}")
        if self.kind in store.LINK_KINDS and not (self.url or "").strip():
            # 표도 이것을 막지만(clip_link_needs_url_ck), 여기서 먼저 막으면 사람이
            # 읽을 수 있는 422 가 되고 DB 까지 갔다 오지 않는다.
            raise ValueError(f"{self.kind} 는 링크형이라 url 이 있어야 한다")
        if self.kind == "memo" and not (self.note or "").strip():
            # 메모인데 내용이 없으면 담을 것이 없다. 제목만 남은 빈 행이 쌓인다.
            raise ValueError("memo 는 note 가 있어야 한다")
        missing = [k for k in REQUIRED_PAYLOAD.get(self.kind, ()) if k not in self.payload]
        if missing:
            raise ValueError(
                f"{self.kind} 의 payload 에 {' · '.join(missing)} 이(가) 없다. "
                f"필요한 열쇠: {' · '.join(REQUIRED_PAYLOAD[self.kind])}")
        return self


class ClipPatch(BaseModel):
    """사람이 붙이는 것만 고친다 — 메모·태그·산업."""

    note: Optional[str] = None
    tags: Optional[List[str]] = Field(None, max_length=20)
    industry_code: Optional[str] = Field(
        None, description="고치면 industry_source='manual' 이 된다")


class Clip(BaseModel):
    """담긴 것 하나. `code`·`name` 은 `securities` 에서 이어 붙인 값이다."""

    clip_id: int
    kind: str
    screen: str
    title: str
    url: Optional[str] = None
    source: Optional[str] = None
    occurred_at: Optional[str] = None
    saved_at: Optional[str] = None
    security_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    industry_code: Optional[str] = None
    industry_source: Optional[str] = None
    note: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    # 새로 담겼는지, 이미 있던 것인지. **중복은 오류가 아니다** — 화면이 그렇게 그린다.
    created: Optional[bool] = None


class ClipPage(BaseModel):
    items: List[Clip]
    total: int
    page: int
    size: int


class ClipStatus(BaseModel):
    available: bool
    reason: str
    hints: List[str] = Field(default_factory=list)
    kinds: List[str]
    screens: List[str]


# ==================================================
# 2. 능력 확인 — 못 쓰면 왜인지까지 말한다
# ==================================================
def _require_available() -> None:
    """못 쓰면 `503` 으로 거절한다. **왜인지와 무엇을 할지를 함께 싣는다.**"""
    state = store.availability()
    if state["available"]:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": state["reason"], "hints": state["hints"]},
    )


@router.get("/status", response_model=ClipStatus, summary="보관함을 쓸 수 있나")
def clip_status() -> ClipStatus:
    """⚠️ **이 경로만은 `503` 을 내지 않는다.** 화면이 버튼을 잠글지 정하려면
    "왜 못 쓰는지" 를 200 으로 받아 볼 수 있어야 한다.
    """
    state = store.availability()
    return ClipStatus(available=state["available"], reason=state["reason"],
                      hints=state["hints"], kinds=list(store.KINDS),
                      screens=list(store.SCREENS))


# ==================================================
# 3. 조회
# ==================================================
# ⚠️ **정적 경로를 `/{clip_id}` 보다 먼저 선언한다.** 뒤에 두면 FastAPI 가 "facets" 를
#    정수로 파싱하려다 422 를 낸다 — 화면만 봐서는 원인이 안 보이는 종류의 고장이다.
@router.get("/facets", summary="필터 UI 가 쓸 값")
def clip_facets(screen: Optional[str] = None, code: Optional[str] = None) -> Dict[str, Any]:
    _require_available()
    return store.facets(screen=screen, code=code)


@router.get("", response_model=ClipPage, summary="담은 것 목록")
def clip_list(
    screen: Optional[str] = None,
    kind: Optional[str] = None,
    code: Optional[str] = Query(None, description="종목코드 6자리"),
    industry: Optional[str] = Query(None, description="KSIC 접두어. '26' 이 '2610' 도 잡는다"),
    year: Optional[int] = Query(None, ge=1900, le=2200),
    month: Optional[int] = Query(None, ge=1, le=12),
    tag: Optional[str] = None,
    q: Optional[str] = Query(None, description="제목·메모에서 찾는다"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> ClipPage:
    """⚠️ 연·월은 **`occurred_at`(자료 날짜)** 기준이다. 담은 시각이 아니다 (ADR-DS-0008)."""
    _require_available()
    if kind and kind not in store.KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"kind 는 {' · '.join(store.KINDS)} 중 하나다")
    result = store.list_clips(screen=screen, kind=kind, code=code, industry=industry,
                              year=year, month=month, tag=tag, query=q, page=page, size=size)
    return ClipPage(**result)


@router.get("/{clip_id}", response_model=Clip, summary="한 건")
def clip_get(clip_id: int) -> Clip:
    _require_available()
    found = store.get(clip_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"clip {clip_id} 이 없다")
    return Clip(**found)


# ==================================================
# 4. 쓰기
# ==================================================
@router.post("", response_model=Clip, summary="한 건 담는다")
def clip_create(body: ClipCreate) -> Clip:
    """⚠️ **같은 링크를 다시 담아도 오류가 아니다.** 기존 것을 그대로 돌려주고
    `created=false` 로 밝힌다 — 사람이 두 번 누르는 것은 "이미 담았나" 를 확인하는 행동이다.
    """
    _require_available()
    saved = store.create(
        kind=body.kind, screen=body.screen, title=body.title, url=body.url,
        source=body.source, occurred_at=body.occurred_at, code=body.code,
        industry_code=body.industry_code, note=body.note, tags=body.tags,
        payload=body.payload,
    )
    if not saved:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "담지 못했다")
    return Clip(**saved)


@router.patch("/{clip_id}", response_model=Clip, summary="메모·태그·산업을 고친다")
def clip_patch(clip_id: int, body: ClipPatch) -> Clip:
    _require_available()
    updated = store.update(clip_id, note=body.note, tags=body.tags,
                           industry_code=body.industry_code)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"clip {clip_id} 이 없다")
    return Clip(**updated)


@router.delete("/{clip_id}", summary="한 건 지운다")
def clip_delete(clip_id: int) -> Dict[str, Any]:
    _require_available()
    if not store.delete(clip_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"clip {clip_id} 이 없다")
    return {"deleted": clip_id}
