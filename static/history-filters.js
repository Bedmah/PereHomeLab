document.querySelector('#historyFilterButton').addEventListener('click', () => {
  historyPage = 1;
  loadHistory().catch(() => { document.querySelector('#historySummary').textContent = 'Не удалось загрузить историю'; });
});
document.querySelector('#historyQuery').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') document.querySelector('#historyFilterButton').click();
});
