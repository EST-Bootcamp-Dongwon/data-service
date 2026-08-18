"""루트 진입점 — `app.main` 의 앱을 그대로 내보내는 얇은 껍데기.

**정본은 `app/main.py` 다.** 이 파일에는 앱을 조립하는 코드가 없고, 앞으로도 두지 않는다.

왜 남겨 두는가. 두 가지가 이 이름에 묶여 있다.

1. **강의 명령** — 수업과 README 가 `uvicorn main:app` 을 쓴다. 정본을 옮겼다고
   그 명령이 죽으면 실습 자료가 통째로 어긋난다.
2. **Vercel 대비책** — 루트에 `app` 을 내보내는 `main.py` 가 있으면 Vercel 이 설정 없이도
   진입점으로 잡는다. ⚠️ 다만 **실제 배포가 쓰는 것은 이 파일이 아니다** —
   `vercel.json` 의 `functions` 키와 `pyproject.toml` 의 `[tool.vercel] entrypoint` 는
   둘 다 `app/main.py` 를 가리킨다(`tests/test_entrypoint.py` 가 그 일치를 검사한다).
   즉 이 파일을 남기는 실질 이유는 1번 하나다.

정본을 둘로 두는 것과는 다르다. 여기서 만드는 앱이 없으므로 두 경로는 **같은 객체**를
가리키고, `tests/test_entrypoint.py::test_root_main_is_a_shim_of_app_main` 이 그 동일성을
검사한다. 이 파일에 로직이 끼어들면 그 테스트가 먼저 깨진다.
"""

from app.main import app

__all__ = ["app"]
