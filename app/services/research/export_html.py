"""인쇄용 HTML 내보내기 — 자체완결 (M8 · 변경노트 N88)

목표 양식(GIC 리서치 리포트)은 **인쇄물**이다. 마크다운은 그림을 담지 못하고
(`export_md` 가 숫자 표로 대신한다), 화면은 서버·CDN 이 있어야 산다.
이 파일은 **파일 하나로 끝나는** 리포트를 만든다.

    POST /api/research/export/html  →  <!doctype html> … 한 덩어리

자체완결이란 무엇을 뜻하나
------------------------
· **CDN 을 부르지 않는다.** ApexCharts 를 쓸 수 없으므로 차트를 **인라인 SVG** 로 그린다.
· CSS 도 인라인이다 (`app.css` 를 불러오지 않는다).
· 이미지·폰트를 바깥에서 가져오지 않는다.
→ 파일을 이메일로 보내거나 USB 에 넣어도 그대로 열린다. 브라우저 인쇄로 PDF 가 된다.

★ **값을 만들지 않는다**
    계열은 `charts.py` 것을, 표지는 `headline.py` 것을, 표는 `tables.py` 것을 그대로 쓴다.
    **그리는 방법만 다르고 값은 안 만든다** — 세 렌더러(화면·MD·인쇄)가 같은 것을 읽는다.
    여기서 숫자를 다시 계산하면 인쇄본과 화면이 어긋나고, 어느 쪽이 맞는지 알 수 없게 된다.

색
--
`app.css` 의 토큰을 **값으로 박아 둔다** (인라인이라 변수를 못 물려받는다).
검증된 8슬롯 범주형 순서와 등락 색(상승 빨강·하락 파랑)을 그대로 옮겼다.
인쇄를 전제로 **라이트 팔레트만** 쓴다 — 다크 배경은 잉크를 먹고 대비도 뒤집힌다.

참고문헌
------
`C1_evidence` 를 그대로 옮긴다. 리서치 리포트는 근거 목록이 있어야 리포트다.
"""
from __future__ import annotations

import html
import math
from typing import Dict, List, Optional, Sequence

# ── 색 (app.css 토큰을 값으로 옮겼다 · 라이트 전용) ──
INK = "#1c2024"
MUTED = "#6b7280"
BORDER = "#e3e6ea"
BORDER_SOFT = "#eef0f3"
SURFACE_2 = "#f9fafb"
ACCENT = "#2563eb"
ACCENT_SOFT = "#eff4ff"
OK, WARN, ERROR = "#16a34a", "#d97706", "#dc2626"
UP, DOWN, FLAT = "#e03131", "#1971c2", "#8a9099"
# 범주형 8슬롯 — **순서를 바꾸지 않는다** (U3 결정 · 검증 완료)
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#7c5cd6", "#0f9bb5", "#b5732f")

TONE_COLOR = {"good": OK, "warning": WARN, "serious": ERROR, "critical": ERROR,
              "up": UP, "down": DOWN, "flat": FLAT, "neutral": MUTED, "": ACCENT}

# 표지에 찍는 문서 종류 (M9 · N95) — `contracts.WORKSTREAMS` 의 이름을 옮긴 것이 아니라
# **표지에서 읽히는 말**로 적는다. 리서치와 Top Pick 은 성격이 다른 문서다.
WORKSTREAM_LABEL = {
    "CORP-R": "기업 리서치 보고서",
    "CORP-TP": "기업 Top Pick 스크리닝",
    "IND-R": "산업 리서치 보고서",
    "IND-TP": "산업 Top Pick 스크리닝",
}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean(values: Sequence) -> List[float]:
    return [float(v) for v in values if _is_num(v)]


# ═════════════════════════════════════════════════════════
# 1. 인라인 SVG 차트
# ═════════════════════════════════════════════════════════
W, PAD_L, PAD_R, PAD_T, PAD_B = 720, 150, 24, 16, 34


def _axis_bounds(values: Sequence[float]) -> tuple:
    """0 을 포함한 눈금 범위. **음수가 있으면 0 선을 그린다** (부호를 감추지 않는다)."""
    clean = _clean(values)
    if not clean:
        return 0.0, 1.0
    low, high = min(clean), max(clean)
    low = min(low, 0.0)
    high = max(high, 0.0)
    if low == high:
        return low - 1.0, high + 1.0
    pad = (high - low) * 0.08
    return low - (pad if low < 0 else 0), high + pad


def _svg(body: str, height: int, width: int = W) -> str:
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img" xmlns="http://www.w3.org/2000/svg">{body}</svg>')


def _grid(x0: int, x1: int, y0: int, y1: int, low: float, high: float,
          ticks: int = 4) -> str:
    """가로 격자 — **실선 하나짜리 얇은 선**이다 (점선은 '임계값' 으로 오해된다)."""
    out = []
    for i in range(ticks + 1):
        value = low + (high - low) * i / ticks
        y = y1 - (value - low) / (high - low or 1) * (y1 - y0)
        stroke = MUTED if abs(value) < 1e-9 and low < 0 else BORDER_SOFT
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                   f'stroke="{stroke}" stroke-width="1"/>')
        out.append(f'<text x="{x0 - 6}" y="{y + 4:.1f}" text-anchor="end" '
                   f'font-size="10" fill="{MUTED}">{value:,.1f}</text>')
    return "".join(out)


def _legend(names: Sequence[str], y: int = 12) -> str:
    """계열이 둘 이상이면 **범례를 늘 낸다** (하나면 제목이 이미 그 이름이다)."""
    if len(names) < 2:
        return ""
    out, x = [], PAD_L
    for index, name in enumerate(names):
        color = SERIES[index % len(SERIES)]
        out.append(f'<rect x="{x}" y="{y - 8}" width="9" height="9" rx="2" fill="{color}"/>'
                   f'<text x="{x + 13}" y="{y}" font-size="10" fill="{INK}">{esc(name)}</text>')
        x += 22 + len(str(name)) * 7
    return "".join(out)


