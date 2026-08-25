#!/usr/bin/env python3
"""DART 공시·정기보고서 일괄 수집 — 유니버스를 돌며 `clip` 에 담는다 (ADR-DS-0020)

    python3 scripts/collect_dart.py --top 350            # 시총 상위 350 (약 3분 반 · 1,000호출 남짓)
    python3 scripts/collect_dart.py --code 005930        # 한 종목만
    python3 scripts/collect_dart.py --top 350 --dry-run  # 네트워크 없이 예상 호출만
    python3 scripts/collect_dart.py --check              # 능력·예산·매핑 나이만
    python3 scripts/collect_dart.py --top 350 --resume-from 035420   # 이어서

**② 일괄 수집의 실행처는 여기 하나다.** HTTP 경로를 만들지 않은 이유는
`app/routers/collect_router.py` 의 모듈 설명에 있다 — 요약하면 진행률·중단 장치를 쓸 곳이
아직 하나도 없고, 웹 프로세스에서 긴 배치를 돌리면 전역(`dart_data._last_attempt`)과
동기 다리 루프를 배치가 점유한다.

⚠️ **갱신 사슬(`invoke refresh`)에 넣지 않았다.** 그 사슬은 시세(SQLite)를 다루고
`CHAIN_ENV` 로 그쪽에 못 박혀 있는데(ADR-DS-0018) 이쪽은 보관함(Postgres)에 담는다.
그리고 순서 의존이 없다 — 사슬 2~4단계가 서로를 기다리는 것과 다르다.

⚠️ **커밋하지 않는다.** `clip` 은 DB 에 있고 git 에 올라가지 않는다.

## 종료 코드

    0  완주 · 예산 소진으로 부분 완료(재개 지점을 알려 준다) · `--check`/`--dry-run`
    2  인자·능력 문제 (어휘 밖 인자 · 키 없음 · 보관함에 못 붙음 · 수집 API 꺼짐)
    3  계통 중단 (DART 한도 초과 · 인증키 거부 · 시스템 점검 · 연속 실패)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 이 스크립트는 scripts/ 안에 있다. `app.*` 를 찾으려면 루트를 경로에 넣어야 한다.
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.clients import dart_data  # noqa: E402  (경로 설정 뒤에 import)
from app.repositories import clip_store, watermark_store  # noqa: E402
from app.services import dart_collector as collector  # noqa: E402

# 계통 중단 사유 — 종목을 바꿔도 낫지 않는 것들. 종료 코드 3 으로 죽는다.
SYSTEMIC = ("aborted", "consecutive_failures", "bad_request")


def describe(state: dict) -> None:
    """지금 수집할 수 있는지와, 못 하면 무엇을 해야 하는지."""
    ability = collector.capability()
    print("DART 공시 수집 — 상태")
    print(f"  쓸 수 있나 : {'예' if ability['available'] else '아니오'}")
    if not ability["available"]:
        print(f"  이유       : {ability['reason']}")
        for hint in ability["hints"]:
            print(f"    {hint}")

    corp = collector.corp_code_state()
    age = f"{corp['age_days']}일 전" if corp["age_days"] is not None else "알 수 없음"
    print(f"  고유번호   : {corp['generated_at'] or '없음'} ({age}) · "
          f"{corp['listed_count']:,}개 상장사")
    if corp["hint"]:
        print(f"    ⚠️ {corp['hint']}")

    if not ability["available"]:
        return

    budget = collector.calls_left(reserve=collector.ONDEMAND_RESERVE)
    print(f"  오늘 예산   : 이 수집기가 쓴 호출 {budget['used_by_collector']:,} · "
          f"남음 {budget['left']:,} / {budget['batch_budget']:,} "
          f"(DART 일 한도 {budget['daily_limit']:,})")
    filings = clip_store.count_filings()
    print(f"  담긴 공시   : {filings['count']:,}건 · 최신 접수일 {filings['latest'] or '없음'}")
    last = watermark_store.get("dart_filing")
    if last:
        print(f"  마지막 회차 : {last['last_synced_at']} · {last['last_key']} · "
              f"{last['status']} · 새로 {last['rows']}건")


def run(args: argparse.Namespace) -> int:
    plan = collector.Plan(
        scope="one" if args.code else "top",
        codes=(args.code,) if args.code else (),
        top=args.top,
        months=args.months,
        incremental=not args.no_incremental,
        budget=args.budget,
        resume_from=args.resume_from,
        dry_run=args.dry_run,
    )

    result = collector.collect(plan, on_progress=lambda line: print(line, flush=True))

    if not result["available"]:
        print(f"\n수집할 수 없다 — {result['reason']}", file=sys.stderr)
        for hint in result["hints"]:
            print(f"  {hint}", file=sys.stderr)
        return 2

    if result["stopped"] == "dry_run":
        print(f"[미리보기] 대상 {result['codes_total']:,}종목 × 유형 3 = "
              f"예상 호출 {result['planned_calls']:,} · "
              f"오늘 남은 예산 {result['budget']['left']:,}")
        print(f"  {result['scope_note']}")
        return 0

    print("\n── 결과 ──")
    print(f"  대상        : {result['codes_total']:,}종목 중 {result['codes_done']:,} 처리 "
          f"({result['scope_note']})")
    print(f"  담김        : 새로 {result['created']:,} · 이미 있던 것 {result['duplicate']:,}")
    print(f"  건너뜀      : {result['skipped']:,} · 공시 없음 {result['empty']:,} · "
          f"실패 {result['failed']:,} · 못 담음 {result['invalid']:,}")
    print(f"  호출        : {result['calls']:,} (오늘 누적 "
          f"{result['budget']['used_by_collector']:,})")
    print(f"  걸린 시간   : {result['elapsed_ms'] / 1000:.1f}초")

    if result["truncated_codes"]:
        # ⚠️ 조용히 넘기지 않는다 — 못 받은 구간은 다음 회차에도 안 메워진다.
        print(f"  ⚠️ 구간이 잘린 채 남은 종목 {len(result['truncated_codes'])}개: "
              f"{', '.join(result['truncated_codes'][:10])}")
        # ⚠️ **`--months` 만으로는 아무 일도 안 일어난다.** 진행 지점이 있는 종목은
        #    `bgn` 을 watermark 에서 가져오므로 `months` 가 통째로 무시된다(설계상 그렇다).
        #    창을 실제로 좁히려면 진행 지점을 무시해야 한다.
        print(f"     좁은 창으로 다시 받는다: python3 scripts/collect_dart.py "
              f"--code {result['truncated_codes'][0]} --no-incremental --months 3")

    for problem in result["problems"]:
        print(f"  ⚠️ {problem}")
    for failure in result["failures"][:10]:
        print(f"  · {failure['code']} {failure['name']} — {failure['state']}: {failure['reason']}")

    if result["stopped"] == "budget":
        # ⚠️ **예산 소진은 실패가 아니다.** 매번 빨간불이면 진짜 실패와 구별이 안 된다.
        print(f"\n오늘 예산을 다 썼다. 이어서 하려면:\n"
              f"  python3 scripts/collect_dart.py --top {args.top} "
              f"--resume-from {result['resume_from']}")
        return 0
    if result["stopped"] in SYSTEMIC:
        print(f"\n계통 오류로 멈췄다 — {result['stopped']}", file=sys.stderr)
        if result["resume_from"]:
            print(f"  이어서 하려면: --resume-from {result['resume_from']}", file=sys.stderr)
        return 3
    if result["stopped"] == "cancelled":
        print("\n중단됐다.")
        return 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DART 공시·정기보고서를 자료 보관함(clip)에 담는다",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="⚠️ 일 한도는 20,000 호출이고 이 수집기는 그중 12,000 까지만 쓴다.",
    )
    parser.add_argument("--top", type=int, default=collector.TOP_N_DEFAULT,
                        help=f"시총 상위 몇 종목까지 (기본 {collector.TOP_N_DEFAULT})")
    parser.add_argument("--code", help="한 종목만. 주면 --top 을 무시한다")
    parser.add_argument("--months", type=int, default=12,
                        help="진행 지점이 없는 종목의 최초 조회 창 (기본 12개월)")
    parser.add_argument("--budget", type=int, default=0,
                        help="이 회차에 쓸 호출 상한. 0 이면 남은 예산 전부")
    parser.add_argument("--resume-from", default="",
                        help="이 종목코드부터 이어서 (코드 오름차순)")
    parser.add_argument("--no-incremental", action="store_true",
                        help="진행 지점을 무시하고 전부 다시 본다")
    parser.add_argument("--dry-run", action="store_true",
                        help="네트워크·DB 쓰기 없이 예상 호출만 센다")
    parser.add_argument("--check", action="store_true",
                        help="수집하지 않고 능력·예산·매핑 나이만 확인한다")
    parser.add_argument("--refresh-corp-code", action="store_true",
                        help="고유번호 매핑을 먼저 새로 만든다 (DART 에서 약 10만 건)")
    args = parser.parse_args()

    if args.top < 1:
        print("--top 은 1 이상이어야 한다", file=sys.stderr)
        return 2

    try:
        if args.refresh_corp_code:
            # 별도 스크립트를 부르지 않고 같은 빌더를 import 해 돌린다 — 두 벌이 되지 않게.
            sys.path.insert(0, str(BASE_DIR / "scripts"))
            import build_corp_code

            build_corp_code.save(build_corp_code.build())
            print(f"  매핑 {dart_data.reload_corp_map():,}개를 다시 읽었다")

        if args.check:
            describe({})
            return 0
        return run(args)
    except dart_data.DartError as error:
        # 인증키 누락·DART 장애는 사람이 고칠 수 있는 문제다. 트레이스를 그대로 뱉지 않는다.
        print(f"\nDART 오류 — {error}", file=sys.stderr)
        return 3 if error.dart_status in collector.FATAL_DART_STATUS else 2
    except RuntimeError as error:
        # `db.unreachable()` 이 만든 처방이 여기로 온다 — 이미 여러 줄짜리 안내다.
        print(f"\n{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n중단됐다. 담긴 것까지는 남아 있다.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
