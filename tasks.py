"""invoke 태스크 — **검증의 정본** (ADR-CT-0006).

CI 는 이 명령을 호출만 한다. Makefile·tox·check.py 를 따로 두지 않는다
(둘을 이중 유지하면 OS 사이에서 조용히 갈라진다).

    pip install invoke
    invoke check          전체 검증
    invoke docs-check     문서 검증

개별 단계만 돌리고 싶을 때: `invoke lint` · `invoke test` · `invoke export`.
`invoke preflight` 는 venv 가 pyproject 를 따라잡았는지만 본다(check 가 먼저 부른다).
"""

from pathlib import Path
from shutil import which

from invoke import task

PROJECT_ROOT = Path(__file__).resolve().parent

# `uv` 는 이 프로젝트의 의존성 정본 도구다(AGENTS.md). venv 안에 설치돼 있으면 그것을 쓰고,
# 없으면 시스템 것을 찾는다.
_VENV_UV = PROJECT_ROOT / ".venv" / "bin" / "uv"
_VENV_UV_WIN = PROJECT_ROOT / ".venv" / "Scripts" / "uv.exe"


def _uv() -> str | None:
  """쓸 수 있는 uv 실행 파일 경로. 없으면 None."""
  for candidate in (_VENV_UV, _VENV_UV_WIN):
    if candidate.exists():
      return str(candidate)
  return which("uv")


@task
def lint(c):
  """ruff — 정적 검사.

  **`ruff format --check` 는 일부러 넣지 않았다** (ADR-DS-0005).
  이 레포는 인라인 주석을 세로로 맞춰 둔 교육용 코드이고, ruff format 은 그 정렬을
  보존하지 못한다 — 실측하면 93개 파일 15,442줄이 바뀌는데 거의 전부가
  `title="...",          # 설명` → `title="...",  # 설명` 로 주석을 뭉개는 변경이다.
  기능은 하나도 안 바뀌면서 읽기 좋은 정렬과 git blame 을 잃는다.

  포맷 일관성 대신 `ruff check`(lint)로 품질 게이트를 건다. 이쪽은 전부 통과 상태다.
  """
  c.run("ruff check .")


# `pyproject.toml` 의 `[project.dependencies]` 중 **손으로 추린** 목록이다. 자동 유도가
# 아니다 — 배포 이름과 import 이름이 다르고(`sqlalchemy[asyncio]` → `sqlalchemy`),
# extra 가 끌고 오는 것(greenlet)까지 세면 목록이 오히려 부정확해진다.
# 의존성을 더하면 **여기도 함께 손본다.**
#
# `yfinance` 를 넣어 둔 이유: 이것만 빠지면 서버는 뜨지만 라우터 3종·엔드포인트 10개가
# 조용히 사라진다(app/main.py:58-64 가 흡수한다). 그 상태로 계약 스냅샷을 돌리면
# "계약이 줄었다"는 실패가 나는데, 원인이 의존성이라는 것이 화면에 안 보인다.
REQUIRED_IMPORTS = ("fastapi", "pydantic", "numpy", "yfinance", "sqlalchemy", "asyncpg")


