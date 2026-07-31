"""KOSIS 공지사항 크롤러 + RSS 변환기

국가통계포털(KOSIS) 공지사항을 두 가지 경로로 가져와 **RSS 2.0 파일**로 저장한다.

1. **RSS 피드** (`https://kosis.kr/rss/notice_rss.jsp`)
   최신 10여 건만 제공된다. 목록 확인·최신글 수집용.
2. **상세 페이지 크롤링** (`https://kosis.kr/serviceInfo/noticeDetail.do?boardIdx=N`)
   피드에 없는 과거 글까지 게시물 번호로 직접 긁어온다. 실질적인 수집은 이쪽.

사용법
------
    # 최신 공지 목록 보기 (RSS 피드)
    python3 scripts/kosis_rss.py --list

    # 게시물 번호 하나를 RSS 로 저장
    python3 scripts/kosis_rss.py --board-idx 2200

    # 파일명을 직접 지정 (test.sh 가 쓰는 형태)
    python3 scripts/kosis_rss.py --board-idx 2200 --rss-output test-2200.xml

    # 범위로 한 번에 수집 (요청 사이 1초 대기)
    python3 scripts/kosis_rss.py --board-range 2200-2220 --delay 1

    # 최신 피드 전체를 한 파일로 저장
    python3 scripts/kosis_rss.py --feed --rss-output kosis-latest.xml

저장 폴더는 기본 `data/kosis_rss/` 이며 없으면 자동으로 만든다.
표준 라이브러리만 사용하므로 추가 설치가 필요 없다.
"""

import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

BASE_URL = "https://kosis.kr"
FEED_URL = f"{BASE_URL}/rss/notice_rss.jsp"
DETAIL_URL = f"{BASE_URL}/serviceInfo/noticeDetail.do?boardIdx={{idx}}"
FILE_DOWN_URL = f"{BASE_URL}/cmm/fms/FileDown.do"

# 프로젝트 루트 기준 기본 저장 폴더 (parents[0]=scripts, parents[1]=프로젝트 루트)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "kosis_rss"

# KOSIS 는 기본 파이썬 User-Agent 로 요청하면 차단하는 경우가 있어 브라우저처럼 위장한다.
HEADERS = {
  "User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
  ),
  "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "ko-KR,ko;q=0.9",
}

KST = timezone(timedelta(hours=9))
CHANNEL_TITLE = "[ KOSIS ] 공지사항"
CHANNEL_DESC = "KOSIS 공지사항을 크롤링해 RSS 2.0 으로 변환한 결과"


# ─────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────

def http_get(url: str, timeout: int = 20, retries: int = 2) -> str:
  """URL 을 GET 해서 본문 문자열을 돌려준다. 일시적 실패는 몇 번 재시도한다."""
  last_error = None
  for attempt in range(retries + 1):
    try:
      request = urllib.request.Request(url, headers=HEADERS)
      with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as error:
      last_error = error
      if attempt < retries:
        # 서버가 잠깐 밀릴 수 있으므로 조금씩 늘려 가며 다시 시도한다.
        time.sleep(1.5 * (attempt + 1))
  raise RuntimeError(f"요청 실패 ({url}): {last_error}")


def collapse(text) -> str:
  """줄바꿈·탭·연속 공백을 한 칸으로 줄인다 (제목·링크 정리용)."""
  if text is None:
    return ""
  return re.sub(r"\s+", " ", text).strip()


def strip_tags(fragment: str) -> str:
  """HTML 조각에서 태그를 걷어내고 엔티티를 되돌린다 (본문 미리보기용)."""
  text = re.sub(r"<br\s*/?>", "\n", fragment or "", flags=re.I)
  text = re.sub(r"<[^>]+>", "", text)
  return html.unescape(text).strip()


def safe_filename(name: str, limit: int = 80) -> str:
  """제목을 파일명으로 쓸 수 있게 금지 문자를 걷어낸다 (Windows 기준)."""
  cleaned = re.sub(r'[\\/:*?"<>|]', "", name)
  cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
  return cleaned[:limit] or "notice"


