const statusText = document.querySelector("#statusText");
const watchedCount = document.querySelector("#watchedCount");
const connectionBadge = document.querySelector("#connectionBadge");
const systemStatusButton = document.querySelector("#systemStatusButton");
const statusMeta = document.querySelector("#statusMeta");
const systemStatusModal = document.querySelector("#systemStatusModal");
const systemStatusCloseButton = document.querySelector("#systemStatusCloseButton");
const systemOverallDot = document.querySelector("#systemOverallDot");
const systemStatusTitle = document.querySelector("#systemStatusTitle");
const systemStatusUpdated = document.querySelector("#systemStatusUpdated");
const systemConnectionList = document.querySelector("#systemConnectionList");
const systemUnavailableBlock = document.querySelector("#systemUnavailableBlock");
const systemUnavailableTitle = document.querySelector("#systemUnavailableTitle");
const systemUnavailableList = document.querySelector("#systemUnavailableList");
const systemErrorBlock = document.querySelector("#systemErrorBlock");
const systemCheckButton = document.querySelector("#systemCheckButton");
const systemCheckResult = document.querySelector("#systemCheckResult");
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
const detailsArea = document.querySelector("#detailsArea");
const callComments = document.querySelector("#callComments");
const commentsCount = document.querySelector("#commentsCount");
const callAssignment = document.querySelector('#callAssignment');
const assignmentLabel = document.querySelector('#assignmentLabel');
const assignmentName = document.querySelector('#assignmentName');
const assignmentPlatform = document.querySelector('#assignmentPlatform');
const assignmentTime = document.querySelector('#assignmentTime');
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
let soundAlertId = null;
const acknowledgedAlertIds = new Set();
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
let browserSocketConnected = false;
let latestSystemStatus = null;
const historyPageSize = 100;
const freshHighlightMs = 5 * 60 * 1000;
const defaultVolumePercent = 100;
const volumeStorageKey = "perehomelab-alert-volume-v2";
const audioCacheVersion = String(Date.now());
let audioProbePromise = null;
let audioGeneration = 0;
let audioFailure = '';
let historyInFlight = null;
let historyRefreshTimer = null;
let historyRenderSignature = '';
let alertSyncInFlight = false;
let detailsRequest = null;
let detailsRenderSignature = '';
let clientConfigReady = false;

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

