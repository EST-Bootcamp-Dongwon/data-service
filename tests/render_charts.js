/* 차트 렌더 검사 — **진짜 ApexCharts** 로 그려 본다 (M4)
 *
 * 왜 만들었나.
 *   `regress_ui.js` 는 문법만 보고, `render_timeseries.js` 는 ApexCharts 를 **대역**으로 바꿔 돌린다.
 *   그래서 "옵션을 잘못 줘서 차트가 통째로 안 그려지는" 오류가 두 검사 모두를 통과해 배포까지 갔다.
 *   실제로 배포본 `/market` 에서 두 건이 그렇게 살아 있었다.
 *
 *     · 캔들 + 이동평균을 **생배열**로 넘김 → `Cannot read properties of null (reading 'y')`
 *       (차트 종류가 candlestick 이면 ApexCharts 는 **모든 계열**을 캔들 파서에 넣어 `data[j].y` 를
 *        읽는다. 이동평균 앞쪽은 워밍업이라 null 이므로 그 자리에서 터진다)
 *     · 막대 차트에 `tooltip.shared: true` 만 줌 → ApexCharts v4 의 막대 기본값이
 *       `intersect: true` 라 "shared 와 intersect 를 같이 못 쓴다" 예외가 난다 (v3 과 달라진 부분)
 *
 *   둘 다 예외가 `chart.render()` 의 프라미스 안에서 터져 **화면에는 빈 칸만 남는다.**
 *   눈으로 보지 않으면 모른다. 그래서 여기서 잡는다.
 *
 * 쓰는 법
 *   npm install --no-save jsdom apexcharts@4      # 처음 한 번 (배포 번들과 무관 — tests/ 는 제외됨)
 *   node tests/render_charts.js                   # 127.0.0.1:8000 을 본다
 *   node tests/render_charts.js http://…          # 배포본을 볼 때
 *   node tests/render_charts.js market timeseries # 화면을 골라서
 *
 * 보는 것
 *   1. 각 화면의 모든 차트가 **예외 없이** 그려지는가 (생성 · render 양쪽)
 *   2. 차트가 실제로 SVG 를 남겼는가 (조용히 빈 칸이 되지 않았는가)
 *   3. 실행 중 창 오류가 없는가
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APEX_PATH = path.join(ROOT, 'node_modules/apexcharts/dist/apexcharts.js');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch {
  console.error('jsdom 이 없습니다 — npm install --no-save jsdom apexcharts@4');
  process.exit(1);
}
if (!fs.existsSync(APEX_PATH)) {
  console.error('apexcharts 가 없습니다 — npm install --no-save jsdom apexcharts@4');
  process.exit(1);
}

const args = process.argv.slice(2);
const BASE = args.find((a) => a.startsWith('http')) || 'http://127.0.0.1:8000';
const ONLY = args.filter((a) => !a.startsWith('http'));

/* 화면 목록. `steps` 는 초기 로딩 뒤에 눌러 볼 것들이다 —
 * 탭 안쪽 차트는 열어 보지 않으면 그려지지 않는다. */
