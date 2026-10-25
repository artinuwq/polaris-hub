/* ─── Email Module ───
   Письма, которые Polaris собирает с подключённых ящиков (mail.ru, Google).
   Каждое письмо — раскрывающаяся карточка. Ящики и получатели настраиваются в Settings. */
window.EmailModule = (() => {
  'use strict';

  const REFRESH_MS = 60000;
  const PAGE = 30;

  let container = null;
  let timer = null;
  let accounts = [];
  let messages = [];
  let hasMore = false;
  let filter = null; // account id | null
  let loaded = false;
  let loading = false;
  let error = '';
  let lastLoad = 0;

  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  }

  function requestHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  async function api(path) {
    const response = await fetch(path, { headers: requestHeaders() });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail || data.message || `HTTP ${response.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data.data || {};
  }

  const isLive = () => Boolean(container && container.isConnected);

  function senderName(sender) {
    const text = String(sender || '').trim();
    const match = text.match(/^"?([^"<]+?)"?\s*<[^>]+>$/);
    return (match ? match[1] : text.replace(/[<>]/g, '')).trim() || '(неизвестный отправитель)';
  }

  function formatTime(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    return sameDay
      ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
      : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  }

  function accountName(m) {
    return m.account_label || m.account_email || '';
  }

  function previewLine(body) {
    return String(body || '').replace(/\s+/g, ' ').trim().slice(0, 140);
  }

  /* ─── Data ─── */
  async function load({ silent = false } = {}) {
    if (loading) return;
    loading = true;
    if (!silent && !loaded) render();
    try {
      const keep = Math.min(100, Math.max(PAGE, messages.length));
      const query = `limit=${keep}${filter ? `&account_id=${filter}` : ''}`;
      const [overview, list] = await Promise.all([api('/api/mail/overview'), api(`/api/mail/messages?${query}`)]);
      accounts = overview.accounts || [];
      messages = list.messages || [];
      hasMore = Boolean(list.has_more);
      error = '';
      loaded = true;
      lastLoad = Date.now();
    } catch (err) {
      error = err.message || 'Не удалось загрузить почту';
    } finally {
      loading = false;
      if (isLive()) render();
    }
  }

  async function loadMore() {
    if (loading || !messages.length) return;
    loading = true;
    render();
    try {
      const last = messages[messages.length - 1].id;
      const query = `limit=${PAGE}&before_id=${last}${filter ? `&account_id=${filter}` : ''}`;
      const list = await api(`/api/mail/messages?${query}`);
      messages = messages.concat(list.messages || []);
      hasMore = Boolean(list.has_more);
    } catch (err) {
      error = err.message || 'Не удалось загрузить ещё';
    } finally {
      loading = false;
      if (isLive()) render();
    }
  }

  /* ─── Render ─── */
  function deliveryBadge(m) {
    if (m.failed_count) return '<span class="badge error">не доставлено в Telegram</span>';
    if (m.pending_count) return '<span class="badge warning">отправляется…</span>';
    if (m.sent_count) return '<span class="badge success">в Telegram</span>';
    return '';
  }

  function renderItem(m) {
    const attachments = (m.attachments || []).length
      ? `<div class="mail-attach">📎 ${escapeHTML(m.attachments.join(', '))}</div>`
      : '';
    return `
      <details class="card mail-item">
        <summary>
          <span class="mail-avatar">${escapeHTML(senderName(m.sender).charAt(0).toUpperCase())}</span>
          <span class="mail-head">
            <span class="mail-top">
              <span class="mail-from">${escapeHTML(senderName(m.sender))}</span>
              <span class="mail-time">${escapeHTML(formatTime(m.received_at))}</span>
            </span>
            <span class="mail-subject">${escapeHTML(m.subject || '(без темы)')}</span>
            <span class="mail-preview">${escapeHTML(previewLine(m.body))}</span>
          </span>
        </summary>
        <div class="mail-expanded">
          <dl class="mail-meta">
            <dt>От</dt><dd>${escapeHTML(m.sender || '—')}</dd>
            <dt>Кому</dt><dd>${escapeHTML(m.recipient || '—')}</dd>
            <dt>Дата</dt><dd>${escapeHTML(m.date_text || '—')}</dd>
            <dt>Ящик</dt><dd>${escapeHTML(accountName(m))}</dd>
          </dl>
          ${attachments}
          <div class="mail-text">${escapeHTML(m.body || '')}</div>
          <div class="mail-foot">${deliveryBadge(m)}</div>
        </div>
      </details>`;
  }

  function renderBody() {
    if (!loaded && loading) {
      return `<section class="card skeleton-card"><div class="skeleton-line skeleton-line-title"></div>
        <div class="skeleton-line"></div><div class="skeleton-line"></div></section>`;
    }
    if (!loaded && error) {
      return `<section class="card empty-card"><h3>Почта недоступна</h3>
        <p class="metric-subtext">${escapeHTML(error)}</p>
        <button type="button" class="small-button primary" data-mail-action="reload">Повторить</button></section>`;
    }
    if (!accounts.length) {
      return `<section class="card empty-card">
        <h3>Почта не подключена</h3>
        <p class="metric-subtext">Добавьте ящик mail.ru или Google — письма появятся здесь и придут в Telegram.</p>
        <button type="button" class="small-button primary" data-view="settings">Настроить почту</button></section>`;
    }
    if (!messages.length) {
      return `<section class="card empty-card"><h3>Писем пока нет</h3>
        <p class="metric-subtext">Новые письма появятся здесь в течение минуты после прихода.</p></section>`;
    }
    return `<div class="mail-list">${messages.map(renderItem).join('')}</div>
      ${hasMore ? `<div class="mail-more"><button type="button" class="small-button" data-mail-action="more" ${loading ? 'disabled' : ''}>
        ${loading ? 'Загружаю…' : 'Показать ещё'}</button></div>` : ''}`;
  }

  function render() {
    if (!container) return;
    const broken = accounts.filter((a) => a.enabled && a.last_status === 'error');
    const warn = broken.map((a) => `
      <div class="mail-warn">⚠️ ${escapeHTML(a.label || a.email)}: ${escapeHTML(a.last_error || 'ошибка подключения')}
        <button type="button" class="small-button" data-view="settings">Исправить</button></div>`).join('');

    const chips = accounts.length > 1
      ? `<div class="attn-filters">
          <button type="button" class="attn-chip${filter === null ? ' is-active' : ''}" data-mail-action="filter" data-id="">Все</button>
          ${accounts.map((a) => `<button type="button" class="attn-chip${filter === a.id ? ' is-active' : ''}"
            data-mail-action="filter" data-id="${a.id}">${escapeHTML(a.label || a.email)} <span>${a.message_count}</span></button>`).join('')}
        </div>`
      : '';

    container.innerHTML = `${warn}${chips}${renderBody()}`;
  }

  function onClick(event) {
    const btn = event.target.closest('[data-mail-action]');
    if (!btn || !container.contains(btn)) return;
    event.preventDefault();
    const action = btn.dataset.mailAction;
    if (action === 'reload') load();
    else if (action === 'more') loadMore();
    else if (action === 'filter') {
      filter = btn.dataset.id ? Number(btn.dataset.id) : null;
      messages = [];
      load();
    }
  }

  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    clearInterval(timer);
    container = el;
    container.addEventListener('click', onClick);
    render();
    if (!loaded || Date.now() - lastLoad > 15000) load({ silent: loaded });
    timer = setInterval(() => {
      if (!isLive()) { clearInterval(timer); timer = null; return; }
      if (!document.hidden) load({ silent: true });
    }, REFRESH_MS);
  }

  return { init, reload: () => load() };
})();
