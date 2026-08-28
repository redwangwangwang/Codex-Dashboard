(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const state = {
    view: 'overview',
    overview: null,
    tasks: [],
    settings: null,
    doctor: null,
    selected: null,
    selectedTab: 'timeline',
    query: '',
    refreshTimer: null,
    eventSource: null,
    eventFailures: 0,
    loading: true,
    token: '',
  };

  const viewMeta = {
    overview: ['Workspace pulse', 'Overview'],
    attention: ['Exceptions first', 'Need Attention'],
    table: ['Operational inventory', 'All Tasks'],
    board: ['Flow by state', 'Task Board'],
    completed: ['Verified outcomes', 'Completed'],
    settings: ['Local runtime', 'Settings'],
  };

  const statusLabels = {
    DISCOVERED: 'Discovered',
    RUNNING: 'Running',
    IDLE: 'Idle',
    WAITING_INPUT: 'Waiting input',
    WAITING_APPROVAL: 'Waiting approval',
    PAUSED: 'Paused',
    BLOCKED: 'Blocked',
    COMPLETED: 'Completed',
    FAILED: 'Failed',
    CANCELLED: 'Cancelled',
  };

  const severityRank = { CRITICAL: 4, HIGH: 3, WARNING: 2, INFO: 1 };

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function safeJson(value) {
    try { return JSON.stringify(value ?? {}, null, 2); }
    catch { return String(value ?? ''); }
  }

  function statusClass(status) {
    return `status-${String(status || 'discovered').toLowerCase().replaceAll('_', '-')}`;
  }

  function statusPill(status) {
    const value = String(status || 'DISCOVERED').toUpperCase();
    return `<span class="status-pill ${statusClass(value)}">${escapeHtml(statusLabels[value] || value)}</span>`;
  }

  function severityPill(severity) {
    const value = String(severity || 'INFO').toUpperCase();
    return `<span class="severity-pill severity-${value.toLowerCase()}">${escapeHtml(value)}</span>`;
  }

  function sourcePill(source) {
    return `<span class="source-pill">${escapeHtml(source || 'rollout')}</span>`;
  }

  function relativeTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 0) return 'now';
    if (seconds < 10) return 'just now';
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
  }

  function exactTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(date);
  }

  function compactPath(path) {
    if (!path) return 'No working directory';
    const parts = String(path).replaceAll('\\', '/').split('/').filter(Boolean);
    if (parts.length <= 3) return String(path);
    return `…/${parts.slice(-3).join('/')}`;
  }

  function progressMarkup(task, small = false) {
    if (!task.progress_known || task.progress == null) {
      return `<span class="progress-unknown">Progress unknown</span>`;
    }
    const value = Math.max(0, Math.min(100, Number(task.progress) || 0));
    return `<div class="progress-wrap">
      <div class="progress-label"><span>${small ? 'Plan' : 'Plan progress'}</span><strong>${Math.round(value)}%</strong></div>
      <div class="progress-track"><div class="progress-bar" style="--progress:${value}%"></div></div>
    </div>`;
  }

  function emptyState(title, copy, action = '') {
    return `<div class="empty-state"><div><div class="empty-icon">◇</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(copy)}</p>${action}</div></div>`;
  }

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.status = status;
      this.payload = payload;
    }
  }

  function loadToken() {
    const url = new URL(window.location.href);
    const fromQuery = url.searchParams.get('token');
    state.token = fromQuery || localStorage.getItem('codex-dashboard-token') || '';
    if (fromQuery) {
      localStorage.setItem('codex-dashboard-token', fromQuery);
      url.searchParams.delete('token');
      history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
  }

  async function api(path, options = {}, retryAuth = true) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
    let response;
    try {
      response = await fetch(path, { ...options, headers, cache: 'no-store' });
    } catch (error) {
      setConnection(false, 'API unreachable');
      throw error;
    }
    let payload = null;
    const type = response.headers.get('content-type') || '';
    if (type.includes('application/json')) {
      try { payload = await response.json(); } catch { payload = null; }
    }
    if (response.status === 401 && retryAuth) {
      const token = window.prompt('Dashboard token required');
      if (token) {
        state.token = token.trim();
        localStorage.setItem('codex-dashboard-token', state.token);
        connectEvents(true);
        return api(path, options, false);
      }
    }
    if (!response.ok) {
      const message = payload?.error?.message || `${response.status} ${response.statusText}`;
      throw new ApiError(message, response.status, payload);
    }
    setConnection(true, 'Live local data');
    return payload;
  }

  function setConnection(online, detail) {
    const dot = $('#connectionDot');
    dot.className = `status-dot ${online ? 'online' : 'offline'}`;
    $('#connectionText').textContent = online ? 'Connected' : 'Disconnected';
    $('#connectionDetail').textContent = detail || (online ? 'Live local data' : 'Retrying');
  }

  function toast(title, message = '', type = 'success') {
    const region = $('#toastRegion');
    const item = document.createElement('div');
    item.className = `toast ${type === 'error' ? 'error' : ''}`;
    item.innerHTML = `<div></div><div><strong>${escapeHtml(title)}</strong>${message ? `<span>${escapeHtml(message)}</span>` : ''}</div>`;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), 4200);
  }

  async function loadCore({ quiet = false } = {}) {
    if (!quiet) state.loading = true;
    try {
      const [overview, taskPayload] = await Promise.all([
        api('/api/overview'),
        api(`/api/tasks?limit=1000${state.query ? `&q=${encodeURIComponent(state.query)}` : ''}`),
      ]);
      state.overview = overview;
      state.tasks = taskPayload.items || [];
      state.loading = false;
      updateAttentionBadge();
      render();
      if (state.selected) refreshSelected(true);
    } catch (error) {
      state.loading = false;
      if (!quiet) renderError(error);
    }
  }

  function scheduleRefresh() {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => loadCore({ quiet: true }), 180);
  }

  function connectEvents(force = false) {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    const query = state.token ? `?token=${encodeURIComponent(state.token)}` : '';
    const source = new EventSource(`/api/events${query}`);
    state.eventSource = source;
    source.addEventListener('open', () => {
      state.eventFailures = 0;
      setConnection(true, 'SSE live updates');
    });
    source.addEventListener('refresh', scheduleRefresh);
    source.onerror = () => {
      state.eventFailures += 1;
      setConnection(false, `SSE reconnect ${state.eventFailures}`);
      if (force || state.eventFailures > 4) {
        source.close();
        const delay = Math.min(30000, 1000 * 2 ** Math.min(5, state.eventFailures));
        setTimeout(() => connectEvents(false), delay);
      }
    };
  }

  function updateAttentionBadge() {
    const count = Number(state.overview?.need_attention || 0);
    const badge = $('#attentionBadge');
    badge.textContent = String(count);
    badge.classList.toggle('hidden', count < 1);
  }

  function setView(view) {
    if (!viewMeta[view]) return;
    state.view = view;
    $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
    const [eyebrow, title] = viewMeta[view];
    $('#viewEyebrow').textContent = eyebrow;
    $('#viewTitle').textContent = title;
    history.replaceState(null, '', `#${view}`);
    render();
    $('#main').focus({ preventScroll: true });
  }

  function render() {
    const main = $('#main');
    if (state.loading) {
      main.innerHTML = `<div class="loading-state"><div class="loader"></div><p>Building the operational picture…</p></div>`;
      return;
    }
    if (state.view === 'overview') main.innerHTML = renderOverview();
    else if (state.view === 'attention') main.innerHTML = renderAttention();
    else if (state.view === 'table') main.innerHTML = renderTable();
    else if (state.view === 'board') main.innerHTML = renderBoard();
    else if (state.view === 'completed') main.innerHTML = renderCompleted();
    else if (state.view === 'settings') renderSettings();
  }

  function renderError(error) {
    $('#main').innerHTML = `<div class="page"><div class="panel">${emptyState(
      'Could not load the dashboard',
      error?.message || 'The local API did not return a usable response.',
      '<button class="button button-primary" data-retry>Retry</button>'
    )}</div></div>`;
  }

  function countStatus(...statuses) {
    return state.tasks.filter(task => statuses.includes(task.status)).length;
  }

  function statCard(label, value, meta, className = '') {
    return `<article class="stat-card ${className}"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value">${escapeHtml(value)}</div><div class="stat-meta">${escapeHtml(meta)}</div></article>`;
  }

  function taskRow(task) {
    return `<article class="task-row" data-session-id="${escapeHtml(task.id)}">
      <div class="task-main"><div class="task-title">${escapeHtml(task.title || 'Untitled session')}</div><div class="task-subtitle">${escapeHtml(compactPath(task.repo_root || task.cwd || task.id))}</div></div>
      <div class="task-phase">${escapeHtml(task.attention_reason || task.phase || 'No phase evidence')}</div>
      <div>${progressMarkup(task, true)}</div>
      <div><div>${statusPill(task.status)}</div><div class="task-time" title="${escapeHtml(exactTime(task.updated_at))}">${relativeTime(task.updated_at)}</div></div>
      <div class="chevron">›</div>
    </article>`;
  }

  function alertCard(alert) {
    const warning = alert.severity === 'WARNING' || alert.severity === 'INFO';
    return `<article class="alert-card ${warning ? 'warning' : ''}" data-session-id="${escapeHtml(alert.session_id)}">
      <div class="alert-symbol">${warning ? '!' : '×'}</div>
      <div class="alert-content"><strong>${escapeHtml(alert.title)}</strong><p>${escapeHtml(alert.message || alert.session_status || '')}</p></div>
      <div class="alert-meta">${severityPill(alert.severity)}<div style="margin-top:6px">${relativeTime(alert.last_seen_at)}</div></div>
    </article>`;
  }

  function renderActivity() {
    const days = [];
    const now = new Date();
    for (let offset = 9; offset >= 0; offset -= 1) {
      const date = new Date(now);
      date.setHours(0, 0, 0, 0);
      date.setDate(date.getDate() - offset);
      const next = new Date(date);
      next.setDate(next.getDate() + 1);
      const count = state.tasks.filter(task => {
        const updated = new Date(task.updated_at || 0);
        return updated >= date && updated < next;
      }).length;
      days.push({ date, count });
    }
    const max = Math.max(1, ...days.map(day => day.count));
    return `<div class="activity-chart">${days.map(day => {
      const height = Math.max(7, Math.round(day.count / max * 100));
      const label = new Intl.DateTimeFormat(undefined, { weekday: 'narrow' }).format(day.date);
      return `<div class="activity-column" title="${day.count} updated task(s)" style="--height:${height}%"><span>${escapeHtml(label)}</span></div>`;
    }).join('')}</div>`;
  }

  function renderOverview() {
    const alerts = [...(state.overview?.alerts || [])].sort((a, b) => (severityRank[b.severity] || 0) - (severityRank[a.severity] || 0));
    const recent = state.tasks.slice(0, 8);
    return `<div class="page">
      <section class="stats-grid">
        ${statCard('Need attention', state.overview?.need_attention || 0, 'Open explainable alerts', 'attention')}
        ${statCard('Running', countStatus('RUNNING'), 'Sessions with recent evidence', 'running')}
        ${statCard('Waiting', countStatus('WAITING_INPUT', 'WAITING_APPROVAL', 'PAUSED'), 'Human action or continuation', 'waiting')}
        ${statCard('Blocked / failed', countStatus('BLOCKED', 'FAILED'), 'Investigate evidence', 'attention')}
        ${statCard('Completed', countStatus('COMPLETED'), 'Explicitly confirmed outcomes', 'completed')}
      </section>
      <section class="dashboard-grid">
        <div class="stack">
          <article class="panel">
            <header class="panel-header"><div><h3>Recent work</h3><p>Latest normalized session activity</p></div><button class="panel-link" data-go-view="table">View all</button></header>
            <div class="task-list">${recent.length ? recent.map(taskRow).join('') : emptyState('No sessions discovered', 'Run Codex or create a managed task. The collector will discover rollout history automatically.')}</div>
          </article>
          <article class="panel">
            <header class="panel-header"><div><h3>Activity signal</h3><p>Tasks updated over the last ten days</p></div><span class="source-pill">Evidence-based</span></header>
            <div class="panel-body">${renderActivity()}</div>
          </article>
        </div>
        <div class="stack">
          <article class="panel">
            <header class="panel-header"><div><h3>Need attention</h3><p>Highest-severity open alerts first</p></div><button class="panel-link" data-go-view="attention">Open queue</button></header>
            <div class="panel-body"><div class="alert-list">${alerts.length ? alerts.slice(0, 7).map(alertCard).join('') : emptyState('All clear', 'No open alert currently requires your attention.')}</div></div>
          </article>
          <article class="panel">
            <header class="panel-header"><div><h3>Truth model</h3><p>How this dashboard avoids false confidence</p></div></header>
            <div class="panel-body">
              <div class="evidence-list">
                <div class="evidence-card"><div class="evidence-head"><strong>Completion is explicit</strong><span class="status-pill status-completed">Rule</span></div><div class="evidence-body">A turn ending or process exit never marks a task complete.</div></div>
                <div class="evidence-card"><div class="evidence-head"><strong>Progress is plan-based</strong><span class="status-pill status-idle">Rule</span></div><div class="evidence-body">Without structured plan steps, progress stays Unknown.</div></div>
                <div class="evidence-card"><div class="evidence-head"><strong>Controls are capability-scoped</strong><span class="status-pill status-running">Rule</span></div><div class="evidence-body">Pause and cancel appear only for dashboard-owned processes.</div></div>
              </div>
            </div>
          </article>
        </div>
      </section>
    </div>`;
  }

  function renderAttention() {
    const alerts = [...(state.overview?.alerts || [])].sort((a, b) => {
      const rank = (severityRank[b.severity] || 0) - (severityRank[a.severity] || 0);
      return rank || String(b.last_seen_at).localeCompare(String(a.last_seen_at));
    });
    const taskMap = new Map(state.tasks.map(task => [task.id, task]));
    return `<div class="page">
      <div class="page-header"><div><h2>Human action queue</h2><p>Every row is backed by an input request, approval, failure, stale signal, or repeated test evidence.</p></div><div class="toolbar">${severityPill('CRITICAL')} ${severityPill('HIGH')} ${severityPill('WARNING')}</div></div>
      <article class="panel"><div class="panel-body"><div class="alert-list">${alerts.length ? alerts.map(alert => {
        const task = taskMap.get(alert.session_id);
        return `<article class="alert-card ${['WARNING','INFO'].includes(alert.severity) ? 'warning' : ''}" data-session-id="${escapeHtml(alert.session_id)}">
          <div class="alert-symbol">!</div>
          <div class="alert-content"><strong>${escapeHtml(alert.title)} · ${escapeHtml(task?.title || alert.session_id)}</strong><p>${escapeHtml(alert.message || '')}</p><div style="margin-top:7px">${task ? statusPill(task.status) : ''} ${sourcePill(task?.source || 'event')}</div></div>
          <div class="alert-meta">${severityPill(alert.severity)}<div style="margin-top:7px">Seen ${relativeTime(alert.last_seen_at)}</div><div>Count ${escapeHtml(alert.count || 1)}</div></div>
        </article>`;
      }).join('') : emptyState('No action required', 'No open alert is waiting for intervention.')}</div></div></article>
    </div>`;
  }

  function renderTable() {
    const tasks = state.tasks;
    return `<div class="page">
      <div class="page-header"><div><h2>${tasks.length} normalized session${tasks.length === 1 ? '' : 's'}</h2><p>Dense evidence index across Codex state DB, rollout files, managed processes, and Git.</p></div><div class="toolbar"><select class="filter-select" data-status-filter><option value="">All status</option>${Object.entries(statusLabels).map(([value,label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join('')}</select></div></div>
      <div class="data-table-wrap"><table class="data-table"><thead><tr><th>Task / repository</th><th>Status</th><th>Phase</th><th>Progress</th><th>Alerts</th><th>Files</th><th>Tests</th><th>Updated</th></tr></thead>
      <tbody>${tasks.length ? tasks.map(task => `<tr data-session-id="${escapeHtml(task.id)}">
        <td><div class="table-title">${escapeHtml(task.title || 'Untitled session')}</div><div class="table-sub">${escapeHtml(task.repo_root || task.cwd || task.id)}</div></td>
        <td>${statusPill(task.status)}</td><td>${escapeHtml(task.phase || '—')}</td><td>${progressMarkup(task, true)}</td>
        <td><span class="mini-count">${escapeHtml(task.open_alerts || 0)}</span></td><td><span class="mini-count">${escapeHtml(task.changed_files || 0)}</span></td>
        <td>${task.latest_test_status ? `<span class="status-pill ${task.latest_test_status === 'PASSED' ? 'status-completed' : 'status-failed'}">${escapeHtml(task.latest_test_status)}</span>` : '—'}</td>
        <td title="${escapeHtml(exactTime(task.updated_at))}">${relativeTime(task.updated_at)}</td></tr>`).join('') : `<tr><td colspan="8">${emptyState('No matching tasks', 'Change the search or wait for the collector to find sessions.')}</td></tr>`}</tbody></table></div>
    </div>`;
  }

  function boardGroup(task) {
    if (task.status === 'RUNNING') return 'In progress';
    if (['WAITING_INPUT', 'WAITING_APPROVAL', 'PAUSED'].includes(task.status)) return 'Waiting';
    if (['BLOCKED', 'FAILED'].includes(task.status)) return 'Blocked';
    if (['COMPLETED', 'CANCELLED'].includes(task.status)) return 'Done';
    return 'Backlog';
  }

  function taskCard(task) {
    return `<article class="task-card" data-session-id="${escapeHtml(task.id)}">
      <div style="display:flex;justify-content:space-between;gap:8px">${statusPill(task.status)}${task.open_alerts ? `<span class="task-card-alert">! ${escapeHtml(task.open_alerts)}</span>` : ''}</div>
      <h4>${escapeHtml(task.title || 'Untitled session')}</h4><div class="repo">${escapeHtml(compactPath(task.repo_root || task.cwd))}</div>
      <div style="margin-top:11px">${progressMarkup(task, true)}</div>
      <div class="task-card-footer"><span>${escapeHtml(task.phase || 'No phase')}</span><span>${relativeTime(task.updated_at)}</span></div>
    </article>`;
  }

  function renderBoard() {
    const groups = ['Backlog', 'In progress', 'Waiting', 'Blocked', 'Done'];
    const grouped = Object.fromEntries(groups.map(group => [group, []]));
    state.tasks.forEach(task => grouped[boardGroup(task)].push(task));
    return `<div class="page"><div class="page-header"><div><h2>Evidence state flow</h2><p>Columns reflect observed state, not estimates of what the agent “probably” did.</p></div></div>
      <section class="board">${groups.map(group => `<div class="board-column"><header class="board-header"><strong>${escapeHtml(group)}</strong><span class="board-count">${grouped[group].length}</span></header><div class="board-list">${grouped[group].length ? grouped[group].map(taskCard).join('') : '<div class="empty-state" style="min-height:150px;padding:24px 8px"><div><p>No tasks</p></div></div>'}</div></div>`).join('')}</section>
    </div>`;
  }

  function renderCompleted() {
    const tasks = state.tasks.filter(task => task.status === 'COMPLETED');
    return `<div class="page"><div class="page-header"><div><h2>Explicitly completed work</h2><p>Only sessions with direct completion evidence or a human confirmation appear here.</p></div></div>
      <article class="panel"><div class="task-list">${tasks.length ? tasks.map(taskRow).join('') : emptyState('No confirmed completions', 'A normal Codex exit remains Idle until completion is explicit.')}</div></article></div>`;
  }

  async function renderSettings() {
    $('#main').innerHTML = `<div class="loading-state"><div class="loader"></div><p>Reading local configuration…</p></div>`;
    try {
      const [settings, doctor] = await Promise.all([api('/api/settings'), api('/api/doctor')]);
      state.settings = settings;
      state.doctor = doctor;
      const checks = Object.entries(doctor.checks || {});
      $('#main').innerHTML = `<div class="page"><div class="settings-grid">
        <form class="settings-section" id="settingsForm"><h3>Runtime thresholds</h3><p>These values change inference timing only. Raw events remain immutable and can be re-projected.</p>
          <div class="form-grid">
            <label class="field"><span>Collector interval (seconds)</span><input name="poll_interval" type="number" min="0.1" step="0.1" value="${escapeHtml(settings.poll_interval)}"></label>
            <label class="field"><span>Stale threshold (seconds)</span><input name="stale_seconds" type="number" min="1" step="1" value="${escapeHtml(settings.stale_seconds)}"></label>
            <label class="field"><span>Hung command threshold</span><input name="command_hung_seconds" type="number" min="1" step="1" value="${escapeHtml(settings.command_hung_seconds)}"></label>
            <label class="field"><span>Git refresh interval</span><input name="git_refresh_seconds" type="number" min="1" step="1" value="${escapeHtml(settings.git_refresh_seconds)}"></label>
          </div><div class="dialog-actions"><button class="button button-primary" type="submit">Save thresholds</button></div>
        </form>
        <div class="stack">
          <section class="settings-section"><h3>Paths and binding</h3><p>Sensitive local paths stay on this machine.</p><div class="stack">
            ${[['Codex home',settings.codex_home],['Dashboard data',settings.data_dir],['Listen address',`${settings.host}:${settings.port}`],['Codex executable',settings.codex_bin],['Token policy',settings.token_required ? 'Required for this bind address' : (settings.token_configured ? 'Configured' : 'Loopback only; optional')]].map(([label,value]) => `<div class="setting-value"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}
          </div></section>
          <section class="settings-section"><h3>Doctor</h3><p>Availability checks do not modify the Codex installation.</p><div class="doctor-list">${checks.map(([name,check]) => `<div class="doctor-row"><span class="check ${check.ok ? 'ok' : ''}"></span><strong>${escapeHtml(name.replaceAll('_',' '))}</strong><span title="${escapeHtml(safeJson(check.value))}">${escapeHtml(Array.isArray(check.value) ? check.value.join(', ') : check.value)}</span></div>`).join('')}</div></section>
        </div>
      </div></div>`;
    } catch (error) { renderError(error); }
  }

  async function refreshSelected(quiet = false) {
    if (!state.selected) return;
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(state.selected.id)}`);
      state.selected = task;
      renderDrawer();
    } catch (error) {
      if (!quiet) toast('Could not refresh task', error.message, 'error');
    }
  }

  async function openTask(sessionId) {
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(sessionId)}`);
      state.selected = task;
      state.selectedTab = 'timeline';
      $('#taskDrawer').setAttribute('aria-hidden', 'false');
      document.body.dataset.drawerOpen = 'true';
      renderDrawer();
    } catch (error) { toast('Could not open task', error.message, 'error'); }
  }

  function closeDrawer() {
    $('#taskDrawer').setAttribute('aria-hidden', 'true');
    delete document.body.dataset.drawerOpen;
    state.selected = null;
  }

  function actionButtons(task) {
    const cap = task.capabilities || {};
    const buttons = [];
    if (cap.instruct) buttons.push('<button class="button button-primary button-small" data-task-action="instruct">Send instruction</button>');
    if (cap.pause) buttons.push('<button class="button button-warning button-small" data-task-action="pause">Pause</button>');
    if (cap.continue) buttons.push('<button class="button button-warning button-small" data-task-action="continue">Continue</button>');
    if (cap.resume && !cap.instruct) buttons.push('<button class="button button-primary button-small" data-task-action="instruct">Resume with instruction</button>');
    if (cap.cancel) buttons.push('<button class="button button-danger button-small" data-task-action="cancel">Cancel process</button>');
    if (cap.mark_complete) buttons.push('<button class="button button-success button-small" data-task-action="complete">Mark complete</button>');
    return buttons.join('') || '<span class="source-pill">Read-only external session</span>';
  }

  function renderDrawer() {
    const task = state.selected;
    if (!task) return;
    $('#drawerKicker').textContent = `${task.source || 'session'} · ${task.id}`;
    $('#drawerTitle').textContent = task.title || 'Untitled session';
    const alerts = (task.alerts || []).filter(alert => alert.state === 'OPEN');
    const tabs = [
      ['timeline', 'Timeline', task.events?.length],
      ['plan', 'Plans', task.plans?.length],
      ['commands', 'Commands', task.commands?.length],
      ['tools', 'Tools', task.tools?.length],
      ['files', 'Files & Diff', task.files?.length],
      ['tests', 'Tests', task.tests?.length],
      ['audit', 'Audit', task.audit?.length],
    ];
    $('#drawerBody').innerHTML = `<section class="detail-hero">
      <div class="detail-hero-top"><div>${statusPill(task.status)} ${sourcePill(task.source)} ${task.managed ? '<span class="source-pill">managed</span>' : ''}</div><div>${progressMarkup(task, true)}</div></div>
      ${task.summary ? `<p class="detail-summary">${escapeHtml(task.summary)}</p>` : ''}
      <div class="detail-meta-grid">
        <div class="meta-box"><span>Phase</span><strong>${escapeHtml(task.phase || 'No phase evidence')}</strong></div>
        <div class="meta-box"><span>Model</span><strong>${escapeHtml(task.model || 'Unknown')}</strong></div>
        <div class="meta-box"><span>Repository</span><strong title="${escapeHtml(task.repo_root || task.cwd)}">${escapeHtml(compactPath(task.repo_root || task.cwd))}</strong></div>
        <div class="meta-box"><span>Last evidence</span><strong title="${escapeHtml(exactTime(task.last_event_at))}">${relativeTime(task.last_event_at || task.updated_at)}</strong></div>
      </div>
    </section>
    <div class="action-bar">${actionButtons(task)}</div>
    ${alerts.map(alert => `<div class="alert-banner"><div><strong>${escapeHtml(alert.title)} · ${escapeHtml(alert.severity)}</strong><p>${escapeHtml(alert.message || '')}</p></div><button class="button button-ghost button-small" data-ack-alert="${escapeHtml(alert.id)}">Acknowledge</button></div>`).join('')}
    <div class="tabs" role="tablist">${tabs.map(([id,label,count]) => `<button class="tab-button ${state.selectedTab === id ? 'active' : ''}" data-detail-tab="${id}" role="tab">${escapeHtml(label)}<span class="tab-count">${count || 0}</span></button>`).join('')}</div>
    <div id="detailTabPanel" class="tab-panel">${renderDetailTab(task, state.selectedTab)}</div>`;
    if (state.selectedTab === 'files') loadWorkingDiff(task.id);
  }

  function renderDetailTab(task, tab) {
    if (tab === 'timeline') return renderTimeline(task.events || []);
    if (tab === 'plan') return renderPlans(task.plans || []);
    if (tab === 'commands') return renderCommands(task.commands || []);
    if (tab === 'tools') return renderTools(task.tools || []);
    if (tab === 'files') return renderFiles(task.files || []);
    if (tab === 'tests') return renderTests(task.tests || []);
    if (tab === 'audit') return renderAudit(task.audit || []);
    return '';
  }

  function renderTimeline(events) {
    if (!events.length) return emptyState('No normalized events', 'The session may only have metadata, or its rollout has not been read yet.');
    return `<div class="timeline">${events.map(event => {
      const dot = event.actor === 'assistant' ? 'agent' : event.actor === 'user' ? 'user' : (event.kind.includes('error') || event.kind.includes('failed') ? 'error' : '');
      return `<article class="timeline-item"><span class="timeline-dot ${dot}"></span><div class="timeline-head"><span class="timeline-kind">${escapeHtml(event.kind)}</span><span class="timeline-time" title="${escapeHtml(exactTime(event.timestamp))}">${relativeTime(event.timestamp)}</span></div>
        ${event.text ? `<div class="timeline-text">${escapeHtml(event.text)}</div>` : ''}
        <details class="timeline-json"><summary>Raw normalized payload</summary><pre class="code-block">${escapeHtml(safeJson(event.payload))}</pre></details></article>`;
    }).join('')}</div>`;
  }

  function renderPlans(plans) {
    if (!plans.length) return emptyState('Progress is Unknown', 'No structured plan has been observed. The dashboard will not invent a percentage.');
    return plans.map(plan => `<article class="plan-version"><div class="plan-version-header"><strong>Plan v${escapeHtml(plan.version)} · ${plan.progress == null ? 'Unknown' : `${Math.round(plan.progress)}%`}</strong><span>${exactTime(plan.timestamp)}</span></div>
      ${plan.explanation ? `<p class="detail-summary">${escapeHtml(plan.explanation)}</p>` : ''}
      ${(plan.steps || []).map(step => `<div class="plan-step ${escapeHtml(step.status)}"><span class="step-check">${step.status === 'completed' ? '✓' : step.status === 'in_progress' ? '•' : ''}</span><span>${escapeHtml(step.text)}</span><span class="step-status">${escapeHtml(step.status)}</span></div>`).join('')}
    </article>`).join('');
  }

  function renderCommands(commands) {
    if (!commands.length) return emptyState('No command evidence', 'Command events will appear here with output, duration, and exit status.');
    return `<div class="evidence-list">${commands.map(command => `<article class="evidence-card"><div class="evidence-head"><strong title="${escapeHtml(command.command)}">$ ${escapeHtml(command.command || 'unknown command')}</strong>${statusPill(command.status)}</div><div class="evidence-body">
      <div class="evidence-grid"><div class="evidence-metric"><span>Exit code</span><strong>${command.exit_code ?? '—'}</strong></div><div class="evidence-metric"><span>Duration</span><strong>${command.duration_ms == null ? '—' : `${command.duration_ms} ms`}</strong></div><div class="evidence-metric"><span>Started</span><strong>${relativeTime(command.started_at)}</strong></div></div>
      ${command.stdout ? `<details open><summary>stdout</summary><pre class="code-block">${escapeHtml(command.stdout)}</pre></details>` : ''}${command.stderr ? `<details open><summary>stderr</summary><pre class="code-block">${escapeHtml(command.stderr)}</pre></details>` : ''}
    </div></article>`).join('')}</div>`;
  }

  function renderTools(tools) {
    if (!tools.length) return emptyState('No tool-call evidence', 'MCP and function-call evidence will appear here when emitted by Codex.');
    return `<div class="evidence-list">${tools.map(tool => `<article class="evidence-card"><div class="evidence-head"><strong>${escapeHtml(tool.tool || 'tool')}</strong>${statusPill(tool.status)}</div><div class="evidence-body">
      <details><summary>Arguments</summary><pre class="code-block">${escapeHtml(safeJson(tool.arguments))}</pre></details>${tool.result_text ? `<details open><summary>Result</summary><pre class="code-block">${escapeHtml(tool.result_text)}</pre></details>` : ''}
    </div></article>`).join('')}</div>`;
  }

  function renderFiles(files) {
    const list = files.length ? `<div class="evidence-list">${files.map(file => `<article class="evidence-card"><div class="evidence-head"><strong>${escapeHtml(file.path)}</strong><div><span class="source-pill">${escapeHtml(file.action)}</span> <button class="button button-ghost button-small" data-file-diff="${escapeHtml(file.path)}">Diff</button></div></div>${file.patch ? `<div class="evidence-body"><pre class="code-block diff-block">${escapeHtml(file.patch)}</pre></div>` : ''}</article>`).join('')}</div>` : emptyState('No file evidence yet', 'Git status and Codex file-change events will be merged here without modifying the repository.');
    return `${list}<article class="evidence-card" style="margin-top:12px"><div class="evidence-head"><strong>Current working tree diff</strong><button class="button button-ghost button-small" data-refresh-diff>Refresh</button></div><div class="evidence-body"><pre id="workingDiff" class="code-block diff-block">Loading diff…</pre></div></article>`;
  }

  async function loadWorkingDiff(sessionId, path = null) {
    const target = $('#workingDiff');
    if (!target) return;
    target.textContent = 'Loading diff…';
    try {
      const query = path ? `?path=${encodeURIComponent(path)}` : '';
      const payload = await api(`/api/tasks/${encodeURIComponent(sessionId)}/diff${query}`);
      target.textContent = payload.diff || 'Working tree is clean, unavailable, or outside a Git repository.';
    } catch (error) { target.textContent = `Unable to read diff: ${error.message}`; }
  }

  function renderTests(tests) {
    if (!tests.length) return emptyState('No recognized test run', 'Known test commands are parsed from command evidence; no passing state is assumed.');
    return `<div class="evidence-list">${tests.map(test => `<article class="evidence-card"><div class="evidence-head"><strong>${escapeHtml(test.command || test.framework || 'test run')}</strong><span class="status-pill ${test.status === 'PASSED' ? 'status-completed' : test.status === 'FAILED' ? 'status-failed' : 'status-idle'}">${escapeHtml(test.status)}</span></div><div class="evidence-body">
      <div class="evidence-grid"><div class="evidence-metric"><span>Passed</span><strong>${test.passed ?? '—'}</strong></div><div class="evidence-metric"><span>Failed</span><strong>${test.failed ?? '—'}</strong></div><div class="evidence-metric"><span>Skipped</span><strong>${test.skipped ?? '—'}</strong></div></div>${test.output ? `<pre class="code-block">${escapeHtml(test.output)}</pre>` : ''}
    </div></article>`).join('')}</div>`;
  }

  function renderAudit(items) {
    if (!items.length) return emptyState('No dashboard actions', 'Control actions and settings changes are recorded here.');
    return `<div class="timeline">${items.map(item => `<article class="timeline-item"><span class="timeline-dot ${item.result === 'error' ? 'error' : 'user'}"></span><div class="timeline-head"><span class="timeline-kind">${escapeHtml(item.action)}</span><span class="timeline-time">${exactTime(item.timestamp)}</span></div><div class="timeline-text">Actor: ${escapeHtml(item.actor)} · Result: ${escapeHtml(item.result)}</div><details><summary>Details</summary><pre class="code-block">${escapeHtml(safeJson(item.detail))}</pre></details></article>`).join('')}</div>`;
  }

  async function runTaskAction(action, payload = {}) {
    const task = state.selected;
    if (!task) return;
    try {
      const updated = await api(`/api/tasks/${encodeURIComponent(task.id)}/actions/${encodeURIComponent(action)}`, { method: 'POST', body: JSON.stringify(payload) });
      state.selected = updated;
      toast('Action accepted', `${action.replaceAll('_',' ')} · ${updated.title || updated.id}`);
      renderDrawer();
      scheduleRefresh();
    } catch (error) { toast('Action failed', error.message, 'error'); }
  }

  function openInstruction(task) {
    const dialog = $('#instructionDialog');
    dialog.elements.session_id.value = task.id;
    dialog.elements.message.value = '';
    $('#instructionError').classList.add('hidden');
    dialog.showModal();
    setTimeout(() => dialog.elements.message.focus(), 30);
  }

  function openComplete(task) {
    const dialog = $('#completeDialog');
    dialog.elements.session_id.value = task.id;
    dialog.elements.summary.value = task.summary || '';
    dialog.showModal();
  }

  function bindEvents() {
    $('#nav').addEventListener('click', event => {
      const button = event.target.closest('[data-view]');
      if (button) setView(button.dataset.view);
    });
    $('#globalSearch').addEventListener('input', event => {
      state.query = event.target.value.trim();
      clearTimeout(state.refreshTimer);
      state.refreshTimer = setTimeout(() => loadCore({ quiet: true }), 260);
    });
    $('#newTaskButton').addEventListener('click', () => {
      const form = $('#newTaskForm');
      form.reset();
      form.elements.start.checked = true;
      form.elements.cwd.value = state.tasks.find(task => task.cwd)?.cwd || '';
      $('#newTaskError').classList.add('hidden');
      $('#newTaskDialog').showModal();
    });
    $('#demoButton').addEventListener('click', async () => {
      try { await api('/api/demo', { method: 'POST', body: '{}' }); toast('Demo loaded', 'Representative sessions and alerts are ready.'); scheduleRefresh(); }
      catch (error) { toast('Could not load demo', error.message, 'error'); }
    });
    $('#scanButton').addEventListener('click', async () => {
      const task = state.tasks[0];
      if (!task) { await loadCore(); return; }
      try { await api(`/api/tasks/${encodeURIComponent(task.id)}/actions/scan`, { method: 'POST', body: '{}' }); toast('Scan complete'); scheduleRefresh(); }
      catch (error) { toast('Scan failed', error.message, 'error'); }
    });

    $('#main').addEventListener('click', async event => {
      const taskTarget = event.target.closest('[data-session-id]');
      if (taskTarget) { openTask(taskTarget.dataset.sessionId); return; }
      const viewTarget = event.target.closest('[data-go-view]');
      if (viewTarget) { setView(viewTarget.dataset.goView); return; }
      if (event.target.closest('[data-retry]')) { loadCore(); return; }
      const statusFilter = event.target.closest('[data-status-filter]');
      if (statusFilter) return;
    });
    $('#main').addEventListener('change', event => {
      if (event.target.matches('[data-status-filter]')) {
        const value = event.target.value;
        const rows = $$('.data-table tbody tr[data-session-id]');
        rows.forEach(row => {
          const task = state.tasks.find(item => item.id === row.dataset.sessionId);
          row.hidden = Boolean(value && task?.status !== value);
        });
      }
    });
    $('#main').addEventListener('submit', async event => {
      if (event.target.id !== 'settingsForm') return;
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.target));
      for (const key of Object.keys(data)) data[key] = Number(data[key]);
      try { state.settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify(data) }); toast('Settings saved', 'New thresholds apply to future reconciliation.'); }
      catch (error) { toast('Settings rejected', error.message, 'error'); }
    });

    $('#taskDrawer').addEventListener('click', async event => {
      if (event.target.closest('[data-close-drawer]')) { closeDrawer(); return; }
      const tab = event.target.closest('[data-detail-tab]');
      if (tab && state.selected) {
        state.selectedTab = tab.dataset.detailTab;
        renderDrawer();
        return;
      }
      const action = event.target.closest('[data-task-action]')?.dataset.taskAction;
      if (action && state.selected) {
        if (action === 'instruct') openInstruction(state.selected);
        else if (action === 'complete') openComplete(state.selected);
        else if (action === 'cancel') {
          if (window.confirm('Cancel this dashboard-owned Codex process?')) runTaskAction('cancel');
        } else runTaskAction(action);
        return;
      }
      const alertButton = event.target.closest('[data-ack-alert]');
      if (alertButton) { runTaskAction('acknowledge', { alert_id: Number(alertButton.dataset.ackAlert) }); return; }
      const fileButton = event.target.closest('[data-file-diff]');
      if (fileButton && state.selected) { loadWorkingDiff(state.selected.id, fileButton.dataset.fileDiff); return; }
      if (event.target.closest('[data-refresh-diff]') && state.selected) loadWorkingDiff(state.selected.id);
    });

    $$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.closeDialog).close()));

    $('#newTaskForm').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = Object.fromEntries(new FormData(form));
      data.start = form.elements.start.checked;
      const errorBox = $('#newTaskError');
      errorBox.classList.add('hidden');
      try {
        const task = await api('/api/tasks', { method: 'POST', body: JSON.stringify(data) });
        $('#newTaskDialog').close();
        toast('Task created', data.start ? 'Codex launch requested.' : 'Idle task draft created.');
        scheduleRefresh();
        openTask(task.id);
      } catch (error) { errorBox.textContent = error.message; errorBox.classList.remove('hidden'); }
    });

    $('#instructionForm').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const errorBox = $('#instructionError');
      errorBox.classList.add('hidden');
      try {
        const task = await api(`/api/tasks/${encodeURIComponent(form.elements.session_id.value)}/actions/instruct`, { method: 'POST', body: JSON.stringify({ message: form.elements.message.value }) });
        $('#instructionDialog').close();
        state.selected = task;
        renderDrawer();
        toast('Instruction sent');
        scheduleRefresh();
      } catch (error) { errorBox.textContent = error.message; errorBox.classList.remove('hidden'); }
    });

    $('#completeForm').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        const task = await api(`/api/tasks/${encodeURIComponent(form.elements.session_id.value)}/actions/complete`, { method: 'POST', body: JSON.stringify({ summary: form.elements.summary.value }) });
        $('#completeDialog').close();
        state.selected = task;
        renderDrawer();
        toast('Completion recorded', 'The terminal state is now explicit.');
        scheduleRefresh();
      } catch (error) { toast('Could not mark complete', error.message, 'error'); }
    });

    document.addEventListener('keydown', event => {
      if (event.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
        event.preventDefault();
        $('#globalSearch').focus();
      }
      if (event.key === 'Escape' && $('#taskDrawer').getAttribute('aria-hidden') === 'false') closeDrawer();
    });
  }

  async function init() {
    loadToken();
    bindEvents();
    const hash = location.hash.slice(1);
    if (viewMeta[hash]) state.view = hash;
    setView(state.view);
    await loadCore();
    connectEvents();
  }

  init();
})();
