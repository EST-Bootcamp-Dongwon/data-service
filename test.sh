#!/bin/bash
#
# KOSIS 공지사항 RSS 게더링 스크립트
#
# scripts/kosis_rss.py 로 공지사항 상세 페이지를 게시물 번호(boardIdx) 범위만큼 긁어와
# RSS 2.0 파일로 저장한다.
#
# 사용법
#   ./test.sh                      # 기본 범위 (2200~2220)
#   ./test.sh 2300 2320            # 범위 직접 지정
#   ./test.sh 2300 2320 3          # 요청 간격 3초
#   OUT_DIR=./tmp ./test.sh        # 저장 폴더 변경
#   SPLIT=1 ./test.sh              # 게시물마다 파일 하나씩 저장
#   WITH_FILES=1 ./test.sh         # 첨부파일도 함께 내려받기

# 오류가 나면 즉시 멈추고, 정의되지 않은 변수 사용도 오류로 본다.
set -euo pipefail

# 스크립트 위치를 기준으로 경로를 잡는다 (어느 폴더에서 실행해도 동작하도록).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
CRAWLER="$SCRIPT_DIR/scripts/kosis_rss.py"

# 인자 → 없으면 기본값
START_IDX="${1:-2200}"
END_IDX="${2:-2220}"
DELAY="${3:-5}"

# 환경변수로 바꿀 수 있는 옵션
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/data/kosis_rss}"
OUTPUT_FILE="${OUTPUT_FILE:-kosis-${START_IDX}-${END_IDX}.xml}"

if [ ! -f "$CRAWLER" ]; then
  echo "크롤러를 찾을 수 없습니다: $CRAWLER" >&2
  exit 1
fi

if ! [[ "$START_IDX" =~ ^[0-9]+$ && "$END_IDX" =~ ^[0-9]+$ ]]; then
  echo "게시물 번호는 숫자여야 합니다 (입력: $START_IDX, $END_IDX)" >&2
  exit 1
fi

if [ "$START_IDX" -gt "$END_IDX" ]; then
  echo "시작 번호가 끝 번호보다 큽니다 ($START_IDX > $END_IDX)" >&2
  exit 1
fi

# 파이썬 쪽에 넘길 옵션을 배열로 모은다.
OPTS=(--board-range "${START_IDX}-${END_IDX}" --delay "$DELAY" --out-dir "$OUT_DIR")

if [ -n "${SPLIT:-}" ]; then
  # 게시물마다 따로 저장 — 이때는 파일명을 지정하지 않는다.
  OPTS+=(--split)
else
  OPTS+=(--rss-output "$OUTPUT_FILE")
fi

[ -n "${WITH_FILES:-}" ] && OPTS+=(--with-files)

echo "─────────────────────────────────────────────"
echo " KOSIS RSS 게더링"
echo "  범위     : boardIdx $START_IDX ~ $END_IDX ($((END_IDX - START_IDX + 1))건)"
echo "  요청 간격: ${DELAY}초"
echo "  저장 폴더: $OUT_DIR"
[ -z "${SPLIT:-}" ] && echo "  저장 파일: $OUTPUT_FILE"
echo "  시작 시각: $(date +'%Y-%m-%d %H:%M:%S')"
echo "─────────────────────────────────────────────"

STARTED=$SECONDS

# 크롤링 실행 — 파이썬 쪽에서 진행 상황·없는 글·실패를 모두 출력한다.
# `set -e` 로 즉시 종료되지 않도록 `|| STATUS=$?` 로 실패 코드를 직접 받는다.
STATUS=0
"$PYTHON" "$CRAWLER" "${OPTS[@]}" || STATUS=$?

echo "─────────────────────────────────────────────"
echo " 완료 — 소요 $((SECONDS - STARTED))초 ($(date +'%Y-%m-%d %H:%M:%S'))"
echo "─────────────────────────────────────────────"

exit $STATUS
