"""자연어 종목 검색 (서비스 계층) — M8 · 변경노트 N85

`search_service` 와 **무엇이 다른가**
-----------------------------------
| | `search_service` (기존) | 이 파일 (신설) |
|---|---|---|
| 묻는 것 | "이 **이름**의 종목" | "이런 **일을 하는** 종목" |
| 예 | `삼성전` → 삼성전자 | `메모리 반도체를 만드는 회사` → SK하이닉스 · 삼성전자 |
| 어떻게 | 접두·포함 매칭 + 시총 순위 | 사업보고서 임베딩 코사인 + 시총 순위 |
| 얼마나 | 몇 ms · 오프라인 | 임베딩 왕복 + 색인 조회 (실측 콜드 4.4초 · 웜 0.1초) |
| 없으면 | 없다 | **낱말 검색으로 떨어진다** (아래) |

성격이 달라 **엔드포인트를 가른다.** 자동완성은 타이핑마다 날아가므로 수 ms 를 지켜야
하는데, 임베딩은 외부 왕복이라 그 계약에 못 들어간다. 한 엔드포인트에 섞으면 빠른 쪽이
느린 쪽의 응답시간을 물려받는다.

순위를 어떻게 매기나 (2026-08-04 실측으로 정했다)
----------------------------------------------
색인 2,489곳에 자연어 질의 20건을 넣고 네 가지 규칙을 쟀다.

| 규칙 | top1 | top3 | top5 | 문제 |
|---|---:|---:|---:|---|
| 순수 코사인 | 15% | 30% | 65% | 순수 사업 회사만 올라온다 — `메모리 반도체` 1위가 어보브반도체 |
| 코사인 + 0.2×규모 | 70% | 80% | 95% | **무관한 대형주가 끼어든다** — `라면·과자` 1위가 SK하이닉스 |
| 코사인 × (1+0.8×규모) | 75% | 85% | 95% | 위와 같은 문제가 남는다 |
| **문턱형 (아래)** | **75%** | **85%** | **95%** | 끼어드는 문제가 사라진다 |

**문턱형** — ① 상위 유사도의 80% 이상인 것만 후보로 남기고 ② 그 안에서만 규모를 더한다.
무관한 대형주는 ①에서 걸린다.

    점수 = 코사인 + 0.2 × 규모가중        (규모가중 = 1 - log(시총순위)/log(3000), 0~1)

★ M9 정정 — "규모 가중이 유사도를 **살 수 없게** 만드는 것이 핵심" 이라고 적었는데
  **실측하면 살 수 있다.** 실제 유사도는 0.43~0.63 폭(약 0.2)에 몰려 있고 규모 가중이
  최대 0.2 라서, 문턱(상위의 80% = 대개 0.46 언저리)을 후보 거의 전원이 통과한 뒤
  규모가 순서를 정한다. 남은 실패 셋이 전부 그 모양이다 —

      반도체 장비 : SFA반도체 sim 0.6335 + cap 0.053 = 0.6865
                   SK하이닉스 sim 0.5720 + cap 0.183 = 0.7547  ← 유사도가 낮은데 1위
      완성차     : 화신     sim 0.5745 + cap 0.036
                   현대모비스  sim 0.5486 + cap 0.129        ← 같은 모양

  **그런데 이 규칙을 고치면 더 나빠진다.** `scripts/bench_semantic.py` 로 25가지를 쟀다
  (M9 · N97). 규모 가중을 줄이면(0.15/0.10/0.05/0.03) top1 이 85% → 65/45/45/40% 로
  떨어지고, 문턱을 조이면(0.85/0.90/0.95/0.97) 85/80/55/50% 다. 낱말 겹침 재순위를
  더해도 80% 로 내려간다. **순수 코사인은 30%** 다.

  즉 규모 가중은 버그가 아니라 **이 검색을 지탱하는 축**이다. 사업보고서 산문끼리의
  코사인이 회사를 가릴 만큼 날카롭지 않아서, "사람이 말한 그 업종에서 누구를 먼저
  떠올리는가" 를 시총이 대신 답하고 있다. 고치려면 규모를 줄일 것이 아니라
  **유사도를 날카롭게** 해야 한다 (더 긴 원문 · 업종코드 결합 · 교차 인코더).

왜 규모를 보나 — `search_service` 가 같은 이유를 이미 적어 두었다. "`삼성` 은 삼성전자·
삼성화재·삼성제약이 전부 점수 2 라 무엇을 먼저 보여줄지 정할 수 없다." 사업 설명도 같다.
`메모리 반도체` 에 맞는 회사가 40곳이면 그중 무엇을 먼저 보일지는 유사도가 못 정한다.

**정직하게 남기는 것**
    · 코사인 원값(`similarity`)과 보정 점수(`score`)를 **둘 다** 돌려준다. 규모 가중이
      순서를 얼마나 바꿨는지 화면에서 볼 수 있어야 한다.
    · 왜 그 종목이 나왔는지 보이도록 **원문 조각**(`why`)을 함께 낸다.
    · 남는 한계 (실측에서 실제로 나온 것):
        `자동차 완성차 제조`  → 1위 현대모비스 (부품사) · 2위 현대차
        `반도체 장비 제조`    → 1위 SK하이닉스 (소자사)
        `담배 제조`          → 1위 삼성물산 · 2위 KT&G
      사업보고서 문장이 비슷하면 역할이 달라도 가까이 놓인다.

    · **재는 법이 저장돼 있다** — `scripts/bench_semantic.py` (질의 20건 · 정답 포함).
      M8 은 "top1 75%" 만 적고 **질의를 남기지 않아 재현할 수 없었다.** M9 에서
      질의·정답을 파일로 못박고 다시 쟀다: **top1 85% · top3 95% · top5 100%**.
      ⚠️ 이 값은 M8 의 75/85/95 와 **비교할 수 없다** — 질의가 다르다.
      그리고 정답표가 좁으면 검색기 잘못이 아닌 것을 잘못으로 센다 (실제로
      `화장품`→에이피알 · `철강`→KISCO홀딩스 · `제약`→셀트리온이 그렇게 오답으로 세어졌다).

결정론 (§1.1) 과의 경계
---------------------
이 검색은 **리서치 파이프라인이 아니다.** 대상을 고르는 것을 돕는 화면 기능이고,
여기서 나온 점수는 리포트의 어떤 수치에도 들어가지 않는다. 색인은 빌드타임에 얼어
있으므로 (`report_index.stats().built_at`) 같은 질의는 같은 순위를 낸다 —
런타임에 달라질 수 있는 것은 HF 가 살아 있느냐뿐이고, 죽으면 낱말 검색으로 떨어진다.
"""
from __future__ import annotations

