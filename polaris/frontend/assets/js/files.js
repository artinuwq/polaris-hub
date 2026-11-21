/* Files — файловый менеджер поверх Polaris Storage.
   Корень — список bucket-ов, дальше «папки» (общие префиксы ключей по «/»). Можно заходить в папки, создавать их,
   загружать файлы (кнопкой или перетаскиванием, с прогрессом), скачивать и удалять файлы и папки целиком.
   Новые bucket, папка и пустой файл создаются прямо в списке, как в проводнике: появляется строка с полем имени,
   справа — ✓ (создать) и ✕ (отмена); Enter и Esc делают то же самое.
   Текстовые файлы (.txt, .md) открываются по нажатию в редакторе на весь экран: остального интерфейса нет,
   выход — системная кнопка «Назад» Telegram (общий стек window.PolarisBack); несохранённые правки не теряются.
   Ходит в те же /api/storage/* (только админ): browse, folders, upload, objects/{id}. */
window.FilesModule = (() => {
  'use strict';

  const PAGE = 200;
  const EDITABLE = /\.(txt|md|markdown)$/i;   // что открывается в редакторе
  const EDIT_MAX = 1024 * 1024;               // больше 1 МиБ в редактор не берём — только скачать
  const ICON_FOLDER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path></svg>';
  const ICON_DOWNLOAD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10"></path><path d="m8 10 4 4 4-4"></path><path d="M5 19h14"></path></svg>';
  const ICON_TRASH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16"></path><path d="M9 7V4h6v3"></path><path d="M6 7l1 13h10l1-13"></path><path d="M10 11v6M14 11v6"></path></svg>';
  const ICON_PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>';
  const ICON_UPLOAD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V5"></path><path d="m8 9 4-4 4 4"></path><path d="M5 19h14"></path></svg>';
  const ICON_FILE_PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"></path><path d="M14 3v5h5"></path><path d="M12 12v6M9 15h6"></path></svg>';
  const ICON_FOLDER_PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path><path d="M12 11v5M9.5 13.5h5"></path></svg>';
  const ICON_REFRESH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14-4.5L4 9"></path><path d="M4 4v5h5"></path><path d="M4 13a8 8 0 0 0 14 4.5l2-2.5"></path><path d="M20 20v-5h-5"></path></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"></path></svg>';
  const ICON_CLOSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"></path></svg>';
  const ICON_FILE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"></path><path d="M14 3v5h5"></path></svg>';

  let container = null;
  let boundTo = null;
  let overlayEl = null;
  let loadToken = 0;
  let uploading = false;
  let notice = null;

  const S = {
    buckets: null,            // список bucket-ов (корень)
    bucket: '', prefix: '',   // где мы сейчас
    folders: [], objects: [], cursor: null,
    loading: false, error: '',
    uploads: [],              // [{name, size, pct, state: wait|run|ok|error, error}]
    draft: null,              // новая строка со вводом имени: {kind: bucket|folder|file, value, error, busy}
  };

  /* ─── утилиты ─── */
  const escapeHTML = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const enc = encodeURIComponent;
  const errText = (e) => (e && e.message) || 'Неизвестная ошибка';

  function fmtSize(bytes) {
    if (bytes === null || bytes === undefined) return '—';
    const units = ['Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ'];
    let value = Number(bytes), i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${i === 0 ? value : value.toFixed(value >= 10 ? 1 : 2)} ${units[i]}`;
  }

  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
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
    constructor(message, status, errors) { super(message); this.status = status; this.errors = errors || {}; }
  }

  function detailToError(payload, status) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${status}`;
    if (detail && typeof detail === 'object') return new ApiError(detail.message || 'Ошибка запроса', status, detail.errors);
    return new ApiError(String(detail), status);
  }

  async function api(path, method = 'GET', body = undefined, raw = false) {
    const opts = { method, headers: { 'Content-Type': 'application/json', ...authHeaders() }, cache: 'no-store' };
    if (body !== undefined) opts.body = JSON.stringify(body);
    let response;
    try {
      response = await fetch(path, opts);
    } catch (e) {
      throw new ApiError('Нет связи с сервером Polaris', 0);
    }
    if (raw && response.ok) return response;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw detailToError(payload, response.status);
    return payload.data ?? payload;
  }

  /* Отправка тела через XMLHttpRequest — только ради прогресса (fetch его не отдаёт). onProgress(отправлено, всего). */
  function xhrSend(url, body, contentType, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', url);
      Object.entries(authHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v));
      xhr.setRequestHeader('Content-Type', contentType || 'application/octet-stream');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded, e.total); };
      xhr.onload = () => {
        let payload = {};
        try { payload = JSON.parse(xhr.responseText); } catch (e) { /* не JSON */ }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(payload.data ?? payload); return; }
        reject(detailToError(payload, xhr.status));
      };
      xhr.onerror = () => reject(new ApiError('Нет связи с сервером Polaris', 0));
      xhr.onabort = () => reject(new ApiError('Загрузка прервана', 0));
      xhr.send(body);
    });
  }

  const xhrUpload = (bucket, key, file, onProgress) => xhrSend(
    `/api/storage/buckets/${enc(bucket)}/upload?key=${enc(key)}`, file, file.type, (loaded, total) => onProgress(loaded / total));

  /* Большие файлы режет браузер: части грузятся параллельно (число задаёт сервер), каждая повторяется при сбое,
     потом сервер склеивает объект без повторной заливки. Так нет временного файла на сервере и лимита прокси на файл целиком. */
  const MULTIPART_MIN = 16 * 1024 * 1024;   // файлы не больше — одним запросом
  const PART_TRIES = 4;
  const retryDelay = (attempt) => 1000 * attempt;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // сеть, прокси и «Storage занят» (все слоты загрузки заняты) стоит повторить; остальное — окончательная ошибка
  const retryable = (e) => [0, 429, 502, 503, 504].includes(e.status);

  async function multipartUpload(bucket, key, file, onProgress) {
    const start = await api(`/api/storage/buckets/${enc(bucket)}/multipart`, 'POST', { key, content_type: file.type || undefined });
    const { upload_id: uploadId, part_size: partSize } = start;
    const total = Math.ceil(file.size / partSize);
    const workers = Math.max(1, Math.min(start.max_parallel || 1, total));
    const etags = new Array(total);
    const sent = new Array(total).fill(0);
    let next = 0;
    let failure = null;
    let finished = 0;
    const report = () => onProgress(sent.reduce((a, b) => a + b, 0) / file.size, `${finished}/${total} ч.`);

    async function worker() {
      while (!failure) {
        const index = next;
        next += 1;
        if (index >= total) return;
        const blob = file.slice(index * partSize, Math.min(file.size, (index + 1) * partSize));
        for (let attempt = 1; !failure; attempt += 1) {
          try {
            const part = await xhrSend(`/api/storage/multipart/${uploadId}/parts/${index + 1}`, blob, 'application/octet-stream',
              (loaded) => { sent[index] = loaded; report(); });
            etags[index] = part.etag;
            sent[index] = blob.size;
            finished += 1;
            report();
            break;
          } catch (e) {
            sent[index] = 0;
            if (attempt >= PART_TRIES || !retryable(e)) { failure = failure || e; return; }
            await sleep(retryDelay(attempt));
          }
        }
      }
    }

    report();
    await Promise.all(Array.from({ length: workers }, worker));
    if (failure) {
      api(`/api/storage/multipart/${uploadId}/abort`, 'POST').catch(() => {});  // убрать брошенные части
      throw failure;
    }
    await api(`/api/storage/multipart/${uploadId}/complete`, 'POST',
      { parts: etags.map((etag, i) => ({ part_number: i + 1, etag })) });
  }

  const uploadOne = (bucket, key, file, onProgress) => (file.size > MULTIPART_MIN
    ? multipartUpload(bucket, key, file, onProgress)
    : xhrUpload(bucket, key, file, (p) => onProgress(p, '')));

  function setNotice(kind, text) { notice = text ? { kind, text } : null; renderNotice(); }

  function renderNotice() {
    const box = container?.querySelector('#fm-msg');
    if (!box) return;
    box.hidden = !notice;
    box.className = `st-msg${notice ? ` is-${notice.kind}` : ''}`;
    box.textContent = notice ? notice.text : '';
  }

  /* ─── модальные окна ─── */
  let modalDismiss = null;  // вызывается, если окно закрыли не кнопкой (фон, «Назад»)

  function closeModal() {
    if (!overlayEl) return;
    const dismiss = modalDismiss;
    modalDismiss = null;
    overlayEl.remove();
    overlayEl = null;
    window.PolarisBack?.pop('files-modal');
    if (dismiss) dismiss();
  }

  function openModal(html, onAction, onDismiss = null) {
    closeModal();
    modalDismiss = onDismiss;
    overlayEl = document.createElement('div');
    overlayEl.className = 'tk-overlay';
    overlayEl.innerHTML = `<div class="tk-modal" role="dialog" aria-modal="true">${html}</div>`;
    const openedAt = Date.now();
    overlayEl.addEventListener('click', (e) => {
      if (e.target === overlayEl && Date.now() - openedAt > 350) { closeModal(); return; }
      const btn = e.target.closest('[data-fmm]');
      if (btn) { e.preventDefault(); onAction(btn.dataset.fmm, btn); }
    });
    overlayEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); onAction('submit', e.target); }
    });
    document.body.appendChild(overlayEl);
    window.PolarisBack?.push('files-modal', closeModal);
    return overlayEl;
  }

  function modalError(text) {
    const box = overlayEl?.querySelector('#fmm-error');
    if (box) { box.textContent = text || ''; box.hidden = !text; }
  }

  function confirmModal({ title, text, confirmLabel, onConfirm }) {
    openModal(`
      <div class="tk-head"><h3>${escapeHTML(title)}</h3></div>
      <p class="tk-desc">${escapeHTML(text)}</p>
      <p id="fmm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn danger" data-fmm="confirm">${escapeHTML(confirmLabel)}</button>
        <button type="button" class="tk-btn" data-fmm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'confirm') return;
      btn.disabled = true;
      try {
        await onConfirm();
        closeModal();
      } catch (e) {
        modalError(errText(e));
        btn.disabled = false;
      }
    });
  }

  /* ─── отрисовка ─── */
  function crumbs() {
    const parts = S.prefix.split('/').filter(Boolean);
    const items = [`<button type="button" class="fm-crumb" data-fm="go" data-bucket="" data-prefix="">Файлы</button>`];
    items.push(`<button type="button" class="fm-crumb${parts.length ? '' : ' is-current'}" data-fm="go" data-bucket="${escapeHTML(S.bucket)}" data-prefix="">${escapeHTML(S.bucket)}</button>`);
    let acc = '';
    parts.forEach((part, i) => {
      acc += `${part}/`;
      items.push(`<button type="button" class="fm-crumb${i === parts.length - 1 ? ' is-current' : ''}" data-fm="go" data-bucket="${escapeHTML(S.bucket)}" data-prefix="${escapeHTML(acc)}">${escapeHTML(part)}</button>`);
    });
    return `<nav class="fm-crumbs" aria-label="Путь">${items.join('<span class="fm-sep">/</span>')}</nav>`;
  }

  const DRAFT = {
    bucket: { icon: ICON_FOLDER, label: 'Имя bucket', placeholder: 'my-files',
      hint: '3–63 символа: строчные латинские буквы, цифры, «-» и «.»' },
    folder: { icon: ICON_FOLDER, label: 'Имя папки', placeholder: 'photos',
      hint: 'Можно сразу вложенную: photos/2026' },
    file: { icon: ICON_FILE, label: 'Имя файла', placeholder: 'notes.txt',
      hint: 'Будет создан пустой файл' },
  };

  /* Новая строка: вместо названия — поле ввода, вместо «скачать/удалить» — ✓ и ✕ */
  function draftRow() {
    const d = S.draft;
    if (!d) return '';
    const cfg = DRAFT[d.kind];
    const off = d.busy ? 'disabled' : '';
    return `
      <div class="fm-row is-draft">
        <span class="fm-icon">${cfg.icon}</span>
        <span class="fm-main">
          <input id="fm-draft" class="fm-draft-input" type="text" value="${escapeHTML(d.value)}" placeholder="${escapeHTML(cfg.placeholder)}" aria-label="${escapeHTML(cfg.label)}" aria-describedby="fm-draft-hint" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="255" ${off} />
          <span id="fm-draft-hint" class="fm-meta${d.error ? ' is-error' : ''}"${d.error ? ' role="alert"' : ''}>${escapeHTML(d.error || cfg.hint)}</span>
        </span>
        <span class="fm-actions">
          <button type="button" class="fm-ibtn is-confirm" data-fm="draft-ok" aria-label="Создать" title="Создать" ${off}>${ICON_CHECK}</button>
          <button type="button" class="fm-ibtn" data-fm="draft-cancel" aria-label="Отмена" title="Отмена" ${off}>${ICON_CLOSE}</button>
        </span>
      </div>`;
  }

  function viewBuckets() {
    if (S.error) return `<p class="st-error">${escapeHTML(S.error)}</p><button type="button" class="st-btn" data-fm="reload">Повторить</button>`;
    if (!S.buckets) return '<p class="st-empty">Загрузка…</p>';
    const rows = draftRow() + S.buckets.map((b) => `
      <div class="fm-row is-folder" data-fm="open-bucket" data-bucket="${escapeHTML(b.name)}">
        <span class="fm-icon">${ICON_FOLDER}</span>
        <span class="fm-main"><span class="fm-name">${escapeHTML(b.name)}</span>
          <span class="fm-meta">${b.object_count} файл. · ${escapeHTML(fmtSize(b.total_size))}</span></span>
      </div>`).join('');
    return `
      <div class="sg-toolbar"><h3 class="st-subtitle">Все bucket-ы</h3>
        <button type="button" class="st-btn primary" data-fm="new-bucket">Создать bucket</button></div>
      ${rows ? `<div class="fm-list">${rows}</div>` : '<p class="st-empty">Bucket-ов пока нет. Создайте первый — это корневая папка Storage.</p>'}`;
  }

  function folderRow(f) {
    return `
      <div class="fm-row is-folder" data-fm="open-folder" data-prefix="${escapeHTML(f.prefix)}">
        <span class="fm-icon">${ICON_FOLDER}</span>
        <span class="fm-main"><span class="fm-name">${escapeHTML(f.name.replace(/\/$/, ''))}</span><span class="fm-meta">папка</span></span>
        <span class="fm-actions"><button type="button" class="fm-ibtn is-danger" data-fm="delete-folder" data-prefix="${escapeHTML(f.prefix)}" data-name="${escapeHTML(f.name)}" aria-label="Удалить папку" title="Удалить папку">${ICON_TRASH}</button></span>
      </div>`;
  }

  function fileRow(o) {
    const meta = [fmtSize(o.size), fmtDate(o.last_modified)].filter(Boolean).join(' · ');
    const open = EDITABLE.test(o.name)
      ? ` is-openable" data-fm="open-file" data-id="${escapeHTML(o.id)}" data-key="${escapeHTML(o.key)}" data-name="${escapeHTML(o.name)}" data-size="${Number(o.size) || 0}" data-etag="${escapeHTML(o.etag || '')}`
      : '';
    return `
      <div class="fm-row${open}">
        <span class="fm-icon">${ICON_FILE}</span>
        <span class="fm-main"><span class="fm-name">${escapeHTML(o.name)}</span><span class="fm-meta">${escapeHTML(meta)}</span></span>
        <span class="fm-actions">
          <button type="button" class="fm-ibtn" data-fm="download" data-id="${escapeHTML(o.id)}" data-name="${escapeHTML(o.name)}" aria-label="Скачать" title="Скачать">${ICON_DOWNLOAD}</button>
          <button type="button" class="fm-ibtn is-danger" data-fm="delete-file" data-id="${escapeHTML(o.id)}" data-name="${escapeHTML(o.name)}" aria-label="Удалить" title="Удалить">${ICON_TRASH}</button>
        </span>
      </div>`;
  }

  /* Круглая кнопка справа снизу: по нажатию раскрывает круглые кнопки действий над собой */
  function fabHTML() {
    const item = (action, icon, label, disabled = false) => `
        <div class="fm-fab-item">
          <span class="fm-fab-label">${label}</span>
          <button type="button" class="fm-fab-btn" data-fm="${action}" aria-label="${label}" title="${label}" tabindex="-1" ${disabled ? 'disabled' : ''}>${icon}</button>
        </div>`;
    return `
      <div class="fm-fab-backdrop" data-fm="fab-close"></div>
      <div class="fm-fab" id="fm-fab">
        <div class="fm-fab-menu" role="menu">
          ${item('pick', ICON_UPLOAD, 'Загрузить файл', uploading)}
          ${item('new-file', ICON_FILE_PLUS, 'Создать файл')}
          ${item('new-folder', ICON_FOLDER_PLUS, 'Новая папка')}
          ${item('reload', ICON_REFRESH, 'Обновить')}
        </div>
        <button type="button" class="fm-fab-main" data-fm="fab-toggle" aria-label="Действия" aria-haspopup="true" aria-expanded="false">${ICON_PLUS}</button>
      </div>`;
  }

  function setFab(open) {
    const fab = container?.querySelector('#fm-fab');
    if (!fab) return;
    fab.classList.toggle('is-open', open);
    container.querySelector('.fm-fab-backdrop')?.classList.toggle('is-open', open);
    fab.querySelector('.fm-fab-main')?.setAttribute('aria-expanded', String(open));
    fab.querySelectorAll('.fm-fab-btn').forEach((b) => { b.tabIndex = open ? 0 : -1; });
  }

  function viewFolder() {
    const byName = (a, b) => a.name.localeCompare(b.name, 'ru', { numeric: true });
    const rows = draftRow() + [...S.folders].sort(byName).map(folderRow).join('') + [...S.objects].sort(byName).map(fileRow).join('');
    let body;
    if (S.error) body = `<p class="st-error">${escapeHTML(S.error)}</p><button type="button" class="st-btn" data-fm="reload">Повторить</button>`;
    else if (S.loading && !rows) body = '<p class="st-empty">Загрузка…</p>';
    else if (!rows) body = '<p class="st-empty">Папка пуста. Загрузите файлы кнопкой или перетащите их сюда.</p>';
    else body = `<div class="fm-list">${rows}</div>`;
    const more = S.cursor ? `<button type="button" class="st-btn" data-fm="more" ${S.loading ? 'disabled' : ''}>Показать ещё</button>` : '';
    return `
      ${crumbs()}
      ${fabHTML()}
      <input id="fm-file" type="file" multiple hidden />
      <div id="fm-uploads" class="fm-uploads"></div>
      <div id="fm-drop" class="fm-dropzone">${body}${more}</div>`;
  }

  function render() {
    if (!container) return;
    const field = container.querySelector('#fm-draft');
    const caret = field && document.activeElement === field ? [field.selectionStart, field.selectionEnd] : null;
    container.innerHTML = `<div id="fm-msg" class="st-msg" hidden></div>${S.bucket ? viewFolder() : viewBuckets()}`;
    renderNotice();
    renderUploads();
    if (caret) {  // перерисовка (загрузка списка, прогресс) не должна выбивать из ввода имени
      const again = container.querySelector('#fm-draft');
      if (again && !again.disabled) { again.focus({ preventScroll: true }); again.setSelectionRange(...caret); }
    }
  }

  function renderUploads() {
    const box = container?.querySelector('#fm-uploads');
    if (!box) return;
    if (!S.uploads.length) { box.innerHTML = ''; return; }
    const label = { wait: 'в очереди', run: '', ok: 'готово', error: '' };
    const rows = S.uploads.map((u) => `
      <div class="fm-upload is-${u.state}">
        <div class="fm-upload-head"><span class="fm-name">${escapeHTML(u.name)}</span>
          <span class="fm-meta">${escapeHTML(u.state === 'run' ? `${Math.round(u.pct * 100)}%${u.note ? ` · ${u.note}` : ''}` : (u.state === 'error' ? u.error : label[u.state]))}</span></div>
        <div class="fm-bar"><i style="width:${Math.round((u.state === 'ok' ? 1 : u.pct) * 100)}%"></i></div>
      </div>`).join('');
    box.innerHTML = `${rows}${uploading ? '' : '<button type="button" class="st-btn" data-fm="dismiss-uploads">Скрыть</button>'}`;
  }

  /* ─── загрузка данных ─── */
  async function loadBuckets() {
    const token = ++loadToken;
    S.error = '';
    try {
      const data = await api('/api/storage/buckets');
      if (token !== loadToken || !container) return;
      S.buckets = data.buckets || [];
    } catch (e) {
      if (token !== loadToken || !container) return;
      S.error = errText(e);
    }
    render();
  }

  async function loadFolder({ append = false } = {}) {
    const token = ++loadToken;
    S.loading = true;
    S.error = '';
    if (!append) { S.folders = []; S.objects = []; S.cursor = null; }
    render();
    try {
      const qs = new URLSearchParams({ prefix: S.prefix, limit: String(PAGE) });
      if (append && S.cursor) qs.set('cursor', S.cursor);
      const data = await api(`/api/storage/buckets/${enc(S.bucket)}/browse?${qs}`);
      if (token !== loadToken || !container) return;
      S.folders = append ? S.folders.concat(data.folders || []) : (data.folders || []);
      S.objects = append ? S.objects.concat(data.objects || []) : (data.objects || []);
      S.cursor = data.next_cursor || null;
    } catch (e) {
      if (token !== loadToken || !container) return;
      S.error = errText(e);
    }
    S.loading = false;
    render();
  }

  function reload() { return S.bucket ? loadFolder() : loadBuckets(); }

  function go(bucket, prefix) {
    S.bucket = bucket;
    S.prefix = prefix;
    S.error = '';
    S.draft = null;
    notice = null;
    if (bucket) loadFolder(); else { S.buckets = null; render(); loadBuckets(); }
  }

  /* ─── действия ─── */
  /* ─── создание прямо в списке ─── */
  function focusDraft() {
    const field = container?.querySelector('#fm-draft');
    if (!field || field.disabled) return;
    field.focus({ preventScroll: true });
    field.setSelectionRange(field.value.length, field.value.length);
    field.closest('.fm-row')?.scrollIntoView?.({ block: 'nearest' });
  }

  function startDraft(kind) {
    setFab(false);
    notice = null;
    S.draft = { kind, value: '', error: '', busy: false };
    render();
    focusDraft();  // в том же обработчике нажатия — иначе мобильная клавиатура не откроется
  }

  function cancelDraft() {
    if (!S.draft) return;
    S.draft = null;
    render();
  }

  function onInput(e) {
    if (e.target.id !== 'fm-draft' || !S.draft) return;
    S.draft.value = e.target.value;
    if (S.draft.error) {  // ошибка относилась к прежнему тексту
      S.draft.error = '';
      const hint = container.querySelector('#fm-draft-hint');
      if (hint) { hint.textContent = DRAFT[S.draft.kind].hint; hint.classList.remove('is-error'); hint.removeAttribute('role'); }
    }
  }

  /* Проверка до обращения к серверу: мгновенный ответ и без лишних запросов. Окончательно решает сервер. */
  function checkDraft(kind, raw) {
    const name = raw.trim();
    if (!name) return { error: 'Введите имя' };
    if (kind === 'bucket') {
      if (!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(name) || /^\d+\.\d+\.\d+\.\d+$/.test(name)) {
        return { error: 'Имя bucket: 3–63 символа, строчные латинские буквы, цифры, «-» и «.»; начало и конец — буква или цифра' };
      }
      if ((S.buckets || []).some((b) => b.name === name)) return { error: 'Bucket с таким именем уже есть' };
      return { name };
    }
    if (kind === 'folder') {
      const clean = name.split('/').map((p) => p.trim()).filter(Boolean).join('/');
      if (!clean) return { error: 'Введите имя папки' };
      if (clean.split('/').some((p) => p === '.' || p === '..')) return { error: 'Сегменты «.» и «..» запрещены' };
      if (!clean.includes('/') && S.folders.some((f) => f.name.replace(/\/$/, '') === clean)) return { error: 'Папка с таким именем уже есть' };
      return { name: clean };
    }
    if (name.includes('/')) return { error: 'В имени файла нельзя использовать «/»' };
    if (name === '.' || name === '..') return { error: 'Недопустимое имя файла' };
    if (S.objects.some((o) => o.name === name)) return { error: 'Файл с таким именем уже есть' };
    return { name };
  }

  function showDraftError(text) {
    if (!S.draft) return;
    S.draft.error = text;
    const hint = container?.querySelector('#fm-draft-hint');
    if (hint) { hint.textContent = text; hint.classList.add('is-error'); hint.setAttribute('role', 'alert'); }
    container?.querySelector('#fm-draft')?.focus({ preventScroll: true });
  }

  async function submitDraft() {
    const d = S.draft;
    if (!d || d.busy) return;
    const field = container?.querySelector('#fm-draft');
    if (field) d.value = field.value;
    const check = checkDraft(d.kind, d.value);
    if (check.error) { showDraftError(check.error); return; }

    d.busy = true;
    render();  // строка блокируется, пока идёт запрос
    const { bucket, prefix } = S;
    try {
      if (d.kind === 'bucket') {
        await api('/api/storage/buckets', 'POST', { name: check.name });
      } else if (d.kind === 'folder') {
        await api(`/api/storage/buckets/${enc(bucket)}/folders`, 'POST', { prefix: `${prefix}${check.name}/` });
      } else {
        const key = `${prefix}${check.name}`;
        // список мог быть загружен не целиком, а загрузка по существующему ключу молча перезаписывает файл
        const probe = await api(`/api/storage/buckets/${enc(bucket)}/objects?prefix=${enc(key)}&limit=1`);
        if ((probe.objects || [])[0]?.key === key) throw new ApiError('Файл с таким именем уже есть', 409);
        await xhrUpload(bucket, key, new File([], check.name), () => {});
      }
    } catch (e) {
      if (S.draft !== d || !container) return;
      d.busy = false;
      d.error = Object.values(e.errors || {})[0] || errText(e);
      render();
      focusDraft();
      return;
    }
    if (S.draft !== d || !container) return;  // пока ждали ответа, ушли в другое место
    S.draft = null;
    if (d.kind === 'bucket') await loadBuckets(); else await loadFolder();
    const done = { bucket: `Bucket «${check.name}» создан`, folder: `Папка «${check.name}» создана`, file: `Файл «${check.name}» создан` };
    setNotice('ok', done[d.kind]);
  }

  function confirmDeleteFile(id, name) {
    confirmModal({
      title: 'Удалить файл?', text: `«${name}» будет удалён безвозвратно.`, confirmLabel: 'Удалить',
      onConfirm: async () => {
        await api(`/api/storage/objects/${enc(id)}`, 'DELETE');
        await loadFolder();
        setNotice('ok', `«${name}» удалён`);
      },
    });
  }

  function confirmDeleteFolder(prefix, name) {
    confirmModal({
      title: 'Удалить папку?', confirmLabel: 'Удалить всё',
      text: `Папка «${name.replace(/\/$/, '')}» и ВСЕ файлы внутри неё (включая вложенные папки) будут удалены безвозвратно.`,
      onConfirm: async () => {
        const r = await api(`/api/storage/buckets/${enc(S.bucket)}/folders?prefix=${enc(prefix)}`, 'DELETE');
        await loadFolder();
        setNotice(r.failed ? 'error' : 'ok', r.failed ? `Удалено ${r.deleted}, не удалось удалить ${r.failed}` : `Папка удалена (файлов: ${r.deleted})`);
      },
    });
  }

  async function downloadFile(id, name) {
    setNotice('ok', `Готовлю «${name}»… если Telegram спросит — подтвердите скачивание`);
    try {
      const result = await window.PolarisDownload.start(id, name);
      setNotice('ok', window.PolarisDownload.message(result, name));
    } catch (e) { setNotice('error', `Не удалось скачать: ${errText(e)}`); }
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length || !S.bucket) return;
    if (uploading) { setNotice('error', 'Дождитесь окончания текущей загрузки'); return; }
    const existing = new Set(S.objects.map((o) => o.name));
    const dup = files.filter((f) => existing.has(f.name)).map((f) => f.name);
    if (dup.length && !window.confirm(`Файлы с такими именами уже есть и будут заменены:\n${dup.join('\n')}\n\nПродолжить?`)) return;

    const bucket = S.bucket, prefix = S.prefix;
    S.uploads = files.map((f) => ({ name: f.name, size: f.size, pct: 0, state: 'wait', error: '' }));
    uploading = true;
    render();
    let failed = 0;
    for (let i = 0; i < files.length; i += 1) {
      const item = S.uploads[i];
      item.state = 'run';
      renderUploads();
      try {
        await uploadOne(bucket, `${prefix}${files[i].name}`, files[i], (p, note) => { item.pct = p; item.note = note; renderUploads(); });
        item.state = 'ok';
      } catch (e) {
        item.state = 'error';
        item.error = errText(e);
        failed += 1;
      }
      renderUploads();
    }
    uploading = false;
    if (!container) return;
    if (S.bucket === bucket && S.prefix === prefix) await loadFolder();
    else renderUploads();
    setNotice(failed ? 'error' : 'ok', failed ? `Загружено ${files.length - failed} из ${files.length}` : `Загружено файлов: ${files.length}`);
  }

  /* ─── редактор текстовых файлов ─── */
  let E = null;  // открытый редактор: {id, key, name, bucket, el, original, eol, bom, etag, dirty, saving, loaded, touched, fit}

  const ICON_BACK_CLOSE = ICON_CLOSE;
  const inTelegram = () => Boolean(window.Telegram?.WebApp?.initData);

  function editorState(text, kind = '') {
    const box = E?.el.querySelector('#fm-editor-state');
    if (!box) return;
    box.textContent = text;
    box.className = `fm-editor-state${kind ? ` is-${kind}` : ''}`;
  }

  function refreshEditor() {
    if (!E) return;
    const field = E.el.querySelector('#fm-editor-text');
    E.dirty = E.loaded && field.value !== E.original;
    E.el.querySelector('[data-fme="save"]').disabled = !E.dirty || E.saving;
    if (E.saving) editorState('Сохраняю', '');
    else if (E.dirty) editorState('Изменён', 'dirty');
    else if (E.loaded && E.touched) editorState('Сохранено', '');
    else if (E.loaded) editorState('', '');
    // пока есть несохранённое, Telegram спросит подтверждение при закрытии мини-приложения
    const tg = window.Telegram?.WebApp;
    if (E.dirty) tg?.enableClosingConfirmation?.(); else tg?.disableClosingConfirmation?.();
  }

  function openEditor(id, key, name, size, etag) {
    if (E) return;
    if (Number(size) > EDIT_MAX) {
      setNotice('error', `«${name}» больше 1 МиБ — в редакторе не открывается, его можно скачать`);
      return;
    }
    const el = document.createElement('div');
    el.className = 'fm-editor';
    el.id = 'fm-editor';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', `Редактор: ${name}`);
    el.innerHTML = `
      <header class="fm-editor-bar">
        <button type="button" class="fm-ibtn" data-fme="close" aria-label="Закрыть" title="Закрыть" ${inTelegram() ? 'hidden' : ''}>${ICON_BACK_CLOSE}</button>
        <div class="fm-editor-title"><span class="fm-editor-name">${escapeHTML(name)}</span><span id="fm-editor-state" class="fm-editor-state" role="status">Загрузка</span></div>
        <button type="button" class="st-btn primary" data-fme="save" disabled>Сохранить</button>
      </header>
      <textarea id="fm-editor-text" class="fm-editor-text" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" aria-label="Содержимое файла" disabled></textarea>`;
    const vv = window.visualViewport;
    const fit = () => {  // экранная клавиатура уменьшает видимую область — панель с кнопкой остаётся на виду
      el.style.height = `${vv ? vv.height : window.innerHeight}px`;
      el.style.top = `${vv ? vv.offsetTop : 0}px`;
    };
    E = { id, key, name, bucket: S.bucket, el, original: '', eol: '\n', bom: false, etag: etag || '', dirty: false,
          saving: false, loaded: false, touched: false, fit };
    const mine = E;
    el.addEventListener('click', (e) => {
      const action = e.target.closest('[data-fme]')?.dataset.fme;
      if (action === 'save') saveEditor();
      else if (action === 'close') requestCloseEditor();
    });
    el.addEventListener('input', (e) => { if (e.target.id === 'fm-editor-text') refreshEditor(); });
    el.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveEditor(); }
      else if (e.key === 'Escape' && !overlayEl) { e.preventDefault(); requestCloseEditor(); }
    });
    // жесты внутри редактора не должны открывать меню приложения (свайп от края) и тянуть страницу
    ['touchstart', 'touchmove', 'touchend'].forEach((t) => el.addEventListener(t, (e) => e.stopPropagation(), { passive: true }));
    document.body.appendChild(el);
    document.documentElement.classList.add('fm-editing');
    fit();
    vv?.addEventListener('resize', fit);
    vv?.addEventListener('scroll', fit);
    window.addEventListener('resize', fit);
    window.PolarisBack?.push('files-editor', requestCloseEditor);  // системная кнопка «Назад» Telegram
    loadEditor(mine);
  }

  async function loadEditor(mine) {
    const field = mine.el.querySelector('#fm-editor-text');
    try {
      const response = await api(`/api/storage/objects/${enc(mine.id)}/download`, 'GET', undefined, true);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (E !== mine) return;
      if (bytes.length > EDIT_MAX) throw new ApiError('Файл больше 1 МиБ — в редакторе не открывается', 413);
      let text;
      try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
      catch (e) { throw new ApiError('Файл не в кодировке UTF-8 — открыть его в редакторе нельзя', 415); }
      mine.bom = bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF;
      mine.eol = text.includes('\r\n') ? '\r\n' : '\n';       // поле ввода приводит всё к «\n», на сохранении вернём как было
      mine.original = text.replace(/\r\n/g, '\n');
      field.value = mine.original;
      field.disabled = false;
      mine.loaded = true;
      refreshEditor();
    } catch (e) {
      if (E !== mine) return;
      editorState(errText(e), 'error');
    }
  }

  const editorBytes = () => {
    const field = E.el.querySelector('#fm-editor-text');
    const text = E.eol === '\r\n' ? field.value.replace(/\n/g, '\r\n') : field.value;
    return new Blob([E.bom ? new Uint8Array([0xEF, 0xBB, 0xBF]) : new Uint8Array(0), text],
      { type: /\.(md|markdown)$/i.test(E.name) ? 'text/markdown' : 'text/plain' });
  };

  function askOverwrite(title, text, confirmLabel) {
    return new Promise((resolve) => {
      openModal(`
        <div class="tk-head"><h3>${escapeHTML(title)}</h3></div>
        <p class="tk-desc">${escapeHTML(text)}</p>
        <div class="tk-actions"><div class="tk-actions-row">
          <button type="button" class="tk-btn primary" data-fmm="yes">${escapeHTML(confirmLabel)}</button>
          <button type="button" class="tk-btn" data-fmm="close">Отмена</button>
        </div></div>`, (action) => {
        if (action !== 'yes') { closeModal(); return; }  // закрытие само вызовет onDismiss → resolve(false)
        modalDismiss = null;
        closeModal();
        resolve(true);
      }, () => resolve(false));
    });
  }

  async function saveEditor() {
    const mine = E;
    if (!mine || !mine.dirty || mine.saving) return false;
    mine.saving = true;
    refreshEditor();
    try {
      // кто-то мог изменить файл с момента открытия: без этой проверки мы молча затёрли бы чужие правки
      const probe = await api(`/api/storage/buckets/${enc(mine.bucket)}/objects?prefix=${enc(mine.key)}&limit=1`);
      const current = (probe.objects || []).find((o) => o.key === mine.key);
      if (!current || (mine.etag && current.etag !== mine.etag)) {
        const go = await askOverwrite(
          current ? 'Файл изменился' : 'Файл удалён',
          current ? 'С момента открытия файл изменили в другом месте. Перезаписать его вашей версией?'
            : 'Файл удалили в другом месте. Создать его заново с вашим текстом?',
          current ? 'Перезаписать' : 'Создать заново');
        if (!go || E !== mine) { mine.saving = false; refreshEditor(); return false; }
      }
      const saved = await xhrUpload(mine.bucket, mine.key, editorBytes(), () => {});
      if (E !== mine) return true;
      mine.original = mine.el.querySelector('#fm-editor-text').value;
      mine.etag = saved?.etag || '';
      mine.id = saved?.id || mine.id;
      mine.touched = true;
      mine.saving = false;
      refreshEditor();
      return true;
    } catch (e) {
      if (E !== mine) return false;
      mine.saving = false;
      refreshEditor();
      editorState(`Не сохранено: ${Object.values(e.errors || {})[0] || errText(e)}`, 'error');
      return false;
    }
  }

  function closeEditor() {
    const mine = E;
    if (!mine) return;
    E = null;
    mine.el.remove();
    document.documentElement.classList.remove('fm-editing');
    const vv = window.visualViewport;
    vv?.removeEventListener('resize', mine.fit);
    vv?.removeEventListener('scroll', mine.fit);
    window.removeEventListener('resize', mine.fit);
    window.PolarisBack?.pop('files-editor');
    window.Telegram?.WebApp?.disableClosingConfirmation?.();
    if (mine.touched && container) {  // размер и время в списке изменились
      loadFolder().then(() => setNotice('ok', `«${mine.name}» сохранён`));
    }
  }

  function requestCloseEditor() {
    if (!E || E.saving) return;
    if (!E.dirty) { closeEditor(); return; }
    openModal(`
      <div class="tk-head"><h3>Сохранить изменения?</h3></div>
      <p class="tk-desc">В «${escapeHTML(E.name)}» есть несохранённые правки.</p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-fmm="save">Сохранить</button>
        <button type="button" class="tk-btn danger" data-fmm="discard">Не сохранять</button>
        <button type="button" class="tk-btn" data-fmm="close">Остаться</button>
      </div></div>`, async (action) => {
      if (action === 'discard') { closeModal(); closeEditor(); return; }
      if (action === 'save') {
        closeModal();
        if (await saveEditor()) closeEditor();
        return;
      }
      closeModal();
    });
  }

  /* ─── события ─── */
  function onClick(e) {
    const el = e.target.closest('[data-fm]');
    if (!el || !container?.contains(el)) return;
    const d = el.dataset;
    switch (d.fm) {
      case 'open-bucket': return go(d.bucket, '');
      case 'open-folder': return go(S.bucket, d.prefix);
      case 'go': return go(d.bucket, d.prefix);
      case 'fab-toggle': return setFab(!container.querySelector('#fm-fab')?.classList.contains('is-open'));
      case 'fab-close': return setFab(false);
      case 'new-file': return startDraft('file');
      case 'reload': setFab(false); return reload();
      case 'more': return loadFolder({ append: true });
      case 'new-bucket': return startDraft('bucket');
      case 'new-folder': return startDraft('folder');
      case 'draft-ok': return submitDraft();
      case 'draft-cancel': return cancelDraft();
      case 'pick': setFab(false); return container.querySelector('#fm-file')?.click();
      case 'open-file': return openEditor(d.id, d.key, d.name, d.size, d.etag);
      case 'download': return downloadFile(d.id, d.name);
      case 'delete-file': return confirmDeleteFile(d.id, d.name);
      case 'delete-folder': return confirmDeleteFolder(d.prefix, d.name);
      case 'dismiss-uploads': S.uploads = []; return renderUploads();
      default: return undefined;
    }
  }

  function onChange(e) {
    if (e.target.id !== 'fm-file') return;
    const files = Array.from(e.target.files || []);
    e.target.value = '';  // чтобы тот же файл можно было выбрать повторно
    uploadFiles(files);
  }

  function onDragOver(e) {
    if (!S.bucket || !e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    container.querySelector('#fm-drop')?.classList.add('is-over');
  }

  function onDragLeave(e) {
    if (e.target === container.querySelector('#fm-drop') || !container.contains(e.relatedTarget)) {
      container.querySelector('#fm-drop')?.classList.remove('is-over');
    }
  }

  function onDrop(e) {
    if (!S.bucket || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    container.querySelector('#fm-drop')?.classList.remove('is-over');
    uploadFiles(e.dataTransfer.files);
  }

  /* ─── жизненный цикл ─── */
  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (boundTo !== el) {
      el.addEventListener('click', onClick);
      el.addEventListener('change', onChange);
      el.addEventListener('input', onInput);
      el.addEventListener('keydown', (e) => {
        if (e.target.id === 'fm-draft') {
          if (e.isComposing) return;  // подтверждение ввода в IME — не Enter для формы
          if (e.key === 'Enter') { e.preventDefault(); submitDraft(); } else if (e.key === 'Escape') { e.preventDefault(); cancelDraft(); }
          return;
        }
        if (e.key === 'Escape') setFab(false);
      });
      el.addEventListener('dragover', onDragOver);
      el.addEventListener('dragleave', onDragLeave);
      el.addEventListener('drop', onDrop);
      boundTo = el;
    }
    container = el;
    render();
    reload();
  }

  function leave() {
    closeEditor();
    closeModal();
    container = null;
    boundTo = null;
    loadToken += 1;
    Object.assign(S, { buckets: null, bucket: '', prefix: '', folders: [], objects: [], cursor: null, loading: false, error: '', draft: null });
    notice = null;
  }

  return { init, leave };
})();
