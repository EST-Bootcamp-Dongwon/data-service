/* 리서치 하네스 전구간 실행 검사 (M4 신설 · M5 에서 4작업으로 확장)
 *
 * H00 → H11 을 **브라우저가 하듯이** 순서대로 부른다. 서버가 상태를 갖지 않으므로
 * Context Pack 을 받아서 다음 요청에 그대로 실어 보낸다 (명세 §1.3).
 *
 * 보는 것
 *   1. 열두 상태가 전부 200 으로 답하는가 (자료가 없어도 오류로 죽지 않는가 — 명세 §6.4)
 *   2. stage_result 봉투가 GIC 공통계약 §7 필드를 그대로 갖고 있는가
 *   3. 진행률이 가중치 누적값인가 (12등분이 아닌가 — 명세 §5.1)
 *   4. **전송량**이 얼마인가 (C1 근거 정책을 정하려면 실측이 필요하다)
 *   5. 네 워크스트림이 **같은 12상태**를 돌면서 H03·H04 만 갈라지는가 (명세 §5.4)
 *
 *   node tests/run_harness.js                    # CORP-R · 005930
 *   node tests/run_harness.js 035720             # 다른 종목
 *   node tests/run_harness.js all                # 네 작업 전부
 *   node tests/run_harness.js IND-R 261          # 산업 리서치
 *   node tests/run_harness.js all http://…       # 배포본에서 네 작업
 */
const BASE = process.argv.find((a) => a.startsWith('http')) || 'http://127.0.0.1:8000';
const ARGS = process.argv.slice(2).filter((a) => !a.startsWith('http'));

// 작업별 기본 대상 — 기업은 종목코드, 산업은 업종코드다
const DEFAULT_TARGET = {
  'CORP-R': '005930', 'CORP-TP': '005930', 'IND-R': '261', 'IND-TP': '261',
};
const ALL = ['CORP-R', 'CORP-TP', 'IND-R', 'IND-TP'];

const asked = ARGS.filter((a) => ALL.includes(a.toUpperCase()) || a.toLowerCase() === 'all');
const RUNS = asked.length === 0 ? ['CORP-R']
  : asked.some((a) => a.toLowerCase() === 'all') ? ALL
    : asked.map((a) => a.toUpperCase());
const OVERRIDE = ARGS.find((a) => !ALL.includes(a.toUpperCase()) && a.toLowerCase() !== 'all');

// GIC 공통계약 §7 의 봉투 필드 — 하나라도 빠지면 계약 위반이다
const ENVELOPE = ['run_id', 'workstream_id', 'stage_id', 'active_role', 'status',
  'input_versions', 'context_io', 'verified_result', 'provisional_interpretation',
  'unavailable_or_unverifiable', 'evidence_ids', 'data_ids', 'calculation_records',
  'gap_ids', 'conflict_ids', 'confidence', 'evaluation', 'visualization_and_interpretation',
  'human_questions', 'feedback_log', 'next_state_input'];

let failures = 0;
const ok = (m, extra = '') => console.log(`  \x1b[32m✓\x1b[0m  ${m}${extra ? ` — ${extra}` : ''}`);
const bad = (m, why) => { failures += 1; console.log(`  \x1b[31m✗\x1b[0m  ${m} — ${why}`); };

const bytes = (obj) => Buffer.byteLength(JSON.stringify(obj), 'utf8');
const kb = (n) => `${(n / 1024).toFixed(1)}KB`;

