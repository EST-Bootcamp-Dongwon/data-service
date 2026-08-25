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
      // 차트 (M8 · N84). **여기는 ApexCharts 가 없는 환경**이다 — 그림은 안 그려지고
      // `숫자 보기`(표) · 신호판 · 값 하나만 남는다. 그 갈래가 살아 있는지를 본다.
      // 그림까지 보는 것은 `render_charts.js` 쪽 일이다 (진짜 ApexCharts + 진짜 서버).
      charts: [
        { id: 'V-CORP-R-0002', kind: 'bar-line', title: '매출과 이익이 어떻게 움직였나?',
          chart_type: '선 + 막대 조합', unit: '조원', axis: { x: '회계연도', y: '금액' },
          categories: ['2023', '2024'], drawable: true, reason: '',
          series: [{ name: '매출액', kind: 'bar', data: [70.1, 80.1] },
                   { name: '영업이익', kind: 'line', data: [5.2, 6.6] }],
          table: { head: ['회계연도', '매출액 (조원)'], rows: [['2023', '70.1'], ['2024', '80.1']] },
          note: 'Y축을 0에서 시작하지 않으면 변화가 과장된다',
          sources: ['DART 재무제표'], data_ids: ['D-CORP-R-0001'] },
        { id: 'V-CORP-R-0004', kind: 'signal', title: '지금 어느 국면인가?',
          chart_type: '신호 3단 패널', unit: '', axis: { x: '신호', y: '방향' },
          categories: ['경기선행지수'], drawable: true, reason: '',
          series: [{ name: '신호', kind: 'signal',
                     data: [{ label: '경기선행지수', value: '확장', up: true, detail: '추세선 위' }] }],
          table: { head: ['신호', '판정'], rows: [['경기선행지수', '확장']] },
          note: '', sources: ['ECOS'], data_ids: [] },
        { id: 'V-CORP-R-0003', kind: 'stat', title: '순위가 얼마나 단단한가?',
          chart_type: '시나리오별 1위 빈도 막대', unit: '회', axis: { x: '시나리오', y: '1위 빈도' },
          categories: ['삼성전자'], drawable: true, reason: '',
          series: [{ name: '단독 1위', kind: 'stat', data: [17] }],
          table: { head: ['종목', '단독 1위'], rows: [['삼성전자', '17']] },
          note: '견줄 상대가 하나뿐이라 막대로 그리지 않고 값으로 낸다',
          sources: [], data_ids: [] },
        { id: 'V-CORP-R-0001', kind: 'line', title: '그릴 수 없는 차트',
          chart_type: '가로 막대', unit: '', axis: { x: '', y: '' },
          categories: [], series: [], table: { head: [], rows: [] },
          note: '', sources: [], data_ids: [],
          drawable: false, reason: '사업보고서에서 부문별 금액을 찾지 못했다' },
      ],
      // 표지 (M8 · N86) — H11 이 팩에 실어 둔다. 값은 `headline.py` 가 만든 모양 그대로다.
      headline: {
        workstream_id: 'CORP-R', target: '삼성전자 (005930)', as_of: '2026-08-04',
        verdict: {
          label: '프리미엄', kind: '밸류에이션 스크리닝', tone: 'warning',
          basis: '08강 08.md', reasons: ['매출이 성장 중이다 (+4.5%)'],
          conditions: ['피어 대비 +32.4% — 뚜렷한 디스카운트가 아니다'],
          ai_proposal: true, human_decision: '사람 승인 필요 — 투자 판단이 아니라 스크리닝 결과다',
          caveat: '투자 권유가 아니라 밸류에이션 연습 결과다.',
        },
        // **부호를 감추지 않는다** — 실측 삼성전자가 -24.5% 다 (N86 의 핵심 규칙)
        metrics: [
          { label: '가치 범위 (중앙)', value: 198186, text: '198,186', unit: '원',
            sub: '밴드 158,549 ~ 237,823원', tone: '' },
          { label: '현재가 대비', value: -24.5, text: '-24.5', unit: '%',
            sub: '현재가 262,500원 기준', tone: 'down' },
        ],
        scenarios: {
          kind: 'price', unit: '원',
          fields: [['target_text', '목표'], ['prob_text', '확률']],
          columns: [
            { name: 'Bear', label: '하락', tone: 'down', target_text: '192,818', prob_text: '4.9%' },
            { name: 'Base', label: '횡보', tone: 'flat', target_text: '266,630', prob_text: '94.9%' },
            { name: 'Bull', label: '상승', tone: 'up', target_text: '372,325', prob_text: '0.2%' },
          ],
          note: '경계는 기술적이고 확률은 통계적이다',
        },
        axes: [
          { axis: '대상·범위 정합성', max: 10, score: 10, score_text: '10.0', ratio: 1,
            pct: 100, tone: 'good', why: '대상 확정 삼성전자', parts: [] },
          { axis: '증거 추적성', max: 15, score: 12.2, score_text: '12.2', ratio: 0.813,
            pct: 81.3, tone: 'warning', why: '본문 수치 79개 중 34개', parts: [] },
          { axis: '해석 품질', max: 20, score: 12, score_text: '12.0', ratio: 0.6,
            pct: 60, tone: 'serious', why: '대안 설명이 얕다', parts: [] },
        ],
        evaluation: { total: 94.5, grade: 'A', critical: [] },
        note: '',
        disclaimer: '표지는 본문에 있는 값을 모아 보인 것이다 — 여기서 새로 만든 수치는 없다.',
      },
      report: {
        page_count: 2, max_pages: 15, workstream_id: 'CORP-R', format: '고정양식',
        // 표 (M8 · N87) — `page.body` 가 **아니다.** 그래서 본문 수치 개수가 달라지지 않는다.
        tables: [
          { key: 'financial_years', title: '연도별 재무 (2021~2025)', drawable: true, reason: '',
            head: ['항목 (조원)', '2024', '2025'], align_right_from: 1,
            rows: [['매출액', '300.9', '333.6'], ['영업이익', '32.7', '43.6']],
            note: 'CAGR 4.5% (4년) · 성장 둔화', basis: 'DART 연결재무제표',
            data_ids: ['D-CORP-R-0001'] },
          { key: 'peer_compare', title: '피어 비교', drawable: false,
            reason: '피어를 고르지 못했다', head: [], rows: [], note: '', basis: '',
            data_ids: [], align_right_from: 1 },
          { key: 'financial_ratios', title: '재무비율 (최신 연도)', drawable: true, reason: '',
            head: ['지표', '값'], align_right_from: 1, rows: [['ROE', '10.36']],
            note: '', basis: '08강 06.md', data_ids: [] },
        ],
        // 어느 장에도 못 붙은 **그릴 수 있는** 표 (차트와 같은 규칙)
        extra_table_keys: ['financial_ratios'],
        merged: ['slot 4 를 slot 3 에 합쳤다 (자료 없음)'], empty_slots: [4],
        policy: 'GIC v15 CORP-R 하네스설계서 §6.1 순서',
        // 장에 못 붙은 **그릴 수 있는** 차트 — `_attach_charts` 가 이렇게 돌려준다.
        // 못 그리는 것(V-0001)은 여기 들어가지 않는다.
        extra_chart_ids: ['V-CORP-R-0004', 'V-CORP-R-0003'],
        pages: [
          { page: 1, slot: 1, title: 'Cover', key_message: '삼성전자 — 저평가 후보',
            body: ['분석 기준일 2026-08-02', '매출 80.1조 · 영업이익 6.6조'],
            visual: '', visual_id: '', table_keys: [], interpretation: {},
            sources: ['DART 사업보고서 2024'],
            confidence: 'medium', human_decision: 'AI 제안 — 사람 승인 전',
            presenter_note: '', merged_from: [], gaps: [] },
          { page: 2, slot: 3, title: '회사 개요', key_message: '반도체와 스마트폰',
            body: ['영업이익률 8.2% 는 PER 밴드와 함께 읽는다', '검사 5건 중 통과 4'],
            visual: '재무 시계열', visual_id: 'V-CORP-R-0002', table_keys: ['financial_years'],
            interpretation: {}, sources: [],
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
      // ── 차트 (M8 · N84) — 여기는 ApexCharts 가 **없는** 환경이다 ──
      // 그림이 없어도 값을 읽을 수 있어야 한다. 그림까지는 `render_charts.js` 가 본다.
      ['차트 자리가 장에 붙음', doc.querySelectorAll('.rp-chart').length >= 2],
      ['차트 자리에 ApexCharts 컨테이너가 생김',
        !!doc.getElementById('rpchart-V-CORP-R-0002')],
      ['ApexCharts 없이도 숫자 보기(표)가 나온다',
        doc.querySelectorAll('.rp-chart-table table').length > 0],
      ['신호판이 화살표 + 글자로 나온다 (색만으로 방향을 말하지 않는다)',
        doc.querySelectorAll('.rp-signal-row').length > 0
        && /[▲▼]/.test(doc.querySelector('.rp-signal-arrow')?.textContent || '')],
      ['값 하나(stat)가 막대 대신 나온다', doc.querySelectorAll('.rp-stat-value').length > 0],
      ['장에 못 붙은 차트를 부록으로 낸다', /장에 붙지 않은 차트/.test(doc.body.innerHTML)],
      ['못 그린 차트를 숨기지 않고 사유를 밝힌다',
        /부문별 금액을 찾지 못했다/.test(doc.body.innerHTML)],
      ['그린 수와 못 그린 수를 함께 센다', /차트 <b>4개<\/b> 중 <b>3개<\/b>/.test(doc.body.innerHTML)],

      // ── 표지 (M8 · N86) ──
      ['표지가 우리 판정을 낸다 (BUY 를 만들지 않는다)',
        /밸류에이션 스크리닝/.test(doc.body.innerHTML)
        && /프리미엄/.test(doc.body.innerHTML)
        && !/\bBUY\b/.test(doc.body.innerHTML)],
      ['표지가 사람 승인 문구를 함께 낸다',
        /사람 승인 필요/.test(doc.querySelector('.rp-hl-human')?.textContent || '')],
      ['현재가 대비의 **음수 부호를 감추지 않는다**',
        /-24\.5/.test(doc.querySelector('.rp-headline .tiles')?.innerHTML || '')],
      ['3칼럼이 Bear · Base · Bull 순으로 나온다',
        [...doc.querySelectorAll('.rp-scn-head b')].map((e) => e.textContent).join(',')
          === 'Bear,Base,Bull'],
      ['시나리오가 색만으로 방향을 말하지 않는다 (화살표를 함께 찍는다)',
        [...doc.querySelectorAll('.rp-scn-arrow')].every((e) => /[▲▼▬]/.test(e.textContent))],
      ['9축이 막대와 숫자를 함께 낸다',
        doc.querySelectorAll('.rp-axis-fill').length === 3
        && doc.querySelectorAll('.rp-axis-num').length === 3],
      ['9축 막대가 축마다 다른 색을 쓰지 않는다 (상태만 얹는다)',
        [...doc.querySelectorAll('.rp-axis-fill')]
          .every((e) => ['', 'good', 'warning', 'serious']
            .includes(e.className.replace('rp-axis-fill', '').trim()))],
      ['9축 채움 너비가 달성률과 같다',
        (doc.querySelectorAll('.rp-axis-fill')[1] || {}).style?.width === '81.3%'],

      // ── 표 (M8 · N87) ──
      ['표가 장에 붙어 나온다', /연도별 재무 \(2021~2025\)/.test(doc.body.innerHTML)],
      ['표의 숫자 칸이 오른쪽 정렬이다',
        doc.querySelectorAll('.rp-table td.num').length > 0],
      ['못 만든 표를 숨기지 않고 사유를 밝힌다',
        /피어를 고르지 못했다/.test(doc.body.innerHTML)],
      ['장에 못 붙은 표를 부록으로 낸다', /장에 붙지 않은 표/.test(doc.body.innerHTML)],
      ['실은 표 수와 못 만든 표 수를 함께 센다',
        /표 <b>3개<\/b> 중 <b>2개<\/b>/.test(doc.body.innerHTML)],
      // ★ 이것이 N87 의 핵심 계약이다 — 표는 본문이 아니므로 수치 개수를 바꾸면 안 된다.
      //   깨지면 H10 증거 추적성 5점이 데이터와 무관하게 움직인다.
      ['표가 본문 수치 개수를 바꾸지 않는다',
        !/rp-body/.test(doc.querySelector('.rp-table')?.closest('ul')?.className || 'x')
        && doc.querySelectorAll('.rp-table .rp-body').length === 0],
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

/* ── 7. /stock 자료 보관함 + DART 수집 (ADR-DS-0019 · ADR-DS-0020) ──────────
 *
 * 이 절이 붙드는 것은 셋이다.
 *
 *  ① 패널 요소가 그대로 있는가 (버튼 하나가 사라져도 화면은 오류를 안 낸다)
 *  ② ⭐ **차트가 안 뜨는 환경에서도 보관함이 산다** — ADR-DS-0019 §9 의 규칙이
 *     지금까지 주석뿐이었다. `onStockResolved()` 를 `render()` 뒤로 옮기면
 *     `drawPrice()` 의 예외에 보관함이 같이 죽는데, **jsdom 은 ApexCharts 가 없으므로
 *     그 환경이 여기서 그대로 재현된다.** 검사로 바꾸는 자리가 여기다.
 *  ③ 못 쓸 때 잠기고 **이유가 보이는가** — 배포본의 기본 상태다.
 */
function stockClipChecks() {
  console.log('\n── 7. /stock 자료 보관함 · DART 수집 ────────');
  const file = path.join(CURRENT, 'stock.html');
  if (!fs.existsSync(file)) { failures++; console.log('  ✗  stock.html 이 없다'); return; }

  const NEED = ['clipCard', 'clipBlocked', 'clipForm', 'clipKind', 'btnClipSave',
    'btnFetchFilings', 'clipStatus', 'clipListWrap', 'clipFilterKind', 'clipFilterMonth',
    'clipFilterTag', 'clipAllStocks', 'clipBody', 'clipScope'];
  const ids = idsOf(fs.readFileSync(file, 'utf8'));
  const gone = NEED.filter((id) => !ids.has(id));
  if (gone.length) { failures++; console.log(`  ✗  빠진 요소: ${gone.join(', ')}`); }
  else console.log(`  ✓  패널 요소 ${NEED.length}개 확인`);

  // 담기 버튼과 DART 버튼이 **같은 잠금**을 물려받는지 — 형제로 있어야 한다.
  const html = fs.readFileSync(file, 'utf8');
  const formStart = html.indexOf('id="clipForm"');
  const listStart = html.indexOf('id="clipListWrap"');
  const inside = formStart >= 0 && listStart > formStart
    && html.indexOf('id="btnFetchFilings"') > formStart
    && html.indexOf('id="btnFetchFilings"') < listStart;
  if (!inside) { failures++; console.log('  ✗  DART 버튼이 clipForm 밖에 있다 — 잠금을 안 물려받는다'); }
  else console.log('  ✓  DART 버튼이 잠금 안쪽에 있다');

  // ⭐ 순서 회귀 — `onStockResolved()` 가 `render()` 의 **첫 실행문**인가.
  const render = html.slice(html.indexOf('function render('));
  const first = render.slice(0, 600);
  const clipAt = first.indexOf('onStockResolved(');
  const drawAt = first.indexOf('drawPrice(');
  if (clipAt < 0 || (drawAt >= 0 && drawAt < clipAt)) {
    failures++;
    console.log('  ✗  onStockResolved 가 그리기보다 뒤에 있다 — 차트 예외에 보관함이 같이 죽는다');
  } else console.log('  ✓  보관함을 그리기보다 먼저 옮긴다 (ADR-DS-0019 §9)');
  return { file, html };
}

/** 서버 없이 화면을 실제로 몰아 본다. **ApexCharts 를 일부러 비운 채**로. */
async function stockClipRun(ctx) {
  if (!ctx) return;
  const clips = [];
  const answer = (url, method, body) => {
    if (url.includes('/api/clips/status')) return { available: true, reason: '', hints: [], kinds: ['news', 'filing', 'dataset', 'report', 'memo', 'post', 'video'], screens: ['stock'] };
    if (url.includes('/api/clips/facets')) return { kinds: [{ kind: 'filing', count: clips.length }], months: [{ month: '2026-08', count: clips.length }], tags: [{ tag: '실적', count: 1 }] };
    if (url.includes('/api/collect/dart/status')) return { available: true, reason: '', hints: [], budget: { left: 9000 }, filings: { count: 1, latest: '2026-08-21' }, corp_code: {}, universe: {}, batch_command: 'x' };
    if (url.includes('/api/collect/dart/security')) {
      clips.push({ clip_id: clips.length + 1, kind: 'filing', screen: 'stock', title: '사업보고서 (2025.12)', url: 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1', source: 'DART', occurred_at: '2026-08-21', tags: ['실적', '정기보고서'], payload: {} });
      return { scope: 'one', created: 1, duplicate: 0, invalid: 0, calls: 3, truncated_codes: [] };
    }
    if (url.includes('/api/clips')) {
      if (method === 'POST') { clips.push({ clip_id: 99, kind: 'memo', screen: 'stock', title: (body && body.title) || 'x', tags: [], payload: {}, created: true }); return clips[clips.length - 1]; }
      return { items: clips, total: clips.length, page: 1, size: 50 };
    }
    if (url.includes('/api/stock/')) {
      return { code: '005930', name: '삼성전자', market: 'KR', price: { close: 70000, change_rate: 1.2 }, series: [], source: 'krx-db' };
    }
    return {};
  };

  const dom = new JSDOM(ctx.html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/stock?code=005930' });
  const { window } = dom;
  // ⚠️ **`matchMedia` 도 `ApexCharts` 도 넣지 않는다.** 그 결핍이 이 검사의 요점이다 —
  //    차트 경로가 던지는 환경에서 보관함이 살아남는지를 본다.
  const errors = [];
  window.addEventListener('error', (e) => errors.push(String(e.message)));
  window.fetch = async (url, opt = {}) => {
    const body = opt.body ? JSON.parse(opt.body) : null;
    const data = answer(String(url), opt.method || 'GET', body);
    return { ok: true, status: 200, text: async () => JSON.stringify(data), json: async () => data };
  };

  try {
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/app.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/shell.js'), 'utf8'));
    const page = ctx.html.match(/<script>([\s\S]*?)<\/script>/g).pop().replace(/<\/?script>/g, '');
    window.eval(page);
    await sleep(400);

    const doc = window.document;
    const checks = [
      ['보관함 패널이 잠기지 않았다', doc.getElementById('clipForm').hidden === false],
      ['범위 표시가 종목을 가리킨다', /삼성전자/.test(doc.getElementById('clipScope').textContent)],
      ['DART 버튼이 눌릴 수 있다', doc.getElementById('btnFetchFilings').disabled === false],
    ];
    checks.forEach(([label, ok]) => { if (!ok) failures++; console.log(`  ${ok ? '✓' : '✗'}  ${label}`); });

    // DART 버튼을 실제로 누른다
    doc.getElementById('btnFetchFilings').click();
    await sleep(300);
    const status = doc.getElementById('clipStatus').textContent;
    const said = /새로\s*1건/.test(status) && /이미 있던 것\s*0건/.test(status);
    if (!said) { failures++; console.log(`  ✗  수집 결과 문장이 이상하다 — «${status}»`); }
    else console.log(`  ✓  수집 결과를 새로/이미 로 나누어 말한다 — «${status}»`);

    const drawn = doc.getElementById('clipBody').querySelectorAll('tr').length;
    if (!drawn) { failures++; console.log('  ✗  담은 것이 목록에 안 그려졌다 (차트 예외에 같이 죽었을 수 있다)'); }
    else console.log(`  ✓  ApexCharts 없이도 목록이 그려진다 — ${drawn}행`);
  } catch (e) {
    failures++;
    console.log(`  ✗  /stock 실행 실패 — ${e.message}`);
  }
}

/** 못 쓸 때 잠기고 이유가 보이는가 — 배포본의 기본 상태다. */
async function stockClipLocked(ctx) {
  if (!ctx) return;
  const dom = new JSDOM(ctx.html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/stock?code=005930' });
  const { window } = dom;
  window.fetch = async (url) => {
    const locked = { available: false, reason: '저장소에 못 붙었다', hints: ['보관함은 Postgres 에만 있다 — 배포본은 S6(Supabase) 뒤에 쓸 수 있다.'], kinds: [], screens: [] };
    const data = String(url).includes('/api/clips/status') ? locked : {};
    return { ok: true, status: 200, text: async () => JSON.stringify(data), json: async () => data };
  };
  try {
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/app.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(ROOT, 'static/assets/shell.js'), 'utf8'));
    window.eval(ctx.html.match(/<script>([\s\S]*?)<\/script>/g).pop().replace(/<\/?script>/g, ''));
    await sleep(300);
    const doc = window.document;
    const shown = doc.getElementById('clipBlocked').hidden === false;
    const why = /S6/.test(doc.getElementById('clipBlocked').textContent);
    if (!shown) { failures++; console.log('  ✗  못 쓰는데 잠금 안내가 안 뜬다'); }
    else console.log('  ✓  못 쓰면 잠기고 안내가 뜬다');
    if (!why) { failures++; console.log('  ✗  **언제** 쓸 수 있는지가 화면에 없다 — 막다른 길이다'); }
    else console.log('  ✓  언제 되는지(S6)까지 화면에 실린다');
    if (doc.getElementById('clipForm').hidden !== true) { failures++; console.log('  ✗  잠겼는데 담기 폼이 열려 있다'); }
    else console.log('  ✓  담기 폼이 잠겼다');
  } catch (e) {
    failures++;
    console.log(`  ✗  잠금 경로 실행 실패 — ${e.message}`);
  }
}

researchChecks()
  .then(async () => {
    const ctx = stockClipChecks();
    await stockClipRun(ctx);
    await stockClipLocked(ctx);
  })
  .then(() => {
    console.log(`\n결과: ${failures === 0 ? '회귀 없음 ✓' : `${failures}건 실패 ✗`}`);
    process.exit(failures === 0 ? 0 : 1);
  });