@task
def preflight(c):
  """검사를 돌리기 전에 `.venv` 가 `pyproject.toml` 을 따라잡았는지 본다.

  `invoke check` 에는 설치 단계가 **일부러** 없다 — 있으면 검증이 조용히 환경을 바꾼다.
  그래서 의존성을 더한 직후에는 venv 가 뒤처져 있는 것이 정상이고, 그 상태로 검사를
  돌리면 lint 를 지나 pytest 수집 단계에서 죽는다.

  ⚠️ **죽는 메시지 자체는 지금 명확하다** — 실측하면 `tests/test_db.py:24` 를 가리키며
  `ModuleNotFoundError: No module named 'sqlalchemy'` 가 뜬다. 이 태스크가 버는 것은
  **시간과 다음 동작**이다: ruff 를 먼저 돌리지 않고 즉시 멈추고, 트레이스백 대신
  "무엇을 실행하라"를 준다.

  ⚠️ S4(읽기 어댑터)에서는 이야기가 달라진다. `app/` 이 엔진 계층을 import 하기 시작하면
  같은 결손이 `conftest.py:24-35` 의 앱 로딩 폴백을 거치면서 **원인에서 먼 메시지**가 된다
  (`main.py` 가 다시 `app.main` 을 부르므로 두 번째 예외는 안 잡힌다). 그때 이 가드의
  값이 커진다.
  """
  import importlib.util

  missing = [name for name in REQUIRED_IMPORTS if importlib.util.find_spec(name) is None]
  if missing:
    # 막다른 길로 만들지 않는다 — 무엇을 해야 하는지까지 알려준다.
    # uv 경로는 `_uv()` 가 이미 OS 별로 찾아 둔다. 여기서 다시 하드코딩하지 않는다.
    uv = _uv() or "uv"
    raise SystemExit(
      f".venv 에 없는 런타임 모듈이 있다: {', '.join(missing)}\n"
      "pyproject.toml 을 고친 뒤 아직 설치하지 않은 상태로 보인다. 먼저 맞춘다:\n"
      f"  {uv} sync --extra dev        (uv.lock 대로 venv 를 맞춘다)\n"
      "그 다음 `invoke check` 를 다시 실행한다."
    )

  # ── Obsidian 미러 훅 (ADR-DS-0013) ───────────────────────────────────
  # **막지 않는다.** 훅이 없어도 검증은 통과해야 한다 — 미러는 문서 편의이지
  # 코드 품질 게이트가 아니다. 다만 없다는 사실이 조용히 묻히면 볼트가 낡으므로,
  # 여기서 한 줄 알려 준다 (훅은 clone 마다 새로 설치해야 한다).
  if not _hook_path().exists():
    print("[preflight] Obsidian 미러 훅이 없다 → `invoke hooks` 로 설치한다 (ADR-DS-0013).")


@task
def test(c):
  """pytest — HTTP 계약 스냅샷 + 엔트리포인트·정적 마운트 검증.

  외부 API 를 부르지 않으므로 네트워크 없이 돈다.
  계약을 의도적으로 바꿨다면 `pytest --snapshot-update` 후 diff 를 커밋에 남긴다.
  """
  c.run("pytest")


@task
def export(c):
  """`requirements.txt` 재생성 — 강사님 요건 3번.

  **이 파일은 생성물이다. 직접 편집하지 않는다.** 정본은 pyproject.toml + uv.lock 이다.

  `--no-emit-project` 를 빼면 `-e .` 가 섞여 Vercel 빌드가 자기 자신을 설치하려 든다.
  `--no-dev` 이므로 pytest·ruff 는 빠지고, scripts extra(matplotlib 40MB+)도 빠진다 —
  그 둘은 배포 번들에 있을 이유가 없다.
  """
  uv = _uv()
  if uv is None:
    # 막다른 길로 만들지 않는다 — 무엇을 해야 하는지까지 알려준다.
    raise SystemExit(
      "uv 를 찾지 못했다. 의존성 정본 도구이므로 먼저 설치한다.\n"
      "  .venv/bin/python -m pip install uv        (venv 안에만 설치 — 권장)\n"
      "  또는  pipx install uv\n"
      "설치 뒤 `invoke export` 를 다시 실행한다."
    )
  c.run(
    f"{uv} export --format requirements-txt "
    "--no-hashes --no-dev --no-emit-project --no-annotate --no-header "
    "-o requirements.txt"
  )


@task
def build(c):
  """Docker 이미지 빌드 + 용량 실측.

  용량을 매번 찍는다. 안 하면 "이미지 최소화"가 영원히 추정으로 남는다.
  """
  c.run("docker compose build")
  c.run('docker images --format "table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}"')


@task(name="format")
def format_code(c):
  """ruff format 을 **실제로 적용**한다 — 기본 검증 경로에는 없다.

  쓸 일이 생기면 단독 커밋으로 하고 `.git-blame-ignore-revs` 에 그 해시를 넣는다.
  근거는 ADR-DS-0005 를 읽는다.
  """
  c.run("ruff format .")


@task(name="check")
def check(c):
  """코드 검증 정본: preflight → lint → test → export → build.

  ⚠️ **순서가 뜻을 가진다.** `export` 가 `build` 보다 앞이라, 의존성을 더한 직후에도
  `uv.lock` → `requirements.txt` 가 먼저 갱신되고 나서 도커가 `uv sync --locked` 를 만난다.
  `invoke build` 를 단독으로 돌리면 그 순서가 없어서 낡은 락으로 하드 실패한다 —
  의존성을 고친 날에는 `invoke check` 로 돌린다.
  """
  preflight(c)
  lint(c)
  test(c)
  export(c)
  build(c)