def _bar_h(chart: Dict) -> str:
    cats = chart["categories"]
    series = chart["series"]
    if not cats or not series:
        return ""
    row = 22
    height = PAD_T + 14 + len(cats) * row * len(series) + PAD_B
    low, high = _axis_bounds([v for s in series for v in (s.get("data") or [])])
    x0, x1 = PAD_L, W - PAD_R
    zero = x0 + (0 - low) / (high - low or 1) * (x1 - x0)
    out = [_legend([s.get("name", "") for s in series])]
    y = PAD_T + 14
    for index, name in enumerate(cats):
        for si, s in enumerate(series):
            value = (s.get("data") or [None] * len(cats))[index] if index < len(s.get("data") or []) else None
            color = SERIES[si % len(SERIES)]
            if _is_num(value):
                end = x0 + (value - low) / (high - low or 1) * (x1 - x0)
                left, width = min(zero, end), abs(end - zero)
                out.append(f'<rect x="{left:.1f}" y="{y}" width="{max(width, 1):.1f}" '
                           f'height="{row - 8}" rx="3" fill="{color}"/>')
                # 값은 막대 **밖**에 찍는다 — 안에 넣으면 짧은 막대에서 잘린다
                out.append(f'<text x="{left + width + 5:.1f}" y="{y + row - 13}" '
                           f'font-size="10" fill="{INK}">{value:,.1f}</text>')
            else:
                out.append(f'<text x="{zero + 5:.1f}" y="{y + row - 13}" font-size="10" '
                           f'fill="{MUTED}">—</text>')
            y += row
        out.append(f'<text x="{x0 - 8}" y="{y - row * len(series) + 11}" text-anchor="end" '
                   f'font-size="11" fill="{INK}">{esc(str(name)[:22])}</text>')
    out.append(f'<line x1="{zero:.1f}" y1="{PAD_T + 10}" x2="{zero:.1f}" y2="{y}" '
               f'stroke="{BORDER}" stroke-width="1"/>')
    return _svg("".join(out), height)


def _bar_v(chart: Dict, line_overlay: bool = False) -> str:
    cats = chart["categories"]
    series = chart["series"]
    if not cats or not series:
        return ""
    height, x0, x1 = 260, 70, W - PAD_R
    y0, y1 = PAD_T + 16, height - PAD_B
    low, high = _axis_bounds([v for s in series for v in (s.get("data") or [])])
    out = [_legend([s.get("name", "") for s in series]),
           _grid(x0, x1, y0, y1, low, high)]
    span = (x1 - x0) / max(len(cats), 1)
    bars = [s for s in series if s.get("kind") != "line"] if line_overlay else series
    lines = [s for s in series if s.get("kind") == "line"] if line_overlay else []

    def ypos(v):
        return y1 - (v - low) / (high - low or 1) * (y1 - y0)

    width = span * 0.62 / max(len(bars), 1)
    for si, s in enumerate(bars):
        color = SERIES[series.index(s) % len(SERIES)]
        for index, value in enumerate(s.get("data") or []):
            if not _is_num(value):
                continue
            # 인접 막대 사이에 2px 표면 간격을 둔다 (테두리를 그리지 않는다)
            x = x0 + span * index + span * 0.19 + si * (width + 2)
            top, base = min(ypos(value), ypos(0)), abs(ypos(value) - ypos(0))
            out.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{max(width - 2, 1):.1f}" '
                       f'height="{max(base, 1):.1f}" rx="3" fill="{color}"/>')
    for s in lines:
        color = SERIES[series.index(s) % len(SERIES)]
        points = [(x0 + span * i + span / 2, ypos(v))
                  for i, v in enumerate(s.get("data") or []) if _is_num(v)]
        if len(points) > 1:
            out.append('<polyline fill="none" stroke-width="2.4" stroke="%s" points="%s"/>'
                       % (color, " ".join(f"{x:.1f},{y:.1f}" for x, y in points)))
        out += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{color}" '
                f'stroke="#fff" stroke-width="2"/>' for x, y in points]
    step = max(1, len(cats) // 12)
    for index, name in enumerate(cats):
        if index % step:
            continue
        out.append(f'<text x="{x0 + span * index + span / 2:.1f}" y="{y1 + 15}" '
                   f'text-anchor="middle" font-size="10" fill="{MUTED}">{esc(str(name)[:12])}</text>')
    return _svg("".join(out), height)


def _line(chart: Dict) -> str:
    cats = chart["categories"]
    series = chart["series"]
    if not cats or not series:
        return ""
    height, x0, x1 = 250, 70, W - PAD_R
    y0, y1 = PAD_T + 16, height - PAD_B
    low, high = _axis_bounds([v for s in series for v in (s.get("data") or [])])
    out = [_legend([s.get("name", "") for s in series]),
           _grid(x0, x1, y0, y1, low, high)]
    span = (x1 - x0) / max(len(cats) - 1, 1)
    for si, s in enumerate(series):
        color = SERIES[si % len(SERIES)]
        points = [(x0 + span * i, y1 - (v - low) / (high - low or 1) * (y1 - y0))
                  for i, v in enumerate(s.get("data") or []) if _is_num(v)]
        if len(points) > 1:
            out.append('<polyline fill="none" stroke-width="2" stroke="%s" points="%s"/>'
                       % (color, " ".join(f"{x:.1f},{y:.1f}" for x, y in points)))
    step = max(1, len(cats) // 8)
    for index, name in enumerate(cats):
        if index % step and index != len(cats) - 1:
            continue
        out.append(f'<text x="{x0 + span * index:.1f}" y="{y1 + 15}" text-anchor="middle" '
                   f'font-size="10" fill="{MUTED}">{esc(str(name)[:10])}</text>')
    return _svg("".join(out), height)


def _scatter(chart: Dict) -> str:
    series = chart["series"]
    points = [(p.get("x"), p.get("y")) for s in series for p in (s.get("data") or [])]
    xs = [x for x, _y in points if _is_num(x)]
    ys = [y for _x, y in points if _is_num(y)]
    if not xs or not ys:
        return ""
    height, x0, x1 = 300, 70, W - PAD_R
    y0, y1 = PAD_T + 16, height - PAD_B
    xlo, xhi = min(xs), max(xs)
    ylo, yhi = _axis_bounds(ys)
    xpad = (xhi - xlo) * 0.08 or 1
    xlo, xhi = xlo - xpad, xhi + xpad
    out = [_legend([s.get("name", "") for s in series]),
           _grid(x0, x1, y0, y1, ylo, yhi)]
    for si, s in enumerate(series):
        color = SERIES[si % len(SERIES)]
        for point in s.get("data") or []:
            if not (_is_num(point.get("x")) and _is_num(point.get("y"))):
                continue
            cx = x0 + (point["x"] - xlo) / (xhi - xlo or 1) * (x1 - x0)
            cy = y1 - (point["y"] - ylo) / (yhi - ylo or 1) * (y1 - y0)
            # 겹치는 점은 **2px 표면 테두리**로 가른다 (테두리 색을 따로 쓰지 않는다)
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{color}" '
                       f'stroke="#fff" stroke-width="2"/>')
            if point.get("label"):
                out.append(f'<text x="{cx + 8:.1f}" y="{cy + 3:.1f}" font-size="9" '
                           f'fill="{MUTED}">{esc(str(point["label"])[:10])}</text>')
    axis = chart.get("axis") or {}
    out.append(f'<text x="{(x0 + x1) / 2:.0f}" y="{height - 4}" text-anchor="middle" '
               f'font-size="10" fill="{MUTED}">{esc(axis.get("x", ""))}</text>')
    return _svg("".join(out), height)


def _range(chart: Dict) -> str:
    """팬차트 — 95% 구간(면)과 점추정(선). 축은 하나다."""
    cats = chart["categories"]
    low_s = next((s for s in chart["series"] if "하한" in s.get("name", "")), None)
    high_s = next((s for s in chart["series"] if "상한" in s.get("name", "")), None)
    point_s = next((s for s in chart["series"] if s.get("kind") == "line"), None)
    if not (low_s and high_s and point_s and cats):
        return _line(chart)
    height, x0, x1 = 270, 78, W - PAD_R
    y0, y1 = PAD_T + 16, height - PAD_B
    lo, hi = _axis_bounds((low_s["data"] or []) + (high_s["data"] or []))
    out = [_grid(x0, x1, y0, y1, lo, hi)]
    span = (x1 - x0) / max(len(cats) - 1, 1)

    def ypos(v):
        return y1 - (v - lo) / (hi - lo or 1) * (y1 - y0)

    top = [(x0 + span * i, ypos(v)) for i, v in enumerate(high_s["data"]) if _is_num(v)]
    bottom = [(x0 + span * i, ypos(v)) for i, v in enumerate(low_s["data"]) if _is_num(v)]
    if top and bottom:
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in top + bottom[::-1])
        out.append(f'<polygon points="{path}" fill="{SERIES[0]}" fill-opacity="0.18"/>')
    line = [(x0 + span * i, ypos(v)) for i, v in enumerate(point_s["data"]) if _is_num(v)]
    if len(line) > 1:
        out.append('<polyline fill="none" stroke-width="2.4" stroke="%s" points="%s"/>'
                   % (SERIES[0], " ".join(f"{x:.1f},{y:.1f}" for x, y in line)))
    out.append(f'<text x="{x0}" y="{y1 + 15}" font-size="10" fill="{MUTED}">{esc(cats[0])}</text>'
               f'<text x="{x1}" y="{y1 + 15}" text-anchor="end" font-size="10" '
               f'fill="{MUTED}">{esc(cats[-1])}</text>')
    out.append(f'<text x="{x0}" y="12" font-size="10" fill="{MUTED}">'
               f'면 = 95% 구간 · 선 = 점추정</text>')
    return _svg("".join(out), height)


