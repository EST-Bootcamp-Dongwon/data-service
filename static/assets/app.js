/* 화면 공통 유틸 (App)
 *
 * 화면 4개가 함께 쓰는 작은 도구 모음이다.
 * - API 호출과 에러 처리
 * - 숫자·금액·등락 표기 (한국 증시 관행: 상승 빨강 · 하락 파랑)
 * - ApexCharts 생성/파괴 관리
 * - 화면 간 내비게이션
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

  /** POST 요청 (JSON 본문 선택). */
  async function post(path, payload) {
    const res = await fetch(API_BASE + path, {
      method: 'POST',
      headers: payload ? { 'Content-Type': 'application/json' } : {},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const text = await res.text();
    let body;
    try { body = JSON.parse(text); } catch { body = text; }
    if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
    return body;
  }

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
    if (rate == null || rate === 0) return color('muted');
    return rate > 0 ? color('up') : color('down');
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
    const base = {
      theme: { mode: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' },
      chart: { fontFamily: 'inherit', background: 'transparent' },
      grid: { borderColor: color('border'), strokeDashArray: 3 },
      tooltip: { theme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' },
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

  // 화면 목록 — 새 화면을 추가하면 여기에만 한 줄 넣으면 모든 화면의 메뉴에 반영된다.
  const PAGES = [
    { key: 'home', href: '/', label: '홈' },
    { key: 'kosis', href: '/kosis', label: 'KOSIS 통계' },
    { key: 'krx', href: '/krx', label: 'KRX 일별 시세' },
    { key: 'yf', href: '/yf', label: '야후 파이낸스' },
    { key: 'quant', href: '/quant', label: '퀀트 분석' },
    { key: 'users', href: '/users', label: '사용자 API' },
    { key: 'tetris', href: '/tetris', label: '테트리스' },
    { key: 'docs', href: '/docs', label: 'API 문서' },
  ];

  /** 현재 화면을 표시한 내비게이션을 그린다. */
  function renderNav(current) {
    const nav = document.getElementById('nav');
    if (!nav) return;
    nav.innerHTML = PAGES.map((p) =>
      `<a href="${p.href}" class="${p.key === current ? 'on' : ''}">${p.label}</a>`
    ).join('');
  }

  return { API_BASE, get, post, num, signed, won, isoDate, signClass, color, signColor,
           draw, debounce, renderNav, PAGES };
})();
