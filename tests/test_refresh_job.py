"""자료 갱신 — 사슬 정본과 화면 실행기 (ADR-DS-0016 · ADR-DS-0017).

이 묶음이 붙드는 것은 **네 가지 약속**이다. 넷 다 어겨도 서버는 멀쩡히 뜨고,
어긴 사실이 한참 뒤에 엉뚱한 증상으로 드러나는 종류다.

1. **사슬은 한 곳에만 적혀 있다** — `refresh_job.STEPS`. `tasks.py` 가 다시 적으면
   순서·인자가 한쪽만 고쳐진 채로 오래 간다 (이 사슬은 이미 그래서 24거래일 밀렸다).
2. **커밋하지 않는다** — push 가 곧 Vercel 배포다. 브라우저 버튼이 배포까지 이어지면
   되돌릴 자리가 없다.
3. **못 하는 곳에서는 이유를 말하고 막는다** — 배포본·`REFRESH_API=off`·`scripts/` 부재.
   조용히 성공한 척하거나 아무 말 없이 500 을 내지 않는다.
4. **갱신 뒤에는 프로세스가 든 사본을 버린다** — 안 버리면 "갱신은 됐다는데 화면은
   그대로" 가 되고, 오류가 안 뜨므로 사람이 **버튼을 의심하게** 된다.

⚠️ **여기서 사슬을 실제로 돌리지 않는다.** 외부 API 를 부르고 파일을 고치는 명령이라
검증 경로에 두지 않는다는 것이 ADR-DS-0016 결정 2 다. 실제 실행은 손으로 잰다.
"""

from __future__ import annotations

import re

import pytest

from app.core import settings
from app.core.paths import PROJECT_ROOT
from app.services import refresh_job as job


# ==================================================
# 1. 사슬 — 정본이 하나인가
# ==================================================
def test_chain_has_five_steps_in_order():
    """다섯 칸이고 **순서가 뜻을 가진다** — 2~4 가 전부 수집 결과를 읽는다."""
    assert [step.key for step in job.STEPS] == [
        "fetch", "bundle", "snapshot", "master", "postgres"
    ]
    assert job.TOTAL_STEPS == 5


def test_only_the_first_step_takes_days():
    """`--days` 는 수집에만 뜻이 있다. 파생 단계는 캐시에 있는 것을 전부 본다."""
    with_days = [step.key for step in job.STEPS if any("{days}" in a for a in step.run_args)]
    assert with_days == ["fetch"]


def test_only_the_last_step_needs_a_database():
    """DB 를 못 띄운 셸에서도 1~4 는 끝까지 돌아야 한다 (S5 전이라 읽기 기본값이 아니다)."""
    assert [step.key for step in job.STEPS if step.needs_db] == ["postgres"]


def test_the_three_committed_outputs_are_flagged():
    """`in_git` 이 곧 "배포본에 닿나" 다. 이 표가 틀리면 화면이 거짓말을 한다."""
    assert [step.key for step in job.STEPS if step.in_git] == ["bundle", "snapshot", "master"]


def test_every_script_exists():
    """사슬 다섯 칸은 전부 있어야 순서가 성립한다."""
    for step in job.STEPS:
        assert (PROJECT_ROOT / step.script).is_file(), f"{step.script} 가 없다"


def test_tasks_py_does_not_redefine_the_chain():
    """`invoke refresh` 는 사슬을 **다시 적지 않고** 이 모듈을 부른다.

    두 벌이 되는 순간 한쪽만 고쳐진 채로 오래 간다 — 이 사슬이 실제로 겪은 고장이다.
    """
    source = (PROJECT_ROOT / "tasks.py").read_text(encoding="utf-8")
    assert "refresh_job" in source, "tasks.py 가 사슬 정본을 부르지 않는다"
    for step in job.STEPS:
        assert step.script not in source, (
            f"tasks.py 가 {step.script} 를 직접 적고 있다 — 사슬 정본은 refresh_job.STEPS 다"
        )


