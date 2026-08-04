#!/usr/bin/env python3
"""배포용 사업보고서 원문 색인 생성 스크립트 — M8 (변경노트 N85)

배포본에서 **사업보고서 원문 파싱이 꺼져 있다** (N41). 원문(0.8MB ZIP) 내려받기가
서버리스 60초 제한을 넘겨 H02 가 통째로 죽었기 때문이다. 그 대가로 slot 4·5·7 이
비어 리포트가 로컬 14장 → 배포본 11장으로 나온다.

이 스크립트는 그 원문을 **빌드타임에 미리 훑어** 두 가지를 만든다.

    scripts/build_report_index.py  →  data/report_index.db
                                          ├─ report  종목별 원문 팩트 (부문·점유율·생산능력·연구개발·사업개요)
                                          ├─ vector  같은 팩트를 임베딩한 int8 벡터 (자연어 검색용)
                                          ├─ miss    **못 만든 종목과 그 사유** (빼지 않고 남긴다)
                                          └─ meta    무엇을 언제 어떻게 만들었나 (자기설명)
                                                ↑ app/repositories/report_index.py 가 읽는다

두 가지 쓰임이 한 파일에서 나온다
--------------------------------
| 쓰임 | 어떻게 | 결정론 |
|---|---|---|
| ① 배포본 slot 4·5·7 복구 | `report` 를 **종목코드로 직접** 집는다 (네트워크 0회) | ✅ 빌드타임 고정 |
| ② 자연어 종목 검색 | `vector` 에 질의 임베딩을 견준다 | 색인은 ✅ · 질의 임베딩만 런타임 |

**§1.1 결정론과의 경계를 여기에 못박는다.** "같은 종목·같은 기준일이면 같은 결과" 는
리서치 파이프라인의 약속이다. ①은 코드로 직접 집으므로 그 약속 안에 그대로 있다 —
색인이 빌드타임에 얼어 있고 `meta` 가 언제 얼렸는지 말한다. ②만 런타임에 HF 를 부르는데,
**②의 결과는 리포트 수치에 들어가지 않는다.** 대상을 고르는 것을 돕는 검색일 뿐이다.

왜 런타임에 임베딩하지 않나
--------------------------
`hf_data.REQUEST_TIMEOUT` 이 20초이고 종목 하나의 사업보고서에 표가 2천~4천 개다.
요청마다 그것을 임베딩하면 서버리스 60초를 몇 배로 넘긴다. 그래서 **문서 쪽은 전부
빌드타임에 미리 계산하고, 런타임에는 질의 한 건만 임베딩한다.**

임베딩 모델을 무엇으로 골랐나 (2026-08-04 실측)
---------------------------------------------
실제 사업보고서 42곳을 건초더미로 놓고 자연어 질의 22건을 넣어 네 후보를 쟀다.
HF 추론 API 는 `/pipeline/feature-extraction` 경로를 써야 한다 (기본 경로는 이 모델들이
sentence-similarity 로 등록돼 있어 400 이 난다).

| 모델 | 차원 | top1 | top3 | 2,763종목 빌드 | int8 크기 |
|---|---:|---:|---:|---:|---:|
| **nlpai-lab/KURE-v1** ← 선택 | 1024 | **91%** | **95%** | 21.8분 | 2.8MB |
| BAAI/bge-m3 | 1024 | 86% | 91% | 92.5분 | 2.8MB |
| intfloat/multilingual-e5-small | 384 | 68% | 86% | 15.7분 | 1.1MB |
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 64% | 91% | 1.0분 | 1.1MB |

**int8 양자화가 정확도를 깎지 않았다** — KURE 는 float32 86/91% → int8 91/95% 로 오히려
올랐다 (표본 22건이라 이 차이 자체는 잡음 범위다. 확인한 것은 "깎이지 않는다" 쪽이다).
그래서 4분의 1 크기인 int8 로 담는다.

솔직하게 남기는 것들
------------------
- **모든 종목이 나오지 않는다.** 우선주는 DART 고유번호가 없고, 인프라펀드·리츠 등은
  사업보고서를 내지 않는다. 그런 종목은 `miss` 표에 사유와 함께 남긴다. 조용히 빼면
  화면이 "2,763곳을 뒤졌다" 고 말하면서 실제로는 덜 뒤진 것이 된다 (불변원칙 §2-3).
- **회사마다 서식이 달라 다 나오지 않는다.** 실측 검출률 — 사업개요 100% · 연구개발 81% ·
  부문 71% · 생산능력 71% · 점유율 29%. 없는 항목은 `found=False` 와 사유가 그대로 담긴다.
- **사업보고서는 연 1회(3월 집중) 나온다.** 색인은 **수동 갱신**이고 얼마나 뒤처졌는지는
  `meta.built_at` 과 종목마다의 `rcept_date` 가 말한다. 자동으로 갱신하지 않는다.
- 검색 정확도는 위 표가 전부다. **top1 91% 는 9%를 틀린다는 뜻이다.** 실측에서 실제로
  "자동차를 만드는 회사" 에 현대모비스(부품사)가, "철강을 만든다" 에 삼성중공업이 1위로
  나왔다. 화면은 순위와 함께 **왜 그 종목이 나왔는지(원문 조각)** 를 같이 보여 준다.

실행
----
    python3 scripts/build_report_index.py                 # 전종목 (DART 52분 + 임베딩 22분)
    python3 scripts/build_report_index.py --limit 200     # 시총 상위 200곳만 (약 5분)
    python3 scripts/build_report_index.py --resume        # 중단된 지점부터 이어서
    python3 scripts/build_report_index.py --stage embed   # 팩트는 그대로 두고 임베딩만 다시
    python3 scripts/build_report_index.py --check         # 현재 산출물 상태만 본다

⚠️ 배포 전에 이 스크립트를 돌릴 필요는 없다 (`build_krx_bundle.py` 와 다르다).
   `report_index.db` 는 사업보고서가 새로 나올 때만 다시 만든다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# 스크립트를 어디서 실행하든 저장소 루트를 기준으로 삼는다
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.clients import dart_report          # noqa: E402
from app.core import secrets                 # noqa: E402

INDEX_DB = PROJECT_ROOT / "data" / "report_index.db"
STOCK_MASTER = PROJECT_ROOT / "data" / "stock_master.json"
CORP_CODE = PROJECT_ROOT / "data" / "corp_code.json"

KST = timezone(timedelta(hours=9))

# ── 임베딩 설정 (위 머리말의 실측 표 참고) ──────────────────────────
EMBED_MODEL = "nlpai-lab/KURE-v1"
EMBED_DIM = 1024
HF_BASE_URL = "https://router.huggingface.co/hf-inference/models"
# 기본 경로는 이 모델이 sentence-similarity 로 등록돼 있어 400 이 난다 (실측).
HF_PATH = "/pipeline/feature-extraction"
EMBED_BATCH = 16                 # 한 번에 보낼 문장 수
EMBED_WORKERS = 6                # 동시 호출 (실측 42곳 19.9초)

# ── 수집 설정 ────────────────────────────────────────────────────
FETCH_WORKERS = 4                # DART 동시 호출. 일 2만 한도 안이고 종목당 2회다
SAVE_EVERY = 25                  # 이만큼마다 중간 저장 — 끊겨도 --resume 으로 잇는다
SUMMARY_CHARS = 900              # 임베딩 문장에 넣을 사업개요 길이

SCHEMA = """
-- 종목별 사업보고서 원문 팩트. `facts` 는 `dart_report.fetch_report_facts` 결과 원형이다.
-- 원형을 그대로 담는 이유 — 런타임이 네트워크로 받던 것과 **같은 모양**이어야
-- H02 아래 코드(파서·Gap 발급)를 하나도 안 고치고 갈아끼울 수 있다.
CREATE TABLE IF NOT EXISTS report (
  code        TEXT PRIMARY KEY,
  name        TEXT,          -- 우리 종목마스터의 이름
  corp_name   TEXT,          -- DART 가 부르는 이름 (다를 수 있다)
  rcept_no    TEXT,          -- 접수번호 — 근거 E- 가 이것을 인용한다
  report_name TEXT,
  rcept_date  TEXT,
  url         TEXT,
  size_mb     REAL,
  table_count INTEGER,
  facts       TEXT,          -- JSON
  doc         TEXT,          -- 임베딩에 넣은 문장 (왜 이 검색결과가 나왔는지 보여 준다)
  fetched_at  TEXT
);

