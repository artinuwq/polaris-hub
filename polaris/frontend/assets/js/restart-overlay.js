/* Экран «Идёт перезапуск»: закрывает интерфейс, пока сервис перезапускается, и сам
   перезагружает страницу, когда /health снова отвечает.
   API: PolarisRestart.show(текст, { sub, hold }), .release(текст), .hide().
   hold: true — пока идёт операция (обновление), не перезагружать страницу, даже если /health отвечает;
   release() снимает ожидание, hide() закрывает экран без перезагрузки (например, при ошибке). */
(() => {
  'use strict';

  const POLL_MS = 1500;       // как часто спрашивать /health
  const MIN_WAIT_MS = 4000;   // если сервис «не упал» за это время — считаем перезапуск быстрым
  const TIMEOUT_MS = 120000;  // дальше ждать бессмысленно — предлагаем обновить вручную
  let overlay = null;
  let styleEl = null;
  let session = 0;   // номер показа: устаревшие тики опроса сами останавливаются
  let holding = false;

  const css = `
    .pr-overlay{position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;
      padding:24px;background:var(--background,#090b12);color:var(--text,#f8fafc);text-align:center;
      font-family:inherit}
    .pr-box{max-width:320px}
    .pr-spinner{width:44px;height:44px;margin:0 auto 18px;border-radius:50%;
      border:4px solid rgba(160,167,180,.25);border-top-color:var(--primary,#5ab8ff);animation:pr-spin 1s linear infinite}
    .pr-title{margin:0 0 8px;font-size:18px;font-weight:600}
    .pr-sub{margin:0;color:var(--text-secondary,#a0a7b4);font-size:14px;line-height:1.45}
    .pr-btn{margin-top:18px;padding:10px 18px;border:0;border-radius:12px;background:var(--primary,#5ab8ff);
      color:#06101a;font-size:14px;font-weight:600;cursor:pointer}
    @keyframes pr-spin{to{transform:rotate(360deg)}}`;

  async function ping() {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 3000);
    try {
      const r = await fetch('/health', { cache: 'no-store', signal: ctl.signal });
      return r.ok;
    } catch (_) {
      return false;
    } finally {
      clearTimeout(t);
    }
  }

  function show(message, opts = {}) {
    if (overlay) return;
    const mySession = ++session;
    holding = Boolean(opts.hold);
    const style = document.createElement('style');
    styleEl = style;
    style.textContent = css;
    overlay = document.createElement('div');
    overlay.className = 'pr-overlay';
    overlay.setAttribute('role', 'alert');
    overlay.innerHTML = `
      <div class="pr-box">
        <div class="pr-spinner"></div>
        <h2 class="pr-title"></h2>
        <p class="pr-sub">Подождите, страница загрузится сама, когда сервер поднимется.</p>
      </div>`;
    overlay.querySelector('.pr-title').textContent = message || 'Идёт перезапуск…';
    if (opts.sub) overlay.querySelector('.pr-sub').textContent = opts.sub;
    document.head.appendChild(style);
    document.body.appendChild(overlay);

    const began = Date.now();
    let wentDown = false;
    const tick = async () => {
      const ok = await ping();
      if (mySession !== session || !overlay) return;
      const elapsed = Date.now() - began;
      if (!ok) wentDown = true;
      // во время обновления «живой» сервер ещё не значит, что всё готово — ждём, пока он упадёт и поднимется
      if (ok && (wentDown || (!holding && elapsed >= MIN_WAIT_MS))) {
        overlay.querySelector('.pr-title').textContent = 'Готово, загружаю…';
        setTimeout(() => window.location.reload(), 300);
        return;
      }
      if (elapsed > TIMEOUT_MS) {
        holding = false;
        overlay.querySelector('.pr-spinner').remove();
        overlay.querySelector('.pr-title').textContent = 'Сервер долго не отвечает';
        overlay.querySelector('.pr-sub').textContent = 'Проверьте сервис на сервере или попробуйте обновить страницу.';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pr-btn';
        btn.textContent = 'Обновить страницу';
        btn.addEventListener('click', () => window.location.reload());
        overlay.querySelector('.pr-box').appendChild(btn);
        return;
      }
      setTimeout(tick, POLL_MS);
    };
    setTimeout(tick, POLL_MS);
  }

  function release(message) {
    holding = false;
    if (overlay && message) overlay.querySelector('.pr-title').textContent = message;
  }

  function hide() {
    session += 1;
    holding = false;
    overlay?.remove();
    styleEl?.remove();
    overlay = null;
    styleEl = null;
  }

  window.PolarisRestart = { show, release, hide };
})();
