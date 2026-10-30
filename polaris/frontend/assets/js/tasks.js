/* ─── Tasks Module ─── */
window.TasksModule = (() => {
  'use strict';

  /* ─── Общий стек для системной кнопки "назад" Telegram ───
     Единая точка правды: любой модуль (app.js, tasks.js) регистрирует
     свой оверлей через push(id, onClose) и снимает через pop(id).
     Кнопка видна, пока стек не пуст; по нажатию закрывается верхний оверлей. */
  window.PolarisBack = window.PolarisBack || (() => {
    const stack = [];
    function sync() {
      const bb = window.Telegram?.WebApp?.BackButton;
      if (!bb) return;
      try {
        if (stack.length > 0) bb.show(); else bb.hide();
      } catch (e) { /* ignore */ }
    }
    function handleClick() {
      const top = stack[stack.length - 1];
      if (top) top.onClose();
    }
    function push(id, onClose) {
      if (stack.some((e) => e.id === id)) return;
      stack.push({ id, onClose });
      sync();
    }
    function pop(id) {
      const idx = stack.findIndex((e) => e.id === id);
      if (idx !== -1) stack.splice(idx, 1);
      sync();
    }
    window.Telegram?.WebApp?.BackButton?.onClick?.(handleClick);
    return { push, pop };
  })();

  /* ─── Icons ─── */
  const ICONS = {
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 13l4 4L19 7"></path></svg>',
    calendar: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="6" width="16" height="14" rx="2"></rect><path d="M8 4v3M16 4v3M4 10h16"></path></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>',
    repeat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 13.4-5.9"></path><path d="M20 12a8 8 0 0 1-13.4 5.9"></path><path d="m14 4.5 3.5 2-.5-4"></path><path d="m10 19.5-3.5-2 .5 4"></path></svg>',
    flag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 21V5a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H8a3 3 0 0 0-3 3z"></path></svg>',
    tag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h7l9 9-7 7-9-9V4z"></path><circle cx="8" cy="8" r="1.5" fill="currentColor"></circle></svg>',
    folder: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7l-2-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2z"></path></svg>',
    list: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="3"></rect><path d="M8 9h8M8 13h5"></path></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"></path></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><path d="M12 8v4M12 16h.01"></path></svg>',
    history: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>',
    chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>',
    energy: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>',
    bell: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>',
    chevronDown: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg>',
    chevronRight: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"></path></svg>',
  };

  /* ─── Constants ─── */
  const PRIORITIES = [
    { id: 'fire', label: 'Срочно' },
    { id: 'important', label: 'Важно' },
    { id: 'normal', label: 'Обычная' },
    { id: 'someday', label: 'Когда-нибудь' },
  ];

  const STATUSES = [
    { id: 'todo', label: 'К выполнению' },
    { id: 'in-progress', label: 'В работе' },
    { id: 'waiting', label: 'Ожидание' },
    { id: 'done', label: 'Готово' },
  ];

  const REPEAT_OPTIONS = [
    { id: 'never', label: 'Никогда' },
    { id: 'daily', label: 'Каждый день' },
    { id: 'weekly', label: 'Каждую неделю' },
    { id: 'monthly', label: 'Каждый месяц' },
    { id: 'yearly', label: 'Каждый год' },
    { id: 'custom', label: 'Другое...' },
  ];

  const ENERGY_OPTIONS = [
    { id: 'quick', label: '🟢 Быстро', desc: '10 минут' },
    { id: 'medium', label: '🟡 Средняя', desc: '30–60 минут' },
    { id: 'large', label: '🔴 Большая', desc: '2 часа+' },
  ];

  const REMIND_OPTIONS = [
    { label: 'Через час', value: '1h' },
    { label: 'Вечером', value: 'evening' },
    { label: 'Завтра', value: 'tomorrow' },
    { label: 'Через неделю', value: '1w' },
  ];

  const TAG_RULES = [
    { keyword: 'polaris', tag: 'polaris' },
    { keyword: 'lumica', tag: 'lumica' },
    { keyword: 'linux', tag: 'linux' },
    { keyword: 'python', tag: 'python' },
    { keyword: 'telegram', tag: 'telegram' },
    { keyword: 'idea', tag: 'idea' },
    { keyword: 'дом', tag: 'дом' },
    { keyword: 'домом', tag: 'дом' },
    { keyword: 'учеба', tag: 'учеба' },
    { keyword: 'учёба', tag: 'учеба' },
    { keyword: 'личное', tag: 'личное' },
    { keyword: 'сервер', tag: 'servers' },
    { keyword: 'оплатить', tag: 'finance' },
    { keyword: 'оплата', tag: 'finance' },
  ];

  const CACHE_KEY = 'polaris.tasks.cache';

  /* ─── State ─── */
  let tasks = [];
  let projects = [];
  let tags = [];
  let selectedTaskId = null;
  let createModalOpen = false;
  let view = 'list'; // 'list' | 'done'
  let loading = false;
  let error = '';
  let apiToken = '';
  let initialized = false;

  /* ─── Helpers ─── */
  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function hydrateIcons(root) {
    root.querySelectorAll('[data-icon]').forEach((node) => {
      const name = node.dataset.icon || 'chevron';
      node.innerHTML = ICONS[name] || ICONS.chevron;
    });
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    const months = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    return `${d.getDate()} ${months[d.getMonth()]}`;
  }

  function formatDateFull(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
    return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
  }

  function formatTime(timeStr) {
    if (!timeStr) return '';
    return timeStr.slice(0, 5);
  }

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function inDaysStr(n) {
    const d = new Date();
    d.setDate(d.getDate() + n);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function isToday(dateStr) { return dateStr === todayStr(); }
  function isTomorrow(dateStr) { return dateStr === inDaysStr(1); }
  function isThisWeek(dateStr) {
    if (!dateStr) return false;
    const d = new Date(dateStr);
    const now = new Date();
    const weekEnd = new Date();
    weekEnd.setDate(weekEnd.getDate() + 7);
    return d >= now && d <= weekEnd;
  }
  function isOverdue(dateStr) {
    if (!dateStr) return false;
    return dateStr < todayStr();
  }

  function nowISO() {
    return new Date().toISOString();
  }

  function parseSmartDate(text) {
    const lower = text.toLowerCase().trim();
    const timeMatch = lower.match(/\bв\s+(\d{1,2})(?::(\d{2}))?\b/);
    let hours = null;
    let minutes = 0;
    if (timeMatch) {
      hours = parseInt(timeMatch[1], 10);
      minutes = timeMatch[2] ? parseInt(timeMatch[2], 10) : 0;
    }

    let date = null;
    let cleanText = lower.replace(/\bв\s+\d{1,2}(?::\d{2})?\b/, '').trim();

    if (/\bсегодня\b/.test(cleanText)) {
      date = new Date();
    } else if (/\bзавтра\b/.test(cleanText)) {
      date = new Date();
      date.setDate(date.getDate() + 1);
    } else if (/\bпослезавтра\b/.test(cleanText)) {
      date = new Date();
      date.setDate(date.getDate() + 2);
    } else if (/\bчерез\s+(\d+)\s+дн[ьяей]\b/.test(cleanText)) {
      const match = cleanText.match(/\bчерез\s+(\d+)\s+дн[ьяей]\b/);
      if (match) { date = new Date(); date.setDate(date.getDate() + parseInt(match[1], 10)); }
    } else {
      const months = {
        'января': 0, 'февраля': 1, 'марта': 2, 'апреля': 3, 'мая': 4, 'июня': 5,
        'июля': 6, 'августа': 7, 'сентября': 8, 'октября': 9, 'ноября': 10, 'декабря': 11,
        'янв': 0, 'фев': 1, 'мар': 2, 'апр': 3, 'май': 4, 'июн': 5,
        'июл': 6, 'авг': 7, 'сен': 8, 'окт': 9, 'ноя': 10, 'дек': 11,
      };
      const monthPattern = Object.keys(months).join('|');
      const dateMatch = cleanText.match(new RegExp(`(\\d{1,2})\\s+(${monthPattern})(?:\\s+(\\d{4}))?`));
      if (dateMatch) {
        const day = parseInt(dateMatch[1], 10);
        const month = months[dateMatch[2]];
        const year = dateMatch[3] ? parseInt(dateMatch[3], 10) : new Date().getFullYear();
        date = new Date(year, month, day);
        const now = new Date();
        if (date < now && !dateMatch[3]) date.setFullYear(date.getFullYear() + 1);
      }
    }

    if (date && hours !== null) {
      date.setHours(hours, minutes, 0, 0);
    }

    if (date) {
      const y = date.getFullYear();
      const m = String(date.getMonth() + 1).padStart(2, '0');
      const d = String(date.getDate()).padStart(2, '0');
      return {
        date: `${y}-${m}-${d}`,
        time: hours !== null ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}` : '',
      };
    }
    return { date: '', time: '' };
  }

  /* ─── API ─── */
  function apiHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    if (apiToken) h['X-Polaris-Token'] = apiToken;
    return h;
  }

  async function apiRequest(path, method = 'GET', body = undefined) {
    const opts = { method, headers: apiHeaders() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const resp = await fetch(path, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
    return data;
  }

  /* ─── Cache ─── */
  function loadCache() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* ignore */ }
    return null;
  }

  function saveCache(data) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(data));
    } catch (e) { /* ignore */ }
  }

  function clearCache() {
    try { localStorage.removeItem(CACHE_KEY); } catch (e) { /* ignore */ }
  }

  /* ─── Load Data ─── */
  async function loadFromServer() {
    loading = true;
    error = '';
    render();

    try {
      const result = await apiRequest('/api/tasks');
      const data = result.data || result;

      tasks = data.tasks || [];
      projects = data.projects || [];
      tags = data.tags || [];

      saveCache({ tasks, projects, tags });
      loading = false;
      error = '';
    } catch (e) {
      loading = false;
      error = e.message;
      // Keep cache data if we have it
      const cached = loadCache();
      if (cached) {
        tasks = cached.tasks || [];
        projects = cached.projects || [];
        tags = cached.tags || [];
      }
    }

    render();
    refreshQuick();
  }

  /* ─── Sorting (по срочности, без разделения на группы) ─── */
  const PRIORITY_ORDER = { fire: 0, important: 1, normal: 2, someday: 3 };

  function urgencyRank(task) {
    if (!task.date) return 4;
    if (isOverdue(task.date)) return 0;
    if (isToday(task.date)) return 1;
    if (isThisWeek(task.date)) return 2;
    return 3;
  }

  function compareByUrgency(a, b) {
    const ra = urgencyRank(a);
    const rb = urgencyRank(b);
    if (ra !== rb) return ra - rb;
    const pa = PRIORITY_ORDER[a.priority] ?? 99;
    const pb = PRIORITY_ORDER[b.priority] ?? 99;
    if (pa !== pb) return pa - pb;
    if (a.date && b.date && a.date !== b.date) return a.date.localeCompare(b.date);
    return (a.time || '99:99').localeCompare(b.time || '99:99');
  }

  const activeTasks = () => tasks.filter((t) => t.status !== 'done').sort(compareByUrgency);
  const doneTasks = () => tasks
    .filter((t) => t.status === 'done')
    .sort((a, b) => (b.done_at || b.updated_at || '').localeCompare(a.done_at || a.updated_at || ''));
  const findTask = (id) => tasks.find((t) => String(t.id) === String(id));

  /* ─── Render: страница ─── */
  const hasTgBack = () => Boolean(window.Telegram?.WebApp?.initData);

  function render() {
    const container = document.getElementById('tasks-container');
    if (!container) return;

    let html = view === 'done' ? renderDoneView() : renderListView();
    container.innerHTML = html;
    hydrateIcons(container);

    if (createModalOpen) renderCreateModal();
  }

  function renderListView() {
    let html = `
      <div class="tasks-toolbar">
        <button class="tasks-add-button" type="button" data-action="tk-create" ${loading ? 'disabled' : ''}>
          <span class="icon" data-icon="plus" aria-hidden="true"></span>
          <span>Добавить задачу</span>
        </button>
        <button class="tasks-done-button" type="button" data-action="tk-open-done">Выполненные</button>
      </div>`;

    if (loading && tasks.length === 0) {
      return `${html}<div class="tasks-empty"><p>Загружаю задачи…</p></div>`;
    }
    if (error) html += `<div class="auth-banner err">${escapeHTML(error)}</div>`;

    const list = activeTasks();
    if (!list.length) {
      html += `
        <div class="tasks-empty">
          <span class="icon" data-icon="list" aria-hidden="true"></span>
          <p>${tasks.length ? 'Все задачи выполнены.' : 'Нет задач. Создайте первую.'}</p>
        </div>`;
    } else {
      html += `<div class="tasks-list">${list.map((t) => renderTaskCard(t)).join('')}</div>`;
    }
    return html;
  }

  function renderDoneView() {
    const list = doneTasks();
    const back = hasTgBack() ? '' : `
      <button class="tasks-back-button" type="button" data-action="tk-close-done" aria-label="Назад">
        <span class="icon" data-icon="back" aria-hidden="true"></span>
      </button>`;
    return `
      <div class="tasks-done-head">${back}<h2>Выполненные${list.length ? ` · ${list.length}` : ''}</h2></div>
      ${list.length
        ? `<div class="tasks-list">${list.map((t) => renderTaskCard(t)).join('')}</div>`
        : '<div class="tasks-empty"><span class="icon" data-icon="check" aria-hidden="true"></span><p>Пока ничего не выполнено.</p></div>'}`;
  }

  function projectDot(color) {
    return color
      ? `<span class="task-project-dot" style="background:${escapeHTML(color)}"></span>`
      : '';
  }

  // Компактная карточка: заголовок + одна строка параметров, кнопка напоминания справа
  function renderTaskCard(task) {
    const isDone = task.status === 'done';
    const overdue = !isDone && task.date && isOverdue(task.date);
    const chips = [];

    if (task.date || task.time) {
      const text = [task.date ? formatDate(task.date) : '', task.time ? formatTime(task.time) : ''].filter(Boolean).join(' ');
      chips.push(`<span class="task-meta-item${overdue ? ' overdue' : ''}"><span class="icon" data-icon="${task.date ? 'calendar' : 'clock'}" aria-hidden="true"></span>${escapeHTML(text)}</span>`);
    }

    const energy = ENERGY_OPTIONS.find((e) => e.id === task.energy);
    if (energy) chips.push(`<span class="task-meta-item" title="${escapeHTML(energy.desc)}">${energy.label.split(' ')[0]}</span>`);

    if (task.repeat && task.repeat !== 'never') {
      chips.push(`<span class="task-meta-item" title="${escapeHTML(REPEAT_OPTIONS.find((r) => r.id === task.repeat)?.label || '')}"><span class="icon" data-icon="repeat" aria-hidden="true"></span></span>`);
    }
    if (task.project) {
      chips.push(`<span class="task-meta-item">${projectDot(task.project_color)}${escapeHTML(task.project)}</span>`);
    }
    if (task.status !== 'todo' && !isDone) {
      chips.push(`<span class="task-status-badge ${task.status}">${escapeHTML(STATUSES.find((s) => s.id === task.status)?.label || '')}</span>`);
    }
    if (task.checklist && task.checklist.length) {
      const doneCount = task.checklist.filter((c) => c.done).length;
      chips.push(`<span class="task-meta-item"><span class="icon" data-icon="list" aria-hidden="true"></span>${doneCount}/${task.checklist.length}</span>`);
    }
    (task.tags || []).forEach((t) => chips.push(`<span class="task-tag">${escapeHTML(t)}</span>`));

    const remind = isDone ? '' : `
      <button class="task-remind-btn" type="button" data-action="tk-remind" data-task-id="${escapeHTML(task.id)}">
        <span class="icon" data-icon="bell" aria-hidden="true"></span>Напомнить позже
      </button>`;

    return `
      <div class="task-card${overdue ? ' overdue' : ''}${isDone ? ' done' : ''}" data-action="tk-open" data-task-id="${escapeHTML(task.id)}">
        <button class="task-check${isDone ? ' checked' : ''}" type="button" data-action="tk-toggle-done" data-task-id="${escapeHTML(task.id)}" aria-label="${isDone ? 'Вернуть в работу' : 'Отметить выполненной'}">
          <span class="icon" data-icon="check" aria-hidden="true"></span>
        </button>
        <div class="task-body">
          <div class="task-title">${escapeHTML(task.title)}</div>
          <div class="task-meta-row">${chips.join('')}${remind}</div>
        </div>
      </div>`;
  }

  /* ─── Оверлеи (маленькие модалки) ─── */
  const overlayClosers = {};

  function openOverlay(id, html, onClose, cls = '') {
    closeOverlay(id);
    const overlay = document.createElement('div');
    overlay.className = 'tk-overlay';
    overlay.dataset.overlay = id;
    overlay.innerHTML = `<div class="tk-modal ${cls}" role="dialog" aria-modal="true">${html}</div>`;
    document.body.appendChild(overlay);
    hydrateIcons(overlay);
    overlayClosers[id] = onClose;
    const openedAt = Date.now();
    overlay.addEventListener('click', (e) => {
      // «призрачный» клик мобильного WebView сразу после тапа не должен закрывать модалку
      if (Date.now() - openedAt < 350) return;
      if (e.target === overlay) onClose();
    });
    window.PolarisBack.push(id, onClose);
    return overlay;
  }

  function closeOverlay(id) {
    document.querySelector(`.tk-overlay[data-overlay="${id}"]`)?.remove();
    delete overlayClosers[id];
    window.PolarisBack.pop(id);
  }

  function updateOverlay(id, html) {
    const modal = document.querySelector(`.tk-overlay[data-overlay="${id}"] .tk-modal`);
    if (!modal) return;
    modal.innerHTML = html;
    hydrateIcons(modal);
  }

  function closeTopOverlay() {
    const all = document.querySelectorAll('.tk-overlay');
    const top = all[all.length - 1];
    if (top && overlayClosers[top.dataset.overlay]) { overlayClosers[top.dataset.overlay](); return true; }
    return false;
  }

  function closeAllOverlays() {
    Object.keys(overlayClosers).forEach((id) => closeOverlay(id));
  }

  function toast(text) {
    document.querySelector('.tk-toast')?.remove();
    const el = document.createElement('div');
    el.className = 'tk-toast';
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2600);
  }

  function askConfirm(text) {
    return new Promise((resolve) => {
      const tg = window.Telegram?.WebApp;
      if (tg?.initData && typeof tg.showConfirm === 'function') {
        try { tg.showConfirm(text, (ok) => resolve(Boolean(ok))); return; } catch (e) { /* fallback */ }
      }
      resolve(window.confirm(text));
    });
  }

  /* ─── Быстрый просмотр (маленькая модалка) ─── */
  const OV_QUICK = 'tasks-quick';
  const OV_EDIT = 'tasks-edit';
  const OV_REMIND = 'tasks-remind';
  const OV_REMIND_LIST = 'tasks-remind-list';
  const OV_REMIND_CUSTOM = 'tasks-remind-custom';

  function quickHTML(task) {
    const isDone = task.status === 'done';
    const when = [task.date ? formatDateFull(task.date) : '', task.time ? formatTime(task.time) : ''].filter(Boolean).join(', ');
    const checklist = (task.checklist || []).map((item, idx) => `
      <label class="tk-check-row">
        <button class="task-check${item.done ? ' checked' : ''}" type="button" data-action="tk-q-check" data-index="${idx}" aria-label="Отметить пункт">
          <span class="icon" data-icon="check" aria-hidden="true"></span>
        </button>
        <span class="${item.done ? 'is-done' : ''}">${escapeHTML(item.text)}</span>
      </label>`).join('');

    return `
      <div class="tk-head"><h3>${escapeHTML(task.title)}</h3>${when ? `<span class="tk-sub">${escapeHTML(when)}</span>` : ''}</div>
      ${task.description ? `<p class="tk-desc">${escapeHTML(task.description)}</p>` : ''}
      <div class="tk-field">
        <label>Статус</label>
        <select id="quick-status">${STATUSES.map((s) => `<option value="${s.id}"${task.status === s.id ? ' selected' : ''}>${escapeHTML(s.label)}</option>`).join('')}</select>
      </div>
      ${checklist ? `<div class="tk-checklist">${checklist}</div>` : ''}
      <div class="tk-actions">
        <button class="tk-btn primary" type="button" data-action="tk-q-edit">Редактировать</button>
        <div class="tk-actions-row">
          <button class="tk-btn ok" type="button" data-action="tk-q-complete">${isDone ? 'Вернуть' : 'Завершить'}</button>
          <button class="tk-btn danger" type="button" data-action="tk-q-delete">Удалить</button>
        </div>
      </div>`;
  }

  function openQuick(taskId) {
    const task = findTask(taskId);
    if (!task) return;
    selectedTaskId = task.id;
    const overlay = openOverlay(OV_QUICK, quickHTML(task), closeQuick);
    const select = overlay.querySelector('#quick-status');
    select?.addEventListener('change', () => changeStatus(task.id, select.value));
  }

  function closeQuick() {
    closeOverlay(OV_QUICK);
    selectedTaskId = null;
  }

  function refreshQuick() {
    const task = selectedTaskId ? findTask(selectedTaskId) : null;
    if (!document.querySelector(`.tk-overlay[data-overlay="${OV_QUICK}"]`)) return;
    if (!task) { closeQuick(); return; }
    updateOverlay(OV_QUICK, quickHTML(task));
    const select = document.querySelector('#quick-status');
    select?.addEventListener('change', () => changeStatus(task.id, select.value));
  }

  /* ─── Изменение данных ─── */
  async function patchTask(taskId, changes) {
    await apiRequest(`/api/tasks/${taskId}`, 'PATCH', changes);
    clearCache();
    await loadFromServer();
  }

  async function changeStatus(taskId, status) {
    try {
      await patchTask(taskId, { status });
      if (status === 'done') closeQuick(); else refreshQuick();
    } catch (e) { error = e.message; render(); }
  }

  async function toggleDone(taskId) {
    const task = findTask(taskId);
    if (!task) return;
    try {
      await patchTask(taskId, { status: task.status === 'done' ? 'todo' : 'done' });
      if (selectedTaskId && String(selectedTaskId) === String(taskId)) closeQuick();
    } catch (e) { error = e.message; render(); }
  }

  async function toggleQuickChecklist(taskId, idx) {
    const task = findTask(taskId);
    if (!task || !task.checklist[idx]) return;
    task.checklist[idx].done = !task.checklist[idx].done;
    refreshQuick(); // мгновенный отклик, сохраняем следом
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { checklist: task.checklist });
      clearCache();
      render();
    } catch (e) { error = e.message; render(); }
  }

  async function deleteTask(taskId) {
    const ok = await askConfirm('Удалить задачу?');
    if (!ok) return;
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'DELETE');
      clearCache();
      closeOverlay(OV_EDIT);
      closeQuick();
      await loadFromServer();
    } catch (e) { error = e.message; render(); }
  }

  /* ─── Редактирование (полная форма) ─── */
  let draft = null; // рабочая копия: чеклист правится локально до «Сохранить»

  function checklistEditHTML() {
    return (draft.checklist || []).map((item, idx) => `
      <div class="tk-check-row">
        <button class="task-check${item.done ? ' checked' : ''}" type="button" data-action="tk-e-check" data-index="${idx}" aria-label="Отметить пункт">
          <span class="icon" data-icon="check" aria-hidden="true"></span>
        </button>
        <span class="tk-check-text ${item.done ? 'is-done' : ''}">${escapeHTML(item.text)}</span>
        <button class="tk-x" type="button" data-action="tk-e-remove" data-index="${idx}" aria-label="Удалить пункт">×</button>
      </div>`).join('');
  }

  function historyHTML(task) {
    return `
      <div class="tk-history">
        <span>Создано: ${escapeHTML(formatDateFull(task.created_at))}</span>
        <span>Изменено: ${escapeHTML(formatDateFull(task.updated_at))}</span>
        ${task.done_at ? `<span>Выполнено: ${escapeHTML(formatDateFull(task.done_at))}</span>` : ''}
      </div>`;
  }

  function openEdit(taskId) {
    const task = findTask(taskId);
    if (!task) return;
    selectedTaskId = task.id;
    draft = { checklist: (task.checklist || []).map((c) => ({ text: c.text, done: Boolean(c.done) })) };

    const opts = (list, current) => list.map((o) => `<option value="${o.id}"${current === o.id ? ' selected' : ''}>${escapeHTML(o.label)}</option>`).join('');
    const html = `
      <div class="tk-form">
        <div class="tk-field">
          <label>Название</label>
          <input type="text" id="ed-title" value="${escapeHTML(task.title)}" maxlength="500" />
        </div>
        <div class="tk-field">
          <label>Описание</label>
          <textarea id="ed-desc" rows="2">${escapeHTML(task.description || '')}</textarea>
        </div>
        <div class="tk-field">
          <label>Чеклист</label>
          <div id="ed-checklist">${checklistEditHTML()}</div>
          <div class="tk-inline-add">
            <input type="text" id="ed-check-input" placeholder="Новый пункт" />
            <button class="tk-btn small" type="button" data-action="tk-e-add">+</button>
          </div>
        </div>

        <div class="tk-grid">
          <div class="tk-field"><label>Дата</label><input type="date" id="ed-date" value="${escapeHTML(task.date || '')}" /></div>
          <div class="tk-field"><label>Время</label><input type="time" id="ed-time" value="${escapeHTML(task.time || '')}" /></div>
          <div class="tk-field"><label>Энергия</label><select id="ed-energy">${ENERGY_OPTIONS.map((e) => `<option value="${e.id}"${task.energy === e.id ? ' selected' : ''}>${escapeHTML(e.label)}</option>`).join('')}</select></div>
          <div class="tk-field"><label>Статус</label><select id="ed-status">${opts(STATUSES, task.status)}</select></div>
          <div class="tk-field"><label>Теги</label><input type="text" id="ed-tags" value="${escapeHTML((task.tags || []).join(', '))}" placeholder="через запятую" /></div>
          <div class="tk-field"><label>Проект</label>
            <select id="ed-project"><option value="">Без проекта</option>${(task.project && !projects.some((p) => p.name === task.project) ? [{ name: task.project }, ...projects] : projects).map((p) => `<option value="${escapeHTML(p.name)}"${task.project === p.name ? ' selected' : ''}>${escapeHTML(p.name)}</option>`).join('')}</select>
          </div>
          <div class="tk-field"><label>Повтор</label><select id="ed-repeat">${opts(REPEAT_OPTIONS, task.repeat)}</select></div>
          <div class="tk-field"><label>Напоминания</label>
            <button class="tk-btn small wide" type="button" data-action="tk-e-reminders"><span class="icon" data-icon="bell" aria-hidden="true"></span><span id="ed-remind-count">Открыть</span></button>
          </div>
        </div>

        <button class="tk-link" type="button" data-action="tk-e-history"><span class="icon" data-icon="history" aria-hidden="true"></span>История</button>
        <div id="ed-history" hidden>${historyHTML(task)}</div>

        <div class="tk-actions">
          <div class="tk-actions-row">
            <button class="tk-btn primary" type="button" data-action="tk-e-save">Сохранить</button>
            <button class="tk-btn" type="button" data-action="tk-e-cancel">Отменить</button>
          </div>
          <div class="tk-actions-row">
            <button class="tk-btn danger" type="button" data-action="tk-e-delete">Удалить</button>
            <button class="tk-btn ok" type="button" data-action="tk-e-complete">${task.status === 'done' ? 'Вернуть' : 'Завершить'}</button>
          </div>
        </div>
      </div>`;

    const overlay = openOverlay(OV_EDIT, html, closeEdit, 'is-edit');
    overlay.querySelector('#ed-check-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); addDraftChecklist(); }
    });
    refreshReminderCount(task.id);
  }

  function closeEdit() {
    closeOverlay(OV_EDIT);
    draft = null;
  }

  function addDraftChecklist() {
    const input = document.getElementById('ed-check-input');
    const text = input?.value.trim();
    if (!text || !draft) return;
    draft.checklist.push({ text, done: false });
    input.value = '';
    redrawDraftChecklist();
  }

  function redrawDraftChecklist() {
    const box = document.getElementById('ed-checklist');
    if (!box) return;
    box.innerHTML = checklistEditHTML();
    hydrateIcons(box);
  }

  async function submitEdit(forceStatus) {
    const task = findTask(selectedTaskId);
    if (!task || !draft) return;
    const title = document.getElementById('ed-title').value.trim();
    if (!title) { toast('Укажите название задачи'); return; }

    const project = document.getElementById('ed-project').value;
    const tagList = document.getElementById('ed-tags').value
      .split(/[,;\n]+/)
      .map((t) => t.trim().replace(/^#/, ''))
      .filter(Boolean);

    const changes = {
      title,
      description: document.getElementById('ed-desc').value.trim(),
      date: document.getElementById('ed-date').value,
      time: document.getElementById('ed-time').value,
      energy: document.getElementById('ed-energy').value,
      status: forceStatus || document.getElementById('ed-status').value,
      repeat: document.getElementById('ed-repeat').value,
      tags: Array.from(new Set(tagList)),
      project,
      project_color: projects.find((p) => p.name === project)?.color || (project === task.project ? task.project_color || '' : ''),
      checklist: draft.checklist,
    };

    try {
      await patchTask(task.id, changes);
      closeEdit();
      closeQuick();
    } catch (e) { toast(`Не удалось сохранить: ${e.message}`); }
  }

  /* ─── Напоминания ─── */
  function pad2(n) { return String(n).padStart(2, '0'); }

  // ISO со смещением часового пояса: календарь показывает время именно так, как мы его задали
  function toLocalISO(d) {
    const off = -d.getTimezoneOffset();
    const sign = off >= 0 ? '+' : '-';
    const abs = Math.abs(off);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:00${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`;
  }

  function presetDate(kind) {
    const now = new Date();
    const d = new Date(now);
    if (kind === '1h') return new Date(now.getTime() + 60 * 60 * 1000);
    if (kind === 'evening') {
      d.setHours(18, 0, 0, 0);
      if (d <= now) d.setDate(d.getDate() + 1);
      return d;
    }
    d.setDate(d.getDate() + 1); // tomorrow
    d.setHours(9, 0, 0, 0);
    return d;
  }

  function fmtReminder(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const key = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    const day = key === todayStr() ? 'Сегодня' : key === inDaysStr(1) ? 'Завтра' : formatDate(key);
    return `${day}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  }

  async function loadReminders(taskId) {
    const result = await apiRequest(`/api/tasks/${taskId}/reminders`);
    return (result.data || result).reminders || [];
  }

  async function refreshReminderCount(taskId) {
    try {
      const items = await loadReminders(taskId);
      const label = document.getElementById('ed-remind-count');
      if (label) label.textContent = items.length ? `Открыть · ${items.length}` : 'Добавить';
      const link = document.querySelector('[data-action="tk-r-all"] span');
      if (link) link.textContent = items.length ? `Все напоминания · ${items.length}` : 'Все напоминания';
    } catch (e) { /* не критично */ }
  }

  // Маленькое окно выбора: через час / вечером / завтра / другое время / отмена
  function openRemindChoice(taskId, after) {
    remindAfter = after || null;
    selectedRemindTask = taskId;
    openOverlay(OV_REMIND, `
      <div class="tk-head"><h3>Напомнить позже</h3></div>
      <div class="tk-choice">
        <button class="tk-btn" type="button" data-action="tk-r-pick" data-kind="1h">Через час</button>
        <button class="tk-btn" type="button" data-action="tk-r-pick" data-kind="evening">Вечером</button>
        <button class="tk-btn" type="button" data-action="tk-r-pick" data-kind="tomorrow">Завтра</button>
        <button class="tk-btn" type="button" data-action="tk-r-custom">Выбрать другое время</button>
        ${remindAfter ? '' : '<button class="tk-link center" type="button" data-action="tk-r-all"><span>Все напоминания</span></button>'}
        <button class="tk-btn ghost" type="button" data-action="tk-r-cancel">Отмена</button>
      </div>`, closeRemindChoice, 'is-small');
    refreshReminderCount(taskId);
  }

  let remindAfter = null;
  let selectedRemindTask = null;

  function closeRemindChoice() { closeOverlay(OV_REMIND); }

  function openRemindCustom() {
    const d = new Date(Date.now() + 60 * 60 * 1000);
    d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5, 0, 0);
    openOverlay(OV_REMIND_CUSTOM, `
      <div class="tk-head"><h3>Другое время</h3></div>
      <div class="tk-grid">
        <div class="tk-field"><label>Дата</label><input type="date" id="rc-date" value="${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}" /></div>
        <div class="tk-field"><label>Время</label><input type="time" id="rc-time" value="${pad2(d.getHours())}:${pad2(d.getMinutes())}" /></div>
      </div>
      <div class="tk-actions"><div class="tk-actions-row">
        <button class="tk-btn primary" type="button" data-action="tk-rc-save">Добавить</button>
        <button class="tk-btn" type="button" data-action="tk-rc-cancel">Отмена</button>
      </div></div>`, () => closeOverlay(OV_REMIND_CUSTOM), 'is-small');
  }

  async function addReminder(taskId, date) {
    if (date.getTime() <= Date.now() - 60000) { toast('Это время уже прошло'); return false; }
    try {
      await apiRequest(`/api/tasks/${taskId}/reminders`, 'POST', { remind_at: toLocalISO(date) });
      clearCache();
      toast(`🔔 Напомню: ${fmtReminder(date.toISOString())}`);
      return true;
    } catch (e) { toast(`Не удалось: ${e.message}`); return false; }
  }

  async function pickReminder(date) {
    const taskId = selectedRemindTask;
    if (!taskId || !(await addReminder(taskId, date))) return;
    closeOverlay(OV_REMIND_CUSTOM);
    closeRemindChoice();
    if (remindAfter) remindAfter();
    refreshReminderCount(taskId);
  }

  // Список всех напоминаний задачи
  async function openRemindList(taskId) {
    selectedRemindTask = taskId;
    openOverlay(OV_REMIND_LIST, '<div class="tk-head"><h3>Напоминания</h3></div><p class="tk-desc">Загружаю…</p>', closeRemindList, 'is-small');
    await redrawRemindList();
  }

  function closeRemindList() { closeOverlay(OV_REMIND_LIST); }

  async function redrawRemindList() {
    const taskId = selectedRemindTask;
    let items = [];
    try { items = await loadReminders(taskId); } catch (e) { toast(e.message); }
    const now = Date.now();
    const rows = items.map((r) => `
      <div class="tk-remind-row${new Date(r.remind_at).getTime() < now ? ' is-past' : ''}">
        <span class="icon" data-icon="bell" aria-hidden="true"></span>
        <span class="tk-remind-time">${escapeHTML(fmtReminder(r.remind_at))}</span>
        <button class="tk-x" type="button" data-action="tk-rl-delete" data-id="${escapeHTML(r.id)}" aria-label="Удалить напоминание">×</button>
      </div>`).join('');
    updateOverlay(OV_REMIND_LIST, `
      <div class="tk-head"><h3>Напоминания</h3></div>
      ${rows ? `<div class="tk-remind-list">${rows}</div>` : '<p class="tk-desc">Напоминаний пока нет.</p>'}
      <div class="tk-actions"><div class="tk-actions-row">
        <button class="tk-btn primary" type="button" data-action="tk-rl-add">+ Добавить</button>
        <button class="tk-btn" type="button" data-action="tk-rl-close">Закрыть</button>
      </div></div>`);
    refreshReminderCount(taskId);
  }

  /* ─── Event Delegation ─── */
  function handleClick(e) {
    const el = e.target.closest('[data-action^="tk-"], [data-action="close-create-modal"], [data-action="submit-create-task"]');
    if (!el) return;
    const action = el.dataset.action;
    const taskId = el.dataset.taskId;

    switch (action) {
      // страница
      case 'tk-create': openCreateModal(); break;
      case 'tk-open-done': openDone(); break;
      case 'tk-close-done': closeDone(); break;
      case 'tk-open': openQuick(taskId); break;
      case 'tk-toggle-done': e.stopPropagation(); toggleDone(taskId); break;
      case 'tk-remind': e.stopPropagation(); openRemindChoice(taskId); break;
      // создание
      case 'close-create-modal': closeCreateModal(); break;
      case 'submit-create-task': submitCreate(); break;
      // быстрый просмотр
      case 'tk-q-edit': openEdit(selectedTaskId); break;
      case 'tk-q-complete': toggleDone(selectedTaskId); break;
      case 'tk-q-delete': deleteTask(selectedTaskId); break;
      case 'tk-q-check': toggleQuickChecklist(selectedTaskId, Number(el.dataset.index)); break;
      // редактирование
      case 'tk-e-save': submitEdit(); break;
      case 'tk-e-cancel': closeEdit(); break;
      case 'tk-e-complete': submitEdit(findTask(selectedTaskId)?.status === 'done' ? 'todo' : 'done'); break;
      case 'tk-e-delete': deleteTask(selectedTaskId); break;
      case 'tk-e-add': addDraftChecklist(); break;
      case 'tk-e-check': {
        const item = draft?.checklist[Number(el.dataset.index)];
        if (item) { item.done = !item.done; redrawDraftChecklist(); }
        break;
      }
      case 'tk-e-remove':
        draft?.checklist.splice(Number(el.dataset.index), 1);
        redrawDraftChecklist();
        break;
      case 'tk-e-history': {
        const box = document.getElementById('ed-history');
        if (box) box.hidden = !box.hidden;
        break;
      }
      case 'tk-e-reminders': openRemindList(selectedTaskId); break;
      // напоминания
      case 'tk-r-pick': pickReminder(presetDate(el.dataset.kind)); break;
      case 'tk-r-custom': openRemindCustom(); break;
      case 'tk-r-cancel': closeRemindChoice(); break;
      case 'tk-r-all': closeRemindChoice(); openRemindList(selectedRemindTask); break;
      case 'tk-rc-cancel': closeOverlay(OV_REMIND_CUSTOM); break;
      case 'tk-rc-save': {
        const date = document.getElementById('rc-date')?.value;
        const time = document.getElementById('rc-time')?.value;
        if (!date || !time) { toast('Укажите дату и время'); break; }
        pickReminder(new Date(`${date}T${time}`));
        break;
      }
      case 'tk-rl-add': openRemindChoice(selectedRemindTask, redrawRemindList); break;
      case 'tk-rl-close': closeRemindList(); break;
      case 'tk-rl-delete':
        apiRequest(`/api/tasks/${selectedRemindTask}/reminders/${el.dataset.id}`, 'DELETE')
          .then(() => { clearCache(); return redrawRemindList(); })
          .catch((err) => toast(err.message));
        break;
      default: break;
    }
  }

  /* ─── Список выполненных ─── */
  function openDone() {
    view = 'done';
    window.PolarisBack.push('tasks-done', closeDone);
    render();
  }

  function closeDone() {
    view = 'list';
    window.PolarisBack.pop('tasks-done');
    render();
  }

  /* ─── Create Modal ─── */
  function renderCreateModal() {
    const existing = document.querySelector('.create-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'create-modal-overlay';
    overlay.innerHTML = `
      <div class="create-modal" role="dialog" aria-modal="true" aria-label="Новая задача">
        <div class="create-modal-header">
          <h2>Новая задача</h2>
        </div>
        <textarea class="create-modal-input" id="create-task-input" placeholder="Что нужно сделать?" rows="2" autofocus></textarea>
        <p class="create-modal-hint">Enter — создать. Теги подставятся автоматически: lumica, linux, telegram, дом, учеба...</p>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px">
          <div class="detail-field">
            <label>Дата</label>
            <input type="date" id="create-task-date" />
          </div>
          <div class="detail-field">
            <label>Время</label>
            <input type="time" id="create-task-time" />
          </div>
        </div>
        <div class="detail-field" style="margin-top:10px">
          <label>Энергия</label>
          <select id="create-task-energy">
            ${ENERGY_OPTIONS.map((e) => `<option value="${e.id}">${escapeHTML(e.label)} — ${escapeHTML(e.desc)}</option>`).join('')}
          </select>
        </div>
        <div class="create-modal-actions">
          <button class="small-button ghost" type="button" data-action="close-create-modal">Отмена</button>
          <button class="small-button primary" type="button" data-action="submit-create-task">Создать</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    hydrateIcons(overlay);

    const input = overlay.querySelector('#create-task-input');
    if (input) {
      requestAnimationFrame(() => input.focus());
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitCreate(); }
        if (e.key === 'Escape') { closeCreateModal(); }
      });
    }

    const openedAt = Date.now();
    overlay.addEventListener('click', (e) => {
      // Игнорируем "призрачный" клик, который мобильный WebView иногда
      // досылает в ту же точку экрана ~200-350мс после тапа на кнопку,
      // из-за которого модалка закрывалась сама через секунду после открытия.
      if (Date.now() - openedAt < 350) return;
      if (e.target === overlay) closeCreateModal();
    });
  }

  function openCreateModal() {
    createModalOpen = true;
    render();
    window.PolarisBack.push('tasks-create-modal', closeCreateModal);
  }
  function closeCreateModal() {
    createModalOpen = false;
    window.PolarisBack.pop('tasks-create-modal');
    const overlay = document.querySelector('.create-modal-overlay');
    if (overlay) overlay.remove();
    render();
  }

  function extractTagsFromTitle(title) {
    const lower = title.toLowerCase();
    const matched = new Set();
    TAG_RULES.forEach((rule) => {
      if (lower.includes(rule.keyword)) {
        matched.add(rule.tag);
      }
    });
    return Array.from(matched);
  }

  async function submitCreate() {
    const input = document.getElementById('create-task-input');
    if (!input) return;
    const title = input.value.trim();
    if (!title) return;

    const dateInput = document.getElementById('create-task-date');
    const timeInput = document.getElementById('create-task-time');
    const energyInput = document.getElementById('create-task-energy');

    const data = {
      title: title,
      date: dateInput?.value || '',
      time: timeInput?.value || '',
      energy: energyInput?.value || 'medium',
      tags: extractTagsFromTitle(title),
    };

    try {
      await apiRequest('/api/tasks', 'POST', data);
      clearCache();
      await loadFromServer();
      closeCreateModal();
    } catch (e) {
      error = e.message;
      render();
    }
  }

  /* ─── Keyboard shortcuts ─── */
  function handleKeydown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') {
      if (!createModalOpen && document.getElementById('tasks-container')) { e.preventDefault(); openCreateModal(); }
      return;
    }
    if (e.key === 'Escape') {
      if (createModalOpen) { closeCreateModal(); return; }
      if (closeTopOverlay()) return;
      if (view === 'done') closeDone();
    }
  }

  /* ─── Init ─── */
  function init(containerId) {
    // при каждом заходе на страницу показываем основной список
    if (view !== 'list') window.PolarisBack.pop('tasks-done');
    view = 'list';
    if (initialized) {
      render();
      loadFromServer();
      return;
    }

    // Get auth token
    const params = new URLSearchParams(window.location.search);
    const tokenFromUrl = params.get('token') || '';
    const tokenStorageKey = 'polaris_update_token';
    if (tokenFromUrl) localStorage.setItem(tokenStorageKey, tokenFromUrl);
    apiToken = tokenFromUrl || localStorage.getItem(tokenStorageKey) || '';

    // Create container
    let container = document.getElementById(containerId);
    if (!container) {
      container = document.createElement('div');
      container.id = containerId;
      const mc = document.getElementById('main-content');
      if (mc) mc.appendChild(container);
    }

    // Load from cache first
    const cached = loadCache();
    if (cached) {
      tasks = cached.tasks || [];
      projects = cached.projects || [];
      tags = cached.tags || [];
    }

    render();

    // Load from server
    loadFromServer();

    // Event listeners
    document.addEventListener('click', handleClick);
    document.addEventListener('keydown', handleKeydown);

    initialized = true;
  }

  /* ─── Public API ─── */
  async function openDetailSafe(taskId) {
    // открывают, например, из календаря — задачи могут быть ещё не загружены
    if (loading) {
      await new Promise((resolve) => {
        const wait = () => (loading ? setTimeout(wait, 120) : resolve());
        wait();
      });
    } else if (!findTask(taskId)) {
      await loadFromServer();
    }
    openQuick(taskId);
  }

  // Уход со страницы: закрываем модалки и снимаем кнопку «Назад»
  function leave() {
    closeAllOverlays();
    draft = null;
    selectedTaskId = null;
    if (createModalOpen) closeCreateModal();
    if (view !== 'list') window.PolarisBack.pop('tasks-done');
    view = 'list';
  }

  return {
    init,
    render,
    loadFromServer,
    openCreateModal,
    openDetail: openDetailSafe,
    leave,
  };
})();