const adminAreaList = document.querySelector('#adminAreaList');
const adminRefreshButton = document.querySelector('#adminRefreshButton');
const soundFileInput = document.querySelector('#soundFileInput');
const soundUploadButton = document.querySelector('#soundUploadButton');
const soundLibraryList = document.querySelector('#soundLibraryList');
const soundSearchInput = document.querySelector('#soundSearchInput');
const soundPrevButton = document.querySelector('#soundPrevButton');
const soundNextButton = document.querySelector('#soundNextButton');
const soundPageInfo = document.querySelector('#soundPageInfo');
const adminLogsRefreshButton = document.querySelector('#adminLogsRefreshButton');
const adminLogList = document.querySelector('#adminLogList');
const adminTabs = document.querySelectorAll('.admin-tab');
adminTabs.forEach((tab) => { tab.disabled = true; });
const adminTabPanels = document.querySelectorAll('.admin-tab-panel');
const ownerOnlyElements = document.querySelectorAll('.owner-only');
const adminUsersRefreshButton = document.querySelector('#adminUsersRefreshButton');
const adminLoginLogsRefreshButton = document.querySelector('#adminLoginLogsRefreshButton');
const adminUserList = document.querySelector('#adminUserList');
const adminLoginLogList = document.querySelector('#adminLoginLogList');
const newAdminUsername = document.querySelector('#newAdminUsername');
const newAdminPassword = document.querySelector('#newAdminPassword');
const createAdminUserButton = document.querySelector('#createAdminUserButton');
const ownAdminPassword = document.querySelector('#ownAdminPassword');
const changeOwnPasswordButton = document.querySelector('#changeOwnPasswordButton');
const ownPasswordStatus = document.querySelector('#ownPasswordStatus');
const profilePasswordNote = document.querySelector('#profilePasswordNote');
let adminCsrfToken = '';
const nativeAdminFetch = window.fetch.bind(window);
window.fetch = async (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
  const adminApi = url.origin === location.origin && url.pathname.startsWith('/api/admin/');
  if (adminApi && !['GET','HEAD'].includes((options.method || 'GET').toUpperCase())) {
    const headers = new Headers(options.headers);
    if (adminCsrfToken) headers.set('X-CSRF-Token', adminCsrfToken);
    options = {...options,headers};
  }
  const response = await nativeAdminFetch(input,options);
  if (adminApi && response.status === 401) location.replace('/admin/login');
  return response;
};

let soundItems = [];
let soundPage = 1;
let currentAdmin = null;
let adminAreas = [];
let selectedAdminArea = '';
const adminRoleNames = {owner: 'Главный администратор', admin: 'Общий администратор', location: 'Администратор локаций'};
const locationSelect = document.querySelector('#adminLocationSelect');
const newAdminRole = document.querySelector('#newAdminRole');
const newAdminAreas = document.querySelector('#newAdminAreas');
const loadedAdminTabs = new Set(['areas']);

function adminNotice(message, error = false) {
  const status = document.querySelector('#adminFeedback');
  status.textContent = message;
  status.classList.toggle('error', error);
}

function describeAdminAction(item) {
  const path = item.target;
  if (path.includes('/me/password')) return 'Изменён свой пароль';
  if (path.includes('/password')) return 'Изменён пароль администратора';
  if (path.includes('/access')) return 'Изменены роль или локации администратора';
  if (path.includes('/active')) return 'Изменён статус администратора';
  if (path.includes('/users')) return item.action === 'DELETE' ? 'Удалён администратор' : 'Создан администратор';
  if (path.includes('/employees')) return 'Изменён сотрудник или его доступ';
  if (path.includes('/sync-history')) return 'Запущена синхронизация истории';
  if (path.includes('/areas')) return 'Сохранены настройки локации';
  if (path.includes('/telegram')) return 'Настройки или проверка Telegram';
  if (path.includes('/sounds')) return item.action === 'DELETE' ? 'Удалён звук' : 'Загружен звук';
  return item.action;
}

function adminCheckedAreas(container) {
  return [...container.querySelectorAll('input:checked')].map((input) => input.value);
}

function adminAreaChecks(container, selected = []) {
  container.replaceChildren();
  adminAreas.forEach((area) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox'; input.value = area.area_id; input.checked = selected.includes(area.area_id);
    label.append(input, document.createTextNode(area.display_name)); container.append(label);
  });
}