def _radar(chart: Dict) -> str:
    cats = chart["categories"]
    series = chart["series"]
    if len(cats) < 3:
        return ""
    height, cx, cy, radius = 330, W / 2, 165, 118
    top = 5.0
    out = [_legend([s.get("name", "") for s in series])]
    for ring in (1, 2, 3, 4, 5):
        pts = []
        for i in range(len(cats)):
            angle = -math.pi / 2 + 2 * math.pi * i / len(cats)
            r = radius * ring / 5
            pts.append(f"{cx + r * math.cos(angle):.1f},{cy + r * math.sin(angle):.1f}")
        out.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{BORDER_SOFT}"/>')
    for si, s in enumerate(series):
        color = SERIES[si % len(SERIES)]
        pts = []
        for i, value in enumerate(s.get("data") or []):
            if not _is_num(value):
                continue
            angle = -math.pi / 2 + 2 * math.pi * i / len(cats)
            r = radius * value / top
            pts.append(f"{cx + r * math.cos(angle):.1f},{cy + r * math.sin(angle):.1f}")
        if len(pts) >= 3:
            out.append(f'<polygon points="{" ".join(pts)}" fill="{color}" fill-opacity="0.14" '
                       f'stroke="{color}" stroke-width="2"/>')
    for i, name in enumerate(cats):
        angle = -math.pi / 2 + 2 * math.pi * i / len(cats)
        x, y = cx + (radius + 16) * math.cos(angle), cy + (radius + 16) * math.sin(angle)
        anchor = "middle" if abs(math.cos(angle)) < 0.3 else ("start" if math.cos(angle) > 0 else "end")
        out.append(f'<text x="{x:.1f}" y="{y + 3:.1f}" text-anchor="{anchor}" font-size="10" '
                   f'fill="{INK}">{esc(str(name)[:12])}</text>')
    return _svg("".join(out), height)


def _timeline(chart: Dict) -> str:
    series = chart["series"]
    dates = sorted({str(p.get("x")) for s in series for p in (s.get("data") or [])})
    if not dates:
        return ""
    height, x0, x1 = 190, 78, W - PAD_R
    y0 = PAD_T + 20
    kinds = [s.get("name", "") for s in series]
    row = 34
    out = [_legend(kinds)]
    index_of = {d: i for i, d in enumerate(dates)}
    span = (x1 - x0) / max(len(dates) - 1, 1)
    for si, s in enumerate(series):
        color = SERIES[si % len(SERIES)]
        y = y0 + si * row
        out.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{BORDER_SOFT}"/>')
        out.append(f'<text x="{x0 - 8}" y="{y + 4}" text-anchor="end" font-size="10" '
                   f'fill="{INK}">{esc(s.get("name", ""))}</text>')
        for point in s.get("data") or []:
            x = x0 + span * index_of.get(str(point.get("x")), 0)
            out.append(f'<circle cx="{x:.1f}" cy="{y}" r="5" fill="{color}" '
                       f'stroke="#fff" stroke-width="2"/>')
    out.append(f'<text x="{x0}" y="{height - 8}" font-size="10" fill="{MUTED}">{esc(dates[0])}</text>'
               f'<text x="{x1}" y="{height - 8}" text-anchor="end" font-size="10" '
               f'fill="{MUTED}">{esc(dates[-1])}</text>')
    return _svg("".join(out), max(height, y0 + len(series) * row + 30))


