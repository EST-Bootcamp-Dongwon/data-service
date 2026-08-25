"""경로 기준점을 한 곳에서 정한다.

**왜 필요한가.** 지금 레포는 저장소 루트를 파일마다 다르게 계산한다 —
`app/repositories/krx_store.py:36` 은 `parents[2]`, `app/repositories/industry_store.py:38` 은
`parent.parent.parent`, 옛 루트 `main.py` 는 `parent` 한 번이었다. 전부 같은 폴더를
가리키지만 **깊이가 파일 위치에 묶여 있어서**, 파일을 옮기는 순간 조용히 다른 곳을 가리킨다.

실제로 그 사고가 엔트리포인트 이동에서 일어난다. `main.py` 가 루트에 있을 때
`Path(__file__).parent / "static"` 은 맞지만, `app/main.py` 로 옮기면 `app/static` 을 찾는다.
정적 파일은 라우터가 아니라서 OpenAPI 스냅샷에도 잡히지 않고, 서버는 정상 기동한 뒤
화면만 404 가 된다.

그래서 기준점을 여기 하나로 모은다. 새 코드는 이 상수를 쓴다.
(기존 모듈들의 `parents[2]` 는 지금 동작하므로 함께 건드리지 않는다 —
저장 계층을 Postgres 로 옮길 때 그 파일들을 열면서 같이 정리한다.)
"""

from __future__ import annotations

from pathlib import Path

# 이 파일은 <루트>/app/core/paths.py 이므로 parents[2] 가 저장소 루트다.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# 앱이 읽는 정적 자산·데이터 폴더. 전부 루트 기준이다.
STATIC_DIR: Path = PROJECT_ROOT / "static"
DATA_DIR: Path = PROJECT_ROOT / "data"

# 갱신 사슬이 부르는 스크립트 폴더 (ADR-DS-0016 · ADR-DS-0017).
# ⚠️ **이미지 안에는 없다.** `.dockerignore` 가 `scripts/` 를 뺀다 — 컨테이너에서
#    화면 갱신 버튼이 막히는 이유가 이것이고, `refresh_job.capability()` 가 그 사실을 말한다.
SCRIPTS_DIR: Path = PROJECT_ROOT / "scripts"

# 실습 아카이브 — M1 리팩터링 이전 화면을 원본 그대로 얼려 둔 수업 자료.
# 강사님 원본(`lecture/`)은 서브모듈이라 내 파일을 넣으면 pull 때 충돌하므로 여기 따로 둔다.
PRACTICE_DIR: Path = PROJECT_ROOT / "실습"
