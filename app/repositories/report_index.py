"""배포용 사업보고서 원문 색인 읽기 (저장소 계층) — M8 · 변경노트 N85

`scripts/build_report_index.py` 가 만든 읽기 전용 번들을 읽는다.

    scripts/build_report_index.py  →  data/report_index.db     2,489곳 · 18.4MB
                                            ↑ 이 모듈이 읽는다

이 모듈은 **읽기만 한다.** 무엇을 고를지·어떻게 셀지는 서비스 계층의 일이다.
(`krx_bundle.py` 와 같은 규칙·같은 모양으로 만들었다)

두 가지 쓰임
----------
| 쓰임 | 함수 | 결정론 |
|---|---|---|
| ① 배포본 slot 4·5·7 복구 | `facts(code)` — 종목코드로 직접 집는다 | ✅ 빌드타임 고정 |
| ② 자연어 종목 검색 | `nearest(vector, limit)` — 코사인 상위 N | 색인은 ✅ · 질의 임베딩만 런타임 |

**§1.1 결정론과의 경계** — "같은 종목·같은 기준일이면 같은 결과" 는 리서치 파이프라인의
약속이다. ①은 코드로 직접 집으므로 그 약속 안에 그대로 있다. 색인은 빌드타임에 얼어
있고 `stats()` 가 언제 얼렸는지 말한다. ②만 런타임에 HF 를 부르는데, **②의 결과는
리포트 수치에 들어가지 않는다** — 대상을 고르는 것을 돕는 검색일 뿐이다.

읽기 전용을 어떻게 여는가
----------------------
`sqlite3.connect(path)` 는 파일이 없으면 만들려 하고 `PRAGMA journal_mode=WAL` 도 쓰기다.
배포 번들은 읽기 전용이라 둘 다 그 자리에서 예외가 난다. URI 형식으로 열어 `mode=ro` 를
못박고 WAL 설정을 하지 않는다 (`krx_bundle` 과 같은 이유).

번들이 원본과 다른 점 (있는 척하지 않는다)
--------------------------------------
| | 실시간 DART | 번들 |
|---|---|---|
| 최신성 | 요청 시점 | **빌드 시점에 고정** (`stats().built_at`) |
| 대상 | 전부 | **2,489곳** — 우선주 167곳·사업보고서 없는 108곳은 없다 |
| 내용 | 같다 | 같다 (`fetch_report_facts` 결과 원형을 그대로 담았다) |
| 소요 | 배포본 약 9초 | **0ms** (인덱스로 한 행) |

없는 종목은 `{"available": False, "reason": ...}` 를 돌려준다 — `miss` 표에 사유가
남아 있으면 그 사유를 그대로 전한다. **없는 것을 없다고 말한다.**

⚠️ 지연 색인 잠금 (요약본 지뢰 1)
    벡터 행렬은 첫 검색 때 만든다. **반드시 잠금 밖에서 먼저 읽고**, 잠금 안에서는
    대입만 한다. 잠금을 쥔 채 18MB 를 읽으면 동시 요청이 그 시간만큼 줄을 서다
    504 가 난다 (M5 에서 두 번 겪었다).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

# 이 파일은 app/repositories/ 안에 있으므로 parents[2] 가 프로젝트 루트다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = PROJECT_ROOT / "data" / "report_index.db"

QUANT_SCALE = 127.0        # 빌드가 단위벡터를 127배해 int8 로 담았다

# 검색용 행렬은 한 번 만들어 재사용한다 (읽기 전용 파일이라 도중에 바뀌지 않는다)
_matrix: Optional[np.ndarray] = None       # (n, dim) float32 · 각 행이 단위벡터
_codes: Optional[List[str]] = None
_names: Optional[List[str]] = None
_lock = threading.Lock()


def available() -> bool:
    return INDEX_PATH.exists()


def _connect() -> sqlite3.Connection:
    """읽기 전용으로 연다 — 배포 번들은 쓸 수 없다."""
    return sqlite3.connect(f"file:{INDEX_PATH}?mode=ro", uri=True)


def _query(sql: str, params: Sequence = ()) -> List[sqlite3.Row]:
    """번들이 없거나 깨져도 **리서치를 죽이지 않는다** — 빈 목록을 준다 (불변원칙 §2-2)."""
    if not available():
        return []
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []


# ══════════════════════════════════════════════════════════
# 1. 종목코드로 원문 팩트 집기 (결정론 · 네트워크 0회)
# ══════════════════════════════════════════════════════════
def facts(code: str) -> Dict:
    """`dart_report.fetch_report_facts` 와 **같은 모양**을 돌려준다.

    같은 모양이어야 H02 아래 코드(파서·Gap 발급·근거 문구)를 하나도 안 고치고
    네트워크 대신 번들을 끼울 수 있다. 다른 점은 `source` 한 필드뿐이다.
    """
    rows = _query("SELECT facts, fetched_at FROM report WHERE code = ?", (str(code),))
    if rows:
        try:
            payload = json.loads(rows[0]["facts"])
        except Exception as error:
            return {"available": False, "code": code,
                    "reason": f"색인의 원문 팩트를 읽지 못했다 — {error}"}
        payload["source"] = "report_index"
        payload["indexed_at"] = rows[0]["fetched_at"]
        return payload

    # 없는 이유를 **알고 있으면 그 이유를 말한다** (조용히 '없다' 로 뭉개지 않는다)
    miss = _query("SELECT reason FROM miss WHERE code = ?", (str(code),))
    if miss:
        return {"available": False, "code": code, "source": "report_index",
                "reason": f"원문 색인에 없다 — {miss[0]['reason']}"}
    return {"available": False, "code": code, "source": "report_index",
            "reason": "원문 색인에 없는 종목이다 (색인은 상장 종목 중 사업보고서가 있는 곳만 담는다)"}


def has(code: str) -> bool:
    return bool(_query("SELECT 1 FROM report WHERE code = ? LIMIT 1", (str(code),)))


def document(code: str) -> str:
    """그 종목의 임베딩 문장 — 검색 결과가 **왜 나왔는지** 보여 주는 데 쓴다."""
    rows = _query("SELECT doc FROM report WHERE code = ?", (str(code),))
    return rows[0]["doc"] if rows else ""


# ══════════════════════════════════════════════════════════
# 2. 벡터 검색
# ══════════════════════════════════════════════════════════
def _load_matrix() -> tuple:
    """(행렬, 코드들, 이름들). 처음 한 번만 만든다.

    ⚠️ **잠금 밖에서 읽는다** (요약본 지뢰 1 — 지연 색인 잠금 데드락).
    """
    global _matrix, _codes, _names
    if _matrix is not None:
        return _matrix, _codes, _names

    rows = _query("SELECT v.code, v.vec, r.name FROM vector v "
                  "JOIN report r ON r.code = v.code ORDER BY v.code")
    if not rows:
        with _lock:
            if _matrix is None:
                _matrix, _codes, _names = np.zeros((0, 0), dtype=np.float32), [], []
        return _matrix, _codes, _names

    codes = [r["code"] for r in rows]
    names = [r["name"] for r in rows]
    raw = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.int8)
    dim = len(rows[0]["vec"])
    # int8 → float32 단위벡터. 18MB 중 벡터는 2.5MB 이고 펼치면 10MB 다 — 한 번만 한다.
    matrix = raw.reshape(len(rows), dim).astype(np.float32) / QUANT_SCALE
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix /= np.where(norms == 0, 1.0, norms)

    with _lock:                       # 느린 읽기가 **끝난 뒤에** 잠깐만 쥔다
        if _matrix is None:
            _matrix, _codes, _names = matrix, codes, names
    return _matrix, _codes, _names


def nearest(vector: Sequence[float], limit: int = 10) -> List[Dict]:
    """질의 벡터에 가까운 종목 상위 N.

    코사인 유사도다 — 색인 벡터도 질의 벡터도 단위벡터로 맞춰 두므로 내적이 곧 코사인이다.
    """
    matrix, codes, names = _load_matrix()
    if matrix.size == 0 or not len(vector):
        return []
    query = np.asarray(vector, dtype=np.float32)
    if query.shape[0] != matrix.shape[1]:
        return []                    # 모델이 바뀌어 차원이 안 맞으면 **빈 결과**다 (억지로 자르지 않는다)
    norm = float(np.linalg.norm(query)) or 1.0
    scores = matrix @ (query / norm)
    top = np.argsort(-scores)[:max(1, int(limit))]
    return [{"code": codes[i], "name": names[i], "score": round(float(scores[i]), 4)}
            for i in top]


def warm_up() -> int:
    """서버가 뜰 때 행렬을 미리 만든다 (첫 검색이 느리지 않도록)."""
    matrix, _codes, _names = _load_matrix()
    return int(matrix.shape[0]) if matrix.size else 0


# ══════════════════════════════════════════════════════════
# 3. 자기설명
# ══════════════════════════════════════════════════════════
def stats() -> Dict:
    """번들이 무엇을 담고 있고 **무엇을 못 담았는지** 밝힌다."""
    if not available():
        return {"available": False,
                "reason": f"{INDEX_PATH.name} 이 없다",
                "notes": ["`python3 scripts/build_report_index.py` 로 만든다",
                          "없어도 리서치는 돈다 — 로컬은 DART 원문을 직접 받는다"]}

    counts = _query("SELECT (SELECT COUNT(*) FROM report) AS reports, "
                    "(SELECT COUNT(*) FROM vector) AS vectors, "
                    "(SELECT COUNT(*) FROM miss) AS misses")
    meta = {r["key"]: r["value"] for r in _query("SELECT key, value FROM meta")}
    row = counts[0] if counts else {"reports": 0, "vectors": 0, "misses": 0}

    reasons = {}
    for item in _query("SELECT reason, COUNT(*) AS n FROM miss GROUP BY reason"):
        reasons[str(item["reason"])[:70]] = item["n"]

    return {
        "available": True,
        "path": INDEX_PATH.name,
        "size_mb": round(INDEX_PATH.stat().st_size / 1e6, 2),
        "reports": row["reports"],
        "vectors": row["vectors"],
        "missing": row["misses"],
        "missing_reasons": reasons,
        "built_at": meta.get("facts_built_at", ""),
        "embed_model": meta.get("embed_model", ""),
        "embed_dim": int(meta.get("embed_dim") or 0),
        "embed_built_at": meta.get("embed_built_at", ""),
        "scope": meta.get("scope", ""),
        "notes": [
            "색인은 **빌드 시점에 고정**된다 — 사업보고서가 새로 나와도 다시 만들기 전까지 그대로다.",
            "사업보고서는 연 1회(3월 집중) 나온다. 갱신은 수동이다.",
            f"담지 못한 {row['misses']}곳은 우선주(DART 고유번호 없음)이거나 "
            "최근 15개월 안에 사업보고서를 내지 않은 곳이다.",
            "회사마다 서식이 달라 항목이 다 나오지 않는다 — 없는 항목은 `found=False` 로 남는다.",
        ],
    }
