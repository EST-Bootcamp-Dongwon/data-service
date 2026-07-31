#!/usr/bin/env bash
# Codespace 에 접속할 때마다 실행된다 (devcontainer.json 의 postAttachCommand).
#
#   1. 서버가 이미 떠 있으면 아무것도 하지 않는다 (재접속마다 중복 실행 방지)
#   2. uvicorn 을 백그라운드로 띄운다
#   3. 8000 포트를 Public 으로 바꾸고, 공유할 주소를 출력한다
#
# 3번이 이 스크립트의 핵심이다. Codespaces 의 전달 포트는 **기본이 Private** 라
# 그대로 두면 강사님이 주소를 열었을 때 GitHub 로그인 화면이 뜨거나 404 가 난다.

set -uo pipefail
cd "$(dirname "$0")/.."

PORT=8000
LOG=/tmp/uvicorn.log

# ── 1. 이미 떠 있으면 넘어간다 ──────────────────────────
if curl -sf -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "✅ 서버가 이미 실행 중입니다 (포트 ${PORT})"
else
  echo "▶ 서버 시작 — uvicorn main:app"
  # nohup + & 로 띄워야 이 스크립트가 끝나도 서버가 살아 있다.
  # --host 0.0.0.0 이어야 컨테이너 밖(포트 전달)에서 접근할 수 있다. 127.0.0.1 이면 안 된다.
  nohup python -m uvicorn main:app --host 0.0.0.0 --port "${PORT}" > "${LOG}" 2>&1 &

  # 기동을 최대 30초 기다린다 (yfinance·pandas import 가 느릴 수 있다)
  for _ in $(seq 30); do
    curl -sf -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
    sleep 1
  done

  if curl -sf -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "✅ 서버 기동 완료"
  else
    echo "❌ 서버가 뜨지 않았습니다. 로그를 확인하세요: cat ${LOG}"
    tail -20 "${LOG}" 2>/dev/null
    exit 0        # 접속 자체를 막지는 않는다
  fi
fi

# ── 2. 포트를 Public 으로 ────────────────────────────────
# Codespace 안에서만 의미가 있다 (로컬 Dev Container 에는 CODESPACE_NAME 이 없다).
if [ -z "${CODESPACE_NAME:-}" ]; then
  echo
  echo "ℹ️  로컬 Dev Container 입니다 — http://localhost:${PORT}"
  exit 0
fi

DOMAIN="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
URL="https://${CODESPACE_NAME}-${PORT}.${DOMAIN}"

echo
echo "▶ ${PORT} 포트를 Public 으로 바꾸는 중…"
# gh CLI 에 codespace 권한이 없으면 실패한다. 그때는 수동 안내로 넘어간다.
if gh codespace ports visibility "${PORT}:public" -c "${CODESPACE_NAME}" 2>/dev/null; then
  VISIBILITY="Public ✅"
else
  VISIBILITY="Private ⚠️  (수동 변경 필요)"
fi

cat <<BANNER

════════════════════════════════════════════════════════════
 공유 주소
   ${URL}
 포트 공개 범위 : ${VISIBILITY}
════════════════════════════════════════════════════════════
BANNER

if [ "${VISIBILITY}" != "Public ✅" ]; then
  cat <<'MANUAL'
 자동 변경에 실패했습니다. 아래 중 하나로 직접 바꿔 주세요.

  A. VS Code 아래쪽 [포트] 탭 → 8000 행에서 마우스 오른쪽 클릭
     → 포트 공개 범위(Port Visibility) → Public

  B. 터미널에서
     gh codespace ports visibility 8000:public -c $CODESPACE_NAME

 ⚠️ Private 상태면 강사님이 주소를 열었을 때 GitHub 로그인/404 가 뜹니다.
 ⚠️ 조직(EST-Bootcamp-Dongwon) 정책으로 Public 이 막혀 있을 수 있습니다.
    그때는 "Org" 범위로 바꾸거나 README 「15. 배포 · 공유」의 대안을 보세요.
MANUAL
fi

echo " 코드스페이스를 멈추면 이 주소도 함께 닫힙니다 (기본 30분 유휴 시 자동 중지)."
echo
