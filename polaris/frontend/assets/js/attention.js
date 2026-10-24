/* ─── Attention Module ───
   Лента «что требует внимания сейчас». Данные собирает Attention Engine на
   Hub (календарь, финансы, серверы), экран только показывает их и даёт
   действия: закрепить, отложить, скрыть. Работает с /api/attention. */
window.AttentionModule = (() => {
  'use strict';

  const REFRESH_MS = 60000;

  const URGENCY = {
    critical: { label: 'Критично', rank: 0 },
    high: { label: 'Важно', rank: 1 },
    medium: { label: 'Средне', rank: 2 },
    low: { label: 'Потом', rank: 3 },
  };

  const SOURCES = {
    calendar: { label: 'Календарь', view: 'calendar', emoji: '📅' },
    finance: { label: 'Финансы', view: 'finance', emoji: '💳' },
    servers: { label: 'Серверы', view: 'servers', emoji: '🖥️' },
  };

  const TYPES = {
    task: 'Задача', reminder: 'Напоминание', event: 'Событие', payment: 'Платёж',
    subscription: 'Подписка', payment_due: 'К оплате', payment_overdue: 'Просрочен платёж',
    server_offline: 'Сервер offline', disk_warning: 'Мало места', disk_critical: 'Диск почти полон',
    service_down: 'Сервис остановлен', service_failed: 'Сервис упал',
  };

  /* ─── State ─── */
  let container = null;
  let timer = null;
  let items = [];
  let counts = { total: 0, critical: 0, high: 0 };
  let loaded = false;
  let loading = false;
  let error = '';
  let filter = 'all';
  let snoozeOpenId = null;
  let busyId = null;
  let undo = null; // { itemId, action, text }
  let undoTimer = null;
  let lastLoad = 0;

  /* ─── Helpers ─── */
  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

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
      const detail = data.detail || data.message || `HTTP ${response.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function isLive() {
    return Boolean(container && container.isConnected);
  }

  function formatDue(item) {
    if (!item.due_date) return { text: '', overdue: false };
    const when = new Date(`${item.due_date}T${item.due_time || '23:59'}`);
    if (Number.isNaN(when.getTime())) return { text: item.due_date, overdue: false };
    const date = when.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
    const text = item.due_time ? `${date}, ${item.due_time}` : date;
    return { text, overdue: when.getTime() < Date.now() };
  }

  function snoozeOptions() {
    const now = new Date();
    const inHours = (h) => new Date(now.getTime() + h * 3600 * 1000);
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(9, 0, 0, 0);
    return [
      { label: '1 час', until: inHours(1) },
      { label: '3 часа', until: inHours(3) },
      { label: 'До завтра', until: tomorrow },
    ];
  }

  /* ─── Data ─── */
  async function load({ silent = false } = {}) {
    if (loading) return;
    loading = true;
    if (!silent && !loaded) render();
    try {
      const resp = await apiRequest('/api/attention/feed');
      const feed = resp.data || {};
      items = Array.isArray(feed.items) ? feed.items : [];
      counts = {
        total: feed.total ?? items.length,
        critical: feed.critical_count ?? 0,
        high: feed.high_count ?? 0,
      };
      error = '';
      loaded = true;
      lastLoad = Date.now();
    } catch (err) {
      error = err.message || 'Не удалось загрузить ленту';
    } finally {
      loading = false;
      if (isLive()) render();
    }
  }

  async function performAction(itemId, action, snoozeUntil) {
    error = '';
    busyId = itemId;
    render();
    try {
      await apiRequest('/api/attention/action', 'POST', {
        item_id: itemId,
        action,
        snooze_until: snoozeUntil || null,
      });
      const item = items.find((x) => x.id === itemId);
      if (action === 'pin') {
        if (item) item.pinned = true;
      } else {
        items = items.filter((x) => x.id !== itemId);
        counts.total = Math.max(0, counts.total - 1);
        showUndo(itemId, action, action === 'dismiss' ? 'Скрыто' : 'Отложено');
      }
    } catch (err) {
      error = `Не удалось выполнить действие: ${err.message}`;
    } finally {
      busyId = null;
      snoozeOpenId = null;
      if (isLive()) render();
    }
    if (!error) load({ silent: true });
  }

  async function removeAction(itemId, action) {
    busyId = itemId;
    render();
    try {
      await apiRequest(`/api/attention/action/${encodeURIComponent(itemId)}/${action}`, 'DELETE');
      error = '';
    } catch (err) {
      error = `Не удалось отменить: ${err.message}`;
    } finally {
      busyId = null;
    }
    await load({ silent: true });
  }

  function showUndo(itemId, action, text) {
    clearTimeout(undoTimer);
    undo = { itemId, action, text };
    undoTimer = setTimeout(() => {
      undo = null;
      if (isLive()) render();
    }, 8000);
  }

  /* ─── Render ─── */
  function renderCard(item) {
    const urgency = URGENCY[item.urgency] ? item.urgency : 'medium';
    const source = SOURCES[item.source] || { label: item.source || '—', view: '', emoji: '•' };
    const due = formatDue(item);
    const busy = busyId === item.id;
    const id = escapeHTML(item.id);

    const snoozePanel = snoozeOpenId === item.id
      ? `<div class="attn-snooze">
          ${snoozeOptions().map((o) => `
            <button type="button" class="small-button" data-attn-action="snooze-do"
              data-id="${id}" data-until="${escapeHTML(o.until.toISOString())}">${escapeHTML(o.label)}</button>`).join('')}
          <button type="button" class="small-button ghost" data-attn-action="snooze-close">Отмена</button>
        </div>`
      : '';

    return `
      <article class="card attn-card attn-${urgency}${item.pinned ? ' is-pinned' : ''}${busy ? ' is-busy' : ''}">
        <div class="attn-main">
          <div class="attn-top">
            <span class="attn-source">${source.emoji} ${escapeHTML(source.label)}</span>
            <span class="badge ${urgency === 'critical' ? 'error' : urgency === 'high' ? 'warning' : ''}">
              ${escapeHTML(URGENCY[urgency].label)}
            </span>
            ${item.pinned ? '<span class="badge primary">📌 Закреплено</span>' : ''}
          </div>
          <h3 class="attn-title">${escapeHTML(item.title)}</h3>
          ${item.description ? `<p class="attn-desc">${escapeHTML(item.description)}</p>` : ''}
          <div class="attn-meta">
            <span>${escapeHTML(TYPES[item.type] || item.type || '')}</span>
            ${due.text ? `<span class="${due.overdue ? 'attn-overdue' : ''}">${escapeHTML(due.text)}${due.overdue ? ' · просрочено' : ''}</span>` : ''}
          </div>
        </div>
        <div class="attn-actions">
          ${source.view ? `<button type="button" class="small-button primary" data-view="${escapeHTML(source.view)}">Открыть</button>` : ''}
          <button type="button" class="small-button" data-attn-action="${item.pinned ? 'unpin' : 'pin'}" data-id="${id}" ${busy ? 'disabled' : ''}>
            ${item.pinned ? 'Открепить' : 'Закрепить'}
          </button>
          <button type="button" class="small-button" data-attn-action="snooze-open" data-id="${id}" ${busy ? 'disabled' : ''}>Отложить</button>
          <button type="button" class="small-button ghost" data-attn-action="dismiss" data-id="${id}" ${busy ? 'disabled' : ''}>Скрыть</button>
        </div>
        ${snoozePanel}
      </article>
    `;
  }

  function renderBody() {
    if (!loaded && loading) {
      return `<section class="card skeleton-card">
        <div class="skeleton-line skeleton-line-title"></div>
        <div class="skeleton-line"></div><div class="skeleton-line"></div>
      </section>`;
    }

    if (!loaded && error) {
      return `<section class="card empty-card attn-empty">
        <h3>Лента недоступна</h3>
        <p class="metric-subtext">${escapeHTML(error)}</p>
        <button type="button" class="small-button primary" data-attn-action="reload">Повторить</button>
      </section>`;
    }

    const visible = filter === 'all' ? items : items.filter((x) => x.source === filter);
    if (!visible.length) {
      return `<section class="card empty-card attn-empty">
        <div class="attn-empty-icon">✓</div>
        <h3>${items.length ? 'В этом разделе чисто' : 'Всё спокойно'}</h3>
        <p class="metric-subtext">${items.length
          ? 'Остальные разделы — под фильтром выше.'
          : 'Сейчас ничего не требует внимания.'}</p>
      </section>`;
    }

    return `<div class="attn-list">${visible.map(renderCard).join('')}</div>`;
  }

  function renderFilters() {
    const present = Object.keys(SOURCES).filter((key) => items.some((x) => x.source === key));
    if (present.length < 2) return '';
    const chip = (key, label, n) => `
      <button type="button" class="attn-chip${filter === key ? ' is-active' : ''}"
        data-attn-action="filter" data-filter="${key}">${escapeHTML(label)} <span>${n}</span></button>`;
    return `<div class="attn-filters">
      ${chip('all', 'Все', items.length)}
      ${present.map((key) => chip(key, SOURCES[key].label, items.filter((x) => x.source === key).length)).join('')}
    </div>`;
  }

  function render() {
    if (!container) return;
    if (filter !== 'all' && !items.some((x) => x.source === filter)) filter = 'all';

    const summary = loaded
      ? `<div class="attn-summary">
          <div class="attn-stat"><strong>${counts.total}</strong><span>всего</span></div>
          <div class="attn-stat is-critical"><strong>${counts.critical}</strong><span>критично</span></div>
          <div class="attn-stat is-high"><strong>${counts.high}</strong><span>важно</span></div>
        </div>`
      : '';

    const errorBar = loaded && error
      ? `<div class="attn-bar is-error">${escapeHTML(error)}
          <button type="button" class="small-button" data-attn-action="reload">Повторить</button></div>`
      : '';

    const undoBar = undo
      ? `<div class="attn-bar">${escapeHTML(undo.text)}
          <button type="button" class="small-button" data-attn-action="undo">Отменить</button></div>`
      : '';

    container.innerHTML = `${summary}${errorBar}${undoBar}${renderFilters()}${renderBody()}`;
  }

  /* ─── Events ─── */
  function onClick(event) {
    const btn = event.target.closest('[data-attn-action]');
    if (!btn || !container.contains(btn)) return;
    event.preventDefault();
    const { attnAction: action, id } = btn.dataset;

    switch (action) {
      case 'reload': load(); break;
      case 'filter': filter = btn.dataset.filter || 'all'; render(); break;
      case 'pin': performAction(id, 'pin'); break;
      case 'unpin': removeAction(id, 'pin'); break;
      case 'dismiss': performAction(id, 'dismiss'); break;
      case 'snooze-open': snoozeOpenId = snoozeOpenId === id ? null : id; render(); break;
      case 'snooze-close': snoozeOpenId = null; render(); break;
      case 'snooze-do': performAction(id, 'snooze', btn.dataset.until); break;
      case 'undo':
        if (undo) {
          const { itemId, action: undoneAction } = undo;
          clearTimeout(undoTimer);
          undo = null;
          removeAction(itemId, undoneAction);
        }
        break;
      default: break;
    }
  }

  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) {
      console.warn('[AttentionModule] Container not found:', containerId);
      return;
    }
    clearInterval(timer);
    container = el;
    container.addEventListener('click', onClick);
    render(); // мгновенно показываем кэш, если он есть
    // экран перерисовывается при любом уведомлении — не дёргаем API чаще раза в 15 с
    if (!loaded || Date.now() - lastLoad > 15000) load({ silent: loaded });
    timer = setInterval(() => {
      if (!isLive()) { clearInterval(timer); timer = null; return; }
      if (!document.hidden) load({ silent: true });
    }, REFRESH_MS);
  }

  return { init, reload: () => load() };
})();
