/* 리서치 화면 (Research) — 네 작업 공용 (명세 §7.2~§7.5 · M6)
 *
 * 화면 하나로 네 작업을 다 돌린다. **12상태는 넷 다 같기 때문**이다 (명세 §5.4).
 * 갈라지는 것은 대상 입력(종목코드 vs 업종코드)과 리포트 양식뿐이고, 어느 상태가
 * 갈라지는지는 `GET /api/research/plan/{id}` 가 `substages` 로 알려 준다.
 *
 * 이 파일이 하는 일 여섯 가지
 *   1) 워밍업      — 화면에 들어오면 함수를 미리 깨운다 (H00 콜드 5.8초 → 웜 0.21초)
 *   2) 대상 선택    — 기업은 종목 자동완성, 산업은 166개 목록 + `resolve`
 *   3) 실행 드라이버 — H00 → H11 을 순서대로 부르며 Context Pack 을 들고 다닌다 (§1.3)
 *   4) 진행률 모달  — §5.1 가중치 누적 · 12상태 체크리스트 · **상태별 예상 시간**
 *   5) 리포트      — 공통계약 §14 의 10필드 + 근거 드릴다운 + 용어 툴팁
 *   6) MD 내보내기  — `POST /api/research/export/md` 결과를 파일로 저장
 *
 * 서버는 상태를 갖지 않는다. 팩을 받아 다음 요청에 그대로 실어 보내는 쪽은 이 파일이다.
 */
