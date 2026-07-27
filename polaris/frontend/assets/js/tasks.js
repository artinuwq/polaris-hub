/* ─── Tasks Module ─── */
window.TasksModule = (() => {
  'use strict';

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
    { id: 'todo', label: 'Todo' },
    { id: 'in-progress', label: 'In Progress' },
    { id: 'waiting', label: 'Waiting' },
    { id: 'done', label: 'Done' },
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

  const CACHE_KEY = 'polaris.tasks.cache';

  /* ─── State ─── */
  let tasks = [];
  let projects = [];
  let tags = [];
  let selectedTaskId = null;
  let createModalOpen = false;
  let detailOpen = false;
  let showDone = false;
  let showHistory = false;
  let loading = false;
  let error = '';
  let apiToken = '';
  let initialized = false;

  /* ─── Helpers ─── */
  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&')
      .replaceAll('<', '<')
      .replaceAll('>', '>')
      .replaceAll('"', '"')
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
  }

  /* ─── Group Tasks ─── */
  function groupTasks() {
    const groups = {
      overdue: [],
      today: [],
      next7: [],
      rest: [],
      nodate: [],
    };

    const active = tasks.filter((t) => t.status !== 'done');
    const done = tasks.filter((t) => t.status === 'done');

    active.forEach((task) => {
      if (task.date && isOverdue(task.date)) {
        groups.overdue.push(task);
      } else if (task.date && isToday(task.date)) {
        groups.today.push(task);
      } else if (task.date && isThisWeek(task.date)) {
        groups.next7.push(task);
      } else if (task.date) {
        groups.rest.push(task);
      } else {
        groups.nodate.push(task);
      }
    });

    // Sort: by priority then by date
    const priorityOrder = { fire: 0, important: 1, normal: 2, someday: 3 };
    Object.keys(groups).forEach((key) => {
      groups[key].sort((a, b) => {
        const pa = priorityOrder[a.priority] ?? 99;
        const pb = priorityOrder[b.priority] ?? 99;
        if (pa !== pb) return pa - pb;
        if (a.date && b.date) return a.date.localeCompare(b.date);
        if (a.date) return -1;
        if (b.date) return 1;
        return 0;
      });
    });

    // Done sorted by done_at desc
    done.sort((a, b) => {
      const da = a.done_at || a.updated_at || '';
      const db = b.done_at || b.updated_at || '';
      return db.localeCompare(da);
    });

    return { ...groups, done };
  }

  /* ─── Render ─── */
  function render() {
    const container = document.getElementById('tasks-container');
    if (!container) return;

    const groups = groupTasks();
    let html = '';

    // Header
    html += `
      <div class="tasks-header">
        <h1>Tasks</h1>
        <button class="small-button primary" type="button" data-action="open-create-modal" ${loading ? 'disabled' : ''}>
          <span class="icon" data-icon="plus" aria-hidden="true" style="width:16px;height:16px"></span>
          Новая задача
        </button>
      </div>
    `;

    // Loading / Error
    if (loading && tasks.length === 0) {
      html += `<div class="tasks-empty"><p>Загружаю задачи…</p></div>`;
      container.innerHTML = html;
      hydrateIcons(container);
      return;
    }

    if (error) {
      html += `<div class="auth-banner err">${escapeHTML(error)}</div>`;
    }

    // Overdue
    if (groups.overdue.length > 0) {
      html += renderOverdueBlock(groups.overdue);
    }

    // Today
    if (groups.today.length > 0) {
      html += renderGroup('Сегодня', groups.today);
    }

    // Next 7 days
    if (groups.next7.length > 0) {
      html += renderGroup('Следующие 7 дней', groups.next7);
    }

    // Rest
    if (groups.rest.length > 0) {
      html += renderGroup('Остальное', groups.rest);
    }

    // No date
    if (groups.nodate.length > 0) {
      html += renderGroup('Без даты', groups.nodate);
    }

    // Empty state
    if (tasks.length === 0) {
      html += `
        <div class="tasks-empty">
          <span class="icon" data-icon="list" aria-hidden="true"></span>
          <p>Нет задач. Создайте первую.</p>
        </div>
      `;
    }

    // Done (collapsible)
    if (groups.done.length > 0) {
      html += `
        <div class="task-group">
          <div class="task-group-header" style="cursor:pointer" data-action="toggle-done">
            <h2>Выполнено (${groups.done.length})</h2>
            <span class="icon" data-icon="${showDone ? 'chevronDown' : 'chevronRight'}" aria-hidden="true" style="width:14px;height:14px;color:var(--text-muted)"></span>
          </div>
          <div class="task-group-divider"></div>
          ${showDone ? groups.done.map((t) => renderTaskCard(t)).join('') : ''}
        </div>
      `;
    }

    // Quick create
    html += `
      <div class="quick-create" data-action="open-create-modal">
        <span class="icon" data-icon="plus" aria-hidden="true"></span>
        <span>Быстрое создание</span>
      </div>
    `;

    container.innerHTML = html;
    hydrateIcons(container);

    if (createModalOpen) renderCreateModal();
    if (detailOpen && selectedTaskId) renderDetailPanel();
  }

  function renderOverdueBlock(items) {
    return `
      <div class="overdue-block">
        <div class="task-group">
          <div class="task-group-header">
            <h2>🔴 Просрочено</h2>
            <span class="task-group-count">${items.length}</span>
          </div>
          <div class="task-group-divider"></div>
          ${items.map((t) => renderTaskCard(t)).join('')}
        </div>
      </div>
    `;
  }

  function renderGroup(label, items) {
    return `
      <div class="task-group">
        <div class="task-group-header">
          <h2>${escapeHTML(label)}</h2>
          <span class="task-group-count">${items.length}</span>
        </div>
        <div class="task-group-divider"></div>
        ${items.map((t) => renderTaskCard(t)).join('')}
      </div>
    `;
  }

  function renderTaskCard(task) {
    const isDone = task.status === 'done';
    const overdue = !isDone && task.date && isOverdue(task.date);
    const projectColor = task.project_color || '';
    const energyIcon = task.energy === 'quick' ? '🟢' : task.energy === 'medium' ? '🟡' : '🔴';

    let metaHtml = '';
    let tagsHtml = '';

    // Energy badge
    const energyLabel = ENERGY_OPTIONS.find((e) => e.id === task.energy)?.desc || '';
    metaHtml += `<span class="task-meta-item energy-${task.energy}">${energyIcon} ${escapeHTML(energyLabel)}</span>`;

    // Date
    if (task.date) {
      const oc = overdue ? ' overdue' : '';
      metaHtml += `<span class="task-meta-item${oc}"><span class="icon" data-icon="calendar" aria-hidden="true"></span>${escapeHTML(formatDate(task.date))}</span>`;
    }

    // Time
    if (task.time) {
      metaHtml += `<span class="task-meta-item"><span class="icon" data-icon="clock" aria-hidden="true"></span>${escapeHTML(formatTime(task.time))}</span>`;
    }

    // Repeat
    if (task.repeat && task.repeat !== 'never') {
      metaHtml += `<span class="task-meta-item"><span class="icon" data-icon="repeat" aria-hidden="true"></span>${escapeHTML(REPEAT_OPTIONS.find((r) => r.id === task.repeat)?.label || '')}</span>`;
    }

    // Project with color
    if (task.project) {
      const dot = projectColor
        ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${escapeHTML(projectColor)};margin-right:3px;flex-shrink:0"></span>`
        : '';
      metaHtml += `<span class="task-meta-item">${dot}${escapeHTML(task.project)}</span>`;
    }

    // Status badge (only if not todo)
    if (task.status !== 'todo') {
      metaHtml += `<span class="task-status-badge ${task.status}">${escapeHTML(STATUSES.find((s) => s.id === task.status)?.label || '')}</span>`;
    }

    // Tags
    if (task.tags && task.tags.length > 0) {
      tagsHtml = `<div class="task-tags">${task.tags.map((t) => `<span class="task-tag">${escapeHTML(t)}</span>`).join('')}</div>`;
    }

    // Checklist progress
    if (task.checklist && task.checklist.length > 0) {
      const done = task.checklist.filter((c) => c.done).length;
      const total = task.checklist.length;
      metaHtml += `<span class="task-meta-item"><span class="icon" data-icon="list" aria-hidden="true"></span>${done}/${total}</span>`;
    }

    return `
      <div class="task-card${overdue ? ' overdue' : ''}${isDone ? ' done' : ''}" data-task-id="${task.id}">
        <button class="task-check${isDone ? ' checked' : ''}" type="button" data-action="toggle-done" data-task-id="${task.id}" aria-label="${isDone ? 'Отметить невыполненным' : 'Отметить выполненным'}">
          <span class="icon" data-icon="check" aria-hidden="true"></span>
        </button>
        <div class="task-body">
          <div class="task-title">${escapeHTML(task.title)}</div>
          <div class="task-meta">${metaHtml}</div>
          ${tagsHtml}
          <div class="task-card-actions">
            <button class="small-button ghost" type="button" data-action="remind-later" data-task-id="${task.id}" style="min-height:26px;padding:0 8px;font-size:0.72rem;margin-top:4px">
              <span class="icon" data-icon="bell" aria-hidden="true" style="width:12px;height:12px"></span>
              Напомнить позже
            </button>
          </div>
        </div>
      </div>
    `;
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
          <button class="icon-button" type="button" data-action="close-create-modal" aria-label="Закрыть">
            <span class="icon" data-icon="close" aria-hidden="true"></span>
          </button>
        </div>
        <textarea class="create-modal-input" id="create-task-input" placeholder="Что нужно сделать?" rows="2" autofocus></textarea>
        <p class="create-modal-hint">Enter — создать. Умные даты: «завтра в 18», «через 3 дня», «31 июля»</p>
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

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeCreateModal();
    });
  }

  function openCreateModal() { createModalOpen = true; render(); }
  function closeCreateModal() {
    createModalOpen = false;
    const overlay = document.querySelector('.create-modal-overlay');
    if (overlay) overlay.remove();
    render();
  }

  async function submitCreate() {
    const input = document.getElementById('create-task-input');
    if (!input) return;
    const title = input.value.trim();
    if (!title) return;

    const parsed = parseSmartDate(title);
    const data = {
      title: title,
      date: parsed.date,
      time: parsed.time,
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

  /* ─── Detail Panel ─── */
  function renderDetailPanel() {
    const existing = document.querySelector('.detail-overlay');
    if (existing) existing.remove();

    const task = tasks.find((t) => t.id === selectedTaskId);
    if (!task) { detailOpen = false; return; }

    const energyIcon = task.energy === 'quick' ? '🟢' : task.energy === 'medium' ? '🟡' : '🔴';
    const energyLabel = ENERGY_OPTIONS.find((e) => e.id === task.energy)?.label || '🟡 Средняя';

    const overlay = document.createElement('div');
    overlay.className = 'detail-overlay';
    overlay.innerHTML = `
      <div class="detail-panel" role="dialog" aria-modal="true" aria-label="Детали задачи">
        <div class="detail-header">
          <h2>${escapeHTML(task.title)}</h2>
          <button class="icon-button" type="button" data-action="close-detail" aria-label="Закрыть">
            <span class="icon" data-icon="close" aria-hidden="true"></span>
          </button>
        </div>

        <!-- Description -->
        <div class="detail-section">
          <div class="detail-section-title">Описание</div>
          <textarea data-field="description" placeholder="Добавить описание..." rows="3">${escapeHTML(task.description)}</textarea>
        </div>

        <div class="detail-divider"></div>

        <!-- Date & Time -->
        <div class="detail-section">
          <div class="detail-section-title">Дата и время</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div class="detail-field">
              <label>Дата</label>
              <input type="date" data-field="date" value="${escapeHTML(task.date)}" />
            </div>
            <div class="detail-field">
              <label>Время</label>
              <input type="time" data-field="time" value="${escapeHTML(task.time)}" />
            </div>
          </div>
        </div>

        <div class="detail-divider"></div>

        <!-- Repeat -->
        <div class="detail-section">
          <div class="detail-section-title">Повторение</div>
          <select data-field="repeat">
            ${REPEAT_OPTIONS.map((r) => `<option value="${r.id}"${task.repeat === r.id ? ' selected' : ''}>${escapeHTML(r.label)}</option>`).join('')}
          </select>
        </div>

        <div class="detail-divider"></div>

        <!-- Energy -->
        <div class="detail-section">
          <div class="detail-section-title">Энергия задачи</div>
          <select data-field="energy">
            ${ENERGY_OPTIONS.map((e) => `<option value="${e.id}"${task.energy === e.id ? ' selected' : ''}>${escapeHTML(e.label)} — ${escapeHTML(e.desc)}</option>`).join('')}
          </select>
        </div>

        <div class="detail-divider"></div>

        <!-- Status -->
        <div class="detail-section">
          <div class="detail-section-title">Статус</div>
          <select data-field="status">
            ${STATUSES.map((s) => `<option value="${s.id}"${task.status === s.id ? ' selected' : ''}>${escapeHTML(s.label)}</option>`).join('')}
          </select>
        </div>

        <div class="detail-divider"></div>

        <!-- Tags -->
        <div class="detail-section">
          <div class="detail-section-title">Теги</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px" id="detail-tags">
            ${(task.tags || []).map((t) => `<span class="task-tag" style="display:inline-flex;align-items:center;gap:3px;padding:2px 8px">${escapeHTML(t)} <button type="button" data-action="remove-tag" data-tag="${escapeHTML(t)}" style="background:none;border:none;color:var(--text-muted);cursor:pointer;padding:0;font-size:0.7rem">×</button></span>`).join('')}
          </div>
          <div style="display:flex;gap:4px">
            <input type="text" id="detail-tag-input" placeholder="Добавить тег" style="flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface);color:var(--text);font-size:0.82rem;outline:none" />
            <button class="small-button" type="button" data-action="add-tag" style="min-height:32px;padding:0 10px;font-size:0.78rem">+</button>
          </div>
        </div>

        <div class="detail-divider"></div>

        <!-- Project -->
        <div class="detail-section">
          <div class="detail-section-title">Проект</div>
          <select data-field="project">
            <option value="">Без проекта</option>
            ${projects.map((p) => {
              const color = p.color || '#5ab8ff';
              const dot = `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${escapeHTML(color)};margin-right:6px;vertical-align:middle"></span>`;
              const sel = task.project === p.name ? ' selected' : '';
              return `<option value="${escapeHTML(p.name)}"${sel}>${dot}${escapeHTML(p.name)}</option>`;
            }).join('')}
          </select>
        </div>

        <div class="detail-divider"></div>

        <!-- Checklist -->
        <div class="detail-section">
          <div class="detail-section-title">Чеклист</div>
          <div id="detail-checklist">
            ${(task.checklist || []).map((item, idx) => `
              <div class="checklist-item">
                <button class="task-check${item.done ? ' checked' : ''}" type="button" data-action="toggle-checklist" data-checklist-index="${idx}">
                  <span class="icon" data-icon="check" aria-hidden="true"></span>
                </button>
                <span style="flex:1;${item.done ? 'text-decoration:line-through;color:var(--text-muted)' : ''}">${escapeHTML(item.text)}</span>
                <button type="button" data-action="remove-checklist" data-checklist-index="${idx}" style="background:none;border:none;color:var(--text-muted);cursor:pointer;padding:2px;font-size:0.8rem">×</button>
              </div>
            `).join('')}
          </div>
          <div style="display:flex;gap:4px;margin-top:6px">
            <input type="text" id="detail-checklist-input" placeholder="Добавить пункт" style="flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface);color:var(--text);font-size:0.82rem;outline:none" />
            <button class="small-button" type="button" data-action="add-checklist" style="min-height:32px;padding:0 10px;font-size:0.78rem">+</button>
          </div>
        </div>

        <div class="detail-divider"></div>

        <!-- History (collapsible) -->
        <div class="detail-section">
          <div class="detail-section-title" style="cursor:pointer;display:flex;align-items:center;gap:6px" data-action="toggle-history">
            История
            <span class="icon" data-icon="${showHistory ? 'chevronDown' : 'chevronRight'}" aria-hidden="true" style="width:12px;height:12px"></span>
          </div>
          ${showHistory ? `
          <div style="display:flex;flex-direction:column;gap:4px;font-size:0.78rem;color:var(--text-muted);margin-top:6px">
            <span>Создано: ${formatDateFull(task.created_at)}</span>
            <span>Изменено: ${formatDateFull(task.updated_at)}</span>
            ${task.done_at ? `<span>Выполнено: ${formatDateFull(task.done_at)}</span>` : ''}
          </div>` : ''}
        </div>

        <div class="detail-divider"></div>

        <!-- Delete -->
        <button class="small-button" type="button" data-action="delete-task" style="color:var(--error);border-color:rgba(255,93,115,0.3)">Удалить задачу</button>
      </div>
    `;

    document.body.appendChild(overlay);
    hydrateIcons(overlay);

    // Auto-save
    const fields = overlay.querySelectorAll('[data-field]');
    let saveTimer = null;
    fields.forEach((el) => {
      el.addEventListener('change', () => { scheduleSave(task.id); });
      el.addEventListener('blur', () => { scheduleSave(task.id); });
    });

    // Tag input
    const tagInput = overlay.querySelector('#detail-tag-input');
    if (tagInput) {
      tagInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); addTagToTask(task.id, tagInput.value); tagInput.value = ''; }
      });
    }

    // Checklist input
    const clInput = overlay.querySelector('#detail-checklist-input');
    if (clInput) {
      clInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); addChecklistItem(task.id, clInput.value); clInput.value = ''; renderDetailPanel(); }
      });
    }

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeDetail();
    });
  }

  let saveTimer = null;

  function scheduleSave(taskId) {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveDetailChanges(taskId), 500);
  }

  async function saveDetailChanges(taskId) {
    const overlay = document.querySelector('.detail-overlay');
    if (!overlay) return;

    const changes = {};
    overlay.querySelectorAll('[data-field]').forEach((el) => {
      const field = el.dataset.field;
      changes[field] = el.value;
    });

    try {
      await apiRequest(`/api/tasks/${taskId}`, 'PATCH', changes);
      clearCache();
      await loadFromServer();
    } catch (e) {
      error = e.message;
    }
  }

  async function addTagToTask(taskId, tagName) {
    const trimmed = tagName.trim();
    if (!trimmed) return;
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;
    if (!task.tags.includes(trimmed)) {
      task.tags.push(trimmed);
      try {
        await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { tags: task.tags });
        clearCache();
        await loadFromServer();
        renderDetailPanel();
      } catch (e) { error = e.message; render(); }
    }
  }

  function openDetail(taskId) { selectedTaskId = taskId; detailOpen = true; render(); }

  function closeDetail() {
    detailOpen = false; selectedTaskId = null;
    const overlay = document.querySelector('.detail-overlay');
    if (overlay) overlay.remove();
    render();
  }

  /* ─── Mutations ─── */
  async function toggleDone(taskId) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;
    const newStatus = task.status === 'done' ? 'todo' : 'done';
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { status: newStatus });
      clearCache();
      await loadFromServer();
    } catch (e) { error = e.message; render(); }
  }

  async function addChecklistItem(taskId, text) {
    const trimmed = text.trim();
    if (!trimmed) return;
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;
    task.checklist.push({ text: trimmed, done: false });
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { checklist: task.checklist });
      clearCache();
    } catch (e) { error = e.message; }
  }

  async function toggleChecklist(taskId, idx) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;
    const item = task.checklist[idx];
    if (!item) return;
    item.done = !item.done;
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { checklist: task.checklist });
      clearCache();
    } catch (e) { error = e.message; }
  }

  async function removeChecklistItem(taskId, idx) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;
    task.checklist.splice(idx, 1);
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { checklist: task.checklist });
      clearCache();
    } catch (e) { error = e.message; }
  }

  async function deleteTask(taskId) {
    try {
      await apiRequest(`/api/tasks/${taskId}`, 'DELETE');
      clearCache();
      closeDetail();
      await loadFromServer();
    } catch (e) { error = e.message; render(); }
  }

  async function remindLater(taskId, option) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;

    const now = new Date();
    let remindDate = null;

    switch (option) {
      case '1h':
        remindDate = new Date(now.getTime() + 60 * 60 * 1000);
        break;
      case 'evening':
        remindDate = new Date(now);
        remindDate.setHours(18, 0, 0, 0);
        if (remindDate < now) remindDate.setDate(remindDate.getDate() + 1);
        break;
      case 'tomorrow':
        remindDate = new Date(now);
        remindDate.setDate(remindDate.getDate() + 1);
        remindDate.setHours(9, 0, 0, 0);
        break;
      case '1w':
        remindDate = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
        break;
    }

    if (remindDate) {
      const remindAt = remindDate.toISOString();
      try {
        await apiRequest(`/api/tasks/${taskId}`, 'PATCH', { remind_at: remindAt });
        clearCache();
        await loadFromServer();
      } catch (e) { error = e.message; render(); }
    }
  }

  /* ─── Event Delegation ─── */
  function handleClick(e) {
    const target = e.target;

    // Toggle done
    const toggleBtn = target.closest('[data-action="toggle-done"]');
    if (toggleBtn) {
      e.stopPropagation();
      toggleDone(parseInt(toggleBtn.dataset.taskId, 10) || toggleBtn.dataset.taskId);
      return;
    }

    // Open task detail
    const card = target.closest('.task-card');
    if (card && !target.closest('[data-action="toggle-done"]') && !target.closest('[data-action="remind-later"]')) {
      openDetail(card.dataset.taskId);
      return;
    }

    // Create modal
    if (target.closest('[data-action="open-create-modal"]')) { openCreateModal(); return; }
    if (target.closest('[data-action="close-create-modal"]')) { closeCreateModal(); return; }
    if (target.closest('[data-action="submit-create-task"]')) { submitCreate(); return; }

    // Detail actions
    if (target.closest('[data-action="close-detail"]')) { closeDetail(); return; }

    const deleteBtn = target.closest('[data-action="delete-task"]');
    if (deleteBtn && selectedTaskId && confirm('Удалить задачу?')) { deleteTask(selectedTaskId); return; }

    // Checklist
    if (target.closest('[data-action="toggle-checklist"]')) {
      const idx = parseInt(target.closest('[data-action="toggle-checklist"]').dataset.checklistIndex, 10);
      if (selectedTaskId) { toggleChecklist(selectedTaskId, idx); setTimeout(renderDetailPanel, 200); }
      return;
    }
    if (target.closest('[data-action="remove-checklist"]')) {
      const idx = parseInt(target.closest('[data-action="remove-checklist"]').dataset.checklistIndex, 10);
      if (selectedTaskId) { removeChecklistItem(selectedTaskId, idx); renderDetailPanel(); }
      return;
    }

    // Tags
    if (target.closest('[data-action="add-tag"]')) {
      const input = document.getElementById('detail-tag-input');
      if (input && selectedTaskId) { addTagToTask(selectedTaskId, input.value); input.value = ''; }
      return;
    }
    if (target.closest('[data-action="remove-tag"]')) {
      const btn = target.closest('[data-action="remove-tag"]');
      const tagName = btn.dataset.tag;
      if (selectedTaskId) {
        const task = tasks.find((t) => t.id === selectedTaskId);
        if (task) {
          task.tags = task.tags.filter((t) => t !== tagName);
          apiRequest(`/api/tasks/${selectedTaskId}`, 'PATCH', { tags: task.tags }).then(() => {
            clearCache(); loadFromServer();
          });
          renderDetailPanel();
        }
      }
      return;
    }

    // Add checklist item
    if (target.closest('[data-action="add-checklist"]')) {
      const input = document.getElementById('detail-checklist-input');
      if (input && selectedTaskId) { addChecklistItem(selectedTaskId, input.value); input.value = ''; renderDetailPanel(); }
      return;
    }

    // Toggle done section
    if (target.closest('[data-action="toggle-done"]')) {
      showDone = !showDone;
      render();
      return;
    }

    // Toggle history
    if (target.closest('[data-action="toggle-history"]')) {
      showHistory = !showHistory;
      renderDetailPanel();
      return;
    }

    // Remind later
    const remindBtn = target.closest('[data-action="remind-later"]');
    if (remindBtn) {
      e.stopPropagation();
      const taskId = remindBtn.dataset.taskId;
      renderRemindMenu(taskId, remindBtn);
      return;
    }
  }

  function renderRemindMenu(taskId, anchor) {
    const existing = document.querySelector('.remind-menu');
    if (existing) existing.remove();

    const menu = document.createElement('div');
    menu.className = 'remind-menu';
    menu.style.cssText = `
      position:fixed;z-index:60;background:#0d1018;border:1px solid var(--border);
      border-radius:var(--radius-sm);box-shadow:var(--shadow);padding:4px;min-width:160px;
    `;

    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.left = `${rect.left}px`;

    menu.innerHTML = REMIND_OPTIONS.map((opt) => `
      <button class="remind-menu-item" type="button" data-remind-value="${opt.value}" data-remind-task="${taskId}" style="
        display:block;width:100%;padding:8px 12px;border:none;background:transparent;
        color:var(--text-secondary);cursor:pointer;text-align:left;font-size:0.84rem;border-radius:6px;
      ">${escapeHTML(opt.label)}</button>
    `).join('');

    document.body.appendChild(menu);

    const closeMenu = (e) => {
      if (!menu.contains(e.target) && e.target !== anchor) {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      }
    };
    setTimeout(() => document.addEventListener('click', closeMenu), 10);

    menu.querySelectorAll('[data-remind-value]').forEach((btn) => {
      btn.addEventListener('click', () => {
        remindLater(btn.dataset.remindTask, btn.dataset.remindValue);
        menu.remove();
      });
      btn.addEventListener('mouseenter', () => { btn.style.background = 'var(--surface-raised)'; btn.style.color = 'var(--text)'; });
      btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; btn.style.color = 'var(--text-secondary)'; });
    });
  }

  /* ─── Keyboard shortcuts ─── */
  function handleKeydown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') {
      if (!createModalOpen) { e.preventDefault(); openCreateModal(); }
      return;
    }
    if (e.key === 'Escape') {
      if (createModalOpen) { closeCreateModal(); return; }
      if (detailOpen) { closeDetail(); return; }
    }
  }

  /* ─── Init ─── */
  function init(containerId) {
    if (initialized) {
      render();
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
  return {
    init,
    render,
    loadFromServer,
    openCreateModal,
  };
})();