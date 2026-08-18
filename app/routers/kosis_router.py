"""KOSIS OpenAPI 실험실 라우터 (컨트롤러 계층)

화면(`static/pages/kosis.html`)의 **3단계 실험 흐름**을 그대로 API 로 옮긴 것이다.

    1단계 찾기   GET /api/kosis/search?q=소비자물가      → 통계표 목록 (org_id·tbl_id)
    2단계 조립   GET /api/kosis/meta?org_id=&tbl_id=     → 항목(ITM)·기간(PRD) 목록
    3단계 호출   GET /api/kosis/data?...                 → 수치 + 차트용 series/categories

설계 원칙
--------
- **인증키는 서버 밖으로 나가지 않는다.** 화면에 보여주는 요청 URL 에서도 `apiKey` 를 뺀다.
- **차트 변환은 서버에서 한다.** KOSIS 응답은 (기간 × 분류 × 항목) 이 평평하게 늘어선
  형태라 그대로는 그릴 수 없다. 화면이 계산하지 않도록 여기서 뒤집어 준다.
- **자른 것은 반드시 알린다.** 시리즈·행을 잘랐으면 `meta` 에 몇 개를 잘랐는지 담는다.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.clients import kosis_data as api  # 외부 연동 (KOSIS 호출)

router = APIRouter(prefix="/api/kosis", tags=["KOSIS 통계 실험실"])


# ==================================================
# DTO
# ==================================================
class PeriodType(BaseModel):
    code: str = Field(..., description="주기 코드", examples=["M"])
    name: str = Field(..., description="주기 이름", examples=["월"])


class KosisStatus(BaseModel):
    """인증키 로딩 상태. **키 값은 포함하지 않는다.**"""

    key_loaded: bool = Field(..., description="인증키를 찾았는지", examples=[True])
    key_source: str = Field(..., description="키 출처 (.key · .env · 환경변수)", examples=[".key"])
    key_length: int = Field(..., description="키 길이 (값 확인용, 값 자체는 비공개)", examples=[44])
    key_masked: str = Field(..., description="앞 4자만 남긴 마스킹 값", examples=["MDgw****"])
    base_url: str = Field(..., description="KOSIS OpenAPI 기본 주소")
    period_types: List[PeriodType] = Field(..., description="선택 가능한 수록 주기")


class TableItem(BaseModel):
    org_id: str = Field(..., description="기관 코드 — `ORG_ID`", examples=["101"])
    org_name: str = Field(..., description="기관명", examples=["국가데이터처"])
    tbl_id: str = Field(..., description="통계표 코드 — `TBL_ID`", examples=["DT_1J22042"])
    tbl_name: str = Field(..., description="통계표명", examples=["월별 소비자물가 등락률"])
    stat_name: str = Field(..., description="통계조사명", examples=["소비자물가조사"])
    path: str = Field(..., description="주제 분류 경로", examples=["물가 > 소비자물가조사"])
    start_period: str = Field(..., description="수록 시작 시점", examples=["1965"])
    end_period: str = Field(..., description="수록 종료 시점", examples=["2026"])
    contents: str = Field(..., description="수록 내용 요약 (200자로 자름)")


class MetaItem(BaseModel):
    id: str = Field(..., description="항목 코드 — `ITM_ID`", examples=["T03"])
    name: str = Field(..., description="항목명", examples=["전년동월비(%)"])
    unit: str = Field("", description="단위", examples=["%"])


class MetaPeriod(BaseModel):
    period_type: str = Field(..., description="수록 주기", examples=["월"])
    start_period: str = Field(..., description="수록 시작", examples=["1965.02"])
    end_period: str = Field(..., description="수록 종료", examples=["2026.06"])


class MetaResponse(BaseModel):
    org_id: str = Field(..., description="기관 코드")
    tbl_id: str = Field(..., description="통계표 코드")
    items: List[MetaItem] = Field(..., description="항목(ITM) 목록 — `itmId` 후보")
    periods: List[MetaPeriod] = Field(..., description="수록 주기·기간 목록")


class Series(BaseModel):
    name: str = Field(..., description="계열 이름 (분류 · 항목)", examples=["총지수"])
    data: List[Optional[float]] = Field(..., description="기간 순서대로의 값 (결측은 null)")


class Chart(BaseModel):
    categories: List[str] = Field(..., description="가로축 라벨 (기간)", examples=[["2026-05", "2026-06"]])
    series: List[Series] = Field(..., description="계열 목록 — ApexCharts 에 그대로 넣는다")
    unit: str = Field("", description="값의 단위", examples=["%"])
    truncated: int = Field(0, description="개수 제한으로 잘라낸 계열 수", examples=[257])
    total_series: int = Field(0, description="자르기 전 전체 계열 수", examples=[269])


class DataRow(BaseModel):
    period: str = Field(..., description="기간 코드 — `PRD_DE`", examples=["202606"])
    period_label: str = Field(..., description="사람이 읽는 기간", examples=["2026-06"])
    item: str = Field("", description="항목명 — `ITM_NM`", examples=["전년동월비(%)"])
    group_label: str = Field("", description="분류명 — `C1_NM` 등을 합친 것", examples=["총지수"])
    value: Optional[float] = Field(None, description="값 — `DT` (결측은 null)", examples=[2.3])
    unit: str = Field("", description="단위 — `UNIT_NM`", examples=["%"])


class TableInfo(BaseModel):
    org_id: str
    tbl_id: str
    tbl_name: str = Field("", description="통계표명")
    period_type: str = Field("", description="주기 코드")
    period_type_name: str = Field("", description="주기 이름")
    unit: str = Field("", description="단위")
    last_updated: str = Field("", description="자료 갱신일 — `LST_CHN_DE`")


class DataMeta(BaseModel):
    elapsed_ms: int = Field(..., description="KOSIS 응답에 걸린 시간(ms)", examples=[278])
    total_rows: int = Field(..., description="KOSIS 가 준 전체 행 수", examples=[876])
    returned_rows: int = Field(..., description="응답에 담은 행 수", examples=[876])
    row_truncated: int = Field(0, description="행 제한으로 잘라낸 수")
    series_truncated: int = Field(0, description="계열 제한으로 잘라낸 수")
    total_series: int = Field(0, description="자르기 전 전체 계열 수")
    request_url: str = Field(..., description="KOSIS 로 나간 요청 URL (**apiKey 는 제외**)")


class DataResponse(BaseModel):
    table: TableInfo = Field(..., description="통계표 정보")
    rows: List[DataRow] = Field(..., description="정규화된 데이터 행")
    chart: Chart = Field(..., description="차트에 바로 넣을 수 있는 형태")
    meta: DataMeta = Field(..., description="호출 결과 요약")


# ==================================================
# 공통 — KOSIS 예외를 HTTP 응답으로 바꾼다
# ==================================================
def _guard(fn, *args, **kwargs):
    """KOSIS 호출을 감싸 `KosisError` 를 알맞은 HTTP 상태 코드로 바꾼다.

    화면은 상태 코드와 `detail` 만 보고 사용자에게 안내하면 되도록 한다.
    """
    try:
        return fn(*args, **kwargs)
    except api.KosisError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/status", response_model=KosisStatus, summary="KOSIS 인증키 상태")
def get_status():
    """KOSIS 인증키를 읽었는지 확인한다.

    실험 화면이 처음 뜰 때 호출해, 키가 없으면 발급 안내를 먼저 보여 준다.
    **키 값 자체는 응답에 담지 않는다.**
    """
    return api.get_status()


@router.get("/search", response_model=List[TableItem], summary="1단계 — 통계표 검색")
def search(
    q: str = Query(..., min_length=1, description="검색어", examples=["소비자물가"]),
    start: int = Query(1, ge=1, description="시작 위치 (1부터)"),
    size: int = Query(20, ge=1, le=100, description="가져올 개수 (최대 100)"),
):
    """통계표를 이름으로 검색한다. 결과의 `org_id`·`tbl_id` 가 다음 단계의 입력이 된다."""
    return _guard(api.search_tables, q.strip(), start, size)


@router.get("/meta", response_model=MetaResponse, summary="2단계 — 항목·기간 메타")
def meta(
    org_id: str = Query(..., description="기관 코드", examples=["101"]),
    tbl_id: str = Query(..., description="통계표 코드", examples=["DT_1J22042"]),
):
    """통계표에서 고를 수 있는 **항목(`itmId`)** 과 **수록 기간** 을 돌려준다.

    이 값이 있어야 화면에서 파라미터를 찍어서 고를 수 있다.
    통계표에 따라 메타가 없을 수 있는데(KOSIS `err=30`), 그때는 빈 목록으로 준다.
    """
    def safe(meta_type: str):
        # 메타가 없는 통계표도 있다. 그 경우 전체 요청을 실패시키지 않고 빈 목록으로 넘긴다.
        try:
            return api.fetch_meta(org_id, tbl_id, meta_type)
        except api.KosisError as error:
            if error.status == 404:
                return []
            raise

    items = _guard(safe, "ITM")
    periods = _guard(safe, "PRD")
    return {"org_id": org_id, "tbl_id": tbl_id, "items": items, "periods": periods}


@router.get("/data", response_model=DataResponse, summary="3단계 — 통계 수치 + 차트 데이터")
def data(
    org_id: str = Query(..., description="기관 코드 — `orgId`", examples=["101"]),
    tbl_id: str = Query(..., description="통계표 코드 — `tblId`", examples=["DT_1J22042"]),
    itm_id: str = Query("ALL", description="항목 코드 — `itmId` (`ALL` 이면 전체)", examples=["T03"]),
    obj_l1: str = Query("ALL", description="분류1 코드 — `objL1` (`ALL` 이면 전체)", examples=["ALL"]),
    prd_se: str = Query("Y", description="수록 주기 — `prdSe` (Y·H·Q·M·D)", examples=["M"]),
    period_count: int = Query(10, ge=1, le=100, description="최근 N개 기간 — `newEstPrdCnt`"),
    start_period: str = Query("", description="시작 시점 — `startPrdDe` (끝 시점과 함께 쓴다)"),
    end_period: str = Query("", description="종료 시점 — `endPrdDe`"),
    max_series: int = Query(12, ge=1, le=50, description="차트에 남길 최대 계열 수"),
):
    """KOSIS 통계 수치를 받아 **표와 차트에 바로 쓸 수 있는 형태**로 돌려준다.

    - 기간은 `period_count`(최근 N개) 또는 `start_period`+`end_period`(직접 지정) 중 하나로 준다.
    - 계열이 `max_series` 를 넘으면 최근 값이 큰 순으로 남기고, 자른 수를 `meta` 에 담는다.
    """
    if prd_se not in api.PERIOD_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"prd_se 는 {', '.join(api.PERIOD_TYPES)} 중 하나여야 합니다. (받은 값: {prd_se})",
        )
    # 기간을 직접 지정할 때는 시작·종료를 둘 다 줘야 한다. 하나만 주면 KOSIS 가 무시해 버린다.
    if bool(start_period) != bool(end_period):
        raise HTTPException(
            status_code=422,
            detail="start_period 와 end_period 는 함께 지정해야 합니다.",
        )

    return _guard(
        api.fetch_data,
        org_id=org_id.strip(),
        tbl_id=tbl_id.strip(),
        itm_id=itm_id.strip() or "ALL",
        obj_l1=obj_l1.strip() or "ALL",
        prd_se=prd_se,
        period_count=period_count,
        start_period=start_period.strip(),
        end_period=end_period.strip(),
        max_series=max_series,
    )
