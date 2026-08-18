"""리서치 하네스 라우터 (명세 §6.1)

    GET  /api/research/workstreams          작업 4종 메타
    GET  /api/research/warmup               함수 깨우기 (M6 — 화면 진입 시 한 번)
    GET  /api/research/plan/{workstream_id} 12상태 · 가중치 · 질문 지점 · 예상 시간
    POST /api/research/runs                 H00 실행 — run_header + 초기 Context Pack
    POST /api/research/runs/steps/{state_id} H01~H11 단일 상태 실행 (stateless)
    POST /api/research/export/md            Context Pack → GIC 양식 마크다운

**서버는 상태를 갖지 않는다** (명세 §1.3). 브라우저가 Context Pack 을 들고 다니고
서버는 받은 것을 고쳐 돌려준다. 그래서 어느 단계든 아무 때나 다시 부를 수 있다.

오류 규약 — 리서치 API 는 **오류로 중단하지 않는다** (명세 §6.4)
--------------------------------------------------------
자료가 없으면 200 으로 답하되 `stage_result.status = "partial-continue"` 와 Gap Log 를 싣는다.
4xx 를 내는 경우는 **요청 자체가 말이 안 될 때**뿐이다 (모르는 워크스트림·모르는 상태).

    "자료가 모자라 못 했다" 와 "요청이 틀렸다" 는 다른 말이다.

응답을 `response_model` 로 조이지 않는 이유는 `ts_router` 와 같다 (변경 노트 N30).
stage_result 는 GIC 공통계약 §7 의 봉투를 **필드명 그대로** 쓰는데, DTO 로 고정하면
계약이 바뀔 때 조용히 잘려 나간다.
"""
from typing import Dict

from fastapi import APIRouter, Body, HTTPException, Path

from app.repositories import industry_store, snapshot_store
from app.services.research import contracts, export_html, export_md, ledger, plan, stages
from app.services.research.knowledge import glossary

router = APIRouter(prefix="/api/research", tags=["리서치 하네스"])


@router.get("/workstreams", summary="작업 4종 메타")
def list_workstreams() -> Dict:
    """어떤 리서치를 돌릴 수 있는지 — ID · 이름 · 필요한 입력 · 데이터 충분도."""
    return {
        "schema_version": contracts.SCHEMA_VERSION,
        "harness_version": contracts.HARNESS_VERSION,
        "workstreams": list(contracts.WORKSTREAMS.values()),
        "implemented": ["CORP-R", "CORP-TP", "IND-R", "IND-TP"],
        "note": ("네 작업 모두 12상태(H00~H11)를 돈다. 갈라지는 자리는 "
                 "CORP-R 은 H03, 나머지 셋은 H04 다 (명세 §5.4)"),
    }


@router.get("/industries", summary="산업 목록 (IND-R · IND-TP 대상 선택)")
def list_industries(digits: int = 3, min_members: int = 3, limit: int = 300) -> Dict:
    """업종코드로 묶은 산업 목록.

    `digits` 는 몇 자리를 한 산업으로 볼지다 (2=중분류 · 3=소분류 · 4~5=세분류).
    구성 종목이 적은 산업도 **빼지 않고** 남기고 `enough` 로 표시만 한다 —
    빼 버리면 "왜 내 업종이 목록에 없나" 를 설명할 수 없다.
    """
    if digits < 2 or digits > 5:
        raise HTTPException(status_code=400, detail="digits 는 2~5 사이여야 합니다")
    rows = industry_store.directory(min_members=min_members, digits=digits)
    return {
        "digits": digits,
        "level": {2: "중분류", 3: "소분류", 4: "세분류", 5: "세세분류"}[digits],
        "total": len(rows),
        "enough": sum(1 for r in rows if r["enough"]),
        "min_members": min_members,
        "rows": rows[:limit],
        "stats": industry_store.stats(),
        "note": ("한국표준산업분류(KSIC)의 정식 단위는 세세분류 5자리다. 다만 DART 업종코드는 "
                 "자릿수가 섞여 있어(2~5자리) 5자리로 자르면 상장사가 5곳을 넘는 산업이 16% 뿐이다. "
                 "기본값을 3자리로 두고, 후보가 모자라면 실행 중에 넓힌다"),
    }


