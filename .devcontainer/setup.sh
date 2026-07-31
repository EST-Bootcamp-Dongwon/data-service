#!/usr/bin/env bash
# Codespace 를 처음 만들 때 1회 실행된다 (devcontainer.json 의 postCreateCommand).
#
# 하는 일은 두 가지뿐이다.
#   1. 파이썬 의존성 설치
#   2. 인증키·시세 캐시 상태를 알려 주기 (없어도 앱은 뜨므로 경고만 한다)

set -euo pipefail
cd "$(dirname "$0")/.."

echo "▶ 파이썬 의존성 설치 (2~3분 걸립니다)"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "✅ 설치 완료 — $(python -V)"

echo
echo "──────────────────────────────────────────────"
echo " 인증키 상태"
echo "──────────────────────────────────────────────"
# 인증키는 저장소에 없다(.key 는 .gitignore 대상).
# Codespaces Secrets 로 넣으면 환경변수로 들어오고, app/core/secrets.py 가 환경변수를
# 가장 먼저 보므로 그대로 동작한다.
missing=()
for key in FRED_API_KEY KRX_API_KEY KOSIS_API_KEY; do
  if [ -n "${!key:-}" ]; then
    echo "  ✅ $key — 설정됨 (${#key}자 이름 / 값은 표시하지 않음)"
  else
    echo "  ⚠️  $key — 없음"
    missing+=("$key")
  fi
done

if [ ${#missing[@]} -gt 0 ]; then
  cat <<'GUIDE'

  키가 없어도 아래는 그대로 동작합니다.
    /stock  종목 통합 조회 (야후 파이낸스 — 인증키 불필요)
    /yf     야후 파이낸스 시세
    /users  사용자 API · /tetris · /docs

  키가 있어야 동작하는 것
    FRED_API_KEY   /stock 의 거시지표 겹쳐 보기 · /api/fred/...
    KRX_API_KEY    /krx 시세 수집 (scripts/fetch_krx.py)
    KOSIS_API_KEY  /kosis 통계 실험실

  넣는 곳: github.com/settings/codespaces → Secrets → New secret
           (이 저장소 api-test 에 접근 권한을 주면 다음 재시작부터 적용됩니다)
GUIDE
fi

echo
echo "──────────────────────────────────────────────"
echo " 시세 캐시 상태"
echo "──────────────────────────────────────────────"
if [ -f data/krx_cache.db ]; then
  echo "  ✅ data/krx_cache.db 있음"
else
  echo "  ⚠️  data/krx_cache.db 없음 (96MB 라 저장소에 올리지 않습니다)"
  echo "     → /krx · /quant 화면은 503 안내가 뜹니다."
  echo "     → /stock 은 data/stock_master.json(98KB)로 종목 판별·한글명 검색까지 정상 동작합니다."
  echo "     → 캐시가 필요하면: python3 scripts/fetch_krx.py --days 60   (KRX_API_KEY 필요)"
fi
echo
