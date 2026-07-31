/* ─── Calendar Module ───
   Клиентский модуль календаря. Работает с единой моделью событий,
   полученной с /api/calendar. Не хранит собственных данных — только
   отображает. Поддерживает view: month (будущие: week, day, timeline). */
window.CalendarModule = (() => {
  'use strict';

  /* ─── Icons ─── */
  const ICONS = {
    today: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 12h0.01M12 7v5a5 5 0 0 1 5 5M12 2a9.85 9.85 0 0 1 3.25.53M4 12a8 8 0 1 1 16 0 8 8 0 0 1-16 0Z"></path></svg>',
    chevronLeft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg>',
    chevronRight: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"></path></svg>',
    calendar: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="6" width="16" height="14" rx="2"></rect><path d="M8 4v3M16 4v3M4 10h16"></path></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>',
    tag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h7l9 9-7 7-9-9V4z"></path><circle cx="8" cy="8" r="1.5" fill="currentColor"></circle></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L3 14h7.5l.5 5 7-11.5L13 2z"></path></svg>',
    list: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="3"></rect><path d="M8 9h8M8 13h5"></path></svg>',
    external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5"></path><path d="M10 14 19 5"></path><path d="M19 14v5H5V5h5"></path></svg>',
  };

  const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const MONTH_NAMES = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
  ];

  /* ─── State ─── */
  let currentYear = new Date().getFullYear();
  let currentMonth = new Date().getMonth() + 1; // 1-based
  let selectedDate = null; // "YYYY-MM-DD" or null
  let events = []; // все события на текущий месяц / день
  let markers = {}; // {"YYYY-MM-DD": ["task", "reminder", ...]}
  let loading = false;
  let error = '';
  let currentView = 'month'; // "month" | "day" (extensible)
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
      const name = node.dataset.icon || 'chevronRight';
      node.innerHTML = ICONS[name] || ICONS.chevronRight;
    });
  }

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function pad(num) {
    return String(num).padStart(2, '0');
  }

  function daysInMonth(year, month) {
    return new Date(year, month, 0).getDate();
  }

  function firstDayOfWeek(year, month) {
    // 0 = Sunday (Вс), 1 = Monday (Пн)
    return new Date(year, month - 1, 1).getDay();
  }

  function formatDateFull(dateStr) {
    if (!dateStr) return ' — ';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return ' — ';
    const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
    const weekdays = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
    return `${weekdays[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
  }

  function formatTime(timeStr) {
    return timeStr ? timeStr.slice(0, 5) : '';
  }

  function getInitData() {
    return window.Telegram?.WebApp?.initData || '';
  }

  function requestHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = getInitData();
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  async function apiRequest(path) {
    const response = await fetch(path, { headers: requestHeaders() });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    }
    return data;
  }

  /* ─── Data Loading ─── */
  async function loadMonth(year, month) {
    if (currentView !== 'month') return;
    loading = true;
    error = '';
    render();

    try {
      const [eventsResp, markersResp] = await Promise.all([
        apiRequest(`/api/calendar/month?year=${year}&month=${month}`),
        apiRequest(`/api/calendar/markers?year=${year}&month=${month}`),
      ]);

      events = eventsResp.data?.events || [];
      markers = markersResp.data?.markers || {};
    } catch (e) {
      error = e.message;
      events = [];
      markers = {};
    }

    loading = false;
    render();
  }

  async function loadDay(dateStr) {
    if (!dateStr) return;
    loading = true;
    error = '';
    render();

    try {
      const resp = await apiRequest(`/api/calendar/day?date=${dateStr}`);
      events = resp.data?.events || [];
    } catch (e) {
      error = e.message;
      events = [];
    }

    loading = false;
    render();
  }

  /* ─── Rendering ─── */

  function render() {
    const container = document.getElementById('calendar-container');
    if (!container) return;

    container.innerHTML = '';
    const fragment = document.createRange().createContextualFragment(
      renderPage()
    );
    container.appendChild(fragment);
    hydrateIcons(container);
  }

  function renderPage() {
    return `
      <div class="calendar-wrapper">
        ${renderHeader()}
        ${renderBody()}
      </div>
    `;
  }

  function renderHeader() {
    const viewToggle = `
      <div class="calendar-view-toggle">
        <button class="calendar-view-button ${currentView === 'month' ? 'active' : ''}" type="button" data-action="set-view" data-view="month">Месяц</button>
        <button class="calendar-view-button" type="button" data-action="set-view" data-view="day" disabled>День</button>
        <button class="calendar-view-button" type="button" data-action="set-view" data-view="week" disabled>Неделя</button>
      </div>
    `;

    return `
      <div class="calendar-header">
        <h1>Календарь</h1>
        ${viewToggle}
      </div>
      <div class="calendar-nav">
        <button class="nav-button" type="button" data-action="prev-month" aria-label="Предыдущий месяц">
          <span class="icon" data-icon="chevronLeft" aria-hidden="true"></span>
        </button>
        <h2>${MONTH_NAMES[currentMonth - 1]} ${currentYear}</h2>
        <button class="nav-button" type="button" data-action="next-month" aria-label="Следующий месяц">
          <span class="icon" data-icon="chevronRight" aria-hidden="true"></span>
        </button>
      </div>
    `;
  }

  function renderBody() {
    if (loading) {
      return `
        <div class="card calendar-card span-12">
          <div class="calendar-loading">
            <p>Загружаю события…</p>
          </div>
        </div>
      `;
    }

    if (error) {
      return `
        <div class="card calendar-card span-12">
          <div class="calendar-error">
            <span class="icon" data-icon="alert" aria-hidden="true"></span>
            <p>${escapeHTML(error)}</p>
          </div>
        </div>
      `;
    }

    if (currentView === 'month') {
      return renderMonth();
    }

    if (currentView === 'day' && selectedDate) {
      return renderDayDetail();
    }

    return renderMonth();
  }

  function renderMonth() {
    const totalDays = daysInMonth(currentYear, currentMonth);
    const startDay = firstDayOfWeek(currentYear, currentMonth); // 0 = Sun

    // Сдвиг начала недели на понедельник
    let offset = (startDay === 0 ? 6 : startDay - 1);

    const today = todayStr();
    const cells = [];

    for (let i = 0; i < offset; i++) {
      cells.push('<div class="calendar-day empty"></div>');
    }

    for (let day = 1; day <= totalDays; day++) {
      const dateStr = `${currentYear}-${pad(currentMonth)}-${pad(day)}`;
      const isToday = dateStr === today;
      const isSelected = selectedDate === dateStr;
      const dayMarkers = markers[dateStr] || [];
      const hasEvents = dayMarkers.length > 0;

      let dayClass = '';
      if (isToday) dayClass += ' today';
      if (isSelected) dayClass += ' selected';
      if (hasEvents) dayClass += ' has-events';

      let markersHtml = '';
      if (hasEvents) {
        const markerColors = dayMarkers.map((type) => {
          const colorMap = {
            task: '#5ab8ff',
            reminder: '#a78bfa',
            payment: '#52d273',
            subscription: '#f5b942',
            event: '#ff5d73',
          };
          return colorMap[type] || '#5ab8ff';
        });
        markersHtml = `<div class="calendar-day-markers">${markerColors.map((c) => `<span class="calendar-day-marker" style="background:${c}"></span>`).join('')}</div>`;
        if (dayMarkers.length > 4) {
          markersHtml += `<span class="calendar-day-marker-count has-events">+${dayMarkers.length - 4}</span>`;
        }
      }

      cells.push(`
        <div class="calendar-day${dayClass}" data-date="${dateStr}" data-action="select-day">
          <span class="calendar-day-number">${day}</span>
          ${markersHtml}
        </div>
      `);
    }

    const totalCells = Math.ceil((offset + totalDays) / 7) * 7;
    const remaining = totalCells - (offset + totalDays);
    for (let i = 0; i < remaining; i++) {
      cells.push('<div class="calendar-day empty"></div>');
    }

    return `
      <button class="calendar-today-button" type="button" data-action="go-today">Сегодня</button>
      <div class="card calendar-card span-12">
        <div class="calendar-month">
          ${WEEKDAYS.map((d) => `<div class="calendar-day-header">${d}</div>`).join('')}
          ${cells.join('')}
        </div>
      </div>
      ${selectedDate ? renderDayDetailCard() : ''}
    `;
  }

  function renderDayDetail() {
    return renderDayDetailCard();
  }

  function renderDayDetailCard() {
    if (!selectedDate) return '';

    const dayEvents = events.filter((e) => e.date === selectedDate);
    const dateFull = formatDateFull(selectedDate);

    return `
      <div class="card calendar-day-detail-card span-12">
        <div class="calendar-nav" style="margin-bottom:12px">
          <button class="nav-button" type="button" data-action="prev-day" aria-label="Предыдущий день">
            <span class="icon" data-icon="chevronLeft" aria-hidden="true"></span>
          </button>
          <h2>${escapeHTML(dateFull)}</h2>
          <button class="nav-button" type="button" data-action="next-day" aria-label="Следующий день">
            <span class="icon" data-icon="chevronRight" aria-hidden="true"></span>
          </button>
        </div>

        ${dayEvents.length === 0
          ? `
            <div class="calendar-day-empty">
              <span class="icon" data-icon="calendar" aria-hidden="true"></span>
              <p>На этот день нет задач, событий или платежей.</p>
            </div>
          `
          : `
            <div class="calendar-day-detail">
              <div class="calendar-day-event-list">
                ${dayEvents.map((ev) => renderEventItem(ev)).join('')}
              </div>
            </div>
          `}
      </div>
    `;
  }

  function renderEventItem(ev) {
    const typeLabel = ev.label || 'Событие';
    const color = ev.color || '#5ab8ff';
    const projectDot = ev.project_color
      ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${escapeHTML(ev.project_color)};margin-right:4px;vertical-align:middle"></span>`
      : '';
    const projectName = ev.project ? `${projectDot}${escapeHTML(ev.project)}` : '';
    const timeStr = formatTime(ev.time);
    const tagsHtml = (ev.tags || []).length > 0
      ? `<div class="calendar-event-meta">${ev.tags.map((t) => `<span class="calendar-event-tag">${escapeHTML(t)}</span>`).join('')}</div>`
      : '';

    return `
      <div class="calendar-day-event" style="border-left-color:${color}" data-action="open-event" data-event-id="${escapeHTML(ev.id)}" data-event-source="${escapeHTML(ev.source)}">
        <div class="calendar-event-meta-col">
          <span class="calendar-event-type-badge calendar-event-type-${escapeHTML(ev.type)}">${escapeHTML(typeLabel)}</span>
        </div>
        <div class="calendar-event-body">
          <div class="calendar-event-title">${escapeHTML(ev.title)}</div>
          ${timeStr
            ? `<div class="calendar-event-time"><span class="icon" data-icon="clock" aria-hidden="true"></span>${timeStr}</div>`
            : '<div style="height:14px"></div>'
          }
          ${projectName ? `<div class="calendar-event-meta">${projectName}</div>` : ''}
          ${tagsHtml}
        </div>
        <span class="icon" data-icon="external" aria-hidden="true" style="width:14px;height:14px;color:var(--text-muted);margin-left:auto"></span>
      </div>
    `;
  }

  /* ─── Event Handlers ─── */
  function handlePrevMonth() {
    currentMonth--;
    if (currentMonth < 1) {
      currentMonth = 12;
      currentYear--;
    }
    selectedDate = null;
    loadMonth(currentYear, currentMonth);
  }

  function handleNextMonth() {
    currentMonth++;
    if (currentMonth > 12) {
      currentMonth = 1;
      currentYear++;
    }
    selectedDate = null;
    loadMonth(currentYear, currentMonth);
  }

  function handleToday() {
    const d = new Date();
    currentYear = d.getFullYear();
    currentMonth = d.getMonth() + 1;
    const today = todayStr();
    selectedDate = today;
    loadMonth(currentYear, currentMonth);
    loadDay(today);
  }

  function handleSelectDay(dateStr) {
    selectedDate = dateStr;
    loadDay(dateStr);
  }

  function handlePrevDay() {
    if (!selectedDate) return;
    const d = new Date(selectedDate);
    d.setDate(d.getDate() - 1);
    selectedDate = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    loadDay(selectedDate);
  }

  function handleNextDay() {
    if (!selectedDate) {
      selectedDate = todayStr();
    } else {
      const d = new Date(selectedDate);
      d.setDate(d.getDate() + 1);
      selectedDate = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }
    loadDay(selectedDate);
  }

  function handleSetView(view) {
    currentView = view;
    render();
    if (view === 'day') {
      if (!selectedDate) selectedDate = todayStr();
      loadDay(selectedDate);
    } else if (view === 'month') {
      loadMonth(currentYear, currentMonth);
    }
  }

  function handleOpenEvent(eventId, source) {
    // Переход к сущности по источнику.
    if (source === 'tasks' || source === 'events') {
      const realId = eventId.replace('reminder-', '').replace('event-', '');
      if (window.TasksModule && window.TasksModule.openDetail) {
        window.TasksModule.openDetail(realId);
      } else {
        const btn = document.querySelector(`[data-view="tasks"]`);
        if (btn) {
          btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
          // Ждём рендера и открываем деталь
          setTimeout(() => {
            if (window.TasksModule && window.TasksModule.openDetail) {
              window.TasksModule.openDetail(realId);
            }
          }, 300);
        }
      }
    }
  }

  /* ─── Initialization ─── */
  function init(containerId) {
    const container = document.getElementById(containerId);
    if (!container) {
      console.warn('[CalendarModule] Container not found:', containerId);
      return;
    }

    const d = new Date();
    currentYear = d.getFullYear();
    currentMonth = d.getMonth() + 1;
    currentView = 'month';
    selectedDate = null;
    events = [];
    markers = {};
    loading = true;
    error = '';

    container.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;

      if (action === 'prev-month') { event.preventDefault(); handlePrevMonth(); return; }
      if (action === 'next-month') { event.preventDefault(); handleNextMonth(); return; }
      if (action === 'go-today') { event.preventDefault(); handleToday(); return; }
      if (action === 'prev-day') { event.preventDefault(); handlePrevDay(); return; }
      if (action === 'next-day') { event.preventDefault(); handleNextDay(); return; }
      if (action === 'set-view') { event.preventDefault(); handleSetView(btn.dataset.view); return; }

      const dayBtn = event.target.closest('[data-action="select-day"]');
      if (dayBtn) {
        event.preventDefault();
        handleSelectDay(dayBtn.dataset.date);
        return;
      }

      const eventBtn = event.target.closest('[data-action="open-event"]');
      if (eventBtn) {
        event.preventDefault();
        handleOpenEvent(eventBtn.dataset.eventId, eventBtn.dataset.eventSource);
        return;
      }
    });

    // Загружаем события месяца и маркеры
    loadMonth(currentYear, currentMonth);

    // Загружаем события на сегодня для детальной панели
    const today = todayStr();
    selectedDate = today;
    loadDay(today);

    initialized = true;
  }

  /* ─── Public API ─── */
  return {
    init,
    loadMonth,
    loadDay,
    reload: () => {
      if (currentView === 'month') {
        loadMonth(currentYear, currentMonth);
      } else if (selectedDate) {
        loadDay(selectedDate);
      }
    },
    getState: () => ({ currentYear, currentMonth, selectedDate, currentView, loading, error, events }),
  };
})();
