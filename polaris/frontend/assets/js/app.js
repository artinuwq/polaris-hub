(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready?.();

  /* ─── Platform detection ─── */
  const platform = tg?.platform || 'unknown';
  const isMobile = /android|ios/i.test(platform) || window.innerWidth < 720;
  const isDesktop = /macos|windows|linux/i.test(platform) && window.innerWidth >= 720;
  const isWeb = platform === 'web' && window.innerWidth >= 720;
  const isFullscreen = tg?.isFullscreen || false;

  if (isMobile) {
    document.body.classList.add('tg-mobile');
    // На телефоне раскрываем на весь экран с небольшой задержкой
    setTimeout(() => {
      tg?.expand?.();
      // Пробуем полноэкранный режим если поддерживается
      if (tg?.requestFullscreen) {
        tg.requestFullscreen().catch(() => {});
      }
    }, 300);
  }
  if (isDesktop) {
    document.body.classList.add('tg-desktop');
  }
  if (isWeb) {
    document.body.classList.add('tg-web');
  }
  if (isFullscreen) {
    document.body.classList.add('tg-fullscreen');
  }

  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl = params.get('token') || '';
  const tokenStorageKey = 'polaris_update_token';
  if (tokenFromUrl) {
    localStorage.setItem(tokenStorageKey, tokenFromUrl);
  }
  const apiToken = tokenFromUrl || localStorage.getItem(tokenStorageKey) || '';

  const elements = {
    authStatus: document.getElementById('auth-status'),
    currentViewLabel: document.getElementById('current-view-label'),
    topbarTitle: document.getElementById('topbar-title'),
    topbarSubtitle: document.getElementById('topbar-subtitle'),
    mainContent: document.getElementById('main-content'),
    drawer: document.getElementById('drawer'),
    drawerNav: document.getElementById('drawer-nav'),
    drawerToggle: document.getElementById('drawer-toggle'),
    brandButton: document.getElementById('brand-button'),
    searchToggle: document.getElementById('search-toggle'),
    searchCapsule: document.getElementById('search-capsule'),
    searchOverlay: document.getElementById('search-overlay'),
    searchInput: document.getElementById('search-input'),
    searchHints: document.getElementById('search-hints'),
    searchResults: document.getElementById('search-results'),
    backdrop: document.getElementById('backdrop'),
  };

  const ICONS = {
    menu:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"></path></svg>',
    close:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"></path></svg>',
    search:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>',
    star:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.6 5.4 6 .9-4.3 4.2 1 6-5.3-2.8-5.3 2.8 1-6-4.3-4.2 6-.9L12 3z"></path></svg>',
    tasks:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="3"></rect><path d="M8 9h8M8 13h5"></path></svg>',
    servers:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="6" rx="2"></rect><rect x="4" y="14" width="16" height="6" rx="2"></rect><path d="M8 7h.01M8 17h.01"></path></svg>',
    finance:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h10a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-4a3 3 0 0 1 3-3Z"></path><path d="M12 8v8M9 11.5c0-1 .9-1.5 2.3-1.5h1.4c1.4 0 2.3.5 2.3 1.5S14.1 13 12.8 13h-1.6C9.9 13 9 13.5 9 14.5s.9 1.5 2.3 1.5h1.4c1.4 0 2.3-.5 2.3-1.5"></path></svg>',
    knowledge:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h8a4 4 0 0 1 4 4v10a3 3 0 0 0-3-3H5z"></path><path d="M19 5h-2a4 4 0 0 0-4 4v10"></path></svg>',
    athena:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 12 2-4 2 4 4 2-4 2-2 4-2-4-4-2z"></path><path d="m17 6 .8 1.8L20 9l-2.2.6L17 11l-.8-1.4L14 9l2.2-1.2z"></path></svg>',
    home:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5 12 4l8 7.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"></path></svg>',
    settings:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4h4l.7 2.1 2.2 1 2.1-.5 2 3.5-1.5 1.6v2l1.5 1.6-2 3.5-2.1-.5-2.2 1L14 20h-4l-.7-2.1-2.2-1-2.1.5-2-3.5 1.5-1.6v-2L2 9.7l2-3.5 2.1.5 2.2-1L10 4z"></path><circle cx="12" cy="12" r="3"></circle></svg>',
    update:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 0 1-13.4 5.9"></path><path d="M4 12a8 8 0 0 1 13.4-5.9"></path><path d="m14 4.5 3.5 2-.5-4"></path><path d="m10 19.5-3.5-2 .5 4"></path></svg>',
    restart:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.5 7.5A7 7 0 1 1 6 12"></path><path d="M7 4v4h4"></path></svg>',
    monitor:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"></rect><path d="M8 20h8M12 16v4"></path></svg>',
    external:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5"></path><path d="M10 14 19 5"></path><path d="M19 14v5H5V5h5"></path></svg>',
    spark:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8L12 2z"></path><path d="m18.5 14 1.1 3.2L23 18.3l-3.4 1.1L18.5 23l-1.1-3.6L14 18.3l3.4-1.1z"></path></svg>',
    chevron:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>',
  };

  const NAV_ITEMS = [
    { id: 'attention', label: 'Attention', subtitle: 'Что сейчас заслуживает внимания', icon: 'star' },
    { id: 'tasks', label: 'Tasks', subtitle: 'Дела и быстрые действия', icon: 'tasks' },
    { id: 'servers', label: 'Servers', subtitle: 'Инфраструктура', icon: 'servers' },
    { id: 'finance', label: 'Finance', subtitle: 'То, что требует оплаты', icon: 'finance' },
    { id: 'knowledge', label: 'Knowledge', subtitle: 'Заметки и база знаний', icon: 'knowledge' },
    { id: 'athena', label: 'Athena', subtitle: 'Интеллектуальный помощник', icon: 'athena' },
    { id: 'home', label: 'Home', subtitle: 'Home Assistant', icon: 'home' },
    { id: 'settings', label: 'Settings', subtitle: 'Команды и доступ', icon: 'settings', footer: true },
  ];

  const state = {
    authenticated: false,
    authMode: '',
    authMessage: 'Проверяю доступ…',
    user: null,
    currentView: 'attention',
    drawerOpen: false,
    searchOpen: false,
    searchQuery: '',
    busy: '',
    notice: null,
    update: {
      loading: false,
      loaded: false,
      data: null,
      message: '',
      error: '',
    },
  };

  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function hydrateIcons(root = document) {
    root.querySelectorAll('[data-icon]').forEach((node) => {
      const name = node.dataset.icon || 'chevron';
      node.innerHTML = ICONS[name] || ICONS.chevron;
    });
  }

  function shortSha(sha) {
    return sha ? sha.slice(0, 7) : '—';
  }

  function normalizeAuthMode(rawMode) {
    return rawMode === 'token' ? 'browser' : 'telegram';
  }

  function setAuthBanner(text, tone = '') {
    if (!elements.authStatus) return;
    elements.authStatus.textContent = text;
    elements.authStatus.classList.remove('ok', 'err', 'warn');
    if (tone) {
      elements.authStatus.classList.add(tone);
    }
  }

  function setNotice(text, tone = '') {
    state.notice = text ? { text, tone } : null;
    renderMain();
  }

  function getViewMeta(viewId) {
    return NAV_ITEMS.find((item) => item.id === viewId) || NAV_ITEMS[0];
  }

  function updateTopbar() {
    const meta = getViewMeta(state.currentView);
    if (elements.currentViewLabel) {
      elements.currentViewLabel.textContent = meta.label;
    }
    if (elements.topbarTitle) {
      elements.topbarTitle.textContent = meta.label;
    }
    if (elements.topbarSubtitle) {
      elements.topbarSubtitle.textContent = meta.subtitle;
    }
    const capsuleText = document.getElementById('search-capsule-text');
    if (capsuleText) {
      capsuleText.textContent = meta.label === 'Attention' ? 'Polaris' : meta.label;
    }
  }

  function syncOverlays() {
    if (elements.drawer) {
      elements.drawer.classList.toggle('is-open', state.drawerOpen);
      elements.drawer.setAttribute('aria-hidden', String(!state.drawerOpen));
    }
    if (elements.searchOverlay) {
      elements.searchOverlay.classList.toggle('is-open', state.searchOpen);
      elements.searchOverlay.setAttribute('aria-hidden', String(!state.searchOpen));
    }
    if (elements.backdrop) {
      elements.backdrop.hidden = !(state.drawerOpen || state.searchOpen);
    }
    document.body.classList.toggle('search-open', state.searchOpen);
    document.body.style.overflow = state.drawerOpen || state.searchOpen ? 'hidden' : '';
    syncBackButton();
  }

  /* ─── Telegram native back button ───
     Заменяет собой кастомные крестики: пока открыт поиск или drawer,
     показываем системную стрелку/крестик ТГ вместо своих кнопок закрытия. */
  function syncBackButton() {
    if (!tg?.BackButton) return;
    if (state.searchOpen || state.drawerOpen) {
      tg.BackButton.show();
    } else {
      tg.BackButton.hide();
    }
  }

  function handleBackButton() {
    if (state.searchOpen) {
      closeSearch();
      return;
    }
    if (state.drawerOpen) {
      closeDrawer();
    }
  }

  tg?.BackButton?.onClick?.(handleBackButton);

  function openDrawer() {
    state.drawerOpen = true;
    state.searchOpen = false;
    syncOverlays();
  }

  function closeDrawer() {
    state.drawerOpen = false;
    syncOverlays();
  }

  function toggleDrawer() {
    state.drawerOpen = !state.drawerOpen;
    if (state.drawerOpen) {
      state.searchOpen = false;
    }
    syncOverlays();
  }

  function openSearch() {
    state.searchOpen = true;
    state.drawerOpen = false;
    syncOverlays();
    renderSearch();
    requestAnimationFrame(() => {
      elements.searchInput?.focus();
      elements.searchInput?.select?.();
    });
  }

  function closeSearch() {
    state.searchOpen = false;
    state.searchQuery = '';
    if (elements.searchInput) {
      elements.searchInput.value = '';
    }
    syncOverlays();
    renderSearch();
  }

  function setView(viewId) {
    state.currentView = viewId;
    closeDrawer();
    closeSearch();
    updateTopbar();
    renderMain();

    // Initialize tasks module when navigating to tasks view
    if (viewId === 'tasks' && window.TasksModule) {
      window.TasksModule.init('tasks-container');
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function toggleActionBusy(busy) {
    state.busy = busy || '';
    renderMain();
  }

  function getInitData() {
    return window.Telegram?.WebApp?.initData || '';
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
    if (!initData && !apiToken) {
      throw new Error(
        'На компьютере откройте Polaris через команду /browser в Telegram-боте. Внутри Telegram Mini App запускается напрямую.'
      );
    }

    if (initData) {
      state.authMode = 'telegram';
      return request('/api/tg/auth', 'POST', { initData });
    }

    state.authMode = 'browser';
    return request('/api/me');
  }

  function renderDrawer() {
    const primary = NAV_ITEMS.filter((item) => !item.footer);
    const footer = NAV_ITEMS.filter((item) => item.footer);

    elements.drawerNav.innerHTML = [
      ...primary.map((item) => drawerItemTemplate(item)),
      footer.length ? '<div class="drawer-divider"></div>' : '',
      ...footer.map((item) => drawerItemTemplate(item)),
    ].join('');
    hydrateIcons(elements.drawerNav);
  }

  function drawerItemTemplate(item) {
    const active = item.id === state.currentView ? ' active' : '';
    return `
      <button class="drawer-item${active}" type="button" data-view="${escapeHTML(item.id)}">
        <span class="icon" data-icon="${escapeHTML(item.icon)}" aria-hidden="true"></span>
        <span class="drawer-item-copy">
          <span class="drawer-item-title">${escapeHTML(item.label)}</span>
          <span class="drawer-item-subtitle">${escapeHTML(item.subtitle)}</span>
        </span>
        <span class="icon" data-icon="chevron" aria-hidden="true"></span>
      </button>
    `;
  }

  function renderNotice() {
    if (!state.notice) {
      return '';
    }

    const tone = state.notice.tone ? ` tone-${escapeHTML(state.notice.tone)}` : '';
    return `
      <section class="card notice-card${tone} span-12">
        <div class="notice-text">${escapeHTML(state.notice.text)}</div>
      </section>
    `;
  }

  function updateSummary() {
    if (state.update.loading) {
      return {
        title: 'Проверяю обновления',
        tone: 'primary',
        subtitle: 'Сверяю локальную и удаленную версию.',
      };
    }

    if (state.update.error) {
      return {
        title: 'Ошибка проверки',
        tone: 'error',
        subtitle: state.update.error,
      };
    }

    if (!state.update.loaded || !state.update.data) {
      return {
        title: 'Проверка еще не выполнена',
        tone: 'neutral',
        subtitle: 'Нажмите «Проверить», чтобы увидеть текущий статус.',
      };
    }

    const data = state.update.data;
    if (data.up_to_date && !data.dirty) {
      return {
        title: `Ветка ${data.branch} актуальна`,
        tone: 'success',
        subtitle: `Локальная версия совпадает с remote. SHA ${shortSha(data.local_sha)}`,
      };
    }

    if (data.up_to_date && data.dirty) {
      return {
        title: `Ветка ${data.branch} актуальна`,
        tone: 'warning',
        subtitle: `Есть локальные изменения. SHA ${shortSha(data.local_sha)}`,
      };
    }

    return {
      title: 'Доступно обновление',
      tone: 'warning',
      subtitle: `${shortSha(data.local_sha)} → ${shortSha(data.remote_sha)} · ветка ${data.branch}`,
    };
  }

  function renderAttentionView() {
    const update = updateSummary();
    const currentTone = update.tone === 'error' ? 'error' : update.tone === 'warning' ? 'warning' : 'success';
    const actionBusy = Boolean(state.busy);

    return `
      ${renderNotice()}
      <section class="page-intro">
        <div>
          <p class="eyebrow">Attention</p>
          <h1 class="page-title">Состояние Polaris</h1>
          <p class="page-subtitle">Только то, что требует внимания сейчас.</p>
        </div>

        <div class="page-actions">
          <button class="small-button primary" type="button" data-action="check-update" ${actionBusy ? 'disabled' : ''}>
            ${state.busy === 'check' ? 'Проверяю…' : 'Проверить'}
          </button>
          <button class="small-button" type="button" data-action="run-update" ${actionBusy ? 'disabled' : ''}>
            ${state.busy === 'update' ? 'Обновляю…' : 'Обновить'}
          </button>
          <button class="small-button ghost" type="button" data-action="run-restart" ${actionBusy ? 'disabled' : ''}>
            ${state.busy === 'restart' ? 'Перезапускаю…' : 'Перезапуск'}
          </button>
        </div>
      </section>

      <article class="card status-card">
        <div class="status-card-copy">
          <p class="kicker">Обновления</p>
          <h2>${escapeHTML(update.title)}</h2>
          <p>${escapeHTML(update.subtitle)}</p>
        </div>
        <div class="badge-row">
          <span class="badge ${currentTone}">${escapeHTML(state.update.loading ? 'ПРОВЕРКА' : update.tone.toUpperCase())}</span>
          <span class="badge">${escapeHTML(state.update.loaded ? 'Проверено' : 'Ожидает проверки')}</span>
        </div>
      </article>
    `;
  }

  function renderModuleView(view) {
    if (view.id === 'tasks') {
      return `
        ${renderNotice()}
        <div id="tasks-container" class="tasks-page"></div>
      `;
    }

    if (view.id === 'settings') {
      return `
        ${renderNotice()}
        <section class="card module-card span-12">
          <p class="eyebrow">Settings</p>
          <h1 class="module-title">Команды и доступ</h1>
          <p class="module-text">
            Настройки меняются из Telegram-бота. На компьютере используйте браузерный вход через token.
          </p>
          <div class="badge-row">
            <span class="badge primary">/config</span>
            <span class="badge">/browser</span>
            <span class="badge">/restart</span>
            <span class="badge">WEBAPP_URL</span>
          </div>
        </article>

        <div class="grid">
          <article class="card list-card span-8">
            <h3>Команды</h3>
            <div class="calm-steps">
              <div class="calm-step">
                <div>
                  <strong>/config</strong>
                  <span>Показать список поддерживаемых параметров.</span>
                </div>
                <span class="list-item-meta">overview</span>
              </div>
              <div class="calm-step">
                <div>
                  <strong>/config set WEBAPP_URL https://example.com</strong>
                  <span>Сохранить URL Mini App в файле .env.</span>
                </div>
                <span class="list-item-meta">write</span>
              </div>
              <div class="calm-step">
                <div>
                  <strong>/config get WEBAPP_URL</strong>
                  <span>Посмотреть текущее значение.</span>
                </div>
                <span class="list-item-meta">read</span>
              </div>
              <div class="calm-step">
                <div>
                  <strong>/restart</strong>
                  <span>Перезапустить сервис после подтверждения.</span>
                </div>
                <span class="list-item-meta">service</span>
              </div>
            </div>
          </article>

          <article class="card list-card span-4">
            <h3>Правило</h3>
            <p class="metric-subtext">
              Polaris не должен заставлять пользователя помнить, где что лежит. Поиск и одна команда должны быть
              быстрее навигации по меню.
            </p>
          </article>
        </div>
      `;
    }

    return `
      ${renderNotice()}
      <section class="card module-card span-12">
        <p class="eyebrow">${escapeHTML(view.label)}</p>
        <h1 class="module-title">${escapeHTML(view.label)}</h1>
        <p class="module-text">${escapeHTML(view.subtitle)}. Сейчас это спокойный каркас для будущих данных и действий.</p>
        <div class="inline-actions">
          <button class="small-button primary" type="button" data-action="open-search">Открыть поиск</button>
          <button class="small-button" type="button" data-view="attention">Вернуться в Attention</button>
        </div>
      </section>

      <div class="grid">
        <article class="card list-card span-8">
          <h3>Сейчас</h3>
          <div class="calm-steps">
            <div class="calm-step">
              <div>
                <strong>Контент подключается позже</strong>
                <span>Здесь появятся реальные данные для этого раздела.</span>
              </div>
              <span class="list-item-meta">scaffold</span>
            </div>
            <div class="calm-step">
              <div>
                <strong>Поиск ведет в разделы</strong>
                <span>Можно открыть этот экран через глобальный поиск.</span>
              </div>
              <span class="list-item-meta">spotlight</span>
            </div>
          </div>
        </article>

        <article class="card list-card span-4">
          <h3>Контекст</h3>
          <p class="metric-subtext">
            Минимализм, спокойствие и быстрые действия. Без лишнего шума и без перегруженных списков.
          </p>
        </article>
      </div>
    `;
  }

  function renderAccessGate() {
    return `
      <section class="card hero-card span-12">
        <div class="hero-copy">
          <p class="eyebrow">Access</p>
          <h1>Нет доступа</h1>
          <p class="access-message" data-auth-message></p>
          <div class="badge-row">
            <span class="badge warning">Telegram initData или token</span>
            <span class="badge">Mini App</span>
            <span class="badge">Browser</span>
          </div>
        </div>

        <div class="hero-actions">
          <button class="small-button primary" type="button" data-action="browser-help">Как открыть на ПК</button>
          <button class="small-button ghost" type="button" data-action="open-search">Поиск</button>
        </div>
      </section>

      <article class="card list-card span-12">
        <h3>Что делать</h3>
        <div class="calm-steps">
          <div class="calm-step">
            <div>
              <strong>Откройте /browser в Telegram-боте</strong>
              <span>Так на компьютере появится доступ через token, без Telegram initData.</span>
            </div>
            <span class="list-item-meta">desktop</span>
          </div>
          <div class="calm-step">
            <div>
              <strong>Внутри Telegram Mini App все работает напрямую</strong>
              <span>Это основной путь на телефоне и на устройствах с доступом к Telegram.</span>
            </div>
            <span class="list-item-meta">mini app</span>
          </div>
        </div>
      </article>
    `;
  }

  function renderMain() {
    if (!elements.mainContent) {
      return;
    }

    const view = getViewMeta(state.currentView);
    elements.mainContent.innerHTML = state.authenticated
      ? state.currentView === 'attention'
        ? renderAttentionView()
        : renderModuleView(view)
      : renderAccessGate();

    if (!state.authenticated) {
      const message = elements.mainContent.querySelector('[data-auth-message]');
      if (message) {
        message.textContent = state.authMessage || 'Откройте Polaris из Telegram Mini App или через /browser.';
      }
    }

    hydrateIcons(elements.mainContent);
  }

  function renderSearchHints() {
    if (!elements.searchHints) {
      return;
    }

    const hints = ['Attention', 'Tasks', 'Servers', 'Finance', 'browser', 'restart', 'WEBAPP_URL', '/config'];
    elements.searchHints.innerHTML = hints
      .map(
        (hint) => `
          <button class="search-hint" type="button" data-search-query="${escapeHTML(hint)}">
            ${escapeHTML(hint)}
          </button>
        `
      )
      .join('');
  }

  function searchCatalog() {
    const browserHelpTitle = state.authMode === 'browser' ? 'Скопировать ссылку' : 'Как открыть на ПК';
    const browserHelpAction = state.authMode === 'browser' ? 'copy-current-url' : 'browser-help';

    return [
      ...NAV_ITEMS.map((item) => ({
        id: `view:${item.id}`,
        kind: 'Section',
        title: item.label,
        subtitle: item.subtitle,
        icon: item.icon,
        keywords: [item.label, item.subtitle, item.id],
        run: () => setView(item.id),
        pinned: item.id === 'attention' || item.id === 'settings',
      })),
      {
        id: 'action:check-update',
        kind: 'Action',
        title: 'Проверить обновления',
        subtitle: '/status',
        icon: 'update',
        keywords: ['update', 'status', 'check', 'version'],
        run: () => checkUpdate(),
        pinned: true,
      },
      {
        id: 'action:run-update',
        kind: 'Action',
        title: 'Обновить Polaris',
        subtitle: '/update',
        icon: 'update',
        keywords: ['update', 'apply', 'git', 'refresh'],
        run: () => runUpdate(),
        pinned: true,
      },
      {
        id: 'action:restart',
        kind: 'Action',
        title: 'Перезапуск сервиса',
        subtitle: '/restart',
        icon: 'restart',
        keywords: ['restart', 'service', 'reboot', 'systemd'],
        run: () => runRestart(),
        pinned: true,
      },
      {
        id: 'action:open-search',
        kind: 'Action',
        title: 'Глобальный поиск',
        subtitle: 'Spotlight / Command palette',
        icon: 'search',
        keywords: ['search', 'command', 'palette', 'spotlight', 'raycast'],
        run: () => openSearch(),
        pinned: true,
      },
      {
        id: 'action:browser-help',
        kind: 'Action',
        title: browserHelpTitle,
        subtitle: state.authMode === 'browser' ? 'Скопировать текущую ссылку.' : 'Подсказка для доступа с компьютера.',
        icon: state.authMode === 'browser' ? 'external' : 'monitor',
        keywords: ['browser', 'desktop', 'token', 'pc', 'computer', 'access'],
        run: () => handleAction(browserHelpAction),
        pinned: true,
      },
    ];
  }

  function renderSearch() {
    if (!state.searchOpen || !elements.searchResults) {
      return;
    }

    renderSearchHints();

    const query = state.searchQuery.trim().toLowerCase();
    const items = searchCatalog()
      .map((item) => ({
        ...item,
        haystack: [item.title, item.subtitle, ...(item.keywords || [])].join(' ').toLowerCase(),
      }))
      .filter((item) => {
        if (!query) {
          return item.pinned;
        }
        return item.haystack.includes(query);
      })
      .sort((left, right) => {
        if (!query) {
          return Number(right.pinned) - Number(left.pinned);
        }
        const leftStarts = left.title.toLowerCase().startsWith(query) ? 1 : 0;
        const rightStarts = right.title.toLowerCase().startsWith(query) ? 1 : 0;
        if (leftStarts !== rightStarts) {
          return rightStarts - leftStarts;
        }
        return left.title.localeCompare(right.title, 'ru');
      });

    if (!items.length) {
      elements.searchResults.innerHTML = `
        <div class="card list-card">
          <h3>Ничего не найдено</h3>
          <p class="metric-subtext">Попробуйте Attention, update, browser, restart или WEBAPP_URL.</p>
        </div>
      `;
      hydrateIcons(elements.searchResults);
      return;
    }

    elements.searchResults.innerHTML = items
      .map(
        (item) => `
          <button class="search-item" type="button" data-result-id="${escapeHTML(item.id)}">
            <div class="search-item-top">
              <span class="icon" data-icon="${escapeHTML(item.icon)}" aria-hidden="true"></span>
              <span class="search-item-title">${escapeHTML(item.title)}</span>
              <span class="search-item-kind">${escapeHTML(item.kind)}</span>
            </div>
            <div class="search-item-subtitle">${escapeHTML(item.subtitle)}</div>
          </button>
        `
      )
      .join('');

    hydrateIcons(elements.searchResults);
  }

  function renderShell() {
    updateTopbar();
    renderDrawer();
    renderMain();
    renderSearch();
    syncOverlays();
  }

  async function loadUpdateStatus() {
    toggleActionBusy('check');
    try {
      const payload = await request('/api/update/status');
      state.update.loading = false;
      state.update.loaded = true;
      state.update.data = payload.data || null;
      state.update.message = payload.message || '';
      state.update.error = '';
      const tone =
        payload.data?.up_to_date && !payload.data?.dirty
          ? 'success'
          : payload.data?.dirty || payload.data?.up_to_date === false
            ? 'warning'
            : 'success';
      if (tone === 'success') {
        setNotice('');
      } else {
        setNotice(payload.message || 'Статус обновлений получен.', tone);
      }
    } catch (error) {
      state.update.loading = false;
      state.update.loaded = true;
      state.update.error = error.message;
      state.update.message = '';
      setNotice(`Ошибка проверки: ${error.message}`, 'error');
    } finally {
      state.busy = '';
      renderShell();
    }
  }

  async function checkUpdate() {
    state.update.loading = true;
    renderShell();
    await loadUpdateStatus();
  }

  async function runUpdate() {
    const ok = window.confirm(
      'Обновить Polaris из git-ветки? Локальные изменения на сервере будут сброшены.'
    );
    if (!ok) {
      return;
    }

    toggleActionBusy('update');
    try {
      const payload = await request('/api/update', 'POST');
      setNotice(payload.message || 'Обновление выполнено.', 'success');
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await loadUpdateStatus();
    } catch (error) {
      setNotice(`Ошибка обновления: ${error.message}`, 'error');
      tg?.HapticFeedback?.notificationOccurred?.('error');
    } finally {
      state.busy = '';
      renderShell();
    }
  }

  async function runRestart() {
    const ok = window.confirm('Клоун, ты уверен что это хочешь?');
    if (!ok) {
      return;
    }

    toggleActionBusy('restart');
    try {
      const payload = await request('/api/restart', 'POST');
      setNotice(payload.message || 'Перезапуск выполнен.', 'success');
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await loadUpdateStatus();
    } catch (error) {
      setNotice(`Ошибка перезапуска: ${error.message}`, 'error');
      tg?.HapticFeedback?.notificationOccurred?.('error');
    } finally {
      state.busy = '';
      renderShell();
    }
  }

  async function copyCurrentUrl() {
    if (!apiToken) {
      setNotice('Сначала откройте этот экран через /browser в Telegram-боте.', 'warning');
      return;
    }

    try {
      await navigator.clipboard.writeText(window.location.href);
      setNotice('Ссылка скопирована в буфер обмена.', 'success');
    } catch (error) {
      setNotice(`Не удалось скопировать ссылку: ${error.message}`, 'warning');
    }
  }

  function browserHelp() {
    setNotice('Для компьютера используйте /browser в Telegram-боте. Так Polaris откроется через token без initData.', 'warning');
  }

  function handleAction(action) {
    switch (action) {
      case 'open-search':
        openSearch();
        break;
      case 'check-update':
        checkUpdate();
        break;
      case 'run-update':
        runUpdate();
        break;
      case 'run-restart':
        runRestart();
        break;
      case 'copy-current-url':
        copyCurrentUrl();
        break;
      case 'browser-help':
        browserHelp();
        break;
      default:
        break;
    }
  }

  function executeSearchResult(resultId) {
    const item = searchCatalog().find((entry) => entry.id === resultId);
    if (!item) {
      return;
    }
    closeSearch();
    item.run();
  }

  async function bootstrap() {
    hydrateIcons(document);
    renderShell();

    try {
      const payload = await validateUser();
      state.authenticated = true;
      state.authMode = normalizeAuthMode(payload.data?.auth || state.authMode);
      state.user = payload.user || payload.data || null;
      state.authMessage = payload.message || 'Доступ разрешен.';
      setAuthBanner(
        `Доступ: ${state.authMode === 'browser' ? 'browser token' : 'Telegram initData'} · ${
          state.user?.display_name || state.user?.first_name || state.user?.username || 'admin'
        }`,
        'ok'
      );
      renderShell();
      await loadUpdateStatus();
    } catch (error) {
      state.authenticated = false;
      state.authMessage = error.message;
      setAuthBanner(`Нет доступа: ${error.message}`, 'err');
      renderShell();
    }
  }

  document.addEventListener('click', (event) => {
    const viewButton = event.target.closest('[data-view]');
    if (viewButton) {
      setView(viewButton.dataset.view);
      return;
    }

    const actionButton = event.target.closest('[data-action]');
    if (actionButton) {
      handleAction(actionButton.dataset.action);
      return;
    }

    const resultButton = event.target.closest('[data-result-id]');
    if (resultButton) {
      executeSearchResult(resultButton.dataset.resultId);
      return;
    }

    const queryButton = event.target.closest('[data-search-query]');
    if (queryButton) {
      state.searchQuery = queryButton.dataset.searchQuery || '';
      if (elements.searchInput) {
        elements.searchInput.value = state.searchQuery;
      }
      renderSearch();
    }
  });

    elements.drawerToggle?.addEventListener('click', toggleDrawer);
  elements.brandButton?.addEventListener('click', openSearch);
  elements.searchToggle?.addEventListener('click', openSearch);
  elements.searchCapsule?.addEventListener('click', openSearch);

  elements.backdrop?.addEventListener('click', () => {
    closeDrawer();
    closeSearch();
  });

  elements.searchInput?.addEventListener('input', (event) => {
    state.searchQuery = event.target.value || '';
    renderSearch();
  });

  elements.searchInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeSearch();
      return;
    }
    if (event.key === 'Enter') {
      const firstResult = elements.searchResults?.querySelector('[data-result-id]');
      if (firstResult) {
        executeSearchResult(firstResult.dataset.resultId);
      }
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (state.searchOpen) {
        closeSearch();
        return;
      }
      if (state.drawerOpen) {
        closeDrawer();
        return;
      }
    }

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openSearch();
      return;
    }

    if (
      event.key === '/' &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey &&
      document.activeElement !== elements.searchInput
    ) {
      event.preventDefault();
      openSearch();
    }
  });

  /* ─── Swipe-to-open drawer ─── */
  let swipeStartX = 0;
  let swipeStartY = 0;
  let swiping = false;
  const SWIPE_EDGE = 28;
  const SWIPE_THRESHOLD = 60;

  function isNearLeftEdge(x) {
    return x <= SWIPE_EDGE;
  }

  function isHorizontalSwipe(dx, dy) {
    return Math.abs(dx) > Math.abs(dy) * 1.2;
  }

  document.addEventListener('touchstart', (event) => {
    if (state.drawerOpen || state.searchOpen) return;
    const touch = event.touches[0];
    if (!isNearLeftEdge(touch.clientX)) return;
    swipeStartX = touch.clientX;
    swipeStartY = touch.clientY;
    swiping = true;
  }, { passive: true });

  document.addEventListener('touchmove', (event) => {
    if (!swiping || state.drawerOpen) return;
    const touch = event.touches[0];
    const dx = touch.clientX - swipeStartX;
    const dy = touch.clientY - swipeStartY;
    if (!isHorizontalSwipe(dx, dy)) {
      swiping = false;
      return;
    }
    if (dx > SWIPE_THRESHOLD) {
      openDrawer();
      swiping = false;
    }
  }, { passive: true });

  document.addEventListener('touchend', () => {
    swiping = false;
  });

  bootstrap();
})();