async function adminRequest(url, options = {}) {
  const response = await fetch(url, {...options, cache: 'no-store'});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Не удалось выполнить запрос');
  return payload;
}
let defaultAckButtonText = 'ФА';
let defaultSyncHistoryHours = 24;
let defaultSoundPlaySeconds = 25;
let defaultSoundName = 'общий звук';
const soundPageSize = 6;

function setActiveTab(tabName) {
  if (currentAdmin?.role === 'location' && ['users','settings'].includes(tabName)) return;
  adminTabs.forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === tabName);
  });
  adminTabPanels.forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.panel === tabName);
  });
  document.querySelector('[data-panel="employees"]')?.classList.toggle('active', tabName === 'employees');
  if (tabName === 'logs' && loadedAdminTabs.has(tabName)) loadAdminLogs();
  if (!loadedAdminTabs.has(tabName)) {
    loadedAdminTabs.add(tabName);
    const load = {settings: loadGlobalSettings, sounds: loadSounds, logs: loadAdminLogs, users: async () => { await loadAdminUsers(); await loadLoginLogs(); }}[tabName];
    if (load) load().catch((error) => { loadedAdminTabs.delete(tabName); adminNotice(error.message, true); });
  }
}

async function loadCurrentAdmin() {
  const response = await fetch('/api/admin/me');
  const payload = await response.json();
  currentAdmin = payload.user || null;
  adminCsrfToken = payload.csrf_token || '';
  const isOwner = currentAdmin && currentAdmin.role === 'owner';
  document.querySelector('#adminIdentity').textContent = `${currentAdmin.username} · ${adminRoleNames[currentAdmin.role] || ''}`;
  document.querySelectorAll('.global-only').forEach((element) => element.classList.toggle('hidden', currentAdmin.role === 'location'));
  newAdminRole.querySelector('[value="admin"]').hidden = !isOwner;
  ownerOnlyElements.forEach((element) => {
    element.classList.toggle('hidden', !isOwner);
  });
  if (currentAdmin.role === 'location' && document.querySelector('.admin-tab.active')?.dataset.tab === 'users') {
    setActiveTab('areas');
  }
  if (isOwner) {
    profilePasswordNote.textContent = 'Пароль главного администратора задаётся в secret.env.';
    ownAdminPassword.disabled = true; changeOwnPasswordButton.disabled = true;
  }
}

async function loadClientConfig() {
  const response = await fetch('/api/client-config');
  const payload = await response.json();
  defaultAckButtonText = payload.ack_button_text || 'ФА';
  defaultSyncHistoryHours = Number(payload.sync_history_hours) >= 0 ? Number(payload.sync_history_hours) : 24;
  defaultSoundPlaySeconds = Number(payload.sound_play_seconds) > 0 ? Number(payload.sound_play_seconds) : 25;
  defaultSoundName = payload.sound_url ? decodeURIComponent(payload.sound_url.split('/').pop()) : 'общий звук';
}

function filteredSounds() {
  const query = soundSearchInput.value.trim().toLowerCase();
  if (!query) {
    return soundItems;
  }
  return soundItems.filter((sound) => `${sound.name} ${sound.path}`.toLowerCase().includes(query));
}

function updateSoundPager(totalItems) {
  const totalPages = Math.max(1, Math.ceil(totalItems / soundPageSize));
  soundPage = Math.min(Math.max(1, soundPage), totalPages);
  soundPageInfo.textContent = `${soundPage} / ${totalPages}`;
  soundPrevButton.disabled = soundPage <= 1;
  soundNextButton.disabled = soundPage >= totalPages;
}

function formatButtonAvailability(area) {
  const total = Number(area.button_count) || 0;
  const active = Number.isFinite(Number(area.active_button_count)) ? Number(area.active_button_count) : total;
  return `${active}/${total}`;
}

async function loadSounds() {
  const response = await fetch('/api/admin/sounds');
  const payload = await response.json();
  soundItems = payload.items || [];
  renderSoundLibrary();
  document.querySelectorAll('[data-sound-select]').forEach((select) => {
    const value = select.value;
    const replacement = createSoundSelect(value);
    select.replaceChildren(...replacement.childNodes);
    select.value = value;
  });
}