# ==================================================
# 2. 커밋하지 않는다 (ADR-DS-0016 결정 1)
# ==================================================
def test_the_chain_never_runs_git_or_deploys():
    """어느 칸도 git·배포를 부르지 않는다. push 가 곧 배포라 그 시점은 사람이 정한다."""
    for step in job.STEPS:
        words = re.findall(r"[a-z]+", " ".join([step.script, *step.run_args, *step.check_args]))
        for banned in job.FORBIDDEN_IN_CHAIN:
            assert banned not in words, f"{step.key} 가 `{banned}` 를 부른다"


def test_finished_run_tells_the_person_how_to_commit():
    """대신 **절차를 알려 준다.** 그 문구가 사라지면 산출물이 로컬에만 남는다."""
    source = (PROJECT_ROOT / "app/services/refresh_job.py").read_text(encoding="utf-8")
    assert "git push origin main" in source and "git push gitlab main" in source


# ==================================================
# 3. 명령 조립 — 셸 형태와 argv 가 같은 정의에서 나오는가
# ==================================================
@pytest.mark.parametrize("check", [False, True])
def test_shell_and_argv_carry_the_same_arguments(check):
    """`invoke refresh` 와 화면 버튼이 **같은 명령**을 돌린다."""
    for step in job.STEPS:
        shell = job.shell_command(step, days=30, check=check)
        executable, script, *arguments = job.argv(step, days=30, check=check)

        assert executable, "실행할 파이썬이 비어 있다"
        # argv 는 절대 경로를 쓴다 — 자식의 cwd 가 무엇이든 같은 파일을 연다.
        assert script == str(PROJECT_ROOT / step.script)
        # 셸 형태는 상대 경로다(사람이 그대로 복사해 쓴다). 인자는 둘이 같아야 한다.
        assert shell.split() == [step.script, *arguments]


def test_days_is_substituted_as_a_plain_number():
    """`{days}` 가 그대로 남으면 스크립트가 문자열을 int 로 못 바꿔 죽는다."""
    fetch = job.STEPS[0]
    assert job.step_args(fetch, days=250, check=False) == ["--days", "250"]
    assert "{days}" not in job.shell_command(fetch, days=250, check=False)


def test_check_mode_uses_the_scripts_own_confirmation_flags():
    """확인 전용 경로를 새로 짜지 않는다 — 각 스크립트가 이미 가진 모드를 잇는다."""
    assert [job.step_args(step, days=30, check=True) for step in job.STEPS] == [
        ["--status"], ["--check"], ["--check"], ["--check"], ["--verify-only"],
    ]


def test_argv_never_goes_through_a_shell():
    """인자에 셸 메타문자가 없다 — 그래야 `shell=True` 없이 그대로 넘길 수 있다."""
    for step in job.STEPS:
        for piece in job.argv(step, days=job.MAX_DAYS, check=False)[2:]:
            assert not set(piece) & set(";|&$`><\n"), f"{step.key} 인자에 셸 메타문자가 있다"


