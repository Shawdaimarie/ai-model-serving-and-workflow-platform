const ICONS = {
  overview: '<svg viewBox="0 0 20 20"><rect x="2.7" y="2.7" width="6" height="6" rx="1.2"/><rect x="11.3" y="2.7" width="6" height="6" rx="1.2"/><rect x="2.7" y="11.3" width="6" height="6" rx="1.2"/><rect x="11.3" y="11.3" width="6" height="6" rx="1.2"/></svg>',
  deployments: '<svg viewBox="0 0 20 20"><path d="M3 7.3 10 3l7 4.3v7.1L10 18l-7-3.6V7.3Z"/><path d="m3.4 7.4 6.6 3.8 6.6-3.8M10 11.2V18"/></svg>',
  models: '<svg viewBox="0 0 20 20"><path d="M10 2.5 16.7 6v8L10 17.5 3.3 14V6L10 2.5Z"/><path d="m6.4 8.1 3.6 2 3.6-2M10 10.1v4"/></svg>',
  requests: '<svg viewBox="0 0 20 20"><path d="M4 4.2h12M4 8h8M4 11.8h10M4 15.6h6"/><circle cx="15.5" cy="12.2" r="2.3"/></svg>',
  workflows: '<svg viewBox="0 0 20 20"><rect x="2.5" y="2.8" width="5" height="4.5" rx="1.1"/><rect x="12.5" y="12.7" width="5" height="4.5" rx="1.1"/><rect x="12.5" y="2.8" width="5" height="4.5" rx="1.1"/><path d="M7.5 5h2.6a2.4 2.4 0 0 1 2.4 2.4v5.3"/></svg>',
  approvals: '<svg viewBox="0 0 20 20"><path d="M10 2.7 16.4 5v4.8c0 3.7-2.7 6.1-6.4 7.5-3.7-1.4-6.4-3.8-6.4-7.5V5L10 2.7Z"/><path d="m7 9.8 2 2 4-4"/></svg>',
  health: '<svg viewBox="0 0 20 20"><path d="M2.5 10h3l2-4.4 3.2 8.7 2.1-4.3h4.7"/><circle cx="10" cy="10" r="7.5"/></svg>',
  rate: '<svg viewBox="0 0 20 20"><path d="M3 14.5 7.2 10l3 2.7 6.8-7.2M12.5 5.5H17v4.4"/></svg>',
  activity: '<svg viewBox="0 0 20 20"><path d="M2.5 10h3l2-4.3 3.1 8.5 2.1-4.2h4.8"/></svg>',
  approval: '<svg viewBox="0 0 20 20"><path d="m10 2.6 6.3 2.3v4.5c0 3.8-2.7 6.4-6.3 8-3.6-1.6-6.3-4.2-6.3-8V4.9L10 2.6Z"/><path d="m7 9.8 2 2 4-4"/></svg>',
  error: '<svg viewBox="0 0 20 20"><path d="M10 3 18 17H2L10 3Z"/><path d="M10 8v4m0 2.2v.1"/></svg>',
  search: '<svg viewBox="0 0 20 20"><circle cx="8.7" cy="8.7" r="5.8"/><path d="m13 13 4.2 4.2"/></svg>',
  info: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5"/><path d="M10 9v4m0-6.2v.1"/></svg>',
  nodes: '<svg viewBox="0 0 20 20"><rect x="2.5" y="2.5" width="6" height="6" rx="1.1"/><rect x="11.5" y="11.5" width="6" height="6" rx="1.1"/><path d="M8.5 5.5h2a1 1 0 0 1 1 1v5"/></svg>'
};
document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = ICONS[el.dataset.icon] || '');

const TITLES = {
  overview: ['Control plane', 'A clear view of your models, workloads, and the systems behind them.'],
  deployments: ['Deployments', 'Manage model endpoints, capacity limits, and pinned runtime configurations.'],
  models: ['Models & runtimes', 'Inspect model versions and the adapter configuration serving them.'],
  requests: ['Active requests', 'Follow queued and running inference, streamed output, and request outcomes.'],
  workflows: ['Workflow runs', 'Inspect durable runs, committed steps, and the versioned graphs behind them.'],
  approvals: ['Human approvals', 'Review model generated work before a permitted tool can make a change.'],
  health: ['System health', 'Measured control plane pressure and the telemetry currently available from runtimes.']
};
const state = { page: 'overview', overview: null, deployments: [], models: [], requests: [], workflows: [], approvals: [], health: null, events: [], source: null };
const modalRoot = document.getElementById('modal-root');

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
function fmtNum(value, digits = 0) {
  const n = Number(value || 0);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}
function fmtTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function ago(value) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}
function statusPill(status) {
  const label = String(status || 'unknown').replaceAll('_', ' ');
  return `<span class="status-pill ${esc(status)}">${esc(label)}</span>`;
}
async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  let data = {};
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function toast(message, isError = false) {
  const node = document.createElement('div');
  node.className = `toast${isError ? ' error' : ''}`;
  node.innerHTML = `<span class="toast-mark"></span><span>${esc(message)}</span>`;
  document.getElementById('toast-region').appendChild(node);
  window.setTimeout(() => node.remove(), 3500);
}