const PAGES = [
  {
    key: 'dashboard',
    file: 'dashboard.html',
    // 대시보드 카드의 스파크라인은 ApexCharts 가 아니라 `shell.js` 가 직접 그린 SVG 다.
    // 차트가 0개인 것이 정상이므로, 대신 스파크라인이 실제로 칠해졌는지를 본다.
    expect: (doc) => {
      const painted = doc.querySelectorAll('svg.spark').length;
      return painted > 0
        ? { note: `카드 스파크라인 ${painted}개 (ApexCharts 가 아니라 shell.js 가 그린다)` }
        : { problem: '카드 스파크라인이 하나도 안 그려졌다' };
    },
  },
  {
    key: 'market',
    file: 'market.html',
    steps: [
      { click: '#viewTabs button[data-view="overlay"]', label: '겹쳐보기 탭' },
      { click: '#overlayChips input', label: '지표 하나 선택' },
      { click: '#viewTabs button[data-view="breadth"]', label: '시장의 폭 탭' },
      { click: '#viewTabs button[data-view="compare"]', label: '종목 비교 탭' },
    ],
  },
  {
    key: 'timeseries',
    file: 'timeseries.html',
    steps: [
      { click: '#viewTabs button[data-view="decompose"]', label: '분해 탭' },
      { click: '#viewTabs button[data-view="forecast"]', label: '예측 탭' },
    ],
  },
  {
    key: 'krx',
    file: 'krx.html',
    // 캔들 카드는 표에서 종목을 눌러야 열린다 — 안 누르면 검사에서 통째로 빠진다
    steps: [{ click: '#tbody tr', label: '표에서 종목 하나 클릭' }],
  },
  { key: 'quant', file: 'quant.html' },
  { key: 'yf', file: 'yf.html' },
  { key: 'stock', file: 'stock.html' },
  { key: 'kosis', file: 'kosis.html' },
  // 리서치 리포트 차트 (M8 · N84) — 네 작업을 따로 돈다.
  //
  // 이 화면만 로직이 **외부 파일**(`research.js`)에 있고, 차트를 보려면 12상태를 끝까지
  // 돌려야 한다. 그래서 `drive` 로 직접 몬다. 작업마다 차트 유형이 갈라지므로
  // (기업 = bar-h·bar-line·scatter·range·timeline·radar / 산업 = line·signal·stat)
  // 하나만 돌리면 절반을 검사하지 않은 채로 통과한다.
  //
  // `regress_ui.js` 4절은 같은 흐름을 **가짜 fetch** 로 돈다. 그쪽은 화면 로직을 보고,
  // 여기는 **진짜 ApexCharts + 진짜 서버**로 옵션이 실제로 그려지는지를 본다.
  researchPage('CORP-R', '005930', '삼성전자'),
  researchPage('CORP-TP', '005930', '삼성전자'),
  researchPage('IND-R', '261', '반도체 제조업'),
  researchPage('IND-TP', '261', '반도체 제조업'),
  // 620 을 하나 더 돈다 — 1위가 여럿인 산업이라 민감도 차트가 **세로 막대(bar)** 로 나온다.
  // 261 은 1위가 한 곳이라 `stat`(값 하나)으로 접히므로, 261 만 돌면 `bar` 를 영영 안 그린다
  // (실측 2026-08-03: 261·264·204·282·301·101·581·272 는 전부 stat · 620 만 bar).
  researchPage('IND-TP', '620', '컴퓨터 프로그래밍·시스템 통합', 'ind-tp-bar'),
];

