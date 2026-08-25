/* 화면 공통 유틸 (App)
 *
 * 모든 화면이 함께 쓰는 작은 도구 모음이다.
 * - API 호출과 에러 처리
 * - 숫자·금액·등락 표기 (한국 증시 관행: 상승 빨강 · 하락 파랑)
 * - ApexCharts 생성/파괴 관리 + 차트 공통 기본값 (M1 디자인 토큰 반영)
 *
 * 화면 간 내비게이션은 M1 에서 `shell.js` 로 옮겼다. (사이드바 + 티커바)
 *
 * 전역을 더럽히지 않도록 `App` 하나만 window 에 붙인다.
 */
window.App = (() => {
  'use strict';

  // file:// 로 직접 열었을 때만 서버 주소를 붙인다.
  // 서버에서 서빙되면 빈 문자열이라 같은 오리진 상대경로가 되어 CORS 문제가 없다.
  const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';

  /** GET 요청 후 JSON 을 돌려준다. 실패하면 FastAPI 의 detail 메시지를 담아 예외를 던진다. */
  async function get(path) {
    const res = await fetch(API_BASE + path);
    const text = await res.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      body = text;                                  // JSON 이 아니면(서버 다운 등) 원문 그대로
    }
    if (!res.ok) {
      const detail = (body && body.detail) || text || `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return body;
  }

  /** POST 요청 (JSON 본문 선택).
   *
   * ⚠️ **`send` 에 위임한다.** 예전에는 여기서 따로 오류를 만들었는데, 그쪽에만
   *    "`detail` 이 객체일 수 있다" 는 수정이 들어가 있었다 — 그래서 자료 보관함의
   *    `503`(`{reason, hints}`)이 `post` 로 오면 화면에 **"[object Object]"** 가 뜨고
   *    처방이 통째로 사라졌다. 수집 API 가 `503`·`429`·`404` 를 내는 첫 POST 경로라
   *    지금 드러난다. 오류 처리는 한 곳에만 둔다.
   */
  const post = (path, payload) => send('POST', path, payload);

  /** JSON 본문을 실어 보내는 요청 (PATCH·DELETE·PUT). `get`·`post` 와 오류 처리가 같다. */
  async function send(method, path, payload) {
    const res = await fetch(API_BASE + path, {
      method,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const text = await res.text();
    let body;
    try { body = JSON.parse(text); } catch { body = text; }
    if (!res.ok) {
      // ⚠️ `detail` 이 객체일 수 있다 — 자료 보관함의 503 은 {reason, hints} 를 담는다.
      //    문자열로 가정하면 화면에 "[object Object]" 가 뜨고 처방이 통째로 사라진다.
      const detail = (body && body.detail) ?? `HTTP ${res.status}`;
      const error = new Error(typeof detail === 'string' ? detail : (detail.reason || `HTTP ${res.status}`));
      error.detail = detail;
      error.status = res.status;
      throw error;
    }
    return body;
  }

  const patch = (path, payload) => send('PATCH', path, payload);
  const del = (path) => send('DELETE', path);

  // ── 표기 ──────────────────────────────
  /** 천 단위 콤마. null·undefined 는 '-' 로. */
  function num(v, digits = 0) {
    if (v == null || Number.isNaN(v)) return '-';
    return Number(v).toLocaleString('ko-KR', { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  /** 부호를 항상 붙인다 (+1,200 / -0.72). 상승·하락을 눈으로 구분하기 위함이다. */
  function signed(v, digits = 0) {
    if (v == null || Number.isNaN(v)) return '-';
    const sign = v > 0 ? '+' : '';
    return sign + num(v, digits);
  }

  /** 원 단위 금액을 조·억으로 줄여 쓴다. (1,234,500,000,000 → 1조 2,345억) */
  function won(v) {
    if (v == null) return '-';
    const abs = Math.abs(v);
    if (abs >= 1e12) {
      const jo = Math.floor(abs / 1e12);
      const eok = Math.round((abs % 1e12) / 1e8);
      return `${v < 0 ? '-' : ''}${num(jo)}조${eok ? ` ${num(eok)}억` : ''}`;
    }
    if (abs >= 1e8) return `${num(Math.round(v / 1e8))}억`;
    if (abs >= 1e4) return `${num(Math.round(v / 1e4))}만`;
    return num(v);
  }

  /** YYYYMMDD → YYYY-MM-DD (이미 하이픈이 있으면 그대로) */
  function isoDate(v) {
    if (!v) return '-';
    return /^\d{8}$/.test(v) ? `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6)}` : v;
  }

  /** 등락률에 맞는 CSS 클래스 ('up' | 'down' | '') */
  function signClass(rate) {
    if (rate == null || rate === 0) return '';
    return rate > 0 ? 'up' : 'down';
  }

  /** CSS 변수에서 실제 색을 읽어온다. 다크모드가 켜져 있으면 다크 색이 나온다. */
  function color(name) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
    return value || '#888';
  }

  /** 등락률에 맞는 차트 색 (상승 빨강 · 하락 파랑 · 보합 회색) */
  function signColor(rate) {
    if (rate == null || rate === 0) return color('flat');
    return rate > 0 ? color('up') : color('down');
  }

  /**
   * 범주형 색을 **슬롯 순서대로** 앞에서 n개 돌려준다.
   *
   * 순서가 곧 색맹 안전성 장치라서, 돌려쓰거나(cycle) 순위에 따라 다시 칠하면 안 된다.
   * 계열이 9개를 넘으면 색을 만들지 말고 '기타'로 접거나 차트를 나눈다. (app.css 상단 U3 규칙)
   */
  function series(n = 8) {
    const slots = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7', 'c8'];
    return slots.slice(0, Math.max(1, Math.min(n, slots.length))).map(color);
  }

  // ── 차트 ──────────────────────────────
  /**
   * ApexCharts 를 그린다. 같은 자리에 다시 그릴 때는 기존 차트를 파괴한 뒤 만든다.
   * (파괴하지 않고 새로 만들면 캔버스가 겹쳐 쌓이면서 메모리가 계속 늘어난다.)
   */
  function draw(registry, elementId, options) {
    if (registry[elementId]) {
      registry[elementId].destroy();
      delete registry[elementId];
    }
    const el = document.getElementById(elementId);
    if (!el || !window.ApexCharts) return null;
    el.innerHTML = '';

    // 모든 차트에 공통으로 적용할 기본값. options 가 우선한다.
    // 색·굵기·격자는 U3 규칙을 따른다 — 격자는 표면에서 한 단계 뜬 1px **실선**(점선 금지),
    // 선은 2px, 계열 색은 슬롯 순서. 데이터만 진하고 나머지는 뒤로 물러난다.
    const dark = matchMedia('(prefers-color-scheme: dark)').matches;
    const base = {
      theme: { mode: dark ? 'dark' : 'light' },
      chart: { fontFamily: 'inherit', background: 'transparent' },
      colors: series(8),
      grid: { borderColor: color('grid'), strokeDashArray: 0 },
      tooltip: { theme: dark ? 'dark' : 'light' },
    };
    const merged = { ...base, ...options, chart: { ...base.chart, ...(options.chart || {}) } };

    const chart = new ApexCharts(el, merged);
    chart.render();
    registry[elementId] = chart;
    return chart;
  }

  // ── 기타 ──────────────────────────────
  /** 연속 호출을 마지막 한 번으로 묶는다 (검색창 타이핑마다 요청하지 않도록). */
  function debounce(fn, wait = 300) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  /** 예전 상단 가로 메뉴 자리. M1 부터는 셸(사이드바)이 대신하므로 그쪽으로 넘긴다.
   *
   * 화면 목록은 `shell.js` 의 `NAV` 하나로 모았다. 이 함수는 예전 호출부가 남아 있어도
   * 깨지지 않게 두는 다리이며, 새 코드는 `Shell.render(key)` 를 직접 부른다.
   */
  function renderNav(current) {
    if (window.Shell) Shell.render(current);
  }

  return { API_BASE, get, post, patch, del, num, signed, won, isoDate, signClass, color, signColor,
           series, draw, debounce, renderNav };
})();
