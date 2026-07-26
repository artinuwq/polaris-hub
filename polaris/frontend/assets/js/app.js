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

  const initData = tg?.initData || '';

  function setAuth(text, kind) {
    if (!authEl) return;
    authEl.textContent = text;
    authEl.classList.remove('ok', 'err');
    if (kind) authEl.classList.add(kind);
  }

  function headers() {
    const result = { 'Content-Type': 'application/json' };
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

  async function request(path, method = 'GET') {
    const response = await fetch(path, { method, headers: headers() });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail || data.message || `HTTP ${response.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function bootstrap() {
    if (!initData && !apiToken) {
      setAuth('Откройте приложение из Telegram-бота.', 'err');
      return;
    }

    try {
      const me = await request('/api/me');
      const name = me.data?.display_name || me.data?.username || 'admin';
      setAuth(me.message || `Доступ разрешён: ${name}`, 'ok');
      if (panelEl) panelEl.hidden = false;
    } catch (err) {
      setAuth(`Нет доступа: ${err.message}`, 'err');
      tg?.showAlert?.(`Нет доступа: ${err.message}`);
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
  bootstrap();
})();