function renderSoundLibrary() {
  soundLibraryList.innerHTML = '';
  const sounds = filteredSounds();
  updateSoundPager(sounds.length);

  if (!soundItems.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Звуки пока не загружены';
    soundLibraryList.appendChild(empty);
    return;
  }

  if (!sounds.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'По этому поиску ничего не найдено';
    soundLibraryList.appendChild(empty);
    return;
  }

  const pageItems = sounds.slice((soundPage - 1) * soundPageSize, soundPage * soundPageSize);
  pageItems.forEach((sound) => {
    const row = document.createElement('div');
    row.className = 'sound-library-item';
    const heading = document.createElement('div');
    heading.className = 'sound-card-heading';
    const icon = document.createElement('span');
    icon.className = 'sound-file-icon'; icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M9 18V5l12-2v13M9 9l12-2"/><ellipse cx="6" cy="18" rx="3" ry="3"/><ellipse cx="18" cy="16" rx="3" ry="3"/></svg>';
    const info = document.createElement('div'); info.className = 'sound-file-info';
    const name = document.createElement('div');
    name.className = 'admin-area-title';
    name.textContent = sound.name;
    name.title = sound.name;
    const meta = document.createElement('div');
    meta.className = 'admin-area-meta';
    meta.textContent = `${sound.name.split('.').pop().toUpperCase()} · ${Math.ceil((sound.size || 0) / 1024)} КБ`;
    info.append(name, meta); heading.append(icon, info);
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.src = sound.url;
    audio.preload = 'none';
    audio.setAttribute('aria-label', `Прослушать ${sound.name}`);
    audio.addEventListener('play', () => {
      soundLibraryList.querySelectorAll('audio').forEach((other) => { if (other !== audio) other.pause(); });
    });

    const actions = document.createElement('div');
    actions.className = 'sound-actions';
    const deleteButton = document.createElement('button');
    deleteButton.className = 'mini-button danger-button';
    deleteButton.type = 'button';
    deleteButton.textContent = 'Удалить';
    deleteButton.addEventListener('click', async () => {
      if (!confirm(`Удалить звук "${sound.name}"?`)) {
        return;
      }
      const response = await fetch(`/api/admin/sounds/${encodeURIComponent(sound.name)}`, { method: 'DELETE' });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        alert(payload.detail || 'Не удалось удалить звук');
        return;
      }
      await loadSounds();
    });
    deleteButton.hidden = currentAdmin?.role === 'location';
    actions.append(deleteButton);

    row.append(heading, audio);
    if (currentAdmin?.role !== 'location') row.append(actions);
    soundLibraryList.appendChild(row);
  });
}

