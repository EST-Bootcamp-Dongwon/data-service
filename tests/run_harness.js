/* 리서치 하네스 전구간 실행 검사 (M4)
 *
 * H00 → H11 을 **브라우저가 하듯이** 순서대로 부른다. 서버가 상태를 갖지 않으므로
 * Context Pack 을 받아서 다음 요청에 그대로 실어 보낸다 (명세 §1.3).
 *
 * 보는 것
 *   1. 열두 상태가 전부 200 으로 답하는가 (자료가 없어도 오류로 죽지 않는가 — 명세 §6.4)
 *   2. stage_result 봉투가 GIC 공통계약 §7 필드를 그대로 갖고 있는가
 *   3. 진행률이 가중치 누적값인가 (12등분이 아닌가 — 명세 §5.1)
 *   4. **전송량**이 얼마인가 (C1 근거 정책을 정하려면 실측이 필요하다)
 *
 *   node tests/run_harness.js                 # 005930
 *   node tests/run_harness.js 035720          # 다른 종목
 *   node tests/run_harness.js 005930 http://… # 배포본
 */
const BASE = process.argv.find((a) => a.startsWith('http')) || 'http://127.0.0.1:8000';
const CODE = process.argv.slice(2).find((a) => !a.startsWith('http')) || '005930';

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

async function main() {
  console.log(`\n리서치 하네스 전구간 · ${BASE} · 종목 ${CODE}\n`);

  console.log('── 0. 메타 ─────────────────────────────');
  const meta = await fetch(`${BASE}/api/research/workstreams`).then((r) => r.json());
  console.log(`  워크스트림 ${meta.workstreams.length}종 · 구현 ${meta.implemented.join(',')}`);
  const planned = await fetch(`${BASE}/api/research/plan/CORP-R`).then((r) => r.json());
  const weights = planned.states.map((s) => s.weight);
  const total = weights.reduce((a, b) => a + b, 0);
  if (total === 100) ok('가중치 합 100', weights.join('+'));
  else bad('가중치 합', `${total}`);
  if (JSON.stringify(weights) !== JSON.stringify(new Array(12).fill(weights[0]))) {
    ok('12등분이 아님 (체감 진행률)', `H02=${weights[2]} H04=${weights[4]}`);
  } else bad('진행률', '12등분이다');

  console.log('\n── 1. H00 실행 ─────────────────────────');
  const run = await post('/api/research/runs', { workstream_id: 'CORP-R', code: CODE });
  if (run.status !== 200) { bad('H00', `HTTP ${run.status} ${JSON.stringify(run.body).slice(0, 200)}`); process.exit(1); }
  ok('run 생성', `${run.body.run_header.run_id} · ${run.ms}ms`);

  let pack = run.body.context_pack;
  const header = run.body.run_header;
  let questions = run.body.human_questions;

  const rows = [];
  const STATES = ['H01', 'H02', 'H03', 'H04', 'H05', 'H06', 'H07', 'H08', 'H09', 'H10', 'H11'];

  console.log('\n── 2. H01 → H11 ────────────────────────');
  for (const state of STATES) {
    const payload = { run_header: header, context_pack: pack, feedback: answersFor(questions, state) };
    const res = await post(`/api/research/runs/steps/${state}`, payload);

    if (res.status !== 200) {
      bad(state, `HTTP ${res.status} — ${JSON.stringify(res.body).slice(0, 300)}`);
      break;
    }
    const result = res.body.stage_result;
    const missing = ENVELOPE.filter((f) => !(f in result));
    if (missing.length) bad(`${state} 봉투`, `빠진 필드: ${missing.join(', ')}`);

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

  console.log('\n── 3. 판정 ─────────────────────────────');
  if (rows.length === STATES.length) ok('열두 상태 전부 200'); else bad('상태 실행', `${rows.length + 1}/12 만 돌았다`);
  const last = rows[rows.length - 1];
  if (last && last.weight === 100) ok('진행률 100% 도달'); else bad('진행률', `${last?.weight}%`);
  if (last && last.status === 'complete') ok('H11 complete'); else bad('H11', `status=${last?.status}`);

  const totalMs = rows.reduce((a, r) => a + r.ms, 0);
  const maxPack = Math.max(...rows.map((r) => r.pack));
  console.log(`\n  전구간 ${(totalMs / 1000).toFixed(1)}초 · 최대 Context Pack ${kb(maxPack)} · ` +
    `왕복 전송 합계 ${kb(rows.reduce((a, r) => a + r.sent + r.got, 0))}`);
  console.log(`  근거 ${last?.e} · 데이터 ${last?.d} · 계산 ${last?.c} · Gap ${last?.gaps}`);

  console.log('\n── 4. 마크다운 내보내기 ─────────────────');
  const md = await post('/api/research/export/md', { context_pack: pack });
  if (md.status === 200) {
    ok('MD 생성', `${md.body.page_count}장 · ${kb(md.body.bytes)}`);
    if (md.body.page_count > 15) bad('장수', `${md.body.page_count}장 — 15장 상한 초과`);
    if (md.body.merged?.length) console.log(`     합침: ${md.body.merged.join(' / ')}`);
    console.log('\n' + md.body.markdown.split('\n').slice(0, 24).map((l) => `     ${l}`).join('\n'));
  } else bad('MD 생성', `HTTP ${md.status} ${JSON.stringify(md.body).slice(0, 200)}`);

  console.log(failures ? `\n결과: 실패 ${failures}건 ✗\n` : '\n결과: 하네스 이상 없음 ✓\n');
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
