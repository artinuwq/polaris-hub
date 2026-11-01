/* ─── Settings Module ───
   Вкладки: «Почта» (ящики mail.ru / Google и получатели уведомлений:
   личка, группа, тема группы) и «Система» (обновление и перезапуск). */
window.SettingsModule = (() => {
  'use strict';

  const REFRESH_MS = 10000;
  const HINTS = {
    mailru: 'Нужен пароль для внешнего приложения: Mail.ru → Настройки → Безопасность → «Пароли для внешних приложений». IMAP должен быть включён.',
    gmail: 'Нужен пароль приложения Google (включите двухэтапную аутентификацию): аккаунт Google → Безопасность → «Пароли приложений». Пробелы в пароле можно оставить.',
  };

  let container = null;
  let timer = null;
  let data = { providers: [], accounts: [], destinations: [] };
  let loaded = false;
  let error = '';
  let passwordEditId = null;
  let tab = 'mail';       // 'mail' | 'system'
  let mailTab = 'accounts'; // 'accounts' | 'dests'

  const ICONS = {
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>',
  };

  function hydrateIcons(root) {
    root.querySelectorAll('[data-icon]').forEach((node) => {
      node.innerHTML = ICONS[node.dataset.icon] || '';
    });
  }

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

  async function api(path, method = 'GET', body = undefined) {
    const opts = { method, headers: requestHeaders() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const response = await fetch(path, opts);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail || payload.message || `HTTP ${response.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return payload.data || {};
  }

  const isLive = () => Boolean(container && container.isConnected);
  const $ = (id) => container.querySelector(`#${id}`);

  function notify(text, kind = 'info') {
    const box = container && $('st-msg');
    if (!box) return;
    box.className = `st-msg is-${kind}`;
    box.textContent = text;
    box.hidden = !text;
  }

  /* ─── Разбор адреса получателя (подсказка до отправки; сервер проверяет сам) ─── */
  function previewTarget(raw) {
    const text = String(raw || '').trim().replace(/[\u2212\u2013]/g, '-');
    if (!text) return '';
    const superId = (d) => (d.startsWith('100') && d.length >= 13 ? `-${d}` : `-100${d}`);
    let m = text.match(/^(?:https?:\/\/)?(?:www\.)?(?:t|telegram)\.me\/c\/(\d{1,15})\/(\d{1,12})(?:\/\d{1,12})?\/?(?:[?#].*)?$/i);
    if (m) return `Тема ${m[2]} в группе ${superId(m[1])}`;
    if (/^@[A-Za-z][A-Za-z0-9_]{3,31}$/.test(text)) return `Канал/группа ${text}`;
    m = text.match(/^(-?\d{1,16})(?:\/(\d{1,12}))?$/);
    if (!m) return '⚠ Не похоже на ID, группу, тему или ссылку t.me/c/…';
    const neg = m[1].startsWith('-');
    const digits = m[1].replace('-', '');
    if (m[2]) return `Тема ${m[2]} в группе ${neg ? m[1] : superId(digits)}`;
    return neg ? `Группа ${m[1]}` : `Личные сообщения пользователю ${m[1]}`;
  }

  /* ─── Data ─── */
  async function load() {
    try {
      data = await api('/api/mail/overview');
      error = '';
      loaded = true;
    } catch (err) {
      error = err.message || 'Не удалось загрузить настройки';
    }
    if (isLive()) renderLists();
  }

  async function act(fn, okText) {
    try {
      await fn();
      if (okText) notify(okText, 'ok');
      await load();
      return true;
    } catch (err) {
      notify(err.message || 'Ошибка', 'error');
      return false;
    }
  }

  /* ─── Render ─── */
  function relTime(iso) {
    if (!iso) return '—';
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function providerLabel(id) {
    return (data.providers.find((p) => p.id === id) || {}).label || id;
  }

  const KIND = { user: 'Личные сообщения', group: 'Группа', topic: 'Тема группы', channel: 'Канал / группа' };

  function badge(kind, text) {
    return `<span class="st-badge is-${kind}">${escapeHTML(text)}</span>`;
  }

  function accountBadge(a) {
    if (!a.enabled) return badge('off', 'Выключен');
    if (a.last_status === 'ok') return badge('ok', 'Работает');
    if (a.last_status === 'error') return badge('err', 'Ошибка');
    return badge('warn', 'Подключаюсь…');
  }

  function destBadge(d) {
    if (!d.enabled) return badge('off', 'Выключен');
    if (d.last_status === 'ok') return badge('ok', 'Доставляется');
    if (d.last_status === 'error') return badge('err', 'Ошибка');
    return badge('warn', 'Не проверен');
  }

  const skeleton = '<div class="card st-item"><div class="skeleton-line"></div><div class="skeleton-line"></div></div>';

  function renderAccounts() {
    if (!loaded) return error ? `<p class="st-error">${escapeHTML(error)}</p>` : skeleton;
    if (!data.accounts.length) return '<p class="st-empty">Ящиков пока нет. Добавьте первый — письма появятся в разделе Email.</p>';
    return data.accounts.map((a) => `
      <article class="card st-item" data-id="${a.id}">
        <div class="st-item-head">
          <span class="st-item-title">${escapeHTML(a.label || a.email)}</span>
          ${accountBadge(a)}
        </div>
        <div class="st-item-sub">${escapeHTML(providerLabel(a.provider))} · ${escapeHTML(a.email)}${a.enabled && a.last_checked_at ? ` · проверено ${escapeHTML(relTime(a.last_checked_at))}` : ''}</div>
        ${a.last_status === 'error' && a.enabled ? `<div class="st-item-error">${escapeHTML(a.last_error)}</div>` : ''}
        ${passwordEditId === a.id ? `
          <div class="st-item-inline">
            <input class="st-input" id="st-newpass-${a.id}" type="password" autocomplete="new-password" placeholder="Новый пароль приложения" />
            <div class="st-item-actions">
              <button type="button" class="st-btn primary" data-st="acc-pass-save" data-id="${a.id}">Сохранить</button>
              <button type="button" class="st-btn" data-st="acc-pass" data-id="${a.id}">Отмена</button>
            </div>
          </div>` : `
          <div class="st-item-actions">
            <button type="button" class="st-btn" data-st="acc-toggle" data-id="${a.id}" data-enabled="${a.enabled ? '0' : '1'}">${a.enabled ? 'Выключить' : 'Включить'}</button>
            <button type="button" class="st-btn" data-st="acc-pass" data-id="${a.id}">Пароль</button>
            <button type="button" class="st-btn danger" data-st="acc-del" data-id="${a.id}">Удалить</button>
          </div>`}
      </article>`).join('');
  }

  function renderDestinations() {
    if (!loaded) return error ? `<p class="st-error">${escapeHTML(error)}</p>` : skeleton;
    if (!data.destinations.length) return '<p class="st-empty">Получателей нет — письма будут только в разделе Email. Добавьте получателя, чтобы они приходили в Telegram.</p>';
    return data.destinations.map((d) => `
      <article class="card st-item" data-id="${d.id}">
        <div class="st-item-head">
          <span class="st-item-title">${escapeHTML(d.label)}</span>
          ${destBadge(d)}
        </div>
        <div class="st-item-sub">${escapeHTML(KIND[d.kind] || d.kind)} · <code>${escapeHTML(d.chat_id)}</code>${d.thread_id ? ` · тема ${d.thread_id}` : ''}</div>
        <div class="st-item-sub">отправлено: ${d.sent_count}${d.pending_count ? ` · в очереди: ${d.pending_count}` : ''}${d.failed_count ? ` · не доставлено: ${d.failed_count}` : ''}</div>
        ${d.last_status === 'error' && d.enabled ? `<div class="st-item-error">${escapeHTML(d.last_error)}</div>` : ''}
        <div class="st-item-actions">
          <button type="button" class="st-btn" data-st="dest-test" data-id="${d.id}">Тест</button>
          <button type="button" class="st-btn" data-st="dest-toggle" data-id="${d.id}" data-enabled="${d.enabled ? '0' : '1'}">${d.enabled ? 'Выключить' : 'Включить'}</button>
          <button type="button" class="st-btn danger" data-st="dest-del" data-id="${d.id}">Удалить</button>
        </div>
      </article>`).join('');
  }

  function renderLists() {
    if (!container) return;
    const list = $('st-list');
    if (list) list.innerHTML = mailTab === 'dests' ? renderDestinations() : renderAccounts();
  }

  function renderTabs() {
    const t = (id, label) => `<button type="button" class="st-tab${tab === id ? ' is-active' : ''}" data-st="tab" data-tab="${id}">${label}</button>`;
    return `<div class="st-tabs">${t('mail', 'Почта')}${t('system', 'Система')}</div>`;
  }

  function renderMailSubTabs() {
    const t = (id, label) => `<button type="button" class="st-tab${mailTab === id ? ' is-active' : ''}" data-st="mail-tab" data-tab="${id}">${label}</button>`;
    return `<div class="st-tabs is-sub">${t('accounts', 'Ящики')}${t('dests', 'Получатели')}</div>`;
  }

  function renderMailBody() {
    const isDests = mailTab === 'dests';
    return `
      ${renderMailSubTabs()}
      <button type="button" class="st-add-button" data-st="open-add">
        <span class="icon" data-icon="plus" aria-hidden="true"></span>
        <span>${isDests ? 'Добавить получателя' : 'Добавить ящик'}</span>
      </button>
      <div id="st-list" class="st-list"></div>`;
  }

  function renderShell() {
    const body = tab === 'system'
      ? (window.PolarisUpdateUI ? window.PolarisUpdateUI.render() : '')
      : renderMailBody();
    container.innerHTML = `
      ${renderTabs()}
      <div id="st-msg" class="st-msg" hidden></div>
      ${body}`;
    hydrateIcons(container);
    renderLists();
  }

  /* ─── Маленькие модалки (общий вид с задачами) ─── */
  let overlayEl = null;

  function closeModal() {
    if (!overlayEl) return;
    overlayEl.remove();
    overlayEl = null;
    window.PolarisBack?.pop('settings-modal');
  }

  function openModal(html, onClick) {
    closeModal();
    overlayEl = document.createElement('div');
    overlayEl.className = 'tk-overlay';
    overlayEl.innerHTML = `<div class="tk-modal" role="dialog" aria-modal="true">${html}</div>`;
    const openedAt = Date.now();
    overlayEl.addEventListener('click', (e) => {
      if (e.target === overlayEl && Date.now() - openedAt > 350) { closeModal(); return; }
      const btn = e.target.closest('[data-sm]');
      if (btn) { e.preventDefault(); onClick(btn.dataset.sm, btn); }
    });
    document.body.appendChild(overlayEl);
    window.PolarisBack?.push('settings-modal', closeModal);
    return overlayEl;
  }

  const modalVal = (id) => overlayEl?.querySelector(`#${id}`)?.value ?? '';
  const modalError = (text) => {
    const box = overlayEl?.querySelector('#sm-error');
    if (box) { box.textContent = text; box.hidden = !text; }
  };

  function openAccountModal() {
    const providers = data.providers.length ? data.providers : [{ id: 'mailru', label: 'Mail.ru' }, { id: 'gmail', label: 'Google' }];
    const el = openModal(`
      <div class="tk-head"><h3>Добавить ящик</h3></div>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-field"><label>Провайдер</label>
        <select id="sm-provider">${providers.map((p) => `<option value="${escapeHTML(p.id)}">${escapeHTML(p.label)}</option>`).join('')}</select>
      </div>
      <div class="tk-field"><label>Email</label><input id="sm-email" type="email" autocomplete="off" placeholder="адрес@почты" /></div>
      <div class="tk-field"><label>Пароль приложения</label><input id="sm-password" type="password" autocomplete="new-password" placeholder="Пароль приложения" /></div>
      <div class="tk-field"><label>Название (необязательно)</label><input id="sm-label" type="text" maxlength="80" placeholder="Например, Рабочая" /></div>
      <p id="sm-hint" class="st-modal-hint">${escapeHTML(HINTS[providers[0].id] || '')}</p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="acc-submit">Добавить</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'acc-submit') return;
      const body = { provider: modalVal('sm-provider'), email: modalVal('sm-email').trim(), password: modalVal('sm-password'), label: modalVal('sm-label').trim() };
      if (!body.email || !body.password) { modalError('Заполните email и пароль'); return; }
      btn.disabled = true;
      btn.textContent = 'Проверяю вход…';
      modalError('');
      try {
        await api('/api/mail/accounts', 'POST', body);
        closeModal();
        notify('Ящик добавлен. Письма появятся в разделе Email.', 'ok');
        await load();
      } catch (err) {
        modalError(err.message || 'Не удалось добавить ящик');
        btn.disabled = false;
        btn.textContent = 'Добавить';
      }
    });
    el.querySelector('#sm-provider').addEventListener('change', (e) => {
      el.querySelector('#sm-hint').textContent = HINTS[e.target.value] || '';
    });
    el.querySelector('#sm-email').focus();
  }

  function openDestinationModal() {
    const el = openModal(`
      <div class="tk-head"><h3>Добавить получателя</h3></div>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-field"><label>Куда отправлять</label>
        <input id="sm-target" type="text" autocomplete="off" placeholder="ID, -100…, 3281520729/1279 или ссылка t.me/c/…" />
        <span id="sm-preview" class="st-modal-hint">&nbsp;</span>
      </div>
      <div class="tk-field"><label>Название (необязательно)</label><input id="sm-dlabel" type="text" maxlength="80" placeholder="Например, Группа проекта" /></div>
      <ul class="st-formats">
        <li><code>123456789</code> — личные сообщения (сначала нажмите /start у бота)</li>
        <li><code>-1003281520729</code> — группа</li>
        <li><code>-1003281520729/1279</code> или ссылка <code>t.me/c/…</code> — тема группы</li>
      </ul>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="dest-submit">Добавить</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'dest-submit') return;
      const body = { target: modalVal('sm-target').trim(), label: modalVal('sm-dlabel').trim() };
      if (!body.target) { modalError('Укажите, куда отправлять'); return; }
      btn.disabled = true;
      modalError('');
      try {
        await api('/api/mail/destinations', 'POST', body);
        closeModal();
        notify('Получатель добавлен. Нажмите «Тест», чтобы проверить доставку.', 'ok');
        await load();
      } catch (err) {
        modalError(err.message || 'Не удалось добавить получателя');
        btn.disabled = false;
      }
    });
    el.querySelector('#sm-target').addEventListener('input', (e) => {
      el.querySelector('#sm-preview').textContent = previewTarget(e.target.value) || '\u00a0';
    });
    el.querySelector('#sm-target').focus();
  }

  function askConfirm(text) {
    return new Promise((resolve) => {
      const tg = window.Telegram?.WebApp;
      if (tg?.initData && typeof tg.showConfirm === 'function') {
        try { tg.showConfirm(text, (ok) => resolve(Boolean(ok))); return; } catch (e) { /* fallback */ }
      }
      resolve(window.confirm(text));
    });
  }

  /* ─── Events ─── */
  async function onClick(event) {
    const btn = event.target.closest('[data-st]');
    if (!btn || !container.contains(btn)) return;
    event.preventDefault();
    const id = Number(btn.dataset.id);

    switch (btn.dataset.st) {
      case 'tab':
        tab = btn.dataset.tab === 'system' ? 'system' : 'mail';
        renderShell();
        break;
      case 'mail-tab':
        mailTab = btn.dataset.tab === 'dests' ? 'dests' : 'accounts';
        renderShell();
        break;
      case 'open-add':
        if (mailTab === 'dests') openDestinationModal(); else openAccountModal();
        break;
      case 'acc-toggle':
        await act(() => api(`/api/mail/accounts/${id}`, 'PATCH', { enabled: btn.dataset.enabled === '1' }));
        break;
      case 'acc-pass':
        passwordEditId = passwordEditId === id ? null : id;
        renderLists();
        break;
      case 'acc-pass-save': {
        const input = $(`st-newpass-${id}`);
        const password = input ? input.value : '';
        btn.disabled = true;
        notify('Проверяю новый пароль…', 'info');
        const ok = await act(() => api(`/api/mail/accounts/${id}`, 'PATCH', { password }), 'Пароль обновлён.');
        btn.disabled = false;
        if (ok) { passwordEditId = null; renderLists(); }
        break;
      }
      case 'acc-del':
        if (await askConfirm('Удалить ящик вместе с сохранёнными письмами?')) {
          await act(() => api(`/api/mail/accounts/${id}`, 'DELETE'), 'Ящик удалён.');
        }
        break;
      case 'dest-toggle':
        await act(() => api(`/api/mail/destinations/${id}`, 'PATCH', { enabled: btn.dataset.enabled === '1' }));
        break;
      case 'dest-test':
        btn.disabled = true;
        notify('Отправляю тестовое сообщение…', 'info');
        await act(() => api(`/api/mail/destinations/${id}/test`, 'POST'), 'Тест отправлен — проверьте чат.');
        btn.disabled = false;
        break;
      case 'dest-del':
        if (await askConfirm('Удалить получателя?')) {
          await act(() => api(`/api/mail/destinations/${id}`, 'DELETE'), 'Получатель удалён.');
        }
        break;
      default: break;
    }
  }

  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    clearInterval(timer);
    container = el;
    renderShell();
    container.addEventListener('click', onClick);
    load();
    timer = setInterval(() => {
      if (!isLive()) { clearInterval(timer); timer = null; return; }
      if (!document.hidden && passwordEditId === null && !overlayEl) load();
    }, REFRESH_MS);
  }

  // Уход со страницы: закрываем модалку и снимаем «Назад»
  function leave() { closeModal(); }

  return { init, leave };
})();