def _signal_html(chart: Dict) -> str:
    """신호판 — 차트가 아니다. **화살표 + 글자**를 함께 낸다 (색만으로 말하지 않는다)."""
    rows = (chart["series"][0].get("data") if chart.get("series") else []) or []
    cells = "".join(
        f'<tr><td style="width:24px;color:{UP if r.get("up") else DOWN}">'
        f'{"▲" if r.get("up") else "▼"}</td>'
        f'<td><b>{esc(r.get("label"))}</b></td><td>{esc(r.get("value"))}</td>'
        f'<td style="color:{MUTED}">{esc(r.get("detail"))}</td></tr>' for r in rows)
    return f'<table class="signal">{cells}</table>'


def _stat_html(chart: Dict) -> str:
    value = ((chart["series"][0].get("data") or [""])[0] if chart.get("series") else "")
    name = (chart["categories"] or [""])[0]
    return (f'<div class="stat"><div class="stat-v">{esc(value)}'
            f'<em>{esc(chart.get("unit"))}</em></div>'
            f'<div class="stat-l">{esc(name)}</div></div>')


_DRAWERS = {"bar-h": _bar_h, "bar": _bar_v, "line": _line, "scatter": _scatter,
            "range": _range, "radar": _radar, "timeline": _timeline}


SVG_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',"
            "'Apple SD Gothic Neo',sans-serif")


def chart_svg_standalone(chart: Dict) -> str:
    """페이지 CSS 없이 혼자 서는 SVG (M9 · N94 — 마크다운이 그림으로 실을 것).

    `chart_svg` 가 낸 것을 그대로 쓰되 세 가지를 더한다. 인쇄본 안에서는
    페이지 `<style>` 이 채워 주던 것들이라, 파일 밖으로 꺼내면 사라진다.

      ① **픽셀 폭** — `width="100%"` 는 `<img>` 안에서 기준이 될 부모가 없다
      ② **font-family** — 상속받을 `<style>` 이 없어 한글이 기본 세리프로 떨어진다
      ③ **흰 배경** — 글자색이 진한 회색이라 어두운 배경에서는 안 보인다

    SVG 가 아닌 것에는 **빈 문자열**을 낸다 — `signal`·`stat` 은 SVG 가 아니라 HTML 이고,
    못 그린 차트는 사유 문단이다. 그림으로 담지 못하는 것을 담은 척하지 않는다
    (그 자리는 `export_md` 가 숫자 표로 낸다).
    """
    svg = chart_svg(chart)
    if not svg.startswith("<svg"):
        return ""
    head, _, rest = svg.partition(">")
    head = head.replace('width="100%"', f'width="{W}"')
    return (f'{head} font-family="{SVG_FONT}">'
            f'<rect width="100%" height="100%" fill="#ffffff"/>{rest}')


def chart_svg(chart: Dict) -> str:
    """차트 하나 → SVG(또는 표/값). **못 그리면 못 그린다고 적는다.**"""
    if not chart.get("drawable"):
        return f'<p class="gap">⚠ 이 차트는 그리지 못했다 — {esc(chart.get("reason"))}</p>'
    kind = chart.get("kind")
    if kind == "signal":
        return _signal_html(chart)
    if kind == "stat":
        return _stat_html(chart)
    if kind == "bar-line":
        return _bar_v(chart, line_overlay=True)
    drawer = _DRAWERS.get(kind)
    if not drawer:
        return f'<p class="gap">⚠ `{esc(kind)}` 은 인쇄본에서 그리는 규칙이 아직 없다</p>'
    try:
        return drawer(chart) or f'<p class="gap">⚠ 그릴 점이 없다</p>'
    except Exception as error:      # 차트 하나가 리포트를 죽이지 않는다 (§2-2)
        return f'<p class="gap">⚠ 그리다 실패했다 — {esc(type(error).__name__)}</p>'


# ═════════════════════════════════════════════════════════
# 2. 문서 조립
# ═════════════════════════════════════════════════════════
def _table_html(table: Dict) -> str:
    if not table:
        return ""
    if not table.get("drawable"):
        return (f'<div class="tbl"><h4>{esc(table.get("title"))}</h4>'
                f'<p class="gap">⚠ 이 표는 만들지 못했다 — {esc(table.get("reason"))}</p></div>')
    start = table.get("align_right_from", 1)
    head = "".join(f'<th{" class=num" if i >= start else ""}>{esc(h)}</th>'
                   for i, h in enumerate(table.get("head") or []))
    body = "".join("<tr>" + "".join(
        f'<td{" class=num" if i >= start else ""}>{esc(c)}</td>'
        for i, c in enumerate(row)) + "</tr>" for row in (table.get("rows") or []))
    return (f'<div class="tbl"><h4>{esc(table.get("title"))}</h4>'
            f'<table>{f"<thead><tr>{head}</tr></thead>" if head else ""}<tbody>{body}</tbody></table>'
            + (f'<p class="note">⚠ {esc(table.get("note"))}</p>' if table.get("note") else "")
            + (f'<p class="src">기준 {esc(table.get("basis"))}</p>' if table.get("basis") else "")
            + "</div>")


def _chart_block(chart: Dict) -> str:
    if not chart:
        return ""
    ids = chart.get("data_ids") or []
    return (f'<figure class="fig"><figcaption><b>{esc(chart.get("title"))}</b>'
            f'<span class="muted"> {esc(chart.get("chart_type"))}'
            f'{" · " + esc(chart.get("unit")) if chart.get("unit") else ""}</span></figcaption>'
            + chart_svg(chart)
            + (f'<p class="note">⚠ {esc(chart.get("note"))}</p>' if chart.get("note") else "")
            + _table_html({**chart, "title": "숫자", "drawable": True,
                           "head": (chart.get("table") or {}).get("head") or [],
                           "rows": (chart.get("table") or {}).get("rows") or [],
                           "note": "", "basis": ""})
            + (f'<p class="src">근거 {esc(" · ".join(ids[:8]))}'
               f'{f" 외 {len(ids) - 8}건" if len(ids) > 8 else ""}</p>' if ids else "")
            + "</figure>")


