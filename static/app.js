const statusText = document.querySelector("#statusText");
const watchedCount = document.querySelector("#watchedCount");
const connectionBadge = document.querySelector("#connectionBadge");
const enableSoundButton = document.querySelector("#enableSoundButton");
const volumeWidget = document.querySelector("#volumeWidget");
const volumeToggleButton = document.querySelector("#volumeToggleButton");
const volumeCloseButton = document.querySelector("#volumeCloseButton");
const volumePanel = document.querySelector("#volumePanel");
const volumeSlider = document.querySelector("#volumeSlider");
const volumeInput = document.querySelector("#volumeInput");
const volumeValue = document.querySelector("#volumeValue");
const volumeBadge = document.querySelector("#volumeBadge");
const volumeTestButton = document.querySelector("#volumeTestButton");
const alertAudio = document.querySelector("#alertAudio");
const refreshHistoryButton = document.querySelector("#refreshHistoryButton");
const prevPageButton = document.querySelector("#prevPageButton");
const nextPageButton = document.querySelector("#nextPageButton");
const pageInfo = document.querySelector("#pageInfo");
const historyList = document.querySelector("#historyList");
const historySummary = document.querySelector("#historySummary");
const batteryList = document.querySelector("#batteryList");
const batterySummary = document.querySelector("#batterySummary");
const toggleBatteriesButton = document.querySelector("#toggleBatteriesButton");
const batterySearchInput = document.querySelector("#batterySearchInput");
const popup = document.querySelector("#popup");
const popupTitle = document.querySelector("#popupTitle");
const popupCount = document.querySelector("#popupCount");
const popupList = document.querySelector("#popupList");
const clearPopupButton = document.querySelector("#clearPopupButton");
const detailsModal = document.querySelector("#detailsModal");
const detailsCloseButton = document.querySelector("#detailsCloseButton");
const detailsName = document.querySelector("#detailsName");
const detailsPressedAt = document.querySelector("#detailsPressedAt");
const detailsAckLabel = document.querySelector("#detailsAckLabel");
const detailsAcknowledgedAt = document.querySelector("#detailsAcknowledgedAt");
const batteryModal = document.querySelector("#batteryModal");
const batteryCloseButton = document.querySelector("#batteryCloseButton");
const batteryDetailsName = document.querySelector("#batteryDetailsName");
const batteryDetailsPercent = document.querySelector("#batteryDetailsPercent");
const batteryDetailsUpdated = document.querySelector("#batteryDetailsUpdated");
const batteryDetailsChanged = document.querySelector("#batteryDetailsChanged");
const batteryDetailsStatus = document.querySelector("#batteryDetailsStatus");
const batteryDetailsArea = document.querySelector("#batteryDetailsArea");
const batteryDetailsEntity = document.querySelector("#batteryDetailsEntity");
const batteryDetailsButtonEntity = document.querySelector("#batteryDetailsButtonEntity");
const batteryDetailsRaw = document.querySelector("#batteryDetailsRaw");

let activeAlerts = [];
let soundEnabled = false;
let reconnectTimer = null;
let historyPage = 1;
let totalHistoryPages = 1;
let showAllBatteries = false;
let selectedAreaId = new URLSearchParams(window.location.search).get("area_id") || "";
let selectedAreaSlug = "";
let areaItems = [];
let totalButtonCount = 0;
let activeButtonCount = 0;
let batteryTotal = 0;
let batteryItems = [];
let batteryAvailable = true;
let batteryError = "";
let ackButtonText = "ФА";
let defaultSoundUrl = null;
let soundPlaySeconds = 25;
let syncHistoryHours = 24;
let soundStopTimer = null;
let soundLoopActive = false;
let soundRepeatRemaining = null;
let refreshInProgress = false;
const historyPageSize = 100;
const freshHighlightMs = 5 * 60 * 1000;
const defaultVolumePercent = 100;
const volumeStorageKey = "perehomelab-alert-volume-v2";

const pathAreaSlug = decodeURIComponent(window.location.pathname.replace(/^\/+|\/+$/g, ""));
if (pathAreaSlug && !["monitor", "admin"].includes(pathAreaSlug)) {
  selectedAreaSlug = pathAreaSlug;
}


function formatHours(hours) {
  return Number.isInteger(hours) ? `${hours} ч` : `${hours.toFixed(1)} ч`;
}