def _hook_path() -> Path:
  """`post-commit` 훅이 놓일 자리.

  ⚠️ 이 레포는 **서브모듈**이라 `.git` 이 디렉터리가 아니라 gitdir 포인터 **파일**이다
  (`gitdir: ../../.git/modules/...`). 그래서 `.git/hooks` 를 그대로 쓰면 빗나간다.
  git 에게 물어보는 것이 유일하게 맞는 방법이다.
  """
  from subprocess import run
  out = run(["git", "rev-parse", "--git-dir"], cwd=PROJECT_ROOT,
            capture_output=True, text=True)
  git_dir = Path(out.stdout.strip()) if out.returncode == 0 else PROJECT_ROOT / ".git"
  if not git_dir.is_absolute():
    git_dir = (PROJECT_ROOT / git_dir).resolve()
  return git_dir / "hooks" / "post-commit"


@task
def hooks(c):
  """`post-commit` 훅을 설치한다 — 커밋할 때마다 문서를 Obsidian 볼트로 미러한다.

  ADR-DS-0013. 커밋된 것만 미러되므로 볼트 내용이 항상 어떤 커밋과 대응한다.

  ⚠️ **커밋을 절대 막지 않는다.** 미러가 실패해도 `|| true` 로 흘리고,
  볼트 폴더가 없으면(다른 머신) 스크립트가 조용히 건너뛴다.
  """
  hook = _hook_path()
  hook.parent.mkdir(parents=True, exist_ok=True)
  hook.write_text(
    "#!/bin/sh\n"
    "# data-service — 문서를 Obsidian 볼트로 미러한다 (ADR-DS-0013).\n"
    "# `invoke hooks` 가 설치한다. 커밋을 막지 않도록 실패는 흘린다.\n"
    'cd "$(git rev-parse --show-toplevel)" || exit 0\n'
    "python3 scripts/sync_obsidian.py --quiet || true\n",
    encoding="utf-8",
  )
  hook.chmod(0o755)
  print(f"post-commit 훅 설치 완료 → {hook}")
  print("  다음 커밋부터 문서가 볼트로 미러된다. 지금 한 번 돌려 보려면:")
  print("    python3 scripts/sync_obsidian.py --check")


@task(name="docs-check")
def docs_check(c):
  """문서 검증: 마크다운 → 링크(오프라인) → OpenAPI 내보내기.

  markdownlint 설정은 `.markdownlint-cli2.jsonc` 다. 설정이 없던 시절에는 기본값
  (영문 80자·compact 표)으로 돌아 **6,500건 넘게** 걸렸고, 그래서 이 게이트는
  "빨간불로 고정"돼 아무것도 잡아 주지 못했다. 지금은 0건에서 출발한다.
  """
  c.run('npx markdownlint-cli2 "docs/**/*.md" "README.md"')

  # --offline: 내부 링크만 본다. 외부 링크는 네트워크 상태에 따라 실패해
  # "검증이 원래 가끔 빨간불"이라는 나쁜 습관을 만든다. 주 1회 수동으로 돌린다.
  #
  # ⚠️ lychee 는 이 환경에 설치돼 있지 않다. 예전에는 그 자리에서 하드 실패해
  #    **링크 검사가 통째로 안 도는데도 그 사실이 안 보였다.** 없으면 아래
  #    폴백으로 최소한 "가리키는 파일이 실재하는가" 는 본다 — 그리고 무엇을
  #    못 하고 있는지 화면에 밝힌다 (환경 가드를 막다른 길로 만들지 않는다).
  if which("lychee"):
    c.run("lychee --offline docs/**/*.md README.md")
  else:
    print("[docs-check] lychee 없음 → 내부 링크만 파이썬 폴백으로 본다.")
    print("[docs-check]   폴백이 못 보는 것: 앵커(#절) 유효성 · 외부 URL.")
    print("[docs-check]   전부 보려면: cargo install lychee (또는 배포판 패키지)")
    _check_local_links()

  openapi(c)


