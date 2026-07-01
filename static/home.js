const regionGrid = document.querySelector('#regionGrid');
const statusText = document.querySelector('#statusText');
const connectionBadge = document.querySelector('#connectionBadge');

function setStatus(text, state = 'wait') {
  statusText.textContent = text;
  connectionBadge.className = `dot ${state}`;
}

function monitorAreaUrl(area) {
  if (!area.area_id) {
    return '/monitor';
  }
  return `/${encodeURIComponent(area.slug || area.area_id)}`;
}

function formatButtonAvailability(area) {
  const total = Number(area.button_count) || 0;
  const active = Number.isFinite(Number(area.active_button_count)) ? Number(area.active_button_count) : total;
  return `${active}/${total}`;
}

function createRegionCard(area) {
  const link = document.createElement('a');
  link.className = 'region-card';
  link.href = monitorAreaUrl(area);

  const title = document.createElement('div');
  title.className = 'region-title';
  title.textContent = area.display_name;

  const count = document.createElement('div');
  count.className = 'region-count';
  count.textContent = formatButtonAvailability(area);

  const label = document.createElement('div');
  label.className = 'region-label';
  label.textContent = area.area_id ? 'доступно в регионе' : 'доступно всего';

  link.append(title, count, label);
  return link;
}

async function loadRegions() {
  try {
    const response = await fetch('/api/areas');
    const payload = await response.json();
    regionGrid.innerHTML = '';
    regionGrid.appendChild(createRegionCard(payload.all || { area_id: '', display_name: 'Все', button_count: 0 }));
    (payload.items || []).forEach((area) => regionGrid.appendChild(createRegionCard(area)));
    setStatus('Готово', 'ok');
  } catch {
    setStatus('Ошибка загрузки', 'error');
  }
}

loadRegions();
setInterval(loadRegions, 30000);