function formatButtonAvailability(activeCount, totalCount) {
  const total = Number(totalCount) || 0;
  const active = Number.isFinite(Number(activeCount)) ? Number(activeCount) : total;
  return `${active}/${total}`;
}

function updateWatchedMetric() {
  if (selectedAreaId) {
    const area = areaItems.find((item) => item.area_id === selectedAreaId);
    watchedCount.textContent = formatButtonAvailability(area?.active_button_count, area?.button_count);
    return;
  }
  watchedCount.textContent = formatButtonAvailability(activeButtonCount, totalButtonCount);
}

function areaQuery() {
  return selectedAreaId ? `area_id=${encodeURIComponent(selectedAreaId)}` : "";
}

function areaUrl(path, params = {}) {
  const search = new URLSearchParams(params);
  if (selectedAreaId) {
    search.set("area_id", selectedAreaId);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function selectedAreaName() {
  if (!selectedAreaId) {
    return "Все города";
  }
  const area = areaItems.find((item) => item.area_id === selectedAreaId);
  return area ? area.display_name : selectedAreaId;
}

function setConnection(state) {
  connectionBadge.className = `dot ${state}`;
}

function shortHaError(value) {
  if (!value) {
    return "";
  }
  const text = String(value);
  if (text.toLowerCase().includes("timeout")) {
    return "HA timeout";
  }
  if (text.toLowerCase().includes("connection") || text.toLowerCase().includes("connect")) {
    return "HA недоступен";
  }
  if (text.includes("401") || text.toLowerCase().includes("unauthorized")) {
    return "HA: ошибка токена";
  }
  return text.length > 42 ? `${text.slice(0, 42)}…` : text;
}

function loadSavedVolume() {
  const savedValue = Number(localStorage.getItem(volumeStorageKey));
  if (!Number.isFinite(savedValue)) {
    return defaultVolumePercent;
  }
  return Math.max(0, Math.min(100, Math.round(savedValue)));
}

function applyVolume(percent, save = true) {
  const cleanPercent = Math.max(0, Math.min(100, Math.round(Number(percent) || defaultVolumePercent)));
  alertAudio.volume = cleanPercent / 100;
  volumeSlider.value = String(cleanPercent);
  volumeInput.value = String(cleanPercent);
  volumeValue.textContent = `${cleanPercent}%`;
  volumeBadge.textContent = `${cleanPercent}%`;
  if (save) {
    localStorage.setItem(volumeStorageKey, String(cleanPercent));
  }
}

function setVolumePanelOpen(open) {
  volumePanel.classList.toggle("hidden", !open);
  volumeWidget.classList.toggle("open", open);
  volumeToggleButton.setAttribute("aria-expanded", open ? "true" : "false");
}

function applyVolumeFromInput() {
  applyVolume(volumeInput.value);
}

async function loadClientConfig() {
  const response = await fetch("/api/client-config");
  const config = await response.json();
  if (config.sound_url) {
    defaultSoundUrl = config.sound_url;
    alertAudio.src = defaultSoundUrl;
  }
  ackButtonText = config.ack_button_text || "ФА";
  clearPopupButton.textContent = ackButtonText;
  detailsAckLabel.textContent = ackButtonText;
  soundPlaySeconds = Number(config.sound_play_seconds) > 0 ? Number(config.sound_play_seconds) : 25;
  syncHistoryHours = Number(config.sync_history_hours) >= 0 ? Number(config.sync_history_hours) : 24;
}

function setSoundEnabled(enabled) {
  soundEnabled = enabled;
  enableSoundButton.textContent = enabled ? "Звук включён" : "Включить звук";
  enableSoundButton.classList.toggle("enabled", enabled);
}

async function unlockSound() {
  if (!alertAudio.src) {
    return false;
  }

  try {
    applyVolume(volumeSlider.value, false);
    await alertAudio.play();
    alertAudio.pause();
    alertAudio.currentTime = 0;
    setSoundEnabled(true);
    return true;
  } catch {
    setSoundEnabled(false);
    enableSoundButton.textContent = "Нажмите для звука";
    return false;
  }
}

async function testCurrentVolume() {
  const testSoundUrl = soundUrlForCurrentArea();
  if (testSoundUrl) {
    alertAudio.src = testSoundUrl;
  }
  if (!alertAudio.src) {
    volumeTestButton.textContent = "Нет звука";
    setTimeout(() => {
      volumeTestButton.textContent = "Проверить";
    }, 1400);
    return;
  }

  stopAlertSound();
  applyVolume(volumeSlider.value, false);
  volumeTestButton.disabled = true;
  volumeTestButton.textContent = "Играет...";
  try {
    alertAudio.currentTime = 0;
    await alertAudio.play();
    setSoundEnabled(true);
  } catch {
    setSoundEnabled(false);
    volumeTestButton.textContent = "Нажмите ещё раз";
  } finally {
    setTimeout(() => {
      volumeTestButton.disabled = false;
      volumeTestButton.textContent = "Проверить";
    }, 1400);
  }
}

function connectWebSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);

  socket.addEventListener("open", () => {
    setConnection("ok");
    socket.send("hello");
  });

  socket.addEventListener("message", (event) => {
    handleServerMessage(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    setConnection("error");
    statusText.textContent = "Нет связи";
    reconnectTimer = setTimeout(connectWebSocket, 2000);
  });

  socket.addEventListener("error", () => {
    setConnection("error");
  });
}

function handleServerMessage(payload) {
  if (payload.last_error) {
    statusText.textContent = shortHaError(payload.last_error);
    setConnection("error");
  } else if (payload.status) {
    statusText.textContent = payload.status;
    setConnection(payload.status.toLowerCase().includes("подключен") ? "ok" : "wait");
  }
  if (typeof payload.watched_count === "number") {
    totalButtonCount = payload.watched_count;
    activeButtonCount = Number.isFinite(Number(payload.active_watched_count))
      ? Number(payload.active_watched_count)
      : payload.watched_count;
    updateWatchedMetric();
  }
  if (Array.isArray(payload.areas)) {
    areaItems = payload.areas;
    renderCityTabs();
    updateWatchedMetric();
  }
  if (payload.type === "press" && Array.isArray(payload.events)) {
    addAlerts(payload.events);
  }
  if (payload.type === "history_sync") {
    loadHistory();
  }
}


async function loadAreas() {
  const response = await fetch("/api/areas");
  const payload = await response.json();
  areaItems = payload.items || [];
  if (payload.all) {
    totalButtonCount = payload.all.button_count || 0;
    activeButtonCount = Number.isFinite(Number(payload.all.active_button_count))
      ? Number(payload.all.active_button_count)
      : totalButtonCount;
  }
  if (selectedAreaSlug && !selectedAreaId) {
    const area = areaItems.find((item) => item.slug === selectedAreaSlug || item.area_id === selectedAreaSlug);
    if (area) {
      selectedAreaId = area.area_id;
    }
  }
  renderCityTabs();
  updateWatchedMetric();
  updateHistorySummary();
}

function renderCityTabs() {
  // В мониторинге быстрых вкладок нет: регион выбирается на главной странице.
}

function createCityTab(area) {
  const button = document.createElement("button");
  button.className = "city-tab";
  if ((area.area_id || "") === selectedAreaId) {
    button.classList.add("active");
  }
  button.innerHTML = `<span>${area.display_name}</span><strong>${formatButtonAvailability(area.active_button_count, area.button_count)}</strong>`;
  button.addEventListener("click", () => selectArea(area.area_id || ""));
  return button;
}

function selectArea(areaId) {
  selectedAreaId = areaId;
  const area = areaItems.find((item) => item.area_id === selectedAreaId);
  const nextUrl = selectedAreaId ? `/${encodeURIComponent(area?.slug || selectedAreaId)}` : "/monitor";
  window.history.replaceState({}, "", nextUrl);
  historyPage = 1;
  activeAlerts = activeAlerts.filter((event) => !selectedAreaId || event.area_id === selectedAreaId);
  renderPopup();
  if (!activeAlerts.length) {
    popup.classList.add("hidden");
  }
  renderCityTabs();
  updateWatchedMetric();
  updateHistorySummary();
  loadHistory();
  loadBatteries();
}

function updateHistorySummary() {
  const prefix = selectedAreaName();
  const area = areaItems.find((item) => item.area_id === selectedAreaId);
  const historyHours = Number(area?.history_sync_hours ?? syncHistoryHours);
  const suffix = historyHours > 0 ? `последние ${formatHours(historyHours)} + новые` : "только новые";
  historySummary.textContent = `${prefix} · ${suffix}`;
}

async function loadHistory() {
  const response = await fetch(areaUrl("/api/history", { page: historyPage, page_size: historyPageSize }));
  if (!response.ok) {
    throw new Error("Не удалось загрузить историю");
  }
  const payload = await response.json();
  renderHistory(payload.items || []);
  historyPage = payload.page || 1;
  totalHistoryPages = payload.total_pages || 1;
  updatePager();
}

async function loadBatteries() {
  try {
    const response = await fetch(areaUrl("/api/batteries"));
    if (!response.ok) {
      throw new Error("Не удалось загрузить заряд");
    }
    const payload = await response.json();
    batteryAvailable = payload.available !== false;
    batteryError = payload.error || "";
    batteryTotal = payload.total || 0;
    batteryItems = payload.items || [];
  } catch {
    batteryAvailable = false;
    batteryError = "Заряд временно недоступен";
    batteryTotal = 0;
    batteryItems = [];
  }
  renderBatteries();
}

async function refreshDashboard() {
  if (refreshInProgress) {
    return;
  }
  refreshInProgress = true;
  const previousText = refreshHistoryButton.textContent;
  refreshHistoryButton.disabled = true;
  refreshHistoryButton.classList.add("loading");
  refreshHistoryButton.textContent = "…";
  refreshHistoryButton.title = "Обновление...";

  try {
    await Promise.all([loadHistory(), loadBatteries(), loadAreas()]);
    refreshHistoryButton.classList.remove("loading");
    refreshHistoryButton.classList.add("success");
    refreshHistoryButton.textContent = "✓";
    refreshHistoryButton.title = `Обновлено: ${new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date())}`;
    setTimeout(() => {
      refreshHistoryButton.classList.remove("success");
      refreshHistoryButton.textContent = previousText;
      refreshHistoryButton.title = "Обновить историю и заряд";
    }, 1400);
  } catch {
    refreshHistoryButton.classList.remove("loading");
    refreshHistoryButton.classList.add("error");
    refreshHistoryButton.textContent = "!";
    refreshHistoryButton.title = "Ошибка обновления";
    setTimeout(() => {
      refreshHistoryButton.classList.remove("error");
      refreshHistoryButton.textContent = previousText;
      refreshHistoryButton.title = "Обновить историю и заряд";
    }, 2200);
  } finally {
    refreshHistoryButton.disabled = false;
    refreshInProgress = false;
  }
}

function renderBatteries() {
  batteryList.innerHTML = "";
  batteryList.classList.toggle("expanded", showAllBatteries || hasBatterySearch());
  toggleBatteriesButton.textContent = showAllBatteries ? "Скрыть" : "Показать все";

  if (!batteryAvailable) {
    batterySummary.textContent = batteryError || "Заряд временно недоступен";
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = batteryError || "Заряд временно недоступен";
    batteryList.appendChild(empty);
    return;
  }

  const filteredItems = getFilteredBatteries();
  const visibleItems = showAllBatteries || hasBatterySearch() ? filteredItems : filteredItems.slice(0, 5);
  batterySummary.textContent = getBatterySummaryText(filteredItems.length, visibleItems.length);

  if (!visibleItems.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = hasBatterySearch() ? "Ничего не найдено" : "Батареи не найдены";
    batteryList.appendChild(empty);
    return;
  }

  visibleItems.forEach((item) => {
    batteryList.appendChild(createBatteryRow(item));
  });
}

function hasBatterySearch() {
  return batterySearchInput.value.trim().length > 0;
}

function getFilteredBatteries() {
  const query = batterySearchInput.value.trim().toLowerCase();
  if (!query) {
    return batteryItems;
  }
  return batteryItems.filter((item) => item.name.toLowerCase().includes(query));
}

function getBatterySummaryText(filteredCount, visibleCount) {
  if (hasBatterySearch()) {
    return `Найдено: ${filteredCount}`;
  }
  if (showAllBatteries) {
    return `Все батареи: ${batteryTotal}`;
  }
  return `Самые разряженные: ${visibleCount} из ${batteryTotal}`;
}


function getBatteryMetaText(item, percent) {
  const received = formatBatteryUpdatedAt(item.received_at);
  if (percent <= 20) {
    return received ? `Критично: ${percent}% · проверено ${received}` : `Критично низкий заряд: ${percent}%`;
  }
  if (percent <= 40) {
    return received ? `Низкий: ${percent}% · проверено ${received}` : `Низкий заряд: ${percent}%`;
  }
  return received ? `Проверено: ${received}` : `Последний заряд: ${percent}%`;
}

function formatBatteryUpdatedAt(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatBatteryUpdatedAtFull(value) {
  if (!value) {
    return "Нет данных";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function getBatteryStaleText(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const ageMs = Date.now() - date.getTime();
  const staleMs = 48 * 60 * 60 * 1000;
  if (ageMs < staleMs) {
    return "";
  }
  const days = Math.max(1, Math.floor(ageMs / (24 * 60 * 60 * 1000)));
  return `HA давно не обновлял: ${days} дн.`;
}

function batteryStatusText(percent) {
  if (percent <= 20) {
    return "Критично низкий заряд";
  }
  if (percent <= 40) {
    return "Низкий заряд";
  }
  return "Норма";
}

function createBatteryRow(item) {
  const percent = Number(item.percent);
  const row = document.createElement("div");
  row.className = "battery-item";
  if (percent <= 20) {
    row.classList.add("critical");
  } else if (percent <= 40) {
    row.classList.add("low");
  }

  const info = document.createElement("div");
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = item.name;

  const meta = document.createElement("div");
  meta.className = "battery-meta";
  meta.textContent = getBatteryMetaText(item, percent);

  const meter = document.createElement("div");
  meter.className = "battery-meter";
  const fill = document.createElement("span");
  fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  meter.appendChild(fill);

  const value = document.createElement("div");
  value.className = "battery-percent";
  value.textContent = `${percent}%`;

  info.append(name, meta, meter);
  row.append(info, value);
  row.tabIndex = 0;
  row.title = "Открыть детали заряда";
  row.addEventListener("click", () => openBatteryDetails(item));
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openBatteryDetails(item);
    }
  });
  return row;
}

function openBatteryDetails(item) {
  const percent = Number(item.percent);
  batteryDetailsName.textContent = item.name || "Переговорка";
  batteryDetailsPercent.textContent = Number.isFinite(percent) ? `${percent}%` : "—";
  batteryDetailsUpdated.textContent = formatBatteryUpdatedAtFull(item.received_at);
  batteryDetailsChanged.textContent = formatBatteryUpdatedAtFull(item.updated_at || item.changed_at);
  const staleText = getBatteryStaleText(item.updated_at || item.changed_at);
  batteryDetailsStatus.textContent = staleText || (Number.isFinite(percent) ? batteryStatusText(percent) : "Нет данных");
  batteryDetailsArea.textContent = item.area_name || "Не указано";
  batteryDetailsEntity.textContent = item.entity_id || "—";
  batteryDetailsButtonEntity.textContent = item.button_entity_id || "—";
  batteryDetailsRaw.textContent = item.unit ? `${item.state || "—"} ${item.unit}` : (item.state || "—");
  batteryModal.classList.remove("hidden");
}

function closeBatteryDetails() {
  batteryModal.classList.add("hidden");
}

function addAlerts(events) {
  const visibleEvents = events.filter((event) => !selectedAreaId || event.area_id === selectedAreaId);
  if (!visibleEvents.length) {
    loadHistory();
    loadBatteries();
    return;
  }
  activeAlerts = [...activeAlerts, ...visibleEvents];
  renderPopup();
  showPopup();
  playAlertSound(visibleEvents[0]);
  loadHistory();
}

function ackTextForActiveAlerts() {
  if (!activeAlerts.length) {
    return ackButtonText;
  }
  const values = [...new Set(activeAlerts.map((event) => event.ack_button_text || ackButtonText))];
  return values.length === 1 ? values[0] : "Подтвердить всё";
}

function groupedAlertsByArea() {
  const groups = [];
  const byKey = new Map();
  activeAlerts.forEach((alert) => {
    const key = alert.area_id || alert.area_name || "unknown";
    if (!byKey.has(key)) {
      const group = {
        key,
        areaName: alert.area_name || "Без региона",
        items: [],
      };
      byKey.set(key, group);
      groups.push(group);
    }
    byKey.get(key).items.push(alert);
  });
  return groups;
}

function renderPopup() {
  popupList.innerHTML = "";

  const hasAlerts = activeAlerts.length > 0;
  popup.classList.toggle("many", activeAlerts.length > 3);
  popupCount.textContent = activeAlerts.length;
  popupTitle.textContent = activeAlerts.length > 1 ? "Несколько нажатий" : "Нажатие";
  clearPopupButton.textContent = ackTextForActiveAlerts();

  const groups = !selectedAreaId && activeAlerts.length > 1 ? groupedAlertsByArea() : [];
  if (groups.length > 1) {
    popup.classList.add("grouped");
    groups.forEach((group) => {
      const section = document.createElement("section");
      section.className = "popup-region-group";
      const title = document.createElement("div");
      title.className = "popup-region-title";
      title.textContent = `${group.areaName} · ${group.items.length}`;
      section.appendChild(title);
      group.items.forEach((alert) => section.appendChild(createPressRow(alert)));
      popupList.appendChild(section);
    });
    return;
  }

  popup.classList.remove("grouped");
  activeAlerts.forEach((alert) => popupList.appendChild(createPressRow(alert)));
}


function displayDeviceName(item) {
  return item.device_name;
}

function createAreaBadge(item) {
  if (selectedAreaId || !item.area_name) {
    return null;
  }
  const area = document.createElement("div");
  area.className = "area-badge";
  area.textContent = item.area_name;
  area.title = item.area_name;
  return area;
}

function soundUrlForEvent(event) {
  return soundUrlFromPath(event.sound_path);
}

function soundUrlForCurrentArea() {
  const area = areaItems.find((item) => item.area_id === selectedAreaId);
  return soundUrlFromPath(area?.sound_path);
}

function soundUrlFromPath(soundPath) {
  const rawUrl = soundPath
    ? (soundPath.startsWith('/') ? soundPath : `/${soundPath}`)
    : defaultSoundUrl;
  if (!rawUrl) {
    return null;
  }
  const url = new URL(rawUrl, window.location.origin);
  url.searchParams.set("audio_v", String(Date.now()));
  return `${url.pathname}${url.search}`;
}

function renderHistory(items) {
  historyList.innerHTML = "";

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "История пока пустая";
    historyList.appendChild(empty);
    return;
  }

  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "history-item";
    if (isFresh(item.pressed_at)) {
      row.classList.add("fresh");
    }

    const left = document.createElement("div");
    left.className = "history-copy";
    const area = createAreaBadge(item);
    if (area) {
      row.classList.add("with-area");
    }
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = displayDeviceName(item);

    const time = document.createElement("div");
    time.className = "time";
    time.textContent = `Нажата: ${item.pressed_at_display}`;

    if (area) {
      left.append(area);
    }
    left.append(name, time);
    row.append(left);
    row.tabIndex = 0;
    row.addEventListener("click", () => openDetails(item));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDetails(item);
      }
    });
    historyList.appendChild(row);
  });
}