function researchPage(workstream, code, name, keySuffix) {
  return {
    key: `research-${keySuffix || workstream.toLowerCase()}`,
    file: 'research.html',
    extraAssets: ['static/assets/research.js'],
    drive: async ({ doc, window, settle }) => {
      const R = window.Research;
      if (!R) return { problem: 'Research 전역이 없다 — research.js 가 안 실려 있다' };
      await settle();

      const card = doc.querySelector(`.ws-card[data-ws="${workstream}"]`);
      if (!card) return { problem: `작업 카드가 없다: ${workstream}` };
      card.click();
      await settle();

      // 대상을 직접 꽂는다 — 자동완성을 타면 검사가 서버 응답 순서에 매인다
      R.state.target = { code, name };
      const runBtn = doc.getElementById('runBtn');
      runBtn.disabled = false;
      runBtn.click();

      // 12상태를 도는 동안 질문 카드(H01·H04·H08)가 뜨면 추천안을 골라 넘긴다.
      for (let i = 0; i < 1200; i++) {
        await sleep(100);
        const q = doc.getElementById('modalQuestion');
        if (q && !q.hidden) {
          const first = doc.querySelector('#qOpts .qopt');
          if (first) first.click();
          doc.getElementById('qNext').click();
        }
        if (!R.state.running && R.state.report) break;
      }
      if (!R.state.report) return { problem: '12상태를 끝내지 못했다 (리포트가 없다)' };
      await settle();
      return {};
    },
    expect: (doc, window) => {
      const charts = ((window.Research.state.pack || {}).CX_workstream || {}).charts || [];
      if (!charts.length) {
        return { problem: 'CX_workstream.charts 가 비었다 — 서버가 계열을 안 실었다' };
      }
      const drawable = charts.filter((c) => c.drawable);
      // 그림이 필요한 유형만 자리를 요구한다. signal·stat 은 순수 HTML 이다.
      const needSvg = drawable.filter((c) => !['signal', 'stat'].includes(c.kind));
      const missing = needSvg.filter((c) => !doc.getElementById(`rpchart-${c.id}`));
      if (missing.length) {
        return { problem: `차트 자리가 없다: ${missing.map((c) => c.id).join(', ')}` };
      }
      // 표 보기가 함께 나오는가 — 라이트 팔레트 대비 완화 규칙이 요구하는 자리다
      if (drawable.length && !doc.querySelectorAll('.rp-chart-table').length) {
        return { problem: '차트에 숫자 보기(표)가 하나도 없다 — U3 완화 규칙 위반' };
      }
      // signal·stat 은 ApexCharts 를 안 쓰므로 **HTML 이 실제로 나왔는지**를 따로 본다
      for (const kind of ['signal', 'stat']) {
        if (drawable.some((c) => c.kind === kind)
            && !doc.querySelectorAll(`.rp-${kind}`).length) {
          return { problem: `${kind} 판이 화면에 안 나왔다` };
        }
      }
      return { note: `${workstream} 차트 ${charts.length}건 (그릴 수 있는 것 ${drawable.length}) · `
        + `유형 ${[...new Set(drawable.map((c) => c.kind))].join('/')}` };
    },
  };
}

const apexSource = fs.readFileSync(APEX_PATH, 'utf8');
const assets = ['static/assets/app.js', 'static/assets/shell.js']
  .map((p) => fs.readFileSync(path.join(ROOT, p), 'utf8'));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 한 화면을 띄워 차트를 전부 그려 보고 결과를 돌려준다. */