-- **못 만든 종목을 남긴다.** 빼 버리면 "전종목을 뒤졌다" 가 거짓말이 된다.
CREATE TABLE IF NOT EXISTS miss (
  code TEXT PRIMARY KEY, name TEXT, reason TEXT, tried_at TEXT
);

-- 임베딩 int8. 단위벡터를 127배해 반올림한 것이라 읽는 쪽에서 127로 나눈다.
CREATE TABLE IF NOT EXISTS vector (
  code TEXT PRIMARY KEY, vec BLOB
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _size_mb(path: Path) -> float:
    return round(path.stat().st_size / 1e6, 2) if path.exists() else 0.0


def _connect() -> sqlite3.Connection:
    INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX_DB)
    conn.executescript(SCHEMA)
    return conn


# ════════════════════════════════════════════════════════════════
# 1. 대상 목록
# ════════════════════════════════════════════════════════════════
def targets(limit: int = 0) -> list:
    """시가총액 순위대로 (종목코드, 이름) 목록.

    **DART 고유번호가 없는 종목은 애초에 부르지 않는다.** 우선주(`005935` 삼성전자우 등)가
    거기 해당하고, 부르면 반드시 실패하는 호출을 2,763번 중 수백 번 하게 된다.
    빼는 대신 `miss` 에 사유를 적는다.
    """
    if not STOCK_MASTER.exists():
        raise SystemExit(f"종목마스터가 없습니다: {STOCK_MASTER}\n"
                         "  python3 scripts/build_stock_master.py 를 먼저 실행하세요.")
    if not CORP_CODE.exists():
        raise SystemExit(f"DART 고유번호 매핑이 없습니다: {CORP_CODE}\n"
                         "  python3 scripts/build_corp_code.py 를 먼저 실행하세요.")

    master = json.loads(STOCK_MASTER.read_text(encoding="utf-8"))
    corp_map = json.loads(CORP_CODE.read_text(encoding="utf-8")).get("map", {})

    # 값은 [이름, 시장, 시총순위] 다. 순위대로 세우면 --limit N 이 곧 '상위 N곳' 이 된다.
    rows = sorted(((code, meta[0], meta[2]) for code, meta in master.items()),
                  key=lambda r: r[2])
    picked, skipped = [], []
    for code, name, _rank in rows:
        (picked if code in corp_map else skipped).append((code, name))
    if limit:
        picked = picked[:limit]
    return picked, skipped