function openDetails(item) {
  detailsName.textContent = displayDeviceName(item);
  detailsPressedAt.textContent = item.pressed_at_display || "—";
  detailsAckLabel.textContent = item.ack_button_text || ackButtonText;
  detailsAcknowledgedAt.textContent = item.acknowledged_at_display || "Не подтверждено";
  detailsModal.classList.remove("hidden");
}

function closeDetails() {
  detailsModal.classList.add("hidden");
}

function isFresh(pressedAt) {
  if (!pressedAt) {
    return false;
  }
  const timestamp = new Date(pressedAt).getTime();
  if (Number.isNaN(timestamp)) {
    return false;
  }
  return Date.now() - timestamp <= freshHighlightMs;
}

function updatePager() {
  pageInfo.textContent = `${historyPage} / ${totalHistoryPages}`;
  prevPageButton.disabled = historyPage <= 1;
  nextPageButton.disabled = historyPage >= totalHistoryPages;
}

function createPressRow(alert) {
  const row = document.createElement("div");
  row.className = "press";

  const left = document.createElement("div");
  left.className = "history-copy";
  const area = createAreaBadge(alert);
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = displayDeviceName(alert);

  const time = document.createElement("div");
  time.className = "time";
  time.textContent = `Нажата: ${alert.pressed_at_display}`;

  if (area) {
    left.append(area);
  }
  left.append(name, time);
  row.append(left);
  return row;
}

