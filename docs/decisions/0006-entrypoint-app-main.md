# ADR-DS-0006: 엔트리포인트 정본은 `app.main:app`, 루트 `main.py` 는 shim 으로 남긴다

## 상태

채택됨 (2026-08-17)

## 맥락

공통 규칙과 상위 계약은 엔트리포인트 정본을 `app.main:app` 으로 규정하고,
01 검증본은 이를 "README·Dockerfile·Vercel 3중 불일치(P0-5)"라고 지적했다.

**실측하면 지적이 겨눈 상태가 아니었다.** `app/main.py` 도 `pyproject.toml` 도 없었고,
Dockerfile 자체가 없어 "Dockerfile CMD 와의 불일치"는 존재하지 않는 불일치였다.
실제로는 **일관되게 `main:app`** 이었다 — 루트 `main.py:81` 에 유일한 FastAPI 인스턴스,
`vercel.json` 의 함수 키도 `"main.py"`, README 4곳과 `.devcontainer/start.sh` 도 전부 그것.

그리고 그 상태는 사고가 아니라 **의도된 결정**이었다. `README.md:185` 가
"강의에서 쓰는 `uvicorn main:app` 명령을 그대로 쓰기 위해" 루트 `main.py` 를 유지한다고
근거를 명시해 두었다.

한편 순진하게 옮기면 **조용히 깨지는 지점**이 있었다. `main.py:99`·`:108` 이
`Path(__file__).parent` 로 정적 파일 폴더를 계산하는데, 이는 그 파일이 루트에 있을 때만
맞는 식이다. `app/` 아래로 옮기면 `app/static`·`app/실습` 을 찾게 된다.
정적 마운트는 라우터가 아니라 OpenAPI 문서에도 잡히지 않으므로,
**서버는 정상 기동하고 화면만 404 가 된다** — 배포하고 나서야 알게 되는 종류다.

## 결정

1. 정본은 **`app/main.py`** 다. `git mv` 로 옮겨 히스토리를 잇는다.
2. 루트 `main.py` 는 **`from app.main import app` 만 담은 shim** 으로 남긴다.
   앱을 조립하는 코드를 여기 두지 않는다.
3. 경로 기준점을 **`app/core/paths.py`** 한 곳에서 정한다.
   새 코드는 `paths.PROJECT_ROOT` 계열을 쓴다.
4. `pyproject.toml` 에 `[tool.vercel] entrypoint = "app/main.py"` 를 둔다.
5. `Dockerfile` 의 `CMD` 는 정본(`app.main:app`)을 쓴다.
6. 두 진입점이 **같은 객체**임을 테스트로 강제한다
   (`tests/test_entrypoint.py::test_root_main_is_a_shim_of_app_main`).

## 근거

- **shim 은 정본을 둘로 두는 것과 다르다.** 루트 파일이 앱을 만들지 않으므로
  `main:app` 과 `app.main:app` 은 같은 객체를 가리킨다. 갈라질 수 있는 여지가 없고,
  그래도 갈라지면 테스트가 먼저 깨진다.
- **두 가지가 그 이름에 묶여 있다.** ① 강의 명령 `uvicorn main:app` — 정본을 옮겼다고
  실습 자료가 어긋나면 손해다. ② Vercel 제로컨피그 — 루트에 `app` 을 내보내는
  `main.py` 가 있으면 별도 설정 없이 진입점으로 잡히고, `vercel.json` 의
  `functions."main.py"` 키도 이 파일을 가리킨다.
- **이동의 안전성을 증명하고 옮겼다.** 먼저 계약을 얼리고(엔드포인트 51개 + 쿼리
  파라미터 스냅샷 + 정적 마운트 + `/practice/` 마운트), 옮긴 뒤 같은 테스트가 통과하는지
  확인했다. 추가로 두 명령 각각으로 실제 uvicorn 을 띄워
  `/docs`·`/static/assets/app.css`·`/practice/`·화면·API 가 200 인지 확인했고,
  컨테이너에서도 같은 결과를 얻었다.

## 결과

- `uvicorn main:app` 과 `uvicorn app.main:app` 이 **둘 다 동작한다.** README·강의 자료를
  급히 고칠 필요가 없다.
- 경로 기준이 한 곳으로 모여, 앞으로 파일을 옮길 때 같은 사고가 재발하지 않는다.
- 다만 **기존 모듈들은 아직 제각각이다** — `app/repositories/krx_store.py:36` 은
  `parents[2]`, `industry_store.py:38` 은 `parent.parent.parent` 를 쓴다. 지금 동작하므로
  함께 건드리지 않았다. 저장 계층을 옮길 때(ADR-DS-0002) 그 파일들을 열면서 정리한다.
- `vercel.json` 의 `maxDuration: 60` 은 **아직 그대로다.** 플랫폼 한도가 300초여도
  이 설정이 있는 한 60초에서 끊긴다. 리서치 하네스가 H02 한 단계에 17~22초를 쓰므로
  (`app/core/parallel.py:5-20` 실측) 별도 판단이 필요하다.
