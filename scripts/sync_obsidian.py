#!/usr/bin/env python3
"""문서를 Obsidian 볼트로 미러한다 (ADR-DS-0013).

    python3 scripts/sync_obsidian.py            # 미러한다
    python3 scripts/sync_obsidian.py --check    # 무엇이 바뀌는지만 보고 쓰지 않는다
    python3 scripts/sync_obsidian.py --quiet    # 훅에서 부를 때 (요약 한 줄만)

**한 방향이다.** 이 레포가 정본이고 볼트는 미러다. 볼트 쪽에서 고친 것은 다음 실행에
덮인다 — 그래서 미러본 첫머리에 그 경고를 박아 넣는다.

왜 `post-commit` 훅에서 도나
    커밋된 것만 미러되므로 볼트 내용이 항상 어떤 커밋과 대응한다. 작업 중인 초안이
    볼트로 새지 않는다. `invoke check` 에 묶지 않은 이유는 그 명령이 CI·다른 머신에서도
    도는 **검증** 명령이라, 레포 밖 파일시스템을 바꾸면 신뢰할 수 없게 되기 때문이다.

볼트가 없으면(다른 머신·CI) 조용히 건너뛴다. 종료 코드는 0 이다 —
훅이 커밋을 막지 않아야 한다.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 볼트 위치. 환경변수가 먼저이고, 없으면 실측된 기본값을 쓴다.
# ⚠️ WSL 에서 보이는 경로다. 윈도우 쪽 경로(`C:\Users\...`)와 같은 폴더를 가리킨다.
DEFAULT_VAULT = Path("/mnt/c/Users/kik32/내 드라이브/Master_Obsidian")
MIRROR_SUBDIR = Path("30_Projects/data-service")

# ── 무엇을 미러하나 (ADR-DS-0013 §3) ─────────────────────────────────────────
# 넘겨줄 때 읽혀야 하는 것만이다. 코드는 미러하지 않는다 —
# 볼트는 문서 보관소이고, 코드는 이미 GitHub·GitLab 두 원격에 이중으로 있다.
MIRROR_FILES = [
    "README.md",
    "AGENTS.md",
    "세션-시작-프롬프트.md",
    "docs/README.md",
]
MIRROR_GLOBS = [
    "docs/decisions/*.md",
]

# 미러하지 않는 것 — 이유는 ADR-DS-0013 §3 의 표에 있다.
#   docs/md/            옛 명세서를 버전별로 얼려 둔 사본 (지금 규약과 어긋난다)
#   docs/openapi.json   생성물 (`invoke openapi` 로 언제든 다시 나온다)
#   docs/승격/          개인 작업 메모

KST = timezone(timedelta(hours=9))

# 미러본임을 밝히는 머리말. **정본 경로를 함께 적는다** —
# 볼트에서 이 파일을 연 사람이 어디를 고쳐야 하는지 바로 알 수 있어야 한다.
BANNER = """> [!warning] 자동 미러본입니다 — 여기서 고친 것은 다음 동기화에 덮입니다
> 정본은 `projects/data-service/{source}` 입니다.
> 이 파일은 `scripts/sync_obsidian.py` 가 커밋 시점마다 다시 씁니다 (ADR-DS-0013).
"""


def _vault_root() -> Path:
    """볼트 경로를 정한다. 환경변수 `OBSIDIAN_VAULT` 가 기본값을 이긴다."""
    raw = os.getenv("OBSIDIAN_VAULT")
    return Path(raw) if raw else DEFAULT_VAULT


def _git(*args: str) -> str:
    """git 을 부르고 결과를 돌려준다. 실패하면 빈 문자열이다 (미러는 계속돼야 한다)."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _collect() -> list[Path]:
    """미러 대상 파일을 레포 상대경로로 모은다."""
    found: list[Path] = []
    for name in MIRROR_FILES:
        path = PROJECT_ROOT / name
        if path.exists():
            found.append(Path(name))
    for pattern in MIRROR_GLOBS:
        for path in sorted(PROJECT_ROOT.glob(pattern)):
            found.append(path.relative_to(PROJECT_ROOT))
    return found


def _title_of(text: str, fallback: str) -> str:
    """문서의 첫 h1 을 제목으로 쓴다. 없으면 파일명이다."""
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def _yaml_str(value: str) -> str:
    """YAML 스칼라로 안전하게 쓴다.

    ⚠️ ADR 제목이 `ADR-DS-0012: 수집 범위를...` 처럼 **콜론을 품는다.**
    그대로 쓰면 YAML 이 매핑으로 읽으려다 깨지고, Obsidian 이 frontmatter 를
    통째로 무시한다(속성 패널이 비어 보인다). 그래서 특수문자가 있으면 인용한다.
    """
    if any(ch in value for ch in ':#[]{}&*!|>%@`"\'') or value.strip() != value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _tags_for(rel: Path) -> list[str]:
    """볼트 검색용 태그. 최소한으로 유지한다 (`Metadata Rules` 의 권고)."""
    tags = ["data-service"]
    if rel.parts[:2] == ("docs", "decisions"):
        tags.append("adr")
    elif rel.name == "README.md" and len(rel.parts) == 1:
        tags.append("readme")
    return tags


