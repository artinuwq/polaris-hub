/* Files — файловый менеджер поверх Polaris Storage.
   Корень — список bucket-ов, дальше «папки» (общие префиксы ключей по «/»). Можно заходить в папки, создавать их,
   загружать файлы (кнопкой или перетаскиванием, с прогрессом), скачивать и удалять файлы и папки целиком.
   Новые bucket, папка и пустой файл создаются прямо в списке, как в проводнике: появляется строка с полем имени,
   справа — ✓ (создать) и ✕ (отмена); Enter и Esc делают то же самое.
   Текстовые файлы (.txt, .md) открываются по нажатию в редакторе на весь экран: остального интерфейса нет,
   выход — системная кнопка «Назад» Telegram (общий стек window.PolarisBack); несохранённые правки не теряются.
   Загрузка файлов показывается только капсулой рядом с круглой кнопкой: «Файл загружается» / «Файлы загружаются»,
   а прогресс — заливкой капсулы слева направо (зелёная часть = доля загруженного, остальное серое).
   Ходит в те же /api/storage/* (только админ): browse, folders, upload, objects/{id}. */
window.FilesModule = (() => {
  'use strict';

  const PAGE = 200;
  const EDITABLE = /\.(txt|md|markdown)$/i;   // что открывается в редакторе
  const EDIT_MAX = 1024 * 1024;               // больше 1 МиБ в редактор не берём — только скачать
  const IMAGE = /\.(jpe?g|png|gif|webp|avif|bmp)$/i;   // что открывается во встроенном просмотре фото (SVG и HEIC — только скачать)
  const IMAGE_MAX = 60 * 1024 * 1024;         // огромные снимки (RAW и т. п.) лучше скачать
  const VIDEO = /\.(mp4|m4v|webm|mov|ogv)$/i;  // что пробуем играть встроенным плеером (mkv, avi и т. п. — только скачать)
  const isMedia = (name) => IMAGE.test(name) || VIDEO.test(name);
  const ZOOM_MAX = 6;
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
  const ICON_IMAGE = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><circle cx="9" cy="10" r="1.6"></circle><path d="m4 18 5-5 4 4 3-3 4 4"></path></svg>';
  const ICON_VIDEO = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="m10.5 9.5 4 2.5-4 2.5z"></path></svg>';
  const PLAYER_ICONS = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"></path></svg>',
    pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"></path></svg>',
    volume: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"></path><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"></path></svg>',
    muted: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"></path><path d="m16 9.5 5 5M21 9.5l-5 5"></path></svg>',
    full: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"></path></svg>',
    exit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"></path></svg>',
  };
  const PLAYER_KEY = 'polaris_player';   // громкость, «без звука» и скорость запоминаются между просмотрами
  const RATES = [0.5, 1, 1.25, 1.5, 2];
  const ICON_PREV = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7"></path></svg>';
  const ICON_NEXT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"></path></svg>';
  const ICON_FILE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"></path><path d="M14 3v5h5"></path></svg>';

  let container = null;
  let boundTo = null;
  let overlayEl = null;
  let loadToken = 0;
  let uploading = false;

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

  /* Статус рядом с круглой кнопкой внизу. Обычное сообщение («создан», ошибка) выезжает и само исчезает.
     На время загрузки это капсула прогресса: часть слева, равная доле загруженного, залита зелёным, остальное серое. */
  let toastEl = null;
  let toastTimer = 0;
  let pillBusy = false;   // капсула показывает загрузку и не должна пропасть, пока она не закончится
  let heldError = '';     // ошибка, пришедшая во время загрузки: покажем сразу после неё

  function ensureToast() {
    if (toastEl) return toastEl;
    toastEl = document.createElement('div');
    toastEl.id = 'fm-toast';
    toastEl.setAttribute('role', 'status');
    toastEl.innerHTML = '<span class="fm-toast-fill"></span><span class="fm-toast-text"></span>';
    toastEl.addEventListener('click', () => { if (!pillBusy) hideToast(); });
    document.body.appendChild(toastEl);
    return toastEl;
  }

  function showToast(kind, text, { progress = null, ms = 0, repop = true } = {}) {
    const el = ensureToast();
    clearTimeout(toastTimer);
    const keep = el.classList.contains('is-visible') && !repop;  // смена текста без повторного «выезда»
    el.querySelector('.fm-toast-text').textContent = text;
    el.className = `fm-toast is-${kind}${progress === null ? '' : ' has-progress'}${keep ? ' is-visible' : ''}`;
    el.style.setProperty('--p', progress === null ? 0 : progress);
    if (!keep) {
      void el.offsetWidth;  // перезапуск анимации, если статус сменился, пока предыдущий ещё виден
      el.classList.add('is-visible');
    }
    if (ms) toastTimer = setTimeout(hideToast, ms);
  }

  function hideToast() {
    if (pillBusy) return;  // уход в другую папку не должен убирать прогресс загрузки
    clearTimeout(toastTimer);
    toastEl?.classList.remove('is-visible');
  }

  function removeToast() {
    clearTimeout(toastTimer);
    toastEl?.remove();
    toastEl = null;
  }

  function setNotice(kind, text) {
    if (!text) { hideToast(); return; }
    if (pillBusy) { if (kind === 'error') heldError = text; return; }  // «создано» во время загрузки не нужно, ошибку покажем после
    showToast(kind, text, { ms: kind === 'error' ? 6000 : 2400 });
  }

  function nudgeToast() {  // «загрузка уже идёт»: капсула слегка пульсирует вместо ещё одного сообщения
    if (!toastEl) return;
    toastEl.classList.remove('is-nudge');
    void toastEl.offsetWidth;
    toastEl.classList.add('is-nudge');
  }

  /* Доля загруженного по всем файлам с учётом размера: большой файл «весит» больше маленьких */
  function uploadShare(items) {
    const finished = (u) => u.state === 'ok' || u.state === 'error';
    const total = items.reduce((sum, u) => sum + u.size, 0);
    if (!total) return items.length ? items.filter(finished).length / items.length : 0;
    return Math.min(1, items.reduce((sum, u) => sum + (finished(u) ? u.size : u.pct * u.size), 0) / total);
  }

  function paintUploadProgress() {
    if (pillBusy && toastEl) toastEl.style.setProperty('--p', uploadShare(S.uploads).toFixed(3));
  }

  /* Итог загрузки в той же капсуле; возвращает, сколько миллисекунд она ещё на экране */
  function showUploadResult(items) {
    const failed = items.filter((u) => u.state === 'error');
    pillBusy = false;
    if (!failed.length) {
      showToast('ok', items.length > 1 ? 'Файлы загружены' : 'Файл загружен', { progress: 1, ms: 2400, repop: false });
      return 2400;
    }
    const first = failed[0];
    showToast('error', items.length === 1
      ? `Не удалось загрузить «${first.name}»: ${first.error}`
      : `Загружено ${items.length - failed.length} из ${items.length}: «${first.name}» — ${first.error}`,
      { ms: 6000, repop: false });
    return 6000;
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
    const image = IMAGE.test(o.name);
    const video = VIDEO.test(o.name);
    const open = EDITABLE.test(o.name) || image || video
      ? ` is-openable" data-fm="open-file" data-id="${escapeHTML(o.id)}" data-key="${escapeHTML(o.key)}" data-name="${escapeHTML(o.name)}" data-size="${Number(o.size) || 0}" data-etag="${escapeHTML(o.etag || '')}`
      : '';
    return `
      <div class="fm-row${open}">
        <span class="fm-icon">${image ? ICON_IMAGE : video ? ICON_VIDEO : ICON_FILE}</span>
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
      <div id="fm-drop" class="fm-dropzone">${body}${more}</div>`;
  }

  function render() {
    if (!container) return;
    const field = container.querySelector('#fm-draft');
    const caret = field && document.activeElement === field ? [field.selectionStart, field.selectionEnd] : null;
    container.innerHTML = S.bucket ? viewFolder() : viewBuckets();
    if (caret) {  // перерисовка (загрузка списка, прогресс) не должна выбивать из ввода имени
      const again = container.querySelector('#fm-draft');
      if (again && !again.disabled) { again.focus({ preventScroll: true }); again.setSelectionRange(...caret); }
    }
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
    hideToast();
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
    hideToast();
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
    if (uploading) { nudgeToast(); return; }  // капсула прогресса уже на экране
    const existing = new Set(S.objects.map((o) => o.name));
    const dup = files.filter((f) => existing.has(f.name)).map((f) => f.name);
    if (dup.length && !window.confirm(`Файлы с такими именами уже есть и будут заменены:\n${dup.join('\n')}\n\nПродолжить?`)) return;

    const bucket = S.bucket, prefix = S.prefix;
    S.uploads = files.map((f) => ({ name: f.name, size: f.size, pct: 0, state: 'wait', error: '' }));
    uploading = true;
    pillBusy = true;
    heldError = '';
    showToast('progress', files.length > 1 ? 'Файлы загружаются' : 'Файл загружается', { progress: 0 });
    render();  // круглая кнопка на время загрузки неактивна
    for (let i = 0; i < files.length; i += 1) {
      const item = S.uploads[i];
      item.state = 'run';
      try {
        await uploadOne(bucket, `${prefix}${files[i].name}`, files[i], (p) => { item.pct = p; paintUploadProgress(); });
        item.state = 'ok';
      } catch (e) {
        item.state = 'error';
        item.error = errText(e);
      }
      paintUploadProgress();
    }
    uploading = false;
    if (!container) { pillBusy = false; return; }  // ушли со страницы: капсулы уже нет
    if (S.bucket === bucket && S.prefix === prefix) await loadFolder();
    const visibleFor = showUploadResult(S.uploads);
    if (heldError) {  // ошибка, пришедшая во время загрузки, — сразу после итога
      const held = heldError;
      heldError = '';
      setTimeout(() => { if (container) setNotice('error', held); }, visibleFor + 200);
    }
  }

  /* ─── редактор текстовых файлов ─── */
  let E = null;  // открытый редактор: {id, key, name, bucket, el, original, eol, bom, etag, dirty, saving, loaded, touched, fit}

  const inTelegram = () => Boolean(window.Telegram?.WebApp?.initData);

  /* Верхняя панель на время редактирования: капсула показывает имя файла (серая — просто смотрим, жёлтая —
     есть несохранённые правки, нажатие сохраняет), «меню» превращается в «назад». */
  const CAP_ICONS = {
    file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"></path><path d="M14 3v5h5"></path></svg>',
    save: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V3z"></path><path d="M8 3v5h7V3"></path><path d="M8 21v-7h8v7"></path></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"></path></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5v.5"></path><circle cx="12" cy="12" r="9"></circle></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7"></path></svg>',
    image: ICON_IMAGE,
    video: ICON_VIDEO,
  };
  const CAP_STATES = ['is-dirty', 'is-saving', 'is-saved', 'is-error'];

  function chromeNodes() {
    return {
      capsule: document.getElementById('search-capsule'),
      capIcon: document.querySelector('#search-capsule .icon'),
      capText: document.getElementById('search-capsule-text'),
      back: document.getElementById('drawer-toggle'),
      backIcon: document.querySelector('#drawer-toggle .icon'),
    };
  }

  function chromeEnter(mine) {
    const n = chromeNodes();
    mine.chrome = {
      capIcon: n.capIcon?.innerHTML, capText: n.capText?.textContent, capLabel: n.capsule?.getAttribute('aria-label'),
      backIcon: n.backIcon?.innerHTML, backLabel: n.back?.getAttribute('aria-label'),
    };
    if (n.backIcon) n.backIcon.innerHTML = CAP_ICONS.back;
    n.back?.setAttribute('aria-label', 'Назад');
    n.capsule?.classList.add('is-file');
    if (n.capText) n.capText.textContent = mine.name;
  }

  function chromeLeave(mine) {
    const n = chromeNodes();
    const saved = mine.chrome || {};
    n.capsule?.classList.remove('is-file', ...CAP_STATES);
    if (n.capIcon && saved.capIcon != null) n.capIcon.innerHTML = saved.capIcon;
    // подпись возвращаем, только если её не успело сменить приложение (например, переход в другой раздел)
    if (n.capText && saved.capText != null && n.capText.textContent === mine.name) n.capText.textContent = saved.capText;
    if (saved.capLabel != null) n.capsule?.setAttribute('aria-label', saved.capLabel); else n.capsule?.removeAttribute('aria-label');
    n.capsule?.removeAttribute('title');
    if (n.backIcon && saved.backIcon != null) n.backIcon.innerHTML = saved.backIcon;
    if (saved.backLabel != null) n.back?.setAttribute('aria-label', saved.backLabel);
  }

  function paintChrome() {
    if (!E) return;
    const n = chromeNodes();
    const state = E.saving ? 'saving' : (E.flash || (E.dirty ? 'dirty' : 'view'));
    n.capsule?.classList.remove(...CAP_STATES);
    if (state !== 'view') n.capsule?.classList.add(`is-${state}`);
    const icon = { view: 'file', dirty: 'save', saving: 'save', saved: 'check', error: 'alert' }[state];
    if (n.capIcon) n.capIcon.innerHTML = CAP_ICONS[icon];
    const hint = { view: 'Файл открыт', dirty: 'Есть несохранённые изменения — нажмите, чтобы сохранить', saving: 'Сохраняю…', saved: 'Сохранено', error: 'Не сохранено' }[state];
    n.capsule?.setAttribute('aria-label', `${E.name}. ${hint}`);
    n.capsule?.setAttribute('title', hint);
  }

  // на время редактирования кнопки верхней панели не должны уводить из файла и терять правки
  function onChromeClick(e) {
    if (!E && !V) return;
    const hit = (sel) => e.target.closest?.(sel);
    if (hit('#drawer-toggle')) { e.preventDefault(); e.stopPropagation(); if (V) closeViewer(); else requestCloseEditor(); }
    else if (hit('#search-capsule')) { e.preventDefault(); e.stopPropagation(); if (E && E.dirty && !E.saving) saveEditor(); }
    else if (hit('#search-toggle')) { e.preventDefault(); e.stopPropagation(); }
  }

  // нажатие на капсулу не должно забирать фокус у текста: иначе на телефоне закроется клавиатура
  function onChromeMouseDown(e) {
    if (E && e.target.closest?.('#search-capsule')) e.preventDefault();
  }

  function refreshEditor() {
    if (!E) return;
    const field = E.el.querySelector('#fm-editor-text');
    E.dirty = E.loaded && field.value !== E.original;
    paintChrome();
    // пока есть несохранённое, Telegram спросит подтверждение при закрытии мини-приложения
    const tg = window.Telegram?.WebApp;
    if (E.dirty) tg?.enableClosingConfirmation?.(); else tg?.disableClosingConfirmation?.();
  }

  /* Полноэкранный слой (редактор, просмотр фото) лежит под верхней панелью; клавиатура уменьшает видимую область */
  function makeFit(el) {
    const vv = window.visualViewport;
    const bar = document.querySelector('.topbar');
    return () => {
      const visibleTop = vv ? vv.offsetTop : 0;
      const visibleHeight = vv ? vv.height : window.innerHeight;
      const top = Math.max(bar ? bar.getBoundingClientRect().bottom : 0, visibleTop);
      el.style.top = `${top}px`;
      el.style.height = `${Math.max(120, visibleTop + visibleHeight - top)}px`;
    };
  }

  function openEditor(id, key, name, size, etag) {
    if (E || V) return;
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
      <textarea id="fm-editor-text" class="fm-editor-text" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" aria-label="Содержимое файла" disabled></textarea>`;
    const vv = window.visualViewport;
    const fit = makeFit(el);
    E = { id, key, name, bucket: S.bucket, el, original: '', eol: '\n', bom: false, etag: etag || '', dirty: false,
          saving: false, loaded: false, touched: false, flash: '', flashTimer: 0, fit };
    const mine = E;
    el.addEventListener('input', (e) => {
      if (e.target.id !== 'fm-editor-text') return;
      mine.flash = '';  // новая правка: «сохранено»/«ошибка» устарели, дальше решает, есть ли несохранённое
      refreshEditor();
    });
    el.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveEditor(); }
      else if (e.key === 'Escape' && !overlayEl) { e.preventDefault(); requestCloseEditor(); }
      e.stopPropagation();  // «/» и Ctrl+K в тексте не должны открывать глобальный поиск (он увёл бы из файла)
    });
    // жесты внутри редактора не должны открывать меню приложения (свайп от края) и тянуть страницу
    ['touchstart', 'touchmove', 'touchend'].forEach((t) => el.addEventListener(t, (e) => e.stopPropagation(), { passive: true }));
    document.body.appendChild(el);
    document.documentElement.classList.add('fm-editing');
    document.addEventListener('click', onChromeClick, true);
    document.addEventListener('mousedown', onChromeMouseDown, true);
    chromeEnter(mine);
    paintChrome();
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
      closeEditor();
      setNotice('error', errText(e));
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
      mine.flash = 'saved';
      clearTimeout(mine.flashTimer);
      mine.flashTimer = setTimeout(() => { if (E === mine && mine.flash === 'saved') { mine.flash = ''; paintChrome(); } }, 1400);
      refreshEditor();  // «сохранён» скажет только список, когда выйдут из редактора (см. closeEditor)
      return true;
    } catch (e) {
      if (E !== mine) return false;
      mine.saving = false;
      mine.flash = 'error';
      refreshEditor();
      setNotice('error', `Не сохранено: ${Object.values(e.errors || {})[0] || errText(e)}`);
      return false;
    }
  }

  function closeEditor() {
    const mine = E;
    if (!mine) return;
    E = null;
    clearTimeout(mine.flashTimer);
    mine.el.remove();
    document.removeEventListener('click', onChromeClick, true);
    document.removeEventListener('mousedown', onChromeMouseDown, true);
    chromeLeave(mine);
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

  /* ─── просмотр фото ─── */
  let V = null;  // открытый просмотр: {el, stage, img, items, index, name, seq, scale, tx, ty, ...}

  function openViewer(id) {
    if (V || E) return;
    const clicked = S.objects.find((o) => o.id === id);
    if (!clicked) return;
    if (IMAGE.test(clicked.name) && Number(clicked.size) > IMAGE_MAX) {
      setNotice('error', `«${clicked.name}» больше ${Math.round(IMAGE_MAX / 1048576)} МиБ — в просмотре не открывается, его можно скачать`);
      return;
    }
    // листаем фото и видео этой папки; огромные снимки в ленту не берём (их можно только скачать)
    const items = S.objects.filter((o) => (IMAGE.test(o.name) && Number(o.size) <= IMAGE_MAX) || VIDEO.test(o.name));
    const index = items.findIndex((o) => o.id === id);
    if (index < 0) return;
    hideToast();  // прежнее сообщение не должно висеть поверх фото
    const el = document.createElement('div');
    el.className = 'fm-viewer';
    el.id = 'fm-viewer';
    el.tabIndex = -1;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.innerHTML = `
      <div class="fm-viewer-stage">
        <img class="fm-viewer-img" alt="" draggable="false" hidden />
        <div class="fm-player is-paused" hidden>
          <video class="fm-viewer-video" playsinline preload="metadata"></video>
          <div class="fm-player-tap"></div>
          <div class="fm-player-skip is-back" aria-hidden="true">−10 с</div>
          <div class="fm-player-skip is-fwd" aria-hidden="true">+10 с</div>
          <div class="fm-player-spinner" aria-hidden="true"></div>
          <button type="button" class="fm-player-big" data-fmp="toggle" aria-label="Воспроизвести">${PLAYER_ICONS.play}</button>
          <div class="fm-player-controls">
            <div class="fm-seek" role="slider" tabindex="0" aria-label="Перемотка" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">
              <div class="fm-seek-track"><div class="fm-seek-buffer"></div><div class="fm-seek-fill"></div></div>
              <div class="fm-seek-thumb"></div><div class="fm-seek-tip"></div>
            </div>
            <div class="fm-player-row">
              <button type="button" class="fm-pbtn is-main" data-fmp="toggle" aria-label="Воспроизвести">${PLAYER_ICONS.play}</button>
              <span class="fm-ptime">0:00 / 0:00</span>
              <span class="fm-pspacer"></span>
              <button type="button" class="fm-pbtn" data-fmp="mute" aria-label="Выключить звук">${PLAYER_ICONS.volume}</button>
              <input type="range" class="fm-vol" min="0" max="1" step="0.05" value="1" aria-label="Громкость" />
              <button type="button" class="fm-pbtn is-text" data-fmp="rate" aria-label="Скорость воспроизведения">1×</button>
              <button type="button" class="fm-pbtn" data-fmp="fullscreen" aria-label="Во весь экран">${PLAYER_ICONS.full}</button>
            </div>
          </div>
        </div>
        <div class="fm-viewer-status" role="status"></div>
      </div>
      <button type="button" class="fm-viewer-nav is-prev" data-fmv="prev" aria-label="Предыдущее фото">${ICON_PREV}</button>
      <button type="button" class="fm-viewer-nav is-next" data-fmv="next" aria-label="Следующее фото">${ICON_NEXT}</button>
      <div class="fm-viewer-bar">
        <span class="fm-viewer-count"></span><span class="fm-viewer-meta"></span>
        <button type="button" class="fm-viewer-dl" data-fmv="download" aria-label="Скачать" title="Скачать">${ICON_DOWNLOAD}</button>
      </div>`;
    const fit = makeFit(el);
    const vv = window.visualViewport;
    V = { el, stage: el.querySelector('.fm-viewer-stage'), img: el.querySelector('.fm-viewer-img'), video: el.querySelector('.fm-viewer-video'), player: el.querySelector('.fm-player'), kind: 'image', resumeAt: 0, items, index, name: items[index].name,
          seq: 0, scale: 1, tx: 0, ty: 0, pts: new Map(), fit, lastTap: null, gesture: null };
    const mine = V;
    el.addEventListener('click', (e) => {
      const action = e.target.closest('[data-fmv]')?.dataset.fmv;
      if (action === 'prev') stepViewer(-1);
      else if (action === 'next') stepViewer(1);
      else if (action === 'download') downloadFile(mine.items[mine.index].id, mine.items[mine.index].name);
      else if (action === 'retry') showItem(mine.index, true);
    });
    bindViewerGestures(mine);
    mine.img.addEventListener('load', () => {
      if (V !== mine || mine.img.dataset.seq !== String(mine.seq)) return;
      mine.el.classList.remove('is-loading');
      mine.img.hidden = false;
      setViewerStatus('');
      mine.natural = `${mine.img.naturalWidth}×${mine.img.naturalHeight}`;
      paintViewer();
      applyViewerTransform();
    });
    mine.img.addEventListener('error', () => {
      if (V !== mine || mine.img.dataset.seq !== String(mine.seq) || !mine.img.getAttribute('src')) return;
      mine.img.hidden = true;
      mine.el.classList.remove('is-loading');
      setViewerStatus('Не удалось загрузить фото', true);
    });
    bindViewerVideo(mine);
    bindPlayer(mine);
    document.body.appendChild(el);
    const root = document.documentElement;
    root.classList.add('fm-editing', 'fm-viewing');
    document.addEventListener('click', onChromeClick, true);
    document.addEventListener('keydown', onViewerKey, true);
    chromeEnter(mine);
    fit();
    vv?.addEventListener('resize', fit);
    vv?.addEventListener('scroll', fit);
    window.addEventListener('resize', fit);
    window.addEventListener('resize', applyViewerTransform);
    window.PolarisBack?.push('files-viewer', closeViewer);
    el.focus({ preventScroll: true });
    showItem(index);
  }

  // клавиши слушаем на уровне документа: после нажатия кнопки листания (она на краю блокируется) фокус пропадает,
  // а стрелки, Esc и масштаб должны работать всегда
  function onViewerKey(e) {
    if (!V || e.altKey) return;
    const k = e.key;
    let handled = true;
    const video = V.kind === 'video';
    if (k === 'Escape') { if (document.fullscreenElement) document.exitFullscreen(); else closeViewer(); }
    else if (k === 'PageUp') stepViewer(-1);
    else if (k === 'PageDown') stepViewer(1);
    else if ((k === 'ArrowLeft' || k === 'ArrowRight') && video && !e.shiftKey) { seekBy(V, k === 'ArrowLeft' ? -5 : 5); showControls(V); }  // в видео стрелки — перемотка, Shift+стрелка — соседний файл
    else if (k === 'ArrowLeft') stepViewer(-1);
    else if (k === 'ArrowRight') stepViewer(1);
    else if (video && (k === ' ' || k.toLowerCase() === 'k') && !document.activeElement?.closest?.('button, input')) togglePlay(V);
    else if (video && k.toLowerCase() === 'm') { V.video.muted = !V.video.muted; showControls(V); }
    else if (video && k.toLowerCase() === 'f') toggleFullscreen(V);
    else if (video && (k === 'ArrowUp' || k === 'ArrowDown')) { setVolume(V, V.video.volume + (k === 'ArrowUp' ? 0.1 : -0.1)); showControls(V); }
    else if (!video && (k === '+' || k === '=')) zoomViewer(V.scale * 1.4);
    else if (!video && k === '-') zoomViewer(V.scale / 1.4);
    else if (!video && k === '0') resetViewerZoom();
    else handled = k === '/' || ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'k');  // глобальный поиск увёл бы из просмотра
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  }

  function setViewerStatus(text, retry = false, download = false) {
    const box = V?.el.querySelector('.fm-viewer-status');
    if (!box) return;
    box.innerHTML = text
      ? `<span>${escapeHTML(text)}</span><span class="fm-viewer-status-actions">${retry ? '<button type="button" class="tk-btn" data-fmv="retry">Повторить</button>' : ''}${download ? '<button type="button" class="tk-btn" data-fmv="download">Скачать</button>' : ''}</span>`
      : '';
    box.hidden = !text;
  }

  function paintViewer() {
    if (!V) return;
    const item = V.items[V.index];
    const n = chromeNodes();
    const video = VIDEO.test(item.name);
    if (n.capIcon) n.capIcon.innerHTML = video ? CAP_ICONS.video : CAP_ICONS.image;
    if (n.capText) n.capText.textContent = item.name;
    V.name = item.name;
    const position = `${V.index + 1} из ${V.items.length}`;
    n.capsule?.setAttribute('aria-label', `${item.name}. ${video ? 'Видео' : 'Фото'} ${position}`);
    n.capsule?.setAttribute('title', item.name);
    V.el.setAttribute('aria-label', `Просмотр: ${item.name}`);
    V.el.querySelector('.fm-viewer-count').textContent = V.items.length > 1 ? `${V.index + 1} / ${V.items.length}` : '';
    V.el.querySelector('.fm-viewer-meta').textContent = [fmtSize(item.size), V.natural || ''].filter(Boolean).join(' · ');
    V.el.querySelectorAll('.fm-viewer-nav').forEach((b) => { b.hidden = V.items.length < 2; });
    V.el.querySelector('.is-prev').disabled = V.index === 0;
    V.el.querySelector('.is-next').disabled = V.index === V.items.length - 1;
  }

  function stopVideo(v) {
    // освободить сетевое соединение и слот скачивания на сервере: без этого плеер продолжал бы буферизовать
    v.video.pause();
    if (document.fullscreenElement || document.webkitFullscreenElement) (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
    cancelAnimationFrame(v.raf);
    clearTimeout(v.idleTimer);
    if (v.video.getAttribute('src')) { v.video.removeAttribute('src'); v.video.load(); }
    v.player.hidden = true;
    v.el.classList.remove('is-idle');
    v.player.classList.remove('is-buffering', 'is-scrubbing');
  }

  const fmtDuration = (sec) => {
    if (!Number.isFinite(sec) || sec <= 0) return '';
    const t = Math.round(sec), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), r = t % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
  };

  async function showItem(index, retry = false) {
    const v = V;
    if (!v) return;
    const keepAt = retry && v.index === index && v.kind === 'video' ? v.resumeAt : 0;  // «Повторить» продолжает с того же места
    v.index = index;
    v.seq += 1;
    const seq = v.seq;
    const item = v.items[index];
    v.kind = VIDEO.test(item.name) ? 'video' : 'image';
    v.natural = '';
    v.resumeAt = keepAt;
    resetViewerZoom(false);
    v.img.hidden = true;
    v.img.removeAttribute('src');
    stopVideo(v);
    v.el.classList.toggle('is-video', v.kind === 'video');
    document.documentElement.classList.toggle('fm-viewing-video', v.kind === 'video');
    v.el.classList.add('is-loading');
    setViewerStatus('Загрузка…');
    paintViewer();
    try {
      if (v.kind === 'video') {
        // ссылка на несколько часов с поддержкой Range: плеер сам докачивает нужные куски и перематывает без полной загрузки
        const link = await window.PolarisDownload.mediaLink(item.id);
        if (V !== v || v.seq !== seq) return;
        v.video.dataset.seq = String(seq);
        v.video.src = link.url;
        applyPlayerPrefs(v);
        v.player.hidden = false;
        updatePlayerUI(v);
      } else {
        // подписанная ссылка: картинку грузит сам браузер (потоком, без копии в памяти страницы)
        const link = await window.PolarisDownload.link(item.id);
        if (V !== v || v.seq !== seq) return;
        v.img.dataset.seq = String(seq);
        v.img.src = link.url;
      }
    } catch (e) {
      if (V !== v || v.seq !== seq) return;
      v.el.classList.remove('is-loading');
      setViewerStatus(errText(e), e.status !== 415, v.kind === 'video' && e.status === 415);  // 415 — не воспроизводится: повтор не поможет, можно скачать
    }
  }


  /* ─── свой плеер: перемотка с буфером, пауза, звук, скорость, полный экран ─── */
  const fmtClock = (sec) => {
    if (!Number.isFinite(sec) || sec < 0) sec = 0;
    const t = Math.floor(sec), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), r = t % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
  };

  function playerPrefs() {
    try { return { volume: 1, muted: false, rate: 1, ...JSON.parse(localStorage.getItem(PLAYER_KEY) || '{}') }; } catch (e) { return { volume: 1, muted: false, rate: 1 }; }
  }

  function savePlayerPrefs(v) {
    try { localStorage.setItem(PLAYER_KEY, JSON.stringify({ volume: v.video.volume, muted: v.video.muted, rate: v.video.playbackRate })); } catch (e) { /* хранилище недоступно — настройки просто не запомнятся */ }
  }

  function applyPlayerPrefs(v) {
    const p = playerPrefs();
    v.video.volume = Math.max(0, Math.min(1, Number(p.volume)));
    v.video.muted = Boolean(p.muted);
    v.video.defaultPlaybackRate = RATES.includes(Number(p.rate)) ? Number(p.rate) : 1;
    v.video.playbackRate = v.video.defaultPlaybackRate;
  }

  function updatePlayerUI(v) {
    if (!v || !v.ui) return;
    const { video, ui } = v;
    const dur = Number.isFinite(video.duration) ? video.duration : 0;
    const ratio = dur > 0 ? (v.scrubRatio ?? video.currentTime / dur) : 0;
    const pct = `${Math.max(0, Math.min(1, ratio)) * 100}%`;
    ui.fill.style.width = pct;
    ui.thumb.style.left = pct;
    ui.time.textContent = `${fmtClock(dur > 0 ? ratio * dur : video.currentTime)} / ${fmtClock(dur)}`;
    let bufferedEnd = 0;
    for (let i = 0; i < video.buffered.length; i += 1) {
      if (video.buffered.start(i) <= video.currentTime + 0.5 && video.buffered.end(i) > bufferedEnd) bufferedEnd = video.buffered.end(i);
    }
    ui.buffer.style.width = dur > 0 ? `${Math.min(1, bufferedEnd / dur) * 100}%` : '0%';
    const playing = !video.paused;
    v.player.classList.toggle('is-paused', !playing);
    const playLabel = playing ? 'Пауза' : (video.ended ? 'Смотреть снова' : 'Воспроизвести');
    ui.playBtns.forEach((b) => { b.innerHTML = b.classList.contains('fm-player-big') ? PLAYER_ICONS.play : (playing ? PLAYER_ICONS.pause : PLAYER_ICONS.play); b.setAttribute('aria-label', playLabel); });
    const silent = video.muted || video.volume === 0;
    ui.mute.innerHTML = silent ? PLAYER_ICONS.muted : PLAYER_ICONS.volume;
    ui.mute.setAttribute('aria-label', silent ? 'Включить звук' : 'Выключить звук');
    ui.vol.value = String(video.muted ? 0 : video.volume);
    ui.rate.textContent = `${video.playbackRate}×`;
    const full = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
    ui.fs.innerHTML = full ? PLAYER_ICONS.exit : PLAYER_ICONS.full;
    ui.fs.setAttribute('aria-label', full ? 'Выйти из полноэкранного режима' : 'Во весь экран');
    ui.seek.setAttribute('aria-valuemax', String(Math.round(dur)));
    ui.seek.setAttribute('aria-valuenow', String(Math.round(video.currentTime)));
    ui.seek.setAttribute('aria-valuetext', `${fmtClock(video.currentTime)} из ${fmtClock(dur)}`);
  }

  function showControls(v) {
    if (!v) return;
    v.el.classList.remove('is-idle');
    clearTimeout(v.idleTimer);
    if (v.kind === 'video' && !v.video.paused && !v.scrubbing) {
      // во время просмотра элементы управления прячутся, как в привычных плеерах; касание или движение мыши возвращает их
      v.idleTimer = setTimeout(() => { if (V === v && !v.video.paused && !v.scrubbing && !v.overControls) v.el.classList.add('is-idle'); }, 2800);
    }
  }

  function togglePlay(v) {
    if (!v || v.player.hidden) return;
    if (v.video.paused) {
      if (v.video.ended) v.video.currentTime = 0;
      v.video.play().catch(() => {});
    } else v.video.pause();
    updatePlayerUI(v);  // сразу, не дожидаясь событий плеера: иначе кнопки на миг показывают старое состояние
    showControls(v);
  }

  function seekBy(v, delta, side) {
    const video = v.video;
    if (!Number.isFinite(video.duration)) return;
    video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + delta));
    updatePlayerUI(v);
    if (side) {  // двойное касание: короткая подсказка «−10 с / +10 с»
      const hint = v.player.querySelector(side === 'back' ? '.is-back' : '.is-fwd');
      hint.classList.remove('is-on');
      void hint.offsetWidth;
      hint.classList.add('is-on');
    }
  }

  function setVolume(v, value) {
    v.video.volume = Math.max(0, Math.min(1, value));
    if (v.video.volume > 0) v.video.muted = false;
  }

  function toggleFullscreen(v) {
    const doc = document;
    if (doc.fullscreenElement || doc.webkitFullscreenElement) { (doc.exitFullscreen || doc.webkitExitFullscreen).call(doc); return; }
    const el = v.player;
    if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
    else if (v.video.webkitEnterFullscreen) v.video.webkitEnterFullscreen();  // iPhone: только системный полноэкранный режим
  }

  function bindPlayer(v) {
    const q = (sel) => v.player.querySelector(sel);
    const ui = v.ui = {
      fill: q('.fm-seek-fill'), buffer: q('.fm-seek-buffer'), thumb: q('.fm-seek-thumb'), tip: q('.fm-seek-tip'), seek: q('.fm-seek'),
      time: q('.fm-ptime'), mute: q('[data-fmp=mute]'), vol: q('.fm-vol'), rate: q('[data-fmp=rate]'), fs: q('[data-fmp=fullscreen]'),
      playBtns: [...v.player.querySelectorAll('[data-fmp=toggle]')], controls: q('.fm-player-controls'), tap: q('.fm-player-tap'),
    };
    const video = v.video;
    const live = () => V === v;
    const loop = () => { if (!live() || video.paused) return; updatePlayerUI(v); v.raf = requestAnimationFrame(loop); };

    // состояние плеера → интерфейс
    video.addEventListener('play', () => { if (!live()) return; cancelAnimationFrame(v.raf); v.raf = requestAnimationFrame(loop); updatePlayerUI(v); showControls(v); });
    ['pause', 'ended', 'seeked', 'volumechange', 'ratechange', 'durationchange', 'progress'].forEach((t) => video.addEventListener(t, () => { if (live()) updatePlayerUI(v); }));
    ['pause', 'ended'].forEach((t) => video.addEventListener(t, () => { if (live()) showControls(v); }));
    ['volumechange', 'ratechange'].forEach((t) => video.addEventListener(t, () => { if (live() && !v.player.hidden && v.video.getAttribute('src')) savePlayerPrefs(v); }));
    ['waiting', 'stalled', 'seeking'].forEach((t) => video.addEventListener(t, () => { if (live() && (!video.paused || t === 'seeking')) v.player.classList.add('is-buffering'); }));
    ['playing', 'canplay', 'seeked', 'pause'].forEach((t) => video.addEventListener(t, () => { if (live()) v.player.classList.remove('is-buffering'); }));
    document.addEventListener('fullscreenchange', () => { if (live()) updatePlayerUI(v); });

    // кнопки
    v.player.addEventListener('click', (e) => {
      const action = e.target.closest('[data-fmp]')?.dataset.fmp;
      if (!action) return;
      if (action === 'toggle') togglePlay(v);
      else if (action === 'mute') { video.muted = !video.muted; if (!video.muted && video.volume === 0) video.volume = 0.5; updatePlayerUI(v); showControls(v); }
      else if (action === 'rate') { video.playbackRate = RATES[(RATES.indexOf(video.playbackRate) + 1) % RATES.length]; updatePlayerUI(v); savePlayerPrefs(v); showControls(v); }
      else if (action === 'fullscreen') { toggleFullscreen(v); showControls(v); }
    });
    ui.vol.addEventListener('input', () => { setVolume(v, Number(ui.vol.value)); updatePlayerUI(v); savePlayerPrefs(v); showControls(v); });

    // полоса перемотки: во время перетаскивания видео не дёргаем — перематываем один раз при отпускании (один Range-запрос)
    const ratioAt = (x) => { const r = ui.seek.getBoundingClientRect(); return Math.max(0, Math.min(1, (x - r.left) / r.width)); };
    const tipAt = (ratio) => {
      const dur = Number.isFinite(video.duration) ? video.duration : 0;
      ui.tip.textContent = fmtClock(ratio * dur);
      ui.tip.style.left = `${ratio * 100}%`;
    };
    ui.seek.addEventListener('pointerdown', (e) => {
      if (!Number.isFinite(video.duration) || (e.pointerType === 'mouse' && e.button !== 0)) return;
      try { ui.seek.setPointerCapture(e.pointerId); } catch (err) { /* нет активного указателя */ }
      v.scrubbing = true;
      v.scrubRatio = ratioAt(e.clientX);
      v.player.classList.add('is-scrubbing');
      tipAt(v.scrubRatio);
      updatePlayerUI(v);
      showControls(v);
    });
    ui.seek.addEventListener('pointermove', (e) => {
      if (!Number.isFinite(video.duration)) return;
      const ratio = ratioAt(e.clientX);
      tipAt(ratio);
      if (v.scrubbing) { v.scrubRatio = ratio; updatePlayerUI(v); }
    });
    const endScrub = (e) => {
      if (!v.scrubbing) return;
      const ratio = e.type === 'pointercancel' ? null : ratioAt(e.clientX);
      v.scrubbing = false;
      v.scrubRatio = null;
      v.player.classList.remove('is-scrubbing');
      if (ratio !== null && Number.isFinite(video.duration)) video.currentTime = ratio * video.duration;
      updatePlayerUI(v);
      showControls(v);
    };
    ui.seek.addEventListener('pointerup', endScrub);
    ui.seek.addEventListener('pointercancel', endScrub);

    // касания по самому видео: мышь — клик играет/ставит паузу, двойной клик — во весь экран;
    // палец — касание показывает/прячет управление, двойное по краям — ±10 с, по центру — пауза
    ui.tap.addEventListener('pointerup', (e) => {
      v.lastPointerType = e.pointerType;
      if (e.pointerType === 'mouse') { if (e.button === 0) togglePlay(v); return; }
      const now = Date.now();
      const last = v.lastPlayerTap;
      if (last && now - last.t < 320 && Math.abs(e.clientX - last.x) < 50) {
        v.lastPlayerTap = null;
        clearTimeout(v.tapTimer);
        const rect = ui.tap.getBoundingClientRect();
        const zone = (e.clientX - rect.left) / rect.width;
        if (zone < 0.33) seekBy(v, -10, 'back'); else if (zone > 0.67) seekBy(v, 10, 'fwd'); else togglePlay(v);
        showControls(v);
        return;
      }
      v.lastPlayerTap = { t: now, x: e.clientX };
      clearTimeout(v.tapTimer);
      v.tapTimer = setTimeout(() => {
        if (!live()) return;
        if (v.el.classList.contains('is-idle') || video.paused) showControls(v);
        else { clearTimeout(v.idleTimer); v.el.classList.add('is-idle'); }
      }, 320);
    });
    // двойное касание пальцем тоже даёт dblclick, но у него свой смысл (±10 с / пауза): полный экран — только по двойному клику мыши
    ui.tap.addEventListener('dblclick', () => { if (v.lastPointerType === 'mouse') toggleFullscreen(v); });
    // пока мышь над панелью, она не прячется
    ui.controls.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') { v.overControls = true; showControls(v); } });
    ui.controls.addEventListener('pointerleave', () => { v.overControls = false; showControls(v); });
    ui.seek.addEventListener('pointerleave', () => { ui.tip.textContent = ''; });
    v.el.addEventListener('pointermove', (e) => { if (e.pointerType === 'mouse' && v.kind === 'video') showControls(v); });
    // касание по самому видео обрабатывает обработчик выше (показать/спрятать); остальные касания просто продлевают показ
    v.el.addEventListener('pointerdown', (e) => { if (v.kind === 'video' && !e.target.closest('.fm-player-tap')) showControls(v); });
  }

  function bindViewerVideo(v) {
    const video = v.video;
    const current = () => V === v && video.dataset.seq === String(v.seq);
    video.addEventListener('loadedmetadata', () => {
      if (!current()) return;
      v.el.classList.remove('is-loading');
      setViewerStatus('');
      if (v.resumeAt > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(v.resumeAt, video.duration);
      v.natural = [video.videoWidth ? `${video.videoWidth}×${video.videoHeight}` : '', fmtDuration(video.duration)].filter(Boolean).join(' · ');
      paintViewer();
      updatePlayerUI(v);
    });
    video.addEventListener('timeupdate', () => { if (current()) v.resumeAt = video.currentTime; });
    video.addEventListener('error', () => {
      if (!current() || !video.getAttribute('src')) return;
      const code = video.error?.code;
      v.resumeAt = video.currentTime || v.resumeAt;
      video.pause();
      v.player.hidden = true;
      v.el.classList.remove('is-loading');
      // 4 — формат/кодек не поддерживается этим плеером; остальное (сеть, истёкшая ссылка) лечится повтором
      if (code === 4) setViewerStatus('Формат не поддерживается встроенным плеером — файл можно скачать', false, true);
      else setViewerStatus('Не удалось воспроизвести видео', true);
    });
  }

  function stepViewer(delta) {
    if (!V) return;
    const next = V.index + delta;
    if (next < 0 || next >= V.items.length) { bounceViewer(); return; }
    showItem(next);
  }

  function bounceViewer() {  // упёрлись в край: картинка слегка «подпрыгивает»
    const img = V?.img;
    if (!img || V.kind !== 'image' || V.scale > 1) return;
    img.classList.remove('is-bounce');
    void img.offsetWidth;
    img.classList.add('is-bounce');
  }

  /* Масштаб и сдвиг: щипок, двойное касание, колесо мыши, перетаскивание; на 1× горизонтальный свайп листает */
  function viewerLimits() {
    const v = V;
    const w = v.img.offsetWidth * v.scale;
    const h = v.img.offsetHeight * v.scale;
    return { x: Math.max(0, (w - v.stage.clientWidth) / 2), y: Math.max(0, (h - v.stage.clientHeight) / 2) };
  }

  function applyViewerTransform(animate = false) {
    const v = V;
    if (!v) return;
    const lim = viewerLimits();
    v.tx = Math.max(-lim.x, Math.min(lim.x, v.tx));
    v.ty = Math.max(-lim.y, Math.min(lim.y, v.ty));
    v.img.style.transition = animate ? 'transform 0.2s ease' : 'none';
    v.img.style.transform = `translate(${v.tx}px, ${v.ty}px) scale(${v.scale})`;
    v.el.classList.toggle('is-zoomed', v.scale > 1.001);
  }

  function resetViewerZoom(animate = true) {
    if (!V) return;
    V.scale = 1; V.tx = 0; V.ty = 0;
    applyViewerTransform(animate);
  }

  function zoomViewer(scale, cx = null, cy = null, animate = true) {
    const v = V;
    if (!v) return;
    const next = Math.max(1, Math.min(ZOOM_MAX, scale));
    const rect = v.stage.getBoundingClientRect();
    const px = (cx ?? rect.left + rect.width / 2) - (rect.left + rect.width / 2);  // точка относительно центра
    const py = (cy ?? rect.top + rect.height / 2) - (rect.top + rect.height / 2);
    const k = next / v.scale;
    v.tx = px - (px - v.tx) * k;   // точка под пальцем остаётся на месте
    v.ty = py - (py - v.ty) * k;
    v.scale = next;
    if (next === 1) { v.tx = 0; v.ty = 0; }
    applyViewerTransform(animate);
  }

  function bindViewerGestures(v) {
    const stage = v.stage;
    const dist = () => { const [a, b] = [...v.pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
    const mid = () => { const [a, b] = [...v.pts.values()]; return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; };
    stage.addEventListener('pointerdown', (e) => {
      if (v.kind !== 'image' || v.img.hidden || (e.pointerType === 'mouse' && e.button !== 0)) return;
      try { stage.setPointerCapture?.(e.pointerId); } catch (err) { /* нет активного указателя — продолжаем без захвата */ }
      v.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (v.pts.size === 2) {
        const m = mid();
        v.gesture = { kind: 'pinch', d0: dist(), s0: v.scale, mx: m.x, my: m.y, tx0: v.tx, ty0: v.ty };
      } else if (v.pts.size === 1) {
        v.gesture = { kind: 'drag', x0: e.clientX, y0: e.clientY, tx0: v.tx, ty0: v.ty, moved: false, t0: Date.now() };
      }
    });
    stage.addEventListener('pointermove', (e) => {
      if (!v.pts.has(e.pointerId) || !v.gesture) return;
      v.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const g = v.gesture;
      if (g.kind === 'pinch' && v.pts.size === 2) {
        const m = mid();
        const next = Math.max(1, Math.min(ZOOM_MAX, g.s0 * dist() / g.d0));
        const rect = stage.getBoundingClientRect();
        const px = g.mx - (rect.left + rect.width / 2);
        const py = g.my - (rect.top + rect.height / 2);
        const k = next / g.s0;
        v.scale = next;
        v.tx = px - (px - g.tx0) * k + (m.x - g.mx);
        v.ty = py - (py - g.ty0) * k + (m.y - g.my);
        applyViewerTransform();
      } else if (g.kind === 'drag') {
        const dx = e.clientX - g.x0;
        const dy = e.clientY - g.y0;
        if (Math.hypot(dx, dy) > 6) g.moved = true;
        if (v.scale > 1.001) { v.tx = g.tx0 + dx; v.ty = g.ty0 + dy; applyViewerTransform(); }
        else if (g.moved) { v.tx = dx * 0.55; v.ty = 0; v.img.style.transition = 'none'; v.img.style.transform = `translate(${v.tx}px, 0) scale(1)`; }
      }
    });
    const finish = (e) => {
      if (!v.pts.has(e.pointerId)) return;
      const g = v.gesture;
      v.pts.delete(e.pointerId);
      if (!g) return;
      if (g.kind === 'pinch') {
        v.gesture = v.pts.size === 1 ? (() => { const [p] = [...v.pts.values()]; return { kind: 'drag', x0: p.x, y0: p.y, tx0: v.tx, ty0: v.ty, moved: true, t0: 0 }; })() : null;
        if (v.scale < 1.02) resetViewerZoom();
        return;
      }
      v.gesture = null;
      const dx = e.clientX - g.x0;
      const dy = e.clientY - g.y0;
      if (v.scale <= 1.001) {
        if (g.moved && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) { v.tx = 0; stepViewer(dx < 0 ? 1 : -1); applyViewerTransform(true); return; }
        v.tx = 0; v.ty = 0; applyViewerTransform(true);
      }
      if (!g.moved && e.type === 'pointerup') {  // касание без движения: два подряд — увеличить/вернуть
        const now = Date.now();
        const last = v.lastTap;
        if (last && now - last.t < 320 && Math.hypot(e.clientX - last.x, e.clientY - last.y) < 30) {
          v.lastTap = null;
          if (v.scale > 1.001) resetViewerZoom(); else zoomViewer(2.5, e.clientX, e.clientY);
        } else v.lastTap = { t: now, x: e.clientX, y: e.clientY };
      }
    };
    stage.addEventListener('pointerup', finish);
    stage.addEventListener('pointercancel', finish);
    stage.addEventListener('wheel', (e) => {
      if (v.kind !== 'image' || v.img.hidden) return;
      e.preventDefault();
      zoomViewer(v.scale * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY, false);
    }, { passive: false });
    // жесты внутри просмотра не должны открывать меню приложения (свайп от края) и тянуть страницу
    ['touchstart', 'touchmove', 'touchend'].forEach((t) => v.el.addEventListener(t, (e) => e.stopPropagation(), { passive: true }));
  }

  function closeViewer() {
    const mine = V;
    if (!mine) return;
    V = null;
    stopVideo(mine);
    clearTimeout(mine.tapTimer);
    mine.el.remove();
    document.removeEventListener('click', onChromeClick, true);
    document.removeEventListener('keydown', onViewerKey, true);
    chromeLeave(mine);
    document.documentElement.classList.remove('fm-editing', 'fm-viewing', 'fm-viewing-video');
    const vv = window.visualViewport;
    vv?.removeEventListener('resize', mine.fit);
    vv?.removeEventListener('scroll', mine.fit);
    window.removeEventListener('resize', mine.fit);
    window.removeEventListener('resize', applyViewerTransform);
    window.PolarisBack?.pop('files-viewer');
  }

  function openFile(d) {
    if (isMedia(d.name)) openViewer(d.id);
    else openEditor(d.id, d.key, d.name, d.size, d.etag);
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
      case 'open-file': return openFile(d);
      case 'download': return downloadFile(d.id, d.name);
      case 'delete-file': return confirmDeleteFile(d.id, d.name);
      case 'delete-folder': return confirmDeleteFolder(d.prefix, d.name);
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
    closeViewer();
    closeEditor();
    closeModal();
    container = null;
    boundTo = null;
    loadToken += 1;
    Object.assign(S, { buckets: null, bucket: '', prefix: '', folders: [], objects: [], cursor: null, loading: false, error: '', draft: null });
    removeToast();
  }

  return { init, leave };
})();
