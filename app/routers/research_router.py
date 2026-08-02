"""리서치 하네스 라우터 (명세 §6.1)

    GET  /api/research/workstreams          작업 4종 메타
    GET  /api/research/plan/{workstream_id} 12상태 · 가중치 · 질문 지점
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
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Path

from app.repositories import industry_store
from app.services.research import contracts, export_md, ledger, plan, stages
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
def lookup_glossary(term: str = "", q: str = "", limit: int = 20) -> Dict:
    """`term` 이면 정확히 하나, `q` 면 자유 검색. 둘 다 없으면 절 목록만 준다."""
    if term:
        return {"mode": "lookup", **glossary.lookup(term)}
    if q:
        rows = glossary.search(q, limit=limit)
        return {"mode": "search", "query": q, "count": len(rows), "rows": rows}
    return {"mode": "index", "sections": glossary.sections(), "stats": glossary.stats()}


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


def _progress(state_id: str) -> Dict:
    """진행률 — §5.1 가중치 누적값 (12등분이 아니다)."""
    return {
        "state_id": state_id,
        "weight_done": plan.weight_done(state_id),
        "next_state": plan.next_state(state_id),
        "total_weight": 100,
    }