def _render(rel: Path, stamp: str, commit: str) -> str:
    """원본에 frontmatter 와 경고 머리말을 붙여 미러본 내용을 만든다."""
    text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")

    # 원본에 이미 frontmatter 가 있으면 두 개가 겹친다. 이 레포 문서에는 없지만,
    # 생기면 조용히 깨지는 대신 그대로 두고 머리말만 붙인다.
    has_front = text.startswith("---\n")

    title = _title_of(text, rel.stem)
    tags = ", ".join(_tags_for(rel))
    banner = BANNER.format(source=rel.as_posix())

    if has_front:
        return f"{text.split('---', 2)[0]}---{text.split('---', 2)[1]}---\n\n{banner}\n{text.split('---', 2)[2].lstrip()}"

    front = (
        "---\n"
        f"title: {_yaml_str(title)}\n"
        "area: Projects\n"
        "project: data-service\n"
        "status: active\n"
        f"tags: [{tags}]\n"
        f"source: {rel.as_posix()}\n"
        f"synced: {stamp}\n"
        f"commit: {commit or 'unknown'}\n"
        "---\n\n"
    )
    return f"{front}{banner}\n{text}"


def _index(rels: list[Path], stamp: str, commit: str, subject: str) -> str:
    """볼트 진입점 노트. 여기도 자동 생성이다 (ADR-DS-0013 §6)."""
    adrs = [r for r in rels if r.parts[:2] == ("docs", "decisions") and r.name != "README.md"]
    others = [r for r in rels if r not in adrs]

    lines = [
        "---",
        "title: data-service",
        "area: Projects",
        "project: data-service",
        "status: active",
        "tags: [data-service, index]",
        f"synced: {stamp}",
        f"commit: {commit or 'unknown'}",
        "---",
        "",
        "# data-service",
        "",
        "> **흩어져 있는 국내 투자 정보를 한 곳에 모아 두는 수집·보관·조회 서비스.**",
        "> 시세·통계는 정형 API 로, 공시·뉴스·커뮤니티는 수집으로 모으고,",
        "> **기업과 산업**을 축으로 되찾는다.",
        "",
        "> [!warning] 이 폴더는 자동 미러본입니다",
        "> 정본은 로컬 `projects/data-service` 이고, 여기서 고친 것은 다음 커밋에 덮입니다.",
        "> 메모를 남기려면 이 폴더 **밖**에 노트를 만들고 링크하세요 (ADR-DS-0013).",
        "",
        "## 이 폴더에 무엇이 있나",
        "",
        f"- 마지막 동기화 — `{stamp}`",
        f"- 대응 커밋 — `{commit or 'unknown'}` {subject}".rstrip(),
        f"- 미러된 문서 — {len(rels)}개",
        "",
        "## 결정 이력 (ADR)",
        "",
        "이 프로젝트가 **왜 지금 모양인지**는 전부 여기에 있다.",
        "",
    ]
    for rel in adrs:
        title = _title_of((PROJECT_ROOT / rel).read_text(encoding="utf-8"), rel.stem)
        lines.append(f"- [[{rel.stem}|{title}]]")

    lines += ["", "## 그 밖의 문서", ""]
    for rel in others:
        title = _title_of((PROJECT_ROOT / rel).read_text(encoding="utf-8"), rel.stem)
        lines.append(f"- [[{rel.stem}|{title}]]")

    lines += [
        "",
        "## 원격",
        "",
        "- GitHub — `EST-Bootcamp-Dongwon/data-service`",
        "- GitLab — `dev-dongwon05253/est-data-service` (push 가 곧 Vercel 배포다)",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="문서를 Obsidian 볼트로 미러한다 (ADR-DS-0013)")
    parser.add_argument("--check", action="store_true", help="쓰지 않고 무엇이 바뀌는지만 본다")
    parser.add_argument("--quiet", action="store_true", help="요약 한 줄만 낸다 (훅용)")
    args = parser.parse_args()

    vault = _vault_root()
    if not vault.is_dir():
        # 다른 머신이거나 드라이브가 안 붙었다. 훅이 커밋을 막으면 안 되므로 조용히 나간다.
        if not args.quiet:
            print(f"[obsidian] 볼트 없음 → 건너뜀: {vault}")
            print("[obsidian]   다른 곳에 있다면 OBSIDIAN_VAULT 로 알려 준다.")
        return 0

    target_root = vault / MIRROR_SUBDIR
    rels = _collect()
    if not rels:
        print("[obsidian] 미러할 문서가 없다.")
        return 0

    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    commit = _git("rev-parse", "--short", "HEAD")
    subject = _git("log", "-1", "--pretty=%s")

    written, unchanged = 0, 0
    for rel in rels:
        body = _render(rel, stamp, commit)
        dest = target_root / rel
        # `synced` 줄만 다른 것은 바뀐 것으로 치지 않는다 — 안 그러면 매 커밋마다
        # 전 파일이 '변경됨'으로 나와 진짜 변경이 묻힌다.
        old = dest.read_text(encoding="utf-8") if dest.exists() else ""
        if _strip_stamp(old) == _strip_stamp(body):
            unchanged += 1
            continue
        written += 1
        if args.check:
            print(f"[obsidian] 바뀜: {rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")

    if not args.check:
        index_path = target_root / "data-service.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(_index(rels, stamp, commit, subject), encoding="utf-8")

    verb = "바뀔 것" if args.check else "미러함"
    print(f"[obsidian] {verb} {written}개 · 그대로 {unchanged}개 → {target_root}")
    return 0


def _strip_stamp(text: str) -> str:
    """`synced`·`commit` 줄을 뺀 내용. 실제 변경만 세기 위한 것이다."""
    return re.sub(r"^(synced|commit):.*$", "", text, flags=re.MULTILINE)


if __name__ == "__main__":
    sys.exit(main())
