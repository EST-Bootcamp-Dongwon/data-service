/* `/timeseries` 화면 렌더 검사 (M3)
 *
 * 문법 검사(`regress_ui.js`)만으로는 **그리다가 터지는 오류**가 안 잡힌다.
 * 여기서는 jsdom 에 화면을 띄우고 **실제 API 응답**을 물려 스크립트를 끝까지 돌린다.
 * ApexCharts 는 캔버스가 필요해 못 쓰므로, 넘어온 옵션을 받아 적는 대역으로 바꾼다
 * (그래서 "차트에 무엇을 넘겼는가" 까지 확인할 수 있다).
 *
 *   node tests/render_timeseries.js               # 127.0.0.1:8000 을 본다
 *   node tests/render_timeseries.js <베이스URL>   # 배포본을 볼 때
 *
 * 보는 것
 *   1. 세 탭(진단·분해·예측)이 오류 없이 그려지는가
 *   2. 각 탭의 핵심 요소가 비어 있지 않은가
 *   3. 차트에 넘긴 계열이 U3 규칙을 지키는가 (이중축 금지 · 계열 수 · 격자 실선)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const TICKER = process.env.TS_TICKER || '005930';

let failures = 0;
const ok = (label, extra = '') => console.log(`  \x1b[32m✓\x1b[0m  ${label}${extra ? ` — ${extra}` : ''}`);
const bad = (label, why) => { failures += 1; console.log(`  \x1b[31m✗\x1b[0m  ${label} — ${why}`); };
const check = (cond, label, why) => (cond ? ok(label) : bad(label, why));

/** 페이지가 부르는 API 를 미리 받아 둔다 (jsdom 안에서 fetch 를 대신 답하기 위해). */
async function fetchAll() {
  const q = `ticker=${encodeURIComponent(TICKER)}&years=2`;
  const paths = [
    `/api/ts/series?${q}`,
    `/api/ts/diagnostics?${q}`,
    `/api/ts/decompose?${q}&period=5&model=additive`,
    `/api/ts/forecast?${q}&horizon=20`,
    '/api/dashboard/ticker',
  ];
  const store = {};
  for (const p of paths) {
    const res = await fetch(BASE + p);
    store[p] = { status: res.status, text: await res.text() };
    if (!res.ok) bad(`API ${p}`, `HTTP ${res.status}`);
  }
  return store;
}

