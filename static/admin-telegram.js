(() => {
  const regionList = document.querySelector('#telegramRegionList');
  const serviceStatus = document.querySelector('#telegramServiceStatus');
  const refreshButton = document.querySelector('#telegramRefreshButton');
  let loading = false;
  let loaded = false;
  let requestSequence = 0;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function request(path = '', options = {}) {
    const response = await fetch(`/api/admin/telegram${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Не удалось выполнить запрос');
    return payload;
  }

  async function action(button, status, callback) {
    const title = button.textContent;
    button.disabled = true;
    button.textContent = 'Подождите…';
    status.className = 'telegram-feedback';
    status.textContent = '';
    try {
      await callback();
    } catch (error) {
      status.className = 'telegram-feedback error';
      status.textContent = error.message || 'Ошибка соединения';
    } finally {
      button.disabled = false;
      button.textContent = title;
    }
  }

  function renderCard(item, keyReady) {
    const card = element('article', 'telegram-region-card');
    const heading = element('div', 'telegram-card-heading');
    const title = element('h3', '', item.display_name);
    const badge = element('span', 'telegram-badge', item.enabled ? 'Включено' : 'Выключено');
    heading.append(title, badge);

    const bot = element('p', 'telegram-meta', item.bot_username ? `Бот: @${item.bot_username}` : 'Бот пока не настроен');
    const destination = element('p', 'telegram-meta');
    const state = element('p', 'telegram-meta');
    const stats = element('p', 'telegram-meta');
    const lastError = element('p', 'telegram-feedback error');
    const tokenLabel = element('label', 'telegram-field', 'Токен бота');
    const token = element('input');
    token.type = 'password';
    token.autocomplete = 'new-password';
    token.spellcheck = false;
    token.placeholder = item.token_configured ? 'Токен сохранён. Введите новый только для замены' : 'Токен от @BotFather';
    token.disabled = !keyReady;
    tokenLabel.append(token);
    const enabledLabel = element('label', 'telegram-toggle');
    const enabled = element('input');
    enabled.type = 'checkbox';
    enabled.checked = item.enabled;
    enabled.disabled = !keyReady;
    enabledLabel.append(enabled, element('span', '', 'Отправлять уведомления этой локации'));
    const profileLabel = element('label', 'telegram-toggle');
    const requireProfile = element('input');
    requireProfile.type = 'checkbox';
    requireProfile.checked = item.require_profile;
    profileLabel.append(requireProfile, element('span', '', 'Требовать корпоративный профиль для действий'));
    const reassignLabel = element('label', 'telegram-toggle');
    const reassign = element('input');
    reassign.type = 'checkbox';
    reassign.checked = item.allow_reassign_any;
    reassignLabel.append(reassign, element('span', '', 'Разрешить всем сотрудникам менять ответственного и отменять принятие'));

    const setupInstruction = element('p', 'telegram-instruction');
    const candidates = element('div', 'telegram-setups');
    const controls = element('div', 'telegram-actions');
    const status = element('p', 'telegram-feedback');
    status.setAttribute('role', 'status');

    const route = `/${encodeURIComponent(item.area_id)}`;

    function updateInformation(current) {
      item = current;
      bot.textContent = item.bot_username ? `Бот: @${item.bot_username}` : 'Бот пока не настроен';
      badge.textContent = item.enabled ? 'Включено' : 'Выключено';
      badge.classList.toggle('active', item.enabled);
      destination.textContent = item.chat_id === null
        ? 'Группа и тема ещё не привязаны'
        : `${item.chat_title || 'Группа'} · ${item.message_thread_id ? `тема ${item.message_thread_id}` : 'основной чат'} · ID ${item.chat_id}`;
      state.textContent = item.polling ? 'Получение команд запущено' : 'Получение команд не запущено';
      const counts = item.delivery_stats || {};
      stats.textContent = `В очереди: ${counts.queued || 0} · отправлено: ${counts.sent || 0} · ошибок: ${counts.failed || 0}`
        + (counts.uncertain ? ` · доставка не подтверждена: ${counts.uncertain}` : '')
        + (counts.expired ? ` · пропущено устаревших: ${counts.expired}` : '');
      lastError.textContent = item.last_error || '';
      const setupOpen = item.setup_until * 1000 > Date.now();
      begin.disabled = !keyReady || !item.token_configured;
      begin.textContent = item.chat_id === null ? 'Начать привязку' : 'Изменить привязку';
      setupInstruction.textContent = item.bot_username && setupOpen
        ? `Добавьте бота в группу. В нужной теме администратор группы должен отправить /setup@${item.bot_username}. Затем нажмите «Обновить привязку». Права администратора самому боту не нужны; отправка сообщений должна быть разрешена.`
        : item.token_configured
          ? 'Приём команд /setup закрыт. Для новой темы нажмите «Начать привязку» или «Изменить привязку». Действующая тема продолжает работать.'
          : 'Сначала сохраните токен. Бот проверится автоматически.';
      candidates.replaceChildren();
      for (const setup of item.setups || []) {
        const proposal = element('div', 'telegram-setup');
        proposal.append(element('span', '', `${setup.chat_title} · ${setup.thread_id ? `тема ${setup.thread_id}` : 'основной чат'}`));
        const confirm = element('button', 'mini-button', 'Привязать эту тему');
        confirm.type = 'button';
        confirm.addEventListener('click', () => action(confirm, status, async () => {
          await request(`${route}/confirm-setup`, { method: 'POST', body: JSON.stringify({ setup_id: setup.id }) });
          await refreshInformation();
          status.textContent = 'Группа и тема привязаны';
        }));
        proposal.append(confirm);
        candidates.append(proposal);
      }
    }

    async function refreshInformation() {
      const payload = await request();
      const current = payload.items.find((region) => region.area_id === item.area_id);
      if (current) updateInformation(current);
    }

    function button(text, callback) {
      const control = element('button', 'mini-button', text);
      control.type = 'button';
      control.addEventListener('click', () => action(control, status, callback));
      controls.append(control);
      return control;
    }

    const save = button('Сохранить', async () => {
      await request(route, { method: 'POST', body: JSON.stringify({ enabled: enabled.checked, token: token.value.trim() || null,
        require_profile: requireProfile.checked, allow_reassign_any: reassign.checked }) });
      token.value = '';
      await refreshInformation();
      status.textContent = 'Настройки сохранены. Изменение обработчика команд занимает до 15 секунд';
    });
    save.disabled = !keyReady;
    const begin = button('Начать привязку', async () => {
      await request(`${route}/begin-setup`, { method: 'POST' });
      await refreshInformation();
      status.textContent = 'Приём команд /setup открыт на 15 минут. Отправьте команду в нужной теме';
    });
    button('Обновить привязку', async () => {
      await refreshInformation();
      status.textContent = item.setups.length ? 'Найдена новая привязка. Подтвердите нужную тему' : 'Данные обновлены. Новых предложений привязки нет';
    });
    button('Отправить тест', async () => {
      const result = await request(`${route}/test`, { method: 'POST' });
      status.textContent = result.message;
      await refreshInformation();
    });
    const remove = button('Удалить настройки', async () => {
      if (!confirm(`Удалить бота и привязку Telegram для локации «${item.display_name}»?`)) return;
      await request(route, { method: 'DELETE' });
      token.value = '';
      enabled.checked = false;
      await refreshInformation();
      status.textContent = 'Токен и привязка удалены';
    });
    remove.classList.add('danger-button');
    updateInformation(item);
    card.append(heading, bot, destination, state, stats, lastError, tokenLabel, enabledLabel, profileLabel, reassignLabel, controls, status, setupInstruction, candidates);
    return card;
  }

  async function load() {
    const sequence = ++requestSequence;
    const areaId = selectedAdminArea;
    loading = true;
    refreshButton.disabled = true;
    serviceStatus.textContent = 'Загрузка настроек…';
    try {
      const payload = await request();
      if (sequence !== requestSequence || areaId !== selectedAdminArea) return;
      serviceStatus.textContent = !payload.key_ready ? payload.key_error
        : !payload.enabled ? 'Telegram отключён через TELEGRAM_ENABLED'
          : payload.error || `Отдельный маршрут для каждой локации. Срок доставки нового вызова: ${payload.event_ttl_seconds} сек.`;
      const items = payload.items.filter((item) => item.area_id === areaId);
      regionList.replaceChildren(...items.map((item) => renderCard(item, payload.key_ready)));
      if (!items.length) regionList.append(element('p', 'admin-note', 'Выберите доступную локацию'));
      loaded = true;
    } catch (error) {
      serviceStatus.textContent = error.message;
    } finally {
      refreshButton.disabled = false;
      loading = false;
    }
  }

  refreshButton.addEventListener('click', load);
  document.querySelector('[data-tab="telegram"]').addEventListener('click', () => {
    if (currentAdmin && !loaded) load();
  });
  window.addEventListener('admin-area-changed', () => {
    loaded = false; regionList.replaceChildren();
    if (document.querySelector('[data-panel="telegram"]').classList.contains('active')) load();
  });
})();