def to_rfc822(date_text: str) -> str:
  """`2020-12-04` / `2026-07-13 15:00:00.0` 같은 값을 RSS 표준 날짜로 바꾼다.

  RSS 2.0 의 pubDate 는 RFC 822 형식을 요구한다. KOSIS 는 이를 지키지 않으므로
  여기서 맞춰 준다. 형식을 알아볼 수 없으면 원문을 그대로 둔다.
  """
  text = collapse(date_text)
  if not text:
    return ""
  for pattern in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d"):
    try:
      parsed = datetime.strptime(text, pattern).replace(tzinfo=KST)
      return parsed.strftime("%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
      continue
  return text


# ─────────────────────────────────────────────────────────────
# 1) RSS 피드 읽기
# ─────────────────────────────────────────────────────────────

def parse_feed(xml_text: str) -> list[dict]:
  """KOSIS RSS 피드 XML 을 게시물 dict 리스트로 바꾼다."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError as error:
    raise RuntimeError(f"RSS 파싱 실패: {error}")

  channel = root.find("channel")
  if channel is None:
    raise RuntimeError("RSS 안에 <channel> 이 없습니다. 주소가 바뀌었는지 확인하세요.")

  items = []
  for node in channel.findall("item"):
    link = collapse(node.findtext("link"))
    guid = collapse(node.findtext("guid"))
    items.append({
      "boardIdx": extract_board_idx(link) or extract_board_idx(guid),
      "title": collapse(node.findtext("title")),
      "link": link,
      "author": "",
      # description 은 CDATA(HTML)라 공백을 건드리지 않고 원문 그대로 보존한다.
      "description": node.findtext("description") or "",
      "pubDate": collapse(node.findtext("pubDate")),
      "attachments": [],
      "source": "feed",
    })
  return items


def extract_board_idx(url: str):
  """링크에서 `boardIdx=2560` 같은 게시물 번호를 뽑아낸다. 없으면 None."""
  match = re.search(r"boardIdx=(\d+)", url or "")
  return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────
# 2) 상세 페이지 크롤링
# ─────────────────────────────────────────────────────────────

# 상세 페이지의 게시물 영역은 아래 구조로 고정돼 있다.
#   <div class="b_title"><strong>제목</strong></div>
#   <div class="cate"><ul><li><strong>항목명</strong><span>값</span></li> ...</ul></div>
#   <div class="txts txts_overflow">본문 HTML</div>
TITLE_RE = re.compile(r'<div class="b_title">\s*<strong>(.*?)</strong>', re.S)
BODY_RE = re.compile(r'<div class="txts[^"]*">(.*?)</div>', re.S)
CATE_RE = re.compile(r'<li>\s*<strong>(.*?)</strong>\s*<span>(.*?)</span>\s*</li>', re.S)
FILE_RE = re.compile(
  r"fn_egov_downFile\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)", re.S)


def crawl_notice(board_idx) -> dict | None:
  """공지사항 상세 페이지 한 건을 크롤링한다. 글이 없으면 None."""
  url = DETAIL_URL.format(idx=board_idx)
  page = http_get(url)

  title_match = TITLE_RE.search(page)
  if not title_match:
    # 없는 게시물이어도 KOSIS 는 200 을 주고 빈 껍데기 페이지를 내려 준다.
    return None

  # 작성기관·게시일 등 메타 항목을 이름:값 으로 모은다.
  meta = {strip_tags(k): strip_tags(v) for k, v in CATE_RE.findall(page)}

  body_match = BODY_RE.search(page)
  body_html = body_match.group(1).strip() if body_match else ""

  # 첨부파일: fn_egov_downFile('news/news_01Form.jsp', '2200', '파일명.xlsx')
  attachments = [
    {"program": program, "docId": doc_id, "name": html.unescape(file_name)}
    for program, doc_id, file_name in FILE_RE.findall(page)
  ]

  return {
    "boardIdx": str(board_idx),
    "title": strip_tags(title_match.group(1)),
    "link": url,
    "author": meta.get("작성기관", ""),
    "description": body_html,
    "pubDate": meta.get("게시일", ""),
    "attachments": attachments,
    "source": "crawl",
  }


def download_attachment(item: dict, attachment: dict, out_dir: Path) -> Path:
  """첨부파일을 실제로 내려받는다 (`--with-files` 일 때만 호출)."""
  form = urllib.parse.urlencode({
    "atchFileId": attachment["docId"],
    "sotreType": attachment["program"],
    "streFileNm": attachment["name"],
  }).encode("utf-8")
  request = urllib.request.Request(FILE_DOWN_URL, data=form, headers=HEADERS)
  with urllib.request.urlopen(request, timeout=30) as response:
    payload = response.read()

  files_dir = out_dir / "files" / item["boardIdx"]
  files_dir.mkdir(parents=True, exist_ok=True)
  path = files_dir / safe_filename(attachment["name"], limit=120)
  path.write_bytes(payload)
  return path


# ─────────────────────────────────────────────────────────────
# 3) RSS 2.0 생성
# ─────────────────────────────────────────────────────────────

def render_item(item: dict) -> str:
  """게시물 dict 하나를 <item> XML 조각으로 만든다."""
  lines = [
    "    <item>",
    f"      <title>{escape(item['title'])}</title>",
    f"      <link>{escape(item['link'])}</link>",
    f"      <description><![CDATA[{item['description']}]]></description>",
  ]
  if item.get("author"):
    lines.append(f"      <category>{escape(item['author'])}</category>")
  if item.get("pubDate"):
    lines.append(f"      <pubDate>{escape(to_rfc822(item['pubDate']))}</pubDate>")
  lines.append(
    f'      <guid isPermaLink="true">{escape(item["link"])}</guid>')
  # 첨부파일은 RSS 표준에 없는 정보라 kosis 네임스페이스를 붙여 확장 요소로 넣는다.
  for attachment in item.get("attachments", []):
    lines.append(
      f'      <kosis:attachment name={quoteattr(attachment["name"])} '
      f'docId={quoteattr(attachment["docId"])} '
      f'program={quoteattr(attachment["program"])} />')
  lines.append("    </item>")
  return "\n".join(lines)


def build_rss(items: list[dict]) -> str:
  """게시물 목록을 담은 완전한 RSS 2.0 문서를 만든다."""
  now = datetime.now(KST).strftime("%a, %d %b %Y %H:%M:%S %z")
  body = "\n".join(render_item(item) for item in items)
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<rss version="2.0" xmlns:kosis="https://kosis.kr/ns/notice">\n'
    "  <channel>\n"
    f"    <title>{escape(CHANNEL_TITLE)}</title>\n"
    f"    <link>{escape(BASE_URL)}</link>\n"
    f"    <description>{escape(CHANNEL_DESC)}</description>\n"
    "    <language>ko</language>\n"
    f"    <lastBuildDate>{now}</lastBuildDate>\n"
    "    <generator>kosis_rss.py</generator>\n"
    f"{body}\n"
    "  </channel>\n"
    "</rss>\n"
  )


def save_rss(items: list[dict], out_dir: Path, filename: str | None) -> Path:
  """RSS 문서를 파일로 저장하고 경로를 돌려준다. 폴더가 없으면 만든다."""
  out_dir.mkdir(parents=True, exist_ok=True)

  if filename:
    name = filename if filename.lower().endswith(".xml") else f"{filename}.xml"
  elif len(items) == 1:
    item = items[0]
    name = f"{item['boardIdx'] or 'notice'}_{safe_filename(item['title'])}.xml"
  else:
    name = f"kosis_notice_{datetime.now(KST):%Y%m%d_%H%M%S}.xml"

  path = out_dir / name
  path.write_text(build_rss(items), encoding="utf-8")
  return path


# ─────────────────────────────────────────────────────────────
# 4) CLI
# ─────────────────────────────────────────────────────────────

def parse_range(text: str) -> list[int]:
  """`2200-2220` 또는 `2200` 을 게시물 번호 리스트로 바꾼다."""
  match = re.fullmatch(r"\s*(\d+)\s*(?:[-~:]\s*(\d+)\s*)?", text)
  if not match:
    raise SystemExit(f"--board-range 형식이 잘못됐습니다: {text} (예: 2200-2220)")
  start = int(match.group(1))
  end = int(match.group(2)) if match.group(2) else start
  if end < start:
    start, end = end, start
  return list(range(start, end + 1))


def print_list(items: list[dict]) -> None:
  """공지 목록을 번호와 함께 출력한다."""
  print(f"── KOSIS 공지사항 RSS 피드 — 총 {len(items)}건 ──")
  for i, item in enumerate(items, start=1):
    date = collapse(item["pubDate"])[:10] or "-"
    idx = item["boardIdx"] or "-"
    print(f"  [{i:>2}] {date}  boardIdx={idx:>6}  {item['title']}")
  print()
  print("  저장: python3 scripts/kosis_rss.py --board-idx <번호>")


def main() -> int:
  parser = argparse.ArgumentParser(
    description="KOSIS 공지사항을 크롤링해 RSS 2.0 파일로 저장한다",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
      "예시\n"
      "  python3 scripts/kosis_rss.py --list\n"
      "  python3 scripts/kosis_rss.py --board-idx 2200\n"
      "  python3 scripts/kosis_rss.py --board-range 2200-2220 --delay 1\n"
      "  python3 scripts/kosis_rss.py --feed --rss-output kosis-latest.xml\n"
    ),
  )
  source = parser.add_argument_group("수집 대상 (하나 선택)")
  source.add_argument("--list", action="store_true", help="RSS 피드 목록만 출력 (저장 안 함)")
  source.add_argument("--feed", action="store_true", help="RSS 피드 전체를 한 파일로 저장")
  source.add_argument("--board-idx", help="게시물 번호 하나를 크롤링해 저장")
  source.add_argument("--board-range", help="게시물 번호 범위를 크롤링 (예: 2200-2220)")

  output = parser.add_argument_group("저장 옵션")
  output.add_argument("--rss-output", "-o", help="저장할 파일명 (기본: 번호_제목.xml)")
  output.add_argument("--out-dir", help=f"저장 폴더 (기본: {DEFAULT_OUT_DIR})")
  output.add_argument("--split", action="store_true",
                      help="범위 수집 시 한 파일로 합치지 않고 게시물마다 따로 저장")
  output.add_argument("--with-files", action="store_true", help="첨부파일도 함께 내려받기")
  output.add_argument("--delay", type=float, default=1.0,
                      help="요청 사이 대기 초 (기본 1.0 — 서버 부하 방지)")
  args = parser.parse_args()

  out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else DEFAULT_OUT_DIR

  # ── 목록 보기 / 피드 저장 ───────────────────────────────
  if args.list or args.feed:
    print(f"RSS 피드 수집 — {FEED_URL}")
    items = parse_feed(http_get(FEED_URL))
    if not items:
      print("공지 항목이 하나도 없습니다.")
      return 1
    if args.list:
      print_list(items)
      return 0
    path = save_rss(items, out_dir, args.rss_output)
    print(f"저장 완료 — {path} ({path.stat().st_size:,} bytes, {len(items)}건)")
    return 0

  # ── 상세 페이지 크롤링 ──────────────────────────────────
  if args.board_idx:
    targets = parse_range(str(args.board_idx))
  elif args.board_range:
    targets = parse_range(args.board_range)
  else:
    parser.print_help()
    print("\n수집 대상을 지정하세요 (--list / --feed / --board-idx / --board-range)")
    return 1

  print(f"공지사항 크롤링 — {len(targets)}건 (boardIdx {targets[0]}~{targets[-1]})")
  print(f"저장 폴더: {out_dir}")

  collected, missing, failed = [], [], []
  for i, board_idx in enumerate(targets):
    try:
      item = crawl_notice(board_idx)
    except RuntimeError as error:
      failed.append((board_idx, str(error)))
      print(f"  [{i + 1}/{len(targets)}] {board_idx} 실패 — {error}")
    else:
      if item is None:
        missing.append(board_idx)
        print(f"  [{i + 1}/{len(targets)}] {board_idx} 없음 — 건너뜀")
      else:
        collected.append(item)
        note = f" (첨부 {len(item['attachments'])})" if item["attachments"] else ""
        print(f"  [{i + 1}/{len(targets)}] {board_idx} {item['title']}{note}")

        if args.with_files:
          for attachment in item["attachments"]:
            try:
              path = download_attachment(item, attachment, out_dir)
              print(f"        첨부 저장 → {path.relative_to(out_dir)}")
            except (urllib.error.URLError, OSError) as error:
              print(f"        첨부 실패 — {attachment['name']}: {error}")

        # --split 이면 수집 즉시 게시물별로 한 파일씩 떨어뜨린다.
        if args.split:
          path = save_rss([item], out_dir, None)
          print(f"        저장 → {path.name}")

    # 마지막 요청 뒤에는 기다릴 필요가 없다.
    if args.delay > 0 and i < len(targets) - 1:
      time.sleep(args.delay)

  if not collected:
    print("\n수집된 게시물이 없습니다.")
    return 1

  # 한 파일로 합쳐 저장 (--split 을 쓰면 위에서 이미 개별 저장했으므로 생략)
  if not args.split:
    path = save_rss(collected, out_dir, args.rss_output)
    print(f"\n저장 완료 — {path} ({path.stat().st_size:,} bytes)")

  print(f"요약 — 수집 {len(collected)}건 · 없음 {len(missing)}건 · 실패 {len(failed)}건")
  if failed:
    for board_idx, error in failed[:10]:
      print(f"  실패 {board_idx}: {error[:100]}")
  return 0


if __name__ == "__main__":
  try:
    raise SystemExit(main())
  except KeyboardInterrupt:
    print("\n중단했습니다.", file=sys.stderr)
    raise SystemExit(130)
  except RuntimeError as error:
    print(f"오류: {error}", file=sys.stderr)
    raise SystemExit(1)
