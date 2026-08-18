"""invoke 태스크 — **검증의 정본** (ADR-CT-0006).

CI 는 이 명령을 호출만 한다. Makefile·tox·check.py 를 따로 두지 않는다
(둘을 이중 유지하면 OS 사이에서 조용히 갈라진다).

    pip install invoke
    invoke check          전체 검증
    invoke docs-check     문서 검증

개별 단계만 돌리고 싶을 때: `invoke lint` · `invoke test` · `invoke export`.
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
  """코드 검증 정본: lint → test → export → build."""
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