async function runPage(page) {
  const html = fs.readFileSync(path.join(ROOT, 'static/pages', page.file), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: BASE });
  const { window } = dom;
  const doc = window.document;

  const failures = [];                       // { chart, message }
  const drawn = new Map();                   // 차트 id → { options, svg }
  const windowErrors = [];

  // ── jsdom 이 없는 브라우저 API 채우기 ──
  // ApexCharts 는 폭·글자 길이를 재서 축을 잡는다. jsdom 은 전부 0 을 주므로 값을 준다.
  Object.defineProperty(window.Element.prototype, 'clientWidth', { get: () => 900, configurable: true });
  Object.defineProperty(window.Element.prototype, 'clientHeight', { get: () => 400, configurable: true });
  window.SVGElement.prototype.getBBox = function () { return { x: 0, y: 0, width: 60, height: 14 }; };
  window.SVGElement.prototype.getComputedTextLength = function () { return 40; };
  window.SVGElement.prototype.getScreenCTM = function () {
    const m = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
    return { ...m, inverse: () => ({ ...m }) };
  };
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.Element.prototype.scrollIntoView = function () { /* jsdom 에는 없다 */ };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });

  // ── 요청은 진짜 서버로 넘긴다 ──
  let inflight = 0;
  let lastActivity = Date.now();
  // 검사가 끝난 뒤의 요청은 기록하지 않는다.
  //
  // 창을 일부러 닫지 않기 때문에(아래 주석) `shell.js` 의 티커바가 `setInterval` 로
  // **계속** 서버를 부른다 (`loadTicks` — 끌 수 있는 손잡이가 없다). 화면을 여러 개
  // 돌리면 끝난 창들의 폴링이 겹쳐서, 그중 하나가 실패할 때 **이미 통과한 화면의
  // 오류로 잡힌다** (실측: 리서치 4개를 잇달아 돌리면 4번째에서 `/api/dashboard/ticker`
  // 요청 실패가 났고, 같은 화면을 혼자 돌리면 통과했다).
  //
  // 티커바 실패 자체는 앱이 이미 "불러오지 못함" 으로 접는다 (`shell.js:212~215`).
  // 검사가 볼 것은 **검사하는 동안의** 요청이다.
  let checksDone = false;
  window.fetch = async (url, init) => {
    inflight += 1;
    lastActivity = Date.now();
    const target = new URL(String(url), BASE);
    try {
      return await fetch(target, init);
    } catch (error) {
      // 요청 자체가 실패하면(네트워크·타임아웃) 페이지는 조용히 빈 화면이 된다. 여기서 밝힌다.
      if (!checksDone) {
        windowErrors.push(`요청 실패 ${target.pathname}${target.search} — ${error.message}`);
      }
      throw error;
    } finally {
      inflight -= 1;
      lastActivity = Date.now();
    }
  };

  window.addEventListener('error', (e) => windowErrors.push(e.message));
  window.addEventListener('unhandledrejection', (e) => {
    windowErrors.push(`처리 안 된 예외 — ${(e.reason && e.reason.message) || e.reason}`);
  });

  // ── ApexCharts 로드 + 예외를 잡아 두는 껍데기 ──
  window.eval(apexSource);
  const RealApex = window.ApexCharts;

  window.ApexCharts = class TrackedApex {
    constructor(el, options) {
      this.id = el.id || '(id 없음)';
      this.el = el;
      drawn.set(this.id, { options, svg: 0 });
      try {
        this.inner = new RealApex(el, options);
      } catch (error) {
        // 생성 시점 예외 — 옵션 충돌(tooltip.shared × intersect 등)이 여기서 난다
        failures.push({ chart: this.id, message: `생성: ${error.message}` });
      }
    }

    render() {
      if (!this.inner) return Promise.resolve();
      try {
        return Promise.resolve(this.inner.render())
          .then(() => { drawn.get(this.id).svg = this.el.querySelectorAll('svg').length; })
          .catch((error) => { failures.push({ chart: this.id, message: `render: ${error.message}` }); });
      } catch (error) {
        // render 가 동기적으로 터지는 경우 (데이터 파싱 오류가 여기 속한다)
        failures.push({ chart: this.id, message: `render: ${error.message}` });
        return Promise.resolve();
      }
    }

    destroy() { try { if (this.inner) this.inner.destroy(); } catch { /* 파괴 실패는 검사 대상이 아니다 */ } }
    updateOptions(...a) { return this.inner ? this.inner.updateOptions(...a) : Promise.resolve(); }
    updateSeries(...a) { return this.inner ? this.inner.updateSeries(...a) : Promise.resolve(); }
  };

  // ── 공통 스크립트 + 화면 전용 스크립트 + 페이지 인라인 스크립트 실행 ──
  //
  // `/research` 는 로직이 외부 파일(`research.js`)에 있어 인라인 스크립트만으로는
  // 아무것도 돌지 않는다. `extraAssets` 로 그런 화면을 채운다.
  assets.forEach((code) => window.eval(code));
  for (const rel of page.extraAssets || []) {
    window.eval(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
  }
  const pageScript = [...doc.querySelectorAll('script')].filter((s) => !s.src)
    .map((s) => s.textContent).join('\n');
  try {
    window.eval(pageScript);
  } catch (error) {
    failures.push({ chart: '(스크립트)', message: error.message });
    return { failures, drawn, windowErrors };
  }

  /** 요청이 다 끝나고 **조용해질 때까지** 기다린다.
   *
   * 단순히 `inflight === 0` 만 보면 안 된다 — 요청과 요청 사이 빈틈에 걸려
   * 아직 시작도 안 한 화면을 "다 됐다" 로 판정한다 (배포본 콜드스타트에서 실제로 겪었다).
   * 그래서 **마지막 요청 이후 조용한 시간**까지 함께 본다. */
  async function settle(quietMs = 900, maxMs = 45000) {
    const started = Date.now();
    await sleep(150);
    while (Date.now() - started < maxMs) {
      if (inflight === 0 && Date.now() - lastActivity > quietMs) break;
      await sleep(150);
    }
    await sleep(250);                                  // 응답 처리 · 렌더까지 조금 더
  }

  await settle();
  for (const step of page.steps || []) {
    const el = doc.querySelector(step.click);
    if (!el) {
      failures.push({ chart: step.label, message: `누를 요소가 없다: ${step.click}` });
      continue;
    }
    el.click();
    await settle();
  }

  const notes = [];
  // 누르기만으로 못 가는 화면은 직접 몬다 (리서치는 12상태 왕복 + 질문 카드가 있다)
  if (page.drive) {
    let driven;
    try {
      driven = (await page.drive({ doc, window, settle })) || {};
    } catch (error) {
      driven = { problem: `몰다가 실패했다 — ${error.message}` };
    }
    if (driven.problem) failures.push({ chart: '(화면 몰기)', message: driven.problem });
  }

  if (page.expect) {
    const verdict = page.expect(doc, window) || {};
    if (verdict.problem) failures.push({ chart: '(화면 점검)', message: verdict.problem });
    if (verdict.note) notes.push(verdict.note);
  }

  // 창은 일부러 닫지 않는다. 배포본처럼 느린 서버에서는 settle 뒤에도 응답이 늦게 오는데,
  // 그때 창이 닫혀 있으면 페이지 스크립트가 `document` 를 잃고 통째로 죽는다.
  // 검사가 끝나면 process.exit 으로 한 번에 정리한다.
  //
  // 다만 **여기부터의 요청은 더 세지 않는다** — 이 화면 검사는 이미 끝났고,
  // 뒤에 남은 티커바 폴링이 다음 화면의 오류로 뒤섞이면 안 된다.
  checksDone = true;
  return { failures, drawn, windowErrors, notes };
}

