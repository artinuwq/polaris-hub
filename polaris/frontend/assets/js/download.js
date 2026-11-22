/* Скачивание объекта Storage.
   В Telegram WebView ссылка blob: с атрибутом download файл не сохраняет (пишет «скачано», а файла нет),
   поэтому берём у сервера короткоживущую подписанную ссылку и отдаём её нативному Telegram.WebApp.downloadFile
   (Telegram сам скачает и положит файл в загрузки). В обычном браузере — обычная ссылка со скачиванием. */
window.PolarisDownload = (() => {
  'use strict';

  function headers() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  async function getLink(objectId) {
    let response;
    try {
      response = await fetch(`/api/storage/objects/${encodeURIComponent(objectId)}/download-link`,
        { method: 'POST', headers: headers(), cache: 'no-store' });
    } catch (e) {
      throw new Error('Нет связи с сервером Polaris');
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail ?? payload.message ?? `HTTP ${response.status}`;
      throw new Error(typeof detail === 'object' ? (detail.message || 'Ошибка запроса') : String(detail));
    }
    return payload.data ?? payload;
  }

  /* Результат: 'started' — Telegram начал скачивание, 'cancelled' — пользователь отказался,
     'external' — старый клиент Telegram: ссылка открыта во внешнем браузере, 'browser' — обычный браузер. */
  async function start(objectId, fileName) {
    const link = await getLink(objectId);
    const url = new URL(link.path, window.location.origin).href;
    const name = fileName || link.file_name || 'file';
    const tg = window.Telegram?.WebApp;
    if (tg?.initData) {
      if (typeof tg.downloadFile === 'function' && tg.isVersionAtLeast?.('8.0')) {
        return new Promise((resolve) => {
          tg.downloadFile({ url, file_name: name }, (accepted) => resolve(accepted ? 'started' : 'cancelled'));
        });
      }
      tg.openLink(url);
      return 'external';
    }
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    return 'browser';
  }

  const MESSAGES = {
    started: (name) => `«${name}»: скачивание запущено — файл появится в загрузках Telegram`,
    cancelled: () => 'Скачивание отменено',
    external: (name) => `«${name}» открыт во внешнем браузере — там и скачается`,
    browser: (name) => `«${name}» скачивается`,
  };

  const message = (result, name) => (MESSAGES[result] || MESSAGES.browser)(name);

  /* Короткоживущий URL без заголовков авторизации — чтобы браузер сам грузил картинку в <img> */
  async function link(objectId) {
    const l = await getLink(objectId);
    return { url: new URL(l.path, window.location.origin).href, file_name: l.file_name, expires_in: l.expires_in };
  }

  return { start, message, link };
})();
