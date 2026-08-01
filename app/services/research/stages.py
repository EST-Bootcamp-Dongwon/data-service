"""H00~H11 상태 함수 (명세 §5.1)

각 함수는 **(Context Pack, 요청) → (갱신된 Context Pack, stage_result)** 하나를 한다.
서버는 상태를 갖지 않으므로 팩을 받아 고쳐 돌려주는 것이 전부다 (명세 §1.3).

지키는 규칙 세 가지.

1. **오류로 중단하지 않는다.** 자료가 없으면 `contracts.downgrade()` 로
   `partial-continue` 를 만들고 Gap 을 남긴 뒤 다음 상태로 넘긴다 (명세 §6.4).
2. **modelable=False 면 모델링을 건너뛴다.** M3 전처리가 그렇게 판정했으면 H04 도 따른다.
3. **근사·한계는 limitation 으로 내보낸다.** M3 의 `caveats` 를 그대로 싣는다.

명세 §5.4 대로 CORP-R 은 H03 이 3단(회계기간 → 재무 → 피어)으로 갈라진다.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional

from ...clients import dart_data, dart_report
from ...repositories import industry_store, snapshot_store
from .. import ts_service
from . import contracts, export_md, ledger, narrative, plan, redteam
from .knowledge import financials, valuation
from .workstreams import corp_r


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
    if workstream != "CORP-R":
        contracts.downgrade(result, f"{workstream} 은 M5 에서 구현한다 — 지금은 CORP-R 만 돈다")
    result["status"] = result["status"] if result["status"] == "partial-continue" else "accepted"
    result["next_state_input"] = ["H01 이 대상 식별과 기준일을 확정한다"]
    return result


# ─────────────────────────────────────────────────────────────
# H01 Scope
# ─────────────────────────────────────────────────────────────
def h01_scope(pack: Dict, request: Dict) -> Dict:
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
            (f"업종({industry.get('matched_prefix', '?')}) 후보 {len(industry.get('peers', []))}곳이 있다"
             if industry.get("available") else "이 종목의 업종코드를 못 찾아 시총 기준이 안전하다")),
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
    workstream = pack["C0_charter"]["workstream_id"]
    result = contracts.new_stage_result(request.get("run_id", ""), workstream, "H02", "EVID")
    result["context_io"]["read_fields"] = ["C0_charter"]
    result["context_io"]["written_fields"] = ["C1_evidence", "logs.gaps"]

    code = pack["C0_charter"]["target"].get("code", "")
    name = pack["C0_charter"]["target"].get("name", "")
    started = time.monotonic()
    rows: List[Dict] = []

    # 1) 공시 목록 — 카테고리별 최신 N건만 E- 로 만든다
    try:
        listing = dart_data.fetch_disclosures(code, months=12, limit=100)
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

    # 2) 재무제표
    try:
        loaded = corp_r.load_financials(code, years=5)
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
        result["verified_result"] = [f"근거 {len(result['evidence_ids'])}건 발급"]
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

    result["verified_result"] = [f"근거 {len(result['evidence_ids'])}건 발급"]
    result["confidence"] = "high" if len(result["evidence_ids"]) >= 5 else "medium"
    if result["status"] == "in-progress":
        result["status"] = "accepted"
    result["next_state_input"] = ["H03 이 이 근거들을 D- 로 정규화한다"]
    return result


# ─────────────────────────────────────────────────────────────
# H03 Normalize — CORP-R 은 3단 (명세 §5.4)
# ─────────────────────────────────────────────────────────────
def h03_normalize(pack: Dict, request: Dict) -> Dict:
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
        ratios = corp_r.ratios_of({k: v for k, v in latest.items() if k != "year"})
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

    _stash(pack, "analysis", {
        "financial": {"series": series, "growth": growth, "latest_ratios": ratios},
        "peers": peers,
        "valuation": view,
        "forecast": forecast,
        "report_facts": facts,
    })

    result["verified_result"] = [
        f"재무 {len(series)}개년 · 성장 추세 {growth.get('trend', '판정불가')}",
        f"피어 {len(peers.get('peers', []))}곳 · {(view.get('per_position') or {}).get('stance', '비교 없음')}",
        f"예측 {forecast.get('statistical', {}).get('model', '없음')}",
    ]
    result["human_questions"] = [
        contracts.question("Q-H04-1", "밸류에이션에서 어떤 멀티플을 본문에 둘까요?",
                           [f"{(view.get('multiple_choice') or {}).get('multiple', 'PER')} (규칙 추천)",
                            "PER", "PBR", "EV/EBITDA"], 0,
                           (view.get("multiple_choice") or {}).get("why", "")),
        contracts.question("Q-H04-2", "성장성과 현금전환 중 어느 쪽을 더 강조할까요?",
                           ["현금전환 (보수적)", "성장성"], 0,
                           "이익의 질 판정: " + (ratios.get("earnings_quality", {}).get("why", "판정 없음"))),
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
    analysis["investment_points"] = _investment_points(analysis)
    report = export_md.assemble(pack, analysis)
    _stash(pack, "report", report)

    result["verified_result"] = [
        f"{report['page_count']}장 구성 (상한 15장)",
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
