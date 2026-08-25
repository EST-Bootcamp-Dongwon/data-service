"""DART 공시·정기보고서 → 자료 보관함(`clip`) 자동 수집 — **정본** (ADR-DS-0020)

수집 여섯 갈래(ADR-DS-0012) 중 **첫 갈래**다. 그 앞까지는 사람이 손으로 담는 길만
열려 있었다(ADR-DS-0019) — 이 모듈이 원천에서 받아 담는 첫 자리다.

부르는 곳은 둘이고 **같은 함수**다.

    POST /api/collect/dart/security      ① 종목 하나 · `/stock` 화면 버튼 · 동기 · 3호출 ≈ 1초
    python3 scripts/collect_dart.py      ② 유니버스 일괄 · 셸 · 350종목 ≈ 3분 반

## 왜 갱신 사슬에 얹지 않았나

`refresh_job` 800줄 중 사슬 표는 52줄이고 나머지는 **자식 프로세스 실행기**다. DART 수집에는
자식 프로세스가 필요 없다 — 종목 하나는 3호출이라 인터프리터를 새로 띄우는 값이 순수
낭비이고, 일괄은 셸에서 돌리면 그만이다. 그리고 사슬은 `CHAIN_ENV` 로 **쓰기 측(SQLite)**
에 못 박혀 있는데(ADR-DS-0018) `clip` 은 Postgres 전용이라 축이 다르다.

여섯 번째 칸으로 넣었다면 조용히 거짓이 되는 자리가 여럿이다 — `--skip-pg` 가 수집까지
삼키고, `snapshot()` 이 건너뛴 단계를 완료로 세어 **공시 0건에 진행률 100%** 가 되며,
끄는 손잡이가 `REFRESH_API=off`(사슬 전체) 하나뿐이 된다. 기각 근거는 ADR-DS-0020 §1.

## 이 모듈이 하지 않는 것

- **스레드를 만들지 않고 전역을 만지지 않는다.** 라우터(동기)와 CLI 가 같은 함수를 부른다.
- **`app.core.db` 를 직접 import 하지 않는다.** 커넥션·트랜잭션·`unreachable()` 처방은
  저장소 계층에 한 벌만 있어야 한다. 지금 `db` 를 부르는 곳은 셋뿐이고 늘리지 않는다.
- **본문을 담지 않는다.** 링크·제목·출처·발행일까지다 (ADR-DS-0008). 공시 원문은
  공공데이터라 예외지만, 그것은 **받아 오는** 것이지 이 표에 넣는 것이 아니다.
- **커밋하지 않는다.** `clip` 은 DB 에 있고 git 에 올라가지 않는다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.clients import dart_data
from app.core import settings
from app.core.paths import DATA_DIR
from app.repositories import clip_store, watermark_store

KST = timezone(timedelta(hours=9))

# ==================================================
# 1. 상수 — 사실과 정책을 갈라 둔다
# ==================================================
# `dart_data.DAILY_CALL_LIMIT`(20,000)은 **DART 가 정한 사실**이고, 아래는 **우리 정책**이다.
# 한 파일에 두면 "한도가 올랐다" 와 "예산을 늘렸다" 가 같은 diff 로 보인다.

# 일괄 수집이 하루에 쓸 몫. 나머지 8,000 은 리서치 화면(CORP-TP)·재무조회·재시도 몫이고,
# 동시에 **계수의 부정확함을 덮는 마진**이다 — 아래 `calls_left()` 주석 참조.
BATCH_CALL_BUDGET = 12_000
# 화면 버튼(①)이 언제나 눌리도록 남겨 두는 몫. 배치가 예산을 다 써도 사람은 한 종목을 본다.
ONDEMAND_RESERVE = 2_000

TOP_N_DEFAULT = 350            # KOSPI200+KOSDAQ150 근사 — 아래 `universe()` 의 경고를 볼 것
REVISIT_HOURS = 20             # ⭐ 호출을 실제로 줄이는 **유일한** 레버 (§증분)
MAX_WINDOW_SPLITS = 3          # 잘림 회수 상한. 없으면 예상 호출 산술이 거짓이 된다
MAX_CONSEC_FAIL = 5            # 네트워크 실패가 이만큼 이어지면 계통 오류로 본다
MAX_CONSEC_BADREQ = 3          # `100`(요청 값 오류)이 이어지면 우리 인자가 틀린 것이다
RETRY_BACKOFF = (1.0, 4.0)     # 같은 종목 재시도 간격(초). 두 번까지만
CORP_CODE_STALE_DAYS = 7       # 고유번호 매핑이 이보다 낡으면 **막지 않고** 알린다

# 담은 것이 무엇이 담았는지 남긴다. 컬럼을 늘릴 수 없으므로 payload 에 둔다 —
# 나중에 "사람이 담은 것" 과 "수집기가 담은 것" 을 가르는 **유일한 근거**다.
COLLECTOR_TAG = "dart_collector/1"

# 이 코드가 오면 종목을 바꿔도 낫지 않는다. 계속 돌면 남은 한도만 태운다.
FATAL_DART_STATUS = frozenset({"010", "011", "012", "020", "800"})

SCOPES: Tuple[str, ...] = ("one", "top")

# 담는 자리. ⚠️ **①공시와 ②정기보고서가 이 값을 공유한다.**
# 유니크가 `(kind, url_key)` 뿐이라 `screen` 을 포함하지 않는다(`sql/init/03-clip.sql`).
# 값이 갈리면 나중에 온 쪽이 `created=false` 로 흡수되고 **먼저 쓴 screen 만 남아**
# 화면 목록이 조용히 반쪽이 된다.
SCREEN = "stock"
KIND = "filing"
SOURCE = "DART"

STOCK_MASTER_FILE = DATA_DIR / "stock_master.json"
CORP_CODE_FILE = DATA_DIR / "corp_code.json"

# 정기보고서 제목. ⚠️ **`startswith("사업보고서 (")` 로는 안 된다** — `[기재정정]` 같은
#   대괄호 접두가 붙은 정정본을 통째로 놓친다. 그리고 꼬리의 `(` 를 요구하는 것이
#   `해외증권거래소등에신고한사업보고서등의국내신고`(실재하는 제목)를 걸러 낸다.
PERIODIC_RE = re.compile(r"^(?:\[[^\]]*\]\s*)*(?:사업|반기|분기)보고서\s*\(")

# 공시일 형식. `_format_date` 는 8자리 숫자가 아니면 **원문을 그대로 흘리므로** 여기서 본다.
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ==================================================
# 2. 순수 함수 — DB·네트워크 없이 검사된다
# ==================================================
def filing_kind(row: Dict[str, Any]) -> str:
    """①공시(`event`)인가 ②정기보고서(`periodic`)인가. **두 조건을 동시에 본다.**

    `kind` 는 DDL 이 둘을 `filing` 한 값으로 못 박았으므로(`clip_kind_ck`) 구분은
    `payload` 로 한다.

    ⚠️ **`category=='실적'` 을 구분자로 쓸 수 없다.** 그 축은 「무슨 사건인가」이고
    ①/②는 「무슨 서류인가」라 **직교한다** — `실적` 에는 사업보고서(②)와
    `연결재무제표기준영업(잠정)실적`(①)이 함께 들어온다.
    ⚠️ **`public_type=='A'` 단독으로도 안 된다.** 정기공시에는 정기보고서가 아닌 것도 있다.
    """
    if row.get("public_type") != "A":
        return "event"
    return "periodic" if PERIODIC_RE.match((row.get("report_name") or "").strip()) else "event"


def to_clip(row: Dict[str, Any], *, code: str) -> Optional[Dict[str, Any]]:
    """DART 공시 한 줄 → `clip_store.create_many()` 가 받는 모양. 담을 수 없으면 `None`.

    ⚠️ **`url` 을 새로 조립하지 않는다.** 정본은 `dart_data` 한 곳이다. 두 벌이 되면
    `url_key` 가 갈려 멱등성이 통째로 무너지는데, `payload.rcept_no` 에는 유니크 제약이
    **없어 DB 가 막아 주지 않는다** — 같은 공시가 몇 번이고 새 행이 된다.

    ⚠️ **`code` 는 요청한 종목코드이지 `row["stock_code"]` 가 아니다.** DART 가 돌려주는
    것은 그 법인의 **보통주** 코드라, 우선주로 물으면 딴 종목에 붙는다.

    ⚠️ **`industry_code` 를 주지 않는다.** 주는 순간 `industry_source='manual'` 이 되어
    `clip_store.reassign_industries()` 가 영영 못 고친다 — 그 컬럼이 있는 이유가
    "사람이 고친 값을 배치가 덮지 않는다" 인데, 수집기가 사람 행세를 하게 된다.

    ⚠️ **`note` 를 채우지 않는다.** 사람이 붙이는 자리이고, 목록 검색(`q`)이 제목과
    메모를 함께 보므로 자동 문구가 들어가면 사람 메모가 그 안에 묻힌다.
    """
    rcept_no = (row.get("rcept_no") or "").strip()
    url = (row.get("url") or "").strip()
    title = (row.get("report_name") or "").strip()
    if not rcept_no or not url or not title:
        return None
    if clip_store.banned_host(url):
        # API 주소에는 인증키가 질의로 붙는다. 저장소도 막지만 여기서 먼저 버린다.
        return None

    # ⚠️ **모양만 보고 `fromisoformat` 을 부르면 안 된다.** `2026-02-30` 처럼 8자리이되
    #    달력에 없는 날짜가 오면 `ValueError` 가 나는데, 이 함수에는 그것을 잡는 자리가 없어
    #    **한 줄 때문에 배치 전체가 죽는다.** 모양(`ISO_DATE_RE`)과 유효성은 다른 축이다.
    raw_date = (row.get("date") or "").strip()
    occurred = None
    if ISO_DATE_RE.match(raw_date):
        try:
            occurred = date.fromisoformat(raw_date)
        except ValueError:
            occurred = None          # 날짜만 비운다 — 접수번호와 링크는 살아 있다

    which = filing_kind(row)
    tags = [t for t in ((row.get("category") or "").strip(),
                        "정기보고서" if which == "periodic" else "") if t]

    return {
        "kind": KIND,
        "screen": SCREEN,
        "title": title[:500],
        "url": url,
        "source": SOURCE,
        "occurred_at": occurred,
        "code": code,
        # `industry_code` 를 넣지 않는다 (위 참조)
        "note": None,
        "tags": tags,
        "payload": {
            "rcept_no": rcept_no,
            # ⚠️ 열쇠 이름은 **`report_nm`** 이다. 클라이언트는 `report_name` 이지만
            #    표 쪽 계약(`sql/init/03-clip.sql` 의 COMMENT)이 `report_nm` 이라 그쪽이 이긴다.
            "report_nm": title,
            "filing_kind": which,
            "public_type": row.get("public_type") or "",
            "public_type_name": row.get("public_type_name") or "",
            "category": row.get("category") or "",
            "filer": row.get("filer") or "",
            "remark": row.get("remark") or "",
            "corp_name": row.get("corp_name") or "",
            "collected_by": COLLECTOR_TAG,
        },
    }


def split_window(bgn: str, end: str) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    """`YYYYMMDD` 구간을 반으로 쪼갠다. 하루짜리면 그대로 둘을 돌려준다."""
    start = datetime.strptime(bgn, "%Y%m%d").date()
    finish = datetime.strptime(end, "%Y%m%d").date()
    if finish <= start:
        return (bgn, end), (bgn, end)
    middle = start + (finish - start) / 2
    return ((start.strftime("%Y%m%d"), middle.strftime("%Y%m%d")),
            ((middle + timedelta(days=1)).strftime("%Y%m%d"), finish.strftime("%Y%m%d")))


def truncated_types(payload: Dict[str, Any]) -> Tuple[str, ...]:
    """이번 응답에서 **자료를 잃은** 유형들. 창을 쪼개 다시 물어야 하는 것들이다.

    잘림은 **두 갈래**이고 수집기에게는 **둘 다 영구 손실**이다.

    ① **DART 쪽이 이미 잘라 보냈다** — 그 유형의 `total_count` 가 받은 줄 수보다 크다.
    ② **우리가 유형 셋을 합친 뒤 `limit` 로 잘랐다** — `dart_data` 가 최신순으로 정렬한
       뒤 앞에서 `limit` 개만 남긴다. 유형별로는 아무도 안 잘렸는데 **합계가 상한을 넘을 때**
       일어난다.

    ⚠️⚠️ **처음에는 ①만 봤고, 그것이 실제로 자료를 잃었다.** 실측(2026-08-25) — 상위 350종목
    첫 회차에서 **정확히 100건에 멈춘 종목이 9개**였고 전부 `status='ok'` 로 기록됐다.
    고려아연은 12개월 창을 물었는데 가장 오래된 공시가 2025-12-15 다(창 시작은 2025-08-18) —
    **4개월치가 오류 없이 사라졌고** watermark 는 전진했다.

    ⚠️ 최상위 `truncated` 로도 판정할 수 없다. 그것은 ①과 ②를 `or` 로 뭉쳐서, 어느 유형을
    다시 물어야 하는지를 말해 주지 못한다. 창 분할은 **유형을 골라** 다시 묻는다.

    ⚠️ **`limit` 을 몰라도 된다.** 판정 재료가 응답 안에 다 있다 — 받은 줄 수(`count`)와
    합친 뒤 살아남은 줄 수를 유형별로 비교하면 ②가 그대로 드러난다.
    """
    survived: Dict[str, int] = {}
    for row in payload.get("rows") or []:
        key = row.get("public_type") or ""
        survived[key] = survived.get(key, 0) + 1

    cut: List[str] = []
    for entry in payload.get("by_type") or []:
        code = entry.get("code", "")
        got = int(entry.get("count") or 0)
        if entry.get("truncated") or survived.get(code, 0) < got:
            cut.append(code)
    return tuple(cut)


def universe(top: int = TOP_N_DEFAULT) -> Tuple[List[str], str]:
    """일괄 수집 대상 종목코드와 **그 목록이 무엇인지 밝히는 한 문장**.

    ⚠️⚠️ **`core` 유니버스가 아니다.** `securities.universe_tier` 는 CHECK 가 서 있지만
    실측 전부 `full` 이고, KOSPI200·KOSDAQ150 구성종목 목록이 이 레포에 없다
    (ADR-DS-0014 — 추정하지 않는다). 그래서 **시가총액 순위 상위 N** 을 대용으로 쓰고,
    그 사실을 로그·watermark·화면 **셋 다**에 싣는다. 낱말을 `core` 로 쓰면 다음 사람이
    구성종목이라고 믿는다.

    ⚠️ **`krx_store.universe()` 를 쓰지 않는다.** 그것은 `STORE_BACKEND` 스위치를 타므로
    저장소 상태에 따라 다른 목록을 낸다 — 같은 명령이 날마다 다른 대상을 도는 셈이다.
    `stock_master.json` 은 저장소와 무관한 파일이라 그 축이 아예 없다.

    ⚠️ **돌려주는 순서는 종목코드 오름차순**이다. 시총 순은 매일 바뀌어서
    `--resume` 재개 지점이 그날그날 다른 곳을 가리키게 된다.
    """
    master = json.loads(STOCK_MASTER_FILE.read_text(encoding="utf-8"))
    ranked = sorted(master.items(), key=lambda kv: kv[1][2] if len(kv[1]) > 2 else 10**9)
    picked = [code for code, _ in ranked[:max(1, int(top))]]
    note = (f"시총 상위 {len(picked)} (data/stock_master.json · "
            "universe_tier 가 전부 full 이라 대용이다 — core 구성종목이 아니다)")
    return sorted(picked), note


def corp_code_state() -> Dict[str, Any]:
    """고유번호 매핑 파일의 나이. **막지 않고 알린다.**

    낡으면 신형 종목코드로 새로 상장한 회사가 `resolve_corp` 404 로 떨어지는데,
    그 실패는 "DART 가 이상한가" 로 보인다. 원인은 파일이다.
    """
    try:
        payload = json.loads(CORP_CODE_FILE.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001  (없어도 수집만 못 하고 앱은 뜬다)
        return {"generated_at": None, "age_days": None, "listed_count": 0,
                "stale": True, "hint": f"{CORP_CODE_FILE.name} 을 읽지 못했다: {error}"}

    text_at = payload.get("generated_at") or ""
    age = None
    try:
        made = datetime.strptime(text_at.replace(" KST", ""), "%Y-%m-%d %H:%M:%S")
        age = (datetime.now(KST).replace(tzinfo=None) - made).days
    except ValueError:
        pass
    stale = age is None or age > CORP_CODE_STALE_DAYS
    on_file = len(payload.get("map") or {})
    # ⚠️ **파일과 메모리를 함께 낸다.** `_load_corp_map()` 은 프로세스 수명 동안 한 번만
    #    읽으므로, 파일을 새로 만들어도 떠 있는 서버는 낡은 지도를 쓴다. 파일만 보고
    #    "최신" 이라고 말하면 **조회는 404 인데 화면은 정상**이라고 하는 어긋남이 생긴다.
    in_memory = dart_data.loaded_corp_count()
    reload_needed = in_memory and in_memory != on_file
    hints = []
    if stale:
        hints.append("고유번호 매핑이 낡았다 — 새로 상장한 회사가 404 로 떨어진다. "
                     "python3 scripts/build_corp_code.py")
    if reload_needed:
        hints.append(f"이 프로세스는 옛 매핑 {in_memory:,}개를 들고 있다(파일은 {on_file:,}개) — "
                     "서버를 다시 띄우면 반영된다")
    return {
        "generated_at": text_at or None,
        "age_days": age,
        "listed_count": on_file,
        "loaded_count": in_memory,
        "stale": bool(stale or reload_needed),
        "hint": " / ".join(hints),
    }


# ==================================================
# 3. 능력 — 막는 것은 환경이 아니라 능력이다
# ==================================================
def capability() -> Dict[str, Any]:
    """지금 수집할 수 있는지와, 못 하면 **무엇을 해야 하는지**.

    ⚠️ **`APP_ENV` 를 보지 않는다** (ADR-DS-0017·0019 와 같은 원칙). 배포본이라서 막는
    것이 아니라 **키가 없거나 표에 못 붙어서** 막는다 — 그래야 Supabase 를 붙인 날(S6)
    이 함수를 고치지 않아도 그대로 살아난다.

    ⚠️ **세 사유를 뭉치지 않는다.** 처방이 전혀 다르다 —
    끈 것은 환경변수를 되돌리고, 키 없음은 `.key` 에 한 줄을 넣고, 표 없음은 S6 을 기다린다.
    """
    if not settings.collect_api_enabled():
        return _blocked("수집 API 가 꺼져 있다",
                        (f"되돌리려면 환경변수 한 줄이다: COLLECT_API={settings.ON}",))

    key, source = dart_data.load_dart_key()
    if not key:
        return _blocked("DART 인증키가 없다", (
            "프로젝트 루트 `.key` 에 `DART_API_KEY = 발급받은_키` 한 줄을 넣는다.",
            "무료 발급: https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do",
        ))

    clip_state = clip_store.availability()
    if not clip_state["available"]:
        # 보관함이 만든 처방을 **그대로 통과시킨다.** 다시 쓰면 두 벌이 되고,
        # `WHEN_IT_WORKS` 가 이미 "언제 되는지" 를 말하고 있다.
        return _blocked(f"자료 보관함을 쓸 수 없다 — {clip_state['reason']}",
                        tuple(clip_state["hints"]), key_source=source, clip=clip_state)

    return {"available": True, "reason": "", "hints": [],
            "key": {"present": True, "source": source}, "clip": clip_state}


def _blocked(reason: str, hints: Sequence[str], *, key_source: str = "",
             clip: Optional[Dict] = None) -> Dict[str, Any]:
    return {"available": False, "reason": reason, "hints": list(hints),
            "key": {"present": bool(key_source), "source": key_source},
            "clip": clip or {"available": False, "reason": "확인하지 않았다", "hints": []}}


def calls_left(*, reserve: int = 0) -> Dict[str, Any]:
    """오늘 남은 호출 예산.

    ⚠️⚠️ **이것은 「이 수집기가 쓴 양」이지 DART 잔량이 아니다.** 리서치 화면·대시보드가
    같은 키로 부르는 호출은 여기 안 세어진다. 정확한 예약 표를 만들려면 컬럼이 필요한데
    **이 레포에는 마이그레이션 러너가 없다** — 그래서 정확한 계수 대신 **마진**을 산다
    (일 한도 20,000 중 12,000 만 쓴다). 넘치면 DART 가 `020` 을 주고 우리는 멈춘다.
    화면 라벨도 「이 수집기가 쓴 양」이어야 거짓말이 안 된다.
    """
    day = watermark_store.today_kst()
    used = watermark_store.calls_today(day)
    budget = max(0, BATCH_CALL_BUDGET - reserve)
    return {
        "day": day,
        "daily_limit": dart_data.DAILY_CALL_LIMIT,
        "batch_budget": BATCH_CALL_BUDGET,
        "ondemand_reserve": ONDEMAND_RESERVE,
        "used_by_collector": used,
        "left": max(0, budget - used),
        "retry_after": watermark_store.seconds_until_midnight_kst(),
    }


# ==================================================
# 4. 계획과 결말
# ==================================================
@dataclass(frozen=True)
class Plan:
    """한 회차가 무엇을 할지. **범위는 `scope` 하나로 정해진다.**"""

    scope: str = "top"
    codes: Tuple[str, ...] = ()          # scope="one" 이면 길이 1
    top: int = TOP_N_DEFAULT
    months: int = 12                     # watermark 가 없는 종목의 **최초** 창
    types: Tuple[str, ...] = dart_data.DEFAULT_PUBLIC_TYPES
    limit: int = 100
    incremental: bool = True
    budget: int = 0                      # 0 = 남은 예산 자동
    resume_from: str = ""
    dry_run: bool = False


@dataclass
class Outcome:
    """종목 하나의 결말."""

    code: str
    name: str = ""
    corp_code: str = ""
    state: str = "ok"                    # ok · empty · skipped · failed · aborted
    reason: str = ""
    # ⚠️ DART 본문 코드를 **값으로** 실어 나른다. 라우터가 `reason` 문자열을 파싱하게 두면
    #    한글 문구를 고치는 날 HTTP 코드가 조용히 바뀐다.
    dart_status: str = ""
    calls: int = 0
    fetched: int = 0
    created: int = 0
    duplicate: int = 0
    invalid: int = 0
    latest: str = ""                     # 이번에 본 최신 rcept_dt (YYYYMMDD)
    truncated: bool = False
    problems: List[str] = field(default_factory=list)


# ==================================================
# 5. 수집
# ==================================================
def collect(plan: Plan, *, on_progress: Optional[Callable[[str], None]] = None,
            should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """공시를 받아 `clip` 에 담는다. 라우터(①)와 CLI(②)가 **같은 이 함수**를 부른다.

    ⚠️ **한 종목의 실패가 배치를 죽이지 않는다.** 다만 계통 오류(키·한도·점검·저장소)는
    죽인다 — 종목을 바꿔도 낫지 않고 남은 한도만 태우기 때문이다.

    ⚠️ **쓰기 순서는 clip 먼저, watermark 나중이다.** 반대로 하면 watermark 만 전진하고
    공시는 **영영 안 담긴다.** 이 순서면 최악이 "다음 회차가 같은 구간을 다시 받는다" 인데
    그때는 `url_key` 유니크가 흡수하므로 손해가 호출 몇 번뿐이다.
    """
    started = time.monotonic()
    say = on_progress or (lambda _line: None)

    state = capability()
    if not state["available"]:
        return _refused(plan, state, started)

    codes, scope_note = _targets(plan)
    corp_state = corp_code_state()
    if corp_state["stale"] and corp_state["hint"]:
        say(f"⚠️ {corp_state['hint']}")

    budget = calls_left(reserve=0 if plan.scope == "one" else ONDEMAND_RESERVE)
    # ⚠️ **이것은 하한이다.** 잘린 유형이 있으면 창을 쪼개 다시 물으므로 실제 호출이
    #    이보다 많아진다(유형 셋이 3단계까지 갈리면 최대 45회). 상한을 계산해 두면 대부분의
    #    회차에서 과하게 거절하게 되므로, **하한으로 시작하고 도중에 예산을 다시 본다** —
    #    `spent + len(types) > limit_calls` 검사가 매 종목마다 실제 소비를 반영한다.
    planned = len(codes) * max(1, len(plan.types))
    if plan.dry_run:
        # 부르는 쪽이 요약으로 한 번 더 말하므로 여기서는 조용히 돌려준다.
        return _summary(plan, [], scope_note, corp_state, budget, started,
                        stopped="dry_run", planned=planned, codes_total=len(codes))
    if planned > budget["left"]:
        # ⚠️ 시작해 놓고 도중에 멈추면 절반만 담긴다. **시작 전에 거절한다.**
        return _summary(plan, [], scope_note, corp_state, budget, started,
                        stopped="budget", planned=planned, codes_total=len(codes),
                        reason=(f"오늘 남은 예산 {budget['left']} 보다 예상 호출 {planned} 이 많다. "
                                f"--budget 으로 회차 상한을 낮추거나 --top 을 줄인다."))

    marks = watermark_store.get_prefix("dart_filing:") if plan.incremental else {}
    outcomes: List[Outcome] = []
    spent = 0
    consec_fail = consec_badreq = 0
    stopped = ""
    # ⚠️ **재개 지점은 한 값이어야 한다.** 진행 로그와 최종 요약이 다른 값을 말하면
    #    사람이 어느 쪽을 믿을지 모른다. 여기 담기는 것은 **아직 처리 안 한** 종목이다 —
    #    이미 끝낸 종목을 가리키면 그 하나를 매번 다시 받는다(멱등이라 해롭진 않지만
    #    호출을 태우고, 무엇보다 두 값이 갈리는 것 자체가 고장의 씨앗이다).
    resume_at = ""
    limit_calls = plan.budget if plan.budget > 0 else budget["left"]

    say(f"── DART 공시 수집 — {scope_note} · 대상 {len(codes)}종목 · "
        f"예상 호출 {planned} · 오늘 남은 예산 {budget['left']} ──")

    for index, code in enumerate(codes):
        if plan.resume_from and code < plan.resume_from:
            continue
        if should_stop and should_stop():
            stopped, resume_at = "cancelled", code
            break
        if spent + len(plan.types) > limit_calls:
            stopped, resume_at = "budget", code
            say(f"예산 소진 — {spent} 호출을 쓰고 {code} 앞에서 멈춘다. "
                f"이어서 하려면: --resume-from {code}")
            break

        outcome = _one(code, plan, marks, say)
        outcomes.append(outcome)
        spent += outcome.calls

        if outcome.calls:
            # 청크마다가 아니라 **종목마다** 센다. 프로세스가 죽어도 그때까지가 남는다.
            watermark_store.bump_calls(budget["day"], outcome.calls)

        if outcome.state == "aborted":
            stopped, resume_at = outcome.reason or "aborted", code
            say(f"■ 계통 오류로 중단한다 — {outcome.reason}")
            break
        if outcome.state == "failed":
            consec_fail += 1
            consec_badreq += 1 if outcome.reason.startswith("100") else 0
            if consec_fail >= MAX_CONSEC_FAIL:
                stopped, resume_at = "consecutive_failures", code
                say(f"■ {MAX_CONSEC_FAIL}종목 연속 실패 — 계통 오류로 보고 중단한다")
                break
            if consec_badreq >= MAX_CONSEC_BADREQ:
                stopped, resume_at = "bad_request", code
                say(f"■ 요청 값 오류가 {MAX_CONSEC_BADREQ}회 이어졌다 — 우리 인자가 틀렸다")
                break
        else:
            consec_fail = consec_badreq = 0

        if (index + 1) % 25 == 0:
            done = _tally(outcomes)
            say(f"[{index + 1}/{len(codes)}] 새로 {done['created']} · 이미 {done['duplicate']} · "
                f"건너뜀 {done['skipped']} · 호출 {spent}/{limit_calls}")

    result = _summary(plan, outcomes, scope_note, corp_state,
                      calls_left(reserve=0 if plan.scope == "one" else ONDEMAND_RESERVE),
                      started, stopped=stopped, planned=planned, codes_total=len(codes),
                      resume_at=resume_at)
    # 회차 단위 진행 지점. **종목별 줄과 다른 축이다** — 이쪽은 "마지막으로 언제 무엇을
    # 돌렸나" 에 답하고, 화면 `/status` 의 `last_run` 이 그것을 읽는다.
    # ⚠️ 종목이 하나뿐인 온디맨드(①)는 남기지 않는다 — 버튼을 한 번 누른 것이
    #    "마지막 일괄 회차" 를 덮으면 배치가 언제 돌았는지를 잃는다.
    # ⚠️ **한 종목도 안 돈 회차는 기록하지 않는다.** `--resume-from` 이 목록에 없는 코드면
    #    전부 건너뛰어지는데, 그것을 `rows=0 · ok` 로 덮으면 **직전 실주행 기록이 지워진다** —
    #    화면의 "마지막 회차" 가 아무 일도 안 한 회차를 가리키게 된다.
    if plan.scope != "one" and result["codes_done"] and any(
            o.state not in ("skipped",) for o in outcomes):
        watermark_store.upsert(
            "dart_filing",
            last_key=f"{plan.scope}:{len(codes)}",
            rows=result["created"],
            status="partial" if stopped else "ok",
            note=f"{scope_note} · 호출 {result['calls']}" + (f" · 중단 {stopped}" if stopped else ""),
        )
    return result


def _targets(plan: Plan) -> Tuple[List[str], str]:
    if plan.scope == "one":
        return list(plan.codes), f"종목 {', '.join(plan.codes)}"
    if plan.scope != "top":
        raise ValueError(f"scope 는 {' · '.join(SCOPES)} 중 하나다. 받은 값: {plan.scope!r}")
    return universe(plan.top)


def _one(code: str, plan: Plan, marks: Dict[str, Dict], say: Callable[[str], None]) -> Outcome:
    """종목 하나를 받아 담는다. **예외를 밖으로 내지 않는다** — 결말로 바꿔 돌려준다."""
    outcome = Outcome(code=code)

    corp_code = dart_data.get_corp_code(code)
    if not corp_code:
        # 우선주는 원래 고유번호가 없다(법인이 같다). 신규 상장이면 매핑 파일이 낡은 것이다.
        outcome.state = "skipped"
        outcome.reason = "DART 고유번호가 없다"
        _mark(code, corp_code="", status="error", note=outcome.reason)
        return outcome
    outcome.corp_code = corp_code
    outcome.name = dart_data.get_corp_name(code)

    mark = marks.get(f"dart_filing:{corp_code}")
    if plan.incremental and mark and _seen_recently(mark):
        outcome.state = "skipped"
        outcome.reason = f"{REVISIT_HOURS}시간 안에 이미 봤다"
        return outcome

    # ⚠️ **`bgn_de` 에 하루를 더하지 않는다.** 더하면 그날 늦게 접수된 공시를 영구히 잃는다
    #    (구간이 일 단위라서). 그대로 두면 그날 것이 다시 오지만 `url_key` 가 흡수한다.
    bgn = (mark or {}).get("last_key") or "" if plan.incremental else ""
    end = datetime.now(KST).strftime("%Y%m%d")

    try:
        rows, calls, still_cut = _fetch_window(
            code, bgn, end, tuple(plan.types), plan.limit, plan.months, depth=0)
    except dart_data.DartError as error:
        # ⚠️ **이미 태운 호출을 잃지 않는다.** 유형 셋 중 둘이 나가고 셋째에서 죽으면 그 둘도
        #    한도를 썼다. 0 으로 두면 예산 계수가 **실제보다 적게** 세어지는데, 그 방향은
        #    한도를 넘기는 쪽이라 안전하지 않다. `error.spent` 는 `_fetch_window` 가 붙인다.
        outcome.calls = getattr(error, "spent", 0)
        return _from_dart_error(outcome, error)

    outcome.calls = calls
    outcome.fetched = len(rows)
    outcome.truncated = bool(still_cut)

    if not rows:
        # ⚠️ **"받아 봤더니 없었다" 를 기록한다.** 안 남기면 매 회차 다시 묻는다.
        outcome.state = "empty"
        _mark(code, corp_code, status="ok", rows=0, last_key=bgn or None,
              note="받아 봤더니 공시가 없었다")
        return outcome

    prepared = [c for c in (to_clip(r, code=code) for r in rows) if c]
    outcome.invalid = len(rows) - len(prepared)
    try:
        result = clip_store.create_many(prepared)
    except Exception as error:  # noqa: BLE001
        # 저장소에 못 붙었다 — **되돌아갈 곳이 없다.** 다음 종목도 마찬가지이므로 멈춘다.
        #
        # ⚠️ **`RuntimeError` 만 잡으면 안 된다.** `db.unreachable()` 이 그 예외를 만드는 것은
        #    `OSError` 일 때뿐인데, asyncpg 의 `AdminShutdownError`·`TooManyConnectionsError`
        #    같은 것들은 `OSError` 계보가 아니라 그 처방을 안 거친다 — 그러면 여기를 그대로
        #    뚫고 나가 배치가 트레이스백으로 죽고 CLI 종료코드 계약도 깨진다.
        outcome.state = "aborted"
        outcome.reason = f"보관함에 못 붙었다: {str(error).splitlines()[0]}"
        return outcome

    outcome.created = result["created"]
    outcome.duplicate = result["duplicate"]
    outcome.invalid += result["invalid"]
    outcome.problems = list(result["problems"])
    outcome.latest = max((r["rcept_no"][:8] for r in rows if r.get("rcept_no")), default="")
    # ⚠️ 창을 좁힐 때 쓰는 것은 **`rcept_dt`(=`date`)** 이지 `rcept_no` 접두가 아니다.
    #    실측으로 둘이 하루 어긋나는 줄이 있고, 접두를 쓰면 그것들이 조용히 버려진다.
    latest_dt = max((r["date"].replace("-", "") for r in rows if r.get("date")), default="")
    outcome.latest = latest_dt or outcome.latest

    _mark(code, corp_code, status="partial" if still_cut else "ok",
          rows=len(rows), last_key=outcome.latest or None,
          note=("100건 상한에 걸린 유형이 남았다 — 구간을 나눠 다시 받아야 한다"
                if still_cut else ""))
    if outcome.created and say:
        say(f"  {code} {outcome.name} — 새로 {outcome.created} · 이미 {outcome.duplicate}")
    return outcome


def _fetch_window(code: str, bgn: str, end: str, types: Tuple[str, ...], limit: int,
                  months: int, *, depth: int) -> Tuple[List[Dict], int, Tuple[str, ...]]:
    """구간 하나를 받는다. 잘린 유형이 있으면 **창을 반으로 쪼개** 다시 받는다.

    ⚠️ **`MAX_WINDOW_SPLITS` 상한이 있어야 `--dry-run` 의 예상 호출이 계속 참이다.**
    상한을 없애면 종목마다 호출 수가 달라져 예산 산술이 거짓이 된다. 상한을 넘게 몰린
    종목은 회수하지 못하고 `partial` 로 **사람에게 보인다** — 조용히 넘기지 않는다.

    ⚠️ **캐시를 타지 않는다**(`use_cache=False`). TTL 이 24시간이고 열쇠에 날짜가 없어,
    타면 그 날 두 번째 실행부터 네트워크를 안 타고 같은 답을 준다 — 오류가 안 뜬다.
    """
    try:
        payload = dart_data.fetch_disclosures(
            code, months=months, limit=limit, types=types,
            bgn_de=bgn, end_de=end, use_cache=False)
    except dart_data.DartError as error:
        # ⚠️ **이미 태운 호출을 잃지 않는다.** 유형 셋 중 둘이 나가고 셋째에서 죽으면 그 둘도
        #    한도를 썼다. 세지 않으면 예산이 **실제보다 적게** 집계되는데, 그 방향은 한도를
        #    넘기는 쪽이라 안전하지 않다. 몇 번째에서 죽었는지는 알 수 없으므로 **하한**으로
        #    1을 센다 — 마진이 그 오차를 덮는다.
        error.spent = getattr(error, "spent", 0) + 1
        raise
    rows = list(payload.get("rows") or [])
    calls = len(payload.get("by_type") or types)
    cut = truncated_types(payload)

    if not cut or depth >= MAX_WINDOW_SPLITS:
        return rows, calls, cut

    # 잘린 유형만 다시 묻는다. 안 잘린 유형까지 다시 부르면 호출이 배로 든다.
    left, right = split_window(payload_begin(payload, bgn), end)
    if left == right:
        # ⚠️ **더 못 좁힌다** — 구간이 하루이거나 역전됐다. `split_window` 는 그럴 때 부모 창을
        #    그대로 돌려주는데, 그것을 좁아진 창으로 믿고 재귀하면 **같은 창을 15번 더 받는다**
        #    (3단계 × 유형 3개 · 실측). 좁힐 여지가 0인데 호출만 15배 든다.
        return rows, calls, cut
    seen = {r.get("rcept_no") for r in rows}
    # ⚠️⚠️ **자식이 못 메운 잘림을 버리지 않는다.** 여기서 빈 튜플을 돌려주면 상한
    #    (`MAX_WINDOW_SPLITS`)에 걸려 못 받은 구간이 **`ok` 로 기록되고** watermark 가
    #    그대로 전진한다 — 그러면 그 구간은 영영 안 메워지는데 아무 데도 안 뜬다.
    #    실측으로 이 자리를 그렇게 짜 두었다가 잡았다(limit=5 · 38줄만 받고 `partial` 0건).
    left_over: List[str] = []
    for window in (left, right):
        try:
            more, more_calls, more_cut = _fetch_window(
                code, window[0], window[1], cut, limit, months, depth=depth + 1)
        except dart_data.DartError as error:
            # 자식이 죽어도 **이 층이 이미 쓴 호출**을 함께 실어 올린다.
            error.spent = getattr(error, "spent", 0) + calls
            raise
        calls += more_calls
        left_over += [t for t in more_cut if t not in left_over]
        for row in more:
            if row.get("rcept_no") not in seen:
                seen.add(row.get("rcept_no"))
                rows.append(row)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("rcept_no", "")), reverse=True)
    return rows, calls, tuple(left_over)


def payload_begin(payload: Dict[str, Any], fallback: str) -> str:
    """응답이 실제로 쓴 시작일(`YYYYMMDD`). `fetch_disclosures` 가 `months` 로 계산했을 수 있다."""
    begin = (payload.get("begin") or "").replace("-", "")
    return begin if len(begin) == 8 and begin.isdigit() else (fallback or begin)


def _seen_recently(mark: Dict[str, Any]) -> bool:
    """⭐ **호출 수를 실제로 줄이는 유일한 레버.**

    ⚠️ 창을 좁히는 것(증분)은 호출 **수**를 줄이지 않는다 — 종목당 유형 수만큼 그대로다.
    줄어드는 것은 돌려받는 줄 수와 100건 상한에 걸릴 확률뿐이다. "증분이니 한도 걱정
    없다" 는 틀린 결론이고, 그 오해가 예산을 태운다. 방문 자체를 건너뛰는 이쪽이 레버다.

    ⚠️ `status='error'` 인 줄은 건너뛰지 않는다 — 그것은 "볼 수 없었다" 라 다시 봐야 한다.
    """
    if mark.get("status") == "error":
        return False
    stamp = mark.get("last_synced_at")
    if not stamp:
        return False
    try:
        seen_at = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=KST)
    return datetime.now(timezone.utc) - seen_at < timedelta(hours=REVISIT_HOURS)


def _mark(code: str, corp_code: str, *, status: str, rows: Optional[int] = None,
          last_key: Optional[str] = None, note: str = "") -> None:
    """종목 하나의 진행 지점을 남긴다. **고유번호가 없으면 종목코드로 남긴다** —
    그래야 "볼 수 없었다" 가 어느 종목 얘기인지 알 수 있다."""
    watermark_store.upsert(f"dart_filing:{corp_code or code}",
                           last_key=last_key, rows=rows, status=status, note=note)


def _from_dart_error(outcome: Outcome, error: dart_data.DartError) -> Outcome:
    """DART 예외를 결말로 바꾼다. **중단할지 건너뛸지가 `dart_status` 로 갈린다.**

    ⚠️ HTTP 상태만 보면 `800`(점검 — 중단)과 `900`(정의되지 않은 오류 — 건너뜀)이
    둘 다 502 라 구별되지 않는다. 그래서 `DartError` 에 `dart_status` 를 실었다.
    """
    code = error.dart_status
    outcome.dart_status = code
    reason = f"{code or 'net'} {str(error).splitlines()[0]}"
    if code in FATAL_DART_STATUS:
        outcome.state = "aborted"
        outcome.reason = reason
        return outcome
    if error.status == 404:
        outcome.state = "skipped"
        outcome.reason = reason
        _mark(outcome.code, outcome.corp_code, status="error", note=reason)
        return outcome
    outcome.state = "failed"
    outcome.reason = reason
    _mark(outcome.code, outcome.corp_code, status="error", note=reason)
    return outcome


# ==================================================
# 6. 요약
# ==================================================
def _tally(outcomes: Sequence[Outcome]) -> Dict[str, int]:
    counts = {"created": 0, "duplicate": 0, "invalid": 0, "fetched": 0, "calls": 0,
              "ok": 0, "empty": 0, "skipped": 0, "failed": 0, "aborted": 0}
    for one in outcomes:
        counts["created"] += one.created
        counts["duplicate"] += one.duplicate
        counts["invalid"] += one.invalid
        counts["fetched"] += one.fetched
        counts["calls"] += one.calls
        counts[one.state] = counts.get(one.state, 0) + 1
    return counts


def _refused(plan: Plan, state: Dict[str, Any], started: float) -> Dict[str, Any]:
    return {"available": False, "reason": state["reason"], "hints": state["hints"],
            "scope": plan.scope, "codes_total": 0, "codes_done": 0,
            "stopped": "unavailable", "elapsed_ms": int((time.monotonic() - started) * 1000),
            **{k: 0 for k in ("created", "duplicate", "invalid", "fetched", "calls")},
            "outcomes": [], "problems": [], "truncated_codes": [], "resume_from": ""}


def _summary(plan: Plan, outcomes: Sequence[Outcome], scope_note: str,
             corp_state: Dict[str, Any], budget: Dict[str, Any], started: float, *,
             stopped: str = "", planned: int = 0, codes_total: int = 0,
             reason: str = "", resume_at: str = "") -> Dict[str, Any]:
    counts = _tally(outcomes)
    done = [o for o in outcomes if o.state not in ("aborted",)]
    return {
        "available": True,
        "reason": reason,
        "hints": [],
        "scope": plan.scope,
        "scope_note": scope_note,
        "codes_total": codes_total,
        "codes_done": len(done),
        "planned_calls": planned,
        "stopped": stopped,
        **counts,
        "budget": budget,
        "corp_code": corp_state,
        # 잘림이 남은 종목은 **보인다.** 조용히 넘기면 그 구간이 영영 안 메워진다.
        "truncated_codes": [o.code for o in outcomes if o.truncated],
        # 중단한 자리에서 **아직 처리하지 않은** 종목. 완주했으면 비어 있다.
        "resume_from": resume_at,
        "problems": [p for o in outcomes for p in o.problems][:20],
        "failures": [{"code": o.code, "name": o.name, "state": o.state,
                      "reason": o.reason, "dart_status": o.dart_status}
                     for o in outcomes if o.state in ("failed", "aborted", "skipped")][:50],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
