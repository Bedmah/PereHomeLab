function updateSupportLink(url) {
  const link = document.querySelector('#supportLink');
  link.hidden = !url;
  if (url) link.href = url;
  else link.removeAttribute('href');
}

fetch('/api/client-config', {cache: 'no-store'})
  .then((response) => response.ok ? response.json() : {})
  .then((config) => updateSupportLink(config.support_url))
  .catch(() => {});