@router.get("/industries/resolve", summary="산업명·업종코드·종목명 → 업종 확정")
def resolve_industry(q: str, digits: int = 3) -> Dict:
    """`261` · `반도체` · `삼성전자` 중 무엇을 넣어도 업종을 찾아 준다."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="검색어(q)가 필요합니다")
    return industry_store.resolve(q, digits=digits)


@router.get("/glossary", summary="08강 용어 사전 (툴팁·검색)")
def lookup_glossary(term: str = "", q: str = "", terms: bool = False,
                    limit: int = 20) -> Dict:
    """`term` 이면 정확히 하나, `q` 면 자유 검색. 둘 다 없으면 절 목록만 준다.

    `terms=true` 는 **표기만** 모아 준다 (뜻·예문 없이 427줄 ≈ 8KB).
    M6 리포트가 화면에서 용어를 찾아 밑줄 치는 데 쓴다 — 사전 전체(180KB)를
    내려받지 않고도 어느 낱말이 사전에 있는지 알 수 있어야 하기 때문이다.
    뜻은 마우스를 올린 그 낱말만 `?term=` 으로 따로 받는다.
    """
    if terms:
        rows = sorted((row.get("term", "") for row in glossary.load().get("terms", [])),
                      key=len, reverse=True)          # 긴 낱말부터 — 겹칠 때 긴 쪽이 이긴다
        return {"mode": "terms", "count": len(rows), "terms": [t for t in rows if t],
                "min_length": glossary.MIN_TERM_LENGTH,
                "note": "표기만이다. 뜻은 `?term=` 으로 하나씩 받는다"}
    if term:
        return {"mode": "lookup", **glossary.lookup(term)}
    if q:
        rows = glossary.search(q, limit=limit)
        return {"mode": "search", "query": q, "count": len(rows), "rows": rows}
    return {"mode": "index", "sections": glossary.sections(), "stats": glossary.stats()}


@router.get("/warmup", summary="함수 깨우기 — 화면 진입 시 한 번 (M6)")
def warmup() -> Dict:
    """무거운 조회 없이 **함수와 지연 캐시만** 깨운다.

    왜 필요한가 — 실측이 말해 준다 (변경 노트 N59 · U-신규 결정)

        H00 콜드 5,777ms  →  웜 206ms

    5.8초는 리서치가 느린 것이 아니라 **함수가 자고 있던 것**이다. 사용자가 대상을
    고르는 동안(보통 몇 초) 이 요청 하나를 먼저 보내 두면 실행 버튼을 눌렀을 때는
    이미 깨어 있다.

    여기서 하는 일은 저장소의 **지연 로딩을 미리 끝내는 것**뿐이다. DART·KOSIS 같은
    외부 API 는 부르지 않는다 — 깨우자고 남의 서버에 요청을 보낼 이유가 없고,
    그쪽 응답을 기다리면 워밍업 자체가 느려진다.
    """
    import time

    steps = []

    def step(name: str, already: bool, fn) -> None:
        """`already` 는 **부르기 전에** 캐시가 차 있었는지다.

        걸린 시간으로 콜드 여부를 짐작하지 않는다 — 로컬은 30ms, 배포본은 그보다
        느려서 어느 문턱을 잡아도 한쪽이 틀린다. 캐시 상태를 직접 보는 쪽이 정확하다.
        """
        begin = time.monotonic()
        try:
            detail = fn()
            ok, error = True, ""
        except Exception as failure:                  # 하나가 죽어도 나머지는 깨운다
            detail, ok, error = None, False, f"{type(failure).__name__}: {failure}"
        steps.append({"name": name, "ok": ok, "loaded_now": not already,
                      "detail": detail, "error": error,
                      "ms": round((time.monotonic() - begin) * 1000, 1)})

    # H00~H01 이 실제로 읽는 것들이다. 순서는 무거운 것부터 — 하나가 느려도 나머지가 이어진다.
    step("industry_map", industry_store._cache is not None,
         lambda: industry_store.stats().get("total"))
    step("market_snapshot", snapshot_store._snapshot is not None,
         lambda: snapshot_store.as_of())
    step("glossary", glossary._cache is not None,
         lambda: glossary.stats().get("total"))

    total = round(sum(s["ms"] for s in steps), 1)
    loaded = [s["name"] for s in steps if s["loaded_now"]]
    return {
        "warm": True,
        "was_cold": bool(loaded),
        "loaded_now": loaded,
        "total_ms": total,
        "steps": steps,
        "note": (f"이 인스턴스에서 처음 읽은 것 {len(loaded)}건 — 방금 깨웠다" if loaded
                 else "이미 깨어 있었다"),
        "why": ("H00 콜드 5,777ms → 웜 206ms (배포본 실측 2026-08-02). "
                "화면 진입 시 이 요청 하나로 그 차이를 없앤다"),
    }


@router.get("/plan/{workstream_id}", summary="12상태 계획과 진행률 가중치")
def get_plan(workstream_id: str = Path(..., description="CORP-R · CORP-TP · IND-R · IND-TP")) -> Dict:
    key = workstream_id.upper()
    if key not in contracts.WORKSTREAMS:
        raise HTTPException(status_code=404,
                            detail=f"모르는 워크스트림입니다: {workstream_id}")
    return plan.plan_for(key)


@router.post("/runs", summary="H00 실행 — 실행 헤더와 초기 Context Pack")
def create_run(payload: Dict = Body(..., examples=[{
    "workstream_id": "CORP-R", "code": "005930", "as_of": "2026-08-01",
    "audience": "GIC 학회", "questions": ["지금 사도 되는가?"],
}])) -> Dict:
    workstream = str(payload.get("workstream_id") or "CORP-R").upper()
    if workstream not in contracts.WORKSTREAMS:
        raise HTTPException(status_code=404, detail=f"모르는 워크스트림입니다: {workstream}")

    code = str(payload.get("code") or payload.get("industry") or "").strip()
    if not code:
        kind = contracts.WORKSTREAMS[workstream]["target_kind"]
        raise HTTPException(
            status_code=400,
            detail=("업종코드나 산업명(code)이 필요합니다 — 예: 261 · 반도체 · 삼성전자"
                    if kind == "industry" else "종목코드(code)가 필요합니다"))

    as_of = str(payload.get("as_of") or contracts.today_kst())
    header = contracts.new_run_header(workstream, as_of,
                                      sequence=int(payload.get("sequence") or 1),
                                      user_objective=str(payload.get("objective") or ""))
    pack = contracts.new_context_pack(
        workstream,
        {"kind": contracts.WORKSTREAMS[workstream]["target_kind"], "code": code,
         "name": str(payload.get("name") or "")},
        as_of, str(payload.get("audience") or ""), payload.get("questions") or [])

    request = {"run_id": header["run_id"], "workstream_id": workstream}
    result = stages.run_stage("H00", pack, request)
    return {
        "run_header": header,
        "context_pack": pack,
        "stage_result": result,
        "progress": _progress("H00"),
        "human_questions": result.get("human_questions", []),
    }


@router.post("/runs/steps/{state_id}", summary="H01~H11 단일 상태 실행 (stateless)")
def run_step(state_id: str = Path(..., description="H01 … H11"),
             payload: Dict = Body(..., examples=[{
                 "run_header": {"run_id": "RUN-CORP-R-20260801-001"},
                 "context_pack": {"C0_charter": {"workstream_id": "CORP-R",
                                                 "target": {"code": "005930"}}},
                 "feedback": [{"question_id": "Q-H01-1", "answer": "업종 기준"}],
             }])) -> Dict:
    key = state_id.upper()
    if key not in plan.BY_ID:
        raise HTTPException(status_code=404, detail=f"모르는 상태입니다: {state_id} (H00~H11)")

    header = payload.get("run_header") or {}
    workstream = str((payload.get("context_pack") or {}).get("C0_charter", {})
                     .get("workstream_id") or header.get("workstream_id") or "CORP-R").upper()
    pack = contracts.ensure_pack(payload.get("context_pack"), workstream)

    # 이전 단계 답변을 먼저 반영한다 (불변원칙 §2-6 "다음 단계는 Feedback Log 를 입력으로 읽는다")
    applied = contracts.apply_feedback(pack, payload.get("feedback"))

    request = {"run_id": header.get("run_id", ""), "workstream_id": workstream,
               "series": payload.get("series"), "options": payload.get("options") or {}}

    try:
        result = stages.run_stage(key, pack, request)
    except Exception as error:                      # 예상 못 한 예외도 200 으로 넘긴다
        result = contracts.new_stage_result(request["run_id"], workstream, key, "ORCH")
        contracts.downgrade(result, f"{key} 실행 중 예외가 났다 — {type(error).__name__}: {error}")
        gap_id = contracts.add_gap(pack, "G-DATA", f"{key} 실행",
                                   f"{type(error).__name__}: {str(error)[:120]}",
                                   "이 단계 결과 없이 다음으로 넘어간다",
                                   "입력을 확인하고 다시 실행한다", severity="high")
        result["gap_ids"].append(gap_id)

    result["input_versions"]["prior_feedback_ids"] = applied
    return {
        "context_pack": pack,
        "stage_result": result,
        "progress": _progress(key),
        "human_questions": result.get("human_questions", []),
        "ledger": ledger.coverage(pack),
    }


@router.post("/export/md", summary="Context Pack → GIC 양식 마크다운")
def export_markdown(payload: Dict = Body(...)) -> Dict:
    pack = contracts.ensure_pack(payload.get("context_pack"))
    extension = pack.get("CX_workstream") or {}
    # H09 가 이미 조립해 팩에 실어 둔 것이 있으면 그것을 쓴다.
    # 여기서 다시 조립하면 H09 가 본 것과 다른 리포트가 나올 수 있다 (회귀검사 §13 "동기화").
    report = payload.get("report") or extension.get("report")
    if not report:
        analysis = payload.get("analysis") or extension.get("analysis") or {}
        analysis = {**analysis, "red_team": extension.get("red_team") or {}}
        report = export_md.assemble(pack, analysis)
    markdown = export_md.to_markdown(pack, report)
    return {
        "markdown": markdown,
        "page_count": report.get("page_count", 0),
        "max_pages": 15,
        "merged": report.get("merged", []),
        "bytes": len(markdown.encode("utf-8")),
    }


@router.post("/export/html", summary="Context Pack → 인쇄용 HTML (자체완결)")
def export_html_report(payload: Dict = Body(...)) -> Dict:
    """CDN·외부 파일을 **부르지 않는** HTML 한 덩어리를 돌려준다.

    차트는 인라인 SVG 로 그린다 (ApexCharts 를 쓸 수 없으므로). 계열은
    `charts.py` 것을, 표지는 `headline.py` 것을, 표는 `tables.py` 것을 **그대로** 쓴다 —
    그리는 방법만 다르고 값은 만들지 않는다. 그래서 화면·마크다운·인쇄본이 같은 수를 낸다.

    브라우저에서 열어 인쇄하면 PDF 가 된다.
    """
    pack = contracts.ensure_pack(payload.get("context_pack"))
    extension = pack.get("CX_workstream") or {}
    # MD 와 **같은 규칙**으로 리포트를 고른다 — H09 가 본 것과 다른 리포트가 나오면 안 된다
    report = payload.get("report") or extension.get("report")
    if not report:
        analysis = payload.get("analysis") or extension.get("analysis") or {}
        analysis = {**analysis, "red_team": extension.get("red_team") or {}}
        report = export_md.assemble(pack, analysis)
    document = export_html.to_html(pack, report)
    return {
        "html": document,
        "page_count": report.get("page_count", 0),
        "bytes": len(document.encode("utf-8")),
        "self_contained": True,
        "note": "외부 요청이 없다 — 파일 하나로 열린다. 인쇄하면 PDF 가 된다.",
    }


def _progress(state_id: str) -> Dict:
    """진행률 — §5.1 가중치 누적값 (12등분이 아니다)."""
    return {
        "state_id": state_id,
        "weight_done": plan.weight_done(state_id),
        "next_state": plan.next_state(state_id),
        "total_weight": 100,
    }
