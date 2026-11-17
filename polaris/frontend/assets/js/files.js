/* Files — файловый менеджер поверх Polaris Storage.
   Корень — список bucket-ов, дальше «папки» (общие префиксы ключей по «/»). Можно заходить в папки, создавать их,
   загружать файлы (кнопкой или перетаскиванием, с прогрессом), скачивать и удалять файлы и папки целиком.
   Ходит в те же /api/storage/* (только админ): browse, folders, upload, objects/{id}. */
window.FilesModule = (() => {
  'use strict';

  const PAGE = 200;
  const ICON_FOLDER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path></svg>';
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

  /* Загрузка через XMLHttpRequest — только ради прогресса отправки (fetch его не отдаёт). */
  function xhrUpload(bucket, key, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', `/api/storage/buckets/${enc(bucket)}/upload?key=${enc(key)}`);
      Object.entries(authHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v));
      xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
        let payload = {};
        try { payload = JSON.parse(xhr.responseText); } catch (e) { /* не JSON */ }
        reject(detailToError(payload, xhr.status));
      };
      xhr.onerror = () => reject(new ApiError('Нет связи с сервером Polaris', 0));
      xhr.onabort = () => reject(new ApiError('Загрузка прервана', 0));
      xhr.send(file);
    });
  }

  function setNotice(kind, text) { notice = text ? { kind, text } : null; renderNotice(); }

  function renderNotice() {
    const box = container?.querySelector('#fm-msg');
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
    window.PolarisBack?.pop('files-modal');
  }

  function openModal(html, onAction) {
    closeModal();
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

  function promptModal({ title, label, placeholder, hint, confirmLabel, onSubmit }) {
    openModal(`
      <div class="tk-head"><h3>${escapeHTML(title)}</h3></div>
      <div class="tk-field"><label for="fmm-input">${escapeHTML(label)}</label>
        <input id="fmm-input" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${escapeHTML(placeholder || '')}" /></div>
      ${hint ? `<p class="tk-desc">${escapeHTML(hint)}</p>` : ''}
      <p id="fmm-error" class="st-modal-error" hidden></p>
      <div class="tk-actions"><div class="tk-actions-row">
        <button type="button" class="tk-btn primary" data-fmm="submit">${escapeHTML(confirmLabel)}</button>
        <button type="button" class="tk-btn" data-fmm="close">Отмена</button>
      </div></div>`, async (action, btn) => {
      if (action === 'close') { closeModal(); return; }
      if (action !== 'submit') return;
      const input = overlayEl.querySelector('#fmm-input');
      const submit = overlayEl.querySelector('[data-fmm="submit"]');
      submit.disabled = true;
      try {
        await onSubmit(input.value.trim());
        closeModal();
      } catch (e) {
        const first = Object.values(e.errors || {})[0];
        modalError(first || errText(e));
        submit.disabled = false;
      }
    });
    overlayEl.querySelector('#fmm-input')?.focus();
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

  function viewBuckets() {
    if (S.error) return `<p class="st-error">${escapeHTML(S.error)}</p><button type="button" class="st-btn" data-fm="reload">Повторить</button>`;
    if (!S.buckets) return '<p class="st-empty">Загрузка…</p>';
    const rows = S.buckets.map((b) => `
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
        <span class="fm-actions"><button type="button" class="st-btn danger" data-fm="delete-folder" data-prefix="${escapeHTML(f.prefix)}" data-name="${escapeHTML(f.name)}">Удалить</button></span>
      </div>`;
  }

  function fileRow(o) {
    const meta = [fmtSize(o.size), fmtDate(o.last_modified)].filter(Boolean).join(' · ');
    return `
      <div class="fm-row">
        <span class="fm-icon">${ICON_FILE}</span>
        <span class="fm-main"><span class="fm-name">${escapeHTML(o.name)}</span><span class="fm-meta">${escapeHTML(meta)}</span></span>
        <span class="fm-actions">
          <button type="button" class="st-btn" data-fm="download" data-id="${escapeHTML(o.id)}" data-name="${escapeHTML(o.name)}">Скачать</button>
          <button type="button" class="st-btn danger" data-fm="delete-file" data-id="${escapeHTML(o.id)}" data-name="${escapeHTML(o.name)}">Удалить</button>
        </span>
      </div>`;
  }

  function viewFolder() {
    const byName = (a, b) => a.name.localeCompare(b.name, 'ru', { numeric: true });
    const rows = [...S.folders].sort(byName).map(folderRow).join('') + [...S.objects].sort(byName).map(fileRow).join('');
    let body;
    if (S.error) body = `<p class="st-error">${escapeHTML(S.error)}</p><button type="button" class="st-btn" data-fm="reload">Повторить</button>`;
    else if (S.loading && !rows) body = '<p class="st-empty">Загрузка…</p>';
    else if (!rows) body = '<p class="st-empty">Папка пуста. Загрузите файлы кнопкой или перетащите их сюда.</p>';
    else body = `<div class="fm-list">${rows}</div>`;
    const more = S.cursor ? `<button type="button" class="st-btn" data-fm="more" ${S.loading ? 'disabled' : ''}>Показать ещё</button>` : '';
    return `
      ${crumbs()}
      <div class="fm-toolbar">
        <button type="button" class="st-btn primary" data-fm="pick" ${uploading ? 'disabled' : ''}>Загрузить файлы</button>
        <button type="button" class="st-btn" data-fm="new-folder">Новая папка</button>
        <button type="button" class="st-btn" data-fm="reload">Обновить</button>
        <input id="fm-file" type="file" multiple hidden />
      </div>
      <div id="fm-uploads" class="fm-uploads"></div>
      <div id="fm-drop" class="fm-dropzone">${body}${more}</div>`;
  }

  function render() {
    if (!container) return;
    container.innerHTML = `<div id="fm-msg" class="st-msg" hidden></div>${S.bucket ? viewFolder() : viewBuckets()}`;
    renderNotice();
    renderUploads();
  }

  function renderUploads() {
    const box = container?.querySelector('#fm-uploads');
    if (!box) return;
    if (!S.uploads.length) { box.innerHTML = ''; return; }
    const label = { wait: 'в очереди', run: '', ok: 'готово', error: '' };
    const rows = S.uploads.map((u) => `
      <div class="fm-upload is-${u.state}">
        <div class="fm-upload-head"><span class="fm-name">${escapeHTML(u.name)}</span>
          <span class="fm-meta">${escapeHTML(u.state === 'run' ? `${Math.round(u.pct * 100)}%` : (u.state === 'error' ? u.error : label[u.state]))}</span></div>
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
    notice = null;
    if (bucket) loadFolder(); else { S.buckets = null; render(); loadBuckets(); }
  }

  /* ─── действия ─── */
  function openNewBucket() {
    promptModal({
      title: 'Новый bucket', label: 'Имя bucket', placeholder: 'my-files', confirmLabel: 'Создать',
      hint: '3–63 символа: строчные латинские буквы, цифры, «-» и «.».',
      onSubmit: async (name) => {
        await api('/api/storage/buckets', 'POST', { name });
        await loadBuckets();
        setNotice('ok', `Bucket «${name}» создан`);
      },
    });
  }

  function openNewFolder() {
    promptModal({
      title: 'Новая папка', label: 'Имя папки', placeholder: 'photos', confirmLabel: 'Создать',
      hint: 'Можно сразу вложенную: «photos/2026». Пустая папка хранится как маркер и видна в этом списке и в S3.',
      onSubmit: async (name) => {
        const clean = name.split('/').map((p) => p.trim()).filter(Boolean).join('/');
        if (!clean) throw new ApiError('Введите имя папки', 422);
        await api(`/api/storage/buckets/${enc(S.bucket)}/folders`, 'POST', { prefix: `${S.prefix}${clean}/` });
        await loadFolder();
        setNotice('ok', `Папка «${clean}» создана`);
      },
    });
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
        await xhrUpload(bucket, `${prefix}${files[i].name}`, files[i], (p) => { item.pct = p; renderUploads(); });
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

  /* ─── события ─── */
  function onClick(e) {
    const el = e.target.closest('[data-fm]');
    if (!el || !container?.contains(el)) return;
    const d = el.dataset;
    switch (d.fm) {
      case 'open-bucket': return go(d.bucket, '');
      case 'open-folder': return go(S.bucket, d.prefix);
      case 'go': return go(d.bucket, d.prefix);
      case 'reload': return reload();
      case 'more': return loadFolder({ append: true });
      case 'new-bucket': return openNewBucket();
      case 'new-folder': return openNewFolder();
      case 'pick': return container.querySelector('#fm-file')?.click();
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
    closeModal();
    container = null;
    boundTo = null;
    loadToken += 1;
    Object.assign(S, { buckets: null, bucket: '', prefix: '', folders: [], objects: [], cursor: null, loading: false, error: '' });
    notice = null;
  }

  return { init, leave };
})();
