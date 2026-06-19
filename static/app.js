const statusText = document.querySelector("#statusText");
const watchedCount = document.querySelector("#watchedCount");
const connectionBadge = document.querySelector("#connectionBadge");
const enableSoundButton = document.querySelector("#enableSoundButton");
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

let activeAlerts = [];
let soundEnabled = false;
let reconnectTimer = null;
let historyPage = 1;
let totalHistoryPages = 1;
let showAllBatteries = false;
let batteryTotal = 0;
let batteryItems = [];
let ackButtonText = "ФА";
let soundPlaySeconds = 25;
let syncHistoryHours = 24;
let soundStopTimer = null;
let soundLoopActive = false;
const historyPageSize = 100;
const freshHighlightMs = 5 * 60 * 1000;


function formatHours(hours) {
  return Number.isInteger(hours) ? `${hours} ч` : `${hours.toFixed(1)} ч`;
}

function setConnection(state) {
  connectionBadge.className = `dot ${state}`;
}

async function loadClientConfig() {
  const response = await fetch("/api/client-config");
  const config = await response.json();
  if (config.sound_url) {
    alertAudio.src = config.sound_url;
  }
  ackButtonText = config.ack_button_text || "ФА";
  clearPopupButton.textContent = ackButtonText;
  detailsAckLabel.textContent = ackButtonText;
  soundPlaySeconds = Number(config.sound_play_seconds) > 0 ? Number(config.sound_play_seconds) : 25;
  syncHistoryHours = Number(config.sync_history_hours) >= 0 ? Number(config.sync_history_hours) : 24;
  historySummary.textContent = syncHistoryHours > 0
    ? `Последние ${formatHours(syncHistoryHours)} + новые`
    : "Только новые нажатия";
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
    alertAudio.volume = 1;
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
  if (payload.status) {
    statusText.textContent = payload.status;
  }
  if (typeof payload.watched_count === "number") {
    watchedCount.textContent = payload.watched_count;
  }
  if (payload.type === "press" && Array.isArray(payload.events)) {
    addAlerts(payload.events);
  }
  if (payload.type === "history_sync") {
    loadHistory();
  }
}

async function loadHistory() {
  const response = await fetch(`/api/history?page=${historyPage}&page_size=${historyPageSize}`);
  const payload = await response.json();
  renderHistory(payload.items || []);
  historyPage = payload.page || 1;
  totalHistoryPages = payload.total_pages || 1;
  updatePager();
}

async function loadBatteries() {
  const response = await fetch("/api/batteries");
  const payload = await response.json();
  batteryTotal = payload.total || 0;
  batteryItems = payload.items || [];
  renderBatteries();
}

function renderBatteries() {
  batteryList.innerHTML = "";
  batteryList.classList.toggle("expanded", showAllBatteries || hasBatterySearch());
  toggleBatteriesButton.textContent = showAllBatteries ? "Скрыть" : "Показать все";

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
  if (percent <= 20) {
    return `Критично низкий заряд: ${percent}%`;
  }
  if (percent <= 40) {
    return `Низкий заряд: ${percent}%`;
  }
  return `Последний заряд: ${percent}%`;
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
  return row;
}

function addAlerts(events) {
  activeAlerts = [...activeAlerts, ...events];
  renderPopup();
  showPopup();
  playAlertSound();
  loadHistory();
}

function renderPopup() {
  popupList.innerHTML = "";

  const hasAlerts = activeAlerts.length > 0;
  popup.classList.toggle("many", activeAlerts.length > 3);
  popupCount.textContent = activeAlerts.length;
  popupTitle.textContent = activeAlerts.length > 1 ? "Несколько нажатий" : "Нажатие";

  activeAlerts.forEach((alert) => {
    popupList.appendChild(createPressRow(alert));
  });
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
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = item.device_name;

    const time = document.createElement("div");
    time.className = "time";
    time.textContent = `Нажата: ${item.pressed_at_display}`;

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
  detailsName.textContent = item.device_name;
  detailsPressedAt.textContent = item.pressed_at_display || "—";
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
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = alert.device_name;

  const time = document.createElement("div");
  time.className = "time";
  time.textContent = `Нажата: ${alert.pressed_at_display}`;

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

  try {
    alertAudio.currentTime = 0;
    await alertAudio.play();
  } catch {
    setSoundEnabled(false);
    stopAlertSound();
  }
}

async function playAlertSound() {
  if (!soundEnabled || !alertAudio.src) {
    return;
  }

  stopAlertSound();
  soundLoopActive = true;
  soundStopTimer = setTimeout(stopAlertSound, soundPlaySeconds * 1000);
  await startSoundLoop();
}

alertAudio.addEventListener("ended", startSoundLoop);

enableSoundButton.addEventListener("click", async () => {
  await unlockSound();
});
clearPopupButton.addEventListener("click", clearAlerts);
refreshHistoryButton.addEventListener("click", () => {
  loadHistory();
  loadBatteries();
});
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

Promise.all([loadClientConfig(), loadHistory(), loadBatteries()])
  .then(() => setSoundEnabled(true))
  .finally(connectWebSocket);
setInterval(loadHistory, 30_000);
setInterval(loadBatteries, 60_000);
