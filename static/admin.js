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

let soundItems = [];
let soundPage = 1;
let currentAdmin = null;
let defaultAckButtonText = 'ФА';
let defaultSyncHistoryHours = 24;
let defaultSoundPlaySeconds = 25;
let defaultSoundName = 'общий звук';
const soundPageSize = 6;

function setActiveTab(tabName) {
  adminTabs.forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === tabName);
  });
  adminTabPanels.forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.panel === tabName);
  });
}

async function loadCurrentAdmin() {
  const response = await fetch('/api/admin/me');
  const payload = await response.json();
  currentAdmin = payload.user || null;
  const isOwner = currentAdmin && currentAdmin.role === 'owner';
  ownerOnlyElements.forEach((element) => {
    element.classList.toggle('hidden', !isOwner);
  });
  if (!isOwner && document.querySelector('.admin-tab.active')?.dataset.tab === 'users') {
    setActiveTab('areas');
  }
  if (isOwner) {
    profilePasswordNote.textContent = 'Главная env-учётка меняется через secret.env. Созданные пользователи могут менять пароль здесь.';
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
    const name = document.createElement('div');
    name.className = 'admin-area-title';
    name.textContent = sound.name;
    const meta = document.createElement('div');
    meta.className = 'admin-area-meta';
    meta.textContent = `${sound.path} · ${Math.ceil((sound.size || 0) / 1024)} KB`;
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.src = sound.url;

    const actions = document.createElement('div');
    actions.className = 'sound-actions';
    const deleteButton = document.createElement('button');
    deleteButton.className = 'mini-button danger-button';
    deleteButton.type = 'button';
    deleteButton.textContent = 'Удалить';
    deleteButton.addEventListener('click', async () => {
      if (!confirm(`Удалить звук "${sound.name}"? Если он выбран в регионах, привязка будет снята.`)) {
        return;
      }
      const response = await fetch(`/api/admin/sounds/${encodeURIComponent(sound.name)}`, { method: 'DELETE' });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        alert(payload.detail || 'Не удалось удалить звук');
        return;
      }
      await loadSounds();
      await loadAdminAreas();
    });
    actions.append(deleteButton);

    row.append(name, meta, audio, actions);
    soundLibraryList.appendChild(row);
  });
}

async function uploadSound() {
  const file = soundFileInput.files && soundFileInput.files[0];
  if (!file) {
    return;
  }
  const response = await fetch(`/api/admin/sounds/${encodeURIComponent(file.name)}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!response.ok) {
    alert('Не удалось загрузить звук');
    return;
  }
  soundFileInput.value = '';
  await loadSounds();
  await loadAdminAreas();
}

async function loadAdminAreas() {
  const response = await fetch('/api/admin/areas');
  const payload = await response.json();
  renderAdminAreas(payload.items || []);
}

async function loadAdminLogs() {
  const response = await fetch('/api/admin/logs?limit=160');
  const payload = await response.json();
  renderAdminLogs(payload.items || []);
}

async function loadAdminUsers() {
  if (!currentAdmin || currentAdmin.role !== 'owner') {
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
  if (!currentAdmin || currentAdmin.role !== 'owner') {
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
    meta.textContent = `${user.role === 'owner' ? 'Главный админ' : 'Админ'} · ${user.is_active ? 'активен' : 'заблокирован'} · последний вход: ${user.last_login_at || 'нет'}`;

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

    actions.append(passwordButton, activeButton, deleteButton);
    row.append(title, meta, passwordInput, actions);
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
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    alert(payload.detail || 'Не удалось создать пользователя');
    return;
  }
  newAdminUsername.value = '';
  newAdminPassword.value = '';
  await loadAdminUsers();
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
}

async function deleteUser(username) {
  const response = await fetch(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    alert(payload.detail || 'Не удалось удалить пользователя');
    return;
  }
  await loadAdminUsers();
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
  return select;
}

function renderAdminAreas(items) {
  adminAreaList.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Пространства пока не найдены. Проверь, что Home Assistant доступен и устройства добавлены в пространства.';
    adminAreaList.appendChild(empty);
    return;
  }

  items.forEach((area) => {
    const row = document.createElement('div');
    row.className = 'admin-area';

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
          throw new Error(payload.detail || 'Не удалось сохранить регион');
        }
        save.textContent = 'Сохранено';
        saveStatus.className = 'admin-save-status success';
        saveStatus.textContent = `Сохранено: ${new Intl.DateTimeFormat('ru-RU', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }).format(new Date())}`;
        setTimeout(loadAdminAreas, 900);
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
        await loadAdminLogs();
      }
    });

    actions.append(save, syncButton);

    row.append(
      title,
      meta,
      linkMeta,
      input,
      slugLabel,
      slugInput,
      soundLabel,
      soundSelect,
      repeatModeLabel,
      repeatMode,
      repeatHint,
      repeatValue,
      historyLabel,
      historyHours,
      ackLabel,
      ackInput,
      enabled,
      actions,
      saveStatus,
      syncStatus,
    );
    adminAreaList.appendChild(row);
  });
}

adminRefreshButton.addEventListener('click', async () => {
  await loadSounds();
  await loadAdminAreas();
  await loadAdminLogs();
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
  await loadCurrentAdmin();
  await loadClientConfig();
  await Promise.all([loadSounds(), loadAdminAreas(), loadAdminLogs(), loadAdminUsers(), loadLoginLogs()]);
}

initializeAdmin();
