/* ─── Finance Module ───
   Контроль регулярных платежей и подписок. Finance не хранит расходы,
   доходы, счета, бюджеты — только регулярные обязательства (RecurringPayment).
   Работает с /api/finance. */
window.FinanceModule = (() => {
  'use strict';

  const ICONS = {
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L3 14h7.5l.5 5 7-11.5L13 2z"></path></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>',
    finance: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h10a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-4a3 3 0 0 1 3-3Z"></path><path d="M12 8v8M9 11.5c0-1 .9-1.5 2.3-1.5h1.4c1.4 0 2.3.5 2.3 1.5S14.1 13 12.8 13h-1.6C9.9 13 9 13.5 9 14.5s.9 1.5 2.3 1.5h1.4c1.4 0 2.3-.5 2.3-1.5"></path></svg>',
  };

  const BILLING_LABELS = {
    weekly: 'Каждую неделю',
    monthly: 'Каждый месяц',
    quarterly: 'Каждый квартал',
    yearly: 'Каждый год',
    custom: 'Другой период',
  };

  const STATUS_LABELS = {
    active: 'Активен',
    paused: 'На паузе',
    cancelled: 'Отменён',
    expired: 'Истёк',
  };

  const STATUS_FILTERS = [
    { value: '', label: 'Все' },
    { value: 'active', label: 'Активные' },
    { value: 'paused', label: 'На паузе' },
    { value: 'cancelled', label: 'Отменённые' },
    { value: 'expired', label: 'Истёкшие' },
  ];

  const CURRENCY_SYMBOLS = { EUR: '€', USD: '$', RUB: '₽', GBP: '£' };

  /* ─── State ─── */
  let payments = [];
  let upcoming = [];
  let summary = null;
  let categories = [];
  let loading = false;
  let error = '';

  let filterStatus = '';
  let filterCategory = '';
  let searchQuery = '';
  let sortBy = 'next_payment_date';

  let modalOpen = false;
  let modalMode = 'create'; // "create" | "edit"
  let modalSaving = false;
  let modalError = '';
  let modalForm = {};

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
      const name = node.dataset.icon || 'finance';
      node.innerHTML = ICONS[name] || '';
    });
  }

  function currencySymbol(cur) {
    return CURRENCY_SYMBOLS[cur] || (cur ? cur + ' ' : '');
  }

  function formatAmount(amount, currency) {
    const symbol = CURRENCY_SYMBOLS[currency];
    const num = Number(amount).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
    return symbol ? `${symbol}${num}` : `${num} ${currency}`;
  }

  function formatDateShort(dateStr) {
    if (!dateStr) return '—';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    const months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    return `${d.getDate()} ${months[d.getMonth()]}`;
  }

  function formatDaysUntil(days) {
    if (days === null || days === undefined) return '';
    if (days < 0) return `просрочен на ${Math.abs(days)} дн.`;
    if (days === 0) return 'сегодня';
    if (days === 1) return 'завтра';
    return `через ${days} дн.`;
  }

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  /* ─── API ─── */
  function requestHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) h['X-Telegram-Init-Data'] = initData;
    const token = localStorage.getItem('polaris_update_token') || '';
    if (token) h['X-Polaris-Token'] = token;
    return h;
  }

  async function apiRequest(path, method = 'GET', body = undefined) {
    const opts = { method, headers: requestHeaders() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const response = await fetch(path, opts);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    }
    return data;
  }

  /* ─── Data loading ─── */
  async function loadAll() {
    loading = true;
    error = '';
    render();

    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set('status', filterStatus);
      if (filterCategory) params.set('category', filterCategory);
      if (searchQuery) params.set('search', searchQuery);
      params.set('sort', sortBy);

      const [paymentsResp, upcomingResp, summaryResp, categoriesResp] = await Promise.all([
        apiRequest(`/api/finance/payments?${params.toString()}`),
        apiRequest('/api/finance/upcoming?limit=5'),
        apiRequest('/api/finance/summary'),
        apiRequest('/api/finance/categories'),
      ]);

      payments = paymentsResp.data?.payments || [];
      upcoming = upcomingResp.data?.payments || [];
      summary = summaryResp.data || null;
      categories = categoriesResp.data?.categories || [];
    } catch (e) {
      error = e.message;
    }

    loading = false;
    render();
  }

  async function reloadList() {
    // Более лёгкая перезагрузка — только список + сводка (после фильтра/поиска)
    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set('status', filterStatus);
      if (filterCategory) params.set('category', filterCategory);
      if (searchQuery) params.set('search', searchQuery);
      params.set('sort', sortBy);

      const resp = await apiRequest(`/api/finance/payments?${params.toString()}`);
      payments = resp.data?.payments || [];
    } catch (e) {
      error = e.message;
    }
    render();
  }

  /* ─── Rendering ─── */
  function render() {
    const container = document.getElementById('finance-container');
    if (!container) return;

    container.innerHTML = '';
    const fragment = document.createRange().createContextualFragment(renderPage());
    container.appendChild(fragment);
    hydrateIcons(container);
    renderModal();
  }

  function renderPage() {
    if (loading && !summary) {
      return `
        <div class="finance-wrapper">
          ${renderHeader()}
          <div class="card finance-card span-12"><p class="metric-subtext">Загружаю финансы…</p></div>
        </div>
      `;
    }

    if (error) {
      return `
        <div class="finance-wrapper">
          ${renderHeader()}
          <div class="card finance-card span-12">
            <div class="calendar-error">
              <span class="icon" data-icon="alert" aria-hidden="true"></span>
              <p>${escapeHTML(error)}</p>
            </div>
          </div>
        </div>
      `;
    }

    return `
      <div class="finance-wrapper">
        ${renderHeader()}
        ${renderSummary()}
        ${renderUpcoming()}
        ${renderAllPayments()}
      </div>
    `;
  }

  function renderHeader() {
    return `
      <div class="calendar-header">
        <h1>Finance</h1>
        <button class="calendar-add-button" type="button" data-action="open-add-payment">
          <span class="icon" data-icon="plus" aria-hidden="true"></span>
          <span>Добавить платёж</span>
        </button>
      </div>
    `;
  }

  function renderSummary() {
    if (!summary) return '';

    const cards = [];

    cards.push(`
      <article class="card metric-card span-4">
        <h3>Активные платежи</h3>
        <div class="metric">
          <span class="metric-value">${summary.active_count}</span>
          <p class="metric-subtext">регулярных обязательств отслеживается</p>
        </div>
      </article>
    `);

    if (summary.next_payment) {
      const np = summary.next_payment;
      cards.push(`
        <article class="card metric-card span-4">
          <h3>Ближайший платёж</h3>
          <div class="metric">
            <span class="metric-value">${escapeHTML(np.name)} — ${formatAmount(np.amount, np.currency)}</span>
            <p class="metric-subtext">${formatDateShort(np.next_payment_date)} · ${formatDaysUntil(np.days_until)}</p>
          </div>
        </article>
      `);
    } else {
      cards.push(`
        <article class="card metric-card span-4">
          <h3>Ближайший платёж</h3>
          <div class="metric">
            <p class="metric-subtext">Нет активных платежей</p>
          </div>
        </article>
      `);
    }

    if (summary.totals_by_currency && summary.totals_by_currency.length) {
      const lines = summary.totals_by_currency.map((t) =>
        `<span class="metric-value" style="font-size:0.95rem">${formatAmount(t.monthly_total, t.currency)} / мес · ${formatAmount(t.yearly_total, t.currency)} / год</span>`
      ).join('');
      cards.push(`
        <article class="card metric-card span-4">
          <h3>Сумма обязательств</h3>
          <div class="metric">
            ${lines}
          </div>
        </article>
      `);
    }

    return `<div class="grid finance-summary-grid">${cards.join('')}</div>`;
  }

  function renderUpcoming() {
    if (!upcoming.length) return '';

    const items = upcoming.map((p) => `
      <div class="finance-upcoming-item" data-action="open-payment" data-payment-id="${escapeHTML(p.id)}">
        <div class="finance-upcoming-main">
          <span class="finance-upcoming-name">${escapeHTML(p.name)}</span>
          <span class="finance-upcoming-category">${escapeHTML(p.category)}</span>
        </div>
        <div class="finance-upcoming-meta">
          <span class="finance-upcoming-amount">${formatAmount(p.amount, p.currency)}</span>
          <span class="finance-upcoming-date">${formatDateShort(p.next_payment_date)} · ${formatDaysUntil(p.days_until)}</span>
        </div>
      </div>
    `).join('');

    return `
      <div class="card finance-card span-12">
        <h3 class="finance-section-title">Ближайшие платежи</h3>
        <div class="finance-upcoming-list">${items}</div>
      </div>
    `;
  }

  function renderAllPayments() {
    const statusChips = STATUS_FILTERS.map((s) => `
      <button class="finance-chip ${filterStatus === s.value ? 'active' : ''}" type="button" data-action="filter-status" data-value="${s.value}">${s.label}</button>
    `).join('');

    const categoryOptions = ['<option value="">Все категории</option>']
      .concat(categories.map((c) => `<option value="${escapeHTML(c)}" ${filterCategory === c ? 'selected' : ''}>${escapeHTML(c)}</option>`))
      .join('');

    const list = payments.length === 0
      ? `<p class="metric-subtext" style="padding:12px 0">Платежи не найдены.</p>`
      : payments.map((p) => renderPaymentRow(p)).join('');

    return `
      <div class="card finance-card span-12">
        <h3 class="finance-section-title">Все платежи</h3>
        <div class="finance-filters">
          <div class="finance-chip-row">${statusChips}</div>
          <div class="finance-filters-row">
            <select class="finance-select" data-action="filter-category">${categoryOptions}</select>
            <select class="finance-select" data-action="sort-payments">
              <option value="next_payment_date" ${sortBy === 'next_payment_date' ? 'selected' : ''}>По дате</option>
              <option value="name" ${sortBy === 'name' ? 'selected' : ''}>По названию</option>
              <option value="amount" ${sortBy === 'amount' ? 'selected' : ''}>По сумме</option>
            </select>
            <label class="finance-search">
              <span class="icon" data-icon="search" aria-hidden="true"></span>
              <input type="search" data-action="search-payments" placeholder="Поиск…" value="${escapeHTML(searchQuery)}" />
            </label>
          </div>
        </div>
        <div class="finance-list">${list}</div>
      </div>
    `;
  }

  function renderPaymentRow(p) {
    const overdue = p.status === 'active' && p.days_until !== null && p.days_until < 0;
    return `
      <div class="finance-row" data-action="open-payment" data-payment-id="${escapeHTML(p.id)}">
        <div class="finance-row-main">
          <span class="finance-row-name">${escapeHTML(p.name)}</span>
          <span class="finance-status-badge status-${escapeHTML(p.status)}">${STATUS_LABELS[p.status] || p.status}</span>
        </div>
        <div class="finance-row-meta">
          <span class="finance-row-category">${escapeHTML(p.category)}</span>
          <span class="finance-row-period">${BILLING_LABELS[p.billing_period] || p.billing_period}</span>
        </div>
        <div class="finance-row-amount">
          <span class="finance-row-amount-value">${formatAmount(p.amount, p.currency)}</span>
          <span class="finance-row-date${overdue ? ' overdue' : ''}">${formatDateShort(p.next_payment_date)}${p.status === 'active' ? ` · ${formatDaysUntil(p.days_until)}` : ''}</span>
        </div>
      </div>
    `;
  }

  /* ─── Modal (create / edit) ─── */
  function defaultForm() {
    return {
      id: '',
      name: '',
      description: '',
      amount: '',
      currency: 'EUR',
      billing_period: 'monthly',
      custom_interval_days: '',
      next_payment_date: todayStr(),
      end_date: '',
      category: 'Subscriptions',
      status: 'active',
    };
  }

  function openCreateModal() {
    modalMode = 'create';
    modalForm = defaultForm();
    modalError = '';
    modalOpen = true;
    renderModal();
  }

  function openEditModal(paymentId) {
    const p = payments.find((x) => x.id === paymentId) || upcoming.find((x) => x.id === paymentId);
    if (!p) return;
    modalMode = 'edit';
    modalForm = {
      id: p.id,
      name: p.name,
      description: p.description || '',
      amount: p.amount,
      currency: p.currency,
      billing_period: p.billing_period,
      custom_interval_days: p.custom_interval_days || '',
      next_payment_date: p.next_payment_date,
      end_date: p.end_date || '',
      category: p.category,
      status: p.status,
    };
    modalError = '';
    modalOpen = true;
    renderModal();
  }

  function closeModal() {
    modalOpen = false;
    modalError = '';
    renderModal();
  }

  function readModalFields() {
    const overlay = document.querySelector('.finance-modal-overlay');
    if (!overlay) return null;
    return {
      name: overlay.querySelector('#finance-modal-name')?.value.trim() || '',
      description: overlay.querySelector('#finance-modal-description')?.value.trim() || '',
      amount: overlay.querySelector('#finance-modal-amount')?.value || '',
      currency: overlay.querySelector('#finance-modal-currency')?.value.trim() || 'EUR',
      billing_period: overlay.querySelector('#finance-modal-period')?.value || 'monthly',
      custom_interval_days: overlay.querySelector('#finance-modal-custom-days')?.value || '',
      next_payment_date: overlay.querySelector('#finance-modal-date')?.value || '',
      end_date: overlay.querySelector('#finance-modal-end-date')?.value || '',
      category: overlay.querySelector('#finance-modal-category')?.value.trim() || 'Other',
      status: overlay.querySelector('#finance-modal-status')?.value || 'active',
    };
  }

  async function submitModal() {
    const fields = readModalFields();
    if (!fields) return;

    if (!fields.name) { modalError = 'Укажите название'; renderModal(); return; }
    if (!fields.amount || Number(fields.amount) < 0) { modalError = 'Укажите сумму'; renderModal(); return; }
    if (!fields.next_payment_date) { modalError = 'Укажите дату следующего платежа'; renderModal(); return; }
    if (fields.billing_period === 'custom' && !fields.custom_interval_days) {
      modalError = 'Укажите интервал в днях для произвольного периода';
      renderModal();
      return;
    }

    const payload = {
      name: fields.name,
      description: fields.description,
      amount: Number(fields.amount),
      currency: fields.currency || 'EUR',
      billing_period: fields.billing_period,
      custom_interval_days: fields.billing_period === 'custom' ? Number(fields.custom_interval_days) : null,
      next_payment_date: fields.next_payment_date,
      end_date: fields.end_date || null,
      category: fields.category || 'Other',
    };

    if (modalMode === 'edit') {
      payload.status = fields.status;
    }

    modalSaving = true;
    modalError = '';
    renderModal();

    try {
      if (modalMode === 'create') {
        await apiRequest('/api/finance/payments', 'POST', payload);
      } else {
        await apiRequest(`/api/finance/payments/${modalForm.id}`, 'PATCH', payload);
      }
      modalSaving = false;
      modalOpen = false;
      renderModal();
      loadAll();
    } catch (e) {
      modalSaving = false;
      modalError = e.message;
      renderModal();
    }
  }

  async function deleteModalPayment() {
    if (!modalForm.id) return;
    if (!window.confirm('Удалить этот платёж? Действие необратимо.')) return;

    modalSaving = true;
    renderModal();
    try {
      await apiRequest(`/api/finance/payments/${modalForm.id}`, 'DELETE');
      modalSaving = false;
      modalOpen = false;
      renderModal();
      loadAll();
    } catch (e) {
      modalSaving = false;
      modalError = e.message;
      renderModal();
    }
  }

  function renderModal() {
    const existing = document.querySelector('.finance-modal-overlay');
    if (existing) existing.remove();
    if (!modalOpen) return;

    const f = modalForm;
    const periodOptions = Object.entries(BILLING_LABELS)
      .map(([val, label]) => `<option value="${val}" ${f.billing_period === val ? 'selected' : ''}>${label}</option>`)
      .join('');

    const categoryList = categories.length
      ? `<datalist id="finance-category-options">${categories.map((c) => `<option value="${escapeHTML(c)}"></option>`).join('')}</datalist>`
      : '';

    const statusOptions = Object.entries(STATUS_LABELS)
      .map(([val, label]) => `<option value="${val}" ${f.status === val ? 'selected' : ''}>${label}</option>`)
      .join('');

    const overlay = document.createElement('div');
    overlay.className = 'create-modal-overlay finance-modal-overlay';
    overlay.innerHTML = `
      <div class="create-modal" role="dialog" aria-modal="true" aria-label="${modalMode === 'create' ? 'Новый платёж' : 'Платёж'}">
        <div class="create-modal-header">
          <h2>${modalMode === 'create' ? 'Новый регулярный платёж' : 'Редактировать платёж'}</h2>
        </div>

        <div class="detail-field">
          <label>Название</label>
          <input type="text" id="finance-modal-name" class="create-modal-input" style="min-height:40px" placeholder="Например, Netflix" value="${escapeHTML(f.name)}" ${modalSaving ? 'disabled' : ''} autofocus />
        </div>

        <div style="display:grid;grid-template-columns:2fr 1fr;gap:8px;margin-top:10px">
          <div class="detail-field">
            <label>Сумма</label>
            <input type="number" step="0.01" min="0" id="finance-modal-amount" value="${escapeHTML(String(f.amount))}" ${modalSaving ? 'disabled' : ''} />
          </div>
          <div class="detail-field">
            <label>Валюта</label>
            <input type="text" id="finance-modal-currency" maxlength="8" value="${escapeHTML(f.currency)}" placeholder="EUR" ${modalSaving ? 'disabled' : ''} />
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px">
          <div class="detail-field">
            <label>Период</label>
            <select id="finance-modal-period" ${modalSaving ? 'disabled' : ''}>${periodOptions}</select>
          </div>
          <div class="detail-field" id="finance-modal-custom-days-wrap" style="${f.billing_period === 'custom' ? '' : 'opacity:0.4'}">
            <label>Интервал (дней)</label>
            <input type="number" min="1" id="finance-modal-custom-days" value="${escapeHTML(String(f.custom_interval_days || ''))}" ${f.billing_period === 'custom' ? '' : 'disabled'} />
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px">
          <div class="detail-field">
            <label>Следующий платёж</label>
            <input type="date" id="finance-modal-date" value="${escapeHTML(f.next_payment_date)}" ${modalSaving ? 'disabled' : ''} />
          </div>
          <div class="detail-field">
            <label>Окончание (необязательно)</label>
            <input type="date" id="finance-modal-end-date" value="${escapeHTML(f.end_date || '')}" ${modalSaving ? 'disabled' : ''} />
          </div>
        </div>

        <div style="display:grid;grid-template-columns:${modalMode === 'edit' ? '1fr 1fr' : '1fr'};gap:8px;margin-top:10px">
          <div class="detail-field">
            <label>Категория</label>
            <input type="text" id="finance-modal-category" list="finance-category-options" value="${escapeHTML(f.category)}" ${modalSaving ? 'disabled' : ''} />
            ${categoryList}
          </div>
          ${modalMode === 'edit' ? `
          <div class="detail-field">
            <label>Статус</label>
            <select id="finance-modal-status" ${modalSaving ? 'disabled' : ''}>${statusOptions}</select>
          </div>` : ''}
        </div>

        <div class="detail-field" style="margin-top:10px">
          <label>Описание</label>
          <textarea id="finance-modal-description" class="create-modal-input" rows="2" placeholder="Необязательно" ${modalSaving ? 'disabled' : ''}>${escapeHTML(f.description || '')}</textarea>
        </div>

        ${modalError ? `<p class="create-modal-hint" style="color:var(--error)">${escapeHTML(modalError)}</p>` : ''}

        <div class="create-modal-actions" style="justify-content:${modalMode === 'edit' ? 'space-between' : 'flex-end'}">
          ${modalMode === 'edit' ? `<button class="small-button ghost" type="button" data-action="delete-payment" style="color:var(--error);border-color:rgba(255,93,115,0.3)" ${modalSaving ? 'disabled' : ''}>Удалить</button>` : ''}
          <div style="display:flex;gap:8px">
            <button class="small-button ghost" type="button" data-action="close-add-payment" ${modalSaving ? 'disabled' : ''}>Отмена</button>
            <button class="small-button primary" type="button" data-action="submit-add-payment" ${modalSaving ? 'disabled' : ''}>${modalSaving ? 'Сохраняю…' : 'Сохранить'}</button>
          </div>
        </div>
      </div>
    `;

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) { closeModal(); return; }
      const btn = event.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;
      if (action === 'close-add-payment') { event.preventDefault(); closeModal(); return; }
      if (action === 'submit-add-payment') { event.preventDefault(); submitModal(); return; }
      if (action === 'delete-payment') { event.preventDefault(); deleteModalPayment(); return; }
    });

    overlay.addEventListener('change', (event) => {
      if (event.target.id === 'finance-modal-period') {
        const wrap = overlay.querySelector('#finance-modal-custom-days-wrap');
        const input = overlay.querySelector('#finance-modal-custom-days');
        const isCustom = event.target.value === 'custom';
        if (wrap) wrap.style.opacity = isCustom ? '1' : '0.4';
        if (input) input.disabled = !isCustom;
      }
    });

    document.body.appendChild(overlay);
  }

  /* ─── Handlers ─── */
  function handleFilterStatus(value) {
    filterStatus = value;
    reloadList();
  }

  function handleFilterCategory(value) {
    filterCategory = value;
    reloadList();
  }

  function handleSort(value) {
    sortBy = value;
    reloadList();
  }

  let searchDebounce = null;
  function handleSearch(value) {
    searchQuery = value;
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => reloadList(), 300);
  }

  /* ─── Init ─── */
  function init(containerId) {
    const container = document.getElementById(containerId);
    if (!container) {
      console.warn('[FinanceModule] Container not found:', containerId);
      return;
    }

    container.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;

      if (action === 'open-add-payment') { event.preventDefault(); openCreateModal(); return; }
      if (action === 'filter-status') { event.preventDefault(); handleFilterStatus(btn.dataset.value); return; }

      const row = event.target.closest('[data-action="open-payment"]');
      if (row) { event.preventDefault(); openEditModal(row.dataset.paymentId); return; }
    });

    container.addEventListener('change', (event) => {
      if (event.target.dataset.action === 'filter-category') { handleFilterCategory(event.target.value); }
      if (event.target.dataset.action === 'sort-payments') { handleSort(event.target.value); }
    });

    container.addEventListener('input', (event) => {
      if (event.target.dataset.action === 'search-payments') { handleSearch(event.target.value); }
    });

    filterStatus = '';
    filterCategory = '';
    searchQuery = '';
    sortBy = 'next_payment_date';

    loadAll();
  }

  /* ─── Public API ─── */
  return {
    init,
    reload: loadAll,
  };
})();