def _headline_html(headline: Dict) -> str:
    verdict = headline.get("verdict") or {}
    if not verdict.get("label"):
        return ""
    color = TONE_COLOR.get(verdict.get("tone", ""), ACCENT)
    tiles = "".join(
        f'<div class="tile"><div class="tl">{esc(m.get("label"))}</div>'
        f'<div class="tv" style="color:{TONE_COLOR.get(m.get("tone", ""), INK)}">'
        f'{esc(m.get("text"))}<em>{esc(m.get("unit"))}</em></div>'
        f'<div class="ts">{esc(m.get("sub"))}</div></div>'
        for m in headline.get("metrics") or [])

    def bullets(label, items):
        if not items:
            return ""
        return (f'<div class="hl-list"><span class="tl">{esc(label)}</span><ul>'
                + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul></div>")

    scenarios = headline.get("scenarios") or {}
    columns = scenarios.get("columns") or []
    scn = ""
    if columns:
        cells = []
        for column in columns:
            arrow = {"up": "▲", "down": "▼"}.get(column.get("tone"), "▬")
            tint = TONE_COLOR.get(column.get("tone", ""), FLAT)
            if scenarios.get("kind") == "price":
                rows = "".join(f"<dt>{esc(label)}</dt><dd>{esc(column.get(key))}</dd>"
                               for key, label in (scenarios.get("fields") or []))
            else:
                rows = "".join(
                    f'<dt>{esc(c.get("horizon"))}</dt><dd>{esc(c.get("condition"))}'
                    + (f' <span class="muted">· 지켜볼 것: {esc(c.get("watch"))}</span>'
                       if c.get("watch") else "") + "</dd>"
                    for c in column.get("cells") or [])
            cells.append(f'<div class="scn" style="border-top-color:{tint}">'
                         f'<div class="scn-h"><span style="color:{tint}">{arrow}</span> '
                         f'<b>{esc(column.get("name"))}</b> '
                         f'<span class="muted">{esc(column.get("label"))}</span></div>'
                         f"<dl>{rows}</dl></div>")
        scn = ('<h3>시나리오 <span class="muted">Bear · Base · Bull</span></h3>'
               f'<div class="scn-grid">{"".join(cells)}</div>'
               + (f'<p class="note">⚠ {esc(scenarios.get("note"))}</p>'
                  if scenarios.get("note") else ""))
    elif scenarios.get("note"):
        scn = f'<p class="note">⚠ {esc(scenarios.get("note"))}</p>'

    axes = headline.get("axes") or []
    axes_html = ""
    if axes:
        evaluation = headline.get("evaluation") or {}
        bars = "".join(
            f'<div class="ax"><span class="ax-n">{esc(a["axis"])}</span>'
            f'<span class="ax-t"><span class="ax-f" style="width:{a["pct"] or 0}%;'
            f'background:{TONE_COLOR.get(a["tone"], ACCENT)}"></span></span>'
            f'<span class="ax-v">{esc(a["score_text"])}<em>/{esc(a["max"])}</em></span></div>'
            f'<p class="ax-w">{esc(a["why"])}</p>' for a in axes)
        critical = "".join(f'<p class="gap">⚠ 중대 결함 — {esc(c)}</p>'
                           for c in evaluation.get("critical") or [])
        axes_html = (f'<h3>품질 평가 9축 <span class="muted">{esc(evaluation.get("total"))}/100 '
                     f'· 등급 {esc(evaluation.get("grade"))}</span></h3>{critical}{bars}')

    return (f'<section class="headline"><div class="hl-top">'
            f'<div><div class="tl">{esc(verdict.get("kind"))}</div>'
            f'<div class="hl-v" style="color:{color}">● {esc(verdict.get("label"))}</div></div>'
            f'<div class="hl-t">{esc(headline.get("target"))}'
            f'<span class="muted"> · 기준일 {esc(headline.get("as_of"))}</span></div></div>'
            + (f'<p class="human">🖐 {esc(verdict.get("human_decision"))}</p>'
               if verdict.get("ai_proposal") else "")
            + f'<div class="tiles">{tiles}</div>'
            + bullets("판단 근거", verdict.get("reasons"))
            + bullets("조건 · 남은 것", verdict.get("conditions"))
            + (f'<p class="note">⚠ {esc(verdict.get("caveat"))}</p>'
               if verdict.get("caveat") else "")
            + scn + axes_html
            + f'<p class="foot">{esc(headline.get("disclaimer"))}</p></section>')


def _references(pack: Dict) -> str:
    """참고문헌 — `C1_evidence` 를 그대로 옮긴다. 근거 목록이 있어야 리서치 리포트다."""
    evidence = pack.get("C1_evidence") or []
    if not evidence:
        return ""
    rows = "".join(
        f'<tr><td>{esc(e.get("id"))}</td><td>{esc(e.get("claim"))}</td>'
        f'<td>{esc(e.get("source"))}</td><td>{esc(e.get("location"))}</td>'
        f'<td>{esc(e.get("published"))}</td>'
        f'<td>{esc(e.get("grade") or e.get("directness"))}</td></tr>' for e in evidence)
    return ('<section class="page"><h2>참고문헌 · 근거 목록</h2>'
            f'<p class="muted">근거 {len(evidence)}건. 리포트의 모든 수치는 이 목록으로 '
            '되짚을 수 있다 (파생값은 계산 기록을 거친다).</p>'
            '<table><thead><tr><th>ID</th><th>주장</th><th>출처</th><th>위치</th>'
            f'<th>공시일</th><th>등급</th></tr></thead><tbody>{rows}</tbody></table></section>')


STYLE = f"""
*{{box-sizing:border-box}}
body{{margin:0;padding:28px;max-width:820px;margin:0 auto;color:{INK};background:#fff;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif;
  font-size:13px;line-height:1.65}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:0 0 8px}}
h3{{font-size:14px;margin:18px 0 8px}} h4{{font-size:13px;margin:0 0 6px}}
.muted{{color:{MUTED};font-weight:400}}
.cover{{padding-bottom:14px;border-bottom:2px solid {INK};margin-bottom:18px}}
.disclaimer{{padding:8px 10px;background:{SURFACE_2};border-left:3px solid {WARN};
  font-size:11px;color:{MUTED};margin:10px 0}}
.headline{{padding:16px;border:1px solid {BORDER};border-radius:8px;margin-bottom:20px}}
.hl-top{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
.hl-v{{font-size:18px;font-weight:700;margin-top:2px}}
.hl-t{{text-align:right;font-weight:600}}
.human{{color:{WARN};font-size:11px;margin:6px 0 12px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0}}
.tile{{padding:8px 10px;background:{SURFACE_2};border:1px solid {BORDER_SOFT};border-radius:6px}}
.tl{{font-size:10px;color:{MUTED}}}
.tv{{font-size:18px;font-weight:600}} .tv em{{font-style:normal;font-size:11px;color:{MUTED};margin-left:2px}}
.ts{{font-size:10px;color:{MUTED};line-height:1.45}}
.hl-list{{margin:6px 0}} .hl-list ul{{margin:2px 0;padding-left:18px}}
.scn-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.scn{{padding:9px;background:{SURFACE_2};border:1px solid {BORDER_SOFT};border-top:3px solid {FLAT};
  border-radius:6px}}
.scn-h{{margin-bottom:5px}} .scn dl{{margin:0;font-size:10.5px}}
.scn dt{{color:{MUTED}}} .scn dd{{margin:0 0 5px}}
.ax{{display:grid;grid-template-columns:150px 1fr 70px;align-items:center;gap:8px;margin-bottom:2px}}
.ax-n{{font-size:11px}}
.ax-t{{height:7px;border-radius:4px;background:{ACCENT_SOFT};overflow:hidden}}
.ax-f{{display:block;height:100%;border-radius:4px}}
.ax-v{{font-size:11px;text-align:right;font-variant-numeric:tabular-nums}}
.ax-v em{{font-style:normal;color:{MUTED}}}
.ax-w{{margin:0 0 7px;font-size:10px;color:{MUTED};line-height:1.45}}
.page{{padding:16px 0;border-top:1px solid {BORDER};page-break-inside:avoid}}
.page-no{{font-size:10px;color:{MUTED};font-weight:600}}
.key{{font-weight:600;font-size:13.5px;margin:4px 0 8px}}
.body{{margin:0 0 10px;padding-left:18px}}
.card{{display:grid;grid-template-columns:78px 1fr;gap:4px 10px;padding:9px;
  background:{SURFACE_2};border-radius:6px;font-size:11.5px;margin:8px 0}}
.card dt{{color:{MUTED}}} .card dd{{margin:0}}
table{{width:100%;border-collapse:collapse;font-size:11px;margin:6px 0}}
th,td{{padding:4px 7px;border-bottom:1px solid {BORDER_SOFT};text-align:left;vertical-align:top}}
th{{background:{SURFACE_2};font-weight:600}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
.signal td{{border:none;padding:3px 6px}}
.stat{{padding:12px;background:{SURFACE_2};border-radius:6px;text-align:center}}
.stat-v{{font-size:26px;font-weight:600}} .stat-v em{{font-style:normal;font-size:12px;color:{MUTED}}}
.stat-l{{font-size:11px;color:{MUTED}}}
.fig{{margin:10px 0;padding:10px;border:1px solid {BORDER_SOFT};border-radius:6px;
  page-break-inside:avoid}}
.fig figcaption{{margin-bottom:6px}}
.tbl{{margin:10px 0;page-break-inside:avoid}}
.note{{margin:5px 0 0;font-size:10px;color:{WARN}}}
.src{{margin:4px 0 0;font-size:10px;color:{MUTED}}}
.gap{{margin:5px 0;font-size:11px;color:{WARN}}}
.note{{margin:6px 0;font-size:11px;color:{MUTED};padding:6px 9px;
  border-left:2px solid {BORDER};background:{SURFACE_2}}}
.merged{{font-size:10px;color:{MUTED};font-style:italic}}
.meta{{margin-top:8px;padding-top:6px;border-top:1px solid {BORDER_SOFT};
  font-size:10px;color:{MUTED};display:flex;gap:14px;flex-wrap:wrap}}
.foot{{margin:14px 0 0;padding-top:8px;border-top:1px solid {BORDER_SOFT};
  font-size:10px;color:{MUTED}}}
/* ── 표지 · 목차 · 러닝헤더 (M9 · N95) ────────────────────────── */
.cover-sheet{{display:flex;flex-direction:column;min-height:240mm}}
.cs-top{{flex:1}}
.cs-kind{{font-size:11px;letter-spacing:2px;color:{MUTED};text-transform:uppercase}}
.cs-title{{font-size:32px;font-weight:700;margin:6px 0 2px;line-height:1.25}}
.cs-sub{{font-size:14px;color:{MUTED};margin-bottom:22px}}
.cs-rule{{height:3px;background:{INK};margin:0 0 22px}}
.cs-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 24px;margin:18px 0}}
.cs-k{{font-size:10px;color:{MUTED}}}
.cs-v{{font-size:13px;font-weight:600}}
.cs-bot{{margin-top:auto;padding-top:16px;border-top:1px solid {BORDER}}}
.toc-sheet ol{{margin:0;padding-left:22px;font-size:12.5px;line-height:2}}
.toc-sheet li::marker{{color:{MUTED}}}
.toc-t{{color:{MUTED};font-size:11px;margin-left:6px}}
.runhead,.runfoot{{display:none}}
.ch{{font-size:10px;color:{MUTED};font-weight:600;letter-spacing:.5px}}

/* ── 인쇄 판형 (M9 · N95) ──────────────────────────────────────
   증권사 리서치 보고서 판형: A4 · 표지 한 장 · 목차 한 장 ·
   장마다 새 쪽 · 매 쪽 러닝헤더와 고지 바닥글.

   ★ 쪽(sheet) 번호는 **찍지 않는다.** 우리가 아는 것은 `page["page"]` 곧
     **장 번호**이고, 한 장이 A4 한 쪽을 넘치면 둘이 갈라진다. 브라우저는
     쪽 번호를 CSS 로 알려 주지 않으므로(Chrome 은 @page 여백상자를 지원하지 않는다),
     아는 것만 찍는다 — `제 N 장 / 총 M 장`. 이 값은 넘치든 말든 **항상 맞다**.
     쪽 번호까지 필요하면 Chrome 인쇄 대화상자의 '머리글/바닥글' 을 켜라. */
@page{{size:A4;margin:16mm 14mm 15mm}}
@media print{{
  body{{padding:0;max-width:none;font-size:10.5px;line-height:1.55}}
  /* 러닝헤더·바닥글 — position:fixed 는 인쇄에서 **매 쪽 반복된다** */
  .runhead{{display:block;position:fixed;top:0;left:0;right:0;
    font-size:9px;color:{MUTED};border-bottom:.5px solid {BORDER};padding-bottom:3px}}
  .runfoot{{display:block;position:fixed;bottom:0;left:0;right:0;
    font-size:8.5px;color:{MUTED};border-top:.5px solid {BORDER};padding-top:3px}}
  .rh-r,.rf-r{{float:right}}
  /* 고정 머리글·바닥글과 겹치지 않게 본문에 여유를 준다 */
  .sheet-body{{padding:10mm 0 9mm}}
  .cover-sheet,.toc-sheet{{page-break-after:always;min-height:0}}
  /* 장마다 새 쪽. 표지·목차의 `page-break-after` 와 붙어도 **빈 쪽이 생기지 않는다** —
     맞닿은 강제 개행은 브라우저가 하나로 합친다 (CSS Fragmentation §3). 그래서
     표지 → 목차 → 표지 요약 → 1장 … 이 각각 한 쪽씩 간다. */
  .page{{page-break-before:always;border-top:none;padding-top:0}}
  /* 그림과 표만 쪼개지 않는다. **카드·표지요약에는 걸지 않는다** — 실측에서
     둘 다 한 쪽에 안 들어가는 크기라, `avoid` 를 걸면 브라우저가 앞쪽을 비워 두고
     넘겨 버려 종이만 늘었다 (CORP-R 27쪽 기준 측정). 안 들어가는 것에 `avoid` 는
     지켜지지도 않는다. */
  .fig,.tbl{{page-break-inside:avoid}}
  .card dt,.card dd{{page-break-inside:avoid}}
  h2,h3{{page-break-after:avoid}}
  tr,li{{page-break-inside:avoid}}
  a{{color:inherit;text-decoration:none}}
}}
@media screen{{
  .cover-sheet{{min-height:0}}
  .toc-sheet{{padding:14px 0;border-top:1px solid {BORDER}}}
}}
"""


def to_html(pack: Dict, report: Dict) -> str:
    """Context Pack + 페이지 계약 → 자체완결 HTML 한 덩어리."""
    from . import export_md            # DISCLAIMER 한 곳에서만 정의한다

    charter = pack.get("C0_charter") or {}
    target = charter.get("target") or {}
    extension = pack.get("CX_workstream") or {}
    charts = {c.get("id", ""): c for c in (extension.get("charts") or [])}
    tables = {t.get("key", ""): t for t in (report.get("tables") or [])}
    title = charter.get("task_name") or f"{target.get('name', '')} 리서치"

    headline = extension.get("headline") or {}
    workstream = charter.get("workstream_id") or ""
    pages = report.get("pages") or []

    # ── 표지 한 장 (M9 · N95) ──
    # 판정을 지어내지 않는다 — `headline.verdict` 가 워크스트림마다 내는 우리 판정을
    # 그대로 옮긴다 (`headline.py` 머리말: BUY 자리에 우리 판정을 놓는다).
    verdict = headline.get("verdict") if isinstance(headline.get("verdict"), dict) else {}
    out = [
        f'<div class="runhead">{esc(title)} · {esc(workstream)}'
        f'<span class="rh-r">기준일 {esc(charter.get("as_of"))}</span></div>',
        f'<div class="runfoot">GIC 학회 내부 학습 자료 — 투자 권유가 아닙니다'
        f'<span class="rf-r">{esc(report.get("generated_at"))} 생성</span></div>',
        '<div class="sheet-body">',
        '<section class="cover-sheet"><div class="cs-top">',
        f'<div class="cs-kind">{esc(WORKSTREAM_LABEL.get(workstream, workstream))}</div>',
        f'<div class="cs-title">{esc(title)}</div>',
        f'<div class="cs-sub">{esc(target.get("code") or "")} · '
        f'분석 기준일 {esc(charter.get("as_of"))}</div>',
        '<div class="cs-rule"></div>',
        '<div class="cs-grid">'
        + "".join(f'<div><div class="cs-k">{esc(k)}</div><div class="cs-v">{esc(v)}</div></div>'
                  for k, v in (
                      ("워크스트림", f'{workstream} · {report.get("format", "")}'),
                      ("구성", f'{report.get("page_count", 0)}장 (상한 {report.get("max_pages", 15)}장)'),
                      # ★ 판정을 지어내지 않는다 — `headline` 이 낸 우리 판정을
                      #   그대로 옮기고, **무엇에 대한 판정인지**(`kind`)를 함께 적는다.
                      #   `BUY` 로 읽히면 안 된다 (`headline.py` 머리말).
                      (f'AI 제안 — {verdict.get("kind") or "판정"}',
                       verdict.get("label") or "판정 없음"),
                      ("사람 결정",
                       verdict.get("human_decision") or "승인 전 — AI 는 제안만 한다"),
                      ("근거", f'E- {len(pack.get("C1_evidence") or [])}건 · '
                               f'D- {len(pack.get("C2_data") or [])}건'),
                      ("스키마", pack.get("schema_version") or ""))) + '</div>',
        '</div><div class="cs-bot">'
        f'<div class="disclaimer">{esc(export_md.DISCLAIMER)}</div></div></section>',
        # ── 목차 한 장 ──
        # ★ **장 번호**를 적는다 (쪽 번호가 아니다). 한 장이 A4 한 쪽을 넘치면
        #   둘이 갈라지는데, 브라우저가 쪽 번호를 CSS 로 주지 않으므로 아는 것만 적는다.
        '<section class="toc-sheet"><h2>목차</h2>'
        '<p class="muted">아래 번호는 <b>장 번호</b>다 — 인쇄 쪽 번호와 다를 수 있다.</p><ol>'
        + "".join(f'<li>{esc(p.get("title"))}'
                  f'<span class="toc-t">slot {p.get("slot")}</span></li>' for p in pages)
        + '</ol></section>',
        _headline_html(headline)]

    for page in report.get("pages") or []:
        # ★ `쪽` 이 아니라 `장` 이다 (M9 · N95). 한 장이 A4 한 쪽을 넘치면 둘이 갈라지는데
        #   브라우저는 쪽 번호를 CSS 로 주지 않는다. **아는 것만 찍는다.**
        out.append(f'<section class="page"><div class="page-no ch">'
                   f'제 {page.get("page")} 장 / 총 {report.get("page_count")} 장</div>'
                   f'<h2>{esc(page.get("title"))}</h2>'
                   f'<p class="key">{esc(page.get("key_message"))}</p>'
                   '<ul class="body">'
                   + "".join(f"<li>{esc(line)}</li>" for line in page.get("body") or [])
                   + "</ul>")
        out.append(_chart_block(charts.get(page.get("visual_id") or "")))
        for key in page.get("table_keys") or []:
            out.append(_table_html(tables.get(key)))
        card = page.get("interpretation") or {}
        if card.get("id"):
            # 여섯 칸을 **전부** 낸다. M8 까지 화면(`rp-card`)만 `causal_hypothesis` 를
            # 빠뜨렸는데 M9 에서 화면도 여섯 칸이 됐다 (N91) — 이제 셋이 같은 것을 낸다.
            out.append('<dl class="card">' + "".join(
                f"<dt>{esc(label)}</dt><dd>{esc(card.get(key))}</dd>"
                for key, label in (("observation", "관찰"), ("meaning", "의미"),
                                   ("causal_hypothesis", "인과 가설"),
                                   ("alternative", "다른 설명"), ("limitation", "한계"),
                                   ("next_check", "다음 확인")) if card.get(key)) + "</dl>")
        if page.get("merged_from"):
            out.append(f'<p class="merged">이 장은 slot '
                       f'{esc(", ".join(str(s) for s in page["merged_from"]))} 을 합친 것이다 '
                       '— 자료가 없는 장을 억지로 만들지 않는다</p>')
        # 발표 노트 (M9 · N92) — API JSON 에만 있고 아무 데도 안 나오던 값이다.
        if page.get("presenter_note"):
            out.append(f'<p class="note">🗣 <b>발표 노트</b> — {esc(page["presenter_note"])}</p>')
        for gap in page.get("gaps") or []:
            out.append(f'<p class="gap">⚠ {esc(gap)}</p>')
        out.append(f'<div class="meta"><span>출처: '
                   f'{esc(" · ".join(page.get("sources") or []) or "—")}</span>'
                   f'<span>신뢰도 {esc(page.get("confidence"))}</span>'
                   f'<span>{esc(page.get("human_decision"))}</span></div></section>')

    # 부록 — 장에 못 붙은 차트·표를 **버리지 않는다**
    extra_charts = [charts[i] for i in (report.get("extra_chart_ids") or []) if i in charts]
    extra_tables = [tables[k] for k in (report.get("extra_table_keys") or []) if k in tables]
    if extra_charts or extra_tables:
        out.append('<section class="page"><h2>부록 · 장에 붙지 않은 그림과 표</h2>'
                   '<p class="muted">만들었으나 놓을 장이 양식에 없다. 버리지 않고 여기 싣는다.</p>')
        out += [_chart_block(c) for c in extra_charts]
        out += [_table_html(t) for t in extra_tables]
        out.append("</section>")

    out.append(_references(pack))

    # 밀도 조정 — 무엇을 합쳤고 무엇을 **만들지 않았는지**를 갈라 밝힌다 (M9 · N89).
    # 인쇄본만 이 목록이 없어서, 읽는 사람이 빠진 장을 알 방법이 없었다.
    if report.get("omissions") or report.get("merged"):
        out.append('<section class="page"><h2>밀도 조정 — 합친 장과 만들지 않은 장</h2>'
                   '<p class="muted">15장은 상한이지 목표가 아니다. 자료가 없는 장을 '
                   '억지로 만들지 않았고, 그 사실을 여기 남긴다.</p><ul>'
                   + "".join(f'<li>{esc(note)}</li>' for note in (report.get("merged") or []))
                   + "</ul></section>")

    logs = pack.get("logs") or {}
    gaps = logs.get("gaps") or []
    if gaps:
        out.append('<section class="page"><h2>미해결 Gap</h2>'
                   '<p class="muted">없는 자료를 만들지 않았다. 무엇이 없는지를 밝힌 목록이다.</p>'
                   '<table><thead><tr><th>ID</th><th>영향받는 주장</th><th>왜 없나</th>'
                   '<th>다음 확인</th></tr></thead><tbody>'
                   + "".join(f'<tr><td>{esc(g.get("id"))}</td>'
                             f'<td>{esc(g.get("affected_claim_or_field"))}</td>'
                             f'<td>{esc(g.get("why_unavailable"))}</td>'
                             f'<td>{esc(g.get("next_check"))}</td></tr>' for g in gaps)
                   + "</tbody></table></section>")

    out.append(f'<p class="foot">{esc(export_md.DISCLAIMER)} · '
               f'생성 {esc(report.get("generated_at"))} · '
               'CDN·외부 파일을 부르지 않는 자체완결 문서다. '
               '<b>PDF 로 저장하려면</b> Ctrl+P(⌘P) → 대상을 “PDF 로 저장”, '
               '용지 A4·배율 기본·<b>배경 그래픽 켜기</b>. '
               '쪽 번호가 필요하면 “머리글/바닥글”도 켜라 — 문서 안의 번호는 '
               '<b>장 번호</b>이지 쪽 번호가 아니다.</p>')
    out.append("</div>")                       # .sheet-body — 고정 머리글·바닥글과 겹치지 않게 감쌌다

    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>{STYLE}</style></head>'
            f'<body>{"".join(out)}</body></html>')
