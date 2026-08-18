"""표준산업분류 맵 만들기 — 피어 선정의 '업종' 을 여기서 얻는다 (M4)

왜 필요한가
    명세 §5.4 는 CORP-R 피어를 **업종 + 시총 밴드**로 잡으라고 한다. 그런데 실측해 보니
    시장 스냅샷의 `sector` 필드는 업종이 아니었다.

        KOSPI  943종목  전부 빈칸
        KOSDAQ         소속부 (우량기업부 535 · 중견기업부 468 · 벤처기업부 348 …)
        미국           거래소명 (NYSE · NASDAQ)

    소속부는 업종이 아니라 코스닥 시장 구분이다. 그래서 업종은 **DART 기업개황의
    표준산업분류(`induty_code`)** 에서 따로 받아 둔다. 회사당 0.12초라 전 종목이 8분쯤 걸린다.
    한 번 만들어 두면 리서치 때는 파일만 읽는다.

주의 — 코드 자릿수가 섞여 있다 (실측)

    삼성전자 264 · SK하이닉스 2612 · DB하이텍 2611 · 한미반도체 29271
    NAVER 63120 · 카카오 63120 (동일) · 현대차 30121 · 기아 30121 (동일)

    5자리로 완전일치만 보면 삼성전자와 SK하이닉스가 남남이 된다. 그래서 쓰는 쪽
    (`services/research/workstreams/corp_r.py`)에서 **접두어를 5→4→3→2자리로 넓혀 가며**
    후보를 찾는다. 이 스크립트는 원본 코드를 있는 그대로 저장하는 것까지만 한다.

쓰는 법
    python3 scripts/build_industry_map.py            # 전체 (약 8분)
    python3 scripts/build_industry_map.py --resume   # 하다 만 것 이어서
    python3 scripts/build_industry_map.py --check    # 만들어진 파일 상태만 본다
    python3 scripts/build_industry_map.py --limit 50 # 앞의 50개만 (시험용)
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.clients import dart_data  # noqa: E402

OUT_FILE = ROOT / "data" / "industry_map.json"
KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def show_status() -> int:
    """만들어진 맵의 상태를 요약한다."""
    if not OUT_FILE.exists():
        print(f"{OUT_FILE} 없음 — `python3 scripts/build_industry_map.py` 로 만든다")
        return 1

    data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    codes = data.get("map", {})
    have = [v for v in codes.values() if v.get("industry_code")]
    print(f"업종 맵  {OUT_FILE} · {OUT_FILE.stat().st_size / 1024:.1f}KB")
    print(f"  종목 {len(codes)}개 중 업종코드 확보 {len(have)}개 · 생성 {data.get('generated_at')}")

    # 자릿수 분포 — 접두어 매칭 규칙이 왜 필요한지 여기서 보인다
    lengths = {}
    for v in have:
        n = len(str(v["industry_code"]))
        lengths[n] = lengths.get(n, 0) + 1
    print(f"  코드 자릿수: {' · '.join(f'{k}자리 {v}개' for k, v in sorted(lengths.items()))}")

    # 2자리 대분류별 상위 묶음
    groups = {}
    for v in have:
        groups.setdefault(str(v["industry_code"])[:2], []).append(v)
    top = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]
    print(f"  2자리 대분류 {len(groups)}종 · 큰 순서: "
          + " · ".join(f"{k} {len(v)}개" for k, v in top))
    return 0


def build(resume: bool, limit: int) -> int:
    corp_map = dart_data._load_corp_map()
    if not corp_map:
        print("data/corp_code.json 이 없다 — 먼저 scripts/build_corp_code.py 를 돌린다")
        return 1

    done = {}
    if resume and OUT_FILE.exists():
        done = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("map", {})
        print(f"이어서 진행 — 이미 {len(done)}개 있음")

    targets = [c for c in sorted(corp_map) if c not in done]
    if limit:
        targets = targets[:limit]
    print(f"대상 {len(targets)}개 · 예상 {len(targets) * 0.12 / 60:.1f}분\n")

    started = time.time()
    failed = 0
    for i, code in enumerate(targets, 1):
        try:
            info = dart_data.fetch_company(code)
            done[code] = {
                "name": info.get("stock_name") or info.get("corp_name") or "",
                "industry_code": str(info.get("industry_code") or ""),
                "fiscal_month": str(info.get("fiscal_month") or ""),
                "market": info.get("market") or "",
            }
        except Exception as error:                      # 한 종목 실패로 전체를 멈추지 않는다
            failed += 1
            done[code] = {"name": "", "industry_code": "", "error": str(error)[:80]}

        if i % 200 == 0 or i == len(targets):
            speed = i / max(0.001, time.time() - started)
            left = (len(targets) - i) / max(0.001, speed)
            print(f"  {i}/{len(targets)} · 실패 {failed} · 남은 시간 {left / 60:.1f}분")
            _save(done)                                  # 중간 저장 — 끊겨도 --resume 로 잇는다

    _save(done)
    print(f"\n완료 — {len(done)}개 · 실패 {failed}개 · {(time.time() - started) / 60:.1f}분")
    return show_status()


def _save(mapping: dict) -> None:
    have = sum(1 for v in mapping.values() if v.get("industry_code"))
    payload = {
        "generated_at": now_kst(),
        "source": "DART 기업개황(company.json)의 표준산업분류 induty_code",
        "total": len(mapping),
        "with_industry": have,
        "map": mapping,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="DART 표준산업분류 맵 생성")
    parser.add_argument("--check", action="store_true", help="만들어진 파일 상태만 본다")
    parser.add_argument("--resume", action="store_true", help="하다 만 것을 이어서 한다")
    parser.add_argument("--limit", type=int, default=0, help="앞의 N개만 (시험용)")
    args = parser.parse_args()

    if args.check:
        return show_status()
    return build(args.resume, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