def _check_local_links() -> None:
  """마크다운의 **상대 경로 링크**가 실재하는 파일을 가리키는지만 본다.

  lychee 가 없을 때의 폴백이다. 앵커(`#절`)와 외부 URL 은 보지 않는다 —
  볼 수 없는 것을 본 척하지 않는 편이 낫다.
  """
  import re
  import sys

  targets = [PROJECT_ROOT / "README.md", *sorted((PROJECT_ROOT / "docs").rglob("*.md"))]
  skip_dirs = {"md"}   # docs/md 는 얼려 둔 옛 명세서 사본이라 lint 대상에서 빠져 있다
  link_re = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

  broken: list[str] = []
  checked = 0
  for doc in targets:
    if any(part in skip_dirs for part in doc.relative_to(PROJECT_ROOT).parts):
      continue
    for target in link_re.findall(doc.read_text(encoding="utf-8")):
      # 외부 URL · 순수 앵커 · 메일은 폴백의 범위 밖이다
      if target.startswith(("http://", "https://", "#", "mailto:")):
        continue
      path = (doc.parent / target.split("#", 1)[0]).resolve()
      checked += 1
      if not path.exists():
        broken.append(f"  {doc.relative_to(PROJECT_ROOT)} → {target}")

  if broken:
    print(f"[docs-check] 깨진 상대 링크 {len(broken)}건:")
    print("\n".join(broken))
    sys.exit(1)
  print(f"[docs-check] 상대 링크 {checked}건 전부 실재 ✓")


@task
def openapi(c):
  """OpenAPI 스키마를 `docs/openapi.json` 으로 내보낸다.

  Swagger 는 `/docs` 가 이미 띄우지만(강사님 요건 1번), 파일로 떨궈 두면
  계약 변경이 git diff 에 남는다.
  """
  import json
  import sys

  sys.path.insert(0, str(PROJECT_ROOT))
  from app.main import app

  target = PROJECT_ROOT / "docs" / "openapi.json"
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
  )
  paths = len(app.openapi()["paths"])
  print(f"docs/openapi.json 갱신 — 경로 {paths}개")


# ==================================================
# 갱신 — 자료를 최신 거래일까지 따라잡힌다
# ==================================================
# ⚠️ **검증이 아니라 운영 명령이다.** `invoke check` 에 묶지 않는다 — 검증 명령이 외부 API 를
#    부르고 파일을 고치기 시작하면 그 명령을 더는 신뢰할 수 없다. `invoke hooks` 를 check 에
#    묶지 않은 것과 같은 이유다 (ADR-DS-0013).
#
# **왜 명령이 필요한가.** 갱신은 원래 스크립트 다섯 개를 **순서대로** 돌리는 일이었고, 그
# 순서는 README 여기저기와 사람 머릿속에만 있었다. 그래서 2026-08-01 이후 24일 동안 아무도
# 돌리지 않았고 배포본 스냅샷이 17거래일 뒤처졌다. 하나만 빼먹어도 조용히 어긋난다 —
# 예를 들어 `fetch_krx` 만 돌리면 로컬 화면은 최신인데 **배포본은 그대로 낡아 있다**
# (배포본이 읽는 것은 커밋되는 `market_snapshot.json.gz` 쪽이기 때문이다).
#
# 순서가 뜻을 가진다. 파생물 셋은 전부 `data/krx_cache.db` 를 읽으므로 **수집이 먼저**다.
# ⚠️ **사슬 정본은 여기가 아니다.** `app/services/refresh_job.py` 의 `STEPS` 가 정본이고,
#    이 명령과 화면 버튼(`POST /api/refresh/run`)이 **같은 정의**를 부른다 (ADR-DS-0017).
#    두 벌로 두면 순서·인자가 한쪽만 고쳐진 채로 오래 간다 — 이 사슬은 이미 한 번
#    "명령이 다섯 개라서" 24거래일을 밀렸다.


def _chain():
  """사슬 정본을 가져온다. import 를 함수 안에서 하는 이유는 `invoke lint` 처럼
  `app` 을 건드릴 필요가 없는 태스크까지 앱 계층을 끌고 오지 않기 위해서다."""
  import sys
  sys.path.insert(0, str(PROJECT_ROOT))
  from app.services import refresh_job

  return refresh_job


