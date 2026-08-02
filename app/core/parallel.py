"""서로 무관한 외부 호출을 동시에 돌린다 (순수 유틸 — 아무것도 import 하지 않는다)

왜 필요한가 — 실측이 말해 준다
------------------------------
배포본에서 CORP-R 전구간이 26.9초인데, 그중 **H02 하나가 17~22초**다.
그런데 함수 콜드스타트가 아니다. 함수가 이미 깨어 있는 상태에서 **처음 보는 종목**으로
다시 재 봐도 같다.

    워밍업(삼성전자)   H00 279ms · H01 215ms · H02    209ms   ← /tmp 캐시 히트
    SK하이닉스        H00 217ms · H01 209ms · H02 18,738ms
    카카오            H00 213ms · H01 227ms · H02 21,718ms
    클래시스          H00 210ms · H01 215ms · H02 17,318ms

H02 는 DART 를 세 번 부른다 (공시목록 1 + 재무제표 2). 배포본에서 **회당 6~7초**다
(로컬은 0.14~0.5초 — 10~40배). 세 번이 **순차**라 그대로 더해진다.

세 호출은 서로의 결과를 쓰지 않는다. 그래서 동시에 보내면 가장 느린 하나만 기다리면 된다.

    순차   ██████ + ██████ + ██████  =  19.5초
    병렬   ██████                    ≈   7초  (가장 느린 것)

왜 스레드로 되나
---------------
GIL 이 있어도 **네트워크 대기 중에는 GIL 을 놓는다.** `urllib` 이 응답을 기다리는 동안
다른 스레드가 자기 요청을 보낼 수 있다. 계산이 아니라 기다림이 병목이라 스레드로 충분하다
(프로세스를 나눌 필요가 없고, 서버리스에서는 나눌 수도 없다).

지키는 것
--------
· **하나가 실패해도 나머지는 살린다.** 예외를 잡아 결과에 담아 돌려준다 —
  GIC 불변원칙 §2-2 (부분 결과라도 계속 낸다) 를 이 층에서도 지킨다.
· **결과를 쓰는 쪽은 순서를 유지한다.** 여기서는 '받아 오기' 만 동시에 하고,
  장부(E-/D-/CALC- 번호)에 적는 일은 부르는 쪽이 정해진 순서대로 한다.
  안 그러면 실행할 때마다 근거 번호가 달라져 리포트가 재현되지 않는다.
· **각자 얼마나 걸렸는지 함께 낸다.** 병렬로 돌리면 어디가 느렸는지 안 보이기 때문이다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

# 한 번에 띄울 스레드 상한. 외부 API 에 동시에 던지는 요청 수이기도 하다.
# DART 는 초당 요청 제한이 아니라 **일일 한도(2만 건)** 로 관리하므로 동시 3~4개는 문제없다
# (스냅샷 빌드는 이미 100개씩 26회를 부르고 있다 — 변경노트 N11).
MAX_WORKERS = 4


def gather(tasks: Dict[str, Callable[[], object]],
           max_workers: int = MAX_WORKERS,
           timeout: Optional[float] = None) -> Dict[str, Dict]:
    """`{이름: 무인자 함수}` 를 동시에 돌리고 이름별 결과를 돌려준다.

    돌려주는 모양 (**예외를 올리지 않는다**)

        {"공시목록": {"ok": True,  "value": …, "error": "",     "seconds": 6.4},
         "재무제표": {"ok": False, "value": None, "error": "…", "seconds": 6.1}}

    `timeout` 을 주면 그 시간을 넘긴 작업은 `ok=False` 와 사유로 돌아온다.
    **던져진 요청 자체를 중간에 끊지는 못한다** (파이썬 스레드는 강제 종료가 안 된다) —
    기다리기를 그만둘 뿐이다. 그래서 서버리스 상한보다 넉넉히 짧게 잡아야 뜻이 있다.
    """
    if not tasks:
        return {}

    started = time.monotonic()
    results: Dict[str, Dict] = {
        name: {"ok": False, "value": None, "error": "", "seconds": None} for name in tasks
    }

    def run(name: str, fn: Callable[[], object]) -> None:
        begin = time.monotonic()
        try:
            results[name].update(ok=True, value=fn())
        except Exception as error:                    # 하나가 죽어도 나머지는 살린다
            results[name].update(ok=False, error=f"{type(error).__name__}: {error}")
        finally:
            results[name]["seconds"] = round(time.monotonic() - begin, 3)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as pool:
        futures = {pool.submit(run, name, fn): name for name, fn in tasks.items()}
        for future in futures:
            remaining = None if timeout is None else max(0.0, timeout - (time.monotonic() - started))
            try:
                future.result(timeout=remaining)
            except Exception as error:                # 주로 TimeoutError
                name = futures[future]
                if results[name]["seconds"] is None:
                    results[name].update(
                        ok=False,
                        error=f"{type(error).__name__}: 제한 시간 안에 끝나지 않았다",
                        seconds=round(time.monotonic() - started, 3))

    return results


def summarize(results: Dict[str, Dict]) -> Dict:
    """어디가 느렸는지 한 줄로 — 리포트의 `limitation` 에 그대로 실을 수 있게."""
    if not results:
        return {"count": 0, "text": "동시에 부른 것이 없다"}
    timings = [(name, row.get("seconds") or 0.0) for name, row in results.items()]
    timings.sort(key=lambda kv: -kv[1])
    total = sum(t for _, t in timings)
    slowest, slowest_seconds = timings[0]
    failed = [name for name, row in results.items() if not row["ok"]]
    return {
        "count": len(results),
        "failed": failed,
        "slowest": slowest,
        "slowest_seconds": slowest_seconds,
        "sum_seconds": round(total, 3),
        # 순차로 돌렸다면 합계만큼 걸렸을 것이다 — 얼마를 아꼈는지 밝힌다
        "saved_seconds": round(max(0.0, total - slowest_seconds), 3),
        "text": (f"{len(results)}건을 동시에 불렀다 — 가장 느린 것은 {slowest} "
                 f"{slowest_seconds:.1f}초 (순차였다면 {total:.1f}초)"),
    }