async function post(path, body) {
  const started = Date.now();
  const res = await fetch(BASE + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return { status: res.status, body: parsed, ms: Date.now() - started, sent: Buffer.byteLength(JSON.stringify(body), 'utf8') };
}

/** 답을 자동으로 채운다 — 첫 번째(추천) 선택지를 고른다. */
function answersFor(questions, stageId) {
  return (questions || []).map((q) => ({
    feedback_id: `F-${q.id}`, stage_id: stageId, question_id: q.id,
    question: q.text, answer: q.options?.[q.recommended ?? 0] || '',
  }));
}

/** 워크스트림 하나를 H00 → H11 로 돌린다. */
async function runWorkstream(workstream, target) {
  console.log(`\n\x1b[1m══ ${workstream} · ${target} ══\x1b[0m`);

  const planned = await fetch(`${BASE}/api/research/plan/${workstream}`).then((r) => r.json());
  const weights = planned.states.map((s) => s.weight);
  const total = weights.reduce((a, b) => a + b, 0);
  if (total === 100) ok('가중치 합 100', weights.join('+'));
  else bad(`${workstream} 가중치 합`, `${total}`);
  if (planned.states.length !== 12) bad(`${workstream} 상태 수`, `${planned.states.length}개 (12여야 한다)`);
  const forked = planned.states.filter((s) => (s.substages || []).length);
  console.log(`  갈라지는 상태: ${forked.map((s) => `${s.state_id}(${s.substages.length})`).join(' · ') || '없음'}`);

  const run = await post('/api/research/runs', { workstream_id: workstream, code: target });
  if (run.status !== 200) {
    bad(`${workstream} H00`, `HTTP ${run.status} ${JSON.stringify(run.body).slice(0, 200)}`);
    return null;
  }
  ok('run 생성', `${run.body.run_header.run_id} · ${run.ms}ms`);

  let pack = run.body.context_pack;
  const header = run.body.run_header;
  let questions = run.body.human_questions;

  const rows = [];
  const STATES = ['H01', 'H02', 'H03', 'H04', 'H05', 'H06', 'H07', 'H08', 'H09', 'H10', 'H11'];

  for (const state of STATES) {
    const payload = { run_header: header, context_pack: pack, feedback: answersFor(questions, state) };
    const res = await post(`/api/research/runs/steps/${state}`, payload);

    if (res.status !== 200) {
      bad(`${workstream} ${state}`, `HTTP ${res.status} — ${JSON.stringify(res.body).slice(0, 300)}`);
      break;
    }
    const result = res.body.stage_result;
    const missing = ENVELOPE.filter((f) => !(f in result));
    if (missing.length) bad(`${workstream} ${state} 봉투`, `빠진 필드: ${missing.join(', ')}`);
    const extra = Object.keys(result).filter((f) => !ENVELOPE.includes(f));
    if (extra.length) bad(`${workstream} ${state} 봉투`, `더해진 필드: ${extra.join(', ')}`);

    pack = res.body.context_pack;
    questions = res.body.human_questions;
    const led = res.body.ledger || {};
    rows.push({
      state, status: result.status, ms: res.ms,
      sent: res.sent, got: bytes(res.body), pack: bytes(pack),
      weight: res.body.progress.weight_done,
      e: led.evidence || 0, d: led.data || 0, c: led.calculations || 0,
      gaps: led.gaps || 0,
      note: (result.verified_result || [])[0] || '',
    });
  }

  console.log(`\n  ${'상태'.padEnd(5)} ${'status'.padEnd(17)} ${'ms'.padStart(6)} ${'보냄'.padStart(8)} ${'받음'.padStart(8)} ${'팩'.padStart(8)} ${'진행'.padStart(5)}  E/D/C/Gap`);
  for (const r of rows) {
    console.log(`  ${r.state.padEnd(5)} ${r.status.padEnd(17)} ${String(r.ms).padStart(6)} ` +
      `${kb(r.sent).padStart(8)} ${kb(r.got).padStart(8)} ${kb(r.pack).padStart(8)} ` +
      `${String(r.weight).padStart(4)}%  ${r.e}/${r.d}/${r.c}/${r.gaps}`);
  }

  if (rows.length === STATES.length) ok('열두 상태 전부 200'); else bad(`${workstream} 상태 실행`, `${rows.length + 1}/12 만 돌았다`);
  const last = rows[rows.length - 1];
  if (last && last.weight === 100) ok('진행률 100% 도달'); else bad(`${workstream} 진행률`, `${last?.weight}%`);
  if (last && last.status === 'complete') ok('H11 complete'); else bad(`${workstream} H11`, `status=${last?.status}`);

  const totalMs = rows.reduce((a, r) => a + r.ms, 0);
  const maxPack = Math.max(...rows.map((r) => r.pack));
  console.log(`  전구간 ${(totalMs / 1000).toFixed(1)}초 · 최대 Context Pack ${kb(maxPack)} · ` +
    `왕복 합계 ${kb(rows.reduce((a, r) => a + r.sent + r.got, 0))}`);
  console.log(`  근거 ${last?.e} · 데이터 ${last?.d} · 계산 ${last?.c} · Gap ${last?.gaps}`);

  const md = await post('/api/research/export/md', { context_pack: pack });
  let pages = 0;
  if (md.status === 200) {
    pages = md.body.page_count;
    ok('MD 생성', `${pages}장 · ${kb(md.body.bytes)}`);
    if (pages > 15) bad(`${workstream} 장수`, `${pages}장 — 15장 상한 초과`);
    if (pages === 0) bad(`${workstream} 장수`, '0장 — 조립하지 못했다');
    if (md.body.merged?.length) console.log(`     합침: ${md.body.merged.join(' / ')}`);
  } else bad(`${workstream} MD 생성`, `HTTP ${md.status} ${JSON.stringify(md.body).slice(0, 200)}`);

  // ── 본문 수치 연결률: 서버가 센 값과 브라우저가 센 값이 같은가 (M7) ──
  //
  // H10 이 점수로 쓰는 숫자(`linkcheck.check_report`)와 화면이 사람에게 보여 주는
  // 숫자(`research.js linkNumbers`)는 **같은 리포트에서 같아야** 한다. 다르면 둘 중
  // 하나는 거짓말이다. `regress_ui.js` 6절이 규칙을 줄 단위로 대조한다면, 여기서는
  // **실제 리포트 전문**으로 대조한다.
  const evaluation = (pack.CX_workstream || {}).evaluation || {};
  const server = evaluation.body_link;
  if (!server) {
    bad(`${workstream} 연결률`, 'H10 이 body_link 를 남기지 않았다');
  } else {
    const browser = browserLinkCounts(pack);
    if (!browser) {
      console.log(`  ·  연결률 대조 건너뜀 — jsdom 이 없다 (npm install --no-save jsdom)`);
    } else if (browser.total === server.total && browser.linked === server.linked) {
      ok('본문 연결률 서버=브라우저',
        `${server.linked}/${server.total} (${Math.round((server.ratio || 0) * 100)}%)`);
    } else {
      bad(`${workstream} 연결률 대조`,
        `서버 ${server.linked}/${server.total} · 브라우저 ${browser.linked}/${browser.total}`);
    }
  }

  return {
    workstream, target, pages,
    seconds: totalMs / 1000, maxPack, states: rows.length,
    evidence: last?.e ?? 0, data: last?.d ?? 0, calc: last?.c ?? 0, gaps: last?.gaps ?? 0,
    verdict: last?.status,
    bodyLink: (pack.CX_workstream || {}).evaluation?.body_link || null,
    score: (pack.CX_workstream || {}).evaluation?.total ?? null,
    grade: (pack.CX_workstream || {}).evaluation?.grade || '-',
    markdown: md.status === 200 ? md.body.markdown : '',
  };
}

/** 브라우저 규칙(`research.js linkNumbers`)으로 같은 리포트의 수치를 센다. */
let researchModule;
function browserLinkCounts(pack) {
  const report = (pack.CX_workstream || {}).report;
  if (!report || !report.pages) return null;
  if (researchModule === undefined) {
    try {
      const { JSDOM } = require('jsdom');
      const fs = require('fs');
      const path = require('path');
      const root = path.resolve(__dirname, '..');
      const dom = new JSDOM('<!doctype html><body>', { runScripts: 'outside-only' });
      dom.window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
      for (const asset of ['app.js', 'shell.js', 'research.js']) {
        dom.window.eval(fs.readFileSync(path.join(root, 'static/assets', asset), 'utf8'));
      }
      researchModule = dom.window.Research;
    } catch { researchModule = null; }
  }
  if (!researchModule) return null;
  const index = researchModule.buildValueIndex(pack);
  let total = 0;
  let linked = 0;
  for (const page of report.pages) {
    for (const line of [page.key_message, ...(page.body || [])]) {
      const got = researchModule.linkNumbers(line, index);
      total += got.total;
      linked += got.linked;
    }
  }
  return { total, linked };
}

async function main() {
  console.log(`\n리서치 하네스 전구간 · ${BASE} · 작업 ${RUNS.join(', ')}\n`);

  const meta = await fetch(`${BASE}/api/research/workstreams`).then((r) => r.json());
  console.log(`── 메타 ─────────────────────────────`);
  console.log(`  워크스트림 ${meta.workstreams.length}종 · 구현 ${meta.implemented.join(', ')}`);
  console.log(`  하네스 ${meta.harness_version} · 스키마 ${meta.schema_version}`);
  for (const w of RUNS) {
    if (!meta.implemented.includes(w)) bad('구현 목록', `${w} 가 implemented 에 없다`);
  }

  const results = [];
  for (const workstream of RUNS) {
    const target = OVERRIDE || DEFAULT_TARGET[workstream];
    const row = await runWorkstream(workstream, target);
    if (row) results.push(row);
  }

  console.log('\n══ 요약 ══════════════════════════════');
  console.log(`  ${'작업'.padEnd(9)} ${'대상'.padEnd(8)} ${'초'.padStart(6)} ${'장'.padStart(4)} ${'팩'.padStart(8)}  ${'E/D/C/Gap'.padEnd(14)} ${'본문링크'.padStart(9)}  평가`);
  for (const r of results) {
    const link = r.bodyLink ? `${r.bodyLink.linked}/${r.bodyLink.total}` : '—';
    console.log(`  ${r.workstream.padEnd(9)} ${String(r.target).padEnd(8)} ${r.seconds.toFixed(1).padStart(6)} ` +
      `${String(r.pages).padStart(4)} ${kb(r.maxPack).padStart(8)}  ${`${r.evidence}/${r.data}/${r.calc}/${r.gaps}`.padEnd(14)} ` +
      `${link.padStart(9)}  ${r.score ?? '—'}/100 ${r.grade}`);
  }

  if (results.length === 1 && results[0].markdown) {
    console.log('\n── 마크다운 앞부분 ──────────────────');
    console.log(results[0].markdown.split('\n').slice(0, 22).map((l) => `     ${l}`).join('\n'));
  }

  console.log(failures ? `\n결과: 실패 ${failures}건 ✗\n` : '\n결과: 하네스 이상 없음 ✓\n');
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
