/* ─── Servers Module ───
   Мониторинг инфраструктуры через Polaris Agent. Hub не подключается к
   серверам по SSH — Agent сам шлёт heartbeat/metrics/events. Работает с
   /api/v1/servers (admin) и получает данные, которые уже собрал Agent. */
window.ServersModule = (() => {
  'use strict';

  const ICONS = {
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L3 14h7.5l.5 5 7-11.5L13 2z"></path></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg>',
    refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36"></path><path d="M21 3v6h-6"></path></svg>',
  };

  const STATUS_LABELS = {
    pending: 'Waiting for agent',
    online: 'Online',
    offline: 'Offline',
    error: 'Error',
  };

  const STATUS_DOTS = {
    pending: '🟡',
    online: '🟢',
    offline: '🔴',
    error: '🔴',
  };

  const SERVICE_LABELS = { running: 'running', stopped: 'stopped', failed: 'failed', unknown: 'unknown' };

  /* ─── State ─── */
  let view = 'list'; // "list" | "add" | "detail"
  let servers = [];
  let loading = false;
  let error = '';

  let addForm = { name: '', address: '' };
  let addSaving = false;
  let addError = '';
  let tokenInfo = null; // {server_id, token, expires_at, expires_in_seconds, install_command}

  let selectedServerId = null;
  let selectedServer = null;
  let selectedEvents = [];
  let selectedMetricsHistory = [];

  let listPollTimer = null;
  let countdownTimer = null;

  /* ─── Helpers ─── */
  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function hydrateIcons(root) {
    root.querySelectorAll('[data-icon]').forEach((node) => {
      node.innerHTML = ICONS[node.dataset.icon] || '';
    });
  }

  function pct(value) {
    return value === null || value === undefined ? '—' : `${Math.round(value)}%`;
  }

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = bytes, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(1)} ${units[i]}`;
  }

  function formatUptime(seconds) {
    if (!seconds) return '—';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    if (days > 0) return `${days} дн. ${hours} ч.`;
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours} ч. ${minutes} мин.`;
    return `${minutes} мин.`;
  }

  function formatAgo(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    if (seconds < 60) return `${seconds} сек. назад`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} мин. назад`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч. назад`;
    return `${Math.floor(seconds / 86400)} дн. назад`;
  }

  function formatCountdown(seconds) {
    if (seconds <= 0) return 'истёк';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function formatEventType(type) {
    const map = {
      agent_offline: 'Agent offline',
      disk_threshold: 'Диск почти заполнен',
      service_down: 'Сервис не работает',
    };
    return map[type] || type;
  }

  /* ─── API ─── */
  function requestHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  async function apiRequest(path, method = 'GET', body = undefined) {
    const opts = { method, headers: requestHeaders() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const response = await fetch(path, opts);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    }
    return data;
  }

  /* ─── Data loading ─── */
  async function loadServers({ silent } = {}) {
    if (!silent) { loading = true; error = ''; render(); }
    try {
      const resp = await apiRequest('/api/v1/servers');
      servers = resp.data?.servers || [];
    } catch (e) {
      error = e.message;
    }
    loading = false;
    render();
  }

  async function loadServerDetail(serverId, { silent } = {}) {
    if (!silent) { loading = true; error = ''; render(); }
    try {
      const [serverResp, eventsResp, metricsResp] = await Promise.all([
        apiRequest(`/api/v1/servers/${serverId}`),
        apiRequest(`/api/v1/servers/${serverId}/events`),
        apiRequest(`/api/v1/servers/${serverId}/metrics-history?limit=20`),
      ]);
      selectedServer = serverResp.data;
      selectedEvents = eventsResp.data?.events || [];
      selectedMetricsHistory = metricsResp.data?.points || [];
    } catch (e) {
      error = e.message;
    }
    loading = false;
    render();
  }

  /* ─── Polling ─── */
  function startListPolling() {
    stopPolling();
    listPollTimer = setInterval(() => loadServers({ silent: true }), 10000);
  }

  function startDetailPolling() {
    stopPolling();
    listPollTimer = setInterval(() => {
      if (selectedServerId) loadServerDetail(selectedServerId, { silent: true });
    }, 8000);
  }

  function stopPolling() {
    if (listPollTimer) { clearInterval(listPollTimer); listPollTimer = null; }
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  }

  /* ─── Rendering ─── */
  function render() {
    const container = document.getElementById('servers-container');
    if (!container) return;
    container.innerHTML = '';
    const fragment = document.createRange().createContextualFragment(renderPage());
    container.appendChild(fragment);
    hydrateIcons(container);
  }

  function renderPage() {
    if (view === 'add') return renderAddView();
    if (view === 'detail') return renderDetailView();
    return renderListView();
  }

  function renderListView() {
    if (loading && !servers.length) {
      return `<div class="servers-wrapper">${renderHeader(false)}<div class="card servers-card span-12"><p class="metric-subtext">Загружаю серверы…</p></div></div>`;
    }
    if (error) {
      return `<div class="servers-wrapper">${renderHeader(false)}<div class="card servers-card span-12"><div class="calendar-error"><span class="icon" data-icon="alert"></span><p>${escapeHTML(error)}</p></div></div></div>`;
    }
    if (!servers.length) {
      return `
        <div class="servers-wrapper">
          ${renderHeader(false)}
          <div class="card servers-card span-12">
            <p class="metric-subtext">Серверов пока нет. Добавьте первый — Hub сгенерирует одноразовый registration token и готовую команду установки Agent.</p>
          </div>
        </div>
      `;
    }

    const cards = servers.map((s) => renderServerCard(s)).join('');
    return `
      <div class="servers-wrapper">
        ${renderHeader(false)}
        <div class="servers-grid">${cards}</div>
      </div>
    `;
  }

  function renderHeader(showBack) {
    return `
      <div class="calendar-header">
        <div style="display:flex;align-items:center;gap:8px">
          ${showBack ? `<button class="servers-back-button" type="button" data-action="back-to-list"><span class="icon" data-icon="back"></span></button>` : ''}
          <h1>Servers</h1>
        </div>
        ${!showBack ? `
          <button class="calendar-add-button" type="button" data-action="open-add-server">
            <span class="icon" data-icon="plus" aria-hidden="true"></span>
            <span>Add Server</span>
          </button>
        ` : ''}
      </div>
    `;
  }

  function renderServerCard(s) {
    const dot = STATUS_DOTS[s.status] || '⚪';
    const label = STATUS_LABELS[s.status] || s.status;
    return `
      <article class="card servers-server-card" data-action="open-server" data-server-id="${escapeHTML(s.id)}">
        <div class="servers-card-top">
          <span class="servers-card-name">${escapeHTML(s.name)}</span>
          <span class="servers-card-status">${dot}</span>
        </div>
        <div class="servers-card-status-label">${escapeHTML(label)}${s.status === 'error' && s.status_reason === 'token_expired' ? ' · token expired' : ''}</div>
        ${s.status === 'online' || s.status === 'offline' ? `
          <div class="servers-card-metrics">
            <span>CPU ${pct(s.cpu_usage)}</span>
            <span>RAM ${pct(s.mem_percent)}</span>
            <span>Disk ${pct(s.disk_percent)}</span>
          </div>
        ` : ''}
        <div class="servers-card-footer">Last seen ${formatAgo(s.seconds_since_seen)}</div>
      </article>
    `;
  }

  function renderAddView() {
    if (tokenInfo) {
      return renderWaitingForAgent();
    }

    return `
      <div class="servers-wrapper">
        ${renderHeader(true)}
        <div class="card servers-card span-12">
          <h3 class="finance-section-title">Новый сервер</h3>
          <div class="detail-field">
            <label>Название</label>
            <input type="text" id="servers-add-name" class="create-modal-input" style="min-height:40px" placeholder="Например, DE-1" value="${escapeHTML(addForm.name)}" ${addSaving ? 'disabled' : ''} autofocus />
          </div>
          <div class="detail-field" style="margin-top:10px">
            <label>Адрес (необязательно)</label>
            <input type="text" id="servers-add-address" class="create-modal-input" style="min-height:40px" placeholder="IP или домен — просто метаданные" value="${escapeHTML(addForm.address)}" ${addSaving ? 'disabled' : ''} />
          </div>
          ${addError ? `<p class="create-modal-hint" style="color:var(--error)">${escapeHTML(addError)}</p>` : ''}
          <div class="create-modal-actions" style="justify-content:flex-end;margin-top:14px">
            <button class="small-button ghost" type="button" data-action="back-to-list" ${addSaving ? 'disabled' : ''}>Отмена</button>
            <button class="small-button primary" type="button" data-action="submit-add-server" ${addSaving ? 'disabled' : ''}>${addSaving ? 'Создаю…' : 'Create'}</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderWaitingForAgent() {
    const remaining = Math.max(0, Math.floor((new Date(tokenInfo.expires_at).getTime() - Date.now()) / 1000));
    const expired = remaining <= 0;

    return `
      <div class="servers-wrapper">
        ${renderHeader(true)}
        <div class="card servers-card span-12">
          <h3 class="finance-section-title">${escapeHTML(tokenInfo.name || 'Новый сервер')}</h3>
          <p class="servers-waiting-status">🟡 Waiting for agent</p>
          <p class="metric-subtext">Установите Polaris Agent на сервере, чтобы он появился здесь online.</p>

          <div class="servers-install-box">
            <button class="servers-copy-button" type="button" data-action="copy-install-command" aria-label="Скопировать команду" title="Скопировать">
              <span class="icon" data-icon="copy"></span>
            </button>
            <code id="servers-install-command">${escapeHTML(tokenInfo.install_command)}</code>
          </div>

          <p class="servers-token-timer ${expired ? 'expired' : ''}">
            ${expired ? '🔴 Registration token expired' : `Token expires in ${formatCountdown(remaining)}`}
          </p>

          ${expired ? `<button class="small-button primary" type="button" data-action="regenerate-token">Сгенерировать новый token</button>` : ''}

          <div class="create-modal-actions" style="justify-content:flex-end;margin-top:14px">
            <button class="small-button ghost" type="button" data-action="back-to-list">Готово</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderDetailView() {
    if (loading && !selectedServer) {
      return `<div class="servers-wrapper">${renderHeader(true)}<div class="card servers-card span-12"><p class="metric-subtext">Загружаю…</p></div></div>`;
    }
    if (!selectedServer) {
      return `<div class="servers-wrapper">${renderHeader(true)}<div class="card servers-card span-12"><p class="metric-subtext">Сервер не найден.</p></div></div>`;
    }

    const s = selectedServer;
    const dot = STATUS_DOTS[s.status] || '⚪';
    const label = STATUS_LABELS[s.status] || s.status;

    if (s.status === 'pending' || (s.status === 'error' && s.status_reason === 'token_expired')) {
      return renderPendingDetail(s);
    }

    return `
      <div class="servers-wrapper">
        ${renderHeader(true)}
        <div class="card servers-card span-12">
          <div class="servers-detail-title-row">
            <h2>${escapeHTML(s.name)}</h2>
            <span>${dot} ${escapeHTML(label)}</span>
          </div>
          ${s.hostname ? `<p class="metric-subtext">${escapeHTML(s.hostname)}${s.address ? ' · ' + escapeHTML(s.address) : ''}</p>` : ''}
        </div>

        <div class="grid finance-summary-grid">
          <article class="card metric-card span-4"><h3>CPU</h3><div class="metric"><span class="metric-value">${pct(s.cpu_usage)}</span></div></article>
          <article class="card metric-card span-4"><h3>RAM</h3><div class="metric"><span class="metric-value">${pct(s.mem_percent)}</span></div></article>
          <article class="card metric-card span-4"><h3>Disk</h3><div class="metric"><span class="metric-value">${pct(s.disk_percent)}</span></div></article>
        </div>

        <div class="card servers-card span-12">
          <h3 class="finance-section-title">Overview</h3>
          <div class="servers-overview-grid">
            <div><span class="servers-overview-label">Uptime</span><span>${formatUptime(s.uptime_seconds)}</span></div>
            <div><span class="servers-overview-label">Last heartbeat</span><span>${formatAgo(s.seconds_since_seen)}</span></div>
            <div><span class="servers-overview-label">Agent version</span><span>${escapeHTML(s.agent_version || '—')}</span></div>
          </div>
        </div>

        ${renderServicesCard(s)}

        <div class="card servers-card span-12">
          <h3 class="finance-section-title">System</h3>
          <div class="servers-overview-grid">
            <div><span class="servers-overview-label">OS</span><span>${escapeHTML(s.os || '—')}</span></div>
            <div><span class="servers-overview-label">Kernel</span><span>${escapeHTML(s.kernel || '—')}</span></div>
            <div><span class="servers-overview-label">Architecture</span><span>${escapeHTML(s.architecture || '—')}</span></div>
            <div><span class="servers-overview-label">Hostname</span><span>${escapeHTML(s.hostname || '—')}</span></div>
          </div>
        </div>

        ${renderMetricsHistoryCard()}
        ${renderEventsCard()}

        <div class="card servers-card span-12">
          <button class="small-button ghost" type="button" data-action="delete-server" style="color:var(--error);border-color:rgba(255,93,115,0.3)">Удалить сервер</button>
        </div>
      </div>
    `;
  }

  function renderPendingDetail(s) {
    // Переиспользуем экран ожидания агента — токен нужно перезапросить,
    // т.к. сырой токен не хранится после выдачи.
    return `
      <div class="servers-wrapper">
        ${renderHeader(true)}
        <div class="card servers-card span-12">
          <h3 class="finance-section-title">${escapeHTML(s.name)}</h3>
          <p class="servers-waiting-status">${s.status === 'error' ? '🔴 Registration token expired' : '🟡 Waiting for agent'}</p>
          <p class="metric-subtext">${s.status === 'error' ? 'Сгенерируйте новый registration token, чтобы установить Agent.' : 'Установите Polaris Agent, чтобы сервер стал online.'}</p>
          <button class="small-button primary" type="button" data-action="regenerate-token-for" data-server-id="${escapeHTML(s.id)}">
            ${s.status === 'error' ? 'Сгенерировать новый token' : 'Показать install-команду'}
          </button>
        </div>
      </div>
    `;
  }

  function renderServicesCard(s) {
    if (!s.services || !s.services.length) return '';
    const rows = s.services.map((svc) => {
      const dot = svc.status === 'running' ? '🟢' : svc.status === 'failed' ? '🔴' : svc.status === 'stopped' ? '🟠' : '⚪';
      return `<div class="servers-service-row"><span>${escapeHTML(svc.name)}</span><span>${dot} ${SERVICE_LABELS[svc.status] || svc.status}</span></div>`;
    }).join('');
    return `<div class="card servers-card span-12"><h3 class="finance-section-title">Services</h3>${rows}</div>`;
  }

  function renderMetricsHistoryCard() {
    if (!selectedMetricsHistory.length) return '';
    const rows = selectedMetricsHistory.slice(-10).reverse().map((p) => {
      const t = new Date(p.recorded_at);
      const time = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
      return `<div class="servers-metric-row"><span>${time}</span><span>CPU ${pct(p.cpu_usage)}</span><span>RAM ${pct(p.mem_percent)}</span></div>`;
    }).join('');
    return `<div class="card servers-card span-12"><h3 class="finance-section-title">Metrics history</h3>${rows}</div>`;
  }

  function renderEventsCard() {
    if (!selectedEvents.length) {
      return `<div class="card servers-card span-12"><h3 class="finance-section-title">Events</h3><p class="metric-subtext">Событий пока нет.</p></div>`;
    }
    const rows = selectedEvents.slice(0, 15).map((e) => `
      <div class="servers-event-row severity-${escapeHTML(e.severity)}">
        <span>${formatEventType(e.type)}</span>
        <span class="servers-event-meta">${e.resolved_at ? 'resolved' : 'active'} · ${escapeHTML(e.created_at?.slice(0, 16).replace('T', ' ') || '')}</span>
      </div>
    `).join('');
    return `<div class="card servers-card span-12"><h3 class="finance-section-title">Events</h3>${rows}</div>`;
  }

  /* ─── Handlers ─── */
  function goToList() {
    stopPolling();
    view = 'list';
    tokenInfo = null;
    addForm = { name: '', address: '' };
    addError = '';
    selectedServer = null;
    selectedServerId = null;
    render();
    loadServers();
    startListPolling();
  }

  function openAddServer() {
    stopPolling();
    view = 'add';
    tokenInfo = null;
    addForm = { name: '', address: '' };
    addError = '';
    render();
  }

  async function submitAddServer() {
    const name = document.getElementById('servers-add-name')?.value.trim() || '';
    const address = document.getElementById('servers-add-address')?.value.trim() || '';
    if (!name) { addError = 'Укажите название сервера'; render(); return; }

    addSaving = true;
    addError = '';
    render();

    try {
      const resp = await apiRequest('/api/v1/servers', 'POST', { name, address });
      const server = resp.data.server;
      const token = resp.data.registration_token;
      tokenInfo = { ...token, name: server.name };
      addSaving = false;
      render();
      startTokenCountdown();
    } catch (e) {
      addSaving = false;
      addError = e.message;
      render();
    }
  }

  function startTokenCountdown() {
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
      if (view === 'add' && tokenInfo) render();
      else clearInterval(countdownTimer);
    }, 1000);
  }

  async function regenerateTokenFor(serverId) {
    try {
      const resp = await apiRequest(`/api/v1/servers/${serverId}/registration-token`, 'POST');
      const server = servers.find((s) => s.id === serverId) || selectedServer;
      tokenInfo = { ...resp.data, name: server?.name || '' };
      view = 'add';
      stopPolling();
      render();
      startTokenCountdown();
    } catch (e) {
      error = e.message;
      render();
    }
  }

  function copyInstallCommand() {
    const text = document.getElementById('servers-install-command')?.textContent || '';
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  }

  function openServer(serverId) {
    stopPolling();
    view = 'detail';
    selectedServerId = serverId;
    selectedServer = null;
    render();
    loadServerDetail(serverId);
    startDetailPolling();
  }

  async function deleteServer() {
    if (!selectedServerId) return;
    if (!window.confirm('Удалить этот сервер? Действие необратимо.')) return;
    try {
      await apiRequest(`/api/v1/servers/${selectedServerId}`, 'DELETE');
      goToList();
    } catch (e) {
      error = e.message;
      render();
    }
  }

  /* ─── Init ─── */
  function init(containerId) {
    const container = document.getElementById(containerId);
    if (!container) {
      console.warn('[ServersModule] Container not found:', containerId);
      return;
    }

    container.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;

      if (action === 'open-add-server') { event.preventDefault(); openAddServer(); return; }
      if (action === 'back-to-list') { event.preventDefault(); goToList(); return; }
      if (action === 'submit-add-server') { event.preventDefault(); submitAddServer(); return; }
      if (action === 'copy-install-command') { event.preventDefault(); copyInstallCommand(); return; }
      if (action === 'regenerate-token') { event.preventDefault(); regenerateTokenFor(tokenInfo.server_id); return; }
      if (action === 'regenerate-token-for') { event.preventDefault(); regenerateTokenFor(btn.dataset.serverId); return; }
      if (action === 'delete-server') { event.preventDefault(); deleteServer(); return; }

      const card = event.target.closest('[data-action="open-server"]');
      if (card) { event.preventDefault(); openServer(card.dataset.serverId); return; }
    });

    view = 'list';
    loadServers();
    startListPolling();
  }

  return { init, reload: () => loadServers() };
})();
