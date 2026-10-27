const auditFilters = document.querySelector('#auditFilters');
const auditList = document.querySelector('#adminActionList');
const auditStatus = document.querySelector('#auditStatus');
let auditPage = 1;
let auditSequence = 0;
let auditFilterTimer;

function auditNode(tag, text, className = '') {
  const node = document.createElement(tag); node.textContent = text; node.className = className; return node;
}

function auditValue(value, field) {
  if (value === null || value === undefined || value === '') return ['sound_path','sound_repeat_seconds','sound_repeat_count','history_sync_hours','ack_button_text'].includes(field) ? 'По умолчанию' : 'Не задано';
  if (['enabled','require_profile','allow_reassign_any','blocked','profile_locked','is_active'].includes(field)) return value ? 'Да' : 'Нет';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  if (field === 'role') return adminRoleNames[value] || value;
  if (field === 'sound_repeat_mode') return value === 'count' ? 'По количеству' : 'По времени';
  if (field === 'allowed_areas' && Array.isArray(value)) return value.map((id) => adminAreas.find((area) => area.area_id === id)?.display_name || id).join(', ') || 'Нет';
  return String(value);
}

async function loadAdminJournal() {
  const sequence = ++auditSequence;
  const params = new URLSearchParams(new FormData(auditFilters)); params.set('page',auditPage);
  auditStatus.textContent = 'Загрузка...';
  try {
    const payload = await adminRequest('/api/admin/audit?'+params);
    if (sequence !== auditSequence) return;
    const actor = auditFilters.elements.actor;
    const selected = actor.value;
    actor.replaceChildren(new Option('Все',''),...(payload.actors || []).map((name) => new Option(name,name)));
    actor.value = selected;
    auditList.replaceChildren(...payload.items.map((entry) => {
      const card = auditNode('article','','audit-entry');
      const header = document.createElement('header');
      header.append(auditNode('span',entry.username),auditNode('time',new Date(entry.created_at).toLocaleString('ru-RU').replace(',','')));
      card.append(header,auditNode('h3',entry.summary));
      const areas = (entry.area_ids || []).map((id) => adminAreas.find((area) => area.area_id === id)?.display_name || id);
      if (areas.length) card.append(auditNode('p',areas.join(', '),'admin-area-meta'));
      if (entry.target) card.append(auditNode('p',entry.target));
      if (entry.details) card.append(auditNode('p',entry.details,'admin-area-meta'));
      if (entry.changes?.length) {
        const diff = auditNode('dl','','audit-diff');
        entry.changes.forEach((change) => {
          const row = document.createElement('div');
          row.append(auditNode('dt',change.label),auditNode('dd',auditValue(change.before,change.field),'audit-before'),
            auditNode('span','→'),auditNode('dd',auditValue(change.after,change.field),'audit-after'));
          diff.append(row);
        });
        card.append(diff);
      }
      return card;
    }));
    auditStatus.textContent = payload.total ? `Записей: ${payload.total}` : 'По выбранным фильтрам записей нет';
    document.querySelector('#auditPage').textContent = `${payload.page} / ${payload.total_pages}`;
    document.querySelector('#auditPrev').disabled = auditPage <= 1;
    document.querySelector('#auditNext').disabled = auditPage >= payload.total_pages;
  } catch (error) { if (sequence === auditSequence) auditStatus.textContent = error.message; }
}

auditFilters.onsubmit = (event) => { event.preventDefault(); auditPage = 1; loadAdminJournal(); };
auditFilters.oninput = () => { clearTimeout(auditFilterTimer); auditFilterTimer = setTimeout(() => { auditPage = 1; loadAdminJournal(); },300); };
auditFilters.onreset = () => setTimeout(() => { auditPage = 1; loadAdminJournal(); },0);
document.querySelector('#auditPrev').onclick = () => { auditPage--; loadAdminJournal(); };
document.querySelector('#auditNext').onclick = () => { auditPage++; loadAdminJournal(); };
document.querySelector('#adminErrors').ontoggle = async (event) => {
  if (!event.target.open || currentAdmin?.role === 'location') return;
  try { renderAdminLogs((await adminRequest('/api/admin/logs?limit=120')).items || []); }
  catch (error) { adminNotice(error.message,true); }
};
function updateAuditAreas() {
  const select = auditFilters.elements.area_id;
  const previous = select.value;
  select.replaceChildren(new Option('Все доступные',''),...adminAreas.map((area) => new Option(area.display_name,area.area_id)));
  select.value = previous;
}
window.addEventListener('admin-ready',updateAuditAreas);
window.addEventListener('admin-areas-loaded',updateAuditAreas);