# ════════════════════════════════════════════════════════════════
# 2. 원문 팩트 수집
# ════════════════════════════════════════════════════════════════
def _doc_text(name: str, facts: dict) -> str:
    """임베딩에 넣을 문장.

    ⚠️ **이 조립 규칙을 바꾸면 위 머리말의 실측 정확도가 더 이상 그 값이 아니다.**
    실측(top1 91%)은 정확히 이 모양으로 잰 것이다. 바꾸면 다시 재고 표를 고쳐라.
    """
    parts = [facts.get("corp_name") or name]
    if facts.get("business_summary"):
        parts.append(facts["business_summary"][:SUMMARY_CHARS])
    segments = facts.get("segments") or {}
    if segments.get("rows"):
        parts.append("사업부문: " + " · ".join(
            str(r.get("segment", "")) for r in segments["rows"][:12]))
    share = facts.get("market_share") or {}
    if share.get("rows"):
        parts.append("주요 제품: " + " · ".join(
            str(r.get("item") or r.get("segment") or "") for r in share["rows"][:10]))
    return " ".join(p for p in parts if p)


def fetch_facts(limit: int = 0, resume: bool = False) -> dict:
    """[2/3] DART 사업보고서 원문 → 종목별 팩트."""
    picked, no_corp_code = targets(limit)
    conn = _connect()
    now = _now_kst()

    # 고유번호가 없어 부르지 않기로 한 종목을 먼저 기록한다 (부르지 않은 것도 결과다)
    conn.executemany(
        "INSERT OR REPLACE INTO miss (code, name, reason, tried_at) VALUES (?,?,?,?)",
        [(code, name, "DART 고유번호가 없다 (우선주 등 — 사업보고서 제출 주체가 아니다)", now)
         for code, name in no_corp_code])
    conn.commit()

    done = set()
    if resume:
        done = {r[0] for r in conn.execute("SELECT code FROM report")}
        done |= {r[0] for r in conn.execute(
            "SELECT code FROM miss WHERE reason NOT LIKE '%고유번호%'")}
    todo = [(c, n) for c, n in picked if c not in done]
    print(f"      대상 {len(picked)}곳 (고유번호 없어 제외 {len(no_corp_code)}곳) · "
          f"이미 끝난 것 {len(done & {c for c, _ in picked})}곳 · 이번에 {len(todo)}곳")
    if not todo:
        conn.close()
        return {"fetched": 0, "missed": 0}

    started = time.monotonic()
    ok = fail = 0
    buffer_ok, buffer_miss = [], []

    def one(item):
        code, name = item
        try:
            return code, name, dart_report.fetch_report_facts(code), ""
        except Exception as error:           # 한 종목이 전체 빌드를 죽이지 않는다
            return code, name, None, f"{type(error).__name__}: {str(error)[:120]}"

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for index, (code, name, facts, error) in enumerate(pool.map(one, todo), 1):
            stamp = _now_kst()
            if error or not facts or not facts.get("available"):
                reason = error or (facts or {}).get("reason", "원문을 받지 못했다")
                buffer_miss.append((code, name, reason, stamp))
                fail += 1
            else:
                buffer_ok.append((
                    code, name, facts.get("corp_name", ""), facts.get("rcept_no", ""),
                    facts.get("report_name", ""), facts.get("rcept_date", ""),
                    facts.get("url", ""), facts.get("size_mb", 0.0),
                    facts.get("table_count", 0),
                    json.dumps(facts, ensure_ascii=False),
                    _doc_text(name, facts), stamp))
                ok += 1

            if index % SAVE_EVERY == 0 or index == len(todo):
                # 중간 저장 — 52분짜리 작업이라 끊겨도 --resume 으로 이어야 한다
                conn.executemany(
                    "INSERT OR REPLACE INTO report (code,name,corp_name,rcept_no,report_name,"
                    "rcept_date,url,size_mb,table_count,facts,doc,fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buffer_ok)
                conn.executemany(
                    "INSERT OR REPLACE INTO miss (code,name,reason,tried_at) VALUES (?,?,?,?)",
                    buffer_miss)
                conn.commit()
                buffer_ok, buffer_miss = [], []
                elapsed = time.monotonic() - started
                rate = elapsed / index
                print(f"      {index}/{len(todo)} · 성공 {ok} 실패 {fail} · "
                      f"{elapsed/60:.1f}분 경과 · 남은 예상 {(len(todo)-index)*rate/60:.1f}분",
                      flush=True)

    conn.execute("INSERT OR REPLACE INTO meta VALUES ('facts_built_at', ?)", (_now_kst(),))
    conn.commit()
    conn.close()
    return {"fetched": ok, "missed": fail, "seconds": round(time.monotonic() - started, 1)}


# ════════════════════════════════════════════════════════════════
# 3. 임베딩
# ════════════════════════════════════════════════════════════════
def _hf_token() -> str:
    token, _ = secrets.load_key(("HUGGINGFACE_ACCESS_TOKEN", "HF_TOKEN", "HUGGINGFACE_API_KEY"))
    if not token:
        raise SystemExit("HuggingFace 토큰이 없습니다.\n"
                         "  .key 에 HUGGINGFACE_ACCESS_TOKEN = hf_... 한 줄을 넣으세요.")
    return token


def embed_batch(texts: list, token: str, timeout: int = 120) -> list:
    request = Request(f"{HF_BASE_URL}/{EMBED_MODEL}{HF_PATH}",
                      data=json.dumps({"inputs": texts}, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json",
                               "User-Agent": "api-test-GIC-harness/M8"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def to_int8(vector: list) -> bytes:
    """단위벡터로 만든 뒤 int8 로 담는다 (4분의 1 크기 · 실측에서 정확도가 깎이지 않았다)."""
    scale = sum(x * x for x in vector) ** 0.5 or 1.0
    return bytes((max(-127, min(127, round(x / scale * 127))) & 0xFF) for x in vector)


def build_vectors(resume: bool = False) -> dict:
    """[3/3] 팩트 문장 → int8 임베딩."""
    token = _hf_token()
    conn = _connect()
    done = {r[0] for r in conn.execute("SELECT code FROM vector")} if resume else set()
    rows = [(r[0], r[1]) for r in conn.execute("SELECT code, doc FROM report ORDER BY code")
            if r[0] not in done and (r[1] or "").strip()]
    print(f"      임베딩 대상 {len(rows)}건 (이미 끝난 것 {len(done)}건) · "
          f"{EMBED_MODEL} {EMBED_DIM}차원 int8")
    if not rows:
        conn.close()
        return {"embedded": 0}

    started = time.monotonic()
    batches = [rows[i:i + EMBED_BATCH] for i in range(0, len(rows), EMBED_BATCH)]
    saved = failed = 0

    def one(batch):
        try:
            return batch, embed_batch([doc for _, doc in batch], token), ""
        except Exception as error:
            return batch, None, f"{type(error).__name__}: {str(error)[:120]}"

    with ThreadPoolExecutor(max_workers=EMBED_WORKERS) as pool:
        for index, (batch, vectors, error) in enumerate(pool.map(one, batches), 1):
            if error or not vectors or len(vectors) != len(batch):
                # 배치 하나가 실패해도 계속 간다. 못 만든 벡터는 `vector` 에 그냥 없다 —
                # 검색은 있는 것끼리만 견주고 `stats()` 가 몇 건이 빠졌는지 밝힌다.
                failed += len(batch)
                print(f"      ⚠ 배치 {index} 실패 — {error or '응답 길이 불일치'}", flush=True)
                continue
            conn.executemany("INSERT OR REPLACE INTO vector (code, vec) VALUES (?,?)",
                             [(code, to_int8(vec)) for (code, _), vec in zip(batch, vectors)])
            conn.commit()
            saved += len(batch)
            if index % 10 == 0 or index == len(batches):
                elapsed = time.monotonic() - started
                print(f"      {saved}/{len(rows)}건 · {elapsed/60:.1f}분 경과 · "
                      f"남은 예상 {(len(rows)-saved)*(elapsed/max(saved,1))/60:.1f}분", flush=True)

    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
        ("embed_model", EMBED_MODEL),
        ("embed_dim", str(EMBED_DIM)),
        ("embed_quant", "int8 (단위벡터 × 127 · 읽는 쪽에서 127로 나눈다)"),
        ("embed_doc_recipe", f"corp_name + business_summary[:{SUMMARY_CHARS}] "
                             "+ 사업부문 라벨 12개 + 주요 제품 라벨 10개"),
        ("embed_built_at", _now_kst()),
        ("embed_failed", str(failed)),
    ])
    conn.commit()
    conn.close()
    return {"embedded": saved, "failed": failed,
            "seconds": round(time.monotonic() - started, 1)}


# ════════════════════════════════════════════════════════════════
# 4. 상태 확인
# ════════════════════════════════════════════════════════════════
def check() -> None:
    if not INDEX_DB.exists():
        print(f"  ✗ {INDEX_DB.name} 이 없습니다. `python3 scripts/build_report_index.py` 로 만드세요.")
        return
    conn = _connect()
    reports = conn.execute("SELECT COUNT(*) FROM report").fetchone()[0]
    vectors = conn.execute("SELECT COUNT(*) FROM vector").fetchone()[0]
    misses = conn.execute("SELECT COUNT(*) FROM miss").fetchone()[0]
    meta = dict(conn.execute("SELECT key, value FROM meta"))

    print(f"  {INDEX_DB.name}  {_size_mb(INDEX_DB)}MB")
    print(f"    원문 팩트 {reports:,}곳 · 임베딩 {vectors:,}곳 · 못 만든 곳 {misses:,}곳")
    for key in ("facts_built_at", "embed_built_at", "embed_model", "embed_dim"):
        if meta.get(key):
            print(f"    {key} = {meta[key]}")

    # 검출률 — 회사마다 서식이 달라 다 나오지 않는다는 사실을 수치로 낸다
    found = {"segments": 0, "market_share": 0, "capacity": 0, "rnd": 0, "business_summary": 0}
    for (raw,) in conn.execute("SELECT facts FROM report"):
        facts = json.loads(raw)
        for key in ("segments", "market_share", "capacity", "rnd"):
            if (facts.get(key) or {}).get("found"):
                found[key] += 1
        if facts.get("business_summary"):
            found["business_summary"] += 1
    if reports:
        print("    검출률 — " + " · ".join(
            f"{label} {found[key]/reports:.0%}" for key, label in
            (("business_summary", "사업개요"), ("segments", "부문"),
             ("market_share", "점유율"), ("capacity", "생산능력"), ("rnd", "연구개발"))))

    reasons: dict = {}
    for (reason,) in conn.execute("SELECT reason FROM miss"):
        head = str(reason).split("—")[0].strip()[:44]
        reasons[head] = reasons.get(head, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:6]:
        print(f"    못 만든 사유 — {reason} {count:,}곳")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="배포용 사업보고서 원문 색인을 만든다.")
    parser.add_argument("--limit", type=int, default=0,
                        help="시가총액 상위 N곳만 (0 이면 전종목)")
    parser.add_argument("--resume", action="store_true",
                        help="이미 만든 종목을 건너뛰고 이어서 만든다")
    parser.add_argument("--stage", choices=("all", "facts", "embed"), default="all",
                        help="어느 단계만 돌릴지")
    parser.add_argument("--check", action="store_true", help="현재 산출물 상태만 확인한다")
    args = parser.parse_args()

    if args.check:
        check()
        return

    started = time.monotonic()
    scope = f"시총 상위 {args.limit}곳" if args.limit else "전종목"
    print(f"사업보고서 원문 색인 — {scope} · {_now_kst()}")

    stats = {}
    print(f"[1/3] 대상 목록 …")
    picked, skipped = targets(args.limit)
    print(f"      {len(picked):,}곳 (DART 고유번호 없는 {len(skipped):,}곳 제외)")

    if args.stage in ("all", "facts"):
        print(f"[2/3] DART 사업보고서 원문 (동시 {FETCH_WORKERS}) …")
        stats["facts"] = fetch_facts(args.limit, resume=args.resume)
    if args.stage in ("all", "embed"):
        print(f"[3/3] 임베딩 (동시 {EMBED_WORKERS}) …")
        stats["vectors"] = build_vectors(resume=args.resume)

    elapsed = time.monotonic() - started
    conn = _connect()
    reports = conn.execute("SELECT COUNT(*) FROM report").fetchone()[0]
    vectors = conn.execute("SELECT COUNT(*) FROM vector").fetchone()[0]
    misses = conn.execute("SELECT COUNT(*) FROM miss").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('scope', ?)",
                 (f"{scope} · 원문 {reports}곳 · 임베딩 {vectors}곳 · 못 만든 곳 {misses}곳",))
    conn.commit()
    conn.close()

    print(f"완료 — {INDEX_DB.name} {_size_mb(INDEX_DB)}MB · "
          f"원문 {reports:,}곳 · 임베딩 {vectors:,}곳 · 못 만든 곳 {misses:,}곳 · "
          f"{elapsed:.0f}초 ({elapsed/60:.1f}분)")
    print(f"  {json.dumps(stats, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