async function uploadSound() {
  const file = soundFileInput.files && soundFileInput.files[0];
  if (!file) {
    return;
  }
  soundUploadButton.disabled = true;
  try {
  const response = await fetch(`/api/admin/sounds/${encodeURIComponent(file.name)}?area_id=${encodeURIComponent(selectedAdminArea)}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || 'Не удалось загрузить звук');
  }
  soundFileInput.value = '';
  await loadSounds();
  adminNotice('Звук загружен. Выберите его в настройках локации.');
  } catch (error) { adminNotice(error.message, true); }
  finally { soundUploadButton.disabled = false; }
}

async function loadAdminAreas() {
  const payload = await adminRequest('/api/admin/areas');
  adminAreas = payload.items || [];
  const previousChecks = adminCheckedAreas(newAdminAreas);
  adminAreaChecks(newAdminAreas, previousChecks);
  if (!adminAreas.some((area) => area.area_id === selectedAdminArea)) selectedAdminArea = adminAreas[0]?.area_id || '';
  locationSelect.replaceChildren(...adminAreas.map((area) => new Option(area.display_name, area.area_id)));
  locationSelect.value = selectedAdminArea;
  renderSelectedAdminArea();
  window.dispatchEvent(new Event('admin-areas-loaded'));
}

function renderSelectedAdminArea() {
  const area = adminAreas.find((item) => item.area_id === selectedAdminArea);
  document.querySelector('#adminLocationTitle').textContent = area?.display_name || 'Локации не назначены';
  renderAdminAreas(area ? [area] : []);
}

async function loadAdminLogs() {
  return loadAdminJournal();
}

async function loadAdminUsers() {
  if (!currentAdmin || currentAdmin.role === 'location') {
    return;
  }
  const response = await fetch('/api/admin/users');
  if (!response.ok) {
    renderAdminUsers([]);
    return;
  }
  const payload = await response.json();
  renderAdminUsers(payload.items || []);
}

async function loadLoginLogs() {
  if (!currentAdmin || currentAdmin.role === 'location') {
    return;
  }
  const response = await fetch('/api/admin/login-logs?limit=200');
  if (!response.ok) {
    renderLoginLogs([]);
    return;
  }
  const payload = await response.json();
  renderLoginLogs(payload.items || []);
}

function renderAdminUsers(items) {
  adminUserList.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Пользователей пока нет';
    adminUserList.appendChild(empty);
    return;
  }

  items.forEach((user) => {
    const row = document.createElement('div');
    row.className = 'admin-user-card';
    const title = document.createElement('div');
    title.className = 'admin-area-title';
    title.textContent = user.username;
    const meta = document.createElement('div');
    meta.className = 'admin-area-meta';
    meta.textContent = `${adminRoleNames[user.role]} · ${user.is_active ? 'активен' : 'заблокирован'} · последний вход: ${user.last_login_at ? new Date(user.last_login_at).toLocaleString('ru-RU') : 'нет'}`;

    const role = document.createElement('select'); role.setAttribute('aria-label', 'Роль');
    role.append(new Option('Администратор локаций', 'location'));
    if (currentAdmin.role === 'owner') role.append(new Option('Общий администратор', 'admin'));
    if (user.role === 'owner') role.append(new Option('Главный администратор', 'owner'));
    role.value = user.role; role.disabled = user.role === 'owner';
    const areas = document.createElement('div'); areas.className = 'access-areas';
    adminAreaChecks(areas, user.allowed_areas || []);
    role.onchange = () => { areas.hidden = role.value !== 'location'; }; role.onchange();
    const accessSave = document.createElement('button'); accessSave.className = 'mini-button';
    accessSave.textContent = 'Сохранить доступ'; accessSave.disabled = user.role === 'owner';
    accessSave.onclick = async () => {
      accessSave.disabled = true;
      try {
        await adminRequest(`/api/admin/users/${encodeURIComponent(user.username)}/access`, {method:'PUT',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({role:role.value, allowed_areas:adminCheckedAreas(areas)})});
        adminNotice('Доступ сохранён'); await loadAdminUsers();
      } catch (error) { adminNotice(error.message, true); }
      finally { accessSave.disabled = false; }
    };

    const passwordInput = document.createElement('input');
    passwordInput.type = 'password';
    passwordInput.placeholder = 'Новый пароль';
    passwordInput.autocomplete = 'new-password';

    const actions = document.createElement('div');
    actions.className = 'admin-area-actions';

    const passwordButton = document.createElement('button');
    passwordButton.className = 'mini-button';
    passwordButton.type = 'button';
    passwordButton.textContent = 'Сменить пароль';
    passwordButton.disabled = user.role === 'owner';
    passwordButton.addEventListener('click', async () => {
      await updateUserPassword(user.username, passwordInput.value);
      passwordInput.value = '';
    });

    const activeButton = document.createElement('button');
    activeButton.className = user.is_active ? 'mini-button danger-button' : 'mini-button';
    activeButton.type = 'button';
    activeButton.textContent = user.is_active ? 'Заблокировать' : 'Разблокировать';
    activeButton.disabled = user.role === 'owner';
    activeButton.addEventListener('click', async () => {
      await setUserActive(user.username, !user.is_active);
    });

    const deleteButton = document.createElement('button');
    deleteButton.className = 'mini-button danger-button';
    deleteButton.type = 'button';
    deleteButton.textContent = 'Удалить';
    deleteButton.disabled = user.role === 'owner';
    deleteButton.addEventListener('click', async () => {
      if (!confirm(`Удалить пользователя "${user.username}"?`)) {
        return;
      }
      await deleteUser(user.username);
    });

    const heading = document.createElement('div'); heading.className = 'admin-user-heading'; heading.append(title, meta);
    if (user.role === 'owner') {
      const note = document.createElement('p'); note.className = 'admin-note'; note.textContent = 'Главная учётная запись. Настраивается в secret.env.';
      row.append(heading, note);
    } else {
      const access = document.createElement('div'); access.className = 'admin-user-access';
      const label = document.createElement('label'); label.className = 'admin-field'; label.append('Роль', role);
      access.append(label, accessSave, areas);
      const passwordRow = document.createElement('div'); passwordRow.className = 'admin-password-row';
      const passwordLabel = document.createElement('label'); passwordLabel.className = 'admin-field'; passwordLabel.append('Новый пароль', passwordInput);
      passwordRow.append(passwordLabel, passwordButton);
      actions.append(activeButton, deleteButton);
      row.append(heading, access, passwordRow, actions);
    }
    adminUserList.appendChild(row);
  });
}

function renderLoginLogs(items) {
  adminLoginLogList.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Журнал входов пуст';
    adminLoginLogList.appendChild(empty);
    return;
  }

  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = item.success ? 'admin-log-item success-log' : 'admin-log-item';
    const meta = document.createElement('div');
    meta.className = 'admin-area-meta';
    meta.textContent = `${item.created_at} · ${item.username} · ${item.success ? 'успешно' : 'ошибка'} · ${item.remote_addr || '-'}`;
    const message = document.createElement('div');
    message.textContent = item.message || '';
    row.append(meta, message);
    adminLoginLogList.appendChild(row);
  });
}

async function createAdminUser() {
  const username = newAdminUsername.value.trim();
  const password = newAdminPassword.value;
  if (!username || !password) {
    alert('Укажи логин и пароль');
    return;
  }
  const response = await fetch('/api/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, role: newAdminRole.value, allowed_areas: adminCheckedAreas(newAdminAreas) }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    alert(payload.detail || 'Не удалось создать пользователя');
    return;
  }
  newAdminUsername.value = '';
  newAdminPassword.value = '';
  await loadAdminUsers();
  adminNotice('Администратор создан');
}

async function updateUserPassword(username, password) {
  if (!password) {
    alert('Укажи новый пароль');
    return;
  }
  const response = await fetch(`/api/admin/users/${encodeURIComponent(username)}/password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    alert(payload.detail || 'Не удалось сменить пароль');
    return;
  }
  await loadAdminUsers();
  adminNotice('Пароль изменён');
}

async function setUserActive(username, isActive) {
  const response = await fetch(`/api/admin/users/${encodeURIComponent(username)}/active`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    alert(payload.detail || 'Не удалось изменить статус пользователя');
    return;
  }
  await loadAdminUsers();
  adminNotice('Статус доступа изменён');
}

async function deleteUser(username) {
  const response = await fetch(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    alert(payload.detail || 'Не удалось удалить пользователя');
    return;
  }
  await loadAdminUsers();
  adminNotice('Администратор удалён');
}

async function changeOwnPassword() {
  const password = ownAdminPassword.value;
  if (!password) {
    ownPasswordStatus.className = 'admin-save-status error';
    ownPasswordStatus.textContent = 'Укажи новый пароль';
    return;
  }
  changeOwnPasswordButton.disabled = true;
  changeOwnPasswordButton.textContent = 'Сохраняю...';
  ownPasswordStatus.className = 'admin-save-status saving';
  ownPasswordStatus.textContent = 'Смена пароля...';
  try {
    const response = await fetch('/api/admin/me/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || 'Не удалось сменить пароль');
    }
    ownAdminPassword.value = '';
    ownPasswordStatus.className = 'admin-save-status success';
    ownPasswordStatus.textContent = 'Пароль изменён';
    if (adminCsrfToken) setTimeout(() => location.replace('/admin/login?changed=1'),800);
  } catch (error) {
    ownPasswordStatus.className = 'admin-save-status error';
    ownPasswordStatus.textContent = error.message || 'Не удалось сменить пароль';
  } finally {
    setTimeout(() => {
      changeOwnPasswordButton.disabled = false;
      changeOwnPasswordButton.textContent = 'Сменить пароль';
    }, 1200);
  }
}

function renderAdminLogs(items) {
  adminLogList.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Ошибок в логах не найдено';
    adminLogList.appendChild(empty);
    return;
  }

  items.slice().reverse().forEach((item) => {
    const row = document.createElement('div');
    row.className = 'admin-log-item';
    const source = document.createElement('div');
    source.className = 'admin-area-meta';
    source.textContent = item.source || 'log';
    const line = document.createElement('div');
    line.textContent = item.line || '';
    row.append(source, line);
    adminLogList.appendChild(row);
  });
}

