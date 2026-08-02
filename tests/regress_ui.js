/* M2 화면 회귀 확인 (M6 에서 리서치 화면 검사를 더했다)
 *
 * M1 이전 원본(`실습/pages/*.html`)과 지금 화면(`static/pages/*.html`)을 jsdom 으로 열어
 * **이식된 기능이 그대로 살아 있는지** 대조한다. 셸(shell.js)을 건드렸으므로
 * 모든 화면이 영향을 받는다.
 *
 * 보는 것
 *   1. 각 화면의 핵심 요소 id 가 그대로 있는가 (원본에 있던 것이 사라지지 않았는가)
 *   2. 페이지 스크립트가 문법 오류 없이 파싱되는가
 *   3. 셸이 사이드바·티커바를 그리는가 (새 메뉴 포함)
 *   4. **리서치 화면이 12상태를 끝까지 돌고 리포트를 그리는가** (M6 — 아래 4·5절)
 *
 * 4절은 서버 없이 돈다. `fetch` 를 가짜 응답으로 바꿔 끼우고 `Research.boot()` 를
 * 실제로 실행한다 — 실행 드라이버·진행률 모달·질문 카드·드릴다운이 한 번에 걸린다.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const vm = require('vm');
const { spawnSync } = require('child_process');   // 6절 — 파이썬 쪽 링크 규칙을 불러 대조한다

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

// ══════════════════════════════════════════════════════════════
// 4. 리서치 화면 (M6) — 서버 없이 12상태를 끝까지 돌린다
// ══════════════════════════════════════════════════════════════
//
// 실제 서버를 쓰지 않는 이유는 `run_harness.js` 가 이미 그 일을 하기 때문이다.
// 여기서 볼 것은 **화면 쪽 로직**이다 — 팩을 들고 다니는가, 진행률이 가중치 누적인가,
// 질문 카드가 뜨는가, 근거 링크가 붙는가.
const STATE_IDS = ['H00', 'H01', 'H02', 'H03', 'H04', 'H05', 'H06', 'H07', 'H08', 'H09', 'H10', 'H11'];
const WEIGHTS = { H00: 2, H01: 5, H02: 20, H03: 18, H04: 20, H05: 6, H06: 8, H07: 8, H08: 3, H09: 4, H10: 4, H11: 2 };

/** 가짜 Context Pack — 근거 링크가 걸리는지 보려고 D-/E-/CALC- 를 조금 담았다. */
function fakePack() {
  return {
    schema_version: 'GIC-HARNESS-1.0',
    C0_charter: { workstream_id: 'CORP-R', target: { kind: 'corp', code: '005930', name: '삼성전자' }, as_of: '2026-08-02' },
    C1_evidence: [{ id: 'E-CORP-R-0001', claim: '2024 연결 재무제표', source: 'DART-재무제표',
                    grade: '회사원문', grade_reason: '회사가 작성해 제출한 원문', directness: '직접',
                    published: '2025-03-11', collected: '2026-08-02', confidence: 'high' }],
    C2_data: [
      { id: 'D-CORP-R-0001', metric: 'revenue', value: 80102655000000, unit: '원', period: '2024',
        basis: 'CFS', actual_or_estimate: 'actual', evidence_id: 'E-CORP-R-0001' },
      { id: 'D-CORP-R-0002', metric: 'operating_income', value: 6566976000000, unit: '원', period: '2024',
        basis: 'CFS', actual_or_estimate: 'actual', evidence_id: 'E-CORP-R-0001' },
    ],
    C3_hypothesis: [], C4_visual: [], C5_interpretation: [],
    C6_decisions: { feedback_log: [], changes: [], approvals: [] },
    logs: {
      gaps: [], conflicts: [],
      calculations: [{ id: 'CALC-CORP-R-0001', formula: '영업이익 ÷ 매출액',
                       inputs: ['D-CORP-R-0002', 'D-CORP-R-0001'], result: 8.2, unit: '%',
                       assumption: '연결 기준', rechecked: true }],
    },
    CX_workstream: {
      h02_timing: { count: 2, failed: [], slowest: '재무제표', slowest_seconds: 6.4, sum_seconds: 12.8,
                    saved_seconds: 6.4, text: '2건을 동시에 불렀다 — 가장 느린 것은 재무제표 6.4초 (순차였다면 12.8초)' },
      evaluation: { total: 78.4, grade: 'B', critical: [], scores: [] },
      report: {
        page_count: 2, max_pages: 15, workstream_id: 'CORP-R', format: '고정양식',
        merged: ['slot 4 를 slot 3 에 합쳤다 (자료 없음)'], empty_slots: [4],
        policy: 'GIC v15 CORP-R 하네스설계서 §6.1 순서',
        pages: [
          { page: 1, slot: 1, title: 'Cover', key_message: '삼성전자 — 저평가 후보',
            body: ['분석 기준일 2026-08-02', '매출 80.1조 · 영업이익 6.6조'],
            visual: '', interpretation: {}, sources: ['DART 사업보고서 2024'],
            confidence: 'medium', human_decision: 'AI 제안 — 사람 승인 전',
            presenter_note: '', merged_from: [], gaps: [] },
          { page: 2, slot: 3, title: '회사 개요', key_message: '반도체와 스마트폰',
            body: ['영업이익률 8.2% 는 PER 밴드와 함께 읽는다', '검사 5건 중 통과 4'],
            visual: '재무 시계열', interpretation: {}, sources: [],
            confidence: 'high', human_decision: 'AI 제안 — 사람 승인 전',
            presenter_note: '', merged_from: [4], gaps: [] },
        ],
      },
    },
  };
}

