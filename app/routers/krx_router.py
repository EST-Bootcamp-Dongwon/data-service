"""KRX 일별 시세 라우터 (컨트롤러 계층)

강의 원본(`lecture/main.py`)의 `GET /api/krx/stocks` 를 **같은 경로로** 유지하되,
이 저장소의 레이어드 구조에 맞게 다시 짰다.

| | 강의 원본 | 이 저장소 |
|---|---|---|
| 데이터 출처 | 요청할 때마다 KRX 직접 호출 | `data/krx_cache.db`(SQLite) 에서 조회 |
| 응답 필드 | KRX 원본 그대로 (`TDD_CLSPRC` = `"71,200"`) | snake_case + 숫자형 (`close` = `71200`) |
| 시장 | 유가증권만 | 유가증권 + 코스닥 |
| 부가 정보 | 없음 | 집계(`summary`) · 검색 · 정렬 · 페이지 |

화면(`static/pages/krx.html`)은 이 엔드포인트들이 주는 값을 그대로 그리기만 한다.
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.clients import krx_data as api  # 외부 연동 (KRX 호출)
from app.core.trading_calendar import to_iso, trading_days  # 공통 유틸 (거래일·KST)
from app.repositories import krx_store as store  # 저장소 (SQLite 캐시)
from app.services import market_data as analysis  # 서비스 (분석 계산)

router = APIRouter(prefix="/api/krx", tags=["KRX 일별 시세"])


# ==================================================
# DTO
# ==================================================
class KrxStock(BaseModel):
    """정규화된 일별매매정보 한 줄 (KRX 원본 필드명은 주석 참고)"""

    date: str = Field(..., description="거래일 (YYYY-MM-DD) — 원본 `BAS_DD`", examples=["2026-07-30"])
    code: str = Field(..., description="종목코드 — `ISU_CD`", examples=["005930"])
    name: Optional[str] = Field(None, description="종목명 — `ISU_NM`", examples=["삼성전자"])
    market: Optional[str] = Field(None, description="시장구분 — `MKT_NM`", examples=["KOSPI"])
    sector: Optional[str] = Field(None, description="소속부 — `SECT_TP_NM`", examples=[""])
    open: Optional[int] = Field(None, description="시가 — `TDD_OPNPRC`", examples=[214000])
    high: Optional[int] = Field(None, description="고가 — `TDD_HGPRC`", examples=[226000])
    low: Optional[int] = Field(None, description="저가 — `TDD_LWPRC`", examples=[202000])
    close: Optional[int] = Field(None, description="종가 — `TDD_CLSPRC`", examples=[207000])
    change: Optional[int] = Field(None, description="전일대비 — `CMPPREVDD_PRC`", examples=[-1500])
    change_rate: Optional[float] = Field(None, description="등락률(%) — `FLUC_RT`", examples=[-0.72])
    volume: Optional[int] = Field(None, description="거래량 — `ACC_TRDVOL`", examples=[46694193])
    value: Optional[int] = Field(None, description="거래대금 — `ACC_TRDVAL`", examples=[9915591555814])
    market_cap: Optional[int] = Field(None, description="시가총액 — `MKTCAP`", examples=[1236000000000000])
    listed_shares: Optional[int] = Field(None, description="상장주식수 — `LIST_SHRS`", examples=[5969782550])


class TopValueItem(BaseModel):
    code: str = Field(..., description="종목코드", examples=["000660"])
    name: Optional[str] = Field(None, description="종목명", examples=["SK하이닉스"])
    market: Optional[str] = Field(None, description="시장 구분", examples=["KOSPI"])
    value: int = Field(..., description="거래대금 (원)", examples=[12945500000000])
    close: Optional[int] = Field(None, description="종가", examples=[1322000])
    change_rate: Optional[float] = Field(None, description="등락률 (%)", examples=[1.85])


class HistogramBin(BaseModel):
    label: str = Field(..., description="구간 이름", examples=["1~3"])
    count: int = Field(..., description="해당 구간 종목 수", examples=[195])


class MarketBreakdown(BaseModel):
    market: str = Field(..., description="시장 구분", examples=["KOSPI"])
    up: int = Field(..., description="상승 종목 수", examples=[475])
    flat: int = Field(..., description="보합 종목 수", examples=[22])
    down: int = Field(..., description="하락 종목 수", examples=[453])
    value: int = Field(..., description="시장 전체 거래대금 (원)", examples=[15400000000000])


class SnapshotSummary(BaseModel):
    """차트 3종이 쓰는 집계값. 2,700종목을 전부 내려보내지 않기 위해 서버에서 계산한다."""

    up: int = Field(..., description="상승 종목 수", examples=[1204])
    flat: int = Field(..., description="보합 종목 수", examples=[86])
    down: int = Field(..., description="하락 종목 수", examples=[1474])
    total_value: int = Field(..., description="전체 거래대금 합계 (원)", examples=[25400000000000])
    total_volume: int = Field(..., description="전체 거래량 합계 (주)", examples=[1240000000])
    top_value: List[TopValueItem] = Field(..., description="거래대금 상위 15종목")
    histogram: List[HistogramBin] = Field(..., description="등락률 분포")
    by_market: List[MarketBreakdown] = Field(..., description="시장별 등락 집계")


class SnapshotResponse(BaseModel):
    bas_dd: str = Field(..., description="기준일자 (YYYYMMDD)", examples=["20260730"])
    date: str = Field(..., description="기준일자 (YYYY-MM-DD)", examples=["2026-07-30"])
    source: str = Field(..., description=(
        "데이터 출처 `<provider>-<tier>` (ADR-DS-0009) — `krx-db`(정본 저장소) · "
        "`krx-bundle`(배포용 축약본) · `krx-live`(KRX 를 방금 호출) · "
        "`krx-live-memo`(라이브 결과를 메모리에서 재사용)"),
                        examples=["krx-db"])
    total: int = Field(..., description="해당 거래일 전체 종목 수", examples=[2764])
    matched: int = Field(..., description="검색·필터를 적용한 뒤 종목 수", examples=[46])
    count: int = Field(..., description="이번 페이지에 담긴 종목 수", examples=[50])
    page: int = Field(..., description="현재 페이지 (1부터)", examples=[1])
    size: int = Field(..., description="페이지당 종목 수", examples=[50])
    sort: str = Field(..., description="정렬 기준", examples=["value"])
    order: str = Field(..., description="정렬 방향", examples=["desc"])
    summary: SnapshotSummary = Field(..., description="전체(필터 적용 후) 집계")
    items: List[KrxStock] = Field(..., description="이번 페이지 종목 목록")


class CacheStats(BaseModel):
    rows: int = Field(..., description="저장된 총 행 수", examples=[691000])
    days: int = Field(..., description="보관 중인 거래일 수", examples=[246])
    codes: int = Field(..., description="등장한 종목 수", examples=[2851])
    first_date: Optional[str] = Field(None, description="가장 오래된 거래일", examples=["20250818"])
    last_date: Optional[str] = Field(None, description="가장 최근 거래일", examples=["20260730"])
    db_path: str = Field(..., description=(
        "DB 파일명. **`mode` 가 가리키는 층의 것**이다 — 원본이 비면 축약본 파일명이 온다"),
                         examples=["krx_cache.db"])
    db_size_mb: float = Field(..., description="DB 파일 크기 (MB). `db_path` 와 같은 층", examples=[101.4])
    # `mode` 는 여기 두지 않는다 — 통로는 `StatusResponse.mode` 하나다 (ADR-DS-0009 §4)


class MarketApi(BaseModel):
    market: str = Field(..., description="시장 구분", examples=["KOSPI"])
    api_id: str = Field(..., description="KRX API ID", examples=["stk_bydd_trd"])
    api_name: str = Field(..., description="KRX API 이름", examples=["유가증권 일별매매정보"])


class StatusResponse(BaseModel):
    key_loaded: bool = Field(..., description="인증키를 찾았는지", examples=[True])
    key_source: str = Field(..., description="인증키를 읽은 곳", examples=[".key"])
    key_length: int = Field(..., description="인증키 길이 (값은 노출하지 않는다)", examples=[40])
    markets: List[MarketApi] = Field(..., description="사용하는 KRX API 목록")
    auth_blocked: bool = Field(..., description="인증 실패로 호출을 차단 중인지", examples=[False])
    last_result: Optional[str] = Field(None, description="마지막 KRX 호출 결과", examples=["ok"])
    last_detail: Optional[str] = Field(None, description="실패했다면 그 이유")
    cache: CacheStats = Field(..., description="저장소 현황. 숫자는 **`mode` 가 가리키는 층**의 것이다")
    mode: str = Field("db", description=(
        "저장소가 서 있는 층 (ADR-DS-0009) — `db`(정본 저장소에서 읽음) · "
        "`bundle`(원본이 비어 배포용 축약본에서 읽음) · "
        "`live`(둘 다 없어 요청할 때마다 KRX 를 직접 호출). "
        "**`cache` 의 숫자는 이 층의 것이다.**"), examples=["db"])


class SyncResponse(BaseModel):
    requested: int = Field(..., description="확인한 거래일 수", examples=[3])
    already: int = Field(..., description="이미 캐시에 있던 거래일 수", examples=[2])
    fetched: int = Field(..., description="새로 받은 거래일 수", examples=[1])
    rows: int = Field(..., description="새로 저장한 행 수", examples=[2764])
    failed: List[Dict] = Field(..., description="실패한 날짜와 사유")
    cache: CacheStats = Field(..., description="수집 후 캐시 현황")


# ==================================================
# 엔드포인트
# ==================================================
@router.get("/status", response_model=StatusResponse, summary="인증키·캐시 상태")
def get_status():
    """인증키를 어디서 읽었는지, KRX 호출이 마지막에 성공했는지, 캐시가 얼마나 찼는지 알려준다.

    **인증키 값은 절대 응답에 담지 않는다.** 길이만 보여 준다.

    KRX 가 401 을 줄 때는 두 가지를 구분해서 알려준다.
    - `Unauthorized Key` — 키 자체가 무효 (오타·미발급)
    - `Unauthorized API Call` — 키는 유효하지만 **해당 API 이용신청이 승인되지 않음**
    """
    status = api.get_status()
    status["cache"] = store.stats()
    # 층 판정은 `store.stats()` 가 정본이다. 여기서 다시 계산하지 않는다 (ADR-DS-0009 §4).
    #
    # 예전에는 `days > 0` 으로 재계산했는데, 원본이 비면 `stats()` 가 **축약본의** days·
    # db_path·db_size_mb 를 채워 주므로 번들 상태가 늘 `cache` 로 접혔다. 그 결과 화면의
    # 💾 캐시 배지에 번들 숫자가 캐시 라벨을 달고 찍혔다.
    #
    # `CacheStats` 에 `mode` 선언이 없어 `cache.mode` 는 응답에서 떨어진다. 그래서
    # 여기서 위로 올려 주어야 한다 — 통로는 `StatusResponse.mode` 하나다.
    status["mode"] = status["cache"].get("mode", "live")
    return status


@router.get("/dates", response_model=List[str], summary="조회 가능한 거래일")
def get_dates(
    limit: int = Query(400, ge=1, le=1000, description="가져올 거래일 수 (최근순)"),
):
    """조회할 수 있는 거래일 목록(YYYYMMDD, 최근순).

    화면의 날짜 선택 박스는 이 목록만 고를 수 있게 해서, 휴장일을 골라 빈 화면을 보는 일을 막는다.

    캐시가 비어 있으면(배포 환경) **최근 거래일 달력**을 대신 돌려준다.
    그 날짜를 고르면 `/stocks` 가 KRX 를 그 자리에서 불러 채운다.
    (달력 기준이라 공휴일이 섞일 수 있고, 그런 날은 조회 결과가 404 다.)
    """
    dates = store.available_dates(limit=limit)
    if dates:
        return dates
    return [d.strftime("%Y%m%d") for d in reversed(trading_days(min(limit, 30)))]


@router.get(
    "/stocks",
    response_model=SnapshotResponse,
    summary="일별 매매정보 (전 종목 스냅샷)",
    responses={404: {"description": "해당 거래일 데이터가 캐시에 없음"}},
)
def get_krx_stocks(
    bas_dd: Optional[str] = Query(
        None, description="기준일자 YYYYMMDD. 생략하면 캐시의 가장 최근 거래일.",
        examples=["20260730"],
    ),
    market: Optional[str] = Query(None, description="시장 구분 — `KOSPI` · `KOSDAQ`"),
    q: str = Query("", description="종목명·종목코드 부분 검색"),
    sort: str = Query("value", description=f"정렬 기준 — {' · '.join(api.SORTABLE)}"),
    order: str = Query("desc", description="정렬 방향 — `desc` · `asc`"),
    page: int = Query(1, ge=1, description="페이지 번호 (1부터)"),
    size: int = Query(50, ge=1, le=500, description="페이지당 종목 수"),
):
    """해당 거래일의 전 종목 매매정보를 반환한다. (강의 원본과 **같은 경로**)

    강의 원본은 요청할 때마다 KRX 를 직접 불렀지만, 여기서는 `krx_cache.db` 에서 읽는다.
    덕분에 응답이 2~3초에서 수십 밀리초로 줄고, 정렬·검색·페이지를 서버에서 처리할 수 있다.

    캐시를 채우려면 터미널에서 `python3 scripts/fetch_krx.py` 를 실행하거나
    `POST /api/krx/sync` 를 호출한다.
    """
    if sort not in api.SORTABLE:
        raise HTTPException(
            status_code=422,
            detail=f"정렬할 수 없는 필드입니다: {sort} (가능: {', '.join(api.SORTABLE)})",
        )

    if bas_dd and not api.DATE_PATTERN.fullmatch(bas_dd):
        raise HTTPException(status_code=422, detail="bas_dd 는 YYYYMMDD 형식이어야 합니다.")

    # 1순위는 저장소다. 있으면 수십 밀리초로 끝난다.
    # ⚠️ `snapshot()` 은 그 날짜가 원본에 없으면 **말없이 축약본으로 내려간다.** 그래서
    # 층을 함께 돌려주는 `snapshot_tiered()` 를 쓴다 — `tier()` 를 따로 부르면 저장소
    # 전체 상태라, 원본이 차 있는데 그 날짜만 없는 경우를 `db` 라고 잘못 말한다.
    cached_date = bas_dd or store.latest_date()
    items: List[Dict] = []
    tier = store.tier()
    if cached_date:
        items, tier = store.snapshot_tiered(cached_date, market)

    # 저장소(원본·축약본) 어디에도 없으면 KRX 를 그 자리에서 부른다.
    # 배포 환경(서버리스)에는 123MB 원본을 올릴 수 없어 축약본이 대신 받는데, 그 축약본마저
    # 없거나 구간 밖이면 여기로 온다. 일별매매정보는 하루치 전 종목을 한 번에 주므로
    # DB 없이도 화면을 채울 수 있다.
    if not items:
        try:
            items, cached_date, tier = store.snapshot_live(bas_dd, market)
        except api.KrxError as error:
            # 인증 문제와 그 밖의 장애를 구분해서 알려 준다
            raise HTTPException(
                status_code=503 if error.unauthorized else 502,
                detail=(
                    f"시세 캐시가 비어 있어 KRX 에 직접 조회했지만 실패했습니다 — {error} "
                    "(로컬에서는 `python3 scripts/fetch_krx.py` 로 캐시를 채울 수 있습니다.)"
                ),
            ) from error

    if not items:
        raise HTTPException(
            status_code=404,
            detail=(f"{cached_date or '최근 거래일'} 데이터를 찾지 못했습니다. "
                    "휴장일이 이어졌거나 아직 장 마감 전일 수 있습니다."),
        )

    bas_dd = cached_date

    # 검색어를 반영한 뒤 집계한다 — 화면의 차트와 표가 같은 모집단을 보게 하기 위함이다
    filtered, matched = api.paginate(items, q=q, sort=sort, order=order, page=1, size=len(items))
    rows, _ = api.paginate(filtered, sort=sort, order=order, page=page, size=size)

    return {
        "bas_dd": bas_dd,
        "date": to_iso(bas_dd),
        "source": f"{store.PROVIDER}-{tier}",
        "total": len(items),
        "matched": matched,
        "count": len(rows),
        "page": page,
        "size": size,
        "sort": sort,
        "order": order,
        "summary": api.summarize(filtered),
        "items": rows,
    }


@router.get(
    "/stocks/{code}/ohlcv",
    summary="종목 일봉 (캔들 차트용)",
    responses={404: {"description": "캐시에 없는 종목코드"}},
)
def get_krx_ohlcv(
    code: str = Path(..., description="종목코드", examples=["005930"]),
    days: int = Query(120, ge=5, le=250, description="가져올 거래일 수 (최근부터)"),
):
    """종목 하나의 일봉을 반환한다. 표에서 종목을 클릭하면 이 API 를 호출한다.

    KRX 일별매매정보는 **하루치 전 종목 스냅샷**이라 시계열이 바로 나오지 않는다.
    거래일 수만큼 받아 DB 에 쌓아 두었기 때문에, 여기서는 SQL 한 번으로 시계열이 나온다.
    """
    # 요청한 거래일 수보다 긴 이동평균은 값이 전부 null 이 되므로 미리 제외한다
    periods = tuple(p for p in (5, 20, 60) if p <= days)
    result = analysis.get_ohlcv(code, count=days, ma_periods=periods)
    if not result["count"]:
        raise HTTPException(status_code=404, detail=f"캐시에 없는 종목코드입니다: {code}")
    return result


@router.post("/sync", response_model=SyncResponse, summary="최근 거래일 수집 (KRX → 캐시)")
def sync_cache(
    days: int = Query(3, ge=1, le=30, description="확인할 최근 거래일 수 (이미 있는 날짜는 건너뛴다)"),
):
    """캐시에 없는 최근 거래일을 KRX 에서 받아 채운다.

    장 마감 후 하루치를 더할 때 쓴다. 250거래일을 처음부터 채우는 것은
    **화면이 아니라 터미널**에서 `python3 scripts/fetch_krx.py --days 250` 으로 실행한다.
    (수 분이 걸려 HTTP 요청이 타임아웃될 수 있다.)
    """
    api.reset_auth_block()      # 승인 직후에도 바로 다시 시도할 수 있게 차단기를 푼다
    result = store.sync(days=days, workers=4)
    result["cache"] = store.stats()
    return result