function showPopup() {
  popup.classList.remove("hidden");
}

function clearAlerts() {
  stopAlertSound();
  acknowledgeActiveAlerts();
  activeAlerts = [];
  renderPopup();
  popup.classList.add("hidden");
  clearPopupButton.textContent = ackButtonText;
}

async function acknowledgeActiveAlerts() {
  const ids = activeAlerts.map((item) => item.id).filter(Boolean);
  if (!ids.length) {
    return;
  }

  try {
    await fetch("/api/acknowledge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    await loadHistory();
  } catch {
    // История обновится следующим автообновлением.
  }
}

function stopAlertSound() {
  soundLoopActive = false;
  soundRepeatRemaining = null;
  if (soundStopTimer) {
    clearTimeout(soundStopTimer);
    soundStopTimer = null;
  }
  alertAudio.pause();
  alertAudio.currentTime = 0;
}

async function startSoundLoop() {
  if (!soundLoopActive || !soundEnabled || !alertAudio.src) {
    return;
  }

  if (soundRepeatRemaining !== null) {
    if (soundRepeatRemaining <= 0) {
      stopAlertSound();
      return;
    }
    soundRepeatRemaining -= 1;
  }

  try {
    alertAudio.currentTime = 0;
    await alertAudio.play();
  } catch {
    setSoundEnabled(false);
    stopAlertSound();
  }
}

function soundConfigForEvent(event) {
  const mode = event?.sound_repeat_mode === "count" ? "count" : "seconds";
  const seconds = Number(event?.sound_repeat_seconds) > 0 ? Number(event.sound_repeat_seconds) : soundPlaySeconds;
  const count = Number(event?.sound_repeat_count) > 0 ? Number(event.sound_repeat_count) : 3;
  return {
    url: soundUrlForEvent(event || {}),
    mode,
    seconds,
    count,
  };
}

async function playAlertSound(event) {
  const config = soundConfigForEvent(event);
  if (config.url && alertAudio.src !== new URL(config.url, window.location.origin).href) {
    alertAudio.src = config.url;
  }
  if (!soundEnabled || !alertAudio.src) {
    return;
  }

  stopAlertSound();
  soundLoopActive = true;
  if (config.mode === "count") {
    soundRepeatRemaining = config.count;
  } else {
    soundRepeatRemaining = null;
    soundStopTimer = setTimeout(stopAlertSound, config.seconds * 1000);
  }
  await startSoundLoop();
}

alertAudio.addEventListener("ended", startSoundLoop);

enableSoundButton.addEventListener("click", async () => {
  await unlockSound();
});
volumeSlider.addEventListener("input", () => {
  applyVolume(volumeSlider.value);
});
volumeInput.addEventListener("input", applyVolumeFromInput);
volumeInput.addEventListener("change", applyVolumeFromInput);
volumeToggleButton.addEventListener("click", () => {
  setVolumePanelOpen(volumePanel.classList.contains("hidden"));
});
volumeCloseButton.addEventListener("click", () => {
  setVolumePanelOpen(false);
});
volumeTestButton.addEventListener("click", testCurrentVolume);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setVolumePanelOpen(false);
  }
});
document.addEventListener("click", (event) => {
  if (!volumeWidget.contains(event.target)) {
    setVolumePanelOpen(false);
  }
});
clearPopupButton.addEventListener("click", clearAlerts);
refreshHistoryButton.addEventListener("click", refreshDashboard);
toggleBatteriesButton.addEventListener("click", () => {
  showAllBatteries = !showAllBatteries;
  renderBatteries();
});
batterySearchInput.addEventListener("input", renderBatteries);
detailsCloseButton.addEventListener("click", closeDetails);
detailsModal.addEventListener("click", (event) => {
  if (event.target === detailsModal) {
    closeDetails();
  }
});
batteryCloseButton.addEventListener("click", closeBatteryDetails);
batteryModal.addEventListener("click", (event) => {
  if (event.target === batteryModal) {
    closeBatteryDetails();
  }
});
prevPageButton.addEventListener("click", () => {
  if (historyPage > 1) {
    historyPage -= 1;
    loadHistory();
  }
});
nextPageButton.addEventListener("click", () => {
  if (historyPage < totalHistoryPages) {
    historyPage += 1;
    loadHistory();
  }
});

applyVolume(loadSavedVolume(), false);

async function initializePage() {
  await loadClientConfig();
  await loadAreas();
  await Promise.all([loadHistory(), loadBatteries()]);
  setSoundEnabled(true);
}

initializePage().finally(connectWebSocket);
setInterval(loadHistory, 30_000);
setInterval(loadBatteries, 60_000);
