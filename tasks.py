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


@task(name="docs-check")
def docs_check(c):
  """문서 검증: 마크다운 → 링크(오프라인) → OpenAPI 내보내기."""
  c.run('npx markdownlint-cli2 "docs/**/*.md" "README.md"')
  # --offline: 내부 링크만 본다. 외부 링크는 네트워크 상태에 따라 실패해
  # "검증이 원래 가끔 빨간불"이라는 나쁜 습관을 만든다. 주 1회 수동으로 돌린다.
  c.run("lychee --offline docs/**/*.md README.md")
  openapi(c)


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