async function refresh() {
  try {
    const [overview, deployments, models, requests, workflows, approvals, health, events] = await Promise.all([
      api('/api/overview'), api('/api/deployments'), api('/api/models'), api('/api/requests'),
      api('/api/workflows'), api('/api/approvals'), api('/api/health'), api('/api/events')
    ]);
    Object.assign(state, { overview, deployments: deployments.deployments || [], models: models.models || [],
      requests: requests.requests || [], workflows: workflows.runs || [], approvals: approvals.approvals || [], health, events: events.events || [] });
    document.getElementById('queue-count').textContent = overview.queued || 0;
    document.getElementById('approval-count').textContent = state.approvals.length;
    document.getElementById('sidebar-runtime').textContent = overview.backend === 'demo' ? 'DEMO RUNTIME' : 'API CONFIGURED';
    document.querySelector('.callout-orb').style.background = overview.backend === 'demo' ? '#d4a248' : '#3ec5a1';
    const focused = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (!focused && modalRoot.childElementCount === 0) renderPage();
  } catch (error) {
    if (!state.health) {
      document.getElementById('page-content').innerHTML = `<div class="error-panel">The control plane API could not be reached. Start the service with <code>python3 server.py</code> and refresh this page.<br><br>${esc(error.message)}</div>`;
    }
  }
}

