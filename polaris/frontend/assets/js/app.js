(() => {
  const authEl = document.getElementById('auth-status');
  const panelEl = document.getElementById('app-panel');
  const statusEl = document.getElementById('update-status');
  const checkBtn = document.getElementById('check-update');
  const updateBtn = document.getElementById('run-update');

  const tg = window.Telegram?.WebApp;
  tg?.ready?.();
  tg?.expand?.();
  if (tg?.themeParams?.bg_color) {
    document.body.style.background = tg.themeParams.bg_color;
  }

  const params = new URLSearchParams(window.location.search);
  const apiToken =
    params.get('token') ||
    localStorage.getItem('polaris_update_token') ||
    '';

  if (params.get('token')) {
    localStorage.setItem('polaris_update_token', params.get('token'));
  }

  // Как в lumica: берём подписанный initData из официального SDK
  function getInitData() {
    return window.Telegram?.WebApp?.initData || '';
  }

  function setAuth(text, kind) {
    if (!authEl) return;
    authEl.textContent = text;
    authEl.classList.remove('ok', 'err');
    if (kind) authEl.classList.add(kind);
  }

  function headers(extra = {}) {
    const result = { 'Content-Type': 'application/json', ...extra };
    const initData = getInitData();
    if (initData) {
      result['X-Telegram-Init-Data'] = initData;
    }
    if (apiToken) {
      result['X-Polaris-Token'] = apiToken;
    }
    return result;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function setBusy(busy) {
    if (checkBtn) checkBtn.disabled = busy;
    if (updateBtn) updateBtn.disabled = busy;
  }

  async function request(path, method = 'GET', body = undefined) {
    const options = { method, headers: headers(), credentials: 'same-origin' };
    if (body !== undefined) {
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail || data.message || data.error || `HTTP ${response.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function validateUser() {
    const initData = getInitData();
    console.log('initData present:', Boolean(initData), 'length:', initData.length);

    if (!initData && !apiToken) {
      throw new Error(
        'Не удалось получить Telegram Web App initData. Откройте Polaris кнопкой Mini App внутри Telegram, а не обычной ссылкой в браузере.'
      );
    }

    // Как в lumica: явный POST /api/tg/auth с initData в JSON
    if (initData) {
      return request('/api/tg/auth', 'POST', { initData });
    }

    return request('/api/me');
  }

  async function bootstrap() {
    try {
      const payload = await validateUser();
      const name =
        payload.data?.display_name ||
        payload.data?.username ||
        payload.user?.username ||
        'admin';
      setAuth(payload.message || `Доступ разрешён: ${name}`, 'ok');
      if (panelEl) panelEl.hidden = false;
    } catch (err) {
      setAuth(`Нет доступа: ${err.message}`, 'err');
      try {
        tg?.showAlert?.(`Нет доступа: ${err.message}`);
      } catch (_) {
        /* ignore */
      }
    }
  }

  async function checkUpdate() {
    setBusy(true);
    setStatus('Проверяю…');
    try {
      const data = await request('/api/update/status');
      setStatus(data.message || 'Готово');
    } catch (err) {
      setStatus(`Ошибка: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function runUpdate() {
    const ok = window.confirm(
      'Обновить Polaris из git-ветки? Локальные изменения на сервере будут сброшены.'
    );
    if (!ok) return;

    setBusy(true);
    setStatus('Обновляю…');
    try {
      const data = await request('/api/update', 'POST');
      setStatus(data.message || 'Обновлено');
      tg?.HapticFeedback?.notificationOccurred?.('success');
    } catch (err) {
      setStatus(`Ошибка: ${err.message}`);
      tg?.HapticFeedback?.notificationOccurred?.('error');
    } finally {
      setBusy(false);
    }
  }

  checkBtn?.addEventListener('click', checkUpdate);
  updateBtn?.addEventListener('click', runUpdate);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
})();
