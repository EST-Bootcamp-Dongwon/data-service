"""자료 갱신 사슬 — **정본**이자 화면에서 돌리는 실행기 (ADR-DS-0016 · ADR-DS-0017)

    수집 → 축약본 → 스냅샷 → 마스터 → Postgres

이 다섯 단계를 **여기 한 번만** 적는다. 부르는 곳은 둘이다.

    invoke refresh                          셸에서 (tasks.py 가 이 모듈을 import 한다)
    POST /api/refresh/run                   화면 '자료 갱신' 버튼

⚠️ **두 벌로 두면 갈라진다.** 사슬은 순서가 뜻을 가지는데(2~4단계가 전부 `krx_cache.db` 를
읽으므로 수집이 먼저다), 그 순서를 두 곳에 적으면 한쪽만 고쳐진 채로 오래 간다.
실제로 이 사슬은 "명령이 다섯 개라서" 24거래일 동안 부분적으로만 돌았다 (ADR-DS-0016).

## 왜 화면에서도 돌리나

ADR-DS-0016 이 남긴 숙제가 **실행처**였다. CI 를 필수 경로에 두지 않는 것이 이 레포
방침이라(절대 제약 6번) 스케줄 자동화를 하지 않았고, 그래서 "누가 언제 돌리나" 가 비어
있었다. 사람이 실행처라면 **사람이 이미 보고 있는 화면**에 버튼을 두는 것이 가장 짧다 —
낡음을 알려 주는 카드와 그것을 고치는 버튼이 같은 자리에 있게 된다.

## 이 모듈이 하지 않는 것

- **커밋하지 않는다.** 산출물 셋이 git 에 올라가고 push 가 곧 Vercel 배포다. 배포 시점은
  사람이 정한다 (ADR-DS-0016 결정 1). 끝나면 커밋 절차를 로그에 적어 줄 뿐이다.
  `FORBIDDEN_IN_CHAIN` 이 그 약속을 검사 가능한 사실로 붙들어 둔다.
- **스케줄을 잡지 않는다.** 버튼은 실행처를 사람으로 **확정**한 것이지 자동화가 아니다.

## 상태를 어디에 두나

**이 프로세스의 메모리**다. 파일이나 DB 에 두지 않는다 — 갱신은 이 프로세스가 띄운
자식이 하는 일이라, 프로세스가 죽으면 자식도 함께 죽고 상태만 남아 봐야 거짓말이 된다.
⚠️ 그래서 **워커가 여럿이면 이 화면이 어긋난다.** 워커 A 가 돌리는 작업을 워커 B 는
모르므로 "실행 중이 아님" 이라고 답한다. 로컬 개발 서버는 단일 프로세스이고
`Dockerfile` 의 `uvicorn` 도 워커 인자가 없다(=1). 그 전제가 깨지면 여기부터 고친다.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional, Tuple

from app.core import settings
from app.core.paths import DATA_DIR, PROJECT_ROOT, SCRIPTS_DIR

KST = timezone(timedelta(hours=9))


# ==================================================
# 1. 사슬 — 이 표가 정본이다
# ==================================================
@dataclass(frozen=True)
class Step:
    """사슬 한 칸.

    `run_args` 의 `{days}` 는 실행 직전에 채운다. 문자열 그대로 두는 이유는
    **셸 명령(`invoke refresh`)과 argv(화면 버튼)가 같은 정의에서 나와야** 하기 때문이다.
    """

    key: str
    label: str
    script: str                        # 저장소 루트 기준 상대 경로
    run_args: Tuple[str, ...]          # 갱신할 때 붙일 인자
    check_args: Tuple[str, ...]        # 아무것도 바꾸지 않고 재기만 할 때
    outputs: Tuple[str, ...]           # 사람이 읽을 산출물 이름
    in_git: bool                       # 산출물이 git 에 올라가나 = 배포본에 닿나
    needs_db: bool = False             # Postgres 에 붙어야 도는 단계인가
    note: str = ""                     # 화면 표에 한 줄로 붙는 설명


# ⚠️ **순서가 뜻을 가진다.** 2~4 는 전부 `krx_cache.db` 를 읽으므로 수집이 먼저다.
#    그리고 1만 돌리면 로컬 화면만 최신이 되고 배포본은 그대로 낡는다 — 배포본이 읽는 것은
#    커밋되는 3·4 쪽이다. 이 어긋남은 오류로 뜨지 않고 **날짜로만** 드러난다.
STEPS: Tuple[Step, ...] = (
    Step(
        key="fetch",
        label="KRX 시세 수집",
        script="scripts/fetch_krx.py",
        run_args=("--days", "{days}"),
        check_args=("--status",),
        outputs=("data/krx_cache.db",),
        in_git=False,
        note="없는 거래일만 받는다. 휴장일(0건)도 기록해 다시 묻지 않는다",
    ),
    Step(
        key="bundle",
        label="배포용 축약본",
        script="scripts/build_krx_bundle.py",
        run_args=(),
        check_args=("--check",),
        outputs=("data/krx_bundle.db", "data/krx_derived.json"),
        in_git=True,                   # 둘 중 파생 JSON 만 git 에 올라간다
        note="번들 DB(30MB)는 git 에 안 올라간다 — 파생 JSON 만 배포본에 닿는다",
    ),
    Step(
        key="snapshot",
        label="시장 스냅샷",
        script="scripts/build_market_snapshot.py",
        run_args=(),
        check_args=("--check",),
        outputs=("data/market_snapshot.json.gz",),
        in_git=True,
        note="스크리닝용 사전계산. 배포본의 '몇 거래일 전' 배지가 이 파일을 본다",
    ),
    Step(
        key="master",
        label="종목 마스터",
        script="scripts/build_stock_master.py",
        run_args=(),
        check_args=("--check",),
        outputs=("data/stock_master.json",),
        in_git=True,
        note="종목코드·이름·시장 셋. 한글 종목명 검색과 야후 접미사 판별이 이것을 쓴다",
    ),
    Step(
        key="postgres",
        label="Postgres 재적재",
        script="scripts/load_pg.py",
        run_args=(),
        check_args=("--verify-only",),
        outputs=("ohlcv", "securities", "ohlcv_sync_log"),
        in_git=False,
        needs_db=True,
        note="쓰기 경로는 아직 SQLite 다(S8 전). 이걸 안 돌리면 두 저장소가 갈린다",
    ),
)

TOTAL_STEPS = len(STEPS)

# 수집 구간 상한. `fetch_krx.py` 자신의 기본값은 250 이고, 사슬의 기본값은 30 이다
# (30거래일이면 한 달 반이라 어지간히 쉬어도 따라잡는다).
MIN_DAYS = 1
MAX_DAYS = 400
DEFAULT_DAYS = 30

# 한 단계에 허용하는 최대 시간. 실측 근거 — 17거래일 수집 66초 · 축약본 102초 ·
# 스냅샷 약 100초(ADR-DS-0016 결과표). 250거래일 수집이 최악이고 약 17분이므로
# 45분이면 정상 실행을 자르지 않으면서 **영원히 매달리는 것**은 끊는다.
STEP_TIMEOUT_SECONDS = 45 * 60

# 중단·시간초과에서 얌전히 죽을 시간을 준 뒤 강제로 끊는다.
TERMINATE_GRACE_SECONDS = 5

# 로그는 링 버퍼다. 250거래일 수집은 수천 줄을 뱉는데 그걸 다 들고 있을 이유가 없다.
# 사람이 보는 것은 **지금 어디쯤인가**와 **왜 실패했는가** 둘뿐이라 꼬리면 충분하다.
LOG_LINES = 400


def step_args(step: Step, *, days: int, check: bool) -> List[str]:
    """그 단계에 붙일 인자 목록. `{days}` 를 채운 결과다."""
    if check:
        return list(step.check_args)
    return [arg.format(days=days) for arg in step.run_args]


def shell_command(step: Step, *, days: int, check: bool) -> str:
    """셸에 붙일 형태 — `scripts/fetch_krx.py --days 30`. `invoke refresh` 가 쓴다.

    인자에 공백이 없다는 것이 전제다(`--days 30` · `--check` 뿐이라 지금은 참이다).
    공백이 들어갈 인자가 생기면 그때 `shlex.join` 으로 바꾼다.
    """
    return " ".join([step.script, *step_args(step, days=days, check=check)])


def argv(step: Step, *, days: int, check: bool) -> List[str]:
    """자식 프로세스로 띄울 argv. 셸을 거치지 않는다.

    ⚠️ **`shell=True` 를 쓰지 않는다.** `days` 가 화면에서 오는 값이라 셸을 거치면
    문자열 하나로 뭉쳐 주입 표면이 생긴다. 라우터가 범위까지 검사하지만
    (`MIN_DAYS`~`MAX_DAYS`), 검사에 기대지 않고 **셸을 아예 없앤다.**
    """
    return [
        sys.executable or "python3",
        str(PROJECT_ROOT / step.script),
        *step_args(step, days=days, check=check),
    ]


# ==================================================
# 2. Postgres 단계의 상대
# ==================================================
def host_database_url() -> str:
    """이 프로세스에서 쓸 `DATABASE_URL`.

    ⚠️ `settings.DEFAULT_LOCAL_DATABASE_URL` 은 `@db:5432` 다. 그것은 **compose 네트워크
    안쪽 이름**이라 호스트 셸에서는 절대 풀리지 않는다 — `socket.gaierror` 로 죽는데
    스택이 asyncpg 안쪽에서 40줄 나와서 원인이 "DB 가 안 떴나" 로 보인다.

    이미 `DATABASE_URL` 이 있으면 **손대지 않는다** — Supabase 를 가리키고 있을 수 있고,
    그 경우 원격 차단은 `load_pg.py` 가 판단할 몫이다(`--allow-remote`).
    컨테이너 안이면 그 값이 `@db:5432` 이고 거기서는 그대로 풀린다.
    """
    existing = settings.env("DATABASE_URL")
    if existing:
        return existing
    return "postgresql+asyncpg://postgres:postgres@localhost:5432/data_service"


# 두드려 본 결과를 잠깐 기억한다. 화면 패널이 2초마다 상태를 묻는데, DB 가 꺼져 있으면
# 연결 시도가 매번 1초를 통째로 잡아먹어 폴링이 그만큼 느려진다.
# ⚠️ **사슬은 이 기억을 쓰지 않는다**(`max_age=0`). 건너뛸지 말지는 그 순간의 사실이어야 한다.
REACHABLE_MEMO_SECONDS = 30.0
_reach_lock = threading.Lock()
_reach_memo: Optional[Tuple[float, str, bool]] = None


def postgres_reachable(url: str = "", *, max_age: float = 0.0) -> bool:
    """그 주소에 붙을 수 있는지 TCP 로만 두드려 본다 (1초).

    붙지 못하면 적재를 **건너뛰되 막지는 않는다.** Postgres 는 아직 읽기 경로의 기본값이
    아니라(S5 전), DB 를 안 띄운 셸에서도 시세 갱신 자체는 끝까지 돌아야 한다.

    `max_age` 를 주면 그 초 안에 같은 주소로 얻은 답을 다시 쓴다 (상태 폴링용).
    """
    import socket
    from urllib.parse import urlparse

    global _reach_memo

    target = url or host_database_url()
    if max_age > 0:
        with _reach_lock:
            memo = _reach_memo
        if memo and memo[1] == target and time.monotonic() - memo[0] < max_age:
            return memo[2]

    parsed = urlparse(target.replace("postgresql+asyncpg://", "postgresql://"))
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1):
            answer = True
    except OSError:
        answer = False

    with _reach_lock:
        _reach_memo = (time.monotonic(), target, answer)
    return answer


# ==================================================
# 3. 실행할 수 있는가 — 환경이 아니라 **능력**을 묻는다
# ==================================================
class RefreshUnavailable(RuntimeError):
    """이 프로세스에서는 갱신을 돌릴 수 없다. 이유와 처방을 함께 들고 있다."""

    def __init__(self, reason: str, hints: Tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.hints = hints


class RefreshBusy(RuntimeError):
    """이미 하나가 돌고 있다. 사슬은 같은 파일을 만지므로 겹쳐 돌릴 수 없다."""


def capability() -> Dict:
    """지금 이 프로세스가 갱신을 실행할 수 있는가. **감추지 않고 이유를 말한다.**

    ADR-DS-0016 §4 가 정한 원칙을 그대로 잇는다 — *따를 수 없는 처방을 띄우지 않는다.*
    배포본 화면에 "지금 갱신" 버튼을 살려 두면 눌러도 안 되고, 아예 지우면 왜 없는지를
    설명할 자리가 사라진다. 그래서 **버튼은 두되 잠그고, 이유와 대안을 함께 싣는다.**

    막히는 이유는 넷이고 **환경 하나로 뭉뚱그리지 않는다** — 처방이 각각 다르기 때문이다.
    """
    if settings.is_vercel():
        return _blocked(
            "배포본에서는 갱신을 실행하지 않습니다.",
            (
                "배포본의 파일시스템은 읽기 전용이고 `data/krx_cache.db` 자체가 없습니다.",
                "산출물 셋은 어차피 **커밋을 거쳐야** 배포본에 닿습니다 "
                "(git 연동 배포는 git 에 있는 것만 옮깁니다).",
                "로컬에서 갱신하고 `git push` 하세요 — 그것이 배포입니다.",
            ),
        )

    if not settings.refresh_api_enabled():
        return _blocked(
            "`REFRESH_API=off` 로 실행 경로를 닫아 두었습니다.",
            (
                "다시 열려면 `REFRESH_API` 를 지우거나 `on` 으로 두고 서버를 다시 띄웁니다.",
                "닫혀 있어도 상태 조회와 `invoke refresh` 는 그대로 됩니다.",
            ),
        )

    if not SCRIPTS_DIR.is_dir():
        return _blocked(
            "이 프로세스에서 `scripts/` 를 볼 수 없습니다.",
            (
                f"찾은 자리: `{SCRIPTS_DIR}`",
                "컨테이너 이미지에는 `scripts/` 가 들어 있지 않습니다 "
                "(`.dockerignore` — 이미지 용량 최소화).",
                "호스트 셸에서 `invoke refresh` 를 쓰거나, 호스트에서 띄운 서버 화면에서 누르세요.",
            ),
        )

    missing = [step.script for step in STEPS if not (PROJECT_ROOT / step.script).is_file()]
    if missing:
        return _blocked(
            f"사슬의 스크립트가 없습니다 — {', '.join(missing)}",
            ("저장소가 온전한지 확인하세요. 사슬 다섯 칸은 전부 있어야 순서가 성립합니다.",),
        )

    if not _writable(DATA_DIR):
        return _blocked(
            f"`{DATA_DIR}` 에 쓸 수 없습니다.",
            (
                "갱신은 이 폴더에 파일 넷을 새로 씁니다.",
                "컨테이너라면 바인드 마운트한 호스트 폴더의 권한을 확인하세요 "
                "(이미지는 uid 1001 `appuser` 로 돕니다).",
            ),
        )

    return {"available": True, "reason": "", "hints": []}


def _blocked(reason: str, hints: Tuple[str, ...]) -> Dict:
    return {"available": False, "reason": reason, "hints": list(hints)}


def _writable(folder) -> bool:
    """실제로 써 보고 지운다. 폴더가 있다고 쓸 수 있는 것은 아니다."""
    import os

    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".refresh-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# ⚠️ 이 사슬은 **git 을 부르지 않는다** (ADR-DS-0016 결정 1 · ADR-DS-0017).
#    push 가 곧 Vercel 배포라 그 시점은 사람이 정한다. 화면 버튼이 생기면서 이 약속은
#    더 무거워졌다 — 브라우저에서 누른 것이 배포까지 이어지면 되돌릴 자리가 없다.
#    `tests/test_refresh_job.py` 가 이 목록을 사슬 정의에 대고 검사한다.
FORBIDDEN_IN_CHAIN = ("git", "vercel", "push", "commit")


# ==================================================
# 4. 작업 — 상태 · 로그 · 자식 프로세스
# ==================================================
def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


class _Job:
    """갱신 한 번. 상태는 전부 `_state_lock` 뒤에 있다."""

    def __init__(self, *, mode: str, days: int, skip_pg: bool):
        self.id = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        self.mode = mode                      # "run" · "check"
        self.days = days
        self.skip_pg = skip_pg
        self.state = "running"
        self.message = "시작합니다…"
        self.started_at = _now()
        self.ended_at = ""
        self._started_monotonic = time.monotonic()
        self._ended_monotonic: Optional[float] = None
        self._log: Deque[str] = deque(maxlen=LOG_LINES)
        self._steps: List[Dict] = [
            {
                "key": step.key,
                "label": step.label,
                "state": "wait",
                "seconds": 0.0,
                "exit_code": None,
                "note": step.note,
                "in_git": step.in_git,
                "outputs": list(step.outputs),
                "command": shell_command(step, days=days, check=(mode == "check")),
            }
            for step in STEPS
        ]
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._state_lock = threading.Lock()

    # ── 기록 ────────────────────────────
    def log(self, line: str) -> None:
        with self._state_lock:
            self._log.append(line)

    def mark(self, index: int, state: str, **fields) -> None:
        with self._state_lock:
            self._steps[index].update(state=state, **fields)

    def mark_remaining(self, state: str, note: str) -> None:
        """아직 손대지 않은 칸을 한꺼번에 정리한다."""
        with self._state_lock:
            for row in self._steps:
                if row["state"] == "wait":
                    row.update(state=state, note=note)

    def finish(self, state: str, message: str) -> None:
        with self._state_lock:
            self.state = state
            self.message = message
            self.ended_at = _now()
            self._ended_monotonic = time.monotonic()

    # ── 자식 프로세스 ────────────────────
    def attach(self, process: Optional[subprocess.Popen]) -> None:
        with self._state_lock:
            self._process = process

    @property
    def cancelled(self) -> bool:
        with self._state_lock:
            return self._cancelled

    def cancel(self) -> bool:
        """중단을 요청한다. 지금 도는 자식을 끊고 남은 단계는 돌지 않는다.

        ⚠️ **끊긴 단계의 산출물은 반쯤 쓰였을 수 있다.** 세 빌더가 `write_text` 로
        곧바로 덮으므로 원자적이지 않다. 다행히 **다섯 단계 전부 다시 돌리면 고쳐진다**
        (수집은 날짜별 커밋 · 적재기는 `ON CONFLICT` · 빌더는 통째로 다시 쓴다).
        그 사실을 화면에도 적는다 — "다시 누르면 됩니다" 가 정확한 처방이다.
        """
        with self._state_lock:
            if self.state != "running":
                return False
            self._cancelled = True
            process = self._process
        if process and process.poll() is None:
            _stop(process)
        return True

    # ── 바깥으로 ────────────────────────
    def snapshot(self) -> Dict:
        with self._state_lock:
            done = sum(1 for row in self._steps if row["state"] in ("done", "skipped"))
            end = self._ended_monotonic or time.monotonic()
            return {
                "id": self.id,
                "mode": self.mode,
                "days": self.days,
                "skip_pg": self.skip_pg,
                "state": self.state,
                "running": self.state == "running",
                "message": self.message,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "seconds": round(end - self._started_monotonic, 1),
                "done_steps": done,
                "total_steps": TOTAL_STEPS,
                "steps": [dict(row) for row in self._steps],
                "log": list(self._log),
            }


def _stop(process: subprocess.Popen) -> None:
    """얌전히 끊고, 안 죽으면 강제로 끊는다."""
    try:
        process.terminate()
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
    except OSError:
        pass


# ==================================================
# 5. 실행기
# ==================================================
_lock = threading.Lock()
_current: Optional[_Job] = None


def start(*, days: int = DEFAULT_DAYS, check: bool = False, skip_pg: bool = False) -> Dict:
    """갱신을 시작하고 **곧바로** 첫 상태를 돌려준다.

    사슬은 4분 안팎(250거래일이면 20분 넘게) 걸리므로 요청 안에서 기다리지 않는다.
    화면은 `state()` 를 물어 가며 그린다.
    """
    global _current

    ready = capability()
    if not ready["available"]:
        raise RefreshUnavailable(ready["reason"], tuple(ready["hints"]))

    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))

    with _lock:
        if _current is not None and _current.state == "running":
            raise RefreshBusy(
                "이미 갱신이 돌고 있습니다. 사슬은 같은 파일을 만지므로 겹쳐 돌릴 수 없습니다."
            )
        job = _Job(mode="check" if check else "run", days=days, skip_pg=skip_pg)
        _current = job

    worker = threading.Thread(target=_work, args=(job,), name=f"refresh-{job.id}", daemon=True)
    worker.start()
    return job.snapshot()


def state() -> Optional[Dict]:
    """마지막(또는 지금 도는) 작업의 상태. 아직 한 번도 안 돌렸으면 None."""
    with _lock:
        job = _current
    return job.snapshot() if job else None


def cancel() -> Optional[Dict]:
    """도는 작업을 중단한다. 돌고 있지 않으면 None."""
    with _lock:
        job = _current
    if job is None or not job.cancel():
        return None
    return job.snapshot()


def _work(job: _Job) -> None:
    """작업 스레드 본체. 예외를 밖으로 내보내지 않는다 — 나가면 상태가 `running` 에 굳는다."""
    mode_text = "확인" if job.mode == "check" else "갱신"
    job.log(f"── 자료 {mode_text} — 전체 {TOTAL_STEPS}단계 · 최근 {job.days}거래일 ──")

    failed: Optional[str] = None
    try:
        for index, step in enumerate(STEPS):
            if job.cancelled:
                job.mark(index, "cancelled", note="앞 단계에서 중단됨")
                continue

            skip_reason = _skip_reason(job, step)
            if skip_reason:
                job.mark(index, "skipped", note=skip_reason)
                job.log(f"[{index + 1}/{TOTAL_STEPS}] {step.label} — 건너뜀. {skip_reason}")
                continue

            mark = " (git 에 올라간다 → 배포본에 반영됨)" if step.in_git else ""
            job.log(f"[{index + 1}/{TOTAL_STEPS}] {step.label}{mark}")
            job.mark(index, "now")

            code, seconds, timed_out = _run_step(job, step)

            if job.cancelled:
                job.mark(index, "cancelled", seconds=seconds, exit_code=code,
                         note="중단됨 — 이 단계의 산출물은 반쯤 쓰였을 수 있다. 다시 돌리면 고쳐진다")
                failed = "중단했습니다."
                break
            if timed_out:
                job.mark(index, "failed", seconds=seconds, exit_code=code,
                         note=f"시간 초과 ({STEP_TIMEOUT_SECONDS // 60}분)")
                failed = f"{step.label} 이 {STEP_TIMEOUT_SECONDS // 60}분을 넘겨 끊었습니다."
                break
            if code != 0:
                job.mark(index, "failed", seconds=seconds, exit_code=code,
                         note=f"종료코드 {code} — 위 로그의 마지막 줄이 원인이다")
                failed = f"{step.label} 이 실패했습니다 (종료코드 {code})."
                break

            job.mark(index, "done", seconds=seconds, exit_code=0)

    except Exception as error:                # 실행기 자신의 결함까지 화면에 드러낸다
        failed = f"갱신기가 예외로 멈췄습니다 — {error}"
        job.log(f"[오류] {error}")

    if failed:
        # 남은 칸을 `wait` 로 두지 않는다. `wait` 는 "곧 돈다" 로 읽히는데 사실은
        # **영영 안 돈다** — 사슬이 여기서 끊겼기 때문이다. 그 차이가 화면에서 보여야
        # "왜 스냅샷은 그대로지?" 를 다시 묻지 않는다.
        job.mark_remaining("cancelled", "앞 단계가 멈춰 돌지 않았습니다")

    _close(job, failed)


def _skip_reason(job: _Job, step: Step) -> str:
    """이 단계를 건너뛸 이유. 없으면 빈 문자열."""
    if not step.needs_db:
        return ""
    if job.skip_pg:
        return "Postgres 재적재를 건너뜁니다 (요청에 담겨 있음)."
    if not postgres_reachable():
        return (
            "DB 에 붙을 수 없습니다 — `docker compose --profile local-db up -d` 로 띄웁니다. "
            "⚠️ 안 띄우면 SQLite 만 최신이 되고 Postgres 는 그 자리에 남습니다."
        )
    return ""


def _run_step(job: _Job, step: Step) -> Tuple[int, float, bool]:
    """한 단계를 자식 프로세스로 돌리고 출력을 줄 단위로 로그에 넣는다.

    돌려주는 것은 `(종료코드, 걸린 초, 시간초과인가)`.
    """
    command = argv(step, days=job.days, check=(job.mode == "check"))

    # 자식에게 넘길 환경. 인증키가 그대로 따라가야 수집이 뜬다.
    #   PYTHONUNBUFFERED — 없으면 파이프에 물린 자식의 출력이 블록 단위로 뭉쳐서
    #                      진행 로그가 끝날 때 한꺼번에 쏟아진다 (진행 표시의 뜻이 사라진다).
    #   PYTHONIOENCODING — 윈도우 콘솔 기본 인코딩(cp949)에서 한글 출력이 죽는 것을 막는다.
    overrides = {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    if step.needs_db:
        overrides["DATABASE_URL"] = host_database_url()
    child_env = settings.subprocess_env(**overrides)

    started = time.monotonic()
    timed_out = threading.Event()

    try:
        process = subprocess.Popen(                     # noqa: S603 — 인자는 위 표에서만 온다
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,                   # 오류도 같은 줄기로 — 순서가 보존된다
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
        )
    except OSError as error:
        job.log(f"      실행할 수 없습니다 — {error}")
        return 127, round(time.monotonic() - started, 1), False

    job.attach(process)

    # ⚠️ Popen 과 attach 사이에 중단이 들어오면 `cancel()` 이 죽일 대상을 못 찾는다.
    #    그 창을 놓치면 250거래일 수집이 **끝까지 돌아 버린다** — 중단이 안 먹는 것으로 보인다.
    if job.cancelled:
        _stop(process)

    def _timeout() -> None:
        timed_out.set()
        _stop(process)

    watchdog = threading.Timer(STEP_TIMEOUT_SECONDS, _timeout)
    watchdog.daemon = True
    watchdog.start()

    try:
        if process.stdout is not None:
            for line in process.stdout:
                job.log(f"      {line.rstrip()}")
        code = process.wait()
    finally:
        watchdog.cancel()
        job.attach(None)

    return code, round(time.monotonic() - started, 1), timed_out.is_set()


def _close(job: _Job, failed: Optional[str]) -> None:
    """끝맺음 — 프로세스 캐시를 씻고, 사람이 볼 판정 한 줄을 남긴다.

    ⚠️ **`finish()` 가 맨 마지막이어야 한다.** 화면은 `running` 이 false 가 되는 순간
    폴링을 멈추므로, 상태를 먼저 닫으면 그 뒤에 적는 줄(지금 상태 · 커밋 절차)을
    **아무도 못 본다.** 하필 그 두 덩어리가 사람이 실제로 읽는 부분이다.
    """
    if failed:
        job.log(f"\n판정: {failed}")
        job.finish("cancelled" if job.cancelled else "failed", failed)
        return

    if job.mode == "run":
        # ⚠️ 이 프로세스가 들고 있는 사본들을 버려야 화면이 새 자료를 본다.
        #    이것을 빠뜨리면 "갱신은 성공했다는데 화면은 그대로" 가 된다 — 갱신 버튼에서
        #    가장 흔하게 나올 실패 모양이고, 오류로 뜨지 않아 사람이 버튼을 의심하게 된다.
        for line in reset_process_caches():
            job.log(f"      {line}")

    summary = data_summary()
    job.log("\n── 지금 상태 ──")
    job.log(f"  KRX 시세   : {summary['krx']['text']}")
    job.log(f"  시장 스냅샷 : {summary['snapshot']['text']}")

    if job.mode == "run":
        job.log("\n⚠️ 배포본에 반영하려면 커밋·push 가 남았습니다 (push 가 곧 Vercel 배포입니다):")
        job.log("   git status --short && git add data/ && git commit && "
                "git push origin main && git push gitlab main")

    job.finish("done", "끝났습니다." if job.mode == "run" else "확인만 했습니다.")


# ==================================================
# 6. 갱신 뒤 씻어야 하는 것
# ==================================================
def reset_process_caches() -> List[str]:
    """사슬이 파일을 바꿨으므로 이 프로세스가 들고 있던 사본을 버린다.

    씻어야 하는 것이 다섯이고 **성격이 제각각**이라 한 줄로 안 끝난다.

    | 대상 | 왜 남아 있나 | 안 씻으면 |
    |---|---|---|
    | 대시보드 `/tmp` 캐시 | 카드 묶음을 5분 캐시 | 갱신 직후에도 `17거래일 전` 이라고 말한다 |
    | 시장 스냅샷 | 기동 때 메모리에 한 번 | 스크리닝이 옛 기준일로 돈다 |
    | 파생 JSON | 기동 때 메모리에 한 번 | 새 거래일을 **휴장일로** 본다 |
    | 자동완성 색인 | 기동 때 메모리에 한 번 | 신규 상장 종목이 검색되지 않는다 |
    | 라이브 조회 캐시 | 10분 TTL | 저장소는 찼는데 배지가 `krx-live-memo` 라고 말한다 |

    ⚠️ import 를 함수 안에서 한다. 이 모듈은 `tasks.py`(invoke)도 import 하는데,
    거기서는 화면 계층이 필요 없고 `dashboard_data` 는 야후·FRED 클라이언트를 끌고 온다.
    """
    from app.repositories import krx_bundle, krx_store, snapshot_store
    from app.services import dashboard_data, search_service

    lines: List[str] = []

    def attempt(label: str, action) -> None:
        try:
            lines.append(f"{label} — {action()}")
        except Exception as error:            # 캐시를 못 씻는다고 갱신이 실패한 것은 아니다
            lines.append(f"{label} — 실패: {error}")

    attempt("대시보드 캐시", lambda: f"{dashboard_data.invalidate()}개 파일 삭제")
    attempt("시장 스냅샷", lambda: f"기준일 {snapshot_store.reload().get('as_of') or '없음'}")
    attempt("거래일 캘린더", lambda: f"{len(krx_bundle.reload().get('trading_days') or [])}일")
    attempt("자동완성 색인", lambda: f"{search_service.reload():,}종목")
    attempt("라이브 조회 캐시", lambda: f"{krx_store.clear_live_cache()}건 삭제")
    return lines


# ==================================================
# 7. 사람이 실제로 보는 두 숫자
# ==================================================
def data_summary() -> Dict:
    """끝에 다시 찍는 두 줄 — KRX 최신 거래일, 스냅샷 기준일·낡음.

    단계별 로그는 길어서 마지막 판정이 스크롤 위로 사라진다. `invoke refresh` 와
    화면 패널이 **같은 두 숫자**를 보도록 여기서 한 번만 만든다.
    """
    from app.repositories import krx_store, snapshot_store

    krx: Dict = {"ok": False, "text": ""}
    try:
        stats = krx_store.stats()
        krx = {
            "ok": True,
            "mode": stats.get("mode"),
            "last_date": stats.get("last_date"),
            "days": stats.get("days"),
            "rows": stats.get("rows"),
            "text": (f"{stats.get('mode')} · {stats.get('last_date')} 까지 "
                     f"· {stats.get('days')}거래일 · {stats.get('rows'):,}행"),
        }
    except Exception as error:
        krx = {"ok": False, "text": f"확인 실패 — {error}"}

    try:
        snap = snapshot_store.stats()
        behind = snap.get("trading_days_behind")
        verdict = f"{behind}거래일 전" if snap.get("stale") else "최신"
        snapshot = {
            "ok": True,
            "as_of": snap.get("as_of"),
            "count": snap.get("count"),
            "stale": bool(snap.get("stale")),
            "trading_days_behind": behind,
            "generated_at": snap.get("generated_at"),
            "text": (f"기준일 {snap.get('as_of')} · {snap.get('count'):,}종목 · {verdict}"
                     + (f" · 마지막 생성 {snap.get('generated_at')}"
                        if snap.get("generated_at") else "")),
        }
    except Exception as error:
        snapshot = {"ok": False, "text": f"확인 실패 — {error}"}

    return {"krx": krx, "snapshot": snapshot}
