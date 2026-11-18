/* Polaris Meme — личная библиотека изображений (ищется в Telegram через @бот запрос, см. docs/meme.md).
   Список с предпросмотром, поиск по названию/тегам/описанию, добавление, правка названия/тегов/описания, удаление.
   Ходит в /api/meme/* (только админ). Картинки показываются по подписанным ссылкам из ответа API. */
window.MemeModule = (() => {
  'use strict';

  const PAGE = 24;
  const ACCEPT = 'image/*';  // тип проверяет сервер по байтам; список MIME в accept на части мобильных WebView не открывает выбор
  const MAX_BYTES = 10 * 1024 * 1024;
  const ICON_EDIT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z"></path><path d="m13.5 6.5 4 4"></path></svg>';
  const ICON_TRASH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16"></path><path d="M9 7V4h6v3"></path><path d="M6 7l1 13h10l1-13"></path><path d="M10 11v6M14 11v6"></path></svg>';

  const ICON_GEAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4h4l.7 2.1 2.2 1 2.1-.5 2 3.5-1.5 1.6v2l1.5 1.6-2 3.5-2.1-.5-2.2 1L14 20h-4l-.7-2.1-2.2-1-2.1.5-2-3.5 1.5-1.6v-2L2 9.7l2-3.5 2.1.5 2.2-1L10 4z"></path><circle cx="12" cy="12" r="3"></circle></svg>';

  let container = null;
  let boundTo = null;
  let overlayEl = null;
  let loadToken = 0;
  let searchTimer = null;
  let lastImageRefresh = 0;
  let notice = null;

  const S = { items: [], total: 0, nextOffset: null, q: '', tag: '', tags: [], loading: false, loaded: false, error: '' };

  /* ─── утилиты ─── */
  const escapeHTML = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const errText = (e) => (e && e.message) || 'Неизвестная ошибка';

  function fmtSize(bytes) {
    const units = ['Б', 'КиБ', 'МиБ'];
    let value = Number(bytes) || 0, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${i === 0 ? value : value.toFixed(value >= 10 ? 1 : 2)} ${units[i]}`;
  }

  function authHeaders() {
    const h = {};
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, method = 'GET', body = undefined, extraHeaders = {}) {
    const opts = { method, headers: { ...authHeaders(), ...extraHeaders }, cache: 'no-store' };
    if (body !== undefined) {
      if (body instanceof Blob) { opts.body = body; } else {
        opts.body = JSON.stringify(body);
        opts.headers['Content-Type'] = 'application/json';
      }
    }
    let response;
    try {
      response = await fetch(path, opts);
    } catch (e) {
      throw new ApiError('Нет связи с сервером Polaris', 0);
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
      throw new ApiError(typeof detail === 'object' ? (detail.message || 'Ошибка запроса') : String(detail), response.status);
    }
    return payload.data ?? payload;
  }

  const parseTags = (text) => String(text || '').split(/[,\n]/).map((t) => t.trim().replace(/^#/, '').trim()).filter(Boolean);

  function setNotice(kind, text) { notice = text ? { kind, text } : null; renderNotice(); }

  function renderNotice() {
    const box = container?.querySelector('#mm-msg');
    if (!box) return;
    box.hidden = !notice;
    box.className = `st-msg${notice ? ` is-${notice.kind}` : ''}`;
    box.textContent = notice ? notice.text : '';
  }

  /* ─── модальные окна ─── */
  function closeModal() {
    if (!overlayEl) return;
    overlayEl.remove();
    overlayEl = null;
    window.PolarisBack?.pop('meme-modal');
  }

  function openModal(html, onAction) {
    closeModal();
    overlayEl = document.createElement('div');
    overlayEl.className = 'tk-overlay';
    overlayEl.innerHTML = `<div class="tk-modal mm-modal" role="dialog" aria-modal="true">${html}</div>`;
    const openedAt = Date.now();
    overlayEl.addEventListener('click', (e) => {
      if (e.target === overlayEl && Date.now() - openedAt > 350) { closeModal(); return; }
      const btn = e.target.closest('[data-mmm]');
      if (btn) { e.preventDefault(); onAction(btn.dataset.mmm, btn); }
    });
    document.body.appendChild(overlayEl);
    window.PolarisBack?.push('meme-modal', closeModal);
    return overlayEl;
  }

  function modalError(text) {
    const box = overlayEl?.querySelector('#mmm-error');
    if (box) { box.textContent = text || ''; box.hidden = !text; }
  }

  const fields = (v = {}) => `
    <div class="tk-field"><label for="mmm-title">Название</label>
      <input id="mmm-title" type="text" maxlength="120" autocomplete="off" placeholder="Спокойной ночи с котиком" value="${escapeHTML(v.title || '')}" /></div>
    <div class="tk-field"><label for="mmm-tags">Теги</label>
      <input id="mmm-tags" type="text" autocomplete="off" placeholder="спокойной ночи, котик, сон, милота" value="${escapeHTML((v.tags || []).join(', '))}" /></div>
    <div class="tk-field"><label for="mmm-desc">Описание</label>
      <textarea id="mmm-desc" rows="3" maxlength="500" placeholder="Необязательно: по нему тоже ищется">${escapeHTML(v.description || '')}</textarea></div>
    <p id="mmm-error" class="st-modal-error" hidden></p>`;

  const readFields = () => ({
    title: overlayEl.querySelector('#mmm-title').value.trim(),
    tags: parseTags(overlayEl.querySelector('#mmm-tags').value),
    description: overlayEl.querySelector('#mmm-desc').value.trim(),
  });

  function openAdd() {
    let file = null;
    let previewUrl = '';
    openModal(`
      <div class="tk-head"><h3>Новое изображение</h3></div>
      <div class="tk-field"><label>Файл (JPEG, PNG, WebP, GIF до 10 МиБ)</label>
        <div class="mm-pick">
          <button type="button" class="tk-btn" data-mmm="pick">Выбрать файл</button>
          <span id="mmm-file-name" class="st-hint">Файл не выбран</span>
        </div>
        <input id="mmm-file" type="file" accept="${ACCEPT}" hidden /></div>
      <img id="mmm-preview" class="mm-preview" alt="" hidden />
      ${fields()}
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-mmm="submit">Добавить</button>
        <button type="button" class="tk-btn" data-mmm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      // как в разделе Files: скрытый input открывается кликом по обычной кнопке (нативный input в Mini App ненадёжен)
      if (action === 'pick') { overlayEl.querySelector('#mmm-file').click(); return; }
      if (action !== 'submit') return;
      if (!file) { modalError('Выберите файл изображения'); return; }
      const v = readFields();
      btn.disabled = true;
      modalError('');
      try {
        const qs = new URLSearchParams({ title: v.title, tags: v.tags.join(','), description: v.description, filename: file.name });
        await api(`/api/meme?${qs}`, 'POST', file, { 'Content-Type': file.type || 'application/octet-stream' });
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        closeModal();
        await load();
        setNotice('ok', 'Изображение добавлено');
      } catch (e) {
        modalError(errText(e));
        btn.disabled = false;
      }
    });
    overlayEl.querySelector('#mmm-file').addEventListener('change', (e) => {
      file = e.target.files?.[0] || null;
      const img = overlayEl.querySelector('#mmm-preview');
      const label = overlayEl.querySelector('#mmm-file-name');
      if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = ''; }
      if (label) label.textContent = file ? `${file.name} · ${fmtSize(file.size)}` : 'Файл не выбран';
      if (!file) { img.hidden = true; return; }
      if (file.size > MAX_BYTES) { modalError('Файл больше 10 МиБ — это предел Telegram для изображений'); file = null; e.target.value = ''; img.hidden = true; if (label) label.textContent = 'Файл не выбран'; return; }
      modalError('');
      previewUrl = URL.createObjectURL(file);
      img.src = previewUrl;
      img.hidden = false;
      const title = overlayEl.querySelector('#mmm-title');
      if (!title.value.trim()) title.value = file.name.replace(/\.[^.]+$/, '').replace(/[_]+/g, ' ').trim();
    });
  }

  function openEdit(item) {
    openModal(`
      <div class="tk-head"><h3>Изменить</h3></div>
      <img class="mm-preview" src="${escapeHTML(item.image_url)}" alt="${escapeHTML(item.title)}" />
      ${fields(item)}
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-mmm="submit">Сохранить</button>
        <button type="button" class="tk-btn" data-mmm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'submit') return;
      btn.disabled = true;
      try {
        await api(`/api/meme/${encodeURIComponent(item.id)}`, 'PATCH', readFields());
        closeModal();
        await load();
        setNotice('ok', 'Сохранено');
      } catch (e) {
        modalError(errText(e));
        btn.disabled = false;
      }
    });
  }

  function openDelete(item) {
    openModal(`
      <div class="tk-head"><h3>Удалить изображение?</h3></div>
      <p class="tk-desc">«${escapeHTML(item.title)}» будет удалено из библиотеки и из Polaris Storage.</p>
      <p id="mmm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn danger" data-mmm="confirm">Удалить</button>
        <button type="button" class="tk-btn" data-mmm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'confirm') return;
      btn.disabled = true;
      try {
        await api(`/api/meme/${encodeURIComponent(item.id)}`, 'DELETE');
        closeModal();
        await load();
        setNotice('ok', 'Удалено');
      } catch (e) {
        modalError(errText(e));
        btn.disabled = false;
      }
    });
  }

  function openView(item) {
    const chips = item.tags.map((t) => `<span class="mm-chip">#${escapeHTML(t)}</span>`).join('');
    openModal(`
      <div class="tk-head"><h3>${escapeHTML(item.title)}</h3></div>
      <img class="mm-preview is-large" src="${escapeHTML(item.image_url)}" alt="${escapeHTML(item.title)}" />
      ${item.description ? `<p class="tk-desc">${escapeHTML(item.description)}</p>` : ''}
      ${chips ? `<div class="mm-tags">${chips}</div>` : ''}
      <p class="tk-desc">${escapeHTML(`${item.width}×${item.height} · ${fmtSize(item.size_bytes)} · ${item.mime_type}`)}</p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn" data-mmm="close">Закрыть</button>
      </div></div>`, (action) => { if (action === 'close') closeModal(); });
  }

  /* ─── настройки (шестерёнка) ─── */
  function settingsBody(d) {
    let where;
    if (!d.bot_configured) where = 'Не задан TELEGRAM_BOT_TOKEN — отправка в Telegram невозможна.';
    else if (d.mode === 'chat') where = `Сейчас используется: ${d.effective_chat} (${d.source === 'env' ? 'из .env, MEME_CACHE_CHAT_ID' : 'из этих настроек'}).`;
    else if (d.mode === 'admin_dm') where = 'Чат не задан: копия приходит вам в личку и сразу удаляется.';
    else where = 'Некуда отправлять: задайте чат или TELEGRAM_ADMIN_IDS.';
    const pct = d.total ? `${d.ready} из ${d.total}` : 'пока пусто';
    return `
      <div class="mm-set">
        <h4>Чат для inline-кэша</h4>
        <p class="tk-desc">Чтобы отправлять картинку в любой чат, Telegram нужен её file_id. Polaris получает его, один раз отправив
          копию в этот чат. Создайте закрытый чат или канал, добавьте туда бота (в канале — администратором с правом публикации)
          и впишите числовой id (обычно начинается с -100) либо @username публичного канала. Сообщения в нём остаются.</p>
        <div class="tk-field"><label for="mmm-chat">ID чата или канала</label>
          <input id="mmm-chat" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="-1001234567890" value="${escapeHTML(d.cache_chat_id)}" /></div>
        <p class="st-hint">${escapeHTML(where)}</p>
        <div class="tk-actions-row">
          <button type="button" class="tk-btn primary" data-mmm="save-chat">Сохранить</button>
          <button type="button" class="tk-btn" data-mmm="test-chat">Проверить</button>
        </div>
      </div>
      <div class="mm-set">
        <h4>Inline-кэш</h4>
        <p class="tk-desc">Готово к отправке из Telegram: ${escapeHTML(pct)}.${d.pending ? ` Без кэша: ${d.pending} — они заработают при первом поиске или сразу по кнопке ниже.` : ''}</p>
        <div class="tk-actions-row">
          <button type="button" class="tk-btn" data-mmm="sync" ${d.pending ? '' : 'disabled'}>Обновить кэш${d.pending ? ` (${d.pending})` : ''}</button>
        </div>
        <p id="mmm-sync" class="st-hint"></p>
      </div>
      <p class="st-hint">Запасной путь по URL для JPEG ${d.fallback_url ? 'включён' : 'выключен: нужен публичный https в WEBAPP_URL'}.</p>`;
  }

  async function openSettings() {
    let data = null;
    let busy = false;
    const note = (kind, text) => {
      const box = overlayEl?.querySelector('#mmm-note');
      if (box) { box.textContent = text || ''; box.className = `st-msg${text ? ` is-${kind}` : ''}`; box.hidden = !text; }
    };
    const paint = () => {
      const body = overlayEl?.querySelector('#mmm-set-body');
      if (body && data) body.innerHTML = settingsBody(data);
    };
    const chatValue = () => overlayEl.querySelector('#mmm-chat')?.value.trim() || '';

    openModal(`
      <div class="tk-head"><h3>Настройки Polaris Meme</h3></div>
      <div id="mmm-set-body"><p class="st-empty">Загрузка…</p></div>
      <div id="mmm-note" class="st-msg" hidden></div>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn" data-mmm="close">Закрыть</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); load(); return; }
      if (!data || busy) return;
      busy = true;
      note('', '');
      const mine = overlayEl;
      try {
        if (action === 'save-chat') {
          data = await api('/api/meme/settings', 'PUT', { cache_chat_id: chatValue() });
          paint();
          note('ok', 'Сохранено');
        } else if (action === 'test-chat') {
          btn.disabled = true;
          const r = await api('/api/meme/settings/test', 'POST', { cache_chat_id: chatValue() });
          note('ok', `Бот видит «${r.title}» и может туда писать`);
          btn.disabled = false;
        } else if (action === 'sync') {
          btn.disabled = true;
          const skip = [];
          let done = 0;
          let firstError = '';
          for (;;) {
            const r = await api(`/api/meme/settings/sync?limit=10&skip=${encodeURIComponent(skip.join(','))}`, 'POST');
            done += r.done;
            skip.push(...r.failed_ids);
            firstError = firstError || r.error;
            if (overlayEl !== mine) return;  // окно закрыли — прекращаем
            const line = overlayEl.querySelector('#mmm-sync');
            if (line) line.textContent = `Обновлено: ${done}, осталось: ${r.remaining}${skip.length ? `, не удалось: ${skip.length}` : ''}…`;
            if (!r.remaining || (!r.done && !r.failed)) break;
          }
          data = await api('/api/meme/settings');
          paint();
          const line = overlayEl?.querySelector('#mmm-sync');
          if (line) line.textContent = '';
          if (skip.length) note('error', `Обновлено ${done}, не удалось ${skip.length}${firstError ? `: ${firstError}` : ''}`);
          else note('ok', `Готово, обновлено: ${done}`);
        }
      } catch (e) {
        if (overlayEl === mine) { note('error', errText(e)); btn.disabled = false; }
      }
      busy = false;
    });

    try {
      data = await api('/api/meme/settings');
      paint();
    } catch (e) {
      const body = overlayEl?.querySelector('#mmm-set-body');
      if (body) body.innerHTML = `<p class="st-error">${escapeHTML(errText(e))}</p>`;
    }
  }

  /* ─── отрисовка ─── */
  function cardHTML(item) {
    const chips = item.tags.map((t) => `<button type="button" class="mm-chip" data-mm="tag" data-tag="${escapeHTML(t)}">#${escapeHTML(t)}</button>`).join('');
    return `
      <article class="mm-card card" data-id="${escapeHTML(item.id)}">
        <button type="button" class="mm-thumb" data-mm="view" data-id="${escapeHTML(item.id)}" aria-label="Открыть «${escapeHTML(item.title)}»">
          <img src="${escapeHTML(item.image_url)}" alt="${escapeHTML(item.title)}" loading="lazy" />
        </button>
        <div class="mm-info">
          <h4 class="mm-title">${escapeHTML(item.title)}</h4>
          ${chips ? `<div class="mm-tags">${chips}</div>` : ''}
          ${item.telegram_ready ? '' : '<span class="mm-state" title="Бот получит file_id при первом поиске в Telegram">ещё не в inline-кэше</span>'}
        </div>
        <div class="mm-actions">
          <button type="button" class="mm-ibtn" data-mm="edit" data-id="${escapeHTML(item.id)}" aria-label="Изменить" title="Изменить">${ICON_EDIT}</button>
          <button type="button" class="mm-ibtn is-danger" data-mm="delete" data-id="${escapeHTML(item.id)}" aria-label="Удалить" title="Удалить">${ICON_TRASH}</button>
        </div>
      </article>`;
  }

  function bodyHTML() {
    if (S.error) return `<p class="st-error">${escapeHTML(S.error)}</p><button type="button" class="st-btn" data-mm="reload">Повторить</button>`;
    if (!S.loaded) return '<p class="st-empty">Загрузка…</p>';
    if (!S.items.length) {
      return S.q || S.tag
        ? '<p class="st-empty">Ничего не найдено. Попробуйте другое слово или сбросьте фильтр.</p>'
        : '<p class="st-empty">Библиотека пуста. Добавьте первое изображение — потом его можно найти в любом чате через @бота.</p>';
    }
    const more = S.nextOffset !== null ? `<button type="button" class="st-btn mm-more" data-mm="more" ${S.loading ? 'disabled' : ''}>Показать ещё</button>` : '';
    return `<div class="mm-grid">${S.items.map(cardHTML).join('')}</div>${more}`;
  }

  function tagsHTML() {
    if (!S.tags.length) return '';
    const chips = S.tags.map((t) => `<button type="button" class="mm-chip${S.tag === t.tag ? ' is-active' : ''}" data-mm="filter" data-tag="${escapeHTML(t.tag)}">#${escapeHTML(t.tag)} <small>${t.count}</small></button>`).join('');
    return `<div class="mm-tags mm-filter">${chips}${S.tag ? '<button type="button" class="mm-chip" data-mm="clear-tag">× сбросить</button>' : ''}</div>`;
  }

  function render() {
    if (!container) return;
    const hadFocus = document.activeElement?.id === 'mm-search';
    const caret = hadFocus ? document.activeElement.selectionStart : 0;
    container.innerHTML = `
      <div id="mm-msg" class="st-msg" hidden></div>
      <div class="mm-toolbar">
        <input id="mm-search" class="st-input" type="search" placeholder="Поиск по названию, тегам и описанию" autocomplete="off" value="${escapeHTML(S.q)}" />
        <button type="button" class="st-btn primary" data-mm="add">Добавить</button>
        <button type="button" class="st-btn mm-gear" data-mm="settings" aria-label="Настройки" title="Настройки">${ICON_GEAR}</button>
      </div>
      <p class="st-hint">${S.loaded ? `Найдено: ${S.total}` : ''}</p>
      ${tagsHTML()}
      ${bodyHTML()}`;
    renderNotice();
    if (hadFocus) {
      const input = container.querySelector('#mm-search');
      input.focus();
      input.setSelectionRange(caret, caret);
    }
  }

  /* ─── данные ─── */
  async function load({ append = false } = {}) {
    const token = ++loadToken;
    S.loading = true;
    S.error = '';
    if (!append) render();
    try {
      const qs = new URLSearchParams({ limit: String(PAGE), offset: String(append ? S.items.length : 0) });
      if (S.q) qs.set('q', S.q);
      if (S.tag) qs.set('tag', S.tag);
      const data = await api(`/api/meme?${qs}`);
      if (token !== loadToken || !container) return;
      S.items = append ? S.items.concat(data.items) : data.items;
      S.total = data.total;
      S.nextOffset = data.next_offset;
      S.tags = data.tags || [];
      S.loaded = true;
    } catch (e) {
      if (token !== loadToken || !container) return;
      S.error = errText(e);
    }
    S.loading = false;
    render();
  }

  function byId(id) { return S.items.find((m) => m.id === id); }

  /* ─── события ─── */
  function onClick(e) {
    const btn = e.target.closest('[data-mm]');
    if (!btn) return;
    const item = btn.dataset.id ? byId(btn.dataset.id) : null;
    switch (btn.dataset.mm) {
      case 'add': openAdd(); break;
      case 'settings': openSettings(); break;
      case 'view': if (item) openView(item); break;
      case 'edit': if (item) openEdit(item); break;
      case 'delete': if (item) openDelete(item); break;
      case 'more': load({ append: true }); break;
      case 'reload': load(); break;
      case 'filter': S.tag = S.tag === btn.dataset.tag ? '' : btn.dataset.tag; load(); break;
      case 'tag': S.tag = btn.dataset.tag; load(); break;
      case 'clear-tag': S.tag = ''; load(); break;
      default: break;
    }
  }

  function onInput(e) {
    if (e.target.id !== 'mm-search') return;
    S.q = e.target.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => load(), 300);
  }

  // ссылки на картинки подписаны на час: если страница простояла дольше, обновим список (не чаще раза в минуту)
  function onImageError(e) {
    if (e.target.tagName !== 'IMG' || !container?.contains(e.target)) return;
    if (Date.now() - lastImageRefresh < 60000) return;
    lastImageRefresh = Date.now();
    load();
  }

  /* ─── жизненный цикл ─── */
  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (boundTo !== el) {
      el.addEventListener('click', onClick);
      el.addEventListener('input', onInput);
      el.addEventListener('error', onImageError, true);
      boundTo = el;
    }
    container = el;
    render();
    load();
  }

  function leave() {
    closeModal();
    clearTimeout(searchTimer);
    container = null;
    boundTo = null;
    loadToken += 1;
    Object.assign(S, { items: [], total: 0, nextOffset: null, q: '', tag: '', tags: [], loading: false, loaded: false, error: '' });
    notice = null;
  }

  return { init, leave };
})();