import math
from typing import Dict, List

from app.clients import hf_data
from app.repositories import report_index
from app.services import search_service

# 순위 규칙 (위 표 참고 — 2026-08-04 실측으로 고른 값)
SIMILARITY_FLOOR = 0.80     # 상위 유사도의 이 배수 아래는 규모 가중을 주지 않는다
CAP_WEIGHT = 0.20           # 규모 가중의 최대 가산점
RANK_SPAN = 3000            # 시총 순위를 0~1 로 눕힐 때의 밑변 (상장 종목 수 언저리)

MAX_LIMIT = 30
POOL = 200                  # 코사인으로 먼저 추릴 후보 수


def _cap_weight(rank) -> float:
    """시총 순위 → 0~1. 1위가 1.0, 3,000위가 0.0. 로그라 상위 몇백 곳에서만 실질 차이가 난다."""
    if not isinstance(rank, int) or rank < 1:
        return 0.0
    return max(0.0, 1.0 - math.log(min(rank, RANK_SPAN)) / math.log(RANK_SPAN))


def _rank_map() -> Dict[str, int]:
    """종목코드 → 시총 순위. `search_service` 가 이미 메모리에 올려 둔 것을 쓴다.

    ⚠️ 순위 필드는 `_p` 다 (`importance` 가 아니다 — `search_service._build_index`).
       이름을 틀리면 가중이 **조용히 0 이 되어** 순수 코사인으로 돌아간다.
       실제로 그렇게 나갔다가 실측에서 `cap_bonus` 가 전부 0.0 인 것을 보고 잡았다.

    미국 종목은 뺀다 — `_p` 가 시총 순위가 아니라 S&P 500 편입 여부(0 또는 1000)이고,
    원문 색인에는 국내 종목만 있다.
    """
    return {entry["code"]: int(entry.get("_p") or 0)
            for entry in search_service.get_index()
            if entry.get("code") and entry.get("market") == "KR"}


