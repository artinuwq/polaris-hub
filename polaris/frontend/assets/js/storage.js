/* ─── Storage Module ───
   Раздел «Storage»: Overview · Configuration · Buckets · Access Keys · Diagnostics.
   Данные — только из API /api/storage/* (никаких заглушек). Секреты (токен бота, Secret Access Key)
   нигде не сохраняются в состоянии модуля: секрет ключа живёт только в открытой модалке и удаляется из DOM
   при закрытии; токен бота сервер вообще не отдаёт. Весь динамический текст экранируется. */
window.StorageModule = (() => {
  'use strict';

  const SECTIONS = [
    ['overview', 'Обзор'], ['buckets', 'Бакеты'], ['keys', 'Ключи доступа'],
    ['diagnostics', 'Диагностика'], ['config', 'Конфигурация'],
  ];
  const tabIcon = (paths) => `<svg class="sg-tab-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const TAB_ICONS = {  // показываются вместо подписей, когда места мало (см. storage.css)
    overview: tabIcon('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
    buckets: tabIcon('<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6l2 13a2 2 0 0 0 2 1.7h8a2 2 0 0 0 2-1.7L20 6"/>'),
    keys: tabIcon('<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3M16 7l3 3M14 9l2 2"/>'),
    diagnostics: tabIcon('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
    config: tabIcon('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'),
  };
  const PERM_INFO = {
    ListBucket: 'список объектов и проверка bucket',
    ReadObject: 'чтение и HEAD объектов',
    WriteObject: 'запись и перезапись объектов',
    DeleteObject: 'удаление объектов',
    MultipartUpload: 'multipart-загрузка (требует WriteObject)',
  };
  const STATUS_TEXT = {
    ok: ['Работает', 'ok'], warning: ['Внимание', 'warn'], error: ['Ошибка', 'err'], unknown: ['Не проверялось', ''],
    disabled: ['Выключен', ''], unavailable: ['Недоступен', 'err'], skipped: ['Пропущено', ''],
  };
  const COMPONENTS = [
    ['core', 'Storage Core'], ['telegram', 'Telegram Backend'], ['s3', 'S3 API'], ['database', 'SQLite Database'],
  ];
  const CHECK_LABELS = {
    database: 'SQLite Storage Database', core: 'Storage Core', telegram: 'Telegram Backend', s3: 'S3 API',
    credentials: 'CredentialProvider',
  };
  const TEST_STEPS = [
    ['config', 'Конфигурация'], ['telegram', 'Telegram Backend'], ['write', 'Запись'], ['read', 'Чтение'],
    ['hash', 'SHA-256'], ['delete', 'Удаление'], ['s3', 'Полный цикл S3'],
  ];

  let container = null;
  let boundTo = null;
  let section = 'overview';
  let bucketName = null;
  let busy = {};          // ключ операции → true
  let notice = null;      // {kind: 'ok'|'error', text}
  let overlayEl = null;
  let loadToken = 0;

  const S = {             // данные разделов (только то, что отдал сервер)
    status: null, statusError: '', checking: false,
    config: null, configError: '', draft: {}, secretMode: {}, preview: null, applied: null, appliedTelegram: false, testResult: null,
    buckets: null, bucketsError: '',
    bucket: null, objects: [], objectsMore: null, objectsPrefix: '', objectsError: '',
    creds: null, credsError: '',
    diag: null, diagError: '',
  };

  /* ─── утилиты ─── */
  const escapeHTML = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');

  function fmtSize(bytes) {
    if (bytes === null || bytes === undefined) return '—';
    const units = ['Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ'];
    let value = Number(bytes), i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${i === 0 ? value : value.toFixed(value >= 10 ? 1 : 2)} ${units[i]}`;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'medium' });
  }

  function relTime(iso) {
    if (!iso) return '—';
    const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (Number.isNaN(seconds)) return '—';
    if (seconds < 60) return `${seconds} с назад`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч назад`;
    return `${Math.floor(seconds / 86400)} д назад`;
  }

  const badge = (status) => {
    const [text, kind] = STATUS_TEXT[status] || [status, ''];
    return `<span class="st-badge${kind ? ` is-${kind}` : ''}" data-status="${escapeHTML(status)}">${escapeHTML(text)}</span>`;
  };

  function requestHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  class ApiError extends Error {
    constructor(message, status, errors) { super(message); this.status = status; this.errors = errors || {}; }
  }

  async function api(path, method = 'GET', body = undefined, raw = false) {
    const opts = { method, headers: requestHeaders(), cache: 'no-store' };
    if (body !== undefined) opts.body = JSON.stringify(body);
    let response;
    try {
      response = await fetch(path, opts);
    } catch (e) {
      throw new ApiError('Нет связи с сервером Polaris', 0);
    }
    if (raw && response.ok) return response;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail ?? payload.message ?? `HTTP ${response.status}`;
      if (detail && typeof detail === 'object') throw new ApiError(detail.message || 'Ошибка запроса', response.status, detail.errors);
      throw new ApiError(String(detail), response.status);
    }
    return payload.data ?? payload;
  }

  const errText = (e) => (e && e.message) || 'Неизвестная ошибка';

  function setNotice(kind, text) { notice = text ? { kind, text } : null; renderNotice(); }

  function renderNotice() {
    const box = container?.querySelector('#sg-msg');
    if (!box) return;
    box.hidden = !notice;
    box.className = `st-msg${notice ? ` is-${notice.kind}` : ''}`;
    box.textContent = notice ? notice.text : '';
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      area.remove();
      return ok;
    }
  }

  async function copyWithNotice(text, okText) {
    const ok = await copyText(text);
    setNotice(ok ? 'ok' : 'error', ok ? okText : 'Не удалось скопировать — выделите текст вручную');
  }

  /* ─── модальные окна ─── */
  function closeModal() {
    if (!overlayEl) return;
    overlayEl.remove();   // вместе с DOM исчезает и показанный секрет
    overlayEl = null;
    window.PolarisBack?.pop('storage-modal');
  }

  function openModal(html, onAction, { dismissable = true } = {}) {
    closeModal();
    overlayEl = document.createElement('div');
    overlayEl.className = 'tk-overlay';
    overlayEl.innerHTML = `<div class="tk-modal sg-modal" role="dialog" aria-modal="true">${html}</div>`;
    const openedAt = Date.now();
    overlayEl.addEventListener('click', (e) => {
      if (e.target === overlayEl && dismissable && Date.now() - openedAt > 350) { closeModal(); return; }
      const btn = e.target.closest('[data-sm]');
      if (btn) { e.preventDefault(); onAction(btn.dataset.sm, btn); }
    });
    document.body.appendChild(overlayEl);
    window.PolarisBack?.push('storage-modal', () => { if (dismissable) closeModal(); });
    return overlayEl;
  }

  const modalEl = (id) => overlayEl?.querySelector(`#${id}`);
  const modalVal = (id) => modalEl(id)?.value ?? '';
  function modalError(text, field) {
    const box = modalEl('sm-error');
    if (box) { box.textContent = text || ''; box.hidden = !text; }
    overlayEl?.querySelectorAll('[data-field-error]').forEach((n) => { n.textContent = ''; n.hidden = true; });
    if (field && text) {
      const slot = overlayEl?.querySelector(`[data-field-error="${field}"]`);
      if (slot) { slot.textContent = text; slot.hidden = false; }
    }
  }
  function applyFieldErrors(error) {
    const entries = Object.entries(error.errors || {});
    if (!entries.length) { modalError(errText(error)); return; }
    modalError('');
    entries.forEach(([field, message]) => {
      const slot = overlayEl?.querySelector(`[data-field-error="${field}"]`);
      if (slot) { slot.textContent = message; slot.hidden = false; } else { modalError(message); }
    });
  }

  function confirmDialog({ title, text, confirmLabel = 'Подтвердить', danger = true, onConfirm }) {
    const own = openModal(`
      <div class="tk-head"><h3>${escapeHTML(title)}</h3></div>
      <p class="tk-desc">${escapeHTML(text)}</p>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn ${danger ? 'danger' : 'primary'}" data-sm="confirm" id="sm-confirm">${escapeHTML(confirmLabel)}</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'confirm') return;
      btn.disabled = true;
      try {
        await onConfirm();
        if (overlayEl === own) closeModal();  // обработчик мог открыть другое окно (например, с новым секретом)
        if (container) renderBody();
      } catch (e) { modalError(errText(e)); btn.disabled = false; }
    });
  }

  /* ─── загрузка данных ─── */
  async function loadSection(name, { quiet = false } = {}) {
    const token = ++loadToken;
    const stale = () => token !== loadToken || !container;
    try {
      if (name === 'overview') {
        S.status = await api('/api/storage/status');
        S.statusError = '';
        if (!stale()) renderBody();
        if (!quiet) await runChecks();
        return;
      }
      if (name === 'config') { S.config = await api('/api/storage/config'); S.configError = ''; }
      if (name === 'buckets') { S.buckets = (await api('/api/storage/buckets')).buckets; S.bucketsError = ''; }
      if (name === 'bucket') { await loadBucket(); return; }
      if (name === 'keys') { S.creds = await api('/api/storage/credentials'); S.credsError = ''; }
      if (name === 'diagnostics') { S.diag = await api('/api/storage/diagnostics'); S.diagError = ''; }
    } catch (e) {
      const key = { overview: 'statusError', config: 'configError', buckets: 'bucketsError', keys: 'credsError', diagnostics: 'diagError' }[name];
      if (key) S[key] = errText(e);
    }
    if (!stale()) renderBody();
  }

  async function runChecks(checks) {
    S.checking = true;
    renderBody();
    try {
      await api('/api/storage/diagnostics/run', 'POST', checks ? { checks } : {});
      S.status = await api('/api/storage/status');
      S.statusError = '';
      if (S.diag) S.diag = await api('/api/storage/diagnostics');
    } catch (e) {
      S.statusError = errText(e);
    }
    S.checking = false;
    if (container) renderBody();
  }

  async function loadBucket() {
    const name = encodeURIComponent(bucketName);
    try {
      S.bucket = await api(`/api/storage/buckets/${name}`);
      const page = await api(`/api/storage/buckets/${name}/objects?limit=100${S.objectsPrefix ? `&prefix=${encodeURIComponent(S.objectsPrefix)}` : ''}`);
      S.objects = page.objects;
      S.objectsMore = page.truncated ? page.next_after : null;
      S.objectsError = '';
    } catch (e) {
      S.objectsError = errText(e);
    }
    if (container) renderBody();
  }

  async function loadMoreObjects() {
    busy.more = true;
    renderBody();
    try {
      const prefix = S.objectsPrefix ? `&prefix=${encodeURIComponent(S.objectsPrefix)}` : '';
      const page = await api(`/api/storage/buckets/${encodeURIComponent(bucketName)}/objects?limit=100&after=${encodeURIComponent(S.objectsMore)}${prefix}`);
      S.objects = S.objects.concat(page.objects);
      S.objectsMore = page.truncated ? page.next_after : null;
    } catch (e) { setNotice('error', errText(e)); }
    busy.more = false;
    renderBody();
  }

  /* ─── отрисовка: общие части ─── */
  const skeleton = '<p class="st-empty">Загрузка…</p>';

  function renderShell() {
    const active = section === 'bucket' ? 'buckets' : section;
    const tabs = SECTIONS.map(([id, label]) =>
      `<button type="button" class="st-tab${active === id ? ' is-active' : ''}" data-sg="section" data-section="${id}" aria-label="${label}" title="${label}"${active === id ? ' aria-current="page"' : ''}>${TAB_ICONS[id] || ''}<span class="sg-tab-label">${label}</span></button>`).join('');
    container.innerHTML = `
      <div class="st-tabs sg-tabs" role="tablist">${tabs}</div>
      <div id="sg-msg" class="st-msg" hidden></div>
      <div id="sg-body" class="sg-body"></div>`;
    renderNotice();
    renderBody();
  }

  function renderBody() {
    const body = container?.querySelector('#sg-body');
    if (!body) return;
    const views = { overview: viewOverview, config: viewConfig, buckets: viewBuckets, bucket: viewBucket, keys: viewKeys, diagnostics: viewDiagnostics };
    body.innerHTML = (views[section] || viewOverview)();
  }

  /* всплывающая подсказка: кнопка «i» + плашка, которая появляется поверх текста над строкой заголовка */
  let tipSeq = 0;
  const tip = (text, label = 'Пояснение') => {
    const id = `sg-tip-${++tipSeq}`;
    return `<span class="sg-tip"><button type="button" class="sg-info" data-sg="tip" aria-expanded="false" aria-controls="${id}" aria-label="${escapeHTML(label)}" title="${escapeHTML(label)}">i</button><span class="sg-tip-pop" id="${id}" role="note" hidden>${text}</span></span>`;
  };
  function setTip(wrap, open) {
    const btn = wrap.querySelector('.sg-info');
    const pop = wrap.querySelector('.sg-tip-pop');
    if (!btn || !pop) return;
    pop.hidden = !open;
    btn.classList.toggle('is-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  const closeTips = (except = null) => container?.querySelectorAll('.sg-tip').forEach((w) => { if (w !== except) setTip(w, false); });

  const errorBlock = (text) => `<p class="st-error" role="alert">${escapeHTML(text)}</p>`;

  /* ─── Overview ─── */
  function viewOverview() {
    if (S.statusError && !S.status) return `${errorBlock(S.statusError)}<button type="button" class="st-btn" data-sg="retry">Повторить</button>`;
    if (!S.status) return skeleton;
    const st = S.status;
    const components = COMPONENTS.map(([key, label]) => {
      const c = st.components[key] || { status: 'unknown', message: 'Ещё не проверялось' };
      return `
        <div class="card sg-comp" data-component="${key}">
          <div class="st-item-head"><span class="st-item-title">${label}</span>${badge(c.status)}</div>
          ${c.status !== 'ok' && c.message ? `<div class="st-item-sub">${escapeHTML(c.message)}</div>` : ''}
          <div class="st-item-sub">Проверено: ${c.checked_at ? escapeHTML(relTime(c.checked_at)) : 'не проверялось'}${c.duration_ms !== null && c.duration_ms !== undefined ? ` · ${c.duration_ms} мс` : ''}</div>
        </div>`;
    }).join('');
    const s = st.stats;
    const dedup = s && s.logical_bytes >= 0 ? Math.max(0, s.logical_bytes - (s.physical_bytes - s.multipart_bytes)) : null;
    const stats = s ? `
      <div class="sg-stats">
        ${tile('Buckets', s.buckets)}
        ${tile('Объекты (логические)', s.objects, `${fmtSize(s.logical_bytes)} суммарно`)}
        ${tile('Blobs (физические)', s.blobs, `${fmtSize(s.physical_bytes)} в хранилище`)}
        ${tile('Незавершённые multipart', s.multipart_uploads, `частей: ${s.multipart_parts} · ${fmtSize(s.multipart_bytes)}`)}
      </div>`
      : `<p class="st-empty">Статистика недоступна: Storage не запущен.</p>`;
    const note = s ? tip(`Цифры точные (суммы из базы, не оценки). Логические объекты и физические blobs считаются раздельно: при дедупликации blobs меньше объектов${dedup ? ` — экономия ${escapeHTML(fmtSize(dedup))}` : ''}. В физический объём входят и части незавершённых multipart-загрузок. Сообщений в Telegram: ${s.chunks}.`, 'О цифрах статистики') : '';
    const problems = st.storage.state !== 'ready' ? `
      <div class="card sg-banner is-warn" role="status">
        <b>Storage ${escapeHTML(STATUS_TEXT[st.storage.state]?.[0] || st.storage.state)}.</b>
        ${st.storage.reasons.length ? escapeHTML(st.storage.reasons.join('; ')) : 'Включите Storage и настройте Telegram в разделе Configuration.'}
        <button type="button" class="st-btn" data-sg="section" data-section="config">Открыть Configuration</button>
      </div>` : '';
    return `
      ${problems}${restartBanner(st.restart_required)}
      <div class="sg-toolbar">
        <h3 class="st-subtitle">Компоненты</h3>
        <button type="button" class="st-btn" data-sg="recheck" ${S.checking ? 'disabled' : ''}>${S.checking ? 'Проверяем…' : 'Проверить снова'}</button>
      </div>
      ${S.statusError ? errorBlock(S.statusError) : ''}
      <div class="sg-grid">${components}</div>
      <div class="sg-toolbar sg-stats-head">
        <h3 class="st-subtitle">Статистика</h3>
        ${note}
      </div>${stats}`;
  }

  const tile = (label, value, sub = '') => `
    <div class="card sg-tile"><div class="sg-tile-value">${escapeHTML(value)}</div>
      <div class="sg-tile-label">${escapeHTML(label)}</div>${sub ? `<div class="st-item-sub">${escapeHTML(sub)}</div>` : ''}</div>`;

  function restartBanner(required) {
    if (!required) return '';
    return `
      <div class="card sg-banner is-warn" data-banner="restart" role="status">
        Есть сохранённые, но ещё не применённые настройки: они вступят в силу только после перезапуска Polaris.
        Автоматически он не перезапускается.
        <button type="button" class="st-btn danger" data-sg="restart">Перезапустить Polaris</button>
      </div>`;
  }

  /* ─── Configuration ─── */
  function fieldValue(f) { return Object.prototype.hasOwnProperty.call(S.draft, f.key) ? S.draft[f.key] : f.value; }

  function renderField(f) {
    const pending = f.pending_restart ? '<span class="st-badge is-warn">ждёт перезапуска</span>' : '';
    const help = f.help ? tip(escapeHTML(f.help)) : '';
    const err = `<div class="st-modal-error" data-cfg-error="${f.key}" hidden></div>`;
    let control;
    if (f.kind === 'secret') {
      const mode = S.secretMode[f.key] || 'keep';
      const state = f.configured ? `Задан: <code>${escapeHTML(f.masked)}</code>` : (f.uses_main_bot_token ? 'Не задан — используется основной токен бота Polaris' : 'Не задан');
      control = `
        <div class="st-item-sub" data-secret-state="${f.key}">${state}</div>
        ${mode === 'replace' ? `<input class="st-input" id="cfg-${f.key}" data-cfg="${f.key}" type="password" autocomplete="new-password" placeholder="Новый токен 123456:ABC…" />
          <button type="button" class="st-btn" data-sg="secret-mode" data-key="${f.key}" data-mode="keep">Отмена</button>`
          : `<div class="st-item-actions">
              <button type="button" class="st-btn" data-sg="secret-mode" data-key="${f.key}" data-mode="replace">${f.configured ? 'Заменить токен' : 'Задать токен'}</button>
              ${f.configured ? `<button type="button" class="st-btn danger${mode === 'clear' ? ' is-active' : ''}" data-sg="secret-mode" data-key="${f.key}" data-mode="clear">${mode === 'clear' ? 'Будет очищен' : 'Очистить'}</button>` : ''}
            </div>`}`;
    } else if (f.kind === 'bool') {
      const v = fieldValue(f) === true || fieldValue(f) === 'true';
      control = `<select class="st-input" id="cfg-${f.key}" data-cfg="${f.key}"><option value="true"${v ? ' selected' : ''}>Включено</option><option value="false"${v ? '' : ' selected'}>Выключено</option></select>`;
    } else {
      control = `<input class="st-input" id="cfg-${f.key}" data-cfg="${f.key}" type="${f.kind === 'int' ? 'number' : 'text'}" value="${escapeHTML(fieldValue(f))}" autocomplete="off" />`;
    }
    return `<div class="st-item sg-field" data-field-key="${f.key}"><div class="st-item-head"><span class="sg-title"><span class="st-item-title">${escapeHTML(f.label)}</span>${help}</span>${pending}</div><div class="sg-control">${control}</div>${err}</div>`;
  }

  function viewConfig() {
    if (S.configError) return `${errorBlock(S.configError)}<button type="button" class="st-btn" data-sg="retry">Повторить</button>`;
    if (!S.config) return skeleton;
    const c = S.config;
    const group = (id, title, extra = '') => `
      <h3 class="st-subtitle">${title}</h3>
      <div class="st-list">${(c.groups[id] || []).map(renderField).join('')}${extra}</div>`;
    const origin = window.location.origin;
    const endpoint = `
      <div class="st-item" data-endpoint>
        <div class="st-item-head"><span class="sg-title"><span class="st-item-title">Endpoint</span>${tip('Адрес с учётом настройки «Путь endpoint» и адреса, по которому открыт Polaris. Addressing style — path (фиксирован: virtual-hosted не поддерживается). Host и параметры reverse proxy задаются инфраструктурой, не интерфейсом.')}</span><span class="st-badge">path-style</span></div>
        <div class="sg-copyrow">
          <code class="st-code" id="sg-endpoint">${escapeHTML(origin + c.s3.path_prefix)}</code>
          <button type="button" class="sg-icon-btn" data-sg="copy-endpoint" aria-label="Копировать endpoint" title="Копировать endpoint"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg></button>
        </div>
      </div>`;
    const envWarn = c.env_file.writable ? '' : '<p class="st-error">Файл .env недоступен для записи — сохранить настройки не получится.</p>';
    const applied = S.applied ? `
      <div class="card sg-banner ${S.applied.applied ? 'is-ok' : ''}" data-applied role="status">${escapeHTML(S.applied.message)}
        ${S.applied.applied && S.appliedTelegram ? `<button type="button" class="st-btn" data-sg="test-saved-telegram" ${busy.tgtest ? 'disabled' : ''}>${busy.tgtest ? 'Проверяем…' : 'Проверить Telegram'}</button>` : ''}</div>` : '';
    const test = S.testResult ? `<div class="st-msg ${S.testResult.ok ? 'is-ok' : 'is-error'}" data-test-result>${S.testResult.steps.map((s) => `${escapeHTML(s.label)}: ${escapeHTML(s.message)}`).join('<br>')}</div>` : '';
    return `
      ${restartBanner(c.restart_required)}${envWarn}${applied}${test}
      ${group('general', 'Общее')}
      ${group('telegram', 'Telegram Backend')}
      ${group('s3', 'S3 API', endpoint)}
      <div class="sg-save">
        <button type="button" class="tk-btn primary sg-save-main" data-sg="cfg-preview" ${busy.preview ? 'disabled' : ''}>Сохранить</button>
        <button type="button" class="tk-btn sg-reset" data-sg="cfg-reset" aria-label="Сбросить изменения" title="Сбросить изменения">↺</button>
      </div>`;
  }

  function captureDraft() {
    // перед перерисовкой формы сохраняем несохранённые правки обычных полей (секреты в состояние не попадают)
    (S.config?.groups ? Object.values(S.config.groups).flat() : []).forEach((f) => {
      if (f.kind === 'secret') return;
      const el = container?.querySelector(`#cfg-${f.key}`);
      if (el) S.draft[f.key] = el.value;
    });
  }

  function collectConfigChanges() {
    const changes = {};
    (S.config?.groups ? Object.values(S.config.groups).flat() : []).forEach((f) => {
      if (f.kind === 'secret') {
        const mode = S.secretMode[f.key] || 'keep';
        const input = container.querySelector(`#cfg-${f.key}`);
        if (mode === 'replace' && input && input.value.trim()) changes[f.key] = input.value.trim();
        if (mode === 'clear') changes[f.key] = '';
        return;
      }
      const el = container.querySelector(`#cfg-${f.key}`);
      if (!el) return;
      let value = el.value;
      if (f.kind === 'bool') value = value === 'true';
      if (f.kind === 'int') value = value === '' ? value : Number(value);
      if (value !== f.value) changes[f.key] = value;
    });
    return changes;
  }

  function showConfigErrors(errors) {
    container.querySelectorAll('[data-cfg-error]').forEach((n) => { n.hidden = true; n.textContent = ''; });
    Object.entries(errors || {}).forEach(([key, message]) => {
      const slot = container.querySelector(`[data-cfg-error="${key}"]`);
      if (slot) { slot.textContent = message; slot.hidden = false; }
    });
  }

  async function previewConfig() {
    const changes = collectConfigChanges();
    if (!Object.keys(changes).length) { setNotice('ok', 'Изменений нет'); return; }
    busy.preview = true;
    try {
      const result = await api('/api/storage/config', 'PUT', { changes, confirm: false });
      if (!result.diff.length) { setNotice('ok', result.message || 'Изменений нет'); return; }
      showPreview(changes, result);
    } catch (e) {
      showConfigErrors(e.errors);
      setNotice('error', e.errors && Object.keys(e.errors).length ? 'Проверьте выделенные поля' : errText(e));
    } finally { busy.preview = false; }
  }

  function showPreview(changes, result) {
    const rows = result.diff.map((d) => `
      <tr><td>${escapeHTML(d.label)}</td><td><code>${escapeHTML(d.old)}</code></td><td><code>${escapeHTML(d.new)}</code></td></tr>`).join('');
    const warnings = (result.warnings || []).map((w) => `<p class="st-error">${escapeHTML(w)}</p>`).join('');
    openModal(`
      <div class="tk-head"><h3>Подтвердите изменения</h3></div>
      <table class="sg-table sg-diff"><thead><tr><th>Параметр</th><th>Сейчас</th><th>Станет</th></tr></thead><tbody>${rows}</tbody></table>
      ${warnings}
      <p class="tk-desc">Значения будут записаны в .env. Они вступят в силу только после перезапуска Polaris — он не перезапускается автоматически.</p>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="apply" id="sm-apply">Применить и сохранить</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'apply') return;
      btn.disabled = true;
      try {
        S.applied = await api('/api/storage/config', 'PUT', { changes, confirm: true });
        S.appliedTelegram = ['telegram_bot_token', 'telegram_chat_id'].some((k) => k in changes);
        closeModal();
        S.draft = {}; S.secretMode = {};
        S.config = await api('/api/storage/config');
        setNotice('ok', 'Настройки сохранены');
        renderBody();
      } catch (e) { modalError(errText(e)); btn.disabled = false; }
    });
  }

  async function testSavedTelegram() {
    busy.tgtest = true;
    renderBody();
    try {
      S.testResult = await api('/api/storage/test', 'POST', { steps: ['telegram'], use_saved: true });
    } catch (e) {
      S.testResult = { ok: false, steps: [{ label: 'Telegram Backend', message: errText(e) }] };
    }
    busy.tgtest = false;
    renderBody();
  }

  function confirmRestart() {
    confirmDialog({
      title: 'Перезапустить Polaris?', danger: true, confirmLabel: 'Перезапустить',
      text: 'Сервис будет перезапущен, интерфейс на несколько секунд станет недоступен. Применятся все сохранённые настройки.',
      onConfirm: async () => {
        try { await api('/api/restart', 'POST'); } catch (e) { if (!(e instanceof TypeError)) throw e; }  // обрыв связи = сервис уже перезапускается
        window.PolarisRestart.show('Идёт перезапуск Polaris…');
      },
    });
  }

  /* ─── Buckets ─── */
  function viewBuckets() {
    if (S.bucketsError) return `${errorBlock(S.bucketsError)}<button type="button" class="st-btn" data-sg="retry">Повторить</button>`;
    if (!S.buckets) return skeleton;
    const rows = S.buckets.map((b) => `
      <tr data-bucket-row="${escapeHTML(b.name)}">
        <td><b>${escapeHTML(b.name)}</b></td><td>${escapeHTML(fmtDate(b.created_at))}</td>
        <td>${b.object_count}</td><td>${escapeHTML(fmtSize(b.total_size))}</td>
        <td class="sg-actions">
          <button type="button" class="st-btn" data-sg="open-bucket" data-name="${escapeHTML(b.name)}">Open</button>
          <button type="button" class="st-btn danger" data-sg="delete-bucket" data-name="${escapeHTML(b.name)}">Delete</button>
        </td></tr>`).join('');
    const table = S.buckets.length ? `
      <div class="sg-scroll"><table class="sg-table"><thead><tr><th>Bucket Name</th><th>Created At</th><th>Object Count</th><th>Total Size</th><th>Actions</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
      : '<p class="st-empty" data-empty="buckets">Buckets пока нет. Создайте первый — это логическое пространство имён внутри Polaris Storage.</p>';
    return `
      <div class="sg-toolbar"><h3 class="st-subtitle">Buckets</h3>
        <button type="button" class="st-btn primary" data-sg="new-bucket">Create Bucket</button></div>${table}`;
  }

  function openCreateBucket() {
    openModal(`
      <div class="tk-head"><h3>Create Bucket</h3></div>
      <div class="tk-field"><label for="sm-name">Имя bucket</label>
        <input id="sm-name" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="my-bucket" maxlength="63" />
        <div class="st-modal-error" data-field-error="name" hidden></div></div>
      <p class="st-modal-hint">3–63 символа: строчные латинские буквы, цифры, «-» и «.»; начало и конец — буква или цифра.</p>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="create" id="sm-create">Создать</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'create') return;
      modalError('');  // старые ошибки не должны висеть, пока идёт новый запрос
      btn.disabled = true;
      try {
        await api('/api/storage/buckets', 'POST', { name: modalVal('sm-name').trim() });
        closeModal();
        setNotice('ok', 'Bucket создан');
        await refreshAfterChange();
      } catch (e) { applyFieldErrors(e); btn.disabled = false; }
    });
    modalEl('sm-name')?.focus();
  }

  function confirmDeleteBucket(name) {
    confirmDialog({
      title: `Удалить bucket «${name}»?`, confirmLabel: 'Удалить',
      text: 'Можно удалить только пустой bucket. Это действие необратимо.',
      onConfirm: async () => {
        await api(`/api/storage/buckets/${encodeURIComponent(name)}`, 'DELETE');
        setNotice('ok', `Bucket «${name}» удалён`);
        if (section === 'bucket') { section = 'buckets'; renderShell(); }
        await refreshAfterChange();
      },
    });
  }

  /* ─── Bucket ─── */
  function viewBucket() {
    if (S.objectsError && !S.bucket) return `${errorBlock(S.objectsError)}<button type="button" class="st-btn" data-sg="back-buckets">← К списку buckets</button>`;
    if (!S.bucket) return skeleton;
    const b = S.bucket;
    const rows = S.objects.map((o) => `
      <tr data-object-row="${escapeHTML(o.id)}">
        <td class="sg-key">${escapeHTML(o.key)}</td><td>${escapeHTML(fmtSize(o.size))}</td>
        <td>${escapeHTML(o.content_type)}</td><td>${escapeHTML(fmtDate(o.last_modified))}</td>
        <td><code>${escapeHTML(o.etag || '—')}</code></td>
        <td class="sg-actions">
          <button type="button" class="st-btn" data-sg="object-info" data-id="${escapeHTML(o.id)}">Метаданные</button>
          <button type="button" class="st-btn" data-sg="object-download" data-id="${escapeHTML(o.id)}" data-key="${escapeHTML(o.key)}">Download</button>
          <button type="button" class="st-btn" data-sg="object-copy-key" data-key="${escapeHTML(o.key)}">Copy Key</button>
          <button type="button" class="st-btn danger" data-sg="object-delete" data-id="${escapeHTML(o.id)}" data-key="${escapeHTML(o.key)}">Delete</button>
        </td></tr>`).join('');
    const list = S.objects.length ? `
      <div class="sg-scroll"><table class="sg-table"><thead><tr><th>Object Key</th><th>Size</th><th>Content-Type</th><th>Last Modified</th><th>ETag</th><th>Actions</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      ${S.objectsMore ? `<button type="button" class="st-btn" data-sg="more-objects" ${busy.more ? 'disabled' : ''}>${busy.more ? 'Загрузка…' : 'Показать ещё'}</button>` : ''}`
      : `<p class="st-empty" data-empty="objects">${S.objectsPrefix ? 'Объектов с таким префиксом нет.' : 'В bucket пока нет объектов. Загрузите их через S3 API (boto3, aws cli, rclone).'}</p>`;
    return `
      <div class="sg-toolbar">
        <button type="button" class="st-btn" data-sg="back-buckets">← Buckets</button>
        <button type="button" class="st-btn danger" data-sg="delete-bucket" data-name="${escapeHTML(b.name)}">Delete Bucket</button>
      </div>
      <div class="card sg-bucket-head"><h3 class="st-subtitle" data-bucket-title>${escapeHTML(b.name)}</h3>
        <div class="st-item-sub">Объектов: <b data-bucket-count>${b.object_count}</b> · объём: ${escapeHTML(fmtSize(b.total_size))} · создан ${escapeHTML(fmtDate(b.created_at))}</div></div>
      ${S.objectsError ? errorBlock(S.objectsError) : ''}
      <div class="sg-filter"><input class="st-input" id="sg-prefix" type="text" placeholder="Фильтр по префиксу ключа" value="${escapeHTML(S.objectsPrefix)}" autocomplete="off" />
        <button type="button" class="st-btn" data-sg="apply-prefix">Найти</button></div>
      ${list}`;
  }

  async function openObjectInfo(id) {
    try {
      const o = await api(`/api/storage/objects/${encodeURIComponent(id)}`);
      const kv = (obj) => Object.keys(obj || {}).length
        ? Object.entries(obj).map(([k, v]) => `<tr><td>${escapeHTML(k)}</td><td>${escapeHTML(v)}</td></tr>`).join('')
        : '<tr><td colspan="2" class="st-empty">нет</td></tr>';
      openModal(`
        <div class="tk-head"><h3>Метаданные объекта</h3><span class="tk-sub">${escapeHTML(o.bucket)}</span></div>
        <table class="sg-table sg-kv">
          <tr><td>Key</td><td data-meta="key">${escapeHTML(o.key)}</td></tr>
          <tr><td>Size</td><td>${escapeHTML(fmtSize(o.size))} (${o.size} Б)</td></tr>
          <tr><td>Content-Type</td><td>${escapeHTML(o.content_type)}</td></tr>
          <tr><td>Last Modified</td><td>${escapeHTML(fmtDate(o.last_modified))}</td></tr>
          <tr><td>ETag</td><td><code>${escapeHTML(o.etag || '—')}</code></td></tr>
        </table>
        <h4 class="sg-h4">Пользовательские метаданные (x-amz-meta-*)</h4><table class="sg-table sg-kv"><tbody>${kv(o.user_metadata)}</tbody></table>
        <h4 class="sg-h4">Заголовки</h4><table class="sg-table sg-kv"><tbody>${kv(o.headers)}</tbody></table>
        <div class="tk-actions"><div class="tk-actions-row">
          <button type="button" class="tk-btn" data-sm="copy">Copy Key</button>
          <button type="button" class="tk-btn" data-sm="close">Закрыть</button>
        </div></div>`, async (action) => {
        if (action === 'close') closeModal();
        if (action === 'copy') await copyWithNotice(o.key, 'Ключ скопирован');
      });
    } catch (e) { setNotice('error', errText(e)); }
  }

  async function downloadObject(id, key) {
    const name = key.split('/').pop() || 'object';
    setNotice('ok', `Готовлю «${name}»… если Telegram спросит — подтвердите скачивание`);
    try {
      const result = await window.PolarisDownload.start(id, name);
      setNotice('ok', window.PolarisDownload.message(result, name));
    } catch (e) { setNotice('error', `Не удалось скачать: ${errText(e)}`); }
  }

  function confirmDeleteObject(id, key) {
    confirmDialog({
      title: 'Удалить объект?', confirmLabel: 'Удалить', text: `«${key}» будет удалён без возможности восстановления.`,
      onConfirm: async () => {
        const result = await api(`/api/storage/objects/${encodeURIComponent(id)}`, 'DELETE');
        setNotice(result.physical_complete ? 'ok' : 'error', result.physical_complete
          ? 'Объект удалён'
          : 'Объект удалён логически, но физическое удаление в Telegram не подтверждено — см. Diagnostics → Recovery');
        await loadBucket();
      },
    });
  }

  /* ─── Access Keys ─── */
  function viewKeys() {
    if (S.credsError) return `${errorBlock(S.credsError)}<button type="button" class="st-btn" data-sg="retry">Повторить</button>`;
    if (!S.creds) return skeleton;
    const cards = S.creds.credentials.map((k) => {
      const perms = k.permissions.map((p) => `<span class="st-badge" title="${escapeHTML(PERM_INFO[p] || '')}">${escapeHTML(p)}</span>`).join(' ');
      const buckets = k.all_buckets ? '<span class="st-badge is-warn">все buckets</span>' : k.buckets.map((b) => `<span class="st-badge">${escapeHTML(b)}</span>`).join(' ');
      const revoked = k.status === 'revoked';
      const actions = k.managed_in_env
        ? '<div class="st-item-sub">Задан в .env (S3_ACCESS_KEY_ID / S3_CREDENTIALS): управляется через конфигурацию Polaris.</div>'
        : `<div class="st-item-actions">
            ${revoked ? '' : `<button type="button" class="st-btn" data-sg="key-rotate" data-id="${k.id}">Rotate</button>
              <button type="button" class="st-btn danger" data-sg="key-revoke" data-id="${k.id}">Revoke</button>`}
            <button type="button" class="st-btn" data-sg="key-edit" data-id="${k.id}">Изменить описание</button>
            ${revoked ? `<button type="button" class="st-btn danger" data-sg="key-delete" data-id="${k.id}">Delete</button>` : ''}
          </div>`;
      return `
        <div class="st-item sg-key-card${revoked ? ' is-revoked' : ''}" data-key-id="${escapeHTML(k.access_key_id)}">
          <div class="st-item-head"><span class="st-item-title">${escapeHTML(k.name)}</span>
            <span>${k.needs_rotation ? '<span class="st-badge is-warn">нужна ротация</span> ' : ''}${k.managed_in_env ? '<span class="st-badge">.env</span> ' : ''}${revoked ? '<span class="st-badge is-err">Revoked</span>' : '<span class="st-badge is-ok">Active</span>'}</span></div>
          <div class="st-item-sub">Access Key ID: <code>${escapeHTML(k.access_key_id)}</code>
            <button type="button" class="st-btn sg-mini" data-sg="copy-text" data-text="${escapeHTML(k.access_key_id)}">Copy</button></div>
          ${k.description ? `<div class="st-item-sub">${escapeHTML(k.description)}</div>` : ''}
          <div class="sg-chips"><span class="st-item-sub">Права:</span> ${perms}</div>
          <div class="sg-chips"><span class="st-item-sub">Buckets:</span> ${buckets}</div>
          <div class="st-item-sub">Создан: ${escapeHTML(fmtDate(k.created_at))} · последнее использование: ${k.last_used_at ? escapeHTML(relTime(k.last_used_at)) : 'ещё не использовался'}${k.rotated_at ? ` · ротация: ${escapeHTML(fmtDate(k.rotated_at))}` : ''}${revoked && k.revoked_at ? ` · отозван: ${escapeHTML(fmtDate(k.revoked_at))}` : ''}</div>
          ${actions}</div>`;
    }).join('');
    const databaseKeys = S.creds.credentials.filter((k) => !k.managed_in_env);
    const empty = databaseKeys.length ? '' : '<p class="st-empty" data-empty="keys">Ключей пока нет. Создайте ключ для сервиса и выдайте ему только нужные права.</p>';
    return `
      <section class="card sg-keys-block" aria-label="Access Keys">
        <div class="sg-toolbar sg-keys-head"><div class="sg-title"><h3 class="st-subtitle">Access Keys</h3>${tip('Secret Access Key показывается один раз при создании или ротации и нигде не хранится. Права проверяются при каждом запросе, в том числе по presigned URL; отозванный ключ перестаёт работать сразу.', 'О ключах доступа')}</div>
          <button type="button" class="st-btn primary" data-sg="new-key">Create Access Key</button></div>
        ${cards ? `<div class="st-list">${cards}</div>` : ''}${empty}
      </section>`;
  }

  async function openCreateKey() {
    let buckets = S.buckets;
    try { if (!buckets) buckets = (await api('/api/storage/buckets')).buckets; S.buckets = buckets; } catch (e) { buckets = []; }
    const bucketBoxes = buckets.length
      ? buckets.map((b) => `<label class="sg-check"><input type="checkbox" data-bucket value="${escapeHTML(b.name)}" /> ${escapeHTML(b.name)}</label>`).join('')
      : '<span class="st-item-sub">Buckets ещё нет — создайте bucket или разрешите все.</span>';
    const permBoxes = Object.keys(PERM_INFO).map((p) =>
      `<label class="sg-check"><input type="checkbox" data-perm value="${p}" /> <b>${p}</b> <span class="st-item-sub">${escapeHTML(PERM_INFO[p])}</span></label>`).join('');
    openModal(`
      <div class="tk-head"><h3>Create Access Key</h3></div>
      <div class="tk-field"><label for="sm-kname">Название</label><input id="sm-kname" type="text" maxlength="80" autocomplete="off" placeholder="backup-service" />
        <div class="st-modal-error" data-field-error="name" hidden></div></div>
      <div class="tk-field"><label for="sm-kdesc">Описание</label><textarea id="sm-kdesc" rows="2" maxlength="500" placeholder="Для чего ключ"></textarea>
        <div class="st-modal-error" data-field-error="description" hidden></div></div>
      <div class="tk-field"><label>Разрешённые buckets</label>
        <label class="sg-check"><input type="radio" name="sm-scope" value="selected" checked /> Выбранные</label>
        <div class="sg-checks" id="sm-buckets">${bucketBoxes}</div>
        <label class="sg-check"><input type="radio" name="sm-scope" value="all" /> Все buckets</label>
        <div class="st-modal-error" data-field-error="buckets" hidden></div></div>
      <div class="tk-field"><label>Права (минимально необходимые)</label><div class="sg-checks" id="sm-perms">${permBoxes}</div>
        <div class="st-modal-error" data-field-error="permissions" hidden></div></div>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="create" id="sm-kcreate">Создать ключ</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'create') return;
      const perms = [...overlayEl.querySelectorAll('[data-perm]:checked')].map((n) => n.value);
      const all = overlayEl.querySelector('input[name="sm-scope"]:checked')?.value === 'all';
      const selected = [...overlayEl.querySelectorAll('[data-bucket]:checked')].map((n) => n.value);
      modalError('');
      btn.disabled = true;
      try {
        const result = await api('/api/storage/credentials', 'POST', {
          name: modalVal('sm-kname').trim(), description: modalVal('sm-kdesc').trim(), permissions: perms,
          all_buckets: all, buckets: all ? null : selected,
        });
        showSecret(result, 'Ключ создан');
        S.creds = await api('/api/storage/credentials');
        if (container) renderBody();
      } catch (e) { applyFieldErrors(e); btn.disabled = false; }
    });
    // MultipartUpload невозможен без WriteObject: связываем флажки, как это проверяет сервер
    overlayEl.addEventListener('change', (e) => {
      const target = e.target;
      const write = overlayEl.querySelector('[data-perm][value="WriteObject"]');
      const multi = overlayEl.querySelector('[data-perm][value="MultipartUpload"]');
      if (target === multi && multi.checked) write.checked = true;
      if (target === write && !write.checked) multi.checked = false;
    });
    modalEl('sm-kname')?.focus();
  }

  function showSecret(result, title) {
    const id = result.access_key_id;
    openModal(`
      <div class="tk-head"><h3>${escapeHTML(title)}</h3></div>
      <p class="sg-warn" role="alert"><b>Сохраните Secret Access Key сейчас.</b> Он показывается один раз и нигде не хранится — после закрытия окна его нельзя посмотреть повторно (потеряли — выполните Rotate).</p>
      <div class="tk-field"><label>Access Key ID</label>
        <code class="st-code" id="sm-akid">${escapeHTML(id)}</code>
        <button type="button" class="tk-btn small" data-sm="copy-id">Копировать</button></div>
      <div class="tk-field"><label>Secret Access Key</label>
        <code class="st-code is-token" id="sm-secret">${escapeHTML(result.secret_access_key)}</code>
        <button type="button" class="tk-btn small" data-sm="copy-secret">Копировать</button></div>
      <p id="sm-error" class="st-modal-hint"></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="done" id="sm-done">Я сохранил секрет</button>
      </div></div>`, async (action) => {
      const say = (text) => { const box = modalEl('sm-error'); if (box) { box.textContent = text; box.hidden = false; } };
      if (action === 'copy-id') say((await copyText(id)) ? 'Access Key ID скопирован' : 'Не удалось скопировать — выделите вручную');
      if (action === 'copy-secret') say((await copyText(result.secret_access_key)) ? 'Secret Access Key скопирован' : 'Не удалось скопировать — выделите вручную');
      if (action === 'done') closeModal();
    }, { dismissable: false });
  }

  function findKey(id) { return S.creds?.credentials.find((k) => k.id === id); }

  function confirmRotate(id) {
    const key = findKey(id);
    confirmDialog({
      title: `Rotate «${key?.name || ''}»?`, confirmLabel: 'Выпустить новый секрет',
      text: 'Будет создан новый Secret Access Key, а прежний перестанет работать немедленно (его значение не раскрывается). Сервисы, использующие ключ, нужно обновить.',
      onConfirm: async () => {
        const result = await api(`/api/storage/credentials/${id}/rotate`, 'POST');
        showSecret(result, 'Новый секрет выпущен');
        S.creds = await api('/api/storage/credentials');
        if (container) renderBody();
      },
    });
  }

  function confirmRevoke(id) {
    const key = findKey(id);
    confirmDialog({
      title: `Revoke «${key?.name || ''}»?`, confirmLabel: 'Отозвать',
      text: 'Ключ немедленно перестанет проходить S3-аутентификацию, в том числе выданные им presigned URL. Отменить отзыв нельзя.',
      onConfirm: async () => { await api(`/api/storage/credentials/${id}/revoke`, 'POST'); setNotice('ok', 'Ключ отозван'); S.creds = await api('/api/storage/credentials'); },
    });
  }

  function confirmDeleteKey(id) {
    const key = findKey(id);
    confirmDialog({
      title: `Удалить «${key?.name || ''}»?`, confirmLabel: 'Удалить',
      text: 'Запись об отозванном ключе будет удалена безвозвратно.',
      onConfirm: async () => { await api(`/api/storage/credentials/${id}`, 'DELETE'); setNotice('ok', 'Ключ удалён'); S.creds = await api('/api/storage/credentials'); },
    });
  }

  function openEditKey(id) {
    const key = findKey(id);
    if (!key) return;
    openModal(`
      <div class="tk-head"><h3>Изменить ключ</h3><span class="tk-sub">${escapeHTML(key.access_key_id)}</span></div>
      <div class="tk-field"><label for="sm-ename">Название</label><input id="sm-ename" type="text" maxlength="80" value="${escapeHTML(key.name)}" />
        <div class="st-modal-error" data-field-error="name" hidden></div></div>
      <div class="tk-field"><label for="sm-edesc">Описание</label><textarea id="sm-edesc" rows="3" maxlength="500">${escapeHTML(key.description)}</textarea>
        <div class="st-modal-error" data-field-error="description" hidden></div></div>
      <p class="st-modal-hint">Права и buckets после создания не меняются: чтобы изменить их, создайте новый ключ.</p>
      <p id="sm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-sm="save">Сохранить</button>
        <button type="button" class="tk-btn" data-sm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'save') return;
      modalError('');
      btn.disabled = true;
      try {
        await api(`/api/storage/credentials/${id}`, 'PATCH', { name: modalVal('sm-ename').trim(), description: modalVal('sm-edesc').trim() });
        closeModal();
        setNotice('ok', 'Сохранено');
        S.creds = await api('/api/storage/credentials');
        renderBody();
      } catch (e) { applyFieldErrors(e); btn.disabled = false; }
    });
  }

  /* ─── Diagnostics ─── */
  function viewDiagnostics() {
    if (S.diagError) return `${errorBlock(S.diagError)}<button type="button" class="st-btn" data-sg="retry">Повторить</button>`;
    if (!S.diag) return skeleton;
    const d = S.diag;
    const checks = Object.keys(CHECK_LABELS).map((name) => {
      const c = d.checks[name] || { status: 'unknown', message: 'Ещё не проверялось' };
      return `
        <tr data-check="${name}"><td><b>${CHECK_LABELS[name]}</b></td><td>${badge(c.status)}</td>
          <td>${escapeHTML(c.message || '')}</td><td>${c.duration_ms ?? '—'}${c.duration_ms !== null && c.duration_ms !== undefined ? ' мс' : ''}</td>
          <td>${c.checked_at ? escapeHTML(fmtDate(c.checked_at)) : '—'}</td>
          <td><button type="button" class="st-btn" data-sg="run-check" data-name="${name}" ${S.checking ? 'disabled' : ''}>Проверить</button></td></tr>`;
    }).join('');
    const tests = d.last_tests ? renderTestResults(d.last_tests, false) : '<p class="st-empty">Тесты ещё не запускались.</p>';
    const stepButtons = TEST_STEPS.map(([id, label]) =>
      `<button type="button" class="st-btn" data-sg="run-step" data-step="${id}" ${busy.test ? 'disabled' : ''}>${label}</button>`).join('');
    return `
      <div class="sg-toolbar"><h3 class="st-subtitle">Health Checks</h3>
        <button type="button" class="st-btn primary" data-sg="run-all-checks" ${S.checking ? 'disabled' : ''}>${S.checking ? 'Проверяем…' : 'Run all checks'}</button></div>
      <div class="sg-scroll"><table class="sg-table"><thead><tr><th>Проверка</th><th>Результат</th><th>Описание</th><th>Время</th><th>Проверено</th><th></th></tr></thead><tbody>${checks}</tbody></table></div>
      <div class="sg-toolbar sg-tests-head"><div class="sg-title"><h3 class="st-subtitle">Storage Tests</h3>${tip('Тесты используют только синтетические данные в служебном bucket и временный ключ; всё созданное удаляется даже при ошибке.', 'О тестах')}${d.last_tests ? testsStatus(d.last_tests) : ''}</div>
        <button type="button" class="st-btn primary" data-sg="run-tests" ${busy.test ? 'disabled' : ''}>${busy.test ? 'Выполняется…' : 'Run all tests'}</button></div>
      <div class="st-item-actions">${stepButtons}</div>
      <div id="sg-tests">${tests}</div>
      ${viewMultipart(d.multipart)}
      ${viewRecovery(d)}`;
  }

  const testsStatus = (t) => `<span class="sg-status${t.ok ? '' : ' is-error'}">${t.ok ? 'Все шаги пройдены' : 'Есть ошибки'} · завершено ${escapeHTML(fmtDate(t.finished_at))}</span>`;

  function renderTestResults(t, withStatus = true) {
    const rows = t.steps.map((s) => `
      <tr data-step-row="${escapeHTML(s.step)}"><td>${escapeHTML(s.label)}</td><td>${badge(s.status)}</td>
        <td>${escapeHTML(s.message)}</td><td>${s.duration_ms} мс</td></tr>`).join('');
    return `<div class="sg-scroll"><table class="sg-table" data-test-table><thead><tr><th>Шаг</th><th>Результат</th><th>Сообщение</th><th>Время</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${withStatus ? `<p class="st-item-sub">${testsStatus(t)}</p>` : ''}`;
  }

  function viewMultipart(uploads) {
    if (!uploads.length) return '';
    const rows = uploads.map((u) => `
      <tr data-upload="${escapeHTML(u.upload_id)}"><td>${escapeHTML(u.bucket)}</td><td class="sg-key">${escapeHTML(u.key)}</td>
        <td><code>${escapeHTML(u.upload_id.slice(0, 8))}…</code></td><td>${escapeHTML(fmtDate(u.created_at))}</td>
        <td>${u.parts} · ${escapeHTML(fmtSize(u.bytes))}</td><td>${escapeHTML(u.status)}</td>
        <td><button type="button" class="st-btn danger" data-sg="abort-upload" data-id="${escapeHTML(u.upload_id)}">Abort</button></td></tr>`).join('');
    return `
      <h3 class="st-subtitle">Multipart Uploads</h3>
      <div class="sg-scroll"><table class="sg-table"><thead><tr><th>Bucket</th><th>Object Key</th><th>Upload</th><th>Создана</th><th>Части</th><th>Состояние</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="st-item-sub">Загрузки не отменяются автоматически: решение за вами.</p>`;
  }

  function viewRecovery(d) {
    const r = d.recovery;
    if (!r) return '<h3 class="st-subtitle">Recovery</h3><p class="st-empty">Недоступно: Storage не запущен.</p>';
    const rec = d.last_reconcile;
    const counters = [
      ['Подозрительные orphan (без ссылки)', r.orphans_suspected], ['Orphan с ссылкой (будут удалены)', r.orphans_pending_delete],
      ['Удаление не удалось (повтор)', r.chunks_delete_failed], ['Удаление отклонено backend', r.chunks_delete_rejected],
      ['Blobs в очереди удаления', r.blobs_deleting + r.blobs_failed], ['Объекты с ошибкой загрузки', r.objects_failed],
    ].map(([label, n]) => `<div class="card sg-tile${n ? ' is-attn' : ''}"><div class="sg-tile-value">${n}</div><div class="sg-tile-label">${escapeHTML(label)}</div></div>`).join('');
    const orphanRows = r.orphans.map((o) => `<tr><td>${escapeHTML(o.kind)}</td><td>${escapeHTML(o.marker || '—')}</td><td>${o.has_reference ? 'да' : 'нет'}</td><td>${o.delete_attempts}</td><td>${escapeHTML(o.last_error || '')}</td><td>${escapeHTML(fmtDate(o.created_at))}</td></tr>`).join('');
    const failedRows = r.failed_deletions.map((f) => `<tr><td>${escapeHTML(f.status)}</td><td>${f.attempts}</td><td>${escapeHTML(f.last_error || '')}</td><td>${escapeHTML(fmtDate(f.last_attempt_at))}</td></tr>`).join('');
    return `
      <div class="sg-toolbar sg-head"><h3 class="st-subtitle">Recovery</h3>${tip(`Только просмотр. Данные, принадлежность которых к Storage не подтверждена, автоматически не удаляются; удаление сообщений в Telegram из интерфейса недоступно. ${rec ? `Последняя сверка: ${escapeHTML(fmtDate(rec.at))} — ${escapeHTML(rec.summary)}.` : 'Фоновая сверка ещё не выполнялась.'}`, 'О разделе Recovery')}</div>
      <div class="sg-stats">${counters}</div>
      ${r.orphans.length ? `<div class="sg-scroll"><table class="sg-table"><thead><tr><th>Тип</th><th>Метка</th><th>Ссылка</th><th>Попыток</th><th>Ошибка</th><th>Создан</th></tr></thead><tbody>${orphanRows}</tbody></table></div>` : '<p class="st-empty" data-empty="orphans">Orphan-записей нет.</p>'}
      ${r.failed_deletions.length ? `<div class="sg-scroll"><table class="sg-table"><thead><tr><th>Статус</th><th>Попыток</th><th>Ошибка</th><th>Последняя попытка</th></tr></thead><tbody>${failedRows}</tbody></table></div>` : ''}`;
  }

  async function runTests(steps) {
    busy.test = true;
    renderBody();
    try {
      const result = await api('/api/storage/test', 'POST', steps ? { steps } : {});
      if (S.diag) S.diag.last_tests = result;
      setNotice(result.ok ? 'ok' : 'error', result.ok ? 'Тесты пройдены' : 'Тесты завершились с ошибками');
      if (section === 'overview') showTestModal(result);
    } catch (e) { setNotice('error', errText(e)); }
    busy.test = false;
    if (container) renderBody();
  }

  function showTestModal(result) {
    openModal(`<div class="tk-head"><h3>Test Storage</h3></div>${renderTestResults(result)}
      <div class="tk-actions"><div class="tk-actions-row"><button type="button" class="tk-btn" data-sm="close">Закрыть</button></div></div>`,
    (action) => { if (action === 'close') closeModal(); });
  }

  function confirmAbortUpload(id) {
    confirmDialog({
      title: 'Отменить multipart-загрузку?', confirmLabel: 'Abort',
      text: 'Все загруженные части будут удалены. Клиент, который их загружает, получит ошибку NoSuchUpload.',
      onConfirm: async () => { await api(`/api/storage/multipart/${id}/abort`, 'POST'); setNotice('ok', 'Загрузка отменена'); S.diag = await api('/api/storage/diagnostics'); },
    });
  }

  /* ─── действия ─── */
  async function refreshAfterChange() {
    if (section === 'buckets') await loadSection('buckets');
    else if (section === 'overview') await loadSection('overview', { quiet: true });
    else if (section === 'bucket') await loadBucket();
    else renderBody();
  }

  async function goSection(next, name = null) {
    section = next;
    bucketName = name;
    notice = null;
    const reset = { overview: ['status', 'statusError'], config: ['config', 'configError'], buckets: ['buckets', 'bucketsError'],
      keys: ['creds', 'credsError'], diagnostics: ['diag', 'diagError'] }[next];
    if (reset) { S[reset[0]] = null; S[reset[1]] = ''; }
    if (next === 'bucket') { S.bucket = null; S.objects = []; S.objectsPrefix = ''; S.objectsError = ''; }
    renderShell();
    await loadSection(next);
  }

  async function onClick(event) {
    closeTips(event.target.closest('.sg-tip'));  // клик вне подсказки закрывает открытые
    const el = event.target.closest('[data-sg]');
    if (!el || !container?.contains(el)) return;
    event.preventDefault();
    const d = el.dataset;
    switch (d.sg) {
      case 'section': return goSection(d.section);
      case 'retry': return goSection(section, bucketName);
      case 'recheck': return runChecks();
      case 'run-all-checks': return runChecks();
      case 'run-check': return runChecks([d.name]);
      case 'new-bucket': return openCreateBucket();
      case 'new-key': return openCreateKey();
      case 'run-tests': return runTests();
      case 'tip': {
        const wrap = el.closest('.sg-tip');
        const open = wrap.querySelector('.sg-tip-pop').hidden;
        closeTips(wrap);
        setTip(wrap, open);
        return;
      }
      case 'run-step': return runTests([d.step]);
      case 'restart': return confirmRestart();
      case 'copy-endpoint': return copyWithNotice(container.querySelector('#sg-endpoint')?.textContent || '', 'Endpoint скопирован');
      case 'copy-text': return copyWithNotice(d.text, 'Скопировано');
      case 'secret-mode': captureDraft(); S.secretMode[d.key] = d.mode; return renderBody();
      case 'cfg-preview': return previewConfig();
      case 'cfg-reset': S.draft = {}; S.secretMode = {}; S.applied = null; return renderBody();
      case 'test-saved-telegram': return testSavedTelegram();
      case 'open-bucket': return goSection('bucket', d.name);
      case 'back-buckets': return goSection('buckets');
      case 'delete-bucket': return confirmDeleteBucket(d.name);
      case 'apply-prefix': S.objectsPrefix = container.querySelector('#sg-prefix')?.value.trim() || ''; return loadBucket();
      case 'more-objects': return loadMoreObjects();
      case 'object-info': return openObjectInfo(d.id);
      case 'object-download': return downloadObject(d.id, d.key);
      case 'object-copy-key': return copyWithNotice(d.key, 'Ключ скопирован');
      case 'object-delete': return confirmDeleteObject(d.id, d.key);
      case 'key-rotate': return confirmRotate(d.id);
      case 'key-revoke': return confirmRevoke(d.id);
      case 'key-delete': return confirmDeleteKey(d.id);
      case 'key-edit': return openEditKey(d.id);
      case 'abort-upload': return confirmAbortUpload(d.id);
      default: return undefined;
    }
  }

  /* ─── жизненный цикл модуля ─── */
  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (boundTo !== el) {
      el.addEventListener('click', onClick);
      el.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTips(); });
      boundTo = el;
    }
    container = el;
    renderShell();
    loadSection(section === 'bucket' ? 'bucket' : section);
  }

  function leave() {
    closeModal();
    container = null;
    boundTo = null;
    loadToken += 1;
    Object.assign(S, { status: null, config: null, draft: {}, secretMode: {}, preview: null, applied: null, appliedTelegram: false, testResult: null,
      buckets: null, bucket: null, objects: [], creds: null, diag: null });
    section = 'overview';
    bucketName = null;
    notice = null;
  }

  return { init, leave };
})();
