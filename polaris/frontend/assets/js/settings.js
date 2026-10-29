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

  function accountStatus(a) {
    if (!a.enabled) return '<span class="badge">Выключен</span>';
    if (a.last_status === 'ok') return '<span class="badge success">Работает</span>';
    if (a.last_status === 'error') return '<span class="badge error">Ошибка</span>';
    return '<span class="badge warning">Подключаюсь…</span>';
  }

  function providerLabel(id) {
    return (data.providers.find((p) => p.id === id) || {}).label || id;
  }

  function renderAccounts() {
    if (!loaded) return error
      ? `<p class="st-error">${escapeHTML(error)}</p>`
      : '<div class="skeleton-line"></div><div class="skeleton-line"></div>';
    if (!data.accounts.length) return '<p class="metric-subtext">Ящиков пока нет — добавьте первый ниже.</p>';
    return data.accounts.map((a) => `
      <div class="st-row">
        <div class="st-row-main">
          <div class="st-row-title">${escapeHTML(a.label || a.email)} ${accountStatus(a)}</div>
          <div class="st-row-sub">${escapeHTML(providerLabel(a.provider))} · ${escapeHTML(a.email)} · писем: ${a.message_count}
            ${a.enabled && a.last_checked_at ? `· проверено ${escapeHTML(relTime(a.last_checked_at))}` : ''}</div>
          ${a.last_status === 'error' && a.enabled ? `<div class="st-row-err">${escapeHTML(a.last_error)}</div>` : ''}
          ${passwordEditId === a.id ? `
            <div class="st-inline">
              <input class="st-input" id="st-newpass-${a.id}" type="password" autocomplete="new-password" placeholder="Новый пароль приложения" />
              <button type="button" class="small-button primary" data-st="acc-pass-save" data-id="${a.id}">Сохранить</button>
              <button type="button" class="small-button ghost" data-st="acc-pass" data-id="${a.id}">Отмена</button>
            </div>` : ''}
        </div>
        <div class="st-row-actions">
          <button type="button" class="small-button" data-st="acc-toggle" data-id="${a.id}" data-enabled="${a.enabled ? '0' : '1'}">${a.enabled ? 'Выключить' : 'Включить'}</button>
          <button type="button" class="small-button" data-st="acc-pass" data-id="${a.id}">Пароль</button>
          <button type="button" class="small-button ghost" data-st="acc-del" data-id="${a.id}">Удалить</button>
        </div>
      </div>`).join('');
  }

  function destStatus(d) {
    if (!d.enabled) return '<span class="badge">Выключен</span>';
    if (d.last_status === 'ok') return '<span class="badge success">Доставляется</span>';
    if (d.last_status === 'error') return '<span class="badge error">Ошибка</span>';
    return '<span class="badge warning">Не проверен</span>';
  }

  const KIND = { user: 'Личные сообщения', group: 'Группа', topic: 'Тема группы', channel: 'Канал / группа' };

  function renderDestinations() {
    if (!loaded) return '<div class="skeleton-line"></div>';
    if (!data.destinations.length) return '<p class="metric-subtext">Получателей нет — письма будут только в разделе Email. Добавьте получателя ниже.</p>';
    return data.destinations.map((d) => `
      <div class="st-row">
        <div class="st-row-main">
          <div class="st-row-title">${escapeHTML(d.label)} ${destStatus(d)}</div>
          <div class="st-row-sub">${escapeHTML(KIND[d.kind] || d.kind)} · <code>${escapeHTML(d.chat_id)}</code>${d.thread_id ? ` · тема ${d.thread_id}` : ''}
            · отправлено: ${d.sent_count}${d.pending_count ? ` · в очереди: ${d.pending_count}` : ''}${d.failed_count ? ` · не доставлено: ${d.failed_count}` : ''}</div>
          ${d.last_status === 'error' && d.enabled ? `<div class="st-row-err">${escapeHTML(d.last_error)}</div>` : ''}
        </div>
        <div class="st-row-actions">
          <button type="button" class="small-button primary" data-st="dest-test" data-id="${d.id}">Тест</button>
          <button type="button" class="small-button" data-st="dest-toggle" data-id="${d.id}" data-enabled="${d.enabled ? '0' : '1'}">${d.enabled ? 'Выключить' : 'Включить'}</button>
          <button type="button" class="small-button ghost" data-st="dest-del" data-id="${d.id}">Удалить</button>
        </div>
      </div>`).join('');
  }

  function renderLists() {
    if (!container) return;
    const a = $('st-accounts');
    const d = $('st-dests');
    if (a) a.innerHTML = renderAccounts();
    if (d) d.innerHTML = renderDestinations();
  }

  function renderTabs() {
    const t = (id, label) => `<button type="button" class="st-tab${tab === id ? ' is-active' : ''}" data-st="tab" data-tab="${id}">${label}</button>`;
    return `<div class="st-tabs">${t('mail', 'Почта')}${t('system', 'Система')}</div>`;
  }

  function renderMailSubTabs() {
    const t = (id, label) => `<button type="button" class="st-tab${mailTab === id ? ' is-active' : ''}" data-st="mail-tab" data-tab="${id}">${label}</button>`;
    return `<div class="st-tabs is-sub">${t('accounts', 'Ящики')}${t('dests', 'Получатели')}</div>`;
  }

  function renderAccountsCard(providers) {
    return `
      <section class="card st-card">
        <div id="st-accounts"></div>
        <div class="st-form">
          <h3 class="st-subtitle">Добавить ящик</h3>
          <div class="st-grid">
            <select class="st-input" id="st-provider">${providers.map((p) => `<option value="${escapeHTML(p.id)}">${escapeHTML(p.label)}</option>`).join('')}</select>
            <input class="st-input" id="st-email" type="email" autocomplete="off" placeholder="адрес@почты" />
            <input class="st-input" id="st-password" type="password" autocomplete="new-password" placeholder="Пароль приложения" />
            <input class="st-input" id="st-label" type="text" maxlength="80" placeholder="Название (необязательно)" />
          </div>
          <p class="st-hint" id="st-provider-hint">${escapeHTML(HINTS[providers[0].id] || '')}</p>
          <button type="button" class="small-button primary" data-st="acc-add" id="st-acc-add">Добавить и проверить</button>
        </div>
      </section>`;
  }

  function renderDestsCard() {
    return `
      <section class="card st-card">
        <div id="st-dests"></div>
        <div class="st-form">
          <h3 class="st-subtitle">Добавить получателя</h3>
          <div class="st-grid">
            <input class="st-input st-wide" id="st-target" type="text" autocomplete="off" placeholder="ID, -100…, 3281520729/1279 или ссылка t.me/c/…" />
            <input class="st-input" id="st-dlabel" type="text" maxlength="80" placeholder="Название (необязательно)" />
          </div>
          <p class="st-hint" id="st-target-preview">&nbsp;</p>
          <button type="button" class="small-button primary" data-st="dest-add" id="st-dest-add">Добавить получателя</button>
        </div>
      </section>`;
  }

  function renderShell() {
    const providers = (data.providers.length ? data.providers : [{ id: 'mailru', label: 'Mail.ru' }, { id: 'gmail', label: 'Google' }]);
    let body;
    if (tab === 'system') {
      body = window.PolarisUpdateUI ? window.PolarisUpdateUI.render() : '';
    } else {
      body = `${renderMailSubTabs()}${mailTab === 'dests' ? renderDestsCard() : renderAccountsCard(providers)}`;
    }
    container.innerHTML = `
      <section class="page-intro">
        <div>
          <h1 class="page-title">Настройки</h1>
        </div>
      </section>
      ${renderTabs()}
      <div id="st-msg" class="st-msg" hidden></div>
      ${body}`;
    renderLists();
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
      case 'acc-add': {
        const body = {
          provider: $('st-provider').value,
          email: $('st-email').value.trim(),
          password: $('st-password').value,
          label: $('st-label').value.trim(),
        };
        btn.disabled = true;
        notify('Проверяю вход в ящик…', 'info');
        const ok = await act(() => api('/api/mail/accounts', 'POST', body), 'Ящик добавлен. Письма появятся в разделе Email.');
        btn.disabled = false;
        if (ok) { $('st-email').value = ''; $('st-password').value = ''; $('st-label').value = ''; }
        break;
      }
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
        if (window.confirm('Удалить ящик вместе с сохранёнными письмами?')) {
          await act(() => api(`/api/mail/accounts/${id}`, 'DELETE'), 'Ящик удалён.');
        }
        break;
      case 'dest-add': {
        const body = { target: $('st-target').value.trim(), label: $('st-dlabel').value.trim() };
        btn.disabled = true;
        const ok = await act(() => api('/api/mail/destinations', 'POST', body), 'Получатель добавлен. Нажмите «Тест», чтобы проверить доставку.');
        btn.disabled = false;
        if (ok) { $('st-target').value = ''; $('st-dlabel').value = ''; $('st-target-preview').textContent = '\u00a0'; }
        break;
      }
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
        if (window.confirm('Удалить получателя?')) {
          await act(() => api(`/api/mail/destinations/${id}`, 'DELETE'), 'Получатель удалён.');
        }
        break;
      default: break;
    }
  }

  function onInput(event) {
    if (event.target.id === 'st-target') {
      $('st-target-preview').textContent = previewTarget(event.target.value) || '\u00a0';
    }
  }

  function onChange(event) {
    if (event.target.id === 'st-provider') {
      $('st-provider-hint').textContent = HINTS[event.target.value] || '';
    }
  }

  function onKey(event) {
    if (event.key !== 'Enter') return;
    const map = { 'st-email': 'st-acc-add', 'st-password': 'st-acc-add', 'st-label': 'st-acc-add', 'st-target': 'st-dest-add', 'st-dlabel': 'st-dest-add' };
    const target = map[event.target.id];
    if (target) { event.preventDefault(); $(target).click(); }
  }

  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    clearInterval(timer);
    container = el;
    renderShell();
    container.addEventListener('click', onClick);
    container.addEventListener('input', onInput);
    container.addEventListener('change', onChange);
    container.addEventListener('keydown', onKey);
    load();
    timer = setInterval(() => {
      if (!isLive()) { clearInterval(timer); timer = null; return; }
      if (!document.hidden && passwordEditId === null) load();
    }, REFRESH_MS);
  }

  return { init };
})();
