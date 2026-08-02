#!/usr/bin/env python3
"""08강 용어집(`voca.md`) → `data/glossary.json` 빌드 스크립트 (명세 §5.6 glossary)

`build_industry_map.py` 와 같은 자리다 — **로컬에서 한 번 돌려 결과 파일을 커밋**하고,
런타임은 그 파일만 읽는다. 강의 자료(`learning/08-investment-analysis/lecture/`)는
서브모듈이라 배포 번들에 실리지 않기 때문이다.

    python3 scripts/build_glossary.py
    python3 scripts/build_glossary.py --source /경로/voca.md

`voca.md` 는 절(## / ###)마다 마크다운 표가 붙어 있고, 표의 열 구성이 절마다 조금씩 다르다.

    | 용어 | 한자/약어 | 영어 | 아주 쉬운 뜻 | 실전 예시 |     ← 대부분
    | 용어 | 한자 | 영어 | 아주 쉬운 뜻 | 실전 예시 |          ← 3장
    | 비교 쌍 | A | B | 핵심 차이 |                            ← 11장 (비교표)

그래서 **머리행을 읽어 열을 매핑**한다. 위치로 자르면 절이 하나 바뀔 때마다 조용히 어긋난다.
알 수 없는 표는 건너뛰고 그 사실을 요약에 적는다 — 지어내지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (BASE_DIR.parents[1] / "learning" / "08-investment-analysis"
                  / "lecture" / "docs" / "voca.md")
OUTPUT = BASE_DIR / "data" / "glossary.json"
KST = timezone(timedelta(hours=9))

# 머리행 낱말 → 우리 필드. 표기가 절마다 흔들려서(한자 / 한자·약어) 부분일치로 잡는다.
#
# ⚠️ 실측 — voca.md 안의 표는 한 모양이 아니다.
#      | 항목 | 내용 |                                  93개 (한자 어원 절)
#      | 용어 | 한자/약어 | 영어 | 아주 쉬운 뜻 | 실전 예시 |  14개 (본문 용어절)
#      | 비교 쌍 | A | B | 핵심 차이 |                     2개 (혼동 용어 비교)
#    처음엔 앞의 두 낱말만 받아 **뜻이 빈 항목이 332개** 나왔다. 머리행을 다 세어 보고 고쳤다.
HEADER_MAP = [
    (("용어", "지표", "항목", "구분", "비교"), "term"),
    (("한자", "약어"), "hanja"),
    (("영어", "영문", "english"), "english"),
    (("쉬운 뜻", "뜻", "의미", "정의", "설명", "내용", "핵심 차이", "차이"), "meaning"),
    (("실전 예시", "예시", "예"), "example"),
]

# 표에 딸려오는 마크다운 장식 제거
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
EMPHASIS_RE = re.compile(r"[*`]+")


def _clean(cell: str) -> str:
    text = LINK_RE.sub(r"\1", cell or "")
    text = EMPHASIS_RE.sub("", text).strip()
    return "" if text in ("-", "—", "N/A") else text


def _map_header(cells: List[str]) -> Optional[Dict[int, str]]:
    """머리행 → {열 번호: 필드}. '용어' 열이 없으면 용어표가 아니라고 본다."""
    mapping: Dict[int, str] = {}
    for index, cell in enumerate(cells):
        lowered = _clean(cell).lower()
        for needles, field in HEADER_MAP:
            if any(needle in lowered for needle in needles) and field not in mapping.values():
                mapping[index] = field
                break
    return mapping if "term" in mapping.values() else None


SUBSECTION_RE = re.compile(r"^(?P<name>[^(（]+)[(（]?(?P<hanja>[^)）]*)[)）]?\s*$")


def _flush_attributes(subsection: str, section: str, rows: List[tuple],
                      terms: List[Dict]) -> None:
    """`| 항목 | 내용 |` 어원표 한 덩어리 → 용어 하나.

    이 표는 **용어 목록이 아니라 한 용어의 속성표**다. 행마다 '한자'·'읽기'·'어원'·'관련어'
    가 들어 있어, 그대로 훑으면 `한자` 라는 이름의 용어가 90여 개 생긴다 (실측).
    그래서 소절 제목(`### 금리 (金利)`)을 용어로 삼고 행들을 속성으로 접는다.
    """
    if not subsection or not rows:
        return
    matched = SUBSECTION_RE.match(subsection)
    name = _clean(matched.group("name")) if matched else subsection
    hanja = _clean(matched.group("hanja")) if matched else ""
    attributes = {label.strip("*").strip(): value for label, value in rows if label and value}
    meaning = attributes.get("어원") or attributes.get("뜻") or attributes.get("의미") or ""
    terms.append({
        "term": name,
        "hanja": attributes.get("한자") or hanja,
        "english": "",
        "meaning": meaning,
        "example": attributes.get("관련어", ""),
        "reading": attributes.get("읽기", ""),
        "attributes": attributes,
        "section": section,
        "subsection": subsection,
        "kind": "어원",
    })


def parse(text: str) -> Dict:
    """voca.md 전문 → {terms, sections, skipped}"""
    section = ""
    subsection = ""
    header: Optional[Dict[int, str]] = None
    attribute_table = False
    attribute_rows: List[tuple] = []
    terms: List[Dict] = []
    skipped: List[str] = []
    seen: Dict[str, int] = {}

    def close_table() -> None:
        nonlocal header, attribute_table, attribute_rows
        if attribute_table:
            _flush_attributes(subsection, section, attribute_rows, terms)
        header, attribute_table, attribute_rows = None, False, []

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("### "):
            close_table()
            subsection = _clean(stripped[4:])
            continue
        if stripped.startswith("## "):
            close_table()
            section = _clean(stripped[3:])
            subsection = ""
            continue
        if not stripped.startswith("|"):
            close_table()                       # 표가 끝났다
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells if c):
            continue                            # 구분선 `|---|---|`

        if header is None:
            header = _map_header(cells)
            if header is None:
                label = f"{section} › {subsection}".strip(" ›")
                if label and label not in skipped:
                    skipped.append(label)
            else:
                # `| 항목 | 내용 |` 두 칸짜리는 용어 목록이 아니라 **한 용어의 속성표**다
                attribute_table = (set(header.values()) == {"term", "meaning"}
                                   and "항목" in _clean(cells[0]))
                attribute_rows = []
            continue

        if attribute_table:
            attribute_rows.append((_clean(cells[0]),
                                   _clean(cells[1]) if len(cells) > 1 else ""))
            continue

        row = {field: _clean(cells[index]) if index < len(cells) else ""
               for index, field in header.items()}
        # 매핑되지 않은 열도 버리지 않는다 — '비교 쌍' 표의 A·B 처럼 뜻을 담고 있을 수 있다
        extra = [_clean(cell) for index, cell in enumerate(cells)
                 if index not in header and _clean(cell)]
        if extra:
            row["extra"] = " / ".join(extra)
        term = row.get("term", "")
        if not term:
            continue

        # 같은 용어가 여러 절에 나오면 **덮어쓰지 않고** 절 이름을 붙여 둘 다 남긴다.
        key = term
        if key in seen:
            seen[key] += 1
            row["duplicate_of"] = key
        else:
            seen[key] = 1
        row.update(section=section, subsection=subsection)
        terms.append(row)

    return {"terms": terms, "skipped": skipped,
            "sections": sorted({t["section"] for t in terms if t["section"]})}


def main() -> int:
    parser = argparse.ArgumentParser(description="08강 voca.md → data/glossary.json")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="voca.md 경로")
    parser.add_argument("--output", default=str(OUTPUT), help="결과 JSON 경로")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"✗ 원본을 찾지 못했습니다: {source}")
        print("  강의 서브모듈이 없으면 --source 로 voca.md 경로를 직접 주세요.")
        return 1

    parsed = parse(source.read_text(encoding="utf-8"))
    payload = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "08강 투자분석 용어집 (voca.md)",
        "source_path": str(source),
        "total": len(parsed["terms"]),
        "sections": parsed["sections"],
        "skipped_tables": parsed["skipped"],
        "terms": parsed["terms"],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    size_kb = output.stat().st_size / 1024
    print(f"✓ {payload['total']}개 용어 · {len(payload['sections'])}개 절 · "
          f"{size_kb:.1f}KB → {output.relative_to(BASE_DIR)}")
    if parsed["skipped"]:
        print(f"  건너뛴 표 {len(parsed['skipped'])}개 (용어표가 아님): "
              f"{', '.join(parsed['skipped'][:5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