function createSoundSelect(selectedPath) {
  const select = document.createElement('select');
  select.dataset.soundSelect = 'true';
  const defaultOption = document.createElement('option');
  defaultOption.value = '';
  defaultOption.textContent = `По умолчанию - ${defaultSoundName}`;
  select.appendChild(defaultOption);

  soundItems.forEach((sound) => {
    const option = document.createElement('option');
    option.value = sound.path;
    option.textContent = sound.name;
    option.selected = sound.path === selectedPath;
    select.appendChild(option);
  });
  if (selectedPath && !soundItems.some((sound) => sound.path === selectedPath)) {
    select.append(new Option(`Файл недоступен - ${selectedPath}`, selectedPath));
  }
  select.value = selectedPath || '';
  return select;
}

function renderAdminAreas(items) {
  adminAreaList.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = currentAdmin?.role === 'location' ? 'Локации не назначены. Обратитесь к администратору.' : 'Локации пока не найдены. Проверьте пространства в Home Assistant и обновите список.';
    adminAreaList.appendChild(empty);
    return;
  }

  items.forEach((area) => {
    const row = document.createElement('div');
    row.className = 'admin-area';
    row.addEventListener('input', () => { row.dataset.dirty = 'true'; });

    const title = document.createElement('div');
    title.className = 'admin-area-title';
    title.textContent = area.ha_name || area.area_id;

    const meta = document.createElement('div');
    meta.className = 'admin-area-meta';
    meta.textContent = `${area.area_id} · доступно: ${formatButtonAvailability(area)}`;

    const input = document.createElement('input');
    input.value = area.display_name || area.ha_name || area.area_id;

    const linkMeta = document.createElement('div');
    linkMeta.className = 'admin-area-meta';
    linkMeta.textContent = `Ссылка: /${area.slug || area.area_id}`;

    const slugLabel = document.createElement('div');
    slugLabel.className = 'admin-area-meta';
    slugLabel.textContent = 'Короткая ссылка';
    const slugInput = document.createElement('input');
    slugInput.value = area.slug || area.area_id;
    slugInput.placeholder = 'spb';
    slugInput.addEventListener('input', () => {
      const value = slugInput.value.trim() || area.area_id;
      linkMeta.textContent = `Ссылка: /${value}`;
    });

    const soundLabel = document.createElement('div');
    soundLabel.className = 'admin-area-meta';
    soundLabel.textContent = 'Звук уведомления';
    const soundSelect = createSoundSelect(area.sound_path || '');

    const repeatModeLabel = document.createElement('div');
    repeatModeLabel.className = 'admin-area-meta';
    repeatModeLabel.textContent = 'Повторять звук';
    const repeatMode = document.createElement('select');
    repeatMode.innerHTML = '<option value="seconds">По времени</option><option value="count">По количеству</option>';
    repeatMode.value = area.sound_repeat_mode || 'seconds';

    const repeatValue = document.createElement('input');
    repeatValue.type = 'number';
    repeatValue.min = '1';
    repeatValue.step = '1';
    repeatValue.value = repeatMode.value === 'count'
      ? (area.sound_repeat_count || 3)
      : (area.sound_repeat_seconds || defaultSoundPlaySeconds);

    const repeatHint = document.createElement('div');
    repeatHint.className = 'admin-area-meta';
    function updateRepeatHint() {
      repeatHint.textContent = repeatMode.value === 'count'
        ? 'Количество проигрываний'
        : `Секунд проигрывания, по умолчанию - ${defaultSoundPlaySeconds}`;
    }
    repeatMode.addEventListener('change', () => {
      repeatValue.value = repeatMode.value === 'count'
        ? (area.sound_repeat_count || 3)
        : (area.sound_repeat_seconds || defaultSoundPlaySeconds);
      updateRepeatHint();
    });
    updateRepeatHint();

    const historyLabel = document.createElement('div');
    historyLabel.className = 'admin-area-meta';
    historyLabel.textContent = 'История нажатий, часов';
    const historyHours = document.createElement('input');
    historyHours.type = 'number';
    historyHours.min = '0';
    historyHours.step = '1';
    historyHours.placeholder = `По умолчанию - ${defaultSyncHistoryHours}`;
    historyHours.value = area.history_sync_hours ?? '';

    const ackLabel = document.createElement('div');
    ackLabel.className = 'admin-area-meta';
    ackLabel.textContent = 'Кнопка подтверждения';
    const ackInput = document.createElement('input');
    ackInput.value = area.ack_button_text || '';
    ackInput.placeholder = `По умолчанию - ${defaultAckButtonText}`;

    const enabled = document.createElement('label');
    enabled.className = 'admin-check';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = area.enabled !== false;
    enabled.append(checkbox, document.createTextNode('Показывать'));

    const syncStatus = document.createElement('div');
    syncStatus.className = 'admin-area-meta';

    const saveStatus = document.createElement('div');
    saveStatus.className = 'admin-save-status';

    const actions = document.createElement('div');
    actions.className = 'admin-area-actions';

    const save = document.createElement('button');
    save.className = 'mini-button';
    save.textContent = 'Сохранить';
    save.addEventListener('click', async () => {
      const repeatNumber = Number(repeatValue.value) || 1;
      const historyNumber = historyHours.value === '' ? null : Math.max(0, Number(historyHours.value) || 0);
      const previousText = save.textContent;
      save.disabled = true;
      save.textContent = 'Сохраняю...';
      saveStatus.className = 'admin-save-status saving';
      saveStatus.textContent = 'Сохранение...';
      try {
        const response = await fetch('/api/admin/areas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            area_id: area.area_id,
            display_name: input.value,
            enabled: checkbox.checked,
            color: area.color || '#60a5fa',
            slug: slugInput.value,
            sound_path: soundSelect.value,
            sound_repeat_mode: repeatMode.value,
            sound_repeat_seconds: repeatMode.value === 'seconds' ? repeatNumber : null,
            sound_repeat_count: repeatMode.value === 'count' ? repeatNumber : null,
            history_sync_hours: historyNumber,
            ack_button_text: ackInput.value,
          }),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Проверьте значения полей');
        }
        save.textContent = 'Сохранено';
        saveStatus.className = 'admin-save-status success';
        saveStatus.textContent = `Сохранено: ${new Intl.DateTimeFormat('ru-RU', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }).format(new Date())}`;
        const saved = await response.json();
        delete row.dataset.dirty;
        const position = adminAreas.findIndex((item) => item.area_id === area.area_id);
        if (position >= 0) adminAreas[position] = {...adminAreas[position], ...saved.item};
        locationSelect.querySelector(`option[value="${CSS.escape(area.area_id)}"]`).textContent = saved.item.display_name;
        document.querySelector('#adminLocationTitle').textContent = saved.item.display_name;
      } catch (error) {
        save.textContent = 'Ошибка';
        saveStatus.className = 'admin-save-status error';
        saveStatus.textContent = error.message || 'Не удалось сохранить';
      } finally {
        setTimeout(() => {
          save.disabled = false;
          save.textContent = previousText;
        }, 1400);
      }
    });

    const syncButton = document.createElement('button');
    syncButton.className = 'mini-button';
    syncButton.type = 'button';
    syncButton.textContent = 'Подтянуть историю';
    syncButton.addEventListener('click', async () => {
      const historyNumber = historyHours.value === '' ? null : Math.max(0, Number(historyHours.value) || 0);
      syncButton.disabled = true;
      syncStatus.textContent = 'Синхронизация...';
      try {
        const response = await fetch(`/api/admin/areas/${encodeURIComponent(area.area_id)}/sync-history`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hours: historyNumber }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || 'Не удалось подтянуть историю');
        }
        syncStatus.textContent = `Добавлено: ${payload.added || 0} · кнопок: ${payload.entity_count || 0}`;
      } catch (error) {
        syncStatus.textContent = error.message || 'Ошибка синхронизации';
      } finally {
        syncButton.disabled = false;
      }
    });

    actions.append(save, syncButton);

    function section(name, fields) {
      const group = document.createElement('section'); group.className = 'area-settings-section';
      const heading = document.createElement('h3'); heading.textContent = name; group.append(heading);
      fields.forEach(([labelText, control]) => {
        const label = document.createElement('label'); label.className = 'admin-field';
        control.setAttribute('aria-label', labelText);
        label.append(document.createTextNode(labelText), control); group.append(label);
      });
      return group;
    }
    const restricted = currentAdmin?.role === 'location';
    [input, slugInput, checkbox].forEach((control) => { control.disabled = restricted; });
    input.setAttribute('aria-label', 'Название локации');
    row.append(meta,
      section('Основное', [['Название локации', input], ['Короткая ссылка', slugInput], ['Кнопка подтверждения', ackInput]]),
      section('Звук', [['Звук уведомления', soundSelect], ['Повторять', repeatMode], ['Длительность / количество', repeatValue]]),
      section('История', [['Подтягивать историю, часов', historyHours]]));
    repeatValue.after(repeatHint);
    row.append(enabled, actions, saveStatus, syncStatus);
    adminAreaList.appendChild(row);
  });
}