def _snippet(code: str, limit: int = 160) -> str:
    """왜 이 종목이 나왔는지 — 임베딩에 들어간 원문 앞부분."""
    doc = report_index.document(code)
    return (doc[:limit] + "…") if len(doc) > limit else doc


def _fallback(query: str, limit: int, reason: str) -> Dict:
    """임베딩을 못 쓸 때 **낱말 검색으로 떨어진다.**

    빈 결과를 주는 것보다 낫지만, **다른 검색을 했다는 사실을 숨기지 않는다** —
    `mode` 와 `degraded_reason` 을 그대로 실어 화면이 배지로 밝힌다 (불변원칙 §2-3).
    """
    rows = search_service.search(query, limit=limit, market="KR")
    return {
        "available": True,
        "mode": "lexical",
        "degraded": True,
        "degraded_reason": reason,
        "query": query,
        "rows": [{"code": r.get("code", ""), "name": r.get("name", ""),
                  "market": r.get("market", ""), "similarity": None, "score": None,
                  "cap_rank": r.get("importance"), "why": "",
                  "indexed": report_index.has(r.get("code", ""))} for r in rows],
        "note": "임베딩을 쓰지 못해 **이름 검색**으로 답했다 — 뜻이 아니라 글자를 맞춘 결과다.",
    }


def search(query: str, limit: int = 10) -> Dict:
    """자연어 → 관련 종목."""
    query = str(query or "").strip()
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    if not query:
        return {"available": False, "reason": "검색어가 비어 있다", "rows": []}

    if not report_index.available():
        return _fallback(query, limit,
                         "원문 색인(data/report_index.db)이 없다 — "
                         "`python3 scripts/build_report_index.py` 로 만든다")

    embedded = hf_data.embed([query])
    if not embedded.get("available"):
        return _fallback(query, limit, f"임베딩 실패 — {embedded.get('reason', '')}")

    # 코사인으로 넉넉히 추린다. 그 뒤 규모 가중은 **추려진 안에서만** 준다.
    pool = report_index.nearest(embedded["vectors"][0], limit=POOL)
    if not pool:
        return _fallback(query, limit, "색인에서 이웃을 찾지 못했다 (차원이 안 맞을 수 있다)")

    top = pool[0]["score"]
    ranks = _rank_map()
    scored: List[Dict] = []
    for hit in pool:
        similarity = hit["score"]
        # ① 문턱 — 상위 유사도의 80% 아래면 규모로 끌어올리지 않는다.
        #    이 한 줄이 "라면·과자 검색에 SK하이닉스가 1위" 를 막는다 (실측).
        boost = (CAP_WEIGHT * _cap_weight(ranks.get(hit["code"], 0))
                 if similarity >= SIMILARITY_FLOOR * top else 0.0)
        scored.append({
            "code": hit["code"], "name": hit["name"],
            "similarity": round(similarity, 4),
            "score": round(similarity + boost, 4),
            "cap_bonus": round(boost, 4),
            "cap_rank": ranks.get(hit["code"], 0) or None,
            "why": _snippet(hit["code"]),
            "indexed": True,
        })
    scored.sort(key=lambda r: -r["score"])

    return {
        "available": True,
        "mode": "semantic",
        "degraded": False,
        "query": query,
        "rows": scored[:limit],
        "model": embedded.get("model", ""),
        "elapsed_sec": embedded.get("elapsed_sec"),
        "pool": len(pool),
        "index": {"reports": report_index.stats().get("reports"),
                  "built_at": report_index.stats().get("built_at")},
        "ranking": (f"코사인 유사도 + 규모 가중 (상위 유사도의 "
                    f"{SIMILARITY_FLOOR:.0%} 이상인 후보에만 최대 {CAP_WEIGHT} 가산). "
                    f"`similarity` 가 원값이고 `score` 가 보정값이다"),
        "note": ("사업보고서 본문으로 찾는다. 실측 top1 85% · top3 95% · top5 100% "
                 "(`scripts/bench_semantic.py` 질의 20건 · 2026-08-04). "
                 "**1위가 늘 맞지는 않는다** — 사업 설명이 비슷하면 역할이 달라도 "
                 "가까이 놓인다 (예: '자동차 완성차' 에 부품사가 올라온다)."),
    }
