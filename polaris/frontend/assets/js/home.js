/* ─── Home Module ───
   Главная: только важное. Дата, четыре карточки (задачи, следующее событие,
   ближайший платёж, серверы) и до трёх ближайших задач. Данные берёт из
   существующих API: /api/tasks, /api/calendar/upcoming, /api/finance/summary,
   /api/v1/servers. Каждый источник грузится отдельно — сбой одного не ломает
   остальные. */
window.HomeModule = (() => {
  'use strict';

  const REFRESH_MS = 60000;
  const MAX_TASKS = 3;
  const PRIORITY_RANK = { fire: 0, important: 1, normal: 2, someday: 3 };
  const WEEKDAYS_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  const CURRENCY_SYMBOLS = { RUB: '₽', EUR: '€', USD: '$', GBP: '£' };

  /* ─── State (живёт между перерисовками экрана) ─── */
  let container = null;
  let timer = null;
  let loading = false;
  let loadedOnce = false;
  let lastLoad = 0;
  const data = { tasks: null, events: null, payment: undefined, servers: null };
  const failed = { tasks: false, events: false, payment: false, servers: false };

  /* ─── Helpers ─── */
  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function requestHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  async function apiGet(path) {
    const response = await fetch(path, { headers: requestHeaders() });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
    return payload.data ?? payload;
  }

  const isLive = () => Boolean(container && container.isConnected);

  function pad(n) { return String(n).padStart(2, '0'); }

  function isoDate(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function dayOffset(isoStr) {
    // Разница в днях между датой YYYY-MM-DD и сегодняшним днём (локально)
    const [y, m, d] = String(isoStr).split('-').map(Number);
    if (!y || !m || !d) return NaN;
    const now = new Date();
    const a = Date.UTC(y, m - 1, d);
    const b = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.round((a - b) / 86400000);
  }

  function plural(n, one, few, many) {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
  }

  /* ─── Data ─── */
  async function loadSource(key, fn) {
    try {
      data[key] = await fn();
      failed[key] = false;
    } catch (err) {
      failed[key] = true;
      console.warn(`[HomeModule] ${key}:`, err);
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    await Promise.all([
      loadSource('tasks', async () => (await apiGet('/api/tasks')).tasks || []),
      loadSource('events', async () => (await apiGet('/api/calendar/upcoming?days_ahead=14')).upcoming || []),
      loadSource('payment', async () => (await apiGet('/api/finance/summary')).next_payment || null),
      loadSource('servers', async () => (await apiGet('/api/v1/servers')).servers || []),
    ]);
    loading = false;
    loadedOnce = true;
    lastLoad = Date.now();
    if (isLive()) render();
  }

  /* ─── Tasks ─── */
  function sortTasks(list) {
    return [...list].sort((a, b) => {
      if (a.date !== b.date) {
        if (!a.date) return 1;
        if (!b.date) return -1;
        return a.date.localeCompare(b.date);
      }
      const ta = a.time || '99:99';
      const tb = b.time || '99:99';
      if (ta !== tb) return ta.localeCompare(tb);
      return (PRIORITY_RANK[a.priority] ?? 99) - (PRIORITY_RANK[b.priority] ?? 99);
    });
  }

  function taskModel() {
    const active = (data.tasks || []).filter((t) => t.status !== 'done');
    // «Близкие»: сегодня, завтра и всё просроченное (просроченное тоже надо сделать сейчас)
    const near = sortTasks(active.filter((t) => t.date && dayOffset(t.date) <= 1));

    let shown;
    let overflow = false;
    if (near.length > MAX_TASKS) {
      shown = near.slice(0, MAX_TASKS);
      overflow = true;
    } else if (near.length === MAX_TASKS) {
      shown = near;
    } else {
      // Меньше трёх близких задач — показываем одну: первую близкую, иначе любую первую
      const first = near[0] || sortTasks(active)[0];
      shown = first ? [first] : [];
    }
    return { total: active.length, nearCount: near.length, shown, overflow };
  }

  function taskWhen(task) {
    if (!task.date) return task.time || 'Без срока';
    const off = dayOffset(task.date);
    let day;
    if (off < 0) day = 'Просрочено';
    else if (off === 0) day = 'Сегодня';
    else if (off === 1) day = 'Завтра';
    else {
      const [y, m, d] = task.date.split('-').map(Number);
      day = new Date(y, m - 1, d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
    }
    return task.time ? `${day} · ${task.time}` : day;
  }

  /* ─── Next event ─── */
  function nextEvent() {
    const now = new Date();
    const nowHM = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const today = isoDate(now);
    for (const day of data.events || []) {
      const events = (day.events || [])
        .filter((ev) => ev.type === 'event')
        .filter((ev) => day.date > today || !ev.time || ev.time >= nowHM)
        .sort((a, b) => (a.time || '').localeCompare(b.time || ''));
      if (events.length) return { date: day.date, ev: events[0] };
    }
    return null;
  }

  function eventWhen(found) {
    // { day: 'Завтра', time: '14:30' } — день показываем, только если событие не сегодня
    const { date, ev } = found;
    const off = dayOffset(date);
    const time = ev.time || 'Весь день';
    if (off <= 0) return { day: '', time };
    if (off === 1) return { day: 'Завтра', time };
    if (off < 7) {
      const [y, m, d] = date.split('-').map(Number);
      return { day: WEEKDAYS_SHORT[new Date(y, m - 1, d).getDay()], time };
    }
    const [y, m, d] = date.split('-').map(Number);
    return { day: new Date(y, m - 1, d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }), time };
  }

  /* ─── Payment / Servers ─── */
  function formatAmount(amount, currency) {
    const symbol = CURRENCY_SYMBOLS[String(currency || '').toUpperCase()] || currency || '';
    const hasFraction = Math.abs(amount % 1) > 0.001;
    const number = new Intl.NumberFormat('ru-RU', {
      minimumFractionDigits: hasFraction ? 2 : 0,
      maximumFractionDigits: 2,
    }).format(amount);
    return `${number} ${symbol}`.trim();
  }

  function daysUntilText(days) {
    if (days == null) return '';
    if (days < 0) return 'Просрочен';
    if (days === 0) return 'Сегодня';
    if (days === 1) return 'Завтра';
    return `Через ${days} ${plural(days, 'день', 'дня', 'дней')}`;
  }

  function serverModel() {
    const list = data.servers || [];
    const online = list.filter((s) => s.status === 'online').length;
    const down = list.filter((s) => s.status === 'offline' || s.status === 'error').length;
    const pending = list.length - online - down;
    let text;
    let tone = '';
    if (!list.length) text = 'Серверов нет';
    else if (down) { text = `Не в сети: ${down}`; tone = 'is-bad'; }
    else if (pending) text = `Ожидают подключения: ${pending}`;
    else { text = 'Все серверы работают'; tone = 'is-good'; }
    return { total: list.length, online, text, tone };
  }

  /* ─── Render ─── */
  function card({ view, title, value, sub, tone = '' }) {
    return `
      <button type="button" class="card home-card ${tone}" data-view="${view}">
        <span class="home-card-title">${escapeHTML(title)}</span>
        <span class="home-card-value">${value}</span>
        <span class="home-card-sub">${escapeHTML(sub)}</span>
      </button>`;
  }

  const NO_DATA = { value: '—', sub: 'Нет данных' };

  function renderCards() {
    const tm = taskModel();
    const cards = [];

    cards.push(failed.tasks && data.tasks === null
      ? card({ view: 'tasks', title: 'Задачи', ...NO_DATA })
      : card({
        view: 'tasks',
        title: 'Задачи',
        value: escapeHTML(tm.total),
        sub: tm.nearCount ? `${tm.nearCount} с близким сроком` : 'Срочных нет',
        tone: tm.nearCount ? 'is-accent' : '',
      }));

    if (failed.events && data.events === null) {
      cards.push(card({ view: 'calendar', title: 'Следующее событие', ...NO_DATA }));
    } else {
      const found = nextEvent();
      if (!found) {
        cards.push(card({ view: 'calendar', title: 'Следующее событие', value: '—', sub: 'Событий нет' }));
      } else {
        const when = eventWhen(found);
        const dayHTML = when.day ? `<span class="home-day">${escapeHTML(when.day)}</span>` : '';
        cards.push(card({
          view: 'calendar',
          title: 'Следующее событие',
          value: `${dayHTML}<span class="home-time">${escapeHTML(when.time)}</span>`,
          sub: found.ev.title,
        }));
      }
    }

    if (failed.payment && data.payment === undefined) {
      cards.push(card({ view: 'finance', title: 'Ближайший платёж', ...NO_DATA }));
    } else if (!data.payment) {
      cards.push(card({ view: 'finance', title: 'Ближайший платёж', value: '—', sub: 'Платежей нет' }));
    } else {
      const p = data.payment;
      cards.push(card({
        view: 'finance',
        title: 'Ближайший платёж',
        value: escapeHTML(formatAmount(p.amount, p.currency)),
        sub: daysUntilText(p.days_until) || p.name,
        tone: p.days_until != null && p.days_until <= 1 ? 'is-warn' : '',
      }));
    }

    if (failed.servers && data.servers === null) {
      cards.push(card({ view: 'servers', title: 'Инфраструктура', ...NO_DATA }));
    } else {
      const sm = serverModel();
      cards.push(card({
        view: 'servers',
        title: 'Инфраструктура',
        value: sm.total ? `${sm.online}/${sm.total}` : '—',
        sub: sm.text,
        tone: sm.tone,
      }));
    }

    return `<div class="home-grid">${cards.join('')}</div>`;
  }

  function renderTasks() {
    if (data.tasks === null) return '';
    const tm = taskModel();
    if (!tm.shown.length) return '';
    const rows = tm.shown.map((task) => {
      const overdue = task.date && dayOffset(task.date) < 0;
      return `
        <button type="button" class="home-task" data-view="tasks">
          <span class="home-task-title">${escapeHTML(task.title)}</span>
          <span class="home-task-when${overdue ? ' is-overdue' : ''}">${escapeHTML(taskWhen(task))}</span>
        </button>`;
    }).join('');
    const warning = tm.overflow
      ? `<button type="button" class="home-warning" data-view="tasks">Внимание. Задач больше трёх, перейдите в раздел «Задачи» для отслеживания всех.</button>`
      : '';
    return `
      <section class="home-section">
        <h2 class="home-section-title">Ближайшие дела</h2>
        <div class="home-tasks">${rows}</div>
        ${warning}
      </section>`;
  }

  function renderDate() {
    const text = new Date().toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
    return `<h1 class="home-date">${escapeHTML(text.charAt(0).toUpperCase() + text.slice(1))}</h1>`;
  }

  function render() {
    if (!container) return;
    if (!loadedOnce) {
      container.innerHTML = `${renderDate()}
        <section class="card skeleton-card">
          <div class="skeleton-line skeleton-line-title"></div>
          <div class="skeleton-line"></div><div class="skeleton-line"></div>
        </section>`;
      return;
    }
    container.innerHTML = `${renderDate()}${renderCards()}${renderTasks()}`;
  }

  /* ─── Init ─── */
  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el) {
      console.warn('[HomeModule] Container not found:', containerId);
      return;
    }
    clearInterval(timer);
    container = el;
    render(); // мгновенно показываем кэш, если он есть
    // экран перерисовывается при любом уведомлении — не дёргаем API чаще раза в 15 с
    if (!loadedOnce || Date.now() - lastLoad > 15000) load();
    timer = setInterval(() => {
      if (!isLive()) { clearInterval(timer); timer = null; return; }
      if (!document.hidden) load();
    }, REFRESH_MS);
  }

  return { init, reload: () => load() };
})();
