/* M2 화면 회귀 확인
 *
 * M1 이전 원본(`실습/pages/*.html`)과 지금 화면(`static/pages/*.html`)을 jsdom 으로 열어
 * **이식된 기능이 그대로 살아 있는지** 대조한다. 셸(shell.js)을 건드렸으므로
 * 모든 화면이 영향을 받는다.
 *
 * 보는 것
 *   1. 각 화면의 핵심 요소 id 가 그대로 있는가 (원본에 있던 것이 사라지지 않았는가)
 *   2. 페이지 스크립트가 문법 오류 없이 파싱되는가
 *   3. 셸이 사이드바·티커바를 그리는가 (새 메뉴 포함)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const ARCHIVE = path.join(ROOT, '실습/pages');
const CURRENT = path.join(ROOT, 'static/pages');

/* M1 에서 **의도적으로 없앤** 요소. 회귀가 아니므로 누락 검사에서 뺀다.
 *
 *   nav — 예전 상단 가로 메뉴(`<nav id="nav">` + `App.renderNav`).
 *         M1 에서 사이드바 셸(`Shell.render`)이 그 역할을 대신하면서 사라졌다.
 *         (`실습/assets/app.js:160` 이 이 요소를 채우던 코드다)
 *
 * 여기 적지 않은 누락은 전부 진짜 회귀로 본다.
 */
const INTENTIONALLY_REMOVED = new Set(['nav']);

// 아카이브 ↔ 현재 화면 짝. `/users`·`/tetris` 는 U6 결정으로 앱에서 내렸으므로 제외한다.
const PAIRS = [
  ['krx.html', 'krx.html'],
  ['kosis.html', 'kosis.html'],
  ['stock.html', 'stock.html'],
  ['yf.html', 'yf.html'],
  ['quant.html', 'quant.html'],
];

/** HTML 에서 id 속성을 전부 뽑는다. */
function idsOf(html) {
  const dom = new JSDOM(html);
  return new Set([...dom.window.document.querySelectorAll('[id]')].map((el) => el.id));
}

/** 인라인 <script> 를 모아 문법만 검사한다 (실행하지 않는다). */
function checkSyntax(html, label) {
  const dom = new JSDOM(html);
  const scripts = [...dom.window.document.querySelectorAll('script:not([src])')];
  const problems = [];
  scripts.forEach((s, i) => {
    try {
      new vm.Script(s.textContent, { filename: `${label}#script${i}` });
    } catch (e) {
      problems.push(`script${i}: ${e.message}`);
    }
  });
  return problems;
}

let failures = 0;
console.log('── 1. 원본 대비 요소 누락 검사 ──────────────');
for (const [oldName, newName] of PAIRS) {
  const oldPath = path.join(ARCHIVE, oldName);
  const newPath = path.join(CURRENT, newName);
  if (!fs.existsSync(oldPath) || !fs.existsSync(newPath)) {
    console.log(`  ?  ${newName} — 파일 없음`);
    continue;
  }
  const oldIds = idsOf(fs.readFileSync(oldPath, 'utf8'));
  const newIds = idsOf(fs.readFileSync(newPath, 'utf8'));
  const gone = [...oldIds].filter((id) => !newIds.has(id));
  const missing = gone.filter((id) => !INTENTIONALLY_REMOVED.has(id));
  const expected = gone.filter((id) => INTENTIONALLY_REMOVED.has(id));

  if (missing.length) {
    failures++;
    console.log(`  ✗  ${newName} — 원본에 있던 ${missing.length}개가 없음: ${missing.join(', ')}`);
  } else {
    const note = expected.length ? ` (M1 에서 의도적으로 뺀 것: ${expected.join(', ')})` : '';
    console.log(`  ✓  ${newName} — 원본 요소 ${oldIds.size}개 유지, 현재 ${newIds.size}개${note}`);
  }
}

console.log('\n── 2. 스크립트 문법 검사 ────────────────────');
for (const file of fs.readdirSync(CURRENT)) {
  if (!file.endsWith('.html')) continue;
  const problems = checkSyntax(fs.readFileSync(path.join(CURRENT, file), 'utf8'), file);
  if (problems.length) {
    failures++;
    console.log(`  ✗  ${file} — ${problems.join(' / ')}`);
  } else {
    console.log(`  ✓  ${file}`);
  }
}

console.log('\n── 3. 셸(사이드바·티커바) 렌더 검사 ──────────');
{
  const html = fs.readFileSync(path.join(CURRENT, 'dashboard.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = () => new Promise(() => {});          // 티커바 호출은 멈춰 둔다
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));

  try {
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/app.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/shell.js'), 'utf8'));
    window.eval("Shell.render('dashboard')");

    const doc = window.document;
    const links = [...doc.querySelectorAll('.side-nav a, aside a')].map((a) => a.getAttribute('href'));
    const brand = doc.querySelector('.side-brand');
    const checks = [
      ['사이드바 그려짐', links.length > 0],
      ['브랜드 = G.I.C Lab', !!brand && /G\.I\.C Lab/.test(brand.textContent)],
      ['QuantLab 잔존 없음', !/QuantLab/.test(doc.body.innerHTML)],
      ['/market 메뉴 있음', links.includes('/market')],
      ['기존 메뉴 유지 (/krx /kosis /stock /yf /quant)',
        ['/krx', '/kosis', '/stock', '/yf', '/quant'].every((h) => links.includes(h))],
      ['실습 아카이브 링크 유지', links.includes('/practice/')],
    ];
    checks.forEach(([label, ok]) => {
      if (!ok) failures++;
      console.log(`  ${ok ? '✓' : '✗'}  ${label}`);
    });
    console.log(`     메뉴: ${links.join(' ')}`);
  } catch (e) {
    failures++;
    console.log(`  ✗  셸 렌더 실패 — ${e.message}`);
  }
}

console.log(`\n결과: ${failures === 0 ? '회귀 없음 ✓' : `${failures}건 실패 ✗`}`);
process.exit(failures === 0 ? 0 : 1);
