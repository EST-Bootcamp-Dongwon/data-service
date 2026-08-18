# syntax=docker/dockerfile:1
#
# 2단 빌드. 빌더에서 의존성을 깔고 런타임에는 결과물만 옮긴다.
# uv 를 pip 로 설치하지 않고 공식 이미지에서 바이너리만 가져온다 — pip 계층이
# 최종 이미지에 남지 않는다.

FROM python:3.12-slim AS builder

# uv 버전을 핀한다. 재현성이 목적이므로 latest 를 쓰지 않는다.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 의존성만 먼저 깐다. 앱 코드가 바뀌어도 이 계층은 캐시에서 재사용된다.
# --no-install-project: 앱 자체는 아래에서 COPY 로 넣는다(빌드 백엔드가 필요 없어진다).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

FROM python:3.12-slim AS runtime

# root 로 돌리지 않는다.
RUN useradd -m -u 1001 appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser main.py ./main.py
COPY --chown=appuser:appuser static ./static
COPY --chown=appuser:appuser 실습 ./실습

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=local

USER appuser
EXPOSE 8000

# 엔트리포인트 정본은 app.main:app 이다. 루트 main.py 는 같은 앱을 내보내는 shim 이라
# 어느 쪽으로 띄워도 같지만, 이미지에서는 정본을 쓴다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