adminRefreshButton.addEventListener('click', async () => {
  if (adminAreaList.querySelector('[data-dirty]') && !confirm('Обновить и отменить несохранённые изменения?')) return;
  adminRefreshButton.disabled = true;
  try {
  await loadSounds();
  await loadAdminAreas();
  adminNotice('Локации обновлены');
  } catch (error) { adminNotice(error.message, true); }
  finally { adminRefreshButton.disabled = false; }
});
adminLogsRefreshButton.addEventListener('click', loadAdminLogs);
adminUsersRefreshButton.addEventListener('click', loadAdminUsers);
adminLoginLogsRefreshButton.addEventListener('click', loadLoginLogs);
createAdminUserButton.addEventListener('click', createAdminUser);
changeOwnPasswordButton.addEventListener('click', changeOwnPassword);
adminTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    setActiveTab(tab.dataset.tab);
  });
});
soundUploadButton.addEventListener('click', uploadSound);
soundSearchInput.addEventListener('input', () => {
  soundPage = 1;
  renderSoundLibrary();
});
soundPrevButton.addEventListener('click', () => {
  if (soundPage > 1) {
    soundPage -= 1;
    renderSoundLibrary();
  }
});
soundNextButton.addEventListener('click', () => {
  soundPage += 1;
  renderSoundLibrary();
});
async function initializeAdmin() {
  try {
  await loadCurrentAdmin();
  await loadClientConfig();
  await loadSounds();
  await loadAdminAreas();
  adminTabs.forEach((tab) => { tab.disabled = false; });
  window.dispatchEvent(new Event('admin-ready'));
  } catch (error) { adminNotice(error.message || 'Не удалось загрузить админку', true); }
}