async function main() {
  const targets = ONLY.length ? PAGES.filter((p) => ONLY.includes(p.key)) : PAGES;
  console.log(`\n차트 렌더 검사 · 서버 ${BASE} · 화면 ${targets.length}개\n`);

  let bad = 0;
  for (const page of targets) {
    process.stdout.write(`── /${page.key} `.padEnd(46, '─') + '\n');
    let result;
    try {
      result = await runPage(page);
    } catch (error) {
      bad += 1;
      console.log(`  \x1b[31m✗\x1b[0m  화면을 띄우지 못함 — ${error.message}\n`);
      continue;
    }

    const { failures, drawn, windowErrors, notes } = result;
    (notes || []).forEach((n) => console.log(`  \x1b[32m✓\x1b[0m  ${n}`));
    const empty = [...drawn.entries()].filter(([, v]) => v.svg === 0)
      .filter(([id]) => !failures.some((f) => f.chart === id));

    if (drawn.size === 0) {
      console.log('  \x1b[33m·\x1b[0m  그려진 차트 없음 (이 화면은 차트가 없거나 자료가 비었다)');
    } else {
      console.log(`  \x1b[32m✓\x1b[0m  차트 ${drawn.size}개 생성 — ${[...drawn.keys()].join(', ')}`);
    }
    failures.forEach((f) => {
      bad += 1;
      console.log(`  \x1b[31m✗\x1b[0m  ${f.chart} — ${f.message}`);
    });
    empty.forEach(([id]) => {
      bad += 1;
      console.log(`  \x1b[31m✗\x1b[0m  ${id} — 예외는 없는데 SVG 가 하나도 안 남았다 (빈 차트)`);
    });
    windowErrors.forEach((m) => {
      bad += 1;
      console.log(`  \x1b[31m✗\x1b[0m  창 오류 — ${m}`);
    });
    console.log('');
  }

  console.log(bad ? `결과: ${bad}건 실패 ✗\n` : '결과: 차트 이상 없음 ✓\n');
  process.exit(bad ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(1); });
