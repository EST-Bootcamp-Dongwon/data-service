/* 화면 셸 (Shell) — 사이드바 + 상단 티커바
 *
 * 모든 화면이 같은 뼈대를 쓰도록, 페이지는 **한 줄만** 부른다.
 *
 *     Shell.render('krx');
 *
 * 그러면 이 파일이
 *   1) `<body>` 에 이미 있던 내용을 전부 본문 영역으로 옮기고
 *   2) 왼쪽에 사이드바를, 위에 티커바를 만들어 끼운다.
 *
 * 화면을 추가할 때 고칠 곳은 아래 `NAV` 하나뿐이다. (예전 `App.renderNav` 의 `PAGES` 를 대체한다)
 *
 * 스파크라인(`Shell.spark`)과 상태등급(`Shell.grade`)도 여기 둔다.
 * 대시보드 카드가 쓰고, 앞으로 리포트 화면(M6)도 같은 것을 쓴다.
 */
window.Shell = (() => {
  'use strict';

  // ── 정보구조 (명세서 §7.1) ────────────────────────────────
  // `soon: true` 는 아직 만들지 않은 화면이다. 링크를 죽여 두고 '준비중'을 글자로 밝힌다.
  // (메뉴에 보여 주는 이유 — 앞으로 무엇이 생기는지가 정보구조의 일부라서다)
  const NAV = [
    {
      items: [
        { key: 'dashboard', href: '/', label: '대시보드', ico: '◈' },
        // 대시보드 카드는 작아서 모양이 안 보이고 기간도 못 바꾼다. 그 둘을 여기서 푼다.
        { key: 'market', href: '/market', label: '시장 상세', ico: '📉' },
      ],
    },
    {
      // M6 — 네 항목이 같은 화면(`research.html`)으로 간다. 12상태가 넷 다 같아
      // 화면을 나눌 이유가 없고, 갈라지는 것은 대상 입력과 리포트 양식뿐이다.
      title: '리서치',
      items: [
        { key: 'corp-r', href: '/research?ws=CORP-R', label: '기업 리서치', ico: '▤' },
        { key: 'corp-tp', href: '/research?ws=CORP-TP', label: '기업 Top Pick', ico: '▤' },
        { key: 'ind-r', href: '/research?ws=IND-R', label: '산업 리서치', ico: '▦' },
        { key: 'ind-tp', href: '/research?ws=IND-TP', label: '산업 Top Pick', ico: '▦' },
      ],
    },
    {
      title: '데이터 실험실',
      items: [
        { key: 'stock', href: '/stock', label: '종목 조회', ico: '🔎' },
        { key: 'yf', href: '/yf', label: '야후 파이낸스', ico: '💹' },
        { key: 'krx', href: '/krx', label: 'KRX 시세', ico: '📈' },
        { key: 'kosis', href: '/kosis', label: 'KOSIS 통계', ico: '📊' },
        { key: 'quant', href: '/quant', label: '퀀트 분석', ico: '🧮' },
        // M3 — numpy 로 직접 구현한 시계열 엔진(`/api/ts/*`)을 눈으로 보는 화면.
        // 명세 §7.1 IA 에는 없던 화면이라 변경 노트 N26 으로 남긴다.
        { key: 'timeseries', href: '/timeseries', label: '시계열 분석', ico: '🌊' },
      ],
    },
    {
      title: '시스템',
      items: [
        { key: 'status', href: '/#data-status', label: '데이터 상태', ico: '◍' },
        { key: 'guide', href: '/guide', label: '프로젝트 안내', ico: '📄' },
        { key: 'practice', href: '/practice/', label: '실습 아카이브', ico: '📦' },
        { key: 'docs', href: '/docs', label: 'API 문서', ico: '⚙' },
      ],
    },
  ];

  // 티커바 갱신 주기. 지수는 15분 이상 지연된 값이라 자주 부를 이유가 없다.
  const TICK_REFRESH_MS = 60_000;

  // 상태등급 기본 글리프 — 색만으로 뜻을 전하지 않기 위해 항상 글자와 함께 낸다.
  const GRADE_ICONS = {
    hot: '▲', warm: '▲', neutral: '●', cool: '▼', cold: '▼',
    good: '✓', warning: '!', serious: '▲', critical: '✕',
  };

  /** HTML 특수문자 이스케이프. 사용자 입력·API 문자열을 넣기 전에 반드시 통과시킨다. */
  function esc(v) {
    return String(v ?? '').replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // ==================================================
  // 1. 셸 조립
  // ==================================================
  /** 사이드바 HTML 을 만든다. 현재 화면(`current`)만 강조한다. */
  function navHtml(current) {
    return NAV.map((group) => {
      const links = group.items.map((item) => {
        const on = item.key === current ? ' on' : '';
        const soon = item.soon ? ' soon' : '';
        const tag = item.tag ? `<span class="tag">${esc(item.tag)}</span>` : '';
        // 준비중 화면은 href 를 빼서 클릭해도 이동하지 않게 한다
        const href = item.soon ? '' : ` href="${esc(item.href)}"`;
        const aria = item.soon ? ' aria-disabled="true"' : '';
        return `<a class="side-link${on}${soon}"${href}${aria}>` +
               `<span class="ico" aria-hidden="true">${item.ico}</span>${esc(item.label)}${tag}</a>`;
      }).join('');
      const title = group.title ? `<h6>${esc(group.title)}</h6>` : '';
      return `<nav class="side-group">${title}${links}</nav>`;
    }).join('');
  }

  /**
   * 셸을 그린다. 페이지의 스크립트 맨 앞에서 한 번 부른다.
   *
   * 이미 있던 `<body>` 자식들을 통째로 본문(`.body`)으로 옮기는 방식이라,
   * 각 화면의 HTML 은 손댈 필요가 없다. (이미 실행된 `<script>` 는 옮겨도 다시 실행되지 않는다)
   */
  function render(current) {
    if (document.querySelector('.shell')) {   // 두 번 불러도 안전하게
      markActive(current);
      return;
    }

    const body = document.createElement('div');
    body.className = 'body';
    // 자식 목록은 옮기는 동안 계속 바뀌므로, 먼저 배열로 복사해 두고 옮긴다
    Array.from(document.body.childNodes).forEach((node) => body.appendChild(node));

    const shell = document.createElement('div');
    shell.className = 'shell';
    shell.innerHTML = `
      <aside class="side" id="shellSide">
        <a class="side-brand" href="/">⚡ G.I.C Lab<small>api-test</small></a>
        ${navHtml(current)}
        <div class="side-foot">
          교육·리서치용 · 투자자문 아님<br />데이터는 15분 이상 지연됩니다
        </div>
      </aside>
      <div class="main">
        <header class="topbar">
          <button class="top-toggle" id="shellToggle" type="button" aria-label="메뉴 열기">☰</button>
          <div class="tick-list" id="shellTicks"></div>
          <div class="top-clock" id="shellClock"></div>
        </header>
      </div>`;
    shell.querySelector('.main').appendChild(body);
    document.body.appendChild(shell);

    bindDrawer();
    startClock();
    loadTicks();
    window.addEventListener('resize', debouncedRepaint);
  }

  /** 이미 그려진 셸에서 강조 위치만 바꾼다. */
  function markActive(current) {
    document.querySelectorAll('.side-link').forEach((el) => el.classList.remove('on'));
    const idx = [];
    NAV.forEach((g) => g.items.forEach((i) => idx.push(i)));
    const pos = idx.findIndex((i) => i.key === current);
    if (pos >= 0) document.querySelectorAll('.side-link')[pos].classList.add('on');
  }

  /** 모바일 서랍 — 버튼으로 열고, 바깥을 누르거나 링크를 고르면 닫는다. */
  function bindDrawer() {
    const side = document.getElementById('shellSide');
    const toggle = document.getElementById('shellToggle');
    if (!side || !toggle) return;
    toggle.addEventListener('click', (event) => {
      event.stopPropagation();
      side.classList.toggle('open');
    });
    side.addEventListener('click', (event) => {
      if (event.target.closest('a')) side.classList.remove('open');
    });
    document.addEventListener('click', (event) => {
      if (side.classList.contains('open') && !side.contains(event.target)) side.classList.remove('open');
    });
  }

  // ==================================================
  // 2. 티커바
  // ==================================================
  /** 티커바 시계 — 한국 시간(KST)으로 1초마다 갱신한다. */
  function startClock() {
    const el = document.getElementById('shellClock');
    if (!el) return;
    const tick = () => {
      const now = new Date().toLocaleTimeString('ko-KR', {
        timeZone: 'Asia/Seoul', hour12: false,
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
      el.textContent = `⏱ ${now} KST`;
    };
    tick();
    setInterval(tick, 1000);
  }

  /** 지수·환율을 받아 티커바를 채운다. 실패해도 화면 전체가 멈추지 않도록 조용히 접는다. */
  async function loadTicks() {
    const el = document.getElementById('shellTicks');
    if (!el) return;

    // 값이 오기 전까지 자리를 잡아 둔다 (레이아웃이 튀지 않게)
    const PLACEHOLDER = ['코스피', '코스닥', '나스닥', '원/달러'];
    el.innerHTML = PLACEHOLDER.map((label) =>
      `<span class="tick skeleton"><b>${label}</b><span class="v">····</span></span>`).join('');

    const paint = async () => {
      if (document.hidden) return;                 // 다른 탭을 보고 있으면 부르지 않는다
      try {
        const data = await App.get('/api/dashboard/ticker');
        const items = (data.items || []).filter((i) => i.ok);
        if (!items.length) throw new Error('빈 응답');
        el.innerHTML = items.map((item) => {
          const cls = App.signClass(item.rate);
          const digits = item.digits ?? 2;
          const delta = item.rate == null ? ''
            : `<span class="d ${cls}">${App.signed(item.diff, digits)} (${App.signed(item.rate, 2)}%)</span>`;
          return `<span class="tick"><b>${esc(item.label)}</b>` +
                 `<span class="v">${App.num(item.value, digits)}</span>${delta}</span>`;
        }).join('');
      } catch (error) {
        // 야후 요청 한도(429)·오프라인 등 — 티커바는 부가 정보라 한 줄 안내로 끝낸다
        el.innerHTML = `<span class="tick skeleton"><b>시세</b>` +
                       `<span class="v">불러오지 못함</span></span>`;
      }
    };

    paint();
    setInterval(paint, TICK_REFRESH_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) paint(); });
  }

  // ==================================================
  // 3. 스파크라인 — 순수 SVG (라이브러리 없음)
  // ==================================================
  // 그린 것을 기억해 두었다가 창 크기가 바뀌면 다시 그린다.
  // (뷰박스를 늘려 쓰면 선 굵기와 끝점이 찌그러지므로, 실제 픽셀 폭을 재서 그린다)
  const sparks = [];

  /**
   * 값 배열을 작은 꺾은선으로 그린다.
   *
   * @param {Element|string} target  그릴 자리 (요소 또는 id)
   * @param {number[]} values        값 배열 (null 은 건너뛴다)
   * @param {object} options         { color, area, height }
   */
  function spark(target, values, options = {}) {
    const el = typeof target === 'string' ? document.getElementById(target) : target;
    if (!el) return;
    const entry = { el, values: values || [], options };
    const known = sparks.findIndex((s) => s.el === el);
    if (known >= 0) sparks[known] = entry; else sparks.push(entry);
    paintSpark(entry);
  }

  function paintSpark({ el, values, options }) {
    const points = values.filter((v) => v != null && !Number.isNaN(v));
    const height = options.height || 34;
    if (points.length < 2) {
      el.innerHTML = `<div class="stat-sub">추이 없음</div>`;
      return;
    }

    // 실제 렌더 폭을 재서 좌표를 픽셀로 계산한다 (요소가 아직 안 붙었으면 240 으로 가정)
    const width = Math.max(el.clientWidth || 240, 60);
    const pad = 3;                                   // 끝점(반지름 4 + 링 2)이 잘리지 않게 여백
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || Math.abs(max) || 1;    // 값이 모두 같으면 가운데 수평선이 된다
    const stepX = (width - pad * 2) / (points.length - 1);
    const toY = (v) => pad + (1 - (v - min) / span) * (height - pad * 2);

    const coords = points.map((v, i) => [pad + i * stepX, toY(v)]);
    const line = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
    const color = options.color || 'var(--c1)';
    const [lastX, lastY] = coords[coords.length - 1];

    // 면적은 색상 그대로 10% 불투명도 — 진한 덩어리가 아니라 옅은 물감처럼 깐다
    const area = options.area === false ? '' :
      `<path class="area" fill="${color}" d="${line} L${lastX.toFixed(1)} ${height} L${pad} ${height} Z" />`;

    // 추이는 그림만으로 끝나지 않게 최저·최고·현재를 글자로도 남긴다
    // (스크린리더가 읽고, 마우스를 올리면 툴팁으로도 보인다)
    const fmt = (v) => v.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
    const label = `${points.length}개 구간 · 최저 ${fmt(min)} · 최고 ${fmt(max)} · 현재 ${fmt(points[points.length - 1])}`;

    el.innerHTML =
      `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" ` +
      `role="img" aria-label="${label}"><title>${label}</title>${area}` +
      `<path class="line" stroke="${color}" d="${line}" />` +
      `<circle class="end" cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="4" fill="${color}" />` +
      `</svg>`;
  }

  /** 창 크기가 바뀌면 전부 다시 그린다 (연속 호출은 마지막 한 번으로 묶는다). */
  let repaintTimer;
  function debouncedRepaint() {
    clearTimeout(repaintTimer);
    repaintTimer = setTimeout(() => sparks.forEach(paintSpark), 150);
  }

  // ==================================================
  // 4. 상태등급 배지
  // ==================================================
  /**
   * 상태등급 배지 HTML.  색만으로 뜻을 말하지 않도록 **글리프 + 글자**를 항상 함께 낸다.
   *
   * @param {string} level  hot·warm·neutral·cool·cold (시장 온도) / good·warning·serious·critical (시스템)
   * @param {string} text   화면에 쓸 말 (과열 · 중립 · 정상 …)
   */
  function grade(level, text) {
    const icon = GRADE_ICONS[level] || '●';
    return `<span class="grade ${esc(level)}"><span class="g-ico" aria-hidden="true">${icon}</span>${esc(text)}</span>`;
  }

  return { NAV, render, markActive, spark, grade, esc };
})();