function parseStatusDate(value) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeTime(value) {
  const date = parseStatusDate(value);
  if (!date) {
    return "нет данных";
  }
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 10) return "только что";
  if (seconds < 60) return `${seconds} сек. назад`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин. назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч. назад`;
  return `${Math.floor(hours / 24)} дн. назад`;
}

function shortStatusDate(value) {
  const date = parseStatusDate(value);
  if (!date) return "нет данных";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function uptimeText(value) {
  const date = parseStatusDate(value);
  if (!date) return "нет данных";
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  if (days) return `${days} дн. ${hours} ч.`;
  if (hours) return `${hours} ч. ${minutes % 60} мин.`;
  return `${minutes} мин.`;
}

function statusClass(state) {
  if (state === "ok") return "ok";
  if (state === "warning" || state === "starting") return "wait";
  return "error";
}

function updateTopSystemStatus() {
  if (!latestSystemStatus) {
    statusText.textContent = browserSocketConnected ? "Проверка HA" : "Подключение";
    statusMeta.textContent = "Получаем состояние системы";
    setConnection(browserSocketConnected ? "wait" : "error");
    return;
  }

  if (!browserSocketConnected) {
    statusText.textContent = "Нет канала событий";
    statusMeta.textContent = "Переподключаем браузер к PereHomeLab";
    setConnection("error");
    return;
  }

  const state = latestSystemStatus.overall || "starting";
  if (state === "ok") {
    statusText.textContent = "HA подключен";
  } else if (state === "warning") {
    statusText.textContent = "Есть предупреждение";
  } else if (state === "error") {
    statusText.textContent = "Проблема с HA";
  } else {
    statusText.textContent = "Проверка HA";
  }
  const active = Number(latestSystemStatus.active_watched_count) || 0;
  const total = Number(latestSystemStatus.watched_count) || 0;
  statusMeta.textContent = `Кнопки ${active}/${total} · проверка ${relativeTime(latestSystemStatus.last_api_check_at)}`;
  setConnection(statusClass(state));
}

function appendSystemRow(label, state, value, detail = "") {
  const row = document.createElement("div");
  row.className = "system-connection-row";
  const dot = document.createElement("span");
  dot.className = `dot ${statusClass(state)}`;
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = label;
  const text = document.createElement("span");
  text.textContent = value;
  copy.append(title, text);
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    copy.appendChild(small);
  }
  row.append(dot, copy);
  systemConnectionList.appendChild(row);
}

function renderSystemStatus() {
  updateTopSystemStatus();
  if (!latestSystemStatus || systemStatusModal.classList.contains('hidden')) return;

  const system = latestSystemStatus;
  const visibleOverall = browserSocketConnected ? system.overall : "error";
  systemOverallDot.className = `dot ${statusClass(visibleOverall)}`;
  systemStatusTitle.textContent = browserSocketConnected
    ? (system.summary || "Состояние системы")
    : "Нет канала событий в браузер";
  systemStatusUpdated.textContent = `Обновлено: ${relativeTime(system.last_api_check_at)}`;
  systemConnectionList.innerHTML = "";

  appendSystemRow(
    "Браузер → PereHomeLab",
    browserSocketConnected ? "ok" : "error",
    browserSocketConnected ? "подключено" : "нет связи",
  );
  appendSystemRow(
    "Home Assistant API",
    system.ha_api_ok === true ? "ok" : system.ha_api_ok === false ? "error" : "starting",
    system.ha_api_ok === true ? "доступен" : system.ha_api_ok === false ? "не отвечает" : "проверяется",
    `Последняя проверка: ${relativeTime(system.last_api_check_at)}`,
  );
  appendSystemRow(
    "Канал событий HA",
    system.ha_ws_connected ? "ok" : "error",
    system.ha_ws_connected ? "подключён" : "переподключается",
    `Переподключений: ${Number(system.reconnect_count) || 0}`,
  );
  const tuyaEntriesTotal = Number(system.tuya_entries_total) || 0;
  const tuyaEntriesLoaded = Number(system.tuya_entries_loaded) || 0;
  appendSystemRow(
    "Интеграция Tuya в HA",
    system.tuya_integration_loaded === true ? "ok" : system.tuya_integration_loaded === false ? "error" : "starting",
    system.tuya_integration_loaded === true
      ? `загружена (${tuyaEntriesLoaded}/${tuyaEntriesTotal})`
      : system.tuya_integration_loaded === false
        ? `загружено ${tuyaEntriesLoaded} из ${tuyaEntriesTotal}`
        : "проверяется",
    `Последняя проверка: ${relativeTime(system.last_tuya_check_at)}`,
  );
  const tuyaEndpoints = system.tuya_endpoints || {};
  ["apigw.tuyaeu.com", "apigw.iotbing.com"].forEach((host) => {
    const endpoint = tuyaEndpoints[host] || {};
    const endpointState = endpoint.reachable === true
      ? (endpoint.recent_error ? "warning" : "ok")
      : endpoint.reachable === false ? "error" : "starting";
    const endpointDetail = endpoint.last_error
      ? `${endpoint.last_error}: ${shortStatusDate(endpoint.last_error_at)}`
      : "Свежих ошибок по адресу в журнале HA нет";
    appendSystemRow(
      `Tuya · ${host}`,
      endpointState,
      endpoint.reachable === true
        ? (endpoint.recent_error ? "доступен, но недавно была ошибка" : "доступен")
        : endpoint.reachable === false ? "не отвечает" : "проверяется",
      endpointDetail,
    );
  });
  appendSystemRow(
    "История нажатий",
    system.history_sync_error ? "error" : system.history_stale ? "warning" : "ok",
    system.history_sync_error ? "ошибка синхронизации" : `проверена ${relativeTime(system.last_history_sync_at)}`,
    `Последняя полная проверка: ${shortStatusDate(system.last_history_sync_at)}`,
  );
  const unavailableCount = Number(system.unavailable_count) || 0;
  appendSystemRow(
    "Кнопки",
    unavailableCount ? "warning" : "ok",
    `доступно ${Number(system.active_watched_count) || 0} из ${Number(system.watched_count) || 0}`,
    unavailableCount ? `Недоступно: ${unavailableCount}` : "Все обнаруженные кнопки доступны",
  );
  appendSystemRow(
    "Последнее нажатие",
    system.last_press_at ? "ok" : "starting",
    system.last_press_name || "пока нет данных",
    system.last_press_at ? shortStatusDate(system.last_press_at) : "",
  );
  appendSystemRow(
    "PereHomeLab",
    "ok",
    `версия ${system.app_version || "—"}`,
    `Работает: ${uptimeText(system.started_at)}`,
  );

  const unavailableItems = system.unavailable_entities || [];
  systemUnavailableBlock.classList.toggle("hidden", unavailableItems.length === 0);
  systemUnavailableTitle.textContent = `Недоступные кнопки (${unavailableCount})`;
  systemUnavailableList.innerHTML = "";
  unavailableItems.forEach((item) => {
    const line = document.createElement("div");
    line.textContent = item.area_name ? `${item.name} · ${item.area_name}` : item.name;
    systemUnavailableList.appendChild(line);
  });

  const errorText = system.last_error
    || system.history_sync_error
    || system.tuya_check_error
    || (system.tuya_recent_error ? system.tuya_last_error : "")
    || "";
  systemErrorBlock.classList.toggle("hidden", !errorText);
  systemErrorBlock.textContent = errorText ? `Последняя ошибка: ${shortHaError(errorText)}` : "";
}

async function loadSystemStatus() {
  try {
    if (!clientConfigReady) {
      await loadClientConfig();
      await unlockSound();
    }
    const response = await fetch("/api/system-status", { cache: "no-store" });
    if (!response.ok) throw new Error("status unavailable");
    latestSystemStatus = await response.json();
    renderSystemStatus();
    await syncActiveAlerts();
  } catch {
    updateTopSystemStatus();
  }
}

function openSystemStatus() {
  systemStatusModal.classList.remove("hidden");
  loadSystemStatus();
}

function closeSystemStatus() {
  systemStatusModal.classList.add("hidden");
}

async function checkSystemNow() {
  systemCheckButton.disabled = true;
  systemCheckButton.textContent = "Проверяем...";
  systemCheckResult.textContent = "Проверяем HA, Tuya, кнопки и историю";
  try {
    const response = await fetch("/api/system-status/check", { method: "POST" });
    if (!response.ok) throw new Error("check failed");
    latestSystemStatus = await response.json();
    renderSystemStatus();
    systemCheckResult.textContent = "Проверка завершена";
  } catch {
    systemCheckResult.textContent = "Не удалось выполнить проверку";
  } finally {
    systemCheckButton.disabled = false;
    systemCheckButton.textContent = "Проверить сейчас";
  }
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
  let rawValue;
  try { rawValue = localStorage.getItem(volumeStorageKey); } catch { return defaultVolumePercent; }
  if (rawValue === null || rawValue.trim() === "") {
    return defaultVolumePercent;
  }
  const savedValue = Number(rawValue);
  if (!Number.isFinite(savedValue)) {
    return defaultVolumePercent;
  }
  return Math.max(0, Math.min(100, Math.round(savedValue)));
}

function applyVolume(percent, save = true) {
  const numericPercent = Number(percent);
  const cleanPercent = Math.max(
    0,
    Math.min(100, Math.round(Number.isFinite(numericPercent) ? numericPercent : defaultVolumePercent)),
  );
  alertAudio.volume = cleanPercent / 100;
  volumeSlider.value = String(cleanPercent);
  volumeInput.value = String(cleanPercent);
  volumeValue.textContent = `${cleanPercent}%`;
  volumeBadge.textContent = `${cleanPercent}%`;
  if (save) {
    try { localStorage.setItem(volumeStorageKey, String(cleanPercent)); } catch {}
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
  soundPlaySeconds = Number(config.sound_play_seconds) > 0 ? Number(config.sound_play_seconds) : 25;
  syncHistoryHours = Number(config.sync_history_hours) >= 0 ? Number(config.sync_history_hours) : 24;
  clientConfigReady = true;
}

function setSoundEnabled(enabled) {
  soundEnabled = enabled;
  enableSoundButton.textContent = enabled ? "Звук включён" : "Включить звук";
  enableSoundButton.classList.toggle("enabled", enabled);
  enableSoundButton.classList.toggle('blocked', !enabled);
  enableSoundButton.setAttribute('aria-label', enabled ? 'Звук уведомлений включён' : 'Разрешить звук уведомлений');
}

function silentAudioUrl() {
  const buffer = new ArrayBuffer(1644);
  const view = new DataView(buffer);
  const write = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 1636, true); write(8, 'WAVEfmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 8000, true); view.setUint32(28, 16000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, 'data'); view.setUint32(40, 1600, true);
  return URL.createObjectURL(new Blob([buffer], {type: 'audio/wav'}));
}

function unlockSound() {
  if (!clientConfigReady) return Promise.resolve(false);
  if (audioProbePromise) return audioProbePromise;
  if (soundLoopActive || volumeTestButton.disabled || !alertAudio.paused) return Promise.resolve(soundEnabled);
  const source = alertAudio.getAttribute('src');
  const probe = silentAudioUrl();
  alertAudio.src = probe;
  alertAudio.volume = 1;
  audioProbePromise = (async () => {
    try {
      await alertAudio.play();
      audioFailure = '';
      setSoundEnabled(true);
      return true;
    } catch (error) {
      audioFailure = error.name === 'NotAllowedError' ? 'blocked' : 'error';
      setSoundEnabled(false);
      enableSoundButton.textContent = audioFailure === 'blocked' ? 'Разрешить звук' : 'Проверить звук';
      enableSoundButton.title = audioFailure === 'blocked' ? 'Браузер ожидает нажатия на страницу для разрешения звука' : 'Не удалось подготовить звук';
      return false;
    } finally {
      alertAudio.pause();
      alertAudio.currentTime = 0;
      if (source) alertAudio.src = source;
      else alertAudio.removeAttribute('src');
      applyVolume(volumeSlider.value, false);
      URL.revokeObjectURL(probe);
      audioProbePromise = null;
    }
  })();
  return audioProbePromise;
}

async function testCurrentVolume() {
  if (audioProbePromise) await audioProbePromise;
  if (activeAlerts.length) {
    if (!soundEnabled) await unlockSound();
    if (soundEnabled && !soundLoopActive) playAlertSound(activeAlerts[0]);
    return;
  }
  stopAlertSound();
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

  applyVolume(volumeSlider.value, false);
  const generation = audioGeneration;
  volumeTestButton.disabled = true;
  volumeTestButton.textContent = "Играет...";
  try {
    alertAudio.currentTime = 0;
    await alertAudio.play();
    if (generation === audioGeneration) setSoundEnabled(true);
  } catch (error) {
    if (generation === audioGeneration && error.name !== 'AbortError') setSoundEnabled(false);
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
    browserSocketConnected = true;
    renderSystemStatus();
    socket.send("hello");
  });

  socket.addEventListener("message", (event) => {
    try {
      handleServerMessage(JSON.parse(event.data));
    } catch {
      statusMeta.textContent = "Получен некорректный ответ сервера";
    }
  });

  socket.addEventListener("close", () => {
    browserSocketConnected = false;
    renderSystemStatus();
    reconnectTimer = setTimeout(connectWebSocket, 2000);
  });

  socket.addEventListener("error", () => {
    browserSocketConnected = false;
    renderSystemStatus();
  });
}

function handleServerMessage(payload) {
  if (payload.system) {
    latestSystemStatus = payload.system;
    renderSystemStatus();
  } else if (payload.last_error) {
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
    scheduleHistoryRefresh();
  }
  if (payload.type === "acknowledged") {
    applyCallChanges(payload.events || []);
    scheduleHistoryRefresh();
  }
  if (payload.type === "call_updated") {
    applyCallChanges(payload.events || []);
    scheduleHistoryRefresh();
  }
  if (payload.type === "state") {
    syncActiveAlerts();
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
    } else {
      selectedAreaId = "__unknown_region__";
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
  const name = document.createElement("span");
  name.textContent = area.display_name || area.area_id || "Без названия";
  const availability = document.createElement("strong");
  availability.textContent = formatButtonAvailability(area.active_button_count, area.button_count);
  button.append(name, availability);
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
  const url = areaUrl("/api/history", { page: historyPage, page_size: historyPageSize,
    q: document.querySelector('#historyQuery')?.value || '', state: document.querySelector('#historyState')?.value || '',
    date_from: document.querySelector('#historyFrom')?.value || '', date_to: document.querySelector('#historyTo')?.value || '' });
  if (historyInFlight?.url === url) return historyInFlight.promise;
  historyInFlight?.controller.abort();
  const request = {url, controller: new AbortController()};
  historyInFlight = request;
  request.promise = (async () => {
    try {
      const response = await fetch(url, {signal: request.controller.signal});
      if (!response.ok) throw new Error('Не удалось загрузить историю');
      const payload = await response.json();
      if (historyInFlight !== request) return;
      renderHistory(payload.items || []);
      historyPage = payload.page || 1;
      totalHistoryPages = payload.total_pages || 1;
      updatePager();
    } catch (error) {
      if (error.name !== 'AbortError') throw error;
    } finally {
      if (historyInFlight === request) historyInFlight = null;
    }
  })();
  return request.promise;
}

function scheduleHistoryRefresh() {
  if (historyRefreshTimer) return;
  historyRefreshTimer = setTimeout(async () => {
    historyRefreshTimer = null;
    if (historyInFlight) await historyInFlight.promise.catch(() => {});
    loadHistory().catch(() => {});
  }, 150);
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

const callRevisions = new Map();
let detailsEventId = null;

function applyCallChanges(events) {
  const silenced = new Set();
  for (const event of events) {
    if ((callRevisions.get(event.id) ?? -1) >= (event.revision || 0)) continue;
    callRevisions.set(event.id, event.revision || 0);
    if (event.sound_silenced) silenced.add(event.id);
    if (detailsEventId === event.id && !detailsModal.classList.contains('hidden')) {
      renderAssignment(event);
      openDetails(event);
    }
  }
  if (silenced.size) removeAcknowledgedAlerts([...silenced]);
  while (callRevisions.size > 2000) callRevisions.delete(callRevisions.keys().next().value);
}

function addAlerts(events) {
  const existingIds = new Set(activeAlerts.map((event) => event.id));
  const visibleEvents = events.filter((event) => (!selectedAreaId || event.area_id === selectedAreaId)
    && !existingIds.has(event.id) && !acknowledgedAlertIds.has(event.id));
  if (!visibleEvents.length) {
    return;
  }
  activeAlerts = [...activeAlerts, ...visibleEvents];
  visibleEvents.forEach((event) => callRevisions.set(event.id, event.revision || 0));
  renderPopup();
  showPopup();
  if (!soundLoopActive) {
    playAlertSound(visibleEvents[0]);
  }
  scheduleHistoryRefresh();
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
  url.searchParams.set("audio_v", audioCacheVersion);
  return `${url.pathname}${url.search}`;
}

function renderHistory(items) {
  const signature = JSON.stringify([selectedAreaId, items.map((item) => [item.id, item.device_name, item.area_name,
    item.pressed_at_display, isFresh(item.pressed_at), item.call_state, item.assignee_short, item.assignee_name])]);
  if (signature === historyRenderSignature) return;
  historyRenderSignature = signature;
  historyList.innerHTML = "";
  const fragment = document.createDocumentFragment();

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
    time.textContent = `Нажата: ${item.pressed_at_display || '—'}`;

    if (area) {
      left.append(area);
    }
    left.append(name, time);
    if (item.call_state === 'accepted') {
      const assignee = document.createElement('div');
      assignee.className = 'history-assignee';
      assignee.textContent = 'Пошёл: ' + (item.assignee_short || item.assignee_name || 'Сотрудник');
      assignee.title = assignee.textContent;
      left.append(assignee);
    }
    row.append(left);
    row.tabIndex = 0;
    row.addEventListener("click", () => openDetails(item));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDetails(item);
      }
    });
    fragment.appendChild(row);
  });
  historyList.appendChild(fragment);
}

async function openDetails(item) {
  const changed = detailsEventId !== item.id;
  detailsEventId = item.id;
  if (changed || item.device_name) detailsName.textContent = displayDeviceName(item) || 'Переговорка';
  if (changed || item.pressed_at_display) detailsPressedAt.textContent = item.pressed_at_display || '—';
  if (changed || item.area_name) detailsArea.textContent = item.area_name || selectedAreaName();
  detailsModal.classList.remove("hidden");
  detailsRequest?.abort();
  const controller = new AbortController();
  detailsRequest = controller;
  if (changed) {
    callAssignment.classList.remove('accepted');
    assignmentLabel.textContent = 'Ответственный';
    assignmentName.textContent = 'Проверяем статус…';
    assignmentPlatform.hidden = true;
    assignmentTime.hidden = true;
    detailsRenderSignature = '';
    commentsCount.textContent = '0';
    callComments.textContent = 'Загрузка комментариев…';
  }
  try {
    const response = await fetch(`/api/history/${encodeURIComponent(item.id)}`, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error('Не удалось загрузить комментарии');
    const payload = (await response.json()).item;
    if (detailsRequest !== controller || detailsEventId !== item.id) return;
    renderAssignment(payload);
    renderComments(payload.comments || []);
  } catch (error) {
    if (detailsRequest === controller && error.name !== 'AbortError') {
      callComments.textContent = error.message;
      assignmentName.textContent = 'Статус временно недоступен';
      assignmentPlatform.hidden = true;
      assignmentTime.hidden = true;
      callAssignment.classList.remove('accepted');
    }
  } finally {
    if (detailsRequest === controller) detailsRequest = null;
  }
}

function renderAssignment(item) {
  const accepted = item.call_state === 'accepted';
  callAssignment.classList.toggle('accepted', accepted);
  assignmentLabel.textContent = accepted ? 'Пошёл' : 'Статус';
  assignmentName.textContent = accepted ? (item.assignee_details || item.assignee_name || item.acknowledged_by || 'Сотрудник') : 'Ожидает сотрудника';
  const platform = item.assignee_source_label || item.assignee_source || item.acknowledged_source;
  assignmentPlatform.textContent = platform || '';
  assignmentPlatform.hidden = !accepted || !platform;
  assignmentTime.textContent = item.acknowledged_at_display || '';
  assignmentTime.dateTime = item.acknowledged_at || '';
  assignmentTime.hidden = !accepted || !item.acknowledged_at_display;
}

function renderComments(comments) {
  const signature = JSON.stringify(comments);
  if (signature === detailsRenderSignature) return;
  detailsRenderSignature = signature;
  commentsCount.textContent = String(comments.length);
  const fragment = document.createDocumentFragment();
  if (!comments.length) {
    const empty = document.createElement('p');
    empty.className = 'comments-empty';
    empty.textContent = 'Комментариев пока нет';
    fragment.append(empty);
  }
  for (const comment of comments) {
    const card = document.createElement('article');
    card.className = 'comment-card';
    const header = document.createElement('div');
    header.className = 'comment-header';
    const author = document.createElement('strong');
    author.textContent = comment.author_name || 'Сотрудник';
    const time = document.createElement('time');
    time.textContent = comment.created_at_display || '';
    if (comment.created_at) time.dateTime = comment.created_at;
    const body = document.createElement('p');
    body.className = 'comment-body';
    body.textContent = comment.body;
    header.append(author, time);
    card.append(header, body);
    fragment.append(card);
  }
  const scroll = callComments.scrollTop;
  callComments.replaceChildren(fragment);
  callComments.scrollTop = scroll;
}

function closeDetails() {
  detailsEventId = null;
  detailsRequest?.abort();
  detailsRequest = null;
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
  time.textContent = `Нажата: ${alert.pressed_at_display || '—'}`;

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

async function clearAlerts() {
  const ids = activeAlerts.map((item) => item.id).filter(Boolean);
  if (!ids.length || clearPopupButton.disabled) {
    return;
  }
  clearPopupButton.disabled = true;
  stopAlertSound();
  try {
    const response = await fetch("/api/acknowledge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!response.ok) throw new Error("Не удалось подтвердить вызов");
    applyCallChanges((await response.json()).events || [], true);
    await loadHistory();
  } catch {
    clearPopupButton.title = "Не удалось сохранить подтверждение. Нажмите ещё раз";
    if (activeAlerts.length && !soundLoopActive) playAlertSound(activeAlerts[0]);
  } finally {
    clearPopupButton.disabled = false;
    if (activeAlerts.length && !soundLoopActive) playAlertSound(activeAlerts[0]);
  }
}

function removeAcknowledgedAlerts(ids) {
  ids.forEach((id) => acknowledgedAlertIds.add(id));
  while (acknowledgedAlertIds.size > 2000) {
    acknowledgedAlertIds.delete(acknowledgedAlertIds.values().next().value);
  }
  const removedSound = ids.includes(soundAlertId);
  const remaining = activeAlerts.filter((event) => !acknowledgedAlertIds.has(event.id));
  if (remaining.length === activeAlerts.length) return;
  activeAlerts = remaining;
  renderPopup();
  if (!activeAlerts.length) {
    stopAlertSound();
    popup.classList.add("hidden");
  } else if (removedSound) {
    stopAlertSound();
    playAlertSound(activeAlerts[0]);
  }
}

async function syncActiveAlerts() {
  if (alertSyncInFlight) return;
  const ids = [...new Set([...activeAlerts.map((event) => event.id), ...[...acknowledgedAlertIds].slice(-500),
    ...(detailsEventId ? [detailsEventId] : [])])];
  if (!ids.length) return;
  alertSyncInFlight = true;
  try {
    const response = await fetch("/api/alerts/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, revisions: Object.fromEntries(ids.map((id) => [id, callRevisions.get(id) ?? -1])) }),
    });
    if (response.ok) {
      const payload = await response.json();
      applyCallChanges(payload.released_events || []);
      applyCallChanges(payload.events || []);
    }
  } catch {} finally { alertSyncInFlight = false; }
}

function stopAlertSound() {
  audioGeneration += 1;
  soundAlertId = null;
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

  const generation = audioGeneration;
  try {
    alertAudio.currentTime = 0;
    await alertAudio.play();
  } catch (error) {
    if (generation !== audioGeneration || error.name === 'AbortError') return;
    audioFailure = error.name === 'NotAllowedError' ? 'blocked' : 'error';
    setSoundEnabled(false);
    enableSoundButton.textContent = audioFailure === 'blocked' ? 'Разрешить звук' : 'Ошибка звука';
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
  const generation = audioGeneration;
  if (audioProbePromise) {
    await audioProbePromise;
    if (generation !== audioGeneration) return;
  }
  const config = soundConfigForEvent(event);
  if (!soundEnabled || !config.url) return;
  stopAlertSound();
  if (config.url && alertAudio.src !== new URL(config.url, window.location.origin).href) {
    alertAudio.src = config.url;
  }
  if (!soundEnabled || !alertAudio.src) {
    return;
  }

  soundLoopActive = true;
  soundAlertId = event?.id || null;
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
  if (soundEnabled && activeAlerts.length && !soundLoopActive) playAlertSound(activeAlerts[0]);
});

function unlockOnInteraction(event) {
  if (!event.isTrusted || soundEnabled || event.target.closest('#clearPopupButton, #volumeTestButton')) return;
  if (event.type === 'keydown' && (event.ctrlKey || event.altKey || event.metaKey)) return;
  unlockSound().then(() => {
    if (soundEnabled && activeAlerts.length && !soundLoopActive) playAlertSound(activeAlerts[0]);
  });
}
document.addEventListener('pointerup', unlockOnInteraction, {passive: true});
document.addEventListener('keydown', unlockOnInteraction);
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
    closeSystemStatus();
    closeDetails();
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
systemStatusButton.addEventListener("click", openSystemStatus);
systemStatusCloseButton.addEventListener("click", closeSystemStatus);
systemCheckButton.addEventListener("click", checkSystemNow);
systemStatusModal.addEventListener("click", (event) => {
  if (event.target === systemStatusModal) {
    closeSystemStatus();
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
  const audioReady = unlockSound();
  await loadAreas();
  await Promise.all([loadHistory(), loadBatteries(), loadSystemStatus(), audioReady]);
}

initializePage().catch(() => { statusMeta.textContent = 'Не удалось загрузить часть данных. Повторите обновление'; }).finally(() => {
  connectWebSocket();
  if (new URLSearchParams(window.location.search).get("system_status") === "open") {
    setTimeout(openSystemStatus, 500);
  }
});
setInterval(() => loadHistory().catch(() => {}), 30_000);
setInterval(loadBatteries, 60_000);
setInterval(loadSystemStatus, 15_000);

requestAnimationFrame(() => document.body.classList.add("ui-ready"));

let pressedTarget = null;
function releasePressedTarget() {
  pressedTarget?.classList.remove('ui-pressed');
  pressedTarget = null;
}
document.addEventListener("pointerdown", (event) => {
  releasePressedTarget();
  const target = event.target.closest("button, a, .battery-item, .history-item");
  if (!target || target.matches(":disabled")) return;
  target.classList.add("ui-pressed");
  pressedTarget = target;
});
document.addEventListener('pointerup', releasePressedTarget);
document.addEventListener('pointercancel', releasePressedTarget);
window.addEventListener('blur', releasePressedTarget);
