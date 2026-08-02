"""H00~H11 상태 함수 (명세 §5.1)

각 함수는 **(Context Pack, 요청) → (갱신된 Context Pack, stage_result)** 하나를 한다.
서버는 상태를 갖지 않으므로 팩을 받아 고쳐 돌려주는 것이 전부다 (명세 §1.3).

지키는 규칙 세 가지.

1. **오류로 중단하지 않는다.** 자료가 없으면 `contracts.downgrade()` 로
   `partial-continue` 를 만들고 Gap 을 남긴 뒤 다음 상태로 넘긴다 (명세 §6.4).
2. **modelable=False 면 모델링을 건너뛴다.** M3 전처리가 그렇게 판정했으면 H04 도 따른다.
3. **근사·한계는 limitation 으로 내보낸다.** M3 의 `caveats` 를 그대로 싣는다.

명세 §5.4 대로 워크스트림마다 갈라지는 상태가 다르다.

    CORP-R    H03 → 회계기간 · 재무 · 피어 정규화 3단          (M4)
    CORP-TP   H04 → 12개월 이벤트 · Quick Score               (M5)
    IND-R     H04 → 밸류체인 · 시장·수급 · 사이클               (M5)
    IND-TP    H04 → 점수 · missing penalty · 민감도             (M5)

**12상태 목록은 넷 다 같다.** 상태를 더하거나 빼지 않고 각 상태 안에서 갈라 부른다.
다만 산업 계열(IND-*)은 대상이 종목이 아니라 산업이라 H01~H03 도 갈래가 다르다 —
`snapshot_store.get('261')` 은 종목이 아니므로 애초에 답이 없다.
그래서 `_target_kind()` 로 `corp` / `industry` 를 갈라 각 상태 안에서 분기한다.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional

from ...clients import dart_data, dart_report, hf_data
from ...core import parallel
from ...repositories import industry_store, snapshot_store
from .. import ts_service
from . import contracts, export_md, ledger, narrative, plan, redteam
from .knowledge import financials, macro, valuation
from .workstreams import corp_r, corp_tp, ind_r, ind_tp


# ─────────────────────────────────────────────────────────────
# 단계 사이에 중간 산출물 넘기기
# ─────────────────────────────────────────────────────────────
# 서버가 상태를 갖지 않으므로(§1.3) H03 이 만든 재무 시계열을 H04 가 그냥 쓸 수 없다.
# 요청 딕셔너리에 담아 두면 **그 요청 안에서만** 살아 있고 다음 호출에는 없다.
# 그래서 Context Pack 의 작업별 확장 블록(`CX_workstream`)에 실어 브라우저를 거쳐 오게 한다.
#
# 여기에 넣는 것은 **다음 상태가 실제로 읽는 것만**이다. 전부 넣으면 팩이 매 요청 오간다.
def _stash(pack: Dict, key: str, value) -> None:
    pack.setdefault("CX_workstream", {})[key] = value


def _stashed(pack: Dict, key: str):
    return (pack.get("CX_workstream") or {}).get(key)


def _workstream(pack: Dict) -> str:
    return pack.get("C0_charter", {}).get("workstream_id", "CORP-R")


def _target_kind(pack: Dict) -> str:
    """`corp`(종목) 인가 `industry`(산업) 인가 — 계약이 정한 값을 그대로 쓴다."""
    return contracts.WORKSTREAMS.get(_workstream(pack), {}).get("target_kind", "corp")


# ─────────────────────────────────────────────────────────────
# H00 Initialize
# ─────────────────────────────────────────────────────────────
def h00_initialize(pack: Dict, request: Dict) -> Dict:
    workstream = request.get("workstream_id", "CORP-R")
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H00", "ORCH")
    result["context_io"]["written_fields"] = ["C0_charter"]
    result["verified_result"] = [
        f"워크스트림 {workstream} · {contracts.WORKSTREAMS.get(workstream, {}).get('name', '')}",
        f"스키마 {contracts.SCHEMA_VERSION} · 하네스 {contracts.HARNESS_VERSION}",
        f"대상 {pack['C0_charter']['target'].get('name')} ({pack['C0_charter']['target'].get('code')})",
    ]
    meta = contracts.WORKSTREAMS.get(workstream, {})
    result["verified_result"].append(
        f"대상 종류 {meta.get('target_kind', 'corp')} · 데이터 충분도 "
        f"{meta.get('sufficiency_label', '')}")
    result["status"] = result["status"] if result["status"] == "partial-continue" else "accepted"
    result["next_state_input"] = [
        "H01 이 산업 경계와 기준일을 확정한다" if meta.get("target_kind") == "industry"
        else "H01 이 대상 식별과 기준일을 확정한다"]
    return result


# ─────────────────────────────────────────────────────────────
# H01 Scope
# ─────────────────────────────────────────────────────────────
def h01_scope(pack: Dict, request: Dict) -> Dict:
    if _target_kind(pack) == "industry":
        return _h01_industry(pack, request)
    return _h01_corp(pack, request)


def _h01_industry(pack: Dict, request: Dict) -> Dict:
    """IND-R · IND-TP 의 H01 — 대상은 종목이 아니라 **산업**이다.

    입력은 업종코드(`261`)일 수도 산업명(`반도체`)일 수도 종목명(`삼성전자`)일 수도 있다.
    셋 다 받아 `ind_r.resolve_target` 이 산업 하나로 확정하고, **몇 자리까지 넓혔는지**를
    함께 남긴다. 그 폭이 곧 이 리포트가 말하는 '산업' 의 크기다.
    """
    workstream = _workstream(pack)
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H01", "SCOPE")
    result["context_io"]["read_fields"] = ["C0_charter", "C6_decisions.feedback_log"]
    result["context_io"]["written_fields"] = ["C0_charter", "C6_decisions", "CX_workstream"]

    charter_target = pack["C0_charter"]["target"]
    query = charter_target.get("code") or charter_target.get("name") or ""
    target = ind_r.resolve_target(query)

    if not target.get("available"):
        gap = contracts.add_gap(pack, "G-SCOPE", f"대상 산업 '{query}'",
                                target.get("reason", "산업을 찾지 못했다"),
                                "산업 경계를 정하지 못해 이후 분석이 전부 조건부가 된다",
                                "업종코드(예: 261) 또는 산업명(예: 반도체)을 다시 준다",
                                severity="high", owner="SCOPE")
        result["gap_ids"].append(gap)
        contracts.downgrade(result, f"산업 '{query}' 를 확정하지 못했다")
    else:
        change = contracts.set_field(
            pack, "C0_charter", "target",
            {**charter_target, "kind": "industry", "code": target["industry_code"],
             "name": target["name"], "level": target["level"],
             "member_count": target["member_count"]},
            "업종코드와 이름을 확정했다", "SCOPE")
        if change:
            result["context_io"]["change_ids"].append(change)
        renamed = contracts.set_field(
            pack, "C0_charter", "task_name",
            f"{contracts.WORKSTREAMS[workstream]['name']} · {target['name']}",
            "산업명이 확정돼 과제명을 채웠다", "SCOPE")
        if renamed:
            result["context_io"]["change_ids"].append(renamed)

        result["verified_result"] = [
            f"{target['industry_code']} {target['name']} ({target['level']}) · "
            f"상장사 {target['member_count']}곳",
            f"대분류 {target['section'].get('letter')} {target['section'].get('name')}",
            target.get("note", ""),
        ]
        if target.get("widened"):
            gap = contracts.add_gap(
                pack, "G-SCOPE", "산업 경계",
                f"구성 종목이 모자라 업종코드를 {' → '.join(target['widened'])} 로 넓혔다",
                "산업이 넓어져 '같은 산업' 이라는 말의 뜻이 느슨해진다",
                "더 좁은 업종을 직접 지정할 수 있다", severity="medium", owner="SCOPE")
            result["gap_ids"].append(gap)
        if target.get("ambiguous"):
            gap = contracts.add_gap(
                pack, "G-SCOPE", "산업 후보",
                f"이름으로 찾은 후보가 {len(target['candidates'])}개다",
                "어느 경계를 골랐는지에 따라 결론이 달라진다",
                "후보 중 하나를 직접 고른다", severity="low", owner="SCOPE")
            result["gap_ids"].append(gap)
        _stash(pack, "industry_target", target)

    staleness = snapshot_store.staleness(pack["C0_charter"].get("as_of", ""))
    if staleness.get("stale"):
        gap = contracts.add_gap(pack, "G-DATE", "시장 스냅샷",
                                staleness.get("message", "기준일이 벌어졌다"),
                                "구성 종목의 시총·멀티플이 최신이 아니다",
                                "스냅샷을 다시 만든다", owner="SCOPE")
        result["gap_ids"].append(gap)

    candidates = target.get("candidates", [])[:4]
    result["human_questions"] = [
        contracts.question(
            "Q-H01-1", "어느 업종 경계로 볼까요?",
            [f"{c['industry_code']} {c['name']} ({c['member_count']}곳)" for c in candidates]
            or ["현재 경계 그대로"],
            0,
            (f"지금은 {target.get('industry_code')} ({target.get('level')}) 로 잡혀 있다. "
             "자릿수를 넓히면 후보가 늘지만 '같은 산업' 의 뜻이 느슨해진다"
             if target.get("available") else "산업을 확정하지 못했다")),
        contracts.question(
            "Q-H01-2",
            ("Top Pick 후보를 몇 곳까지 볼까요?" if workstream == "IND-TP"
             else "이 산업에서 가장 먼저 답을 얻어야 할 질문은 무엇입니까?"),
            (["10곳 (추천)", "5곳", "전수"] if workstream == "IND-TP"
             else ["지금 사이클 어디인가", "누가 이익을 가져가는가", "수요는 늘고 있는가"]),
            0,
            (f"업종 상장사는 {target.get('member_count', 0)}곳이다. "
             "전수는 느리고 5곳은 대표성이 낮다" if workstream == "IND-TP"
             else "이 답이 15장 중 어느 장을 앞으로 뺄지 정한다")),
    ]
    result["status"] = "review-needed" if result["status"] != "partial-continue" else result["status"]
    result["evaluation"]["verdict"] = "human-decision"
    result["next_state_input"] = ["H02 가 산업 근거(KOSIS·ECOS·스냅샷·KRX)를 모은다"]
    return result


def _h01_corp(pack: Dict, request: Dict) -> Dict:
    result = contracts.new_stage_result(request.get("run_id", ""), pack["C0_charter"]["workstream_id"],
                                        "H01", "SCOPE")
    result["context_io"]["read_fields"] = ["C0_charter", "C6_decisions.feedback_log"]
    result["context_io"]["written_fields"] = ["C0_charter", "C6_decisions"]

    code = pack["C0_charter"]["target"].get("code", "")
    row = snapshot_store.get(code)
    if not row:
        gap = contracts.add_gap(pack, "E-ENTITY", f"대상 {code}", "스냅샷에서 찾지 못했다",
                                "피어 비교와 밸류에이션을 못 낸다", "종목코드를 다시 확인한다",
                                severity="high", owner="SCOPE")
        result["gap_ids"].append(gap)
        contracts.downgrade(result, f"종목 {code} 를 시장 스냅샷에서 찾지 못했다")
    else:
        change = contracts.set_field(pack, "C0_charter", "target",
                                     {**pack["C0_charter"]["target"],
                                      "name": row.get("name"), "market": row.get("market"),
                                      "market_cap": row.get("market_cap")},
                                     "스냅샷에서 종목 정보를 확정했다", "SCOPE")
        if change:
            result["context_io"]["change_ids"].append(change)
        # 과제명은 H00 때 종목명을 몰라 비어 있다. 이름이 확정된 지금 채운다 (이력 남김).
        renamed = contracts.set_field(
            pack, "C0_charter", "task_name",
            f"{contracts.WORKSTREAMS[pack['C0_charter']['workstream_id']]['name']} · {row.get('name')}",
            "종목명이 확정돼 과제명을 채웠다", "SCOPE")
        if renamed:
            result["context_io"]["change_ids"].append(renamed)
        result["verified_result"] = [
            f"{row.get('name')} ({code}) · {row.get('market')}",
            f"시가총액 {(row.get('market_cap') or 0) / 1e12:,.1f}조",
            f"스냅샷 기준일 {snapshot_store.as_of()}",
        ]

    # 스냅샷 노후 검사 (명세 §2.3 — 5거래일 이상 벌어지면 G-DATE)
    staleness = snapshot_store.staleness(pack["C0_charter"].get("as_of", ""))
    if staleness.get("stale"):
        gap = contracts.add_gap(pack, "G-DATE", "시장 스냅샷",
                                staleness.get("message", "기준일이 벌어졌다"),
                                "멀티플이 최신 주가를 반영하지 못한다",
                                "스냅샷을 다시 만든다", owner="SCOPE")
        result["gap_ids"].append(gap)

    industry = industry_store.peers_by_industry(code)
    result["human_questions"] = [
        contracts.question(
            "Q-H01-1", "피어 그룹을 어떤 기준으로 잡을까요?",
            ["업종 기준 (추천) — 표준산업분류", "시총 기준 — 시총 0.5~2.0배 밴드", "직접 지정"],
            0,
            (f"업종({industry.get('matched_prefix', '?')} "
             f"{industry.get('industry_name', '')}) 후보 {len(industry.get('peers', []))}곳이 있다"
             if industry.get("available") else "이 종목의 업종코드를 못 찾아 시총 기준이 안전하다")),
        # CORP-TP 는 재무 기간보다 **판정 문턱**을 먼저 정해야 한다 (설계서 CTP-00 질문 ③).
        # 점수를 다 낸 뒤에 문턱을 정하면 결과를 보고 문턱을 맞추게 된다.
        contracts.question(
            "Q-H01-2", "Proceed / Watch 판정 문턱을 어떻게 둘까요?",
            [f"{corp_tp.DEFAULT_THRESHOLDS['proceed']} / {corp_tp.DEFAULT_THRESHOLDS['watch']} (기본)",
             "4.0 / 3.0 (엄격)", "3.0 / 2.0 (느슨)"], 0,
            "기회지수 5점 만점 기준이다. 점수를 보고 문턱을 정하면 결론을 맞추는 셈이 된다")
        if pack["C0_charter"]["workstream_id"] == "CORP-TP" else
        contracts.question(
            "Q-H01-2", "재무 표시 기간은 몇 년으로 할까요?",
            ["5년 (추천)", "3년"], 0,
            "DART 한 번 호출에 3개년이 온다 — 5년은 2회, 3년은 1회로 끝난다"),
    ]
    result["status"] = "review-needed" if result["status"] != "partial-continue" else result["status"]
    result["evaluation"]["verdict"] = "human-decision"
    result["next_state_input"] = ["H02 가 답변에 따라 근거를 모은다"]
    return result


# ─────────────────────────────────────────────────────────────
# H02 Evidence
# ─────────────────────────────────────────────────────────────
# 카테고리별 상한 — 공시 87건을 전부 실으면 C1 이 단계마다 오간다.
# 정책은 실측 후 확정한다 (사용자 결정 대기 중이라 기본값을 여기 하나로 모아 둔다).
EVIDENCE_PER_CATEGORY = 3

# H02 시간 예산 (초).
#
# ⚠️ 배포본에서 DART 가 로컬보다 **훨씬 느리다.** 실측 — 재무제표 한 번 호출이
#    로컬 0.3초 · Vercel 약 9초 (H03 이 18.4초 걸렸다). 공시목록·원문까지 더하면
#    서버리스 상한 60초를 넘겨 `FUNCTION_INVOCATION_TIMEOUT` 으로 통째로 죽는다.
#
# 그래서 H02 는 **필수(공시·재무) → 선택(사업보고서 원문)** 순으로 하고,
# 예산을 넘기면 선택 항목을 건너뛴 뒤 Gap 을 남긴다. 한 소스가 느리다고 리서치 전체가
# 죽으면 안 된다 (GIC 불변원칙 §2-2 — 부분 결과라도 계속 낸다).
H02_BUDGET_SECONDS = 25


def _serverless() -> bool:
    """배포본(Vercel 서버리스)인가."""
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def _report_document_allowed(request: Dict) -> tuple:
    """사업보고서 원문을 받을지 정한다 → (받을까, 사유).

    ⚠️ **배포본에서는 기본으로 끈다.** 실측한 사실만 적는다.

        로컬     원문 내려받기 0.28초 · 9MB 파싱 0.4초 → H02 전체 2.7초
        배포본   H02 가 60초를 넘겨 `FUNCTION_INVOCATION_TIMEOUT` (재시도해도 같음)
                 같은 인스턴스에서 H03(재무제표)은 콜드 15초 · 웜 0.3초로 멀쩡하다
                 → DART 조회가 아니라 **원문(0.8MB ZIP) 내려받기**가 붙들고 있다

    끄면 slot 4·5·7 이 비지만, **켜 두면 H02 가 통째로 죽어 근거까지 전부 날아간다.**
    부분 결과라도 계속 내는 쪽이 GIC 원칙에 맞다 (불변원칙 §2-2).

    필요하면 요청에서 켤 수 있다 — `options: {"include_report_document": true}`.
    """
    option = (request.get("options") or {}).get("include_report_document")
    if option is True:
        return True, "요청이 원문 파싱을 켰다"
    if option is False:
        return False, "요청이 원문 파싱을 껐다"
    if _serverless():
        return False, ("배포본에서는 기본으로 끈다 — 원문 내려받기가 서버리스 60초 제한을 넘긴다 "
                       "(로컬은 0.3초). 켜려면 options.include_report_document=true")
    return True, "로컬이라 원문을 받는다"


def h02_evidence(pack: Dict, request: Dict) -> Dict:
    if _target_kind(pack) == "industry":
        return _h02_industry(pack, request)
    return _h02_corp(pack, request)


def _h02_industry(pack: Dict, request: Dict) -> Dict:
    """IND-* 의 근거 수집 — DART 가 아니라 **KOSIS · ECOS · 스냅샷 · KRX** 다.

    산업 단위로 재무를 받으려면 구성 종목마다 DART 를 불러야 하는데,
    93곳이면 배포본에서 90초를 넘긴다. 그래서 **이미 사전계산된 것**만 쓴다.
    """
    workstream = _workstream(pack)
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H02", "EVID")
    result["context_io"]["read_fields"] = ["C0_charter", "CX_workstream"]
    result["context_io"]["written_fields"] = ["C1_evidence", "logs.gaps"]

    target = _stashed(pack, "industry_target")
    if not target or not target.get("available"):
        contracts.downgrade(result, "H01 이 산업을 확정하지 못해 근거를 모으지 못했다")
        result["next_state_input"] = ["H01 을 다시 실행해 산업을 확정한다"]
        return result

    name = target.get("name") or target.get("industry_code")

    # KOSIS 생산지수와 ECOS 경기지수를 **동시에** 부른다 (기업 쪽 H02 와 같은 이유).
    # 둘은 서로의 결과를 쓰지 않고, ECOS 는 안에서 두 계열을 또 부른다.
    section = (target.get("section") or {}).get("letter", "")
    fetched = parallel.gather({
        "생산지수": lambda: ind_r.production_index(section),
        "경기지수": macro.cycle_phase,
    })
    timing = parallel.summarize(fetched)
    _stash(pack, "h02_timing", timing)

    # 1) 산업 분류 자체가 근거다 (구성 종목의 출처)
    evidence_id = ledger.add_evidence(
        pack, claim=f"{name} 업종 구성 종목 {target['member_count']}곳",
        source="DART-기업개황", location=f"표준산업분류 {target['industry_code']}",
        directness="직접", confidence="high",
        reason="DART 기업개황의 표준산업분류로 업종을 확정했다")
    result["evidence_ids"].append(evidence_id)

    # 2) 시장 스냅샷 (시총·멀티플)
    evidence_id = ledger.add_evidence(
        pack, claim=f"{name} 구성 종목 시가총액·멀티플",
        source="KRX", location=f"시장 스냅샷 {snapshot_store.as_of()}",
        published=snapshot_store.as_of(), directness="직접", confidence="high")
    result["evidence_ids"].append(evidence_id)

    # 3) KOSIS 생산지수 — 있으면 근거, 없으면 Gap (위에서 동시에 받아 왔다)
    production = (fetched["생산지수"]["value"] if fetched["생산지수"]["ok"]
                  else {"available": False, "gap_code": "G-SOURCE",
                        "reason": f"KOSIS 조회 실패 — {fetched['생산지수']['error'][:100]}"})
    _stash(pack, "production", production)
    if production.get("available"):
        evidence_id = ledger.add_evidence(
            pack, claim=f"{production['series_name']} 생산지수 "
                        f"{production['cagr_years']}년 시계열",
            source="KOSIS", url="https://kosis.kr", location=production["source"],
            published=production.get("latest_period", ""),
            directness="직접" if not production.get("substituted") else "간접",
            confidence="high" if not production.get("substituted") else "medium",
            reason=production.get("substitute_note") or "산업 수요의 물량 지표")
        result["evidence_ids"].append(evidence_id)
        if production.get("substituted"):
            gap = contracts.add_gap(
                pack, "G-DATA", "산업 생산지수",
                production["substitute_note"],
                "이 산업만의 수요 신호가 아니라 전산업 신호를 대신 쓴다",
                "KOSIS 에서 이 업종에 맞는 통계표를 사람이 찾는다",
                severity="medium", owner="EVID")
            result["gap_ids"].append(gap)
    else:
        gap = contracts.add_gap(
            pack, production.get("gap_code", "G-SOURCE"), "산업 생산지수",
            production.get("reason", "KOSIS 조회 실패"),
            "수요 쪽 신호가 하나 빠진다 — 사이클 판정이 주가에만 기대게 된다",
            "KOSIS 인증키와 통계표를 확인한다", severity="medium", owner="EVID")
        result["gap_ids"].append(gap)
        contracts.downgrade(result, f"생산지수를 받지 못했다 — {production.get('reason', '')}")

    # 4) ECOS 경기지수 (위에서 동시에 받아 왔다)
    economy = (fetched["경기지수"]["value"] if fetched["경기지수"]["ok"]
               else {"available": False,
                     "reason": f"ECOS 조회 실패 — {fetched['경기지수']['error'][:100]}"})
    _stash(pack, "economy", economy)
    if economy.get("available"):
        evidence_id = ledger.add_evidence(
            pack, claim=f"경기 국면 {economy['phase']} (선행지수 {economy['leading']['level']})",
            source="ECOS", location="경기선행·동행지수 순환변동치",
            published=economy["leading"].get("as_of", ""),
            directness="직접", confidence=economy.get("confidence", "medium"))
        result["evidence_ids"].append(evidence_id)
    else:
        gap = contracts.add_gap(
            pack, "G-DATA", "경기 국면", economy.get("reason", "경기지수를 받지 못했다"),
            "산업 사이클 판정에서 거시 신호가 빠진다", "ECOS 조회를 다시 시도한다",
            severity="low", owner="EVID")
        result["gap_ids"].append(gap)

    result["verified_result"] = [f"근거 {len(result['evidence_ids'])}건 발급",
                                 f"산업 {target['industry_code']} {name}",
                                 timing["text"]]
    result["confidence"] = "high" if len(result["evidence_ids"]) >= 4 else "medium"
    if result["status"] == "in-progress":
        result["status"] = "accepted"
    result["next_state_input"] = ["H03 이 구성 종목과 생산지수를 D- 로 정규화한다"]
    return result


def _h02_corp(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H02", "EVID")
    result["context_io"]["read_fields"] = ["C0_charter"]
    result["context_io"]["written_fields"] = ["C1_evidence", "logs.gaps"]

    code = pack["C0_charter"]["target"].get("code", "")
    name = pack["C0_charter"]["target"].get("name", "")
    started = time.monotonic()
    rows: List[Dict] = []

    # ── 공시 목록과 재무제표를 **동시에** 부른다 ──
    #
    # 둘은 서로의 결과를 쓰지 않는다. 그런데 배포본에서 DART 는 회당 6~7초라
    # (로컬의 10~40배) 순차로 두면 그대로 더해진다. 실측 — 함수가 이미 깨어 있어도
    # H02 가 17~22초였고, 그중 대부분이 이 기다림이었다.
    #
    # **받아 오는 것만 동시에 한다.** 장부(E- 번호)에 적는 일은 아래에서 정해진
    # 순서대로 한다 — 순서가 흔들리면 같은 종목의 리포트가 실행할 때마다 달라진다.
    fetched = parallel.gather({
        "공시목록": lambda: dart_data.fetch_disclosures(code, months=12, limit=100),
        "재무제표": lambda: corp_r.load_financials(code, years=5),
    })
    timing = parallel.summarize(fetched)
    _stash(pack, "h02_timing", timing)

    # 1) 공시 목록 — 카테고리별 최신 N건만 E- 로 만든다
    try:
        if not fetched["공시목록"]["ok"]:
            raise RuntimeError(fetched["공시목록"]["error"])
        listing = fetched["공시목록"]["value"]
        rows = listing.get("rows", [])
        by_category: Dict[str, List[Dict]] = {}
        for row in rows:
            by_category.setdefault(row.get("category", "기타"), []).append(row)

        issued = 0
        for category, items in by_category.items():
            for row in items[:EVIDENCE_PER_CATEGORY]:
                evidence_id = ledger.add_evidence(
                    pack, claim=f"{name} {row.get('report_name')}",
                    source="DART-공시목록", url=row.get("url", ""),
                    published=row.get("date", ""), location=f"접수번호 {row.get('rcept_no')}",
                    direction="context", recency=row.get("date", ""),
                    reason=f"{category} 공시")
                result["evidence_ids"].append(evidence_id)
                issued += 1

        skipped = len(rows) - issued
        if skipped > 0:
            gap = contracts.add_gap(
                pack, "G-SOURCE", "공시 근거",
                f"12개월 공시 {len(rows)}건 중 카테고리별 최신 {EVIDENCE_PER_CATEGORY}건씩 "
                f"{issued}건만 E- 로 만들었다",
                f"나머지 {skipped}건은 리포트에서 참조되지 않는다",
                "특정 사건을 봐야 하면 접수번호로 개별 조회한다",
                severity="low", owner="EVID")
            result["gap_ids"].append(gap)
    except Exception as error:
        contracts.downgrade(result, f"공시 목록을 받지 못했다 — {error}")

    # 2) 재무제표 (위에서 이미 받아 왔다 — 여기서는 장부에 적기만 한다)
    try:
        if not fetched["재무제표"]["ok"]:
            raise RuntimeError(fetched["재무제표"]["error"])
        loaded = fetched["재무제표"]["value"]
        if loaded["available"]:
            first, last = loaded["rows"][0]["year"], loaded["rows"][-1]["year"]
            evidence_id = ledger.add_evidence(
                pack, claim=f"{name} {first}~{last} 연결 재무제표",
                source="DART-재무제표", location=f"{loaded.get('basis')} · 사업보고서",
                published=f"{last + 1}-03", directness="직접", confidence="high")
            result["evidence_ids"].append(evidence_id)
            _stash(pack, "financials", loaded)
        else:
            contracts.downgrade(result, "재무제표를 받지 못했다 — " + " / ".join(loaded["failures"][:2]))
    except Exception as error:
        contracts.downgrade(result, f"재무제표 조회 실패 — {error}")

    # 3) 사업보고서 원문 (slot 3·4·5·7 재료) — **선택 항목이다**
    #
    # 여기까지 오는 데 이미 예산을 다 썼으면 건너뛴다. 원문은 있으면 좋은 것이지
    # 없으면 리서치가 안 되는 것이 아니다. 반대로 이것 때문에 함수가 죽으면
    # 앞에서 모은 근거까지 전부 날아간다.
    elapsed = time.monotonic() - started
    allowed, why = _report_document_allowed(request)
    if elapsed > H02_BUDGET_SECONDS:
        allowed, why = False, (f"필수 수집에 {elapsed:.0f}초가 걸려 건너뛴다 "
                               f"(예산 {H02_BUDGET_SECONDS}초)")
    if not allowed:
        _stash(pack, "report_facts", {"available": False, "reason": why})
        gap = contracts.add_gap(
            pack, "G-DATA", "사업보고서 원문 (부문별 매출 · 점유율 · 생산능력)", why,
            "slot 4·5·7 을 자료 없이 낸다 — 피어 안 순위로 대신한다",
            "로컬에서 돌리거나 options.include_report_document=true 로 켠다",
            severity="medium", owner="EVID")
        result["gap_ids"].append(gap)
        contracts.downgrade(result, f"사업보고서 원문을 건너뛰었다 — {why}")
        result["verified_result"] = [f"근거 {len(result['evidence_ids'])}건 발급", timing["text"]]
        result["next_state_input"] = ["H03 이 이 근거들을 D- 로 정규화한다"]
        return result

    try:
        # 공시 목록을 이미 받아 뒀다 — 사업보고서를 찾겠다고 **다시 부르지 않는다**.
        # (배포본에서 DART 한 번이 9초다. 같은 목록을 두 번 받으면 그만큼 그냥 버린다)
        facts = dart_report.fetch_report_facts(code, rows=rows)
        _stash(pack, "report_facts", facts)
        if facts.get("available"):
            evidence_id = ledger.add_evidence(
                pack, claim=f"{name} {facts.get('report_name')} 본문",
                source="DART-사업보고서", url=facts.get("url", ""),
                published=facts.get("rcept_date", ""),
                location=f"접수번호 {facts.get('rcept_no')} · 본문 {facts.get('size_mb')}MB",
                confidence="high")
            result["evidence_ids"].append(evidence_id)
            for key, label in (("segments", "부문별 매출"), ("market_share", "시장점유율"),
                               ("capacity", "생산능력"), ("rnd", "연구개발")):
                block = facts.get(key, {})
                if not block.get("found"):
                    gap = contracts.add_gap(
                        pack, "G-DATA", f"{label} (사업보고서)",
                        block.get("reason", "찾지 못했다"),
                        "해당 장을 자료 없이 낸다", "사업보고서를 사람이 확인한다",
                        severity="low", owner="EVID")
                    result["gap_ids"].append(gap)
        else:
            contracts.downgrade(result, facts.get("reason", "사업보고서 원문을 받지 못했다"))
    except Exception as error:
        contracts.downgrade(result, f"사업보고서 원문 실패 — {error}")

    # 4) 시세 (KRX/야후는 ts_service 가 고른다 — 명세 N25)
    evidence_id = ledger.add_evidence(
        pack, claim=f"{name} 일봉 시계열", source="KRX" if code.isdigit() else "yfinance",
        location="일별 종가", directness="직접", confidence="high")
    result["evidence_ids"].append(evidence_id)

    result["verified_result"] = [f"근거 {len(result['evidence_ids'])}건 발급", timing["text"]]
    result["confidence"] = "high" if len(result["evidence_ids"]) >= 5 else "medium"
    if result["status"] == "in-progress":
        result["status"] = "accepted"
    result["next_state_input"] = ["H03 이 이 근거들을 D- 로 정규화한다"]
    return result


# ─────────────────────────────────────────────────────────────
# H03 Normalize — CORP-R 은 3단 (명세 §5.4)
# ─────────────────────────────────────────────────────────────
def h03_normalize(pack: Dict, request: Dict) -> Dict:
    if _target_kind(pack) == "industry":
        return _h03_industry(pack, request)
    return _h03_corp(pack, request)


def _h03_industry(pack: Dict, request: Dict) -> Dict:
    """IND-* 의 정규화 — 구성 종목의 지표를 **같은 기준일**로 나란히 놓는다.

    시장 스냅샷이 한 기준일로 만들어져 있어 날짜가 어긋날 일이 없다 (명세 §2.3).
    빠진 값은 채우지 않고 비운 채로 두고, 몇 곳이 빠졌는지 센다.
    """
    workstream = _workstream(pack)
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H03", "DATA")
    result["context_io"]["read_fields"] = ["C1_evidence", "CX_workstream"]
    result["context_io"]["written_fields"] = ["C2_data", "logs.calculations"]

    target = _stashed(pack, "industry_target")
    if not target or not target.get("available"):
        contracts.downgrade(result, "산업이 확정되지 않아 정규화할 것이 없다")
        result["next_state_input"] = ["H01 을 다시 실행한다"]
        return result

    snapshot_evidence = next((e["id"] for e in pack["C1_evidence"]
                              if e["source"].startswith("KRX")), "")
    production = _stashed(pack, "production") or {}
    steps: List[str] = []

    # 1단 — 구성 종목 지표
    matched, missing = 0, 0
    for member in target.get("members", []):
        row = snapshot_store.get(member.get("code", ""))
        if not row:
            missing += 1
            continue
        matched += 1
        for metric, unit in (("market_cap", "원"), ("per", "배"), ("pbr", "배"),
                             ("roe", "%"), ("r250", "%")):
            value = row.get(metric)
            if value is None:
                continue
            data_id = ledger.add_data(
                pack, metric=f"{row.get('code')}.{metric}", value=value, unit=unit,
                currency="KRW" if metric == "market_cap" else "",
                period=snapshot_store.as_of(), basis="시장",
                evidence_id=snapshot_evidence, rounding="스냅샷 값 그대로")
            result["data_ids"].append(data_id)
    steps.append(f"구성 종목: {matched}곳 정규화 · {missing}곳은 스냅샷에 없어 뺐다 "
                 f"· D- {len(result['data_ids'])}건")
    if missing:
        gap = contracts.add_gap(
            pack, "G-DATA", "구성 종목 지표",
            f"{missing}곳이 시장 스냅샷에 없다 (신규 상장·거래정지 등)",
            "산업 합계와 순위에서 그만큼 빠진다", "스냅샷을 다시 만든다",
            severity="low", owner="DATA")
        result["gap_ids"].append(gap)

    # 2단 — 생산지수 (CAGR 검산을 계산 원장에 남긴다)
    if production.get("available"):
        production_evidence = next((e["id"] for e in pack["C1_evidence"]
                                    if e["source"].startswith("KOSIS")), "")
        data_id = ledger.add_data(
            pack, metric=f"{production['series_name']}.생산지수", value=production["latest"],
            unit="지수", period=production.get("latest_period", ""), basis="KOSIS",
            evidence_id=production_evidence, rounding="원값 그대로")
        result["data_ids"].append(data_id)
        check = production["cagr_check"]
        calc_id = ledger.add_calculation(
            pack, check["formula"], [data_id], production.get("cagr_pct"), "%",
            assumption=f"{check['first_period']} {check['first']} → "
                       f"{check['last_period']} {check['last']} · {check['years']}년",
            intermediate={"first": check["first"], "last": check["last"],
                          "years": check["years"]})
        result["calculation_records"].append(calc_id)
        steps.append(f"생산지수: {production['series_name']} {production['latest']} "
                     f"· CAGR {production.get('cagr_pct')}% (검산 기록)")
    else:
        steps.append("생산지수: 없음 — 수요 신호가 빠진다")

    result["verified_result"] = steps
    if result["status"] == "in-progress":
        result["status"] = "accepted"
    result["visualization_and_interpretation"].update({
        "chart_or_table": "구성 종목 지표 표 · 생산지수 시계열",
        "observation": " / ".join(steps),
        "limitation": ("상장사만 본다 — 비상장사가 빠져 있어 산업 전체가 아니다. "
                       "시가총액은 매출이 아니라 기대의 크기다."),
        "next_check": "H04 가 밸류체인·시장·사이클을 판단한다",
    })
    result["next_state_input"] = ["H04 가 산업 구조와 사이클을 판단한다"]
    return result


def _h03_corp(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H03", "DATA")
    result["context_io"]["read_fields"] = ["C1_evidence"]
    result["context_io"]["written_fields"] = ["C2_data", "logs.calculations"]

    code = pack["C0_charter"]["target"].get("code", "")
    steps: List[str] = []

    # 1단 — 회계기간
    periods = corp_r.normalize_periods(code)
    steps.append(f"회계기간: 결산 {periods['fiscal_month']}월 — {periods['note']}")
    if periods["fiscal_month"] != "12":
        gap = contracts.add_gap(pack, "E-PERIOD", "회계기간",
                                f"결산월이 {periods['fiscal_month']}월이다",
                                "피어와 기간이 어긋난다", "비교 시 기간 차이를 표기한다",
                                owner="DATA")
        result["gap_ids"].append(gap)

    # 2단 — 재무
    loaded = _stashed(pack, "financials") or corp_r.load_financials(code, years=5)
    series: List[Dict] = []
    if loaded.get("available"):
        evidence_id = next((e["id"] for e in pack["C1_evidence"]
                            if e["source"].startswith("DART-재무제표")), "")
        for row in loaded["rows"]:
            ratios = corp_r.ratios_of(row["accounts"])
            inputs = ratios["inputs"]
            entry = {"year": row["year"], **inputs}
            series.append(entry)
            for metric, value in inputs.items():
                if value is None:
                    continue
                data_id = ledger.add_data(
                    pack, metric=metric, value=value, unit="원", currency="KRW",
                    period=str(row["year"]), basis=row.get("basis", ""),
                    evidence_id=evidence_id, rounding="원 단위 그대로")
                result["data_ids"].append(data_id)
        steps.append(f"재무: {loaded['years_loaded']}개년 · DART 호출 {loaded['calls']}회 "
                     f"· D- {len(result['data_ids'])}건")
    else:
        contracts.downgrade(result, "재무제표가 없어 비율을 못 낸다")

    # 3단 — 피어
    answer = contracts.find_answer(pack, "Q-H01-1") or "업종"
    manual = [c.strip() for c in answer.split(",")] if answer and "," in answer else None
    mode = "업종" if answer.startswith("업종") else ("시총" if answer.startswith("시총") else "업종")
    peers = corp_r.select_peers(code, mode=mode, manual=manual)
    if peers.get("available"):
        table = corp_r.peer_table(peers["target"], peers["peers"])
        peers["table"] = table
        steps.append(f"피어: {peers['method']} 로 {len(peers['peers'])}곳 — {peers.get('note', '')}")
        for note in peers.get("dropped", []):
            gap = contracts.add_gap(pack, "G-SCOPE", "피어 선정", note,
                                    "상대 비교의 해석 폭이 달라진다", "피어를 직접 지정할 수 있다",
                                    severity="low", owner="DATA")
            result["gap_ids"].append(gap)
    else:
        contracts.downgrade(result, peers.get("reason", "피어를 못 골랐다"))

    _stash(pack, "series", series)
    _stash(pack, "peers", peers)
    _stash(pack, "periods", periods)

    result["verified_result"] = steps
    result["calculation_records"] = [c["id"] for c in pack["logs"]["calculations"]]
    if result["status"] == "in-progress":
        result["status"] = "accepted"
    result["visualization_and_interpretation"].update({
        "chart_or_table": "재무 시계열 표 · 피어 비교 표",
        "observation": " / ".join(steps),
        "limitation": "연결 기준이며 일회성 손익은 분리하지 못했다",
        "next_check": "H04 가 비율과 멀티플을 계산한다",
    })
    result["next_state_input"] = ["H04 가 시계열·재무비율·피어 판단을 한다"]
    return result


# ─────────────────────────────────────────────────────────────
# H04 Analyze
# ─────────────────────────────────────────────────────────────
def h04_analyze(pack: Dict, request: Dict) -> Dict:
    """워크스트림이 갈라지는 자리 (명세 §5.4).

        CORP-R    재무비율 · 시계열 예측 · 밸류에이션
        CORP-TP   12개월 이벤트 · Quick Score 6차원 · P/W/D
        IND-R     밸류체인 · 시장·수급 · 사이클
        IND-TP    점수 · missing penalty · 민감도 · 순위 안정성

    CORP-TP 는 CORP-R 의 재무·피어 계산을 **그대로 재사용**한다 — 같은 회사를 두 번
    다르게 계산하면 두 리포트의 숫자가 어긋난다.
    """
    workstream = _workstream(pack)
    if workstream == "IND-R":
        return _h04_ind_r(pack, request)
    if workstream == "IND-TP":
        return _h04_ind_tp(pack, request)
    return _h04_corp(pack, request)


def _h04_ind_r(pack: Dict, request: Dict) -> Dict:
    workstream = _workstream(pack)
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H04", "ANLY")
    result["context_io"]["read_fields"] = ["C0_charter", "C1_evidence", "C2_data", "CX_workstream"]
    result["context_io"]["written_fields"] = ["C3_hypothesis", "CX_workstream", "logs.gaps"]

    target = _stashed(pack, "industry_target")
    if not target or not target.get("available"):
        contracts.downgrade(result, "산업이 확정되지 않아 분석할 것이 없다")
        result["next_state_input"] = ["H01 을 다시 실행한다"]
        return result

    options = request.get("options") or {}
    analysis = ind_r.analyze(target,
                             with_zeroshot=options.get("include_zeroshot", True))

    for gap in analysis.get("gaps", []):
        gap_id = contracts.add_gap(pack, gap["code"], gap["affected"], gap["reason"],
                                   "해당 장을 자료 없이 낸다", gap.get("next_check", ""),
                                   severity="medium", owner="ANLY")
        result["gap_ids"].append(gap_id)

    market = analysis.get("market", {})
    cycle = analysis.get("cycle", {})
    rivalry = analysis.get("competition", {})
    index_row = analysis.get("index", {})

    if rivalry.get("available"):
        calc_id = ledger.add_calculation(
            pack, "HHI = Σ(시가총액 비중%)²", [], rivalry["hhi"], "",
            assumption="**점유율이 아니라 시가총액 비중**이다 — 비상장사가 빠져 있다",
            intermediate={"cr3_pct": rivalry["cr3_pct"], "members": rivalry["member_count"]})
        result["calculation_records"].append(calc_id)

    hypotheses = []
    if cycle.get("available"):
        hypotheses.append({
            "id": f"H-{workstream}-0001",
            "statement": f"이 산업은 {cycle['phase']} 이다 ({cycle.get('vote_summary')})",
            "causal_path": ["경기", "생산", "업종 실적", "업종 주가"],
            "confirm_kpi": ["생산지수 YoY", "경기선행지수 순환변동치"],
            "falsify_condition": "생산지수가 2분기 연속 전년 대비 마이너스로 돌아서면 폐기",
            "competing": ["금리 변화가 주가만 움직였다", "특정 대형주 하나가 지수를 끌었다"],
            "confidence": cycle.get("confidence", "low"),
        })
    if rivalry.get("available"):
        hypotheses.append({
            "id": f"H-{workstream}-0002",
            "statement": f"이 산업은 {rivalry['level']} 구조다 (HHI {rivalry['hhi']})",
            "causal_path": ["집중도", "가격 결정력", "마진"],
            "confirm_kpi": ["상위 기업 영업이익률", "신규 진입 건수"],
            "falsify_condition": "매출 기준 점유율을 확인했을 때 순위가 달라지면 폐기",
            "competing": ["시총 비중과 매출 점유율이 다르다", "비상장 대형 사업자가 있다"],
            "confidence": "low",
        })
    pack["C3_hypothesis"] = hypotheses

    _stash(pack, "analysis", analysis)
    result["verified_result"] = [
        f"{target['industry_code']} {target['name']} · 상장사 {target['member_count']}곳",
        (f"상장 시가총액 합계 {(market.get('listed_market_cap') or 0) / 1e12:,.1f}조 · "
         f"{rivalry.get('level', '집중도 미상')}" if market.get("available") else "시장 규모 미상"),
        f"사이클 {cycle.get('phase', '판정 유보')} ({cycle.get('confidence', '-')})",
    ]
    result["unavailable_or_unverifiable"] = [
        f"{g['affected']} — {g['reason']}" for g in analysis.get("gaps", [])]
    result["human_questions"] = [
        contracts.question("Q-H04-1", "발표에서 강조할 구간은 어디입니까?",
                           ["사이클 국면", "경쟁 구조", "시장 규모·성장"], 0,
                           f"지금 신호는 {cycle.get('vote_summary', '없음')} 이다"),
        contracts.question("Q-H04-2", "6/12/24개월 중 핵심 투자 시계는 무엇입니까?",
                           ["12개월", "6개월", "24개월"], 0,
                           "이 답이 시나리오 장에서 어느 표를 앞으로 뺄지 정한다"),
    ]
    result["evaluation"]["verdict"] = "human-decision"
    if result["status"] == "in-progress":
        result["status"] = "review-needed"
    result["next_state_input"] = ["H05 가 산업 차트 명세를 만든다"]
    return result


def _h04_ind_tp(pack: Dict, request: Dict) -> Dict:
    workstream = _workstream(pack)
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H04", "ANLY")
    result["context_io"]["read_fields"] = ["C0_charter", "C1_evidence", "C2_data", "CX_workstream"]
    result["context_io"]["written_fields"] = ["C3_hypothesis", "CX_workstream", "logs.calculations"]

    target = _stashed(pack, "industry_target")
    if not target or not target.get("available"):
        contracts.downgrade(result, "산업이 확정되지 않아 후보를 못 고른다")
        result["next_state_input"] = ["H01 을 다시 실행한다"]
        return result

    # 사용자가 후보 수를 골랐으면 그 값을 쓴다 (Q-H01-2)
    answer = contracts.find_answer(pack, "Q-H01-2")
    want = ind_tp.MAX_CANDIDATES
    if answer.startswith("5"):
        want = 5
    elif answer.startswith("전수"):
        want = max(ind_tp.MAX_CANDIDATES, target.get("member_count", 0))

    universe = ind_tp.candidates(target, want=want)
    if not universe.get("available"):
        contracts.downgrade(result, universe.get("reason", "후보를 못 골랐다"))
        result["next_state_input"] = ["H05 로 넘어간다 (후보 없음)"]
        return result

    attacks = ind_tp.redteam_candidates(universe["rows"])
    entries = ind_tp.score_candidates(universe["rows"], redteam_map=attacks)
    ranking = ind_tp.rank(entries)
    swings = ind_tp.sensitivity(entries)
    top = next((r for r in ranking if r.get("rank") == 1), None)

    for warning in universe.get("warnings", []):
        gap_id = contracts.add_gap(pack, warning["code"], "후보군", warning["message"],
                                   warning["treatment"], warning.get("next_input", ""),
                                   severity="medium", owner="ANLY")
        result["gap_ids"].append(gap_id)

    # 계산 원장 — 설계서 §7.2 "반올림 전 값을 보존한다"
    for row in ranking[:5]:
        if not row.get("available"):
            continue
        calc_id = ledger.add_calculation(
            pack, row["formula"], [], row["adjusted_score"], "점",
            assumption=(f"{row['name']} · valid_weight {row['valid_weight']} · "
                        f"penalty 계수 {row['penalty_coefficient']}"),
            intermediate={"observed_score": row["observed_score"],
                          "coverage": row["coverage"],
                          "missing_penalty": row["missing_penalty"],
                          "missing": row["missing"]})
        result["calculation_records"].append(calc_id)

    analysis = {
        "available": True,
        "industry": {k: v for k, v in target.items() if k != "members"},
        "universe": universe,
        "entries": entries,
        "ranking": ranking,
        "sensitivity": swings,
        "red_team": {"per_candidate": attacks,
                     "questions_each": len(next(iter(attacks.values()), [])),
                     "note": "모든 후보에 같은 질문을 던졌다 (설계서 ITP-T09)",
                     "limitation": "시장 스냅샷으로 답할 수 있는 것만 물었다"},
        "weights": ind_tp.WEIGHTS,
        "proposal": {
            "top_pick": None if not top else {"code": top["code"], "name": top["name"],
                                              "adjusted": top.get("adjusted_display"),
                                              "coverage": top.get("coverage_display"),
                                              "tied": top.get("tied", False)},
            "ai_proposal": True,
            "human_decision": "사람 승인 필요 — 총점 1위를 확정하지 않는다 (설계서 ITP-T10)",
            "stability": swings.get("stability"),
            "caution": (f"순위 안정성 {swings.get('stability')} · "
                        f"{swings.get('flip_count', 0)}개 시나리오에서 1위가 바뀐다"),
        },
    }
    _stash(pack, "analysis", analysis)

    pack["C3_hypothesis"] = [{
        "id": f"H-{workstream}-0001",
        "statement": (f"후보군 안에서 {top['name']} 이 상대적으로 앞선다 "
                      f"(adjusted {top.get('adjusted_display')})" if top else "1위를 정하지 못했다"),
        "causal_path": ["성장·수익·밸류·모멘텀", "가중 점수", "상대 순위"],
        "confirm_kpi": ["다음 분기 실적", "coverage 개선"],
        "falsify_condition": "가중치를 ±5%p 흔들었을 때 1위가 바뀌면 폐기",
        "competing": ["규모 편향으로 뽑혔다", "결측이 많은 후보가 penalty 로 불리해졌다"],
        "confidence": "medium" if swings.get("stability") == "높음" else "low",
    }]

    result["verified_result"] = [
        f"후보 {universe['count']}곳 / 업종 상장사 {universe['universe_total']}곳",
        (f"1위 {top['name']} adjusted {top.get('adjusted_display')} "
         f"(coverage {top.get('coverage_display')}%)" if top else "1위 없음"),
        f"민감도 {swings.get('scenario_count', 0)}시나리오 · 안정성 {swings.get('stability')}",
    ]
    result["provisional_interpretation"] = [
        f"{r['rank']}위 {r['name']} {r.get('adjusted_display')}"
        + ("(공동)" if r.get("tied") else "")
        for r in sorted((r for r in ranking if r.get("rank")), key=lambda r: r["rank"])[:6]]
    result["unavailable_or_unverifiable"] = [
        f"{r['name']} — 결측 {', '.join(r.get('missing', []))}"
        for r in ranking if r.get("missing")]
    result["human_questions"] = [
        contracts.question("Q-H04-1", "가중치를 바꿔 볼까요?",
                           ["기본 유지 (성장25·수익20·밸류20·모멘텀15·안정10·RedTeam10)",
                            "성장성 강조", "밸류에이션 강조"], 0,
                           "기본값 결과는 보존하고 별도 시나리오로 계산한다 (설계서 ITP-T08)"),
        contracts.question("Q-H04-2", "점수보다 우선할 정성 요소가 있습니까?",
                           ["없음 — 점수대로 본다", "규모 편향 보정", "결측이 적은 후보 우선"], 0,
                           universe.get("bias", "")),
    ]
    result["evaluation"]["verdict"] = "human-decision"
    if result["status"] == "in-progress":
        result["status"] = "review-needed"
    result["next_state_input"] = ["H05 가 후보 비교 차트 명세를 만든다"]
    return result


def _h04_corp(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H04", "ANLY")
    result["context_io"]["read_fields"] = ["C0_charter", "C1_evidence", "C2_data"]
    result["context_io"]["written_fields"] = ["C3_hypothesis", "logs.calculations"]

    code = pack["C0_charter"]["target"].get("code", "")
    series = _stashed(pack, "series") or []
    peers = _stashed(pack, "peers") or {}
    facts = _stashed(pack, "report_facts") or {}

    # 1) 재무 비율 · 성장률
    growth = {"available": False}
    ratios: Dict = {}
    if series:
        revenues = [row.get("revenue") for row in series]
        years = [str(row["year"]) for row in series]
        growth = financials.growth_series(revenues, years)
        if growth.get("available") and growth.get("cagr") is not None:
            calc_id = ledger.add_calculation(
                pack, "CAGR = (마지막/처음)^(1/연수) - 1",
                [d for d in result["data_ids"] if d][:2], growth["cagr"], "%",
                assumption=f"{years[0]}~{years[-1]} 매출액 기준")
            result["calculation_records"].append(calc_id)
        latest = series[-1]
        # 업종을 함께 넘겨 **금융업이면 안정성 판정을 보류하게** 한다 (U8 결정 ②).
        # 은행은 예금이 부채라 부채비율 1,000% 가 정상이다 (08강 06.md 421행).
        ratios = corp_r.ratios_of({k: v for k, v in latest.items() if k != "year"},
                                  industry_store.industry_of(code),
                                  pack["C0_charter"]["target"].get("name", ""))
    else:
        contracts.downgrade(result, "재무 시계열이 없어 비율을 못 낸다")

    # 2) 시계열 예측 — modelable=False 면 건너뛴다 (M3 와 같은 분기)
    forecast: Dict = {}
    try:
        forecast = ts_service.forecast(code, years=2, horizon=20)
        if forecast.get("skipped"):
            contracts.downgrade(result, f"시계열 모델링을 건너뛰었다 — {forecast.get('reason', '')}")
    except Exception as error:
        forecast = {"skipped": True, "reason": str(error)[:120]}
        contracts.downgrade(result, f"시계열 예측 실패 — {error}")

    # 3) 밸류에이션
    target_row = peers.get("target") or snapshot_store.get(code) or {}
    industry_code = industry_store.industry_of(code)
    debt = (ratios.get("debt_ratio") or {}).get("value")
    view: Dict = {}
    if peers.get("available"):
        view = corp_r.valuation_view(target_row, peers.get("table", {}),
                                     growth.get("cagr"), industry_code, debt)
        eps = None
        net = series[-1].get("net_income") if series else None
        shares = target_row.get("shares")
        if net and shares:
            eps = net / shares
        view["band"] = valuation.band_target(
            target_row.get("close"), eps,
            (view.get("per_position") or {}).get("peer_median"))
        if view["band"].get("available"):
            calc_id = ledger.add_calculation(
                pack, "주당가치 = 피어 중앙값 PER × EPS", [], view["band"]["base"], "원",
                assumption="밴드는 ±20% · 밸류에이션 연습")
            result["calculation_records"].append(calc_id)
    else:
        contracts.downgrade(result, "피어가 없어 상대가치 판단을 못 한다")

    # 4) 가설(C3) — 확인 KPI 와 반증 조건을 반드시 붙인다 (공통계약 §6 C3)
    hypotheses = []
    if growth.get("available"):
        hypotheses.append({
            "id": f"H-{workstream}-0001",
            "statement": f"매출이 {growth.get('trend')} 국면이다 (CAGR {growth.get('cagr')}%)",
            "causal_path": ["수요", "매출", "영업이익"],
            "confirm_kpi": ["다음 분기 매출액", "영업이익률"],
            "falsify_condition": "다음 2개 분기 연속 매출 역성장",
            "competing": ["환율 효과", "일회성 대형 수주"],
            "confidence": "medium",
        })
    if view.get("screen"):
        hypotheses.append({
            "id": f"H-{workstream}-0002",
            "statement": f"피어 대비 {view['screen'].get('verdict')} 다",
            "causal_path": ["피어 멀티플", "상대 위치"],
            "confirm_kpi": ["피어 중앙값 PER", "ROE"],
            "falsify_condition": "피어 재선정 시 위치가 뒤집히면 폐기",
            "competing": view["screen"].get("blockers", []),
            "confidence": "low" if view.get("blocked") else "medium",
        })
    pack["C3_hypothesis"] = hypotheses

    analysis = {
        "financial": {"series": series, "growth": growth, "latest_ratios": ratios},
        "peers": peers,
        "valuation": view,
        "forecast": forecast,
        "report_facts": facts,
    }

    # ── CORP-TP 만 여기서 갈라진다 (명세 §5.4) ──
    # 재무·피어 계산은 위에서 이미 끝났다. **다시 계산하지 않고 그대로 받아** 이벤트 창과
    # Quick Score 를 얹는다. 같은 회사를 두 번 다르게 계산하면 두 리포트의 숫자가 어긋난다.
    if workstream == "CORP-TP":
        disclosures: List[Dict] = []
        try:
            disclosures = dart_data.fetch_disclosures(code, months=12, limit=100).get("rows", [])
        except Exception as error:
            contracts.downgrade(result, f"공시 목록을 받지 못해 이벤트 창을 못 만든다 — {error}")

        options = request.get("options") or {}
        thresholds = corp_tp.parse_thresholds(contracts.find_answer(pack, "Q-H01-2"))
        quick = corp_tp.analyze(
            pack_target={**(peers.get("target") or snapshot_store.get(code) or {}),
                         "code": code, "name": pack["C0_charter"]["target"].get("name", "")},
            series=series, ratios=ratios, growth=growth, peers=peers,
            position=(view.get("per_position") or {}),
            quadrant=(view.get("quadrant") or {}),
            report_facts=facts, disclosures=disclosures,
            coverage=ledger.coverage(pack),
            red_checks=(_stashed(pack, "red_team") or {}).get("checks", []),
            evidence_ids=[e["id"] for e in pack.get("C1_evidence", [])],
            as_of=pack["C0_charter"].get("as_of", ""),
            thresholds=thresholds,
            with_sentiment=options.get("include_sentiment", True))
        analysis.update(quick)

        card = quick["scorecard"]
        if card.get("index") is not None:
            calc_id = ledger.add_calculation(
                pack, card["index_formula"], [], card["index"], "점",
                assumption=card["index_note"],
                intermediate={row["key"]: row["score"] for row in card["rows"]})
            result["calculation_records"].append(calc_id)
        if card.get("unscored"):
            gap = contracts.add_gap(
                pack, "G-DATA", "Quick Score 미채점 차원",
                f"{', '.join(card['unscored'])} 를 매기지 못했다",
                "**Unscored 를 0점으로 세지 않는다** — 기회지수 분모에서 뺐다",
                "해당 차원의 자료를 더 모은다", severity="medium", owner="ANLY")
            result["gap_ids"].append(gap)

        hypotheses.append({
            "id": f"H-{workstream}-0003",
            "statement": (f"{quick['verdict']['verdict']} — 기회지수 "
                          f"{card.get('index')}/5"),
            "causal_path": ["12개월 사건", "재무·밸류·촉매·위험", "판정"],
            "confirm_kpi": ["다음 분기 실적", "예정 사건의 실현 여부"],
            "falsify_condition": "미채점 차원이 채워졌을 때 판정이 뒤집히면 폐기",
            "competing": sum((r.get("counter_evidence", []) for r in card.get("rows", [])), [])[:3],
            "confidence": "medium" if card.get("coverage", 0) >= 0.6 else "low",
        })
        pack["C3_hypothesis"] = hypotheses

    _stash(pack, "analysis", analysis)

    if workstream == "CORP-TP":
        events = analysis.get("events", {})
        card = analysis.get("scorecard", {})
        decision = analysis.get("verdict", {})
        result["verified_result"] = [
            f"12개월 사건 {events.get('total', 0)}건 "
            f"(구조 {events.get('counts', {}).get('구조', 0)})",
            f"기회지수 {card.get('index')}/5 · 미채점 {len(card.get('unscored', []))}",
            f"AI 제안 {decision.get('verdict')} — {decision.get('human_decision', '')}",
        ]
        result["human_questions"] = [
            contracts.question("Q-H04-1", "판정에서 가장 중시할 차원은 무엇입니까?",
                               [r["name"] for r in card.get("rows", [])][:4] or ["없음"], 0,
                               card.get("separate_note", "")),
            contracts.question("Q-H04-2", "이 제안을 승인하시겠습니까?",
                               [f"{decision.get('verdict')} 승인", "다른 판정으로 수정", "보류"], 0,
                               " / ".join(decision.get("reasons", [])[:2])),
        ]
    else:
        result["verified_result"] = [
            f"재무 {len(series)}개년 · 성장 추세 {growth.get('trend', '판정불가')}",
            f"피어 {len(peers.get('peers', []))}곳 · "
            f"{(view.get('per_position') or {}).get('stance', '비교 없음')}",
            f"예측 {forecast.get('statistical', {}).get('model', '없음')}",
        ]
        result["human_questions"] = [
            contracts.question("Q-H04-1", "밸류에이션에서 어떤 멀티플을 본문에 둘까요?",
                               [f"{(view.get('multiple_choice') or {}).get('multiple', 'PER')} (규칙 추천)",
                                "PER", "PBR", "EV/EBITDA"], 0,
                               (view.get("multiple_choice") or {}).get("why", "")),
            contracts.question("Q-H04-2", "성장성과 현금전환 중 어느 쪽을 더 강조할까요?",
                               ["현금전환 (보수적)", "성장성"], 0,
                               "이익의 질 판정: "
                               + (ratios.get("earnings_quality", {}).get("why", "판정 없음"))),
        ]
    result["evaluation"]["verdict"] = "human-decision"
    if result["status"] == "in-progress":
        result["status"] = "review-needed"
    result["next_state_input"] = ["H05 가 차트 명세를 만든다"]
    return result


# ─────────────────────────────────────────────────────────────
# H05 Visualize
# ─────────────────────────────────────────────────────────────
def h05_visualize(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H05", "VIZ")
    result["context_io"]["written_fields"] = ["C4_visual"]

    analysis = _stashed(pack, "analysis") or {}
    specs = []

    def spec(index: int, question: str, chart_type: str, data_ids: List[str], axis: Dict,
             unit: str, forbidden: str, sources: List[str]) -> Dict:
        return {"id": f"V-{workstream}-{index:04d}", "research_question": question,
                "chart_type": chart_type, "data_ids": data_ids, "axis": axis, "unit": unit,
                "forbidden_misread": forbidden, "sources": sources}

    # ── 산업 계열은 그릴 것이 통째로 다르다 ──
    if workstream in ("IND-R", "IND-TP"):
        if analysis.get("market", {}).get("available"):
            specs.append(spec(1, "산업 안에서 시가총액이 어떻게 나뉘나?", "가로 막대", [],
                              {"x": "시가총액", "y": "종목"}, "조원",
                              "시가총액 비중은 **시장점유율이 아니다** — 비상장사가 빠져 있다",
                              [f"시장 스냅샷 {snapshot_store.as_of()}"]))
        production = (analysis.get("market") or {}).get("production", {})
        if production.get("available"):
            specs.append(spec(2, "이 산업의 수요는 늘고 있나?", "선그래프", [],
                              {"x": "월", "y": "생산지수"}, "지수",
                              "생산지수는 물량 기준이라 가격 변화가 빠져 있다",
                              [production.get("source", "KOSIS")]))
        if analysis.get("index", {}).get("available"):
            specs.append(spec(3, "업종 주가는 어떻게 움직였나?", "선그래프 (100 기준 지수화)", [],
                              {"x": "거래일", "y": "지수"}, "100 기준",
                              "시총 상위만 넣어 만든 지수다 — 생존 편향이 있다",
                              ["KRX 일별 시세"]))
        if analysis.get("cycle", {}).get("available"):
            specs.append(spec(4, "지금 어느 국면인가?", "신호 3단 패널", [],
                              {"x": "월", "y": "지수·모멘텀"}, "지수 · %",
                              "단위가 다른 값을 한 축에 겹치지 않는다 — 100 기준으로 지수화한다",
                              ["ECOS", "KOSIS", "KRX"]))
        if analysis.get("ranking"):
            specs.append(spec(5, "후보 중 누가 앞서나?", "가로 막대 (coverage 표기)", [],
                              {"x": "adjusted score", "y": "후보"}, "점",
                              "결측이 많은 후보는 penalty 로 낮게 나온다 — 나쁜 것과는 다르다",
                              [f"시장 스냅샷 {snapshot_store.as_of()}"]))
        if (analysis.get("sensitivity") or {}).get("available"):
            specs.append(spec(6, "순위가 얼마나 단단한가?", "시나리오별 1위 빈도 막대", [],
                              {"x": "시나리오", "y": "1위 빈도"}, "회",
                              "동점은 공동 1위로 세었다 — 숨은 타이브레이커를 쓰지 않는다",
                              ["민감도 계산 원장"]))
        pack["C4_visual"] = specs
        result["verified_result"] = [f"차트 명세 {len(specs)}건"]
        if not specs:
            contracts.downgrade(result, "그릴 자료가 없어 차트 명세를 못 만들었다")
        else:
            result["status"] = "accepted"
        result["next_state_input"] = ["H06 이 각 차트에 해석카드를 붙인다"]
        return result

    # ── CORP-TP 는 이벤트·점수 차트를 앞에 둔다 ──
    if workstream == "CORP-TP":
        if analysis.get("events", {}).get("available"):
            specs.append(spec(5, "최근 12개월에 무슨 일이 있었나?", "타임라인 (구조/일회성/예정)", [],
                              {"x": "날짜", "y": "사건 성격"}, "건",
                              "공시는 회사가 낸 사실이다 — 주가 반응과 인과를 섞지 않는다",
                              ["DART 공시목록"]))
        if analysis.get("scorecard", {}).get("rows"):
            specs.append(spec(6, "여섯 차원이 어떻게 갈리나?", "레이더 (기회/부담 분리)", [],
                              {"x": "차원", "y": "0~5"}, "점",
                              "부담·위험은 높을수록 불리하다 — 같은 방향으로 읽으면 결론이 뒤집힌다",
                              ["Quick Score 계산 원장"]))

    if (analysis.get("report_facts") or {}).get("segments", {}).get("found"):
        specs.append(spec(1, "매출이 어느 사업에서 나오나?", "가로 막대", [],
                          {"x": "매출액", "y": "부문"}, "백만원",
                          "부문 매출은 내부거래 제거 전후가 다르다 — 합계와 안 맞을 수 있다",
                          ["DART 사업보고서"]))
    if analysis.get("financial", {}).get("series"):
        specs.append(spec(2, "매출과 이익이 어떻게 움직였나?", "선 + 막대 조합",
                          [d["id"] for d in pack["C2_data"] if d["metric"] == "revenue"],
                          {"x": "회계연도", "y": "금액"}, "조원",
                          "Y축을 0에서 시작하지 않으면 변화가 과장된다",
                          ["DART 재무제표"]))
    if analysis.get("peers", {}).get("available"):
        specs.append(spec(3, "피어 대비 어느 위치인가?", "산점도 (PER × ROE)", [],
                          {"x": "ROE", "y": "PER"}, "배 · %",
                          "적자 기업의 PER 은 표시하지 않는다 — 음수 PER 은 의미가 없다",
                          [f"시장 스냅샷 {snapshot_store.as_of()}"]))
    if analysis.get("forecast", {}).get("statistical"):
        specs.append(spec(4, "앞으로 어느 범위인가?", "팬차트 (신뢰구간)", [],
                          {"x": "거래일", "y": "종가"}, "원",
                          "신뢰구간은 예측이 맞을 확률이 아니라 모형 가정 아래의 범위다",
                          ["KRX 일봉"]))

    pack["C4_visual"] = specs
    result["verified_result"] = [f"차트 명세 {len(specs)}건"]
    if not specs:
        contracts.downgrade(result, "그릴 자료가 없어 차트 명세를 못 만들었다")
    else:
        result["status"] = "accepted"
    result["next_state_input"] = ["H06 이 각 차트에 해석카드를 붙인다"]
    return result


# ─────────────────────────────────────────────────────────────
# H06 Interpret
# ─────────────────────────────────────────────────────────────
def h06_interpret(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H06", "INTP")
    result["context_io"]["written_fields"] = ["C5_interpretation"]

    analysis = _stashed(pack, "analysis") or {}
    red = _stashed(pack, "red_team") or {"checks": []}

    if workstream == "IND-R":
        cards = [
            narrative.industry_card(workstream, 1, analysis),
            narrative.cycle_card(workstream, 2, analysis.get("cycle", {})),
        ]
    elif workstream == "IND-TP":
        cards = [
            narrative.ranking_card(workstream, 1, analysis),
            narrative.cycle_card(workstream, 2, analysis.get("cycle", {})),
        ]
    else:
        cards = [
            narrative.segment_card(workstream, 1, analysis.get("report_facts") or {}),
            narrative.financial_card(workstream, 2, analysis.get("financial", {}).get("series", []),
                                     analysis.get("financial", {}).get("growth", {}),
                                     analysis.get("financial", {}).get("latest_ratios", {}), red),
            narrative.peer_card(workstream, 3, analysis.get("peers", {}),
                                (analysis.get("valuation") or {}).get("per_position", {}),
                                (analysis.get("valuation") or {}).get("screen", {}), red),
            narrative.timeseries_card(workstream, 4, analysis.get("forecast", {}), red),
        ]
        if workstream == "CORP-TP":
            cards.append(narrative.event_card(workstream, 5, analysis.get("events", {})))
            cards.append(narrative.score_card(workstream, 6, analysis.get("scorecard", {}),
                                              analysis.get("verdict", {})))
    pack["C5_interpretation"] = cards
    result["verified_result"] = [f"해석카드 {len(cards)}장"]
    result["provisional_interpretation"] = [c["meaning"] for c in cards]
    result["unavailable_or_unverifiable"] += [
        f"{c['research_question']} — {c['observation']}"
        for c in cards if c["observation"] == narrative.UNCHECKED]
    result["status"] = "accepted"
    result["visualization_and_interpretation"].update({
        "chart_or_table": "해석카드 4장",
        "observation": cards[1]["observation"] if len(cards) > 1 else "",
        "meaning": cards[1]["meaning"] if len(cards) > 1 else "",
        "limitation": cards[3]["limitation"] if len(cards) > 3 else "",
        "next_check": cards[1]["next_check"] if len(cards) > 1 else "",
    })
    result["next_state_input"] = ["H07 이 반대 근거를 만든다"]
    return result


# ─────────────────────────────────────────────────────────────
# H07 Red Team
# ─────────────────────────────────────────────────────────────
def h07_red_team(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H07", "RED")
    result["context_io"]["read_fields"] = ["C1_evidence", "C2_data", "C3_hypothesis", "C5_interpretation"]

    analysis = _stashed(pack, "analysis") or {}
    financial = analysis.get("financial", {})
    valuation_view = analysis.get("valuation", {})
    forecast = analysis.get("forecast", {})
    ratios = financial.get("latest_ratios", {})

    # ── 산업 계열은 검사 재료가 다르다 ──
    # `redteam.run_all` 은 기업 재무를 전제로 만든 검사 5종이다. 산업에 그대로 들이대면
    # 다섯 개가 전부 '판정불가' 로 나와 아무것도 검사하지 않은 것과 같아진다.
    # 그래서 산업이 만들어 낸 신호로 같은 형태의 검사를 만든다.
    if _target_kind(pack) == "industry":
        red = _industry_red_team(analysis)
        _stash(pack, "red_team", red)
        for check in red["checks"]:
            if check["verdict"] == "실패" and check.get("code"):
                gap = contracts.add_gap(pack, check["code"], check["check"], check["finding"],
                                        check.get("counter_evidence", ""),
                                        check.get("next_check", ""), owner="RED")
                result["gap_ids"].append(gap)
        for _ in range(red["confidence_penalty"]):
            contracts.lower_confidence(result, "Red Team 검사 실패로 신뢰도를 내렸다")
        result["verified_result"] = [red["summary"]]
        result["provisional_interpretation"] = [c["counter_evidence"] for c in red["checks"]
                                                if c.get("counter_evidence")]
        result["unavailable_or_unverifiable"] = [c["finding"] for c in red["checks"]
                                                 if c["verdict"] == "판정불가"]
        result["status"] = "accepted" if red["failed"] == 0 else "partial-continue"
        if red["failed"]:
            result["evaluation"]["verdict"] = "conditional-pass"
            result["evaluation"]["reasons"].append(f"Red Team 실패 {red['failed']}건")
        result["next_state_input"] = ["H08 이 누적 질문을 사람에게 확인한다"]
        return result

    red = redteam.run_all({
        "revenue_growth": financial.get("growth", {}).get("cagr"),
        "peer_position": valuation_view.get("per_position", {}),
        "operating_margin": (ratios.get("operating_margin") or {}).get("value"),
        "backtest": (forecast.get("probability") or {}).get("backtest"),
        "up_prob": (forecast.get("probability") or {}).get("up_prob"),
        "financial_series": financial.get("series", []),
        "earnings_quality": ratios.get("earnings_quality"),
        "claims": [{"kind": "correlation", "competing": h.get("competing"),
                    "mid_kpi": h.get("confirm_kpi")} for h in pack.get("C3_hypothesis", [])],
    })
    _stash(pack, "red_team", red)

    for check in red["checks"]:
        if check["verdict"] == "실패" and check.get("code"):
            gap = contracts.add_gap(pack, check["code"], check["check"], check["finding"],
                                    check.get("counter_evidence", ""), check.get("next_check", ""),
                                    owner="RED")
            result["gap_ids"].append(gap)

    for _ in range(red["confidence_penalty"]):
        contracts.lower_confidence(result, "Red Team 검사 실패로 신뢰도를 내렸다")

    result["verified_result"] = [red["summary"]]
    result["provisional_interpretation"] = [c["counter_evidence"] for c in red["checks"]
                                            if c.get("counter_evidence")]
    result["unavailable_or_unverifiable"] = [c["finding"] for c in red["checks"]
                                             if c["verdict"] == "판정불가"]
    result["status"] = "accepted" if red["failed"] == 0 else "partial-continue"
    if red["failed"]:
        result["evaluation"]["verdict"] = "conditional-pass"
        result["evaluation"]["reasons"].append(f"Red Team 실패 {red['failed']}건")
    result["next_state_input"] = ["H08 이 누적 질문을 사람에게 확인한다"]
    return result


def _industry_red_team(analysis: Dict) -> Dict:
    """산업 계열 Red Team — 기업용 검사 5종을 산업 재료로 다시 만든다 (명세 §5.5 형태 유지).

    검사하는 것

        ① 시장 규모 주장    상장 시가총액을 시장 규모라고 말하지 않았는가
        ② 점유율 주장       시총 비중을 점유율이라고 말하지 않았는가
        ③ 사이클 주장       신호 셋이 같은 방향인가, 갈리는가
        ④ 수요→주가 인과   실물과 주가가 같이 움직인 것을 인과로 읽지 않았는가
        ⑤ 후보군 완전성     상위 몇 곳만 보고 산업 전체를 말하지 않았는가

    판정불가를 통과로 세지 않는 규칙은 기업 쪽과 같다 (변경노트 N39).
    """
    checks: List[Dict] = []

    def add(name: str, verdict: str, finding: str, counter: str = "",
            code: str = "", next_check: str = "") -> None:
        checks.append({"check": name, "verdict": verdict, "finding": finding,
                       "counter_evidence": counter, "code": code, "next_check": next_check})

    market = analysis.get("market", {})
    if market.get("available"):
        add("시장 규모 주장", redteam.PASS,
            f"'{market['label']}' 로 이름을 붙였고 시장 규모라고 부르지 않았다",
            next_check="산업협회·시장조사 보고서로 실제 시장 규모를 확인한다")
    else:
        add("시장 규모 주장", redteam.UNKNOWN, "시장 규모를 못 냈다", code="G-SCOPE")

    rivalry = analysis.get("competition", {})
    if rivalry.get("available"):
        add("점유율 주장", redteam.PASS,
            f"HHI {rivalry['hhi']} 는 시가총액 기준이며 점유율이 아니라고 밝혔다",
            counter="매출 기준으로 재면 순위가 달라질 수 있다",
            next_check="대표 기업 사업보고서의 시장점유율 표를 확인한다")
    else:
        add("점유율 주장", redteam.UNKNOWN, "집중도를 못 냈다", code="G-DATA")

    cycle = analysis.get("cycle", {})
    if not cycle.get("available"):
        add("사이클 주장", redteam.UNKNOWN, cycle.get("reason", "국면을 판정하지 않았다"),
            code="G-DATA")
    elif cycle.get("confidence") == "low":
        add("사이클 주장", redteam.FAIL,
            f"신호가 갈린다 — {cycle.get('vote_summary')}",
            counter="한 방향으로 단정할 근거가 없다. 국면 후보를 복수로 유지해야 한다",
            code="E-INTERP",
            next_check="다음 달 생산지수·경기선행지수로 방향이 모이는지 본다")
    else:
        add("사이클 주장", redteam.PASS,
            f"신호 {len(cycle.get('votes', []))}개가 같은 방향이다 — {cycle.get('vote_summary')}",
            next_check="표본 구간을 바꿔도 결론이 유지되는지 확인한다")

    mismatch = (analysis.get("supply_demand") or {}).get("mismatch")
    if not mismatch:
        add("수요→주가 인과", redteam.UNKNOWN, "생산과 주가를 나란히 못 놓았다", code="G-DATA")
    else:
        add("수요→주가 인과", redteam.UNKNOWN, mismatch["text"],
            counter=mismatch["caution"], code="E-CAUSAL",
            next_check="금리·환율 같은 제3 변수를 통제한 뒤 다시 본다")

    universe = analysis.get("universe", {})
    if universe.get("available"):
        share = universe.get("universe_cap_share", 0)
        if universe["count"] < universe["universe_total"]:
            add("후보군 완전성", redteam.FAIL,
                f"업종 상장사 {universe['universe_total']}곳 중 {universe['count']}곳만 봤다 "
                f"(시총의 {share:.1%})",
                counter="시총 상위만 보면 작은 회사에서 나오는 성장을 놓친다",
                code="G-SCOPE",
                next_check="후보 수를 늘리거나 특정 종목을 직접 지정한다")
        else:
            add("후보군 완전성", redteam.PASS, "업종 상장사를 전수로 봤다")
    else:
        member_count = analysis.get("member_count", 0)
        add("후보군 완전성", redteam.UNKNOWN,
            f"후보군을 만들지 않았다 (산업리서치는 후보를 고르지 않는다 · 구성 종목 {member_count}곳)")

    failed = [c for c in checks if c["verdict"] == redteam.FAIL]
    unknown = [c for c in checks if c["verdict"] == redteam.UNKNOWN]
    return {
        "checks": checks,
        "failed": len(failed),
        "unknown": len(unknown),
        "passed": len(checks) - len(failed) - len(unknown),
        "confidence_penalty": len(failed),
        "codes": sorted({c["code"] for c in checks if c.get("code")}),
        "summary": (f"검사 {len(checks)}건 중 통과 {len(checks) - len(failed) - len(unknown)} · "
                    f"실패 {len(failed)} · 판정불가 {len(unknown)}"),
        "note": "판정불가를 통과로 세지 않는다 — 못 본 것과 봤는데 괜찮은 것은 다르다",
    }


# ─────────────────────────────────────────────────────────────
# H08 Human Review
# ─────────────────────────────────────────────────────────────
def h08_human_review(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H08", "HUMAN")
    result["context_io"]["read_fields"] = ["전체"]
    result["context_io"]["written_fields"] = ["C6_decisions"]

    log = pack["C6_decisions"]["feedback_log"]
    answered = [f for f in log if f.get("state") == "반영됨"]
    skipped = [f for f in log if f.get("state") == "의견 미입력"]

    result["verified_result"] = [
        f"의견 {len(answered)}건 반영 · 미입력 {len(skipped)}건",
        "AI 제안과 사람 승인을 분리해 기록한다 (불변원칙 §2-9·§2-10)",
    ]
    result["human_questions"] = [
        contracts.question("Q-H08-1", "지금까지의 범위·가정·해석을 승인하시겠습니까?",
                           ["승인", "수정 필요"], 0,
                           f"Gap {len(pack['logs']['gaps'])}건 · 충돌 {len(pack['logs']['conflicts'])}건"),
        contracts.question("Q-H08-2", "최종 리포트에서 가장 강조할 메시지는 무엇입니까?",
                           ["재무 추세", "피어 대비 위치", "시계열 시나리오"], 0,
                           "1장 헤드라인과 2장 요약이 이 답을 따른다"),
    ]
    if skipped:
        result["unavailable_or_unverifiable"] = [
            f"{f.get('question_id')} — 의견 미입력 (선호를 추정하지 않는다)" for f in skipped]
    result["status"] = "review-needed"
    result["evaluation"]["verdict"] = "human-decision"
    result["next_state_input"] = ["H09 가 승인된 내용으로 15장을 조립한다"]
    return result


# ─────────────────────────────────────────────────────────────
# H09 Assemble
# ─────────────────────────────────────────────────────────────
def h09_assemble(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H09", "ORCH")
    result["context_io"]["read_fields"] = ["C0_charter", "C1_evidence", "C2_data", "C5_interpretation"]

    analysis = _stashed(pack, "analysis") or {}
    analysis["red_team"] = _stashed(pack, "red_team") or {}
    # 투자포인트는 기업 계열에만 있다 — 산업리서치는 1위 기업을 고르지 않는다
    # (산업리서치 하네스설계서 `ERR-TOP-PICK-BOUNDARY`).
    if _target_kind(pack) == "corp":
        analysis["investment_points"] = _investment_points(analysis)
    report = export_md.assemble(pack, analysis)
    _stash(pack, "report", report)
    _stash(pack, "analysis", analysis)

    result["verified_result"] = [
        f"{report['page_count']}장 구성 (상한 15장 · {report.get('format', '')})",
        report["policy"],
    ]
    if report["merged"]:
        result["provisional_interpretation"] = report["merged"]
    if report["empty_slots"]:
        result["unavailable_or_unverifiable"] = [
            f"slot {slot} — 자료가 없어 다른 장에 합쳤다" for slot in report["empty_slots"]]
    result["status"] = "accepted"
    result["next_state_input"] = ["H10 이 100점 루브릭으로 채점한다"]
    return result


def _investment_points(analysis: Dict) -> List[Dict]:
    """투자포인트를 규칙으로 만든다 — Red Team 을 통과한 관찰만 올린다."""
    points = []
    financial = analysis.get("financial", {})
    growth = financial.get("growth", {})
    ratios = financial.get("latest_ratios", {})
    view = analysis.get("valuation", {})
    red = analysis.get("red_team", {})
    failed_checks = {c["check"] for c in red.get("checks", []) if c["verdict"] == "실패"}

    if growth.get("available") and (growth.get("cagr") or 0) > 0 and "매출 성장 주장" not in failed_checks:
        points.append({"claim": f"매출이 CAGR {growth['cagr']:+.1f}% 로 늘었다",
                       "because": f"{growth.get('years')}개년 추세 {growth.get('trend')}"})
    roe_row = ratios.get("roe", {})
    if roe_row.get("grade") == financials.GRADE_GOOD:
        points.append({"claim": f"ROE {roe_row['value']}% 로 자본효율이 기준을 넘는다",
                       "because": roe_row.get("why", "")})
    quality = ratios.get("earnings_quality", {})
    if quality.get("available") and quality.get("grade") == financials.GRADE_GOOD:
        points.append({"claim": "장부 이익이 현금으로 들어온다",
                       "because": f"영업현금흐름/영업이익 {quality['cash_conversion']:.2f}"})
    screen = view.get("screen", {})
    if screen.get("verdict") == "저평가 후보" and "저평가 주장" not in failed_checks:
        points.append({"claim": "피어 대비 저평가 후보다",
                       "because": " / ".join(screen.get("reasons", []))})
    return points


# ─────────────────────────────────────────────────────────────
# H10 Evaluate — 공통계약 §12.1 100점 루브릭
# ─────────────────────────────────────────────────────────────
RUBRIC = [
    ("대상·범위 정합성", 10), ("증거 추적성", 15), ("데이터·계산 정합성", 15),
    ("분석 논리", 15), ("시각화 정합성", 10), ("해석 품질", 20),
    ("Red Team·불확실성", 5), ("사용자 의견 추적", 5), ("양식·책임 경계", 5),
]


def h10_evaluate(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H10", "EVAL")
    result["context_io"]["read_fields"] = ["전체"]

    cover = ledger.coverage(pack)
    report = _stashed(pack, "report") or {"page_count": 0}
    red = _stashed(pack, "red_team") or {}
    cards = pack.get("C5_interpretation", [])

    scores = []

    def score(name: str, full: int, ratio: float, why: str) -> None:
        got = round(full * max(0.0, min(1.0, ratio)), 1)
        scores.append({"axis": name, "max": full, "score": got, "why": why})

    target = pack["C0_charter"].get("target", {})
    score("대상·범위 정합성", 10, 1.0 if target.get("name") else 0.3,
          "종목·기준일이 확정됐다" if target.get("name") else "대상 식별이 불완전하다")
    score("증거 추적성", 15, cover["data_linked_ratio"],
          f"D- {cover['data']}건 중 {cover['data_with_evidence']}건이 E- 로 연결됐다")
    score("데이터·계산 정합성", 15, 1.0 if cover["calculations"] else 0.5,
          f"계산 기록 {cover['calculations']}건 (재계산 완료)")
    hypotheses = pack.get("C3_hypothesis", [])
    with_falsify = [h for h in hypotheses if h.get("falsify_condition")]
    score("분석 논리", 15, len(with_falsify) / len(hypotheses) if hypotheses else 0.0,
          f"가설 {len(hypotheses)}건 중 반증 조건이 붙은 것 {len(with_falsify)}건")
    score("시각화 정합성", 10, 1.0 if pack.get("C4_visual") else 0.0,
          f"차트 명세 {len(pack.get('C4_visual', []))}건 (금지 오해 포함)")
    full_cards = [c for c in cards if c.get("limitation") != narrative.UNCHECKED]
    score("해석 품질", 20, len(full_cards) / len(cards) if cards else 0.0,
          f"해석카드 {len(cards)}장 중 한계까지 채운 것 {len(full_cards)}장")
    score("Red Team·불확실성", 5, 1.0 if red.get("checks") else 0.0,
          red.get("summary", "검사 없음"))
    feedback = pack["C6_decisions"]["feedback_log"]
    score("사용자 의견 추적", 5, 1.0 if feedback else 0.0,
          f"의견 {len(feedback)}건이 C6 에 기록됐다")
    score("양식·책임 경계", 5, 1.0 if 0 < report["page_count"] <= 15 else 0.0,
          f"{report['page_count']}장 · 고정 Disclaimer 포함")

    total = round(sum(s["score"] for s in scores), 1)

    # 중대 결함은 총점과 **별도로** 표시한다 (§12.1 마지막 문단)
    critical = []
    if not target.get("name"):
        critical.append("대상 오식별 — 종목을 확정하지 못했다")
    if cover["data_linked_ratio"] < 0.5 and cover["data"]:
        critical.append(f"근거 없는 수치가 절반을 넘는다 ({cover['data_linked_ratio']:.0%} 만 연결)")
    if red.get("failed"):
        critical.append(f"Red Team 실패 {red['failed']}건이 결론에 영향을 줄 수 있다")

    grade = "A" if total >= 90 else "B" if total >= 75 else "C" if total >= 60 else "D"
    result["verified_result"] = [f"총점 {total}/100 · 등급 {grade}"]
    result["provisional_interpretation"] = [f"{s['axis']} {s['score']}/{s['max']} — {s['why']}"
                                            for s in scores]
    result["unavailable_or_unverifiable"] = critical
    result["evaluation"]["verdict"] = "pass" if not critical and total >= 75 else "conditional-pass"
    result["evaluation"]["reasons"] = critical or [f"총점 {total}"]
    result["status"] = "accepted"
    _stash(pack, "evaluation", {"total": total, "grade": grade, "scores": scores,
                                "critical": critical, "coverage": cover})
    result["next_state_input"] = ["H11 이 Run Summary 를 만든다"]
    return result


# ─────────────────────────────────────────────────────────────
# H11 Complete
# ─────────────────────────────────────────────────────────────
def h11_complete(pack: Dict, request: Dict) -> Dict:
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H11", "ORCH")
    evaluation = _stashed(pack, "evaluation") or {}
    report = _stashed(pack, "report") or {}
    logs = pack.get("logs", {})

    unresolved = [f"{g['id']} {g.get('affected_claim_or_field')}" for g in logs.get("gaps", [])]
    result["verified_result"] = [
        f"근거 {len(pack.get('C1_evidence', []))} · 데이터 {len(pack.get('C2_data', []))} "
        f"· 계산 {len(logs.get('calculations', []))}",
        f"리포트 {report.get('page_count', 0)}장 · 평가 {evaluation.get('total', 0)}/100 "
        f"({evaluation.get('grade', '-')})",
    ]
    result["unavailable_or_unverifiable"] = unresolved
    result["status"] = "complete"
    result["next_state_input"] = [
        "4차 루프 엔지니어링을 붙일 때 이 Context Pack 을 baseline 으로 쓴다",
        "미해결 Gap 은 사람이 원문을 확인해야 닫힌다",
    ]
    return result


STAGES: Dict[str, Callable[[Dict, Dict], Dict]] = {
    "H00": h00_initialize, "H01": h01_scope, "H02": h02_evidence, "H03": h03_normalize,
    "H04": h04_analyze, "H05": h05_visualize, "H06": h06_interpret, "H07": h07_red_team,
    "H08": h08_human_review, "H09": h09_assemble, "H10": h10_evaluate, "H11": h11_complete,
}


def run_stage(state_id: str, pack: Dict, request: Dict) -> Dict:
    """상태 하나를 실행한다. 알 수 없는 상태면 Gap 을 남기고 partial-continue 로 돌려준다."""
    handler = STAGES.get(state_id)
    if not handler:
        result = contracts.new_stage_result(request.get("run_id", ""),
                                            pack["C0_charter"]["workstream_id"], state_id, "ORCH")
        contracts.downgrade(result, f"{state_id} 는 알 수 없는 상태다 (H00~H11)")
        return result
    return handler(pack, request)