function fakeStageResult(stateId) {
  return {
    run_id: 'RUN-CORP-R-20260802-001', workstream_id: 'CORP-R', stage_id: stateId,
    active_role: 'ORCH', status: stateId === 'H11' ? 'complete' : 'accepted',
    verified_result: [`${stateId} 를 끝냈다`], gap_ids: [], confidence: 'high',
  };
}

/** 리서치 화면이 부르는 주소를 전부 흉내 낸다. */
function fakeFetch(url) {
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  // H02 만 일부러 늦게 답한다 — 기다리는 동안 모달이 **경과 시간을 세는지** 보기 위해서다.
  // 나머지가 즉답이면 '진행 중' 줄이 화면에 머무는 순간이 없어 그 검사를 못 한다.
  const delay = /\/runs\/steps\/H02$/.test(path) ? 400 : 0;
  const json = (body) => new Promise((resolve) => setTimeout(() => resolve({
    ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)),
  }), delay));

  if (path.startsWith('/api/research/warmup')) return json({ warm: true, was_cold: true, loaded_now: ['glossary'], total_ms: 40, steps: [] });
  if (path.startsWith('/api/research/workstreams')) {
    return json({
      workstreams: [
        { id: 'CORP-R', name: '기업 리서치', target_kind: 'corp', needs: ['종목코드'], sufficiency_label: '🟢 높음' },
        { id: 'CORP-TP', name: '기업 Top Pick', target_kind: 'corp', needs: ['종목코드'], sufficiency_label: '🟢 높음' },
        { id: 'IND-R', name: '산업 리서치', target_kind: 'industry', needs: ['업종코드'], sufficiency_label: '🟡 중간' },
        { id: 'IND-TP', name: '산업 Top Pick', target_kind: 'industry', needs: ['업종코드'], sufficiency_label: '🟡 중간' },
      ],
      implemented: ['CORP-R', 'CORP-TP', 'IND-R', 'IND-TP'],
    });
  }
  if (path.startsWith('/api/research/plan/')) {
    let done = 0;
    return json({
      workstream_id: 'CORP-R', target_kind: 'corp', total_weight: 100,
      states: STATE_IDS.map((id) => {
        done += WEIGHTS[id];
        return { state_id: id, name: id, role: 'ORCH', does: '…',
                 asks: ['H01', 'H04', 'H08'].includes(id), weight: WEIGHTS[id],
                 weight_done: done, substages: [],
                 expected_seconds: id === 'H02' ? 9.1 : 0.63, expected_exact: id === 'H02' };
      }),
      asks_at: ['H01', 'H04', 'H08'],
      expected_total_seconds: 15.6,
      baseline: { measured_at: '2026-08-02', note: '환경마다 다르다', why_h02_slow: 'DART 응답이다' },
    });
  }
  if (path.includes('/api/research/glossary')) {
    if (path.includes('terms=true')) return json({ mode: 'terms', count: 2, terms: ['영업이익률', 'PER'], min_length: 2 });
    return json({ found: true, meaning: '가짜 뜻', english: '', section: '08강', match: '정확일치' });
  }
  if (path.startsWith('/api/search')) return json([{ code: '005930', name: '삼성전자', exchange: 'KOSPI', market: 'KR' }]);
  if (path.startsWith('/api/research/runs/steps/')) {
    const stateId = path.split('/').pop();
    return json({
      context_pack: fakePack(), stage_result: fakeStageResult(stateId),
      progress: { state_id: stateId, weight_done: STATE_IDS.slice(0, STATE_IDS.indexOf(stateId) + 1).reduce((a, s) => a + WEIGHTS[s], 0) },
      // 질문은 H01·H04·H08 이 끝난 뒤에 온다 (실제 서버와 같은 자리)
      human_questions: ['H01', 'H04', 'H08'].includes(stateId)
        ? [{ id: `Q-${stateId}-1`, text: '피어 그룹 기준은?', options: ['업종 기준', '시총 기준'], recommended: 0, why: 'H04 가 읽는다' }]
        : [],
      ledger: { evidence: 1, data: 2, calculations: 1, gaps: 0, conflicts: 0, data_linked_ratio: 1 },
    });
  }
  if (path.startsWith('/api/research/runs')) {
    return json({
      run_header: { run_id: 'RUN-CORP-R-20260802-001', workstream_id: 'CORP-R' },
      context_pack: fakePack(), stage_result: fakeStageResult('H00'),
      progress: { state_id: 'H00', weight_done: 2 }, human_questions: [],
    });
  }
  if (path.startsWith('/api/research/export/md')) return json({ markdown: '# 삼성전자\n', page_count: 2, bytes: 12, merged: [] });
  if (path.startsWith('/api/dashboard/ticker')) return json({ items: [] });
  return json({});
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function researchChecks() {
  console.log('\n── 4. 리서치 화면 (M6) ──────────────────────');
  const file = path.join(CURRENT, 'research.html');
  if (!fs.existsSync(file)) { failures++; console.log('  ✗  research.html 이 없다'); return; }

  // 4-1. 필요한 요소 id 가 전부 있는가
  const ids = idsOf(fs.readFileSync(file, 'utf8'));
  const NEED = ['wsPick', 'targetInput', 'pickDrop', 'runBtn', 'modalBack', 'progFill', 'progPct',
    'stateList', 'ledgerLine', 'modalQuestion', 'qText', 'qOpts', 'qSkip', 'qNext',
    'drill', 'drillBody', 'termTip', 'resultBox', 'tabBody', 'exportBtn', 'h02Note'];
  const gone = NEED.filter((id) => !ids.has(id));
  if (gone.length) { failures++; console.log(`  ✗  research.html — 빠진 요소: ${gone.join(', ')}`); }
  else console.log(`  ✓  research.html 요소 ${NEED.length}개 확인`);

  // 4-2. research.js 문법
  try {
    new vm.Script(fs.readFileSync(path.join(ROOT, 'static/assets/research.js'), 'utf8'), { filename: 'research.js' });
    console.log('  ✓  research.js 문법');
  } catch (e) { failures++; console.log(`  ✗  research.js 문법 — ${e.message}`); }

  // 4-3. 실제로 12상태를 돌려 본다 (fetch 를 가짜로 갈아 끼운 채)
  const dom = new JSDOM(fs.readFileSync(file, 'utf8'), { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/research?ws=CORP-R' });
  const { window } = dom;
  window.fetch = (url) => fakeFetch(url);
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));
  window.URL.createObjectURL = () => 'blob:fake';
  window.URL.revokeObjectURL = () => {};

  try {
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/app.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/shell.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/research.js'), 'utf8'));
    window.eval('Research.boot()');
    await sleep(60);

    const doc = window.document;
    const R = window.Research;

    // 대상을 고르고 실행한다 (사용자가 하는 것과 같은 순서)
    R.state.target = { code: '005930', name: '삼성전자' };
    const running = window.eval('Research.state');           // 같은 객체를 참조한다
    doc.getElementById('runBtn').disabled = false;
    doc.getElementById('runBtn').click();

    // 질문 카드가 뜨면 추천안을 고르고 넘긴다. 도는 동안 '진행 중' 줄과
    // 경과 시계가 실제로 움직이는지도 함께 본다.
    let sawQuestion = false;
    let sawRunningRow = false;
    let sawLiveClock = false;
    for (let i = 0; i < 120; i++) {
      await sleep(25);
      const nowRow = doc.querySelector('.state-row.now .st-time');
      if (nowRow) {
        sawRunningRow = true;
        // `0.3 / 9.1초` 처럼 흐른 시간이 0 을 넘겨 찍혔으면 시계가 도는 것이다
        if (/^[1-9]|^0\.[1-9]/.test(nowRow.textContent.trim())) sawLiveClock = true;
      }
      const card = doc.getElementById('modalQuestion');
      if (card && !card.hidden) {
        sawQuestion = true;
        const first = doc.querySelector('#qOpts .qopt');
        if (first) first.click();
        doc.getElementById('qNext').click();
      }
      if (!running.running && running.report) break;
    }

    const rows = [...doc.querySelectorAll('#stateList .state-row')];
    const percent = doc.getElementById('progPct').textContent;
    const checks = [
      ['12상태가 모달에 그려짐', rows.length === 12],
      ['12상태 전부 실행됨', running.rows.length === 12],
      ['진행률 100% 도달 (가중치 누적)', percent === '100%'],
      ['진행률이 12등분이 아님 (H02 후 27%)', running.rows[2] && running.rows[2].weight === 27],
      ['질문 카드가 떴다 (H01·H04·H08)', sawQuestion],
      ['기다리는 상태가 진행 중으로 표시됨', sawRunningRow],
      ['기다리는 동안 경과 시간이 흐름', sawLiveClock],
      ['답변이 C6 로 갈 feedback 으로 만들어짐', running.rows.length === 12],
      ['리포트가 그려짐', !!doc.querySelector('.rp')],
      ['근거 링크(data-evidence-id)가 붙음', doc.querySelectorAll('[data-evidence-id]').length > 0],
      ['합친 장을 화면이 설명함 (merged_from)', /slot 4/.test(doc.body.innerHTML)],
      ['연결률을 정직하게 밝힘', /근거로 이어진다/.test(doc.body.innerHTML)],
      ['h02_timing 을 모달이 보여 줌', /순차였다면/.test(doc.getElementById('h02Note').innerHTML)],
      ['용어 툴팁 표시가 붙음', doc.querySelectorAll('.term').length > 0],
    ];
    checks.forEach(([label, ok]) => {
      if (!ok) failures++;
      console.log(`  ${ok ? '✓' : '✗'}  ${label}`);
    });

    // 4-4. 근거 드릴다운 — D- 를 누르면 사슬이 열리는가
    const link = doc.querySelector('[data-evidence-id]');
    link.click();
    const drillHtml = doc.getElementById('drillBody').innerHTML;
    const drillChecks = [
      ['드릴다운 패널이 열림', !doc.getElementById('drill').hidden],
      ['사슬에 계산(CALC-)이 있음', /CALC-CORP-R-0001/.test(drillHtml)],
      ['사슬에 출처(E-)가 있음', /E-CORP-R-0001/.test(drillHtml)],
      ['출처 등급이 사유와 함께 나옴', /회사원문/.test(drillHtml) && /제출한 원문/.test(drillHtml)],
    ];
    drillChecks.forEach(([label, ok]) => {
      if (!ok) failures++;
      console.log(`  ${ok ? '✓' : '✗'}  ${label}`);
    });

    // 4-5. 상태별 예상 시간 — 서버 기준선에서 시작해 브라우저 측정으로 넘어가는가
    //
    // 위에서 12상태를 이미 돌렸으므로 H00~H11 에는 잰 값이 쌓여 있다.
    // '아직 재 본 적 없는 상태' 를 보려면 기록에 없는 ID 를 써야 한다.
    const fresh = { state_id: 'H99-처음', expected_seconds: 9.1, expected_exact: true };
    const seeded = R.estimate('CORP-R', fresh);
    R.recordTiming('CORP-R', fresh.state_id, 4200);
    const learned = R.estimate('CORP-R', fresh);
    const timingChecks = [
      ['첫 실행은 서버 기준선을 쓴다', /배포본 실측/.test(seeded.source) || seeded.own === false],
      ['잰 값이 쌓이면 그쪽을 쓴다', learned.own === true && Math.abs(learned.seconds - 4.2) < 0.5],
    ];
    timingChecks.forEach(([label, ok]) => {
      if (!ok) failures++;
      console.log(`  ${ok ? '✓' : '✗'}  ${label}`);
    });
  } catch (e) {
    failures++;
    console.log(`  ✗  리서치 화면 실행 실패 — ${e.message}\n${(e.stack || '').split('\n').slice(1, 4).join('\n')}`);
  }

  // ── 5. 근거 링크는 **확실할 때만** 걸어야 한다 ──
  //
  // 실측 — 본문 수치의 5~11% 만 장부와 이어진다. 나머지는 파생값이라 D- 가 없다.
  // 아무 D- 나 갖다 붙이면 없는 근거 사슬을 만드는 것이므로, 여기서 그 경계를 못박는다.
  console.log('\n── 5. 근거 링크 보수성 (M6) ─────────────────');
  try {
    const dom2 = new JSDOM('<!doctype html><body>', { runScripts: 'outside-only' });
    dom2.window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
    dom2.window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/app.js'), 'utf8'));
    dom2.window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/shell.js'), 'utf8'));
    dom2.window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/research.js'), 'utf8'));
    const R2 = dom2.window.Research;

    const pack = {
      C2_data: [
        { id: 'D-1', metric: 'revenue', value: 80102655000000, unit: '원' },
        { id: 'D-2', metric: 'operating_income', value: 6566976000000, unit: '원' },
        { id: 'D-3', metric: '005930.roe', value: 8.2, unit: '%' },
        { id: 'D-4', metric: '000660.roe', value: 8.2, unit: '%' },
      ],
    };
    const index = R2.buildValueIndex(pack);
    // [설명, 본문 한 줄, 기대 링크 수, 기대 '수치 토큰' 수]
    // 토큰 수까지 보는 이유 — 종목코드·연도를 수치로 세면 "몇 개가 근거로 이어지나" 가
    // 실제보다 나빠 보이고, 화면이 내는 정직한 숫자가 오히려 사람을 오해시킨다.
    const cases = [
      ['지표 이름이 같은 줄에 있으면 링크', '매출 80.1조를 올렸다', 1, 1],
      ['연도는 링크하지 않는다', '분석 기준일 2026-08-02', 0, 0],
      ['개수는 링크하지 않는다 (검사 5건)', '검사 5건 중 통과 4', 0, 0],
      ['같은 값을 가진 D- 가 둘이면 링크하지 않는다', 'ROE 8.2% 로 나타났다', 0, 1],
      ['종목코드는 수치로 세지 않는다', 'SK하이닉스 (000660) 시총 3.2조', 0, 1],
    ];
    cases.forEach(([label, line, wantLinked, wantTotal]) => {
      const got = R2.linkNumbers(line, index);
      const ok = got.linked === wantLinked && got.total === wantTotal;
      if (!ok) failures++;
      console.log(`  ${ok ? '✓' : '✗'}  ${label} — 링크 ${got.linked}/${got.total}개 (기대 ${wantLinked}/${wantTotal})`);
    });
  } catch (e) {
    failures++;
    console.log(`  ✗  링크 규칙 검사 실패 — ${e.message}`);
  }

  // ── 6. 같은 링크 규칙이 브라우저와 서버에서 **같은 답**을 내는가 (M7) ──
  //
  // M7 에서 H10 이 본문 연결률을 점수로 재게 되면서 같은 규칙이 파이썬에도 생겼다
  // (`app/services/research/linkcheck.py`). 두 벌이 어긋나면 화면이 보여 주는 연결률과
  // 점수가 달라진다 — 그러면 둘 중 하나는 거짓말이 된다. 여기가 그 경계다.
  //
  // 서버를 띄우지 않는다. 파이썬 모듈을 **파일 경로로 직접** 돌려 답만 받아 대조한다.
  console.log('\n── 6. 링크 규칙 이중 구현 대조 (M7) ─────────');
  try {
    const dom3 = new JSDOM('<!doctype html><body>', { runScripts: 'outside-only' });
    dom3.window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
    dom3.window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/app.js'), 'utf8'));
    dom3.window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/shell.js'), 'utf8'));
    dom3.window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/research.js'), 'utf8'));
    const R3 = dom3.window.Research;

    // 규칙의 **모든 갈래**를 건드리는 팩 — 조 표기(1e12 이상) · 소수 1·2자리 ·
    // 자릿점 정수 · 같은 값 중복 · 파생 D-(evidence_id 없음)
    const pack = {
      C2_data: [
        { id: 'D-1', metric: 'revenue', value: 80102655000000, unit: '원', evidence_id: 'E-1' },
        { id: 'D-2', metric: 'operating_income', value: 6566976000000, unit: '원', evidence_id: 'E-1' },
        { id: 'D-3', metric: '005930.roe', value: 8.2, unit: '%', evidence_id: 'E-2' },
        { id: 'D-4', metric: '000660.roe', value: 8.2, unit: '%', evidence_id: 'E-2' },
        { id: 'D-5', metric: '005930.per', value: 12.45, unit: '배', evidence_id: 'E-2' },
        { id: 'D-6', metric: 'valuation_band_base', value: 198186, unit: '원', calc_id: 'CALC-1' },
        { id: 'D-7', metric: 'revenue_cagr', value: 7.35, unit: '%', calc_id: 'CALC-2' },
        { id: 'D-8', metric: '생산지수', value: 115.5, unit: '지수', evidence_id: 'E-3' },
      ],
    };
    // 자릿점·반올림·단위가 서로 다르게 걸리는 줄들. 파이썬 `toFixed`·`Math.round` 흉내가
    // 어긋나면 여기서 바로 드러난다.
    const lines = [
      '매출 80.1조를 올렸다',
      '영업이익 6.6조 · 영업이익률 8.2%',
      'ROE 8.2% 로 나타났다',
      'PER 12.45배 · PER 12.5배',
      '주당 가치 범위 158,549~237,823원 (기준 198,186원)',
      '매출 CAGR 7.35% · 확장 국면',
      '생산지수 115.5 지수',
      '분석 기준일 2026-08-02 · 접수 2026-07',
      'SK하이닉스 (000660) 시총 3.2조',
      '검사 5건 중 통과 4 · 실패 0 · 판정불가 1',
      '후보 10곳 / 업종 상장사 72곳',
      '1위 SK하이닉스 · adjusted 4.6 · coverage 100%',
      '2025: 매출 80.1조 · 영업이익 6.6조',
      '피어 대비 +32.4%',
      '상장 시가총액 합계 1,234.5조',
    ];

    const jsRows = lines.map((line) => {
      const got = R3.linkNumbers(line, R3.buildValueIndex(pack));
      const ids = [...String(got.html).matchAll(/data-evidence-id="([^"]+)"/g)].map((m) => m[1]);
      return { total: got.total, linked: got.linked, data_ids: ids };
    });

    const script = path.join(ROOT, 'app/services/research/linkcheck.py');
    const candidates = [path.join(ROOT, '.venv/bin/python'), 'python3', 'python'];
    let out = null;
    let usedPython = '';
    for (const bin of candidates) {
      const run = spawnSync(bin, [script], {
        input: JSON.stringify({ C2_data: pack.C2_data, lines }),
        encoding: 'utf8',
      });
      if (run.status === 0 && run.stdout) { out = run.stdout; usedPython = bin; break; }
    }

    if (!out) {
      // **조용히 통과시키지 않는다.** 못 돌렸으면 못 돌렸다고 실패로 센다 —
      // 대조를 건너뛴 채 초록불이 뜨면 어긋남을 놓친 것과 같다.
      failures++;
      console.log('  ✗  파이썬 쪽을 돌리지 못했다 — 대조를 건너뛰었다 (초록불로 세지 않는다)');
    } else {
      const pyRows = JSON.parse(out);
      let mismatch = 0;
      lines.forEach((line, i) => {
        const a = jsRows[i];
        const b = pyRows[i];
        const same = a.total === b.total && a.linked === b.linked
          && JSON.stringify(a.data_ids) === JSON.stringify(b.data_ids);
        if (!same) {
          mismatch++;
          failures++;
          console.log(`  ✗  «${line}»`);
          console.log(`       JS ${a.linked}/${a.total} [${a.data_ids}] · PY ${b.linked}/${b.total} [${b.data_ids}]`);
        }
      });
      if (!mismatch) {
        const totals = jsRows.reduce((s, r) => ({ t: s.t + r.total, l: s.l + r.linked }), { t: 0, l: 0 });
        console.log(`  ✓  ${lines.length}줄 전부 같은 답 — 수치 ${totals.t}개 중 ${totals.l}개 링크 (${usedPython.split('/').pop()})`);
      }
    }
  } catch (e) {
    failures++;
    console.log(`  ✗  이중 구현 대조 실패 — ${e.message}`);
  }
}

researchChecks().then(() => {
  console.log(`\n결과: ${failures === 0 ? '회귀 없음 ✓' : `${failures}건 실패 ✗`}`);
  process.exit(failures === 0 ? 0 : 1);
});
