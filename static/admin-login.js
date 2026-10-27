const form = document.querySelector('#adminLoginForm');
const password = document.querySelector('#loginPassword');
const status = document.querySelector('#loginStatus');
if (new URLSearchParams(location.search).has('changed')) status.textContent = 'Пароль изменён. Войдите снова.';
document.querySelector('#showLoginPassword').onchange = (event) => {
  password.type = event.target.checked ? 'text' : 'password';
};
form.onsubmit = async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true; button.textContent = 'Входим...'; status.textContent = '';
  try {
    const response = await fetch('/api/admin/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:document.querySelector('#loginUsername').value.trim(),password:password.value})});
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Проверьте логин и пароль');
    password.value = '';
    location.replace('/admin');
  } catch (error) {
    status.textContent = error.message || 'Сервер недоступен';
  } finally {
    button.disabled = false; button.textContent = 'Войти';
  }
};
