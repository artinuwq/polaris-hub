(() => {
  const statusEl = document.getElementById('update-status');
  const checkBtn = document.getElementById('check-update');
  const updateBtn = document.getElementById('run-update');

  const tg = window.Telegram?.WebApp;
  tg?.ready?.();
  tg?.expand?.();

  // Токен из query (?token=...) или localStorage — тот же, что UPDATE_API_TOKEN
  const params = new URLSearchParams(window.location.search);
  const token =
    params.get('token') ||
    localStorage.getItem('polaris_update_token') ||
    '';

  if (params.get('token')) {
    localStorage.setItem('polaris_update_token', params.get('token'));
  }

  function headers() {
    const result = { 'Content-Type': 'application/json' };
    if (token) {
      result['X-Polaris-Token'] = token;
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
      throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    }
    return data;
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
    const ok = window.confirm('Обновить Polaris из git-ветки? Локальные изменения на сервере будут сброшены.');
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
})();
