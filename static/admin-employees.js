(() => {
  const panel = document.createElement('section');
  panel.className = 'admin-panel admin-tab-panel';
  panel.dataset.panel = 'employees';
  panel.innerHTML = '<h2>Сотрудники</h2><p class="admin-note">Участники появляются после обращения к боту. Запрет в локации не влияет на другие боты.</p><label class="telegram-toggle global-only"><input id="allEmployeeProfiles" type="checkbox">Общие профили всех локаций</label><div class="employee-tools"><input type="search" placeholder="ФИО, логин, @username или Telegram ID" aria-label="Поиск сотрудника"><button class="mini-button" type="button">Обновить</button></div><p role="status"></p><div class="employee-list"></div><div class="telegram-actions"><button class="mini-button" type="button">←</button><span></span><button class="mini-button" type="button">→</button></div>';
  document.querySelector('#adminContent').append(panel);
  const allProfiles = panel.querySelector('#allEmployeeProfiles');
  const search = panel.querySelector('input[type="search"]');
  const status = panel.querySelector('[role="status"]');
  const list = panel.querySelector('.employee-list');
  const [refresh, prev, next] = panel.querySelectorAll('button');
  const pager = prev.nextElementSibling;
  let page = 1;
  let sequence = 0;
  let timer;
  let regions = [];
  const createBlock = document.createElement('details');
  createBlock.className = 'employee-create';
  createBlock.classList.add('global-only');
  createBlock.append(node('summary', 'Добавить сотрудника'));
  const createForm = document.createElement('form');
  createForm.className = 'employee-create-form';
  const createLogin = field(createForm, 'Корпоративный логин', '');
  const createName = field(createForm, 'ФИО', '');
  const createTelegram = field(createForm, 'Telegram ID (необязательно)', '');
  createLogin.required = true;
  createName.required = true;
  createTelegram.inputMode = 'numeric';
  const createActions = node('div', '', 'telegram-actions');
  const createButton = node('button', 'Создать сотрудника', 'mini-button');
  createButton.type = 'submit';
  createActions.append(createButton);
  const createFeedback = node('p', '', 'telegram-feedback');
  createFeedback.setAttribute('role', 'status');
  createForm.append(createActions, createFeedback);
  createBlock.append(createForm, node('p', 'Telegram ID можно связать позже. Если пользователь уже появился автоматически, заполните его существующую карточку.', 'admin-note'));
  panel.querySelector('.employee-tools').before(createBlock);
  createForm.onsubmit = async (event) => {
    event.preventDefault();
    createButton.disabled = true;
    createFeedback.classList.remove('error');
    createFeedback.textContent = 'Создание…';
    try {
      const result = await request('', {method: 'POST', body: JSON.stringify({login: createLogin.value,
        full_name: createName.value, telegram_user_id: createTelegram.value.trim() || null})});
      createForm.reset();
      createFeedback.textContent = 'Создан: ' + result.item.display_name;
      search.value = result.item.login;
      allProfiles.checked = true;
      page = 1;
      await load();
    } catch (error) { createFeedback.classList.add('error'); createFeedback.textContent = error.message; }
    finally { createButton.disabled = false; }
  };

  function node(tag, text, className = '') {
    const item = document.createElement(tag);
    item.textContent = text;
    item.className = className;
    return item;
  }
  async function request(path, options = {}) {
    const response = await fetch('/api/admin/employees' + path, {cache: 'no-store', ...options,
      headers: {'Content-Type': 'application/json'}});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Ошибка запроса');
    return result;
  }
  function field(card, title, value, type = 'text') {
    const label = node('label', title, 'telegram-field');
    const input = document.createElement('input');
    input.type = type;
    input.value = value || '';
    label.append(input);
    card.append(label);
    return input;
  }
  function card(item) {
    const article = node('article', '', 'telegram-region-card');
    article.append(node('h3', item.display_name));
    const incomplete = node('span', 'Нужно заполнить логин и ФИО', 'employee-badge');
    incomplete.hidden = item.profile_complete !== false;
    article.append(incomplete);
    if (item.telegram_name || item.telegram_username) {
      article.append(node('p', 'Telegram: ' + [item.telegram_name, item.telegram_username ? '@' + item.telegram_username : ''].filter(Boolean).join(' · '), 'telegram-meta'));
    }
    if (item.last_seen_at_display) article.append(node('p', 'Последнее обращение: ' + item.last_seen_at_display, 'telegram-meta'));
    if (!allProfiles.checked) {
      const accessLabel = node('label', '', 'telegram-toggle');
      const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = Boolean(item.area_enabled);
      enabled.dataset.autoSave = 'true';
      enabled.disabled = Boolean(item.blocked || (item.allowed_areas !== null && !item.allowed_areas.includes(selectedAdminArea)));
      accessLabel.append(enabled, node('span', enabled.disabled ? 'Доступ ограничен общим администратором' : 'Разрешить действия в боте этой локации'));
      const feedback = node('p', '', 'telegram-feedback'); feedback.setAttribute('role', 'status');
      const areaId = selectedAdminArea;
      enabled.onchange = async () => {
        enabled.disabled = true;
        try {
          await request(`/${item.id}/areas/${encodeURIComponent(areaId)}`, {method:'PUT',body:JSON.stringify({enabled:enabled.checked})});
          feedback.textContent = 'Сохранено';
        } catch (error) { enabled.checked = !enabled.checked; feedback.textContent = error.message; }
        finally { enabled.disabled = false; }
      };
      article.append(accessLabel, feedback);
      if (currentAdmin?.role === 'location') return article;
      article.append(node('p','ФИО, логин и запрет изменения профиля общие для всех локаций.','admin-note'));
    }
    const login = field(article, 'Корпоративный логин', item.login);
    const name = field(article, 'ФИО', item.full_name);
    const telegram = field(article, 'Telegram ID (очистите для отвязки)', item.telegram_user_id);
    login.placeholder = 'Корпоративный логин';
    article.addEventListener('input', (event) => { if (!event.target.dataset.autoSave) article.dataset.dirty = 'true'; });
    const blockedLabel = node('label', '', 'telegram-toggle');
    const blocked = document.createElement('input');
    blocked.type = 'checkbox';
    blocked.checked = item.blocked;
    blockedLabel.append(blocked, node('span', 'Профиль заблокирован'));
    const lockedLabel = node('label', '', 'telegram-toggle');
    const locked = document.createElement('input');
    locked.type = 'checkbox';
    locked.checked = Boolean(item.profile_locked);
    lockedLabel.append(locked, node('span', 'Запретить изменение и удаление данных в боте'));
    const accessLabel = node('label', '', 'telegram-toggle');
    const all = document.createElement('input');
    all.type = 'checkbox';
    all.checked = item.allowed_areas === null;
    accessLabel.append(all, node('span', 'Доступ ко всем локациям'));
    const areas = node('div', '', 'employee-regions');
    const checks = regions.map((region) => {
      const label = node('label', '', 'telegram-toggle');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = item.allowed_areas?.includes(region.area_id) || false;
      label.append(input, node('span', region.display_name));
      areas.append(label);
      return {input, id: region.area_id};
    });
    all.onchange = () => { areas.hidden = all.checked; };
    all.onchange();
    const feedback = node('p', '', 'telegram-feedback');
    feedback.setAttribute('role', 'status');
    const controls = node('div', '', 'telegram-actions');
    const save = node('button', 'Сохранить', 'mini-button');
    save.type = 'button';
    save.onclick = async () => {
      save.disabled = true;
      feedback.classList.remove('error');
      feedback.textContent = 'Сохранение…';
      try {
        const result = await request('/' + item.id, {method: 'PUT', body: JSON.stringify({login: login.value,
          full_name: name.value, telegram_user_id: telegram.value || null, blocked: blocked.checked, profile_locked: locked.checked,
          allowed_areas: all.checked ? null : checks.filter(({input}) => input.checked).map(({id}) => id)})});
        article.querySelector('h3').textContent = result.item.display_name;
        login.value = result.item.login;
        name.value = result.item.full_name;
        telegram.value = result.item.telegram_user_id || '';
        incomplete.hidden = result.item.profile_complete !== false;
        delete article.dataset.dirty;
        feedback.textContent = 'Сохранено';
      } catch (error) { feedback.classList.add('error'); feedback.textContent = error.message; }
      finally { save.disabled = false; }
    };
    const audit = node('button', 'Журнал изменений', 'mini-button');
    audit.type = 'button';
    const journal = node('div', '', 'employee-audit');
    journal.hidden = true;
    audit.onclick = async () => {
      if (!journal.hidden) { journal.hidden = true; return; }
      audit.disabled = true;
      try {
        const result = await request('/' + item.id + '/audit');
        journal.replaceChildren(...result.items.map((entry) => node('p',
          `${entry.created_at_display} · ${entry.actor} · ${{profile_saved:'Профиль сохранён',discovered:'Обнаружен в Telegram',admin_created:'Создан администратором',admin_updated:'Изменён администратором'}[entry.action] || entry.action}\n${entry.details}`)));
        journal.hidden = false;
      } catch (error) { feedback.textContent = error.message; }
      finally { audit.disabled = false; }
    };
    controls.append(save, audit);
    article.append(blockedLabel, lockedLabel, accessLabel, areas, controls, feedback, journal);
    return article;
  }
  async function load(refreshRegions = false, silent = false) {
    const current = ++sequence;
    if (!selectedAdminArea && !allProfiles.checked) {
      list.replaceChildren(); status.textContent = 'Нет назначенных локаций'; return;
    }
    if (!silent) status.textContent = 'Загрузка…';
    try {
      const areaQuery = allProfiles.checked ? '' : `&area_id=${encodeURIComponent(selectedAdminArea)}`;
      const [result, regionItems] = await Promise.all([request(`?q=${encodeURIComponent(search.value)}&page=${page}${areaQuery}`),
        regions.length && !refreshRegions ? Promise.resolve(regions) : fetch('/api/admin/areas').then(async (response) => {
          if (!response.ok) throw new Error('Не удалось загрузить локации');
          return (await response.json()).items;
        })]);
      if (current !== sequence) return;
      regions = regionItems;
      if (silent && (list.querySelector('[data-dirty="true"]') || list.contains(document.activeElement))) return;
      list.replaceChildren(...result.items.map(card));
      status.textContent = `Сотрудников: ${result.total}`;
      pager.textContent = `${result.page} / ${result.total_pages}`;
      prev.disabled = page <= 1;
      next.disabled = page >= result.total_pages;
    } catch (error) { if (current === sequence) status.textContent = error.message; }
  }
  refresh.onclick = () => load(true);
  allProfiles.onchange = () => { page = 1; load(); };
  prev.onclick = () => { page--; load(); };
  next.onclick = () => { page++; load(); };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(); }, 300); };
  document.querySelector('[data-tab="employees"]').addEventListener('click', () => {
    if (!currentAdmin) return;
    document.querySelectorAll('.admin-tab-panel').forEach((item) => item.classList.toggle('active', item === panel));
    load();
  });
  window.addEventListener('admin-area-changed', () => {
    sequence++; page = 1; list.replaceChildren();
    if (panel.classList.contains('active')) load();
  });
  document.querySelectorAll('.admin-tab:not([data-tab="employees"])').forEach((tab) => tab.addEventListener('click', () => panel.classList.remove('active')));
  setInterval(() => {
    if (panel.classList.contains('active') && !document.hidden && !list.querySelector('[data-dirty="true"]') && !list.contains(document.activeElement)) load(false, true);
  }, 10000);
})();