window.Research = (() => {
  'use strict';

  const esc = (v) => window.Shell ? Shell.esc(v) : String(v ?? '');

  const STATE_IDS = ['H01', 'H02', 'H03', 'H04', 'H05', 'H06', 'H07', 'H08', 'H09', 'H10', 'H11'];

  // ── 상태별 예상 시간 기록 (localStorage) ───────────────────
  //
  // 예상 시간은 **환경마다 10배 넘게 다르다** — 로컬 전구간 0.8~2.6초 · 배포본 9.2~16.0초.
  // 그래서 서버가 준 배포본 실측 기준선으로 시작하되, 그 브라우저가 실제로 잰 값이
  // 쌓이면 그쪽으로 갈아탄다. 주소(origin)별로 따로 세는 이유가 그것이다.
  const TIMING_KEY = 'gic.research.timing.v1';
  const TIMING_KEEP = 5;                      // 최근 몇 번을 기억할지 (중앙값을 쓴다)

  function readTimings() {
    try { return JSON.parse(localStorage.getItem(TIMING_KEY)) || {}; } catch { return {}; }
  }

  function timingKey(workstream, stateId) {
    return `${location.origin}|${workstream}|${stateId}`;
  }

  /** 한 상태가 실제로 걸린 시간을 기록한다. 최근 것만 남긴다. */
  function recordTiming(workstream, stateId, ms) {
    try {
      const all = readTimings();
      const key = timingKey(workstream, stateId);
      const list = (all[key] || []).concat(Math.round(ms)).slice(-TIMING_KEEP);
      all[key] = list;
      localStorage.setItem(TIMING_KEY, JSON.stringify(all));
    } catch { /* 저장이 막혀 있어도(사생활 보호 모드 등) 리서치는 그대로 돈다 */ }
  }

  /**
   * 그 상태가 몇 초 걸릴지 — **직접 잰 값이 있으면 그것을, 없으면 서버 기준선을** 쓴다.
   * 어느 쪽을 썼는지(`source`)를 함께 돌려줘서 화면이 밝힐 수 있게 한다.
   */
  function estimate(workstream, stateRow) {
    const samples = readTimings()[timingKey(workstream, stateRow.state_id)] || [];
    if (samples.length) {
      const sorted = [...samples].sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)];
      return { seconds: median / 1000, exact: true, own: true,
               source: `이 브라우저 측정 ${samples.length}회 중앙값` };
    }
    return {
      seconds: stateRow.expected_seconds ?? 0,
      exact: !!stateRow.expected_exact,
      own: false,
      source: `배포본 실측 ${(state.plan?.baseline?.measured_at) || ''}`,
    };
  }

  // ── 화면 상태 ──────────────────────────────────────────────
  const state = {
    workstream: 'CORP-R',
    meta: null,                 // GET /workstreams
    plan: null,                 // GET /plan/{ws}
    target: null,               // {code, name, hint}
    header: null,
    pack: null,
    pending: [],                // 다음 요청에 실어 보낼 답변 (질문 카드가 채운다)
    rows: [],                   // 상태별 실행 결과
    ledger: null,
    report: null,
    running: false,
    aborted: false,
    warm: null,
    tab: 'report',
    terms: null,                // 용어 표기 목록 (툴팁용)
    termCache: new Map(),
    resolver: null,             // 질문 카드가 기다리는 Promise 의 resolve
    stageId: '',                // 지금 기다리고 있는 상태
    stageStarted: 0,            // 그 상태를 부른 시각 (경과 시계가 읽는다)
  };

  const $ = (id) => document.getElementById(id);

  // ══════════════════════════════════════════════════════════
  // 1. 워밍업 — 화면에 들어오면 함수를 미리 깨운다
  // ══════════════════════════════════════════════════════════
  //
  // 실측 — H00 콜드 5,777ms · 웜 206ms. 5.8초는 리서치가 느린 것이 아니라 함수가
  // 자고 있던 것이다. 사용자가 대상을 고르는 몇 초 동안 미리 깨워 두면 사라진다.
  // **실패해도 화면은 그대로 돈다.** 워밍업은 빨라지자는 것이지 조건이 아니다.
  async function warmUp() {
    const el = $('warmState');
    if (el) el.textContent = '함수를 깨우는 중…';
    const started = performance.now();
    try {
      const data = await App.get('/api/research/warmup');
      state.warm = data;
      const ms = Math.round(performance.now() - started);
      if (el) {
        el.textContent = data.was_cold
          ? `준비 완료 · 방금 깨웠다 (${ms}ms · ${data.loaded_now.join(', ')})`
          : `준비 완료 · 이미 깨어 있었다 (${ms}ms)`;
      }
    } catch (error) {
      state.warm = { warm: false, error: String(error.message || error) };
      if (el) el.textContent = '워밍업 실패 — 첫 실행이 5.8초쯤 더 걸릴 수 있다';
    }
  }

  // ══════════════════════════════════════════════════════════
  // 2. 작업 · 대상 선택
  // ══════════════════════════════════════════════════════════
  function renderWorkstreams() {
    const box = $('wsPick');
    if (!box || !state.meta) return;
    box.innerHTML = state.meta.workstreams.map((w) => {
      const on = w.id === state.workstream ? ' on' : '';
      return `<button type="button" class="ws-card${on}" data-ws="${esc(w.id)}">` +
             `<span class="ws-id">${esc(w.id)}</span>` +
             `<b>${esc(w.name)}</b>` +
             `<span class="ws-note">${esc(w.sufficiency_label)}</span>` +
             `<span class="ws-note">${esc(w.needs.join(' · '))}</span></button>`;
    }).join('');
    box.querySelectorAll('.ws-card').forEach((el) => {
      el.addEventListener('click', () => selectWorkstream(el.dataset.ws));
    });
  }

  async function selectWorkstream(id) {
    if (!id || state.running) return;
    state.workstream = id;
    state.target = null;
    renderWorkstreams();
    if (window.Shell) Shell.markActive(id.toLowerCase());
    // 주소를 바꿔 두면 새로고침·북마크가 같은 작업으로 돌아온다
    history.replaceState(null, '', `/research?ws=${encodeURIComponent(id)}`);

    const kind = kindOf(id);
    $('targetInput').value = '';
    $('targetInput').placeholder = kind === 'industry'
      ? '업종코드·산업명·종목명 (예: 261 · 반도체 · 삼성전자)'
      : '종목코드 또는 종목명 (예: 005930 · 삼성전자)';
    $('targetHint').textContent = kind === 'industry'
      ? '산업 166개 중에서 고른다. 무엇을 넣어도 업종 하나로 확정해 준다.'
      : '국내·미국 상장 종목을 찾는다.';
    $('pickDrop').innerHTML = '';
    paintTargetState();
    state.plan = await App.get(`/api/research/plan/${id}`);
    renderPlanPreview();
    if (kind === 'industry') loadIndustries();
  }

  function kindOf(id) {
    const row = (state.meta?.workstreams || []).find((w) => w.id === id);
    return row ? row.target_kind : 'corp';
  }

  /** 실행 전에 "몇 초쯤 걸리는지" 를 미리 보여 준다 — 모달과 같은 근거를 쓴다. */
  function renderPlanPreview() {
    const el = $('planPreview');
    if (!el || !state.plan) return;
    const total = state.plan.states.reduce((sum, s) => sum + estimate(state.workstream, s).seconds, 0);
    const heavy = [...state.plan.states]
      .sort((a, b) => estimate(state.workstream, b).seconds - estimate(state.workstream, a).seconds)[0];
    const source = estimate(state.workstream, state.plan.states[0]).source;
    const forked = state.plan.states.filter((s) => (s.substages || []).length);
    el.innerHTML =
      `<b>12상태 · 예상 ${total.toFixed(0)}초</b> — 가장 오래 걸리는 곳은 ` +
      `<b>${esc(heavy.state_id)} ${esc(heavy.name)} ${estimate(state.workstream, heavy).seconds.toFixed(1)}초</b>다. ` +
      `<br />갈라지는 상태: ${forked.length ? forked.map((s) => `${esc(s.state_id)}(${s.substages.length})`).join(' · ') : '없음'} · ` +
      `질문 지점: ${state.plan.asks_at.join(' · ')}` +
      `<br /><span class="muted">예상 시간 출처: ${esc(source)} · ${esc(state.plan.baseline?.miss_note || '')}</span>`;
  }

  /** 산업 목록을 미리 받아 둔다 (IND-* 전용). */
  async function loadIndustries() {
    try {
      const data = await App.get('/api/research/industries?digits=3&min_members=1&limit=300');
      state.industries = data.rows || [];
      $('targetHint').textContent =
        `산업 ${data.total}개 (구성 종목 3곳 이상 ${data.enough}개) · ${data.level} 기준`;
    } catch { state.industries = []; }
  }

  /** 대상 검색 — 기업은 종목 자동완성, 산업은 목록 필터 + `resolve`. */
  const searchTarget = App.debounce(async (query) => {
    const drop = $('pickDrop');
    if (!drop) return;
    const text = query.trim();
    if (!text) { drop.innerHTML = ''; return; }

    try {
      if (kindOf(state.workstream) === 'industry') {
        const lower = text.toLowerCase();
        const local = (state.industries || []).filter((r) =>
          r.industry_code.startsWith(text) || r.name.toLowerCase().includes(lower)).slice(0, 12);
        let rows = local.map((r) => ({
          code: r.industry_code, name: r.name,
          sub: `구성 ${r.member_count}곳 · ${esc(r.section || '')}`,
        }));
        // 목록에 없으면 종목명일 수 있다 — 서버가 업종으로 바꿔 준다
        if (!rows.length) {
          const found = await App.get(`/api/research/industries/resolve?q=${encodeURIComponent(text)}`);
          if (found.available) {
            rows = [{ code: found.industry_code, name: found.name,
                      sub: `${esc(found.matched_by || '')} · 구성 ${found.member_count}곳` }];
          }
        }
        paintDrop(rows, '찾는 산업이 없다 — 업종코드(261)나 산업명(반도체)으로 넣어 본다');
      } else {
        const found = await App.get(`/api/search?q=${encodeURIComponent(text)}&limit=10`);
        paintDrop((found || []).map((r) => ({
          code: r.code, name: r.name, sub: `${r.exchange || r.market}${r.is_etf ? ' · ETF' : ''}`,
        })), '찾는 종목이 없다');
      }
    } catch (error) {
      drop.innerHTML = `<div class="pick-row"><span class="sub">검색 실패 — ${esc(error.message)}</span></div>`;
    }
  }, 220);

  function paintDrop(rows, emptyText) {
    const drop = $('pickDrop');
    if (!rows.length) {
      drop.innerHTML = `<div class="pick-row"><span class="sub">${esc(emptyText)}</span></div>`;
      return;
    }
    drop.innerHTML = rows.map((r) =>
      `<button type="button" class="pick-row" data-code="${esc(r.code)}" data-name="${esc(r.name)}">` +
      `<span class="code">${esc(r.code)}</span><span>${esc(r.name)}</span>` +
      `<span class="sub">${esc(r.sub)}</span></button>`).join('');
    drop.querySelectorAll('.pick-row[data-code]').forEach((el) => {
      el.addEventListener('click', () => {
        state.target = { code: el.dataset.code, name: el.dataset.name };
        $('targetInput').value = `${el.dataset.name} (${el.dataset.code})`;
        drop.innerHTML = '';
        paintTargetState();
      });
    });
  }

  function paintTargetState() {
    const button = $('runBtn');
    if (button) button.disabled = !state.target || state.running;
    const label = $('targetPicked');
    if (label) {
      label.textContent = state.target
        ? `대상 확정 — ${state.target.name} (${state.target.code})`
        : '대상을 고르면 실행할 수 있다';
    }
  }

  // ══════════════════════════════════════════════════════════
  // 3. 실행 드라이버 — H00 → H11
  // ══════════════════════════════════════════════════════════
  async function run() {
    if (!state.target || state.running) return;
    state.running = true;
    state.aborted = false;
    state.rows = [];
    state.pending = [];
    state.report = null;
    state.ledger = null;
    paintTargetState();
    openModal();

    try {
      // H00 — 실행 헤더와 초기 Context Pack
      const started = performance.now();
      state.stageId = 'H00';
      state.stageStarted = started;
      paintModal();
      const created = await App.post('/api/research/runs', {
        workstream_id: state.workstream,
        code: state.target.code,
        name: state.target.name,
        as_of: $('asOf').value || undefined,
        audience: $('audience').value || undefined,
        questions: ($('objective').value || '').trim() ? [$('objective').value.trim()] : [],
      });
      state.stageId = '';
      recordTiming(state.workstream, 'H00', performance.now() - started);
      pushRow('H00', created.stage_result, created.progress, performance.now() - started, null);
      state.header = created.run_header;
      state.pack = created.context_pack;
      state.pending = created.human_questions || [];
      paintModal();

      for (const stateId of STATE_IDS) {
        if (state.aborted) break;
        // 앞 상태가 남긴 질문을 **먼저** 받는다 (§2-6 — 다음 상태가 Feedback Log 를 읽는다)
        const feedback = state.pending.length ? await askQuestions(stateId, state.pending) : [];
        if (state.aborted) break;

        const begin = performance.now();
        state.stageId = stateId;
        state.stageStarted = begin;
        paintModal();
        const response = await App.post(`/api/research/runs/steps/${stateId}`, {
          run_header: state.header,
          context_pack: state.pack,
          feedback,
        });
        const elapsed = performance.now() - begin;
        state.stageId = '';
        recordTiming(state.workstream, stateId, elapsed);

        state.pack = response.context_pack;
        state.pending = response.human_questions || [];
        state.ledger = response.ledger || state.ledger;
        pushRow(stateId, response.stage_result, response.progress, elapsed, response.ledger);
        paintModal();
      }

      state.report = (state.pack?.CX_workstream || {}).report || null;
      renderResult();
      paintModal();
    } catch (error) {
      $('modalError').textContent = `실행이 멈췄다 — ${error.message}`;
      $('modalError').hidden = false;
    } finally {
      state.running = false;
      paintTargetState();
      paintModal();
    }
  }

  function pushRow(stateId, result, progress, ms, ledger) {
    state.rows.push({
      state_id: stateId,
      status: result?.status || '',
      role: result?.active_role || '',
      note: (result?.verified_result || [])[0] || '',
      confidence: result?.confidence || '',
      gaps: (result?.gap_ids || []).length,
      ms,
      weight: progress?.weight_done ?? 0,
      ledger: ledger || null,
    });
  }

  // ══════════════════════════════════════════════════════════
  // 4. 진행률 모달
  // ══════════════════════════════════════════════════════════
  function openModal() {
    $('modalError').hidden = true;
    $('modalBack').hidden = false;
    $('modalQuestion').hidden = true;
    $('modalProgress').hidden = false;
    paintModal();
    startTicker();
  }

  function closeModal() {
    // 실행 중에 닫으면 **거기서 멈춘다.** 몰래 계속 돌리지 않는다.
    if (state.running) state.aborted = true;
    if (state.resolver) { state.resolver([]); state.resolver = null; }
    stopTicker();
    $('modalBack').hidden = true;
  }

  // ── 경과 시계 ────────────────────────────────────────────
  //
  // 예상 시간은 예상일 뿐이라 **빗나갈 때가 있다.** 실측 — IND-R 의 H04 는 기준선이
  // 2.4초인데 처음 보는 산업에서는 9.3초가 나왔다 (캐시가 비어 있었다).
  // 그때 화면이 예상값만 들고 가만히 있으면 멈춘 것처럼 보인다. 그래서 기다리는 동안
  // **실제로 흐른 시간을 계속 센다.** 예상을 넘어서면 그 사실도 같이 말한다.
  let ticker = null;

  function startTicker() {
    stopTicker();
    ticker = setInterval(tickRunning, 250);
  }

  function stopTicker() {
    if (ticker) { clearInterval(ticker); ticker = null; }
  }

  /** 진행 중인 줄의 시간과 경과 줄만 고친다 (모달 전체를 다시 그리지 않는다). */
  function tickRunning() {
    if (!state.running) { stopTicker(); return; }
    const cell = document.querySelector('.state-row.now .st-time');
    if (cell && state.stageId) {
      const spent = (performance.now() - state.stageStarted) / 1000;
      const planned = (state.plan?.states || []).find((s) => s.state_id === state.stageId);
      const guess = planned ? estimate(state.workstream, planned).seconds : 0;
      cell.textContent = spent > guess + 0.5 && guess
        ? `${spent.toFixed(1)}초 (예상 ${guess.toFixed(1)}초 넘김)`
        : `${spent.toFixed(1)} / ${guess.toFixed(1)}초`;
    }
    const spentAll = state.rows.reduce((sum, r) => sum + r.ms, 0) / 1000
      + (state.stageId ? (performance.now() - state.stageStarted) / 1000 : 0);
    const label = $('progSpent');
    if (label) label.textContent = spentAll.toFixed(1);
  }

  function paintModal() {
    if (!state.plan) return;
    const done = new Map(state.rows.map((r) => [r.state_id, r]));
    const last = state.rows[state.rows.length - 1];
    const percent = last ? last.weight : 0;

    $('modalTitle').textContent =
      `${(state.meta.workstreams.find((w) => w.id === state.workstream) || {}).name} 실행 중`;
    $('modalSub').textContent = state.target ? `${state.target.name} (${state.target.code})` : '';
    $('progFill').style.width = `${percent}%`;
    $('progPct').textContent = `${percent}%`;

    // 남은 시간 — 아직 안 끝난 상태들의 예상치를 더한다
    const remaining = state.plan.states
      .filter((s) => !done.has(s.state_id))
      .reduce((sum, s) => sum + estimate(state.workstream, s).seconds, 0);
    const spent = state.rows.reduce((sum, r) => sum + r.ms, 0) / 1000;
    $('progEta').innerHTML = state.running
      ? `경과 <b id="progSpent">${spent.toFixed(1)}</b>초 · 남은 예상 <b>${remaining.toFixed(0)}초</b>` +
        ` <span class="muted">(${esc(estimate(state.workstream, state.plan.states[0]).source)})</span>`
      : (state.rows.length
        ? `전구간 ${spent.toFixed(1)}초 · ${state.rows.length}상태`
        : '');

    $('stateList').innerHTML = state.plan.states.map((s) => {
      const row = done.get(s.state_id);
      const guess = estimate(state.workstream, s);
      // 질문 카드를 띄우고 사람을 기다리는 동안에는 `stageId` 가 비어 있다 —
      // 그때 어느 줄도 '진행 중' 이 아니어야 맞다. 기다리는 쪽은 우리가 아니라 서버가 아니다.
      const running = state.running && !row && state.stageId === s.state_id;

      let cls = 'wait';
      let icon = '·';
      if (row) {
        cls = row.status === 'partial-continue' ? 'warn'
          : row.status === 'blocked' ? 'bad' : 'done';
        icon = cls === 'done' ? '✓' : cls === 'warn' ? '!' : '✕';
      } else if (running) { cls = 'now'; icon = '▶'; }

      const time = row ? `${(row.ms / 1000).toFixed(1)}초`
        : running ? `0.0 / ${guess.seconds.toFixed(1)}초`
          : `${guess.exact ? '' : '≈'}${guess.seconds.toFixed(1)}초`;
      const note = row ? (row.note || s.does)
        : running ? `${esc(s.does)} …`
          : (s.asks ? `${s.does} · 질문 있음` : s.does);
      const subs = (s.substages || []).length && (row || running)
        ? `<div class="st-subs">└ ${s.substages.map(esc).join(' · ')}</div>` : '';
      return `<div class="state-row ${cls}">` +
             `<span class="st-ico" aria-hidden="true">${icon}</span>` +
             `<span class="st-name">${esc(s.state_id)} ${esc(s.name)}</span>` +
             `<span class="st-role">${esc(s.role)}</span>` +
             `<span class="st-note">${esc(note)}</span>` +
             `<span class="st-time">${esc(time)}</span>${subs}</div>`;
    }).join('');

    // H02 가 끝났으면 **어느 외부 호출이 느렸는지**를 그대로 보여 준다
    const timing = (state.pack?.CX_workstream || {}).h02_timing;
    $('h02Note').innerHTML = timing
      ? `<b>H02</b> ${esc(timing.text)}` +
        (timing.failed?.length ? ` · 실패 ${esc(timing.failed.join(', '))}` : '')
      : (state.plan.baseline?.why_h02_slow ? `<span class="muted">${esc(state.plan.baseline.why_h02_slow)}</span>` : '');

    const led = state.ledger || {};
    $('ledgerLine').innerHTML = state.ledger
      ? ['근거', '데이터', '계산', 'Gap', '충돌'].map((label, i) => {
        const value = [led.evidence, led.data, led.calculations, led.gaps, led.conflicts][i] ?? 0;
        return `<span>${label} <b>${value}</b></span>`;
      }).join('')
      : '<span class="muted">장부는 H02 부터 쌓인다</span>';

    $('modalDone').hidden = state.running || !state.report;
  }

  // ── 질문 카드 (H01 · H04 · H08) ──────────────────────────
  //
  // 건너뛰면 `의견 미입력` 으로 기록된다. **AI 가 선호를 추정하지 않는다** (§2-7).
  function askQuestions(stateId, questions) {
    return new Promise((resolve) => {
      let index = 0;
      const picked = new Array(questions.length).fill(null);
      state.resolver = resolve;

      const paint = () => {
        const q = questions[index];
        $('modalProgress').hidden = true;
        $('modalQuestion').hidden = false;
        $('qStage').textContent = `${q.id.split('-')[1] || stateId} — 확인이 필요합니다`;
        $('qCount').textContent = `(${index + 1}/${questions.length})`;
        $('qText').textContent = q.text;
        $('qWhy').textContent = q.why || '답변은 C6 Decisions 에 기록되고 다음 상태가 읽는다';
        $('qOpts').innerHTML = (q.options || []).map((option, i) => {
          const on = picked[index] === option ? ' on' : '';
          const rec = i === (q.recommended ?? 0) ? '<span class="rec">추천</span>' : '';
          return `<button type="button" class="qopt${on}" data-i="${i}">` +
                 `<span class="mark" aria-hidden="true">${picked[index] === option ? '◉' : '○'}</span>` +
                 `<span>${esc(option)}</span>${rec}</button>`;
        }).join('');
        $('qOpts').querySelectorAll('.qopt').forEach((el) => {
          el.addEventListener('click', () => {
            picked[index] = q.options[Number(el.dataset.i)];
            paint();
          });
        });
        $('qNext').textContent = index + 1 < questions.length ? '다음' : '반영하고 계속';
      };

      const finish = (skipAll) => {
        state.resolver = null;
        $('modalQuestion').hidden = true;
        $('modalProgress').hidden = false;
        resolve(questions.map((q, i) => ({
          feedback_id: `F-${q.id}`,
          stage_id: stateId,
          question_id: q.id,
          question: q.text,
          // 고르지 않았으면 **빈 답**을 보낸다. 서버가 '의견 미입력' 으로 적는다
          answer: skipAll ? '' : (picked[i] || ''),
        })));
      };

      $('qSkip').onclick = () => finish(true);
      $('qNext').onclick = () => {
        if (index + 1 < questions.length) { index += 1; paint(); } else finish(false);
      };
      paint();
    });
  }

  // ══════════════════════════════════════════════════════════
  // 5. 리포트 · 장부 · 근거 드릴다운
  // ══════════════════════════════════════════════════════════

  // D- 의 지표 이름은 영어다 (`revenue` · `operating_income` · `261.per` …).
  // 본문은 한국어라 그대로는 못 맞춘다. **본문에 이 낱말이 있을 때만** 링크를 건다.
  const METRIC_LABELS = {
    revenue: ['매출'], operating_income: ['영업이익'], net_income: ['순이익', '당기순이익'],
    assets: ['자산'], equity: ['자본'], liabilities: ['부채'],
    current_assets: ['유동자산'], current_liabilities: ['유동부채'],
    operating_cash_flow: ['영업현금흐름', '현금흐름'],
    market_cap: ['시가총액', '시총'], per: ['PER'], pbr: ['PBR'], roe: ['ROE'],
    r250: ['수익률'], 생산지수: ['생산지수'],
    close: ['종가', '주가'], shares: ['주식수'],
    // ── 파생값 (M7 · 변경노트 N69) ──
    // 이름은 **본문에 실제로 쓰이는 낱말**이어야 한다. 지표 이름을 그대로 적으면
    // 한국어 본문과 영영 안 맞아서 링크가 걸리지 않는다.
    // ⚠️ 서버의 `app/services/research/linkcheck.py METRIC_LABELS` 와 같은 표다 — 함께 고친다.
    revenue_cagr: ['CAGR'], operating_margin: ['영업이익률'], net_margin: ['순이익률'],
    asset_turnover: ['자산회전율'], equity_multiplier: ['재무레버리지'],
    debt_ratio: ['부채비율'], current_ratio: ['유동비율'],
    cash_conversion: ['현금전환', '영업현금흐름/영업이익'],
    eps: ['EPS'], peer_median_per: ['피어 중앙값', '피어 PER'],
    valuation_band_base: ['주당 가치', '주당가치', '기준'],
    valuation_band_low: ['주당 가치', '주당가치'],
    valuation_band_high: ['주당 가치', '주당가치'],
    per_premium_pct: ['피어 대비'],
    listed_market_cap: ['시가총액 합계', '상장 시가총액'],
    cap_share_pct: ['시가총액 비중'], cr3_pct: ['CR3'], hhi: ['HHI'],
    candidate_cap_share: ['업종 시총'], coverage: ['coverage'],
  };

  function labelsOf(metric) {
    const tail = String(metric || '').split('.').pop();
    return METRIC_LABELS[tail] || (tail ? [tail] : []);
  }

  /**
   * 값 → D- 색인. 서버가 쓴 **표기법 그대로** 열쇠를 만든다
   * (`_trillion` 은 원 단위를 조로 줄여 소수 1자리로 쓴다 — `80.1조`).
   */
  function buildValueIndex(pack) {
    const map = new Map();
    const put = (key, row) => {
      if (!key) return;
      const bucket = map.get(key) || [];
      if (!bucket.includes(row)) bucket.push(row);
      map.set(key, bucket);
    };
    for (const row of pack.C2_data || []) {
      const value = row.value;
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      put(String(value), row);
      put(value.toFixed(1), row);
      put(value.toFixed(2), row);
      put(Math.round(value).toLocaleString('ko-KR'), row);
      if (Math.abs(value) >= 1e12) put((value / 1e12).toFixed(1), row);
    }
    return map;
  }

  // 수치가 아니라 **개수**인 것들 — `검사 5건` 의 5는 근거가 될 수 없다
  const COUNT_SUFFIX = /^\s*(건|개|곳|장|회|차|위|명|종목|년|개월)/;
  const VALUE_SUFFIX = /^\s*(조|억|%|배|원|점|지수|p|pp)/;
  const NUMBER_RE = /[-+]?\d[\d,]*(?:\.\d+)?/g;

  /**
   * 본문 한 줄에서 **근거로 되짚을 수 있는 수치만** 링크로 만든다.
   *
   * ⚠️ 실측 — 본문 수치의 5~11% 만 장부의 D- 와 이어진다. D- 는 원시 계정
   *    (`revenue` · `operating_income` …)인데 본문에 나오는 것은 대부분 **파생값**
   *    (밸류에이션 밴드 · CAGR · 점수)이라 장부에 번호가 없기 때문이다.
   *    그래서 **확실할 때만 링크하고, 못 이은 수를 화면이 그대로 밝힌다.**
   *    아무 D- 나 갖다 붙이면 없는 근거 사슬을 만들어 내는 것이라 하지 않는다.
   *    (파생값도 장부에 올리는 일은 M7 안건이다 — 마스터인덱스 변경노트 참고)
   *
   * @returns {{html: string, total: number, linked: number}}
   */
  function linkNumbers(text, index) {
    const line = String(text ?? '');
    // 날짜(2026-08-02 · 2026-06)와 종목코드((000660))는 통째로 건너뛴다.
    // 숫자처럼 생겼을 뿐 값이 아니라서, 세면 "몇 개가 근거로 이어지나" 가 실제보다 나빠 보인다.
    const skipZones = [];
    for (const m of line.matchAll(/\d{4}-\d{2}(?:-\d{2})?|\(\d{6}\)/g)) {
      skipZones.push([m.index, m.index + m[0].length]);
    }

    let out = '';
    let cursor = 0;
    let total = 0;
    let linked = 0;

    for (const match of line.matchAll(NUMBER_RE)) {
      const start = match.index;
      const end = start + match[0].length;
      if (skipZones.some(([s, e]) => start >= s && end <= e)) continue;

      const raw = match[0];
      const after = line.slice(end);
      const bare = raw.replace(/,/g, '');
      if (/^(19|20)\d{2}$/.test(bare)) continue;          // 연도
      if (COUNT_SUFFIX.test(after)) continue;             // 개수 (건·개·곳 …)
      // 단위도 소수점도 자릿점도 없는 **작은 맨숫자**는 값이 아니라 개수·순위다
      // (`검사 5건 중 통과 4` 의 4). 장부에 있는 값이면 그대로 센다.
      if (!VALUE_SUFFIX.test(after) && !/[.,]/.test(raw) && raw.length <= 3
          && !index.has(raw) && !index.has(bare)) continue;

      total += 1;
      const candidates = index.get(raw) || index.get(bare) || [];
      if (!candidates.length) continue;

      // 같은 줄에 지표 이름이 있는 것만 인정한다
      const corroborated = candidates.filter((row) =>
        labelsOf(row.metric).some((label) => line.includes(label)));
      let pick = null;
      if (corroborated.length === 1) pick = corroborated[0];
      else if (!corroborated.length && candidates.length === 1 && VALUE_SUFFIX.test(after)) {
        pick = candidates[0];                             // 단위가 붙은 유일한 후보
      }
      if (!pick) continue;                                // 여럿이면 링크하지 않는다

      out += esc(line.slice(cursor, start));
      out += `<button type="button" data-evidence-id="${esc(pick.id)}" ` +
             `title="${esc(pick.id)} ${esc(pick.metric)}">${esc(raw)}</button>`;
      cursor = end;
      linked += 1;
    }
    out += esc(line.slice(cursor));
    return { html: out, total, linked };
  }

  /** 용어에 밑줄을 친다 (긴 낱말부터 · 겹치지 않게 — 서버 `annotate` 와 같은 규칙). */
  function markTerms(html) {
    if (!state.terms || !state.terms.length) return html;
    // **태그와 글자를 갈라** 글자 구간에만 손댄다. 이미 만들어진 태그(근거 링크 등)를
    // 건드리면 속성 안에 span 이 끼어들어 마크업이 깨진다.
    return String(html).split(/(<[^>]*>)/).map((piece) => {
      if (!piece || piece.startsWith('<')) return piece;
      let body = piece;
      const used = [];
      for (const term of state.terms) {
        const at = body.indexOf(term);
        if (at < 0) continue;
        if (used.some(([s, e]) => !(at + term.length <= s || at >= e))) continue;
        used.push([at, at + term.length]);
        if (used.length >= 6) break;                      // 한 줄에 툴팁을 너무 많이 달지 않는다
      }
      used.sort((a, b) => b[0] - a[0]);                   // 뒤에서부터 끼워 넣는다
      for (const [s, e] of used) {
        const word = body.slice(s, e);
        body = `${body.slice(0, s)}<span class="term" data-term="${esc(word)}">${word}</span>${body.slice(e)}`;
      }
      return body;
    }).join('');
  }

  function renderResult() {
    $('resultBox').hidden = false;
    renderSummary();
    renderTab();
  }

  function renderSummary() {
    const evaluation = (state.pack?.CX_workstream || {}).evaluation || {};
    const led = state.ledger || {};
    const seconds = state.rows.reduce((sum, r) => sum + r.ms, 0) / 1000;
    const tiles = [
      ['리포트', `${state.report?.page_count ?? 0}장`, `상한 15장 · ${state.report?.format || ''}`],
      ['평가', `${evaluation.total ?? '—'}/100`, `등급 ${evaluation.grade || '—'}`],
      // 직접근거와 계산유래를 **갈라서** 밝힌다. 합쳐 놓으면 "전부 근거가 있다" 로 읽혀서
      // 그중 몇 건이 우리가 만든 값인지가 사라진다 (M7 · 변경노트 N69).
      ['근거 · 데이터', `${led.evidence ?? 0} · ${led.data ?? 0}`,
        `추적가능 ${Math.round((led.data_traceable_ratio ?? led.data_linked_ratio ?? 0) * 100)}%`
        + ` — 직접근거 ${led.data_with_evidence ?? 0} · 계산유래 ${led.data_derived ?? 0}`],
      ['Gap · 충돌', `${led.gaps ?? 0} · ${led.conflicts ?? 0}`, '없는 자료는 없다고 밝힌 것이다'],
      ['전구간', `${seconds.toFixed(1)}초`, `${state.rows.length}상태`],
    ];
    $('resultTiles').innerHTML = tiles.map(([label, value, sub]) =>
      `<div class="tile"><div class="tile-label">${esc(label)}</div>` +
      `<div class="tile-value">${esc(value)}</div>` +
      `<div class="tile-sub">${esc(sub)}</div></div>`).join('');

    const critical = evaluation.critical || [];
    $('resultCritical').innerHTML = critical.length
      ? `<div class="badge warn">⚠ 중대 결함 ${critical.length}건 — ${critical.map(esc).join(' / ')}</div>`
      : '';
  }

  function renderTab() {
    document.querySelectorAll('#resultTabs .pill').forEach((el) => {
      el.classList.toggle('on', el.dataset.tab === state.tab);
    });
    if (state.tab === 'report') renderReport();
    else if (state.tab === 'ledger') renderLedger();
    else renderStates();
  }

  function renderReport() {
    const box = $('tabBody');
    if (!state.report) { box.innerHTML = '<p class="hint">리포트가 없다.</p>'; return; }
    const index = buildValueIndex(state.pack);
    let totals = 0;
    let links = 0;

    const pages = state.report.pages.map((page) => {
      const key = linkNumbers(page.key_message, index);
      totals += key.total; links += key.linked;
      const body = (page.body || []).map((line) => {
        const marked = linkNumbers(line, index);
        totals += marked.total; links += marked.linked;
        return `<li>${markTerms(marked.html)}</li>`;
      }).join('');

      const card = page.interpretation && page.interpretation.id ? `
        <dl class="rp-card">
          <dt>관찰</dt><dd>${markTerms(esc(page.interpretation.observation))}</dd>
          <dt>의미</dt><dd>${markTerms(esc(page.interpretation.meaning))}</dd>
          <dt>대안 가설</dt><dd>${esc(page.interpretation.alternative)}</dd>
          <dt>한계</dt><dd>${esc(page.interpretation.limitation)}</dd>
          <dt>다음 확인</dt><dd>${esc(page.interpretation.next_check)}</dd>
        </dl>` : '';

      // "왜 12장인가" 를 화면이 설명해야 한다 (합친 장은 그 사실을 페이지에 적는다)
      const merged = (page.merged_from || []).length
        ? `<div class="rp-merged">이 장은 slot ${page.merged_from.join(', ')} 을 합친 것이다 — 자료가 없는 장을 억지로 만들지 않는다 (§6.2)</div>`
        : '';
      const gaps = (page.gaps || []).length
        ? `<div class="rp-gap">⚠ ${page.gaps.map(esc).join(' / ')}</div>` : '';

      return `<article class="rp">
        <div class="rp-head"><span class="rp-no">${page.page} / ${state.report.page_count}</span>
          <h3>${esc(page.title)}</h3>
          ${window.Shell ? Shell.grade(confidenceLevel(page.confidence), `신뢰도 ${page.confidence}`) : ''}</div>
        <p class="rp-key">${markTerms(key.html)}</p>
        <ul class="rp-body">${body}</ul>
        ${card}${merged}${gaps}
        <div class="rp-meta">
          <span>출처: ${(page.sources || []).length ? page.sources.map(esc).join(' · ') : '—'}</span>
          <span>${esc(page.human_decision)}</span>
          ${page.visual ? `<span>차트: ${esc(page.visual)}</span>` : ''}
        </div>
      </article>`;
    }).join('');

    const missed = totals - links;
    box.innerHTML =
      `<p class="hint" id="linkNote">본문 수치 <b>${totals}개</b> 중 <b>${links}개</b>가 근거로 이어진다. ` +
      `나머지 ${missed}개는 <b>파생값</b>(밸류에이션 밴드 · CAGR · 점수 · 개수)이라 장부에 D- 번호가 없다 — ` +
      `없는 사슬을 만들지 않으려고 링크를 걸지 않았다. 원자료는 <b>장부</b> 탭에 전부 있다.</p>` +
      (state.report.merged?.length
        ? `<p class="hint">밀도 조정: ${state.report.merged.map(esc).join(' · ')}</p>` : '') +
      `<div class="rp-list">${pages}</div>`;
    bindDrill(box);
    bindTips(box);
  }

  function confidenceLevel(value) {
    return value === 'high' ? 'good' : value === 'low' ? 'serious' : 'warning';
  }

  function renderLedger() {
    const box = $('tabBody');
    const pack = state.pack || {};
    const evidence = pack.C1_evidence || [];
    const data = pack.C2_data || [];
    const calcs = (pack.logs || {}).calculations || [];
    const gaps = (pack.logs || {}).gaps || [];

    box.innerHTML = `
      <p class="hint">모든 수치는 여기서 근거까지 되짚을 수 있다. <b>D- 를 누르면</b> 사슬이 열린다.</p>
      <h2>데이터 D- <span class="muted">${data.length}건</span></h2>
      <div class="table-scroll"><table><thead><tr>
        <th>ID</th><th>지표</th><th class="num">값</th><th>단위</th><th>기간</th><th>근거</th>
      </tr></thead><tbody>${data.slice(0, 400).map((row) => `<tr>
        <td><button type="button" data-evidence-id="${esc(row.id)}">${esc(row.id)}</button></td>
        <td>${esc(row.metric)}</td>
        <td class="num">${typeof row.value === 'number' ? App.num(row.value, 2) : esc(row.value)}</td>
        <td>${esc(row.unit)}</td><td>${esc(row.period)}</td>
        <td>${row.evidence_id ? esc(row.evidence_id)
          : row.calc_id ? `<span class="muted">파생 · ${esc(row.calc_id)}</span>`
            : '<span class="muted">없음</span>'}</td>
      </tr>`).join('')}</tbody></table></div>
      ${data.length > 400 ? `<p class="hint">앞 400건만 보였다 (전체 ${data.length}건).</p>` : ''}

      <h2 style="margin-top:20px">근거 E- <span class="muted">${evidence.length}건</span></h2>
      <div class="table-scroll"><table><thead><tr>
        <th>ID</th><th>주장</th><th>출처</th><th>등급</th><th>신뢰도</th>
      </tr></thead><tbody>${evidence.map((row) => `<tr>
        <td><button type="button" data-evidence-id="${esc(row.id)}">${esc(row.id)}</button></td>
        <td>${esc(row.claim)}</td><td>${esc(row.source)}</td>
        <td>${esc(row.grade)}</td><td>${esc(row.confidence)}</td>
      </tr>`).join('')}</tbody></table></div>

      <h2 style="margin-top:20px">계산 CALC- <span class="muted">${calcs.length}건</span></h2>
      <div class="table-scroll"><table><thead><tr>
        <th>ID</th><th>식</th><th class="num">결과</th><th>입력</th><th>가정</th>
      </tr></thead><tbody>${calcs.map((row) => `<tr>
        <td><button type="button" data-evidence-id="${esc(row.id)}">${esc(row.id)}</button></td>
        <td><code>${esc(row.formula)}</code></td>
        <td class="num">${typeof row.result === 'number' ? App.num(row.result, 2) : esc(row.result)} ${esc(row.unit)}</td>
        <td>${(row.inputs || []).length ? row.inputs.map(esc).join(', ') : '<span class="muted">기록 없음</span>'}</td>
        <td>${esc(row.assumption)}</td>
      </tr>`).join('')}</tbody></table></div>

      <h2 style="margin-top:20px">Gap <span class="muted">${gaps.length}건</span></h2>
      <div class="table-scroll"><table><thead><tr>
        <th>ID</th><th>무엇이 없나</th><th>왜 없나</th><th>결론에 미치는 영향</th><th>어떻게 닫나</th>
      </tr></thead><tbody>${gaps.map((row) => `<tr>
        <td>${esc(row.id)}</td><td>${esc(row.affected_claim_or_field)}</td>
        <td>${esc(row.reason)}</td><td>${esc(row.impact)}</td><td>${esc(row.plan_to_close)}</td>
      </tr>`).join('')}</tbody></table></div>`;
    bindDrill(box);
  }

  function renderStates() {
    const box = $('tabBody');
    box.innerHTML = `
      <p class="hint">이번 실행의 상태별 기록이다. 여기 걸린 시간이 다음 실행의 <b>예상 시간</b>이 된다.</p>
      <div class="table-scroll"><table><thead><tr>
        <th>상태</th><th>역할</th><th>status</th><th class="num">걸린 시간</th>
        <th class="num">예상</th><th class="num">진행</th><th>첫 줄</th>
      </tr></thead><tbody>${state.rows.map((row) => {
        const planned = state.plan.states.find((s) => s.state_id === row.state_id) || {};
        const guess = estimate(state.workstream, planned);
        return `<tr>
          <td><code>${esc(row.state_id)}</code></td><td>${esc(row.role)}</td>
          <td>${esc(row.status)}</td>
          <td class="num">${(row.ms / 1000).toFixed(2)}초</td>
          <td class="num">${guess.exact ? '' : '≈'}${guess.seconds.toFixed(1)}초</td>
          <td class="num">${row.weight}%</td><td>${esc(row.note)}</td></tr>`;
      }).join('')}</tbody></table></div>
      <p class="hint">예상 시간 출처: ${esc(estimate(state.workstream, state.plan.states[0]).source)} ·
        ${esc(state.plan.baseline?.note || '')}</p>`;
  }

  // ── 근거 드릴다운 패널 ────────────────────────────────────
  function bindDrill(root) {
    root.querySelectorAll('[data-evidence-id]').forEach((el) => {
      el.addEventListener('click', () => openDrill(el.dataset.evidenceId));
    });
  }

  /** D- → CALC- → E- 사슬을 펼친다 (명세 §7.4). 팩이 브라우저에 있어 왕복이 없다. */
  function openDrill(id) {
    const pack = state.pack || {};
    const panel = $('drill');
    const body = $('drillBody');
    $('drillId').textContent = id;
    panel.hidden = false;

    const data = (pack.C2_data || []).find((r) => r.id === id);
    const evidence = (pack.C1_evidence || []).find((r) => r.id === id);
    const calc = ((pack.logs || {}).calculations || []).find((r) => r.id === id);

    if (data) body.innerHTML = chainOfData(pack, data);
    else if (evidence) body.innerHTML = `<ul class="chain">${evidenceNode(evidence)}</ul>${usedByEvidence(pack, evidence.id)}`;
    else if (calc) body.innerHTML = `<ul class="chain">${calcNode(pack, calc)}</ul>`;
    else body.innerHTML = `<p class="drill-empty">${esc(id)} 를 장부에서 찾지 못했다.</p>`;
  }

  function chainOfData(pack, row) {
    const calcs = ((pack.logs || {}).calculations || [])
      .filter((c) => (c.inputs || []).includes(row.id) || c.result_data_id === row.id);
    const evidence = (pack.C1_evidence || []).find((e) => e.id === row.evidence_id);
    const nodes = [`<li>
      <div class="c-kind">데이터</div><div class="c-id">${esc(row.id)}</div>
      <div class="c-main">${esc(row.metric)} <b>${typeof row.value === 'number' ? App.num(row.value, 2) : esc(row.value)}</b> ${esc(row.unit)}</div>
      <div class="c-sub">${esc(row.period)} · 기준 ${esc(row.basis) || '—'} · ${esc(row.actual_or_estimate)}
        ${row.transform ? ` · 변환 ${esc(row.transform)}` : ''}</div></li>`];

    nodes.push(calcs.length
      ? calcs.map((c) => calcNode(pack, c)).join('')
      : `<li><div class="c-kind">계산</div>
           <div class="c-sub">이 값을 쓴 계산 기록이 없다 — 원자료를 그대로 실은 값이다</div></li>`);

    // 파생값은 자기 E- 가 없다 — 계산에서 나왔으니 당연하다. 입력을 타고 내려가
    // **닿는 E- 를 대신 펼친다.** 이게 없으면 사슬이 실제로는 이어져 있는데도
    // "근거가 없다" 로 읽혀서, 우리가 만든 값을 근거 없는 값으로 오해하게 된다.
    if (evidence) {
      nodes.push(evidenceNode(evidence));
    } else if (row.calc_id) {
      const roots = rootEvidence(pack, row);
      nodes.push(`<li><div class="c-kind">출처</div>
        <div class="c-sub">이 값은 <b>계산으로 만든 파생값</b>이라 직접 근거(E-)가 없다.
          입력을 따라가면 아래 근거에 닿는다 ${roots.length ? '' : '— 그런데 닿는 근거가 없다. 확인이 필요하다'}</div></li>`);
      roots.forEach((e) => nodes.push(evidenceNode(e)));
    } else {
      nodes.push(`<li><div class="c-kind">출처</div>
        <div class="c-sub">E- 도 계산 기록도 붙어 있지 않다. 근거 없는 수치는 리포트에 쓰지 않는 것이 원칙이라 이 값은 확인이 필요하다</div></li>`);
    }
    return `<ul class="chain">${nodes.join('')}</ul>`;
  }

  /** 파생 D- 에서 입력을 타고 내려가 닿는 E- 들 (서버 `ledger.trace` 와 같은 규칙 · 깊이 4). */
  function rootEvidence(pack, row) {
    const seen = new Set([row.id]);
    const found = [];
    let frontier = ((pack.logs || {}).calculations || [])
      .filter((c) => c.result_data_id === row.id)
      .flatMap((c) => (c.inputs || []).map((id) => (pack.C2_data || []).find((d) => d.id === id)))
      .filter(Boolean);
    for (let depth = 0; depth < 4 && frontier.length; depth += 1) {
      const next = [];
      for (const item of frontier) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        const origin = (pack.C1_evidence || []).find((e) => e.id === item.evidence_id);
        if (origin) { if (!found.includes(origin)) found.push(origin); continue; }
        const deeper = ((pack.logs || {}).calculations || [])
          .find((c) => c.result_data_id === item.id);
        for (const id of (deeper || {}).inputs || []) {
          const more = (pack.C2_data || []).find((d) => d.id === id);
          if (more) next.push(more);
        }
      }
      frontier = next;
    }
    return found.slice(0, 4);                           // 같은 출처가 수십 개 D- 를 낳는다
  }

  function calcNode(pack, calc) {
    const inputs = (calc.inputs || []).map((inputId) => {
      const row = (pack.C2_data || []).find((d) => d.id === inputId);
      return row
        ? `<div class="c-sub">${esc(row.id)} ${esc(row.metric)} ${typeof row.value === 'number' ? App.num(row.value, 2) : esc(row.value)} ${esc(row.unit)}</div>`
        : `<div class="c-sub">${esc(inputId)}</div>`;
    }).join('');
    return `<li>
      <div class="c-kind">계산</div><div class="c-id">${esc(calc.id)}</div>
      <div class="c-main"><code>${esc(calc.formula)}</code></div>
      <div class="c-sub">결과 ${typeof calc.result === 'number' ? App.num(calc.result, 3) : esc(calc.result)} ${esc(calc.unit)}
        ${calc.rechecked ? ' · 독립 재계산 완료 (§8.3)' : ''}</div>
      ${calc.assumption ? `<div class="c-sub">가정: ${esc(calc.assumption)}</div>` : ''}
      ${inputs || '<div class="c-sub">입력 D- 가 기록되지 않았다</div>'}</li>`;
  }

  function evidenceNode(row) {
    return `<li>
      <div class="c-kind">출처</div><div class="c-id">${esc(row.id)}</div>
      <div class="c-main">${esc(row.claim)}</div>
      <div class="c-sub">${esc(row.source)}${row.location ? ` · ${esc(row.location)}` : ''}</div>
      <div class="c-sub">등급 <b>${esc(row.grade)}</b> — ${esc(row.grade_reason || row.reason)}</div>
      <div class="c-sub">${esc(row.directness)} · 발행 ${esc(row.published) || '—'} · 수집 ${esc(row.collected)}</div>
      <div class="c-sub">신뢰도 ${esc(row.confidence)}${row.url ? ` · <a href="${esc(row.url)}" target="_blank" rel="noopener">원문</a>` : ''}</div></li>`;
  }

  function usedByEvidence(pack, evidenceId) {
    const rows = (pack.C2_data || []).filter((d) => d.evidence_id === evidenceId);
    if (!rows.length) return '<p class="drill-empty">이 근거에서 나온 D- 가 없다.</p>';
    return `<p class="hint" style="margin-top:12px">이 근거에서 나온 데이터 ${rows.length}건</p>` +
      `<ul class="chain">${rows.slice(0, 30).map((row) => `<li>
        <div class="c-id">${esc(row.id)}</div>
        <div class="c-main">${esc(row.metric)} ${typeof row.value === 'number' ? App.num(row.value, 2) : esc(row.value)} ${esc(row.unit)}</div>
      </li>`).join('')}</ul>`;
  }

  // ── 용어 툴팁 ────────────────────────────────────────────
  async function loadTerms() {
    try {
      const data = await App.get('/api/research/glossary?terms=true');
      // 사전 표기는 `정보 비대칭 (情報 非對稱) / 역선택 (逆選擇)` 처럼 여러 낱말이 붙어 있다.
      // 화면에서 찾으려면 낱말 단위로 쪼개야 한다 (뜻은 서버가 부분일치로 찾아 준다).
      const words = new Set();
      for (const term of data.terms || []) {
        for (const piece of String(term).split('/')) {
          const word = piece.replace(/[(（][^)）]*[)）]/g, '').trim();
          if (word.length >= (data.min_length || 2)) words.add(word);
        }
      }
      state.terms = [...words].sort((a, b) => b.length - a.length);
    } catch { state.terms = []; }
  }

  function bindTips(root) {
    const tip = $('termTip');
    root.querySelectorAll('.term').forEach((el) => {
      el.addEventListener('mouseenter', async () => {
        const term = el.dataset.term;
        const box = el.getBoundingClientRect();
        tip.hidden = false;
        tip.style.left = `${Math.min(box.left, window.innerWidth - 340)}px`;
        tip.style.top = `${box.bottom + 6}px`;
        tip.innerHTML = `<b>${esc(term)}</b><span class="tip-en">찾는 중…</span>`;

        let found = state.termCache.get(term);
        if (!found) {
          try {
            found = await App.get(`/api/research/glossary?term=${encodeURIComponent(term)}`);
          } catch { found = { found: false, reason: '사전을 부르지 못했다' }; }
          state.termCache.set(term, found);
        }
        if (tip.hidden) return;
        tip.innerHTML = found.found
          ? `<b>${esc(term)}</b>` +
            (found.english ? `<span class="tip-en">${esc(found.english)}</span>` : '') +
            `<div>${esc(found.meaning)}</div>` +
            (found.example ? `<div class="tip-en">예: ${esc(found.example)}</div>` : '') +
            `<span class="tip-src">${esc(found.section)} · ${esc(found.match)}` +
            `${found.ambiguous ? ' · 여러 절에 있는 낱말이다' : ''}</span>`
          : `<b>${esc(term)}</b><div class="tip-en">${esc(found.reason || '사전에 없다')}</div>`;
      });
      el.addEventListener('mouseleave', () => { tip.hidden = true; });
    });
  }

  // ══════════════════════════════════════════════════════════
  // 6. MD 내보내기
  // ══════════════════════════════════════════════════════════
  async function exportMarkdown() {
    const button = $('exportBtn');
    const status = $('exportStatus');
    if (!state.pack) return;
    button.disabled = true;
    status.textContent = '만드는 중…';
    try {
      // H09 가 조립해 팩에 실어 둔 것을 함께 보낸다 — 서버가 다시 조립하면
      // H09 가 본 것과 다른 리포트가 나올 수 있다 (회귀검사 §13 "동기화").
      const made = await App.post('/api/research/export/md', {
        context_pack: state.pack,
        report: state.report || undefined,
      });
      const name = `${state.workstream}_${state.target.code}_${(state.header?.run_id || 'run')}.md`;
      const blob = new Blob([made.markdown], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
      status.textContent = `${made.page_count}장 · ${(made.bytes / 1024).toFixed(1)}KB · ${name}`;
    } catch (error) {
      status.textContent = `내보내지 못했다 — ${error.message}`;
    } finally {
      button.disabled = false;
    }
  }

  // ══════════════════════════════════════════════════════════
  // 시작
  // ══════════════════════════════════════════════════════════
  async function boot() {
    const asked = new URLSearchParams(location.search).get('ws');
    Shell.render((asked || 'CORP-R').toLowerCase());

    // 워밍업과 메타 조회를 **동시에** 보낸다 — 서로 기다릴 이유가 없다
    const [meta] = await Promise.all([
      App.get('/api/research/workstreams'),
      warmUp(),
      loadTerms(),
    ]);
    state.meta = meta;
    $('asOf').value = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });

    renderWorkstreams();
    await selectWorkstream(
      (state.meta.workstreams.find((w) => w.id === (asked || '').toUpperCase()) || {}).id || 'CORP-R');

    $('targetInput').addEventListener('input', (event) => {
      state.target = null;
      paintTargetState();
      searchTarget(event.target.value);
    });
    $('runBtn').addEventListener('click', run);
    $('modalClose').addEventListener('click', closeModal);
    $('modalDone').addEventListener('click', closeModal);
    $('drillClose').addEventListener('click', () => { $('drill').hidden = true; });
    $('exportBtn').addEventListener('click', exportMarkdown);
    document.querySelectorAll('#resultTabs .pill').forEach((el) => {
      el.addEventListener('click', () => { state.tab = el.dataset.tab; renderTab(); });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if (!$('drill').hidden) $('drill').hidden = true;
      else if (!$('modalBack').hidden) closeModal();
    });
  }

  return { boot, state, linkNumbers, buildValueIndex, estimate, recordTiming, openDrill };
})();