locationSelect.onchange = () => {
  if (document.querySelector('[data-dirty]') && !confirm('Перейти в другую локацию без сохранения?')) {
    locationSelect.value = selectedAdminArea; return;
  }
  selectedAdminArea = locationSelect.value;
  renderSelectedAdminArea();
  window.dispatchEvent(new Event('admin-area-changed'));
};
newAdminRole.onchange = () => { newAdminAreas.hidden = newAdminRole.value !== 'location'; };
window.addEventListener('beforeunload', (event) => {
  if (document.querySelector('[data-dirty]')) { event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('unhandledrejection', (event) => { adminNotice(event.reason?.message || 'Ошибка соединения', true); });
document.querySelector('#adminLogoutButton').onclick = async () => {
  if (document.querySelector('[data-dirty]') && !confirm('Выйти без сохранения изменений?')) return;
  try {
    await adminRequest('/api/admin/auth/logout',{method:'POST'});
    document.querySelectorAll('[data-dirty]').forEach((node) => delete node.dataset.dirty);
    location.replace('/admin/login');
  } catch (error) { adminNotice(error.message,true); }
};

initializeAdmin();

async function loadGlobalSettings() {
  const payload = await adminRequest('/api/admin/settings');
  document.querySelector('#supportUrlInput').value = payload.support_url || '';
}
document.querySelector('#globalSettingsForm').onsubmit = async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  const status = document.querySelector('#globalSettingsStatus');
  button.disabled = true; status.textContent = 'Сохранение...'; status.classList.remove('error');
  try {
    const payload = await adminRequest('/api/admin/settings',{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({support_url:document.querySelector('#supportUrlInput').value.trim()})});
    document.querySelector('#supportUrlInput').value = payload.support_url;
    updateSupportLink(payload.support_url);
    status.textContent = 'Сохранено для всех локаций';
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
  finally { button.disabled = false; }
};