function setPage(page) {
  if (!TITLES[page]) return;
  state.page = page;
  document.querySelectorAll('.nav-link').forEach(button => button.classList.toggle('active', button.dataset.page === page));
  document.getElementById('crumb-current').textContent = page === 'overview' ? 'Overview' : TITLES[page][0];
  document.getElementById('page-title').textContent = TITLES[page][0];
  document.getElementById('page-description').textContent = TITLES[page][1];
  document.getElementById('page-eyebrow').textContent = page === 'overview' ? 'OPERATIONS / LIVE' : `PLATFORM / ${page.replaceAll('-', ' ').toUpperCase()}`;
  renderPage();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function button(label, action, kind = '') {
  return `<button class="btn ${kind}" data-action="${esc(action)}">${label}</button>`;
}
function pageActions() {
  const slot = document.getElementById('heading-actions');
  if (state.page === 'overview') slot.innerHTML = `${button(`${ICONS.nodes} New workflow`, 'new-workflow')}${button('+ Inference request', 'new-inference', 'primary')}`;
  else if (state.page === 'requests') slot.innerHTML = button('+ New request', 'new-inference', 'primary');
  else if (state.page === 'workflows') slot.innerHTML = button('+ Start workflow', 'new-workflow', 'primary');
  else if (state.page === 'deployments') slot.innerHTML = button('Runtime configuration', 'go-models');
  else slot.innerHTML = '';
}
function renderPage() {
  pageActions();
  const target = document.getElementById('page-content');
  const renderers = { overview: renderOverview, deployments: renderDeployments, models: renderModels, requests: renderRequests,
    workflows: renderWorkflows, approvals: renderApprovals, health: renderHealth };
  target.innerHTML = renderers[state.page]();
}
function statCard(label, value, unit, note, icon, tint = '', tag = 'measured') {
  return `<article class="card stat-card"><div class="stat-head"><span>${esc(label)}</span><span class="stat-symbol ${tint}">${ICONS[icon]}</span></div><div class="stat-value">${esc(value)}<span class="stat-unit">${esc(unit || '')}</span></div><div class="stat-foot"><span>${esc(note)}</span><span class="measure-tag">${esc(tag)}</span></div></article>`;
}
function requestRows(rows, limit = 5) {
  const data = rows.slice(0, limit);
  if (!data.length) return `<tr><td colspan="6" class="empty-row">No requests yet. Submit an inference request to see live activity.</td></tr>`;
  return data.map(r => `<tr data-click="request" data-id="${esc(r.id)}"><td class="id-cell">${esc(r.id.slice(0, 8))}…</td><td><span class="model-cell"><span class="mini-model">AI</span>${esc(r.model)}</span></td><td>${statusPill(r.status)}</td><td>${r.tokens == null ? '—' : fmtNum(r.tokens)}</td><td>${fmtNum(r.queueMs, 1)} ms</td><td>${esc(ago(r.createdAt))}</td></tr>`).join('');
}
function requestsTable(rows, limit = 5) {
  return `<div class="table-scroll"><table><thead><tr><th>REQUEST ID</th><th>MODEL</th><th>STATUS</th><th>TOKENS</th><th>QUEUE</th><th>AGE</th></tr></thead><tbody>${requestRows(rows, limit)}</tbody></table></div>`;
}
function renderOverview() {
  const o = state.overview || {}, h = state.health || {}, counts = o.requests || {};
  const queuePct = Math.min(100, Number(h.queue?.pressurePercent || 0));
  const rateAvailable = Boolean(o.tokenUsageAvailable);
  const rate = Number(o.tokensPerSecond5m || 0);
  const model = state.models[0] || {};
  return `<section class="grid stats-grid">
    ${statCard('Generation rate', rateAvailable ? fmtNum(rate, 2) : '—', rateAvailable ? 'tok/s' : '', rateAvailable ? 'Rolling 5 minute window' : 'Backend usage unavailable', 'rate', '', rateAvailable ? 'measured' : 'unavailable')}
    ${statCard('Active requests', fmtNum(o.active), ` / ${fmtNum(o.activeLimit)}`, 'Currently dispatched', 'activity', 'mint')}
    ${statCard('Awaiting approval', fmtNum(h.workflowApprovalsPending), 'runs', 'Human review required', 'approval', 'amber')}
    ${statCard('Failed requests', fmtNum(h.failedRequestsLastHour), 'last hour', 'Resubmit after reviewing error', 'error', 'red')}
  </section>
  <section class="grid overview-grid">
    <article class="card"><div class="card-heading"><div><h2 class="card-title">Serving performance</h2><p class="card-subtitle">Observed from completed requests · rolling window</p></div><span class="range-label">◷ &nbsp; LAST 5 MIN</span></div>
      <div class="chart-area"><div class="chart-empty"><div class="empty-note">${ICONS.info}<span>Time series history is not collected by this reference service.</span></div></div>
        <div class="metric-aside"><small>Generation rate · rolling 5 min</small><strong>${rateAvailable ? fmtNum(rate, 2) : '—'} <span class="stat-unit">${rateAvailable ? 'tok/s' : ''}</span></strong><div class="meter-label"><span>Queue occupancy</span><b>${fmtNum(queuePct, 1)}%</b></div><div class="meter"><span style="width:${queuePct}%"></span></div></div></div>
      <div class="chart-foot"><span>${rateAvailable ? 'Rate uses backend reported output tokens / generation time.' : 'Backend did not report completion token usage for this window.'}</span><span>${fmtNum(counts.completed || 0)} completed</span></div>
    </article>
    <article class="card runtime-card"><div class="card-heading"><div><h2 class="card-title">Primary deployment</h2><p class="card-subtitle">Pinned model and runtime configuration</p></div>${statusPill(state.deployments[0]?.status || 'configured')}</div>
      <div class="runtime-status"><span class="callout-orb"></span>${o.backend === 'demo' ? 'Deterministic demo runtime' : 'Inference API configured'}<small>${esc(String(o.backend || '').toUpperCase())}</small></div>
      <div class="runtime-model"><span class="model-mark">AI</span><div><strong>${esc(model.id || o.model || 'No model configured')}</strong><small>${esc(model.backend || o.backend || '—')} · pinned revision</small></div><span class="version-pill">${esc(model.revision || o.modelRevision || '—')}</span></div>
      <div class="runtime-details"><div><span class="detail-label">ACTIVE SLOTS</span><span class="detail-value">up to ${fmtNum(o.activeLimit)} concurrent</span></div><div><span class="detail-label">QUEUE CAPACITY</span><span class="detail-value">${fmtNum(o.queueLimit)} requests</span></div></div>
      <button class="card-bottom-link" data-action="go-models"><span>Inspect runtime configuration</span><span>→</span></button>
    </article>
  </section>
  <section class="grid lower-grid">
    <article class="card table-card"><div class="table-toolbar"><div><h2>Recent requests</h2><span class="table-meta">${fmtNum(state.requests.length)} total</span></div><button class="link-button" data-action="go-requests">View all &nbsp;→</button></div>${requestsTable(state.requests, 5)}</article>
    <article class="card"><div class="table-toolbar"><h2>Control events</h2><span class="table-meta">latest 18</span></div>${renderEventList(state.events)}</article>
  </section>`;
}
function renderEventList(events) {
  if (!events.length) return `<div class="event-list"><div class="event-item"><div class="event-track"><span class="event-dot"></span></div><div class="event-copy"><div class="event-name">No control events yet</div><div class="event-source">Inference and workflow transitions will appear here.</div></div></div></div>`;
  return `<div class="event-list">${events.slice(0, 8).map(e => {
    const kind = e.kind || 'event', failure = /failed|rejected|cancelled/.test(kind), success = /completed|approved/.test(kind);
    return `<div class="event-item"><div class="event-track"><span class="event-dot ${failure ? 'failure' : success ? 'success' : ''}"></span></div><div class="event-copy"><div class="event-topline"><span class="event-name">${esc(kind.replaceAll('_', ' '))}</span><time class="event-time">${esc(fmtTime(e.createdAt))}</time></div><div class="event-source">${esc(e.source.slice(0, 8))} · ${esc(e.data?.stepId || e.data?.message || e.data?.priority !== undefined ? (e.data.stepId || e.data.message || `priority ${e.data.priority}`) : 'control event')}</div></div></div>`;
  }).join('')}</div>`;
}
function renderDeployments() {
  const rows = state.deployments;
  return `<div class="page-grid"><section class="card table-card"><div class="table-toolbar"><div><h2>Model endpoints</h2><span class="table-meta">${rows.length} deployment</span></div><button class="link-button" data-action="go-models">Runtime settings →</button></div><div class="table-scroll"><table><thead><tr><th>DEPLOYMENT</th><th>MODEL REVISION</th><th>BACKEND</th><th>STATUS</th><th>CAPACITY</th><th>ENDPOINT</th></tr></thead><tbody>${rows.length ? rows.map(d => `<tr><td><span class="deployment-cell"><span class="deployment-logo">AI</span><span><strong>${esc(d.name)}</strong><small>${esc(d.id)}</small></span></span></td><td><span class="detail-value">${esc(d.model)}@${esc(d.revision)}</span></td><td><span class="detail-value">${esc(d.backend)}</span></td><td>${statusPill(d.status)}</td><td><span class="detail-value">${fmtNum(d.activeLimit)} slots · ${fmtNum(d.queueLimit)} queue</span></td><td><code class="id-cell">${esc(d.endpoint)}</code></td></tr>`).join('') : `<tr><td colspan="6" class="empty-row">No deployments configured.</td></tr>`}</tbody></table></div></section>
    <aside class="card side-card"><h3>Deployment controls</h3><p>The first cut exposes one pinned model deployment and a bounded request queue. Advanced device and quantization values remain owned by the runtime serving API.</p><div style="margin-top:14px"><div class="side-kv"><span>Concurrency limit</span><strong>${fmtNum(state.overview?.activeLimit)}</strong></div><div class="side-kv"><span>Request queue cap</span><strong>${fmtNum(state.overview?.queueLimit)}</strong></div><div class="side-kv"><span>Tenant quota</span><strong>${fmtNum(state.overview?.activeLimit ? Math.min(4, state.overview.activeLimit) : 4)} active</strong></div><div class="side-kv"><span>Configuration</span><strong>version pinned</strong></div></div></aside></div>`;
}
function renderModels() {
  const model = state.models[0] || {}, h = state.health || {};
  const backendValue = model.backend === 'demo' ? 'Deterministic demo' : model.backend === 'vllm' ? 'vLLM-compatible API' : model.backend === 'llamacpp' ? 'llama.cpp-compatible API' : 'OpenAI-compatible API';
  return `<div class="grid model-config-grid"><section class="card"><div class="config-hero"><span class="model-mark">AI</span><div><h2>${esc(model.id || 'No model')}</h2><p>${esc(backendValue)} &nbsp;·&nbsp; ${esc(model.revision || 'revision unavailable')}</p></div>${statusPill(model.status || 'configured')}</div>
      <div class="config-section"><div class="config-section-title">PINNED CONFIGURATION</div><div class="config-pairs"><div class="config-pair"><small>Model revision</small><strong>${esc(model.revision || '—')}</strong></div><div class="config-pair"><small>Runtime adapter</small><strong>${esc(model.runtime || '—')}</strong></div><div class="config-pair"><small>Execution device</small><strong>Backend managed</strong></div><div class="config-pair"><small>Precision / quantization</small><strong>Backend managed</strong></div><div class="config-pair"><small>Batching owner</small><strong>Inference backend</strong></div><div class="config-pair"><small>Streaming</small><strong>Server sent events</strong></div></div>
      <div class="config-section-title" style="margin-top:15px">RUNTIME ADAPTER</div><div class="config-pairs"><div class="config-pair"><small>Selected backend</small><strong>${esc(model.backend || '—')}</strong></div><div class="config-pair"><small>Transport</small><strong>${model.backend === 'demo' ? 'In process' : 'OpenAI-compatible HTTP'}</strong></div></div></div></section>
    <aside class="card side-card"><h3>Runtime ownership</h3><p>${esc(model.runtime || 'The configured adapter')} streams text output into the platform request lifecycle. Batching, KV cache allocation, device placement, and accelerator memory are managed by the serving backend.</p><div class="info-banner" style="margin-top:14px">${ICONS.info}<span>${esc(h.kvCache?.detail || 'KV cache and GPU telemetry are not exposed by the configured API.')}</span></div><div style="margin-top:11px"><div class="side-kv"><span>GPU utilization</span><strong>Unavailable</strong></div><div class="side-kv"><span>KV block table</span><strong>Backend managed</strong></div><div class="side-kv"><span>Version history</span><strong>Run snapshots</strong></div></div></aside></div>`;
}
function renderRequests() {
  const rows = state.requests;
  return `<section class="card table-card"><div class="table-toolbar"><div><h2>Request history</h2><span class="table-meta">${rows.length} recent</span></div><div class="table-filters"><label class="toolbar-search">${ICONS.search}<input id="request-search" placeholder="Search request or model" value=""></label><select class="select-filter" id="status-filter"><option value="all">All statuses</option><option>queued</option><option>running</option><option>completed</option><option>failed</option><option>cancelled</option></select></div></div><div id="request-table">${requestsTable(rows, 100)}</div></section><div class="subhead"><h2>Request lifecycle</h2><span>Control events and token output are separate streams.</span></div><div class="info-banner">${ICONS.info}<span>Requests keep their model name, revision, sampling settings, priority, and deadline in a per-request configuration snapshot. A cancelled or interrupted inference must be resubmitted; its generation state is not resumable.</span></div>`;
}
function applyRequestFilters() {
  const search = (document.getElementById('request-search')?.value || '').toLowerCase();
  const status = document.getElementById('status-filter')?.value || 'all';
  const rows = state.requests.filter(r => (status === 'all' || r.status === status) && (`${r.id} ${r.model} ${r.tenant}`.toLowerCase().includes(search)));
  const target = document.getElementById('request-table');
  if (target) target.innerHTML = requestsTable(rows, 100);
}
function renderWorkflows() {
  const rows = state.workflows;
  return `<div class="page-grid"><section class="card table-card"><div class="table-toolbar"><div><h2>Workflow runs</h2><span class="table-meta">${rows.length} recent</span></div><button class="link-button" data-action="new-workflow">Start from template →</button></div><div class="table-scroll"><table><thead><tr><th>RUN ID</th><th>WORKFLOW</th><th>VERSION</th><th>STATUS</th><th>STEPS</th><th>UPDATED</th></tr></thead><tbody>${rows.length ? rows.map(w => `<tr data-click="workflow" data-id="${esc(w.id)}"><td class="id-cell">${esc(w.id.slice(0, 8))}…</td><td>${esc(w.name)}</td><td><span class="detail-value">v${esc(w.version)}</span></td><td>${statusPill(w.status)}</td><td>${fmtNum(w.steps)}</td><td>${esc(ago(w.updatedAt))}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-row">No workflow runs yet. Start the draft, validate, and publish workflow.</td></tr>'}</tbody></table></div></section>
    <aside class="card workflow-template"><h3 class="card-title">Available template</h3><p class="card-subtitle">DRAFT, VALIDATE, APPROVE, PUBLISH</p><div class="workflow-flow"><span class="flow-node"><span>✳</span>Draft</span><span class="flow-arrow">→</span><span class="flow-node"><span>✓</span>Validate</span><span class="flow-arrow">↺</span><span class="flow-node"><span>◎</span>Review</span><span class="flow-arrow">→</span><span class="flow-node"><span>↗</span>Save</span></div><div class="workflow-note">Version 1.0.0 · step limit ${fmtNum(state.workflows?.[0]?.graph?.max_steps || 8)} · SQLite checkpoints · approval gate required before the permitted preview tool.</div><button class="btn primary small" style="margin-top:13px;width:100%" data-action="new-workflow">Start workflow</button></aside></div>`;
}
function renderApprovals() {
  const rows = state.approvals;
  if (!rows.length) return `<section class="card" style="padding:32px;text-align:center"><div class="stat-symbol amber" style="margin:0 auto 12px">${ICONS.approval}</div><h2 class="card-title" style="margin-bottom:6px">No approvals waiting</h2><p class="card-subtitle">Workflow runs will pause here until an operator reviews their output.</p><button class="btn primary" style="margin-top:17px" data-action="new-workflow">Start a workflow</button></section>`;
  return `<div class="approval-list">${rows.map(a => `<article class="card approval-card"><div class="approval-card-top"><div><h3>${esc(a.workflow)}</h3><span class="approval-meta">run ${esc(a.runId.slice(0, 8))} · v${esc(a.version)} · awaiting human approval</span></div>${statusPill('pending')}</div><div class="preview-box">${esc(a.preview || 'No draft text is available for review.')}</div><div class="approval-actions"><button class="btn danger small" data-action="reject-approval" data-id="${esc(a.id)}">Reject</button><button class="btn success small" data-action="approve-approval" data-id="${esc(a.id)}">Approve & continue</button></div></article>`).join('')}</div>`;
}
function renderHealth() {
  const h = state.health || {}, q = h.queue || {}, gpu = h.gpu || {}, kv = h.kvCache || {};
  const pressure = Number(q.pressurePercent || 0);
  return `<div class="grid health-grid">
    <article class="card health-card"><div class="health-card-head"><h3>Inference queue</h3><span class="health-icon">${ICONS.activity}</span></div><div class="health-number">${fmtNum(q.queued)}<span class="stat-unit">/ ${fmtNum(q.capacity)} queued</span></div><div class="health-note">Pressure is measured from requests waiting in the platform admission queue.</div><div class="meter health-meter"><span style="width:${Math.min(100,pressure)}%"></span></div><div class="health-footer"><span>Active dispatch slots</span><strong>${fmtNum(q.active)} / ${fmtNum(q.activeCapacity)}</strong></div></article>
    <article class="card health-card"><div class="health-card-head"><h3>Accelerator telemetry</h3><span class="health-icon">${ICONS.health}</span></div><div class="health-number unavailable-value">${esc(gpu.status || 'unavailable')}</div><div class="health-note">${esc(gpu.detail || 'GPU utilization is not reported by the current backend adapter.')}</div><div class="health-footer"><span>GPU utilization</span><strong>Unavailable</strong></div></article>
    <article class="card health-card"><div class="health-card-head"><h3>KV cache manager</h3><span class="health-icon">${ICONS.nodes}</span></div><div class="health-number unavailable-value">${esc(kv.status || 'backend managed')}</div><div class="health-note">${esc(kv.detail || 'KV cache metrics are not reported by the current backend adapter.')}</div><div class="health-footer"><span>Block fragmentation</span><strong>Unavailable</strong></div></article>
    <article class="card health-card"><div class="health-card-head"><h3>Workflow engine</h3><span class="health-icon">${ICONS.workflows}</span></div><div class="health-number">${fmtNum(h.workflowApprovalsPending)}<span class="stat-unit">awaiting approval</span></div><div class="health-note">Run state and step outputs are checkpointed transactionally to local SQLite.</div><div class="health-footer"><span>Storage</span><strong>${esc(h.storage?.engine || '—')} · WAL</strong></div></article>
    <article class="card health-card"><div class="health-card-head"><h3>Request errors</h3><span class="health-icon">${ICONS.error}</span></div><div class="health-number">${fmtNum(h.failedRequestsLastHour)}<span class="stat-unit">last hour</span></div><div class="health-note">Failed requests include a short reason and an action to review or resubmit.</div><div class="health-footer"><span>Window</span><strong>Rolling 60 minutes</strong></div></article>
    <article class="card health-card"><div class="health-card-head"><h3>Model runtime</h3><span class="health-icon">${ICONS.models}</span></div><div class="health-number unavailable-value">${esc(state.overview?.backend === 'demo' ? 'demo mode' : 'API configured')}</div><div class="health-note">${esc(h.runtime || 'Runtime adapter')} · backend health checks are not exposed on the compatibility endpoint.</div><div class="health-footer"><span>Pinned revision</span><strong>${esc(state.overview?.modelRevision || '—')}</strong></div></article>
  </div><div class="subhead"><h2>Measurement scope</h2></div><div class="info-banner">${ICONS.info}<span>Queue depth, active slots, local request timings, completed-token throughput, and workflow approval counts are measured by this process. GPU utilization, accelerator memory, KV blocks, prefix sharing, and fragmentation need a backend metrics adapter; no synthetic values are shown.</span></div>`;
}

function showModal(title, body, footer = '', wide = false) {
  modalRoot.innerHTML = `<div class="modal-backdrop" data-action="backdrop-close"><section class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header class="modal-head"><h2>${esc(title)}</h2><button class="modal-close" data-action="close-modal" aria-label="Close">×</button></header><div class="modal-body">${body}</div>${footer ? `<footer class="modal-foot">${footer}</footer>` : ''}</section></div>`;
}
function closeModal() {
  if (state.source) { state.source.close(); state.source = null; }
  modalRoot.innerHTML = '';
}
function newInferenceModal() {
  const body = `<form id="inference-form"><div class="form-field"><label for="prompt">Prompt</label><textarea class="form-control" id="prompt" name="prompt" required maxlength="20000" placeholder="Ask the pinned model to generate a response…"></textarea><small>Up to 20,000 characters. The exact model revision and request settings are saved with the run.</small></div><div class="form-row"><div class="form-field"><label for="tenant">Tenant</label><input class="form-control" id="tenant" name="tenant" value="default" maxlength="64"></div><div class="form-field"><label for="priority">Priority</label><select class="form-control" id="priority" name="priority"><option value="1">Normal · 1</option><option value="2">Elevated · 2</option><option value="3">High · 3</option><option value="0">Background · 0</option></select></div></div><div class="form-row"><div class="form-field"><label for="max-tokens">Maximum output tokens</label><input class="form-control" id="max-tokens" name="max_tokens" type="number" min="1" max="4096" value="128"></div><div class="form-field"><label for="deadline">Deadline</label><select class="form-control" id="deadline" name="deadline_seconds"><option value="30">30 seconds</option><option value="120" selected>2 minutes</option><option value="600">10 minutes</option></select></div></div></form>`;
  showModal('New inference request', body, `<span class="foot-note">Queued requests can be cancelled before dispatch.</span><button class="btn primary" form="inference-form" type="submit">Submit request</button>`);
}
function newWorkflowModal() {
  const body = `<form id="workflow-form"><div class="form-field"><label>Workflow template</label><div class="config-pair"><strong>Draft, validate, and publish · v1.0.0</strong></div><small>Model draft → deterministic validation ↺ refinement → human approval → permission-checked preview tool.</small></div><div class="form-field"><label for="workflow-prompt">Prompt</label><textarea class="form-control" id="workflow-prompt" name="prompt" maxlength="20000" required placeholder="Describe the content to draft and submit for review…"></textarea></div><div class="info-banner">${ICONS.info}<span>Runs are checkpointed after each step. Tool permission <code>artifact.preview.write</code> is enforced by the local registry and requires approval before execution.</span></div></form>`;
  showModal('Start workflow', body, `<span class="foot-note">State persists in the local SQLite database.</span><button class="btn primary" form="workflow-form" type="submit">Start run</button>`);
}
async function watchRequest(id) {
  const item = state.requests.find(r => r.id === id);
  const canCancel = item && ['queued', 'running'].includes(item.status);
  const outputCount = item?.tokens == null ? 'usage unavailable' : `${fmtNum(item.tokens)} output tokens`;
  const failure = item?.error ? `<div class="info-banner" style="margin-bottom:13px;border-color:#f0d7d9;color:#a05158;background:#fff7f7">${ICONS.error}<span>${esc(item.error)} · This generation cannot resume. Review the backend and resubmit.</span></div>` : '';
  showModal(`Inference · ${id.slice(0, 8)}`, `${failure}<div class="config-pairs" style="margin-bottom:13px"><div class="config-pair"><small>Model revision</small><strong>${esc(item ? `${item.model}@${item.modelRevision}` : 'Loading…')}</strong></div><div class="config-pair"><small>Status</small><strong id="stream-status">${esc(item?.status || 'connecting')}</strong></div></div><div class="stream-section"><h3>Generated output <span id="stream-token-count">${esc(outputCount)}</span></h3><div class="token-output" id="token-output"><span style="color:#9da7b5">${item?.output ? 'Loading stored stream events…' : 'Waiting for model output…'}</span></div></div><div class="stream-section"><h3>Control events <span>separate from token stream</span></h3><div class="control-events" id="control-events"><div class="control-event"><time>now</time><strong>connecting</strong><span>Opening request event stream</span></div></div></div>`, `<span class="foot-note">${canCancel ? 'Cancellation is cooperative while the backend stream is active.' : 'Generation cannot resume after cancellation or failure.'}</span>${canCancel ? `<button class="btn danger small" data-action="cancel-request" data-id="${esc(id)}">Cancel request</button>` : '<button class="btn small" data-action="close-modal">Close</button>'}`, true);
  if (state.source) state.source.close();
  const source = new EventSource(`/api/requests/${encodeURIComponent(id)}/events`);
  state.source = source;
  const output = document.getElementById('token-output');
  const eventLog = document.getElementById('control-events');
  const status = document.getElementById('stream-status');
  const count = document.getElementById('stream-token-count');
  const pushEvent = (name, event) => {
    let data = {};
    try { data = JSON.parse(event.data); } catch (_) {}
    if (name === 'token') {
      if (output.querySelector('span')) output.textContent = '';
      output.textContent += data.data?.text || '';
      count.textContent = `${output.textContent.length} characters streamed`;
      output.scrollTop = output.scrollHeight;
      return;
    }
    const control = data.data || {};
    const line = document.createElement('div');
    line.className = 'control-event';
    line.innerHTML = `<time>${esc(fmtTime(data.createdAt))}</time><strong>${esc(name)}</strong><span>${esc(control.message || control.action || control.durationMs ? (control.message || control.action || `${control.durationMs} ms`) : JSON.stringify(control))}</span>`;
    eventLog.appendChild(line);
    if (['running', 'queued', 'completed', 'failed', 'cancelled'].includes(name)) status.textContent = name;
    if (name === 'completed') count.textContent = control.usageAvailable ? `${fmtNum(control.tokens)} output tokens` : 'Backend token usage unavailable';
    if (['completed', 'failed', 'cancelled'].includes(name)) {
      source.close();
      window.setTimeout(refresh, 100);
    }
  };
  ['queued', 'running', 'completed', 'failed', 'cancelled', 'token'].forEach(name => source.addEventListener(name, event => pushEvent(name, event)));
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) return;
    if (status && status.textContent === 'connecting') status.textContent = 'reconnecting';
  };
}
async function showWorkflow(id) {
  showModal(`Workflow run · ${id.slice(0, 8)}`, '<div class="loading-line"></div>', '<span class="foot-note">Loading run history…</span><button class="btn small" data-action="close-modal">Close</button>', true);
  try {
    const run = await api(`/api/workflows/${encodeURIComponent(id)}`);
    if (!modalRoot.childElementCount) return;
    const steps = run.graph.nodes.map((node, index) => {
      const detail = (run.stepDetails || []).find(step => step.id === node.id);
      const stepStatus = detail?.status || (run.currentNode === node.id ? run.status : 'pending');
      return `<div class="workflow-step"><span class="step-marker ${esc(stepStatus)}">${detail ? '✓' : index + 1}</span><div><strong>${esc(node.label)}</strong><small>${esc(node.type)}${node.permission ? ` · ${esc(node.permission)}` : ''}${detail?.error ? ` · ${esc(detail.error)}` : ''}</small></div>${statusPill(stepStatus)}</div>`;
    }).join('');
    const evs = (run.events || []).slice(-8).map(e => `<div class="control-event"><time>${esc(fmtTime(e.createdAt))}</time><strong>${esc(e.kind)}</strong><span>${esc(e.data?.message || e.data?.stepId || e.data?.route || '')}</span></div>`).join('') || '<div class="control-event">No events recorded yet.</div>';
    const resume = run.canResume ? `<button class="btn primary small" data-action="resume-workflow" data-id="${esc(id)}">Resume from checkpoint</button>` : '';
    const footer = `<span class="foot-note">Workflow v${esc(run.version)} · ${fmtNum(run.steps)} committed steps</span>${resume || '<button class="btn" data-action="close-modal">Close</button>'}`;
    document.querySelector('.modal-head h2').textContent = `${run.name} · ${id.slice(0, 8)}`;
    document.querySelector('.modal-body').innerHTML = `<div class="config-pairs"><div class="config-pair"><small>Run status</small><strong>${esc(run.status)}</strong></div><div class="config-pair"><small>Workflow version</small><strong>${esc(run.version)}</strong></div><div class="config-pair"><small>Current node</small><strong>${esc(run.currentNode || 'terminal')}</strong></div><div class="config-pair"><small>Updated</small><strong>${esc(fmtTime(run.updatedAt))}</strong></div></div>${run.error ? `<div class="info-banner" style="margin-top:13px;border-color:#f0d7d9;color:#a05158;background:#fff7f7">${ICONS.error}<span>${esc(run.error)}${run.canResume ? ' · Resume is available from the last committed step.' : ''}</span></div>` : ''}<div class="workflow-steps">${steps}</div><div class="stream-section"><h3>Structured events</h3><div class="control-events">${evs}</div></div><details class="json-details"><summary>Versioned input and runtime snapshot</summary><pre>${esc(JSON.stringify({ input: run.input, configuration: run.configuration }, null, 2))}</pre></details>`;
    document.querySelector('.modal-foot').innerHTML = footer;
  } catch (error) { document.querySelector('.modal-body').innerHTML = `<div class="error-panel">${esc(error.message)}</div>`; }
}

document.body.addEventListener('click', async event => {
  const nav = event.target.closest('.nav-link[data-page]');
  if (nav) return setPage(nav.dataset.page);
  const row = event.target.closest('[data-click]');
  if (row) {
    if (row.dataset.click === 'request') return watchRequest(row.dataset.id);
    if (row.dataset.click === 'workflow') return showWorkflow(row.dataset.id);
  }
  const control = event.target.closest('[data-action]');
  if (!control) return;
  const action = control.dataset.action;
  if (action === 'backdrop-close' && event.target === control) return closeModal();
  if (action === 'close-modal') return closeModal();
  if (action === 'new-inference') return newInferenceModal();
  if (action === 'new-workflow') return newWorkflowModal();
  if (action === 'go-models') return setPage('models');
  if (action === 'go-requests') return setPage('requests');
  if (action === 'cancel-request') {
    try { await api(`/api/requests/${encodeURIComponent(control.dataset.id)}/cancel`, { method: 'POST', body: '{}' }); toast('Cancellation requested'); }
    catch (error) { toast(error.message, true); }
    return refresh();
  }
  if (action === 'approve-approval' || action === 'reject-approval') {
    let note = '';
    if (action === 'reject-approval') {
      const entered = prompt('Optional note for the workflow record:');
      if (entered === null) return;
      note = entered;
    }
    try {
      const decision = action === 'approve-approval' ? 'approve' : 'reject';
      await api(`/api/approvals/${encodeURIComponent(control.dataset.id)}/${decision}`, { method: 'POST', body: JSON.stringify({ note }) });
      toast(decision === 'approve' ? 'Approved; workflow will continue' : 'Approval rejected');
      refresh();
    } catch (error) { toast(error.message, true); }
    return;
  }
  if (action === 'resume-workflow') {
    try { await api(`/api/workflows/${encodeURIComponent(control.dataset.id)}/resume`, { method: 'POST', body: '{}' }); toast('Workflow resumed from its checkpoint'); closeModal(); refresh(); }
    catch (error) { toast(error.message, true); }
  }
});
document.body.addEventListener('submit', async event => {
  if (event.target.id === 'inference-form') {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = { prompt: form.get('prompt'), tenant: form.get('tenant'), priority: Number(form.get('priority')),
      max_tokens: Number(form.get('max_tokens')), deadline_seconds: Number(form.get('deadline_seconds')) };
    try {
      const result = await api('/api/inference', { method: 'POST', body: JSON.stringify(payload) });
      closeModal(); toast('Inference request queued'); await refresh(); setPage('requests'); watchRequest(result.id);
    } catch (error) { toast(error.message, true); }
  }
  if (event.target.id === 'workflow-form') {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      const result = await api('/api/workflows/start', { method: 'POST', body: JSON.stringify({ prompt: form.get('prompt') }) });
      closeModal(); toast('Workflow run started'); await refresh(); setPage('workflows'); showWorkflow(result.id);
    } catch (error) { toast(error.message, true); }
  }
});
document.body.addEventListener('input', event => { if (event.target.id === 'request-search') applyRequestFilters(); });
document.body.addEventListener('change', event => { if (event.target.id === 'status-filter') applyRequestFilters(); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && modalRoot.childElementCount) closeModal();
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    const search = document.getElementById('request-search');
    if (search) search.focus(); else setPage('requests');
  }
});

refresh().then(() => renderPage());
window.setInterval(refresh, 3000);