async function main() {
  console.log(`\n── 0. API 응답 확보 (${BASE} · ${TICKER}) ──────────`);
  const responses = await fetchAll();
  if (failures) { console.log('\nAPI 가 실패해 렌더 검사를 진행할 수 없습니다.'); process.exit(1); }
  ok('네 엔드포인트 + 티커바 응답 수신');

  const html = fs.readFileSync(path.join(ROOT, 'static/pages/timeseries.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: BASE });
  const { window } = dom;
  const doc = window.document;

  // ── 대역들 ──────────────────────────────
  const drawn = {};                        // 차트 id → 넘긴 옵션
  class FakeApex {
    constructor(el, options) { this.el = el; this.options = options; drawn[el.id] = options; }
    render() { this.el.setAttribute('data-rendered', '1'); }
    destroy() {}
  }
  window.ApexCharts = FakeApex;

  // jsdom 은 실제 폭을 재지 않아 0 이 나온다. 스파크라인·차트가 폭을 물으므로 값을 준다.
  Object.defineProperty(window.Element.prototype, 'clientWidth', { get: () => 800, configurable: true });
  Object.defineProperty(window.Element.prototype, 'clientHeight', { get: () => 300, configurable: true });

  window.fetch = async (url) => {
    const key = String(url).replace(BASE, '');
    const hit = responses[key];
    if (!hit) return { ok: false, status: 404, text: async () => JSON.stringify({ detail: `대역에 없는 경로: ${key}` }) };
    return { ok: hit.status >= 200 && hit.status < 300, status: hit.status, text: async () => hit.text };
  };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

  const errors = [];
  window.addEventListener('error', (e) => errors.push(e.message));

  // ── 공통 스크립트 + 페이지 스크립트 실행 ──
  for (const asset of ['static/assets/app.js', 'static/assets/shell.js']) {
    window.eval(fs.readFileSync(path.join(ROOT, asset), 'utf8'));
  }
  const pageScript = [...doc.querySelectorAll('script')]
    .filter((s) => !s.src).map((s) => s.textContent).join('\n');

  console.log('\n── 1. 페이지 스크립트 실행 ────────────────');
  try {
    window.eval(pageScript);
    ok('스크립트가 예외 없이 초기화됨');
  } catch (error) {
    bad('스크립트 초기화', error.message);
    console.log(error.stack.split('\n').slice(0, 4).join('\n'));
    process.exit(1);
  }

  const settle = () => new Promise((r) => setTimeout(r, 250));
  await settle();

  // ── 2. 진단 탭 ──────────────────────────
  console.log('\n── 2. 진단 탭 ────────────────────────────');
  const banner = doc.getElementById('qualityBanner').textContent;
  check(!banner.includes('불러오는 중') && !banner.includes('불러오지 못'),
        '품질 배너', `내용: ${banner.slice(0, 60)}`);
  check(banner.includes('전처리'), '배너에 전처리 판정 표시', banner.slice(0, 60));
  check(doc.getElementById('adfStats').children.length >= 4,
        'ADF 타일 4장', `${doc.getElementById('adfStats').children.length}장`);
  check(!doc.getElementById('orderBody').textContent.includes('불러오는 중'),
        '추천 차수 카드', '아직 로딩 상태');
  check(drawn.acfChart && drawn.pacfChart, 'ACF·PACF 차트 생성', '차트가 안 그려짐');
  check(doc.getElementById('acfTable').querySelector('table') != null, 'ACF 표 보기 생성', '표 없음');

  if (drawn.acfChart) {
    const s = drawn.acfChart.series;
    check(s.length === 2, 'ACF 계열 = 신뢰띠 + 값 2개', `${s.length}개`);
    check(s[0].type === 'rangeArea' && s[1].type === 'column',
          'ACF 형태 = rangeArea + column', s.map((x) => x.type).join('+'));
    const colors = new Set(s[1].data.map((d) => d.fillColor));
    check(colors.size >= 1 && colors.size <= 2,
          'ACF 막대 색은 유의/비유의 두 가지뿐 (강조 규칙)', `${colors.size}가지`);
    check(drawn.acfChart.grid.strokeDashArray === 0, '격자 실선 (점선 금지)', '점선 격자');
    check(drawn.acfChart.dataLabels.enabled === false, '점마다 숫자 안 찍음', '데이터라벨 켜짐');
  }

  // ── 3. 분해 탭 ──────────────────────────
  console.log('\n── 3. 분해 탭 ────────────────────────────');
  doc.querySelector('#viewTabs button[data-view="decompose"]').click();
  await settle();
  const panels = doc.querySelectorAll('#decomposeStack .panel');
  check(panels.length === 4, '분해 4단 small multiples', `${panels.length}칸`);
  check(doc.getElementById('decomposeStats').children.length >= 4, '분해 지표 타일', '타일 없음');
  const stackCharts = ['dc-observed', 'dc-trend', 'dc-seasonal', 'dc-resid'].filter((k) => drawn[k]);
  check(stackCharts.length === 4, '네 칸 모두 차트 생성', `${stackCharts.length}개`);
  stackCharts.forEach((k) => {
    if (drawn[k].series.length !== 1) bad(`${k} 계열 1개`, `${drawn[k].series.length}개`);
  });
  check(stackCharts.every((k) => drawn[k].series.length === 1),
        '칸마다 계열 하나 (이중축 회피의 결과)', '계열이 여럿');
  check(doc.getElementById('decomposeNote').textContent.length > 10, '계절 세기 판정 문구', '문구 없음');

  // ── 4. 예측 탭 ──────────────────────────
  console.log('\n── 4. 예측 탭 ────────────────────────────');
  doc.querySelector('#viewTabs button[data-view="forecast"]').click();
  await settle();
  check(doc.getElementById('modelStats').children.length >= 4, '모형 타일 4장',
        `${doc.getElementById('modelStats').children.length}장`);
  check(drawn.forecastChart != null, '예측 팬차트 생성', '차트 없음');
  if (drawn.forecastChart) {
    const s = drawn.forecastChart.series;
    check(s.length === 3, '예측 계열 = 신뢰구간 + 관측 + 예측', `${s.length}개`);
    check(s[0].type === 'rangeArea', '신뢰구간이 rangeArea', s[0].type);
    check(drawn.forecastChart.yaxis && !Array.isArray(drawn.forecastChart.yaxis),
          'Y축 하나 (이중축 금지)', 'Y축이 여러 개');
    const band = s[0].data.filter((d) => d.y != null);
    check(band.length > 1, '신뢰구간에 값이 있음', `${band.length}점`);
  }
  const scenarios = doc.querySelectorAll('#scenarioGrid .scenario');
  check(scenarios.length === 3, '시나리오 3단 카드', `${scenarios.length}장`);
  if (scenarios.length === 3) {
    const names = [...scenarios].map((el) => el.querySelector('.nm').textContent.split(' ')[0]);
    check(names.join(',') === 'Bull,Base,Bear', '순서 = Bull → Base → Bear', names.join(','));
    check([...scenarios].every((el) => el.querySelector('dd').textContent.length > 5),
          '카드마다 트리거·무효화 문구', '문구 비어 있음');
    check([...scenarios].every((el) => el.querySelector('.meter i').style.width),
          '확률 미터 렌더', '미터 비어 있음');
  }
  check(doc.getElementById('backtestStats').children.length >= 4, '백테스트 타일 4장',
        `${doc.getElementById('backtestStats').children.length}장`);
  const caveats = doc.querySelectorAll('#caveatList li');
  check(caveats.length >= 3, 'caveats 목록', `${caveats.length}줄`);
  if (caveats.length) {
    check(caveats[0].classList.contains('lead'), '맨 앞 caveat 강조 (백테스트 결론)', '강조 없음');
    console.log(`     맨 앞 줄: ${caveats[0].textContent.slice(0, 90)}`);
  }

  // ── 5. 실행 중 오류 ─────────────────────
  console.log('\n── 5. 실행 중 오류 ───────────────────────');
  check(errors.length === 0, '창 오류 없음', errors.slice(0, 3).join(' / '));

  console.log(failures ? `\n결과: 실패 ${failures}건 ✗\n` : '\n결과: 렌더 이상 없음 ✓\n');
  process.exit(failures ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(1); });