# ==================================================
# 4. 실행할 수 있는가 — 이유를 말하고 막는가
# ==================================================
def test_local_shell_can_run_it(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("REFRESH_API", raising=False)
    assert job.capability()["available"] is True


def test_deployment_is_blocked_with_a_followable_prescription(monkeypatch):
    """배포본에서는 못 한다. **감추지 않고** 대신 무엇을 할지 말한다 (ADR-DS-0016 §4)."""
    monkeypatch.setenv("APP_ENV", "vercel")
    ready = job.capability()
    assert ready["available"] is False
    assert ready["reason"]
    assert ready["hints"], "이유만 말하고 처방이 없으면 막다른 길이다"
    assert any("push" in hint for hint in ready["hints"])


def test_the_kill_switch_closes_only_the_run_path(monkeypatch):
    """`REFRESH_API=off` 는 실행만 닫는다. 상태 조회와 `invoke refresh` 는 그대로다."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("REFRESH_API", "off")
    ready = job.capability()
    assert ready["available"] is False
    assert "REFRESH_API" in ready["reason"]


def test_missing_scripts_folder_says_so(monkeypatch, tmp_path):
    """컨테이너 이미지에는 `scripts/` 가 없다. 그 사실이 화면에 그대로 나와야 한다."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(job, "SCRIPTS_DIR", tmp_path / "없는폴더")
    ready = job.capability()
    assert ready["available"] is False
    assert "scripts" in ready["reason"]
    assert any("dockerignore" in hint or "컨테이너" in hint for hint in ready["hints"])


# ==================================================
# 5. `REFRESH_API` 어휘 (settings.py §3-1)
# ==================================================
def test_refresh_api_defaults_to_on(monkeypatch):
    monkeypatch.delenv("REFRESH_API", raising=False)
    assert settings.refresh_api() == settings.ON
    assert settings.refresh_api_enabled() is True


@pytest.mark.parametrize("value, expected", [("on", True), ("OFF", False), (" off ", False)])
def test_refresh_api_reads_the_vocabulary(monkeypatch, value, expected):
    monkeypatch.setenv("REFRESH_API", value)
    assert settings.refresh_api_enabled() is expected


def test_unknown_refresh_api_value_raises_with_a_prescription(monkeypatch):
    """`store_backend()`·`app_env()` 와 같은 모양이다 — 오타를 조용히 삼키지 않는다.

    이 손잡이에서 거짓 음성은 방향이 나쁘다: `REFRESH_API=false` 를 `on` 으로 떨어뜨리면
    **껐다고 믿었는데 열려 있는** 상태가 된다.
    """
    monkeypatch.setenv("REFRESH_API", "false")
    with pytest.raises(ValueError) as caught:
        settings.refresh_api()
    message = str(caught.value)
    assert "REFRESH_API=on" in message and "REFRESH_API=off" in message


# ==================================================
# 6. 자식 환경 (settings.subprocess_env)
# ==================================================
def test_subprocess_env_passes_the_current_environment_through(monkeypatch):
    """인증키가 그대로 따라가야 자식이 뜬다. 골라 담으면 키 하나 빠졌을 때 401 로 죽는다."""
    monkeypatch.setenv("KRX_API_KEY", "지어낸값")
    child = settings.subprocess_env()
    assert child["KRX_API_KEY"] == "지어낸값"


def test_subprocess_env_ignores_empty_overrides():
    """빈 값으로 덮으면 `env()` 의 규약(빈 값 = 없음)과 어긋난다."""
    child = settings.subprocess_env(DATABASE_URL="", PYTHONUNBUFFERED="1")
    assert child["PYTHONUNBUFFERED"] == "1"
    assert child.get("DATABASE_URL") != ""


# ==================================================
# 7. 한 번에 하나 (사슬은 같은 파일을 만진다)
# ==================================================
class _FakeRunning:
    """돌고 있는 척하는 작업. 실제로 사슬을 돌리지 않고 겹침 방지만 확인한다."""

    state = "running"
    id = "20260101-000000"

    def snapshot(self) -> dict:
        return {"id": self.id, "state": self.state, "running": True}

    def cancel(self) -> bool:
        return False


def test_a_second_start_is_refused(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(job, "_current", _FakeRunning())
    with pytest.raises(job.RefreshBusy):
        job.start()


def test_start_is_refused_where_it_cannot_run(monkeypatch):
    monkeypatch.setenv("APP_ENV", "vercel")
    monkeypatch.setattr(job, "_current", None)
    with pytest.raises(job.RefreshUnavailable) as caught:
        job.start()
    assert caught.value.hints, "거절할 때도 무엇을 해야 하는지 말한다"


def test_days_is_clamped_before_it_reaches_a_subprocess():
    """라우터가 이미 막지만 실행기도 스스로 막는다 — 검사 한 겹에 기대지 않는다."""
    assert job.MIN_DAYS >= 1
    assert job.MAX_DAYS <= 400
    assert job.MIN_DAYS <= job.DEFAULT_DAYS <= job.MAX_DAYS


# ==================================================
# 8. 갱신 뒤 씻어야 하는 다섯 (가장 조용한 고장)
# ==================================================
def test_every_cache_seam_exists():
    """다섯 자리 중 하나만 빠져도 "갱신은 됐다는데 화면은 그대로" 가 된다."""
    from app.repositories import krx_bundle, krx_store, snapshot_store
    from app.services import dashboard_data, search_service

    for module, name in (
        (dashboard_data, "invalidate"),
        (snapshot_store, "reload"),
        (krx_bundle, "reload"),
        (search_service, "reload"),
        (krx_store, "clear_live_cache"),
    ):
        assert callable(getattr(module, name, None)), f"{module.__name__}.{name} 가 없다"


def test_snapshot_reload_also_drops_the_code_index():
    """색인까지 버려야 한다. 스냅샷만 비우면 `get(code)` 가 계속 옛 행을 돌려준다.

    파일은 새것인데 한 종목만 옛날 값이라 **화면에서 보고 알아채기가 매우 어렵다.**
    """
    from app.repositories import snapshot_store

    snapshot_store.load()
    snapshot_store._code_index = {"가짜": {"code": "가짜"}}
    snapshot_store.reload()
    assert snapshot_store._code_index is None


def test_reset_reports_what_it_reset():
    """씻은 결과를 로그에 적는다 — 조용히 지나가면 이 단계가 있는지도 모르게 된다."""
    lines = job.reset_process_caches()
    assert len(lines) == 5
    assert all("—" in line for line in lines)


# ==================================================
# 9. 두 숫자 — 셸과 화면이 같은 문장을 쓴다
# ==================================================
def test_data_summary_has_both_axes():
    summary = job.data_summary()
    assert set(summary) == {"krx", "snapshot"}
    for axis in summary.values():
        assert axis["text"], "빈 문장이면 화면에 아무것도 안 뜬다"


# ==================================================
# 10. HTTP 표면
# ==================================================
def test_status_is_open_even_where_running_is_not(client, monkeypatch):
    """못 하는 것과 고장난 것은 다르다. 배포본에서도 200 으로 이유를 답한다."""
    monkeypatch.setenv("APP_ENV", "vercel")
    body = client.get("/api/refresh/status").json()
    assert body["available"] is False
    assert body["reason"] and body["hints"]
    assert len(body["chain"]) == job.TOTAL_STEPS


def test_run_is_refused_with_503_where_it_cannot_run(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("REFRESH_API", "off")
    response = client.post("/api/refresh/run", json={"check": True})
    assert response.status_code == 503
    assert "REFRESH_API" in response.json()["detail"]


def test_run_is_refused_with_409_while_one_is_going(client, monkeypatch):
    """화면이 무엇을 해야 할지 갈리므로 503 과 코드를 나눈다 — 이쪽은 기다리면 된다."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("REFRESH_API", raising=False)
    monkeypatch.setattr(job, "_current", _FakeRunning())
    assert client.post("/api/refresh/run", json={"check": True}).status_code == 409


def test_days_outside_the_vocabulary_is_422(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    assert client.post("/api/refresh/run", json={"days": 9999}).status_code == 422
    assert client.post("/api/refresh/run", json={"days": 0}).status_code == 422


def test_cancel_says_so_when_nothing_is_going(client, monkeypatch):
    monkeypatch.setattr(job, "_current", None)
    response = client.post("/api/refresh/cancel")
    assert response.status_code == 409
    assert "없습니다" in response.json()["detail"]