@task(help={
  "days": "수집할 거래일 수 (기본 30). 오래 쉬었으면 250 까지 올린다",
  "check": "아무것도 바꾸지 않고 지금 무엇이 얼마나 낡았는지만 잰다",
  "skip-pg": "Postgres 재적재를 건너뛴다",
})
def refresh(c, days=30, check=False, skip_pg=False):
  """자료를 최신 거래일까지 따라잡힌다 — 수집 → 축약본 → 스냅샷 → 마스터 → Postgres.

      invoke refresh                # 최근 30거래일 중 없는 날짜만 (약 4분)
      invoke refresh --days 250     # 오래 쉬었을 때
      invoke refresh --check        # 재기만 한다 (외부 호출 없음)
      invoke refresh --skip-pg      # DB 를 안 띄웠을 때

  ⚠️ **커밋하지 않는다.** 세 산출물(`market_snapshot.json.gz` · `krx_derived.json` ·
  `stock_master.json`)은 git 에 올라가고 push 가 곧 Vercel 배포다. 배포 시점은 사람이 정한다.

  ⚠️ **같은 사슬을 화면에서도 돌릴 수 있다** — 대시보드 '자료 갱신' 패널
  (ADR-DS-0017). 정의가 한 곳(`app/services/refresh_job.STEPS`)이라 둘은 갈라지지 않는다.
  """
  import sys
  job = _chain()
  python = sys.executable or "python3"
  mode = "확인" if check else "갱신"
  total = job.TOTAL_STEPS
  print(f"── 자료 {mode} — 전체 {total}단계 ──\n")

  for index, step in enumerate(job.STEPS, start=1):
    command = job.shell_command(step, days=days, check=check)

    # ── Postgres 단계만 상대를 먼저 확인한다 ──────
    # `krx_store.sync()` 는 SQLite 에만 쓴다(쓰기 경로 전환은 S8). 그래서 수집한 뒤 이것을
    # 돌리지 않으면 두 저장소가 갈리고, S5 로 스위치를 뒤집는 순간 화면이 옛 자료를 본다.
    if step.needs_db:
      if skip_pg:
        print(f"[{index}/{total}] {step.label} — 건너뜀 (--skip-pg)\n")
        continue
      url = job.host_database_url()
      if not job.postgres_reachable(url):
        print(f"[{index}/{total}] {step.label} — 건너뜀. DB 에 붙을 수 없다.")
        print("      띄우려면: docker compose --profile local-db up -d")
        print("      ⚠️ 안 띄우면 SQLite 만 최신이 되고 Postgres 는 그 자리에 남는다.\n")
        continue
      print(f"[{index}/{total}] {step.label}")
      c.run(f"DATABASE_URL={url} {python} {command}", pty=False)
      print()
      continue

    mark = " (git 에 올라간다 → 배포본에 반영됨)" if step.in_git else ""
    print(f"[{index}/{total}] {step.label}{mark}")
    c.run(f"{python} {command}", pty=False)
    print()

  if not check:
    # 이 명령은 자기 프로세스에서 화면을 띄우지 않으므로 캐시를 씻을 것이 없다.
    # 다만 **서버가 떠 있으면 그쪽 프로세스는 옛 사본을 들고 있다** — 화면 버튼으로 돌리면
    # 그 자리에서 씻기지만(ADR-DS-0017), 셸에서 돌린 경우에는 서버를 다시 띄워야 한다.
    print("ℹ️ 서버가 떠 있었다면 다시 띄우거나 대시보드의 '자료 갱신'을 한 번 눌러야")
    print("   그 프로세스가 들고 있던 스냅샷·색인 사본이 새것으로 바뀐다.\n")

  _refresh_summary(check)


def _refresh_summary(check: bool) -> None:
  """끝에 사람이 실제로 보는 두 숫자를 다시 찍는다 — 최신 거래일과 스냅샷 기준일.

  ⚠️ 두 줄의 문구는 화면 패널과 **같은 함수**에서 나온다 (`refresh_job.data_summary()`).
  따로 만들면 셸과 화면이 다른 말을 하게 되는데, 그 둘이 어긋나면 어느 쪽을 믿을지가
  사람에게 또 하나의 질문이 된다.
  """
  summary = _chain().data_summary()
  print("\n── 지금 상태 ──")
  print(f"  KRX 시세   : {summary['krx']['text']}")
  print(f"  시장 스냅샷 : {summary['snapshot']['text']}")

  if not check:
    print("\n⚠️ 배포본에 반영하려면 커밋·push 가 남았다 (push 가 곧 Vercel 배포다):")
    print("   git status --short && git add data/ && git commit && "
          "git push origin main && git push gitlab main")
