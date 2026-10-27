import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import secrets

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.cities import RESERVED_AREA_SLUGS, CityStore, slugify
from app.battery import collect_batteries
from app.config import PROJECT_ROOT, get_live_ack_button_text, get_live_sound_play_seconds, load_config
from app.ha_client import HomeAssistantClient
from app.history import HistoryStore
from app.logging_config import configure_logging
from app.users import UserStore
from app.admin_access import require_area, require_global, require_user_control, visible_areas
from app.admin_sessions import AdminSessions
from app.admin_audit import AdminAudit, audit_change
from app.watcher import HomeAssistantWatcher
from app.telegram_service import TelegramError, TelegramService

logger = configure_logging()
config = load_config()
logger.info(
    'Config loaded: version=%s, HA_URL=%s, token_length=%s, auto_discover=%s, domain=%s, device_class=%s, trigger=%s, history_sync_hours=%s, db=%s, sound=%s, app_download=%s/%s',
    config.app_version,
    config.ha_url,
    len(config.ha_token),
    config.ha_auto_discover,
    config.ha_entity_domain,
    config.ha_device_class or '-',
    config.ha_trigger_to,
    config.ha_sync_history_hours,
    config.history_db_path,
    config.button_sound,
    config.app_download_dir,
    config.app_download_filename,
)
history = HistoryStore(config.history_db_path)
cities = CityStore(config.history_db_path)
users = UserStore(config.history_db_path)
users.ensure_owner(config.admin_username, config.admin_password)
sessions = AdminSessions(users)
audit = AdminAudit(users)
ha_client = HomeAssistantClient(config)
watcher = HomeAssistantWatcher(config, ha_client, history, cities, logger)
telegram = TelegramService(config, history, cities, logger, watcher.broadcast_acknowledged)
watcher.on_press = telegram.enqueue


def call_event(item, city_settings=None):
    event = dict(item)
    if city_settings is None:
        city_settings = {city.area_id: city for city in cities.list(include_disabled=True)}
    city = city_settings.get(item.get('area_id'))
    if city:
        for field in ('sound_path', 'sound_repeat_mode', 'sound_repeat_seconds', 'sound_repeat_count', 'ack_button_text'):
            event[field] = getattr(city, field)
        event['area_name'] = city.display_name
    return event


def call_events(items):
    if not items:
        return []
    settings = {city.area_id: city for city in cities.list(include_disabled=True)}
    return [call_event(item, settings) for item in items]


async def broadcast_call_change(items, action):
    await watcher._broadcast({'type': 'call_updated', 'action': action, 'events': call_events(items)})


telegram.on_change = broadcast_call_change




def list_sound_files() -> list[dict]:
    sound_dir = PROJECT_ROOT / 'sound'
    sound_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(sound_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {'.mp3', '.wav', '.ogg'}:
            continue
        items.append({
            'name': path.name,
            'path': f'sound/{path.name}',
            'url': f'/sound/{path.name}',
            'size': path.stat().st_size,
        })
    return items


def safe_sound_filename(filename: str) -> str:
    allowed = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
    clean = ''.join(char for char in Path(filename).name if char in allowed).strip().replace(' ', '_')
    if not clean:
        raise HTTPException(status_code=400, detail='Invalid sound filename')
    if Path(clean).suffix.lower() not in {'.mp3', '.wav', '.ogg'}:
        raise HTTPException(status_code=400, detail='Only mp3, wav and ogg files are allowed')
    return clean




def resolve_app_download_path() -> Path | None:
    candidates = [
        config.app_download_dir / config.app_download_filename,
        PROJECT_ROOT / 'static' / 'downloads' / config.app_download_filename,
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None

def recent_error_log_items(limit: int) -> list[dict]:
    markers = ('ERROR', 'WARNING', 'Traceback', 'Home Assistant', 'HA ')
    items: list[dict] = []
    for log_name in ('app.log', 'access.log'):
        path = PROJECT_ROOT / 'logs' / log_name
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError as exc:
            items.append({'source': log_name, 'line': f'Не удалось прочитать лог: {exc}'})
            continue
        for line in lines[-2000:]:
            if any(marker in line for marker in markers):
                items.append({'source': log_name, 'line': line})
    return items[-limit:]


class AcknowledgeRequest(BaseModel):
    ids: list[str] = Field(max_length=1000)
    revisions: dict[str, int] = Field(default_factory=dict, max_length=1000)


class TelegramSettingsRequest(BaseModel):
    enabled: bool = False
    token: str | None = None
    require_profile: bool | None = None
    allow_reassign_any: bool | None = None


class EmployeeUpdateRequest(BaseModel):
    login: str = Field(max_length=100)
    full_name: str = Field(max_length=150)
    blocked: bool = False
    profile_locked: bool | None = None
    allowed_areas: list[str] | None = Field(default=None, max_length=500)
    telegram_user_id: str | None = Field(default=None, max_length=20)


class EmployeeCreateRequest(BaseModel):
    login: str = Field(max_length=100)
    full_name: str = Field(max_length=150)
    telegram_user_id: str | None = Field(default=None, max_length=20)


class TelegramSetupRequest(BaseModel):
    setup_id: str


security = HTTPBasic(auto_error=False)


def _request_addr(request: Request) -> str | None:
    return request.client.host if request.client else None


def _request_user_agent(request: Request) -> str | None:
    return request.headers.get('user-agent')


def session_credential(user):
    if user['role'] == 'owner':
        return sessions.digest(config.admin_username + '\0' + config.admin_password) if user['username'] == config.admin_username else None
    return users.credential(user['username']) if user['role'] in {'admin','location'} else None


def authenticate_admin(username, password):
    if secrets.compare_digest(username.encode(),config.admin_username.encode()) and secrets.compare_digest(password.encode(),config.admin_password.encode()):
        return {'username':username,'role':'owner','allowed_areas':None}
    user = users.authenticate(username,password)
    return user if user and user['role'] in {'admin','location'} else None


def require_admin(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> dict:
    session = sessions.resolve(request.cookies.get(sessions.cookie_name), session_credential)
    if session:
        user, csrf = session
        if request.method not in {'GET','HEAD','OPTIONS'} and not secrets.compare_digest(request.headers.get('x-csrf-token','').encode(),csrf.encode()):
            raise HTTPException(403,'Обновите страницу и повторите действие')
        request.state.csrf_token = csrf
        request.state.admin = {key:user[key] for key in ('username','role','allowed_areas')}
        return request.state.admin
    if credentials is None:
        raise HTTPException(401, 'Войдите в админку')
    if sessions.throttled(credentials.username, _request_addr(request)):
        raise HTTPException(429, 'Слишком много попыток. Повторите через 5 минут', headers={'Retry-After':'300'})
    env_username_ok = secrets.compare_digest(credentials.username.encode('utf-8'), config.admin_username.encode('utf-8'))
    env_password_ok = secrets.compare_digest(credentials.password.encode('utf-8'), config.admin_password.encode('utf-8'))
    if env_username_ok and env_password_ok:
        if request.url.path == '/admin':
            users.add_login_log(
                credentials.username,
                True,
                'owner',
                _request_addr(request),
                _request_user_agent(request),
                'Вход через главную env-учётку',
            )
        request.state.admin = {'username': credentials.username, 'role': 'owner', 'allowed_areas': None}
        return request.state.admin

    user = users.authenticate(credentials.username, credentials.password)
    if user and user['role'] in {'admin', 'location'}:
        if request.url.path == '/admin':
            users.add_login_log(
                user['username'],
                True,
                user['role'],
                _request_addr(request),
                _request_user_agent(request),
                'Вход в админку',
            )
        request.state.admin = user
        return user

    users.add_login_log(
        credentials.username or '-',
        False,
        None,
        _request_addr(request),
        _request_user_agent(request),
        'Неверный логин или пароль / пользователь заблокирован',
    )
    raise HTTPException(
        status_code=401,
        detail='Invalid admin credentials',
    )


def require_owner(admin: dict = Depends(require_admin)) -> dict:
    if admin.get('role') != 'owner':
        raise HTTPException(
            status_code=403,
            detail='Управление пользователями доступно только главному администратору',
        )
    return admin


class CityUpdateRequest(BaseModel):
    area_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(max_length=150)
    enabled: bool = True
    color: str = '#60a5fa'
    slug: str | None = None
    sound_path: str | None = None
    sound_repeat_mode: Literal['seconds', 'count'] = 'seconds'
    sound_repeat_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    sound_repeat_count: int | None = Field(default=None, ge=1)
    history_sync_hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    ack_button_text: str | None = Field(default=None, max_length=100)


class HistorySyncRequest(BaseModel):
    hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = 'location'
    allowed_areas: list[str] = Field(default_factory=list, max_length=500)


class UserAccessRequest(BaseModel):
    role: str
    allowed_areas: list[str] = Field(default_factory=list, max_length=500)


class EmployeeAreaRequest(BaseModel):
    enabled: bool


class GlobalSettingsRequest(BaseModel):
    support_url: str = Field(default='',max_length=2048)


def validate_admin_access(admin: dict, role: str, allowed_areas: list[str]) -> None:
    require_global(admin)
    if role not in {'admin', 'location'}:
        raise HTTPException(400, 'Некорректная роль')
    if admin['role'] != 'owner' and role != 'location':
        raise HTTPException(403, 'Только главный администратор назначает общих администраторов')
    known = {city.area_id for city in cities.list(include_disabled=True)}
    if role == 'location' and (not allowed_areas or not set(allowed_areas) <= known):
        raise HTTPException(400, 'Выберите хотя бы одну существующую локацию')


def manageable_user(admin: dict, username: str) -> dict:
    require_global(admin)
    try:
        target = users.get_user(username)
    except KeyError:
        raise HTTPException(404, 'Пользователь не найден') from None
    require_user_control(admin, target)
    return target


class UserPasswordRequest(BaseModel):
    password: str


class UserActiveRequest(BaseModel):
    is_active: bool


@asynccontextmanager
async def lifespan(_: FastAPI):
    await telegram.start()
    await watcher.start()
    try:
        yield
    finally:
        await watcher.stop()
        await telegram.stop()


app = FastAPI(title='PereHomeLab', lifespan=lifespan)


@app.middleware('http')
async def audit_admin_changes(request: Request, call_next):
    response = await call_next(request)
    admin = getattr(request.state, 'admin', None)
    if request.url.path.startswith(('/api/admin/', '/admin')):
        response.headers['Cache-Control'] = 'no-store'
    if admin and response.status_code < 400 and admin.get('_changes'):
        await asyncio.to_thread(audit.save, admin['username'], admin['_changes'])
    return response


app.mount('/static', StaticFiles(directory=PROJECT_ROOT / 'static'), name='static')
app.mount('/sound', StaticFiles(directory=PROJECT_ROOT / 'sound'), name='sound')


@app.get('/download-app')
async def download_app() -> FileResponse:
    path = resolve_app_download_path()
    if path is None:
        logger.warning('App download file not found: dir=%s, filename=%s', config.app_download_dir, config.app_download_filename)
        raise HTTPException(status_code=404, detail='Application file not found')
    return FileResponse(
        path,
        media_type='application/octet-stream',
        filename=config.app_download_filename,
    )


@app.get('/')
async def home() -> FileResponse:
    return FileResponse(PROJECT_ROOT / 'static' / 'home.html')


@app.get('/monitor')
async def monitor() -> FileResponse:
    return FileResponse(PROJECT_ROOT / 'static' / 'index.html')


@app.get('/admin')
async def admin_page(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
    try:
        require_admin(request, credentials)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return RedirectResponse('/admin/login', status_code=303)
    return FileResponse(PROJECT_ROOT / 'static' / 'admin.html')


@app.get('/admin/login')
async def admin_login_page():
    return FileResponse(PROJECT_ROOT / 'static' / 'admin-login.html')


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1,max_length=100)
    password: str = Field(min_length=1,max_length=1024)


@app.post('/api/admin/auth/login')
async def admin_login(payload: AdminLoginRequest, request: Request):
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403,'Недопустимый источник запроса')
    username = payload.username.strip()
    remote_addr = _request_addr(request)
    if await asyncio.to_thread(sessions.throttled,username,remote_addr):
        raise HTTPException(429,'Слишком много попыток. Повторите через 5 минут',headers={'Retry-After':'300'})
    user = await asyncio.to_thread(authenticate_admin,username,payload.password)
    await asyncio.to_thread(users.add_login_log,username,bool(user),user['role'] if user else None,
        remote_addr,_request_user_agent(request),'Вход в админку' if user else 'Неверный логин или пароль')
    if not user:
        raise HTTPException(401,'Неверный логин или пароль')
    token, csrf = await asyncio.to_thread(sessions.create,username,session_credential(user))
    sessions.delete(request.cookies.get(sessions.cookie_name))
    response = JSONResponse({'user':user,'csrf_token':csrf})
    response.set_cookie(sessions.cookie_name,token,max_age=sessions.lifetime,httponly=True,
        secure=request.url.scheme=='https',samesite='strict',path='/')
    return response


@app.post('/api/admin/auth/logout')
async def admin_logout(request: Request, admin: dict = Depends(require_admin)):
    sessions.delete(request.cookies.get(sessions.cookie_name))
    response = JSONResponse({'logged_out':True})
    response.delete_cookie(sessions.cookie_name,path='/')
    return response


@app.get('/healthz')
async def healthz() -> dict:
    return {'status': 'ok', 'version': config.app_version}


@app.get('/{area_slug}')
async def monitor_area(area_slug: str) -> FileResponse:
    if area_slug in RESERVED_AREA_SLUGS:
        raise HTTPException(status_code=404, detail='Not found')
    if not any(city.slug == area_slug or city.area_id == area_slug for city in cities.list()):
        raise HTTPException(status_code=404, detail='Локация не найдена')
    return FileResponse(PROJECT_ROOT / 'static' / 'index.html')


@app.get('/api/status')
async def status() -> dict:
    return {
        'status': watcher.status_message,
        'watched_count': watcher.watched_count,
        'active_watched_count': watcher.active_watched_count,
        'last_error': watcher.last_error,
        'system': watcher.system_snapshot(),
    }


@app.get('/api/system-status')
async def system_status() -> dict:
    return watcher.system_snapshot()


@app.post('/api/system-status/check')
async def system_status_check() -> dict:
    return await watcher.check_now()


@app.get('/api/diagnostics')
async def diagnostics() -> dict:
    ha_ok = False
    ha_error = None
    ha_response = None
    try:
        ha_response = await ha_client.health_check()
        ha_ok = True
    except Exception as exc:
        ha_error = str(exc)

    return jsonable_encoder({
        'ha_ok': ha_ok,
        'ha_error': ha_error,
        'ha_response': ha_response,
        'ha_url': config.ha_url,
        'ha_token_length': len(config.ha_token),
        'ha_auto_discover': config.ha_auto_discover,
        'ha_entity_ids_count': len(config.ha_entity_ids),
        'ha_entity_domain': config.ha_entity_domain,
        'ha_device_class': config.ha_device_class,
        'ha_entity_id_suffix': config.ha_entity_id_suffix,
        'ha_trigger_from': config.ha_trigger_from,
        'ha_trigger_to': config.ha_trigger_to,
        'ha_reconnect_seconds': config.ha_reconnect_seconds,
        'ha_sync_history_hours': config.ha_sync_history_hours,
        'ha_history_reconcile_seconds': config.ha_history_reconcile_seconds,
        'ha_healthcheck_seconds': config.ha_healthcheck_seconds,
        'app_version': config.app_version,
        'watched_count': watcher.watched_count,
        'active_watched_count': watcher.active_watched_count,
        'watcher_status': watcher.status_message,
        'watcher_last_error': watcher.last_error,
        'history_db_path': config.history_db_path,
        'history_db_exists': config.history_db_path.exists(),
        'button_sound': config.button_sound,
        'button_sound_exists': config.button_sound.exists(),
        'areas': watcher.areas_payload(include_disabled=True),
    })



@app.get('/api/areas')
async def areas() -> dict:
    items = watcher.areas_payload()
    return {
        'items': items,
        'total': len(items),
        'support_url': history.get_runtime_state('support_url') or '',
        'all': {
            'area_id': '',
            'display_name': 'Все',
            'button_count': watcher.watched_count,
            'active_button_count': watcher.active_watched_count,
        },
    }


@app.get('/api/admin/areas')
async def admin_areas(admin: dict = Depends(require_admin)) -> dict:
    try:
        await asyncio.wait_for(watcher.refresh_areas_from_ha(), timeout=4)
    except Exception as exc:
        logger.warning('HA area refresh failed, returning cached areas: %s', exc)
    items = visible_areas(admin, watcher.areas_payload(include_disabled=True))
    return {'items': items, 'total': len(items)}


@app.post('/api/admin/areas')
async def admin_update_area(request: CityUpdateRequest, admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, request.area_id)
    before = next((city.to_dict() for city in cities.list(include_disabled=True) if city.area_id==request.area_id), {})
    if admin['role'] == 'location':
        existing = next((city for city in cities.list(include_disabled=True) if city.area_id == request.area_id), None)
        if not existing:
            raise HTTPException(404, 'Локация не найдена')
        if (request.display_name.strip(), request.slug, request.enabled, request.color) != (existing.display_name, existing.to_dict()['slug'], existing.enabled, existing.color):
            raise HTTPException(403, 'Изменение названия, ссылки и доступности локации требует общего администратора')
    if request.sound_path and request.sound_path not in {item['path'] for item in list_sound_files()}:
        raise HTTPException(400, 'Выберите существующий звук из библиотеки')
    clean_slug = slugify(request.slug or request.display_name, request.area_id)
    if clean_slug in RESERVED_AREA_SLUGS:
        raise HTTPException(status_code=409, detail=f'Ссылка /{clean_slug} зарезервирована')
    for city in cities.list(include_disabled=True):
        if city.area_id != request.area_id and (city.slug or slugify(city.display_name, city.area_id)) == clean_slug:
            raise HTTPException(status_code=409, detail=f'Ссылка /{clean_slug} уже занята другой локацией')

    city = cities.upsert(
        request.area_id,
        request.display_name,
        request.enabled,
        request.color,
        clean_slug,
        request.sound_path,
        request.sound_repeat_mode,
        request.sound_repeat_seconds,
        request.sound_repeat_count,
        request.history_sync_hours,
        request.ack_button_text,
    )
    watcher.refresh_city_settings()
    audit_change(admin,'location','Настройки локации сохранены',city.display_name,before,city.to_dict(),[request.area_id])

    async def reload_entities_in_background() -> None:
        try:
            await watcher.reload_entities()
        except Exception as exc:
            logger.warning('Saved city settings, but HA reload failed: %s', exc)

    asyncio.create_task(reload_entities_in_background())
    return {'item': city.to_dict()}


@app.post('/api/admin/areas/{area_id}/sync-history')
async def admin_sync_area_history(
    area_id: str,
    request: HistorySyncRequest,
    admin: dict = Depends(require_admin),
) -> dict:
    require_area(admin, area_id)
    try:
        result = await watcher.sync_history_for_area(area_id, request.hours)
        audit_change(admin,'history','История подтянута из Home Assistant',area_ids=[area_id],details=f"Добавлено нажатий: {result.get('added',0)}")
        return result
    except Exception as exc:
        logger.warning('Manual HA history sync failed for area %s: %s', area_id, exc)
        raise HTTPException(status_code=503, detail=f'Не удалось подтянуть историю: {exc}') from exc


@app.get('/api/batteries')
async def batteries(
    limit: int | None = Query(default=None, ge=1, le=500),
    area_id: str | None = Query(default=None),
) -> dict:
    try:
        states = await ha_client.get_states()
        items = collect_batteries(states, watcher.entity_area_map(), area_id=area_id or None)
        received_at = datetime.now().astimezone().isoformat(timespec='seconds')
    except Exception as exc:
        logger.warning('HA battery data unavailable: %s', exc)
        return {
            'items': [],
            'total': 0,
            'limit': limit,
            'area_id': area_id,
            'available': False,
            'error': 'Заряд временно недоступен',
            'received_at': None,
        }
    for item in items:
        item['received_at'] = received_at
    visible_items = items[:limit] if limit else items
    return {
        'items': visible_items,
        'total': len(items),
        'limit': limit,
        'area_id': area_id,
        'available': True,
        'error': None,
        'received_at': received_at,
    }


@app.get('/api/history')
async def press_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    area_id: str | None = Query(default=None),
    q: str = Query(default='', max_length=200),
    state: str = Query(default='', pattern='^(|accepted|waiting)$'),
    date_from: str = Query(default='', pattern=r'^(|\d{4}-\d{2}-\d{2})$'),
    date_to: str = Query(default='', pattern=r'^(|\d{4}-\d{2}-\d{2})$'),
) -> dict:
    try:
        for value in (date_from, date_to):
            if value:
                datetime.fromisoformat(value)
        payload = history.page(page, page_size, area_id=area_id or None, query=q, state=state, date_from=date_from, date_to=date_to)
    except ValueError:
        raise HTTPException(400, 'Некорректная дата') from None
    city_settings = {
        city.area_id: city
        for city in cities.list(watcher.area_counts(), include_disabled=True, active_button_counts=watcher.active_area_counts())
    }
    payload['items'] = telegram.calls.decorate_many(payload.get('items', []))
    for item in payload['items']:
        city = city_settings.get(item.get('area_id') or '')
        item['ack_button_text'] = city.ack_button_text if city else None
    return payload


@app.get('/api/history/{event_id}')
async def press_details(event_id: str) -> dict:
    item = telegram.calls.details(event_id, include_actions=False)
    if not item:
        raise HTTPException(404, 'Вызов не найден')
    return {'item': call_event(item)}


@app.post('/api/acknowledge')
async def acknowledge(request: AcknowledgeRequest) -> dict:
    items = await telegram.acknowledge(request.ids)
    current = telegram.calls.get_many(request.ids)
    return {'updated': len(items), 'events': call_events(current)}


@app.post('/api/alerts/state')
async def alerts_state(request: AcknowledgeRequest) -> dict:
    items = telegram.calls.get_many(request.ids)
    changed = [item for item in items if item['revision'] > request.revisions.get(item['id'], -1)]
    events = call_events(changed)
    return {'acknowledged_ids': [item['id'] for item in items if item and item['sound_silenced']],
            'events': events,
            'released_events': [item for item in events if item['last_released_at'] and not item['sound_silenced']]}


@app.get('/api/admin/employees')
async def admin_employees(q: str = Query(default='', max_length=150), page: int = Query(default=1, ge=1),
                          area_id: str | None = None, admin: dict = Depends(require_admin)) -> dict:
    if area_id:
        require_area(admin, area_id)
    else:
        require_global(admin)
    return telegram.employees.page(q, page, area_id=area_id)


@app.post('/api/admin/employees')
async def admin_employee_create(request: EmployeeCreateRequest, admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    try:
        item = telegram.employees.create(request.login, request.full_name, request.telegram_user_id, admin['username'])
        audit_change(admin,'employees','Сотрудник добавлен',item['display_name'],after=item)
        if item['telegram_user_id']:
            telegram.seen_users.pop(item['telegram_user_id'], None)
            await broadcast_call_change(telegram.calls.refresh_profile([item['telegram_user_id']]), 'profile')
        return {'item': item}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.put('/api/admin/employees/{employee_id}')
async def admin_employee_update(employee_id: str, request: EmployeeUpdateRequest, admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    try:
        previous = telegram.employees.get(employee_id)
        item = telegram.employees.update(employee_id, request.login, request.full_name, request.blocked,
            request.allowed_areas, request.telegram_user_id, admin['username'], request.profile_locked)
        audit_change(admin,'employees','Общие ограничения сотрудника изменены',item['display_name'],
            {'allowed_areas':previous.get('allowed_areas')},{'allowed_areas':item.get('allowed_areas')})
        common_fields = ('login','full_name','blocked','profile_locked','telegram_user_id')
        audit_change(admin,'employees','Профиль сотрудника изменён',item['display_name'],
            {key:previous.get(key) for key in common_fields},{key:item.get(key) for key in common_fields},telegram.employees.areas_for(employee_id))
        if previous['allowed_areas'] != item['allowed_areas']:
            for area_id in telegram.employees.areas_for(employee_id):
                before_enabled = previous['allowed_areas'] is None or area_id in previous['allowed_areas']
                after_enabled = item['allowed_areas'] is None or area_id in item['allowed_areas']
                audit_change(admin,'employees','Доступ сотрудника к локации изменён',item['display_name'],
                    {'enabled':before_enabled},{'enabled':after_enabled},[area_id])
        changes = telegram.calls.refresh_profile([previous['telegram_user_id'], item['telegram_user_id']])
        for user_id in [previous['telegram_user_id'], item['telegram_user_id']]:
            if user_id:
                telegram.seen_users.pop(user_id, None)
        await broadcast_call_change(changes, 'profile')
        return {'item': item}
    except KeyError:
        raise HTTPException(404, 'Сотрудник не найден') from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get('/api/admin/employees/{employee_id}/audit')
async def admin_employee_audit(employee_id: str, admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    return {'items': telegram.employees.audit(employee_id)}


@app.put('/api/admin/employees/{employee_id}/areas/{area_id}')
async def admin_employee_area(employee_id: str, area_id: str, request: EmployeeAreaRequest,
                              admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, area_id)
    try:
        before = telegram.employees.area_enabled(employee_id,area_id)
        telegram.employees.set_area_access(employee_id, area_id, request.enabled, admin['username'])
        employee = telegram.employees.get(employee_id)
        audit_change(admin,'employees','Доступ сотрудника к боту изменён',employee['display_name'],{'enabled':before},{'enabled':request.enabled},[area_id])
    except KeyError:
        raise HTTPException(404, 'Сотрудник в этой локации не найден') from None
    return {'saved': True}


@app.get('/api/admin/telegram')
async def admin_telegram(admin: dict = Depends(require_admin)) -> dict:
    payload = telegram.public_settings()
    payload['items'] = visible_areas(admin, payload['items'])
    return payload


@app.post('/api/admin/telegram/{area_id}')
async def admin_telegram_save(area_id: str, request: TelegramSettingsRequest, admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, area_id)
    before = telegram._setting(area_id) or {}
    try:
        await telegram.configure(area_id, request.enabled, request.token)
        telegram.store.set_policy(area_id, request.require_profile, request.allow_reassign_any)
        after = telegram._setting(area_id) or {}
        audit_change(admin,'telegram','Настройки Telegram сохранены',before=before,after=after,area_ids=[area_id],
            details='Токен бота заменён' if request.token else '')
    except (ValueError, TelegramError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {'saved': True}


@app.delete('/api/admin/telegram/{area_id}')
async def admin_telegram_delete(area_id: str, admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, area_id)
    telegram.store.delete(area_id)
    audit_change(admin,'telegram','Настройки Telegram удалены',area_ids=[area_id])
    return {'deleted': True}


@app.post('/api/admin/telegram/{area_id}/confirm-setup')
async def admin_telegram_confirm(area_id: str, request: TelegramSetupRequest, admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, area_id)
    try:
        before = telegram._setting(area_id) or {}
        telegram.store.confirm_setup(area_id, request.setup_id)
        audit_change(admin,'telegram','Группа Telegram привязана',before=before,after=telegram._setting(area_id),area_ids=[area_id])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {'saved': True}


@app.post('/api/admin/telegram/{area_id}/begin-setup')
async def admin_telegram_begin(area_id: str, admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, area_id)
    try:
        telegram.begin_setup(area_id)
        audit_change(admin,'telegram','Начата привязка Telegram',area_ids=[area_id])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {'started': True}


@app.post('/api/admin/telegram/{area_id}/test')
async def admin_telegram_test(area_id: str, admin: dict = Depends(require_admin)) -> dict:
    require_area(admin, area_id)
    try:
        result = await telegram.test(area_id)
        audit_change(admin,'telegram','Отправлено тестовое уведомление',area_ids=[area_id])
        return result
    except (ValueError, TelegramError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None




@app.get('/api/admin/sounds')
async def admin_sounds(_: str = Depends(require_admin)) -> dict:
    return {'items': list_sound_files()}


@app.get('/api/admin/settings')
async def admin_settings(admin: dict = Depends(require_admin)):
    require_global(admin)
    return {'support_url':history.get_runtime_state('support_url') or ''}


@app.put('/api/admin/settings')
async def admin_save_settings(payload: GlobalSettingsRequest, admin: dict = Depends(require_admin)):
    require_global(admin)
    support_url = payload.support_url.strip()
    try:
        parsed = urlsplit(support_url)
        if support_url and (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or any(ord(char)<32 for char in support_url)):
            raise ValueError()
        parsed.port
    except ValueError:
        raise HTTPException(400,'Укажите ссылку http:// или https:// без логина и пароля') from None
    previous = history.get_runtime_state('support_url') or ''
    history.set_runtime_state('support_url',support_url,datetime.now().isoformat(timespec='seconds'))
    audit_change(admin,'settings','Изменена ссылка поддержки',before={'support_url':previous},after={'support_url':support_url},
        area_ids=[city.area_id for city in cities.list(include_disabled=True)])
    return {'support_url':support_url}


@app.get('/api/admin/logs')
async def admin_logs(limit: int = Query(default=120, ge=1, le=500), admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    items = recent_error_log_items(limit)
    return {'items': items, 'total': len(items), 'limit': limit, 'actions': users.actions(limit)}


@app.get('/api/admin/audit')
async def admin_audit(page: int = Query(default=1,ge=1), area_id: str = '',
        category: str = Query(default='',pattern='^(|location|telegram|employees|users|sounds|history|settings|legacy)$'),
        actor: str = Query(default='',max_length=100), q: str = Query(default='',max_length=150),
        date_from: str = '', date_to: str = '', admin: dict = Depends(require_admin)):
    if area_id:
        require_area(admin,area_id)
    try:
        for value in (date_from,date_to):
            if value:
                datetime.strptime(value,'%Y-%m-%d')
        if date_from and date_to and date_from > date_to:
            raise ValueError()
    except ValueError:
        raise HTTPException(400,'Проверьте период журнала') from None
    return audit.page(admin,page,area_id=area_id,category=category,actor=actor,query=q,date_from=date_from,date_to=date_to)


@app.get('/api/admin/me')
async def admin_me(request: Request, admin: dict = Depends(require_admin)) -> dict:
    return {'user': admin, 'csrf_token':getattr(request.state,'csrf_token',None)}


@app.post('/api/admin/me/password')
async def admin_change_own_password(
    request: UserPasswordRequest,
    admin: dict = Depends(require_admin),
) -> dict:
    if admin.get('role') == 'owner' and admin.get('username') == config.admin_username:
        raise HTTPException(
            status_code=409,
            detail='Главный env-администратор меняется только через secret.env',
        )
    try:
        item = users.set_password(admin['username'], request.password)
        audit_change(admin,'users','Изменён свой пароль',admin['username'])
        return {'item':item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден') from exc


@app.get('/api/admin/users')
async def admin_users(admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    items = users.list_users()
    if admin['role'] != 'owner':
        items = [item for item in items if item['role'] == 'location']
    return {'items': items, 'total': len(items)}


@app.post('/api/admin/users')
async def admin_create_user(request: UserCreateRequest, admin: dict = Depends(require_admin)) -> dict:
    validate_admin_access(admin, request.role, request.allowed_areas)
    try:
        user = users.create_user(request.username, request.password, request.role, request.allowed_areas)
        admin['audit_user'] = user['username']
        audit_change(admin,'users','Администратор создан',user['username'],after=user,area_ids=user['allowed_areas'])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail='Пользователь с таким логином уже существует') from exc
    return {'item': user}


@app.post('/api/admin/users/{username}/password')
async def admin_change_user_password(
    username: str,
    request: UserPasswordRequest,
    admin: dict = Depends(require_admin),
) -> dict:
    target = manageable_user(admin, username)
    try:
        item = users.set_password(username, request.password)
        audit_change(admin,'users','Пароль администратора изменён',username,area_ids=target['allowed_areas'])
        return {'item':item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден') from exc


@app.put('/api/admin/users/{username}/access')
async def admin_user_access(username: str, request: UserAccessRequest, admin: dict = Depends(require_admin)) -> dict:
    previous = manageable_user(admin, username)
    validate_admin_access(admin, request.role, request.allowed_areas)
    item = users.set_access(username, request.role, request.allowed_areas)
    audit_change(admin,'users','Доступ администратора изменён',username,previous,item,
        list(dict.fromkeys((previous['allowed_areas'] or [])+(item['allowed_areas'] or []))))
    return {'item':item}


@app.post('/api/admin/users/{username}/active')
async def admin_set_user_active(
    username: str,
    request: UserActiveRequest,
    admin: dict = Depends(require_admin),
) -> dict:
    target = manageable_user(admin, username)
    try:
        item = users.set_active(username, request.is_active)
        audit_change(admin,'users','Администратор разблокирован' if request.is_active else 'Администратор заблокирован',username,
            {'is_active':target['is_active']},{'is_active':item['is_active']},target['allowed_areas'])
        return {'item':item}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден или это главный администратор') from exc


@app.delete('/api/admin/users/{username}')
async def admin_delete_user(username: str, admin: dict = Depends(require_admin)) -> dict:
    target = manageable_user(admin, username)
    try:
        users.delete_user(username)
        audit_change(admin,'users','Администратор удалён',username,area_ids=target['allowed_areas'])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден или это главный администратор') from exc
    return {'deleted': username}


@app.get('/api/admin/login-logs')
async def admin_login_logs(limit: int = Query(default=200, ge=1, le=500), admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    items = users.login_logs(limit)
    return {'items': items, 'total': len(items), 'limit': limit}


@app.post('/api/admin/sounds/{filename}')
async def admin_upload_sound(filename: str, request: Request, area_id: str = '', admin: dict = Depends(require_admin)) -> dict:
    if area_id:
        require_area(admin,area_id)
    safe_filename = safe_sound_filename(filename)
    target = PROJECT_ROOT / 'sound' / safe_filename
    max_bytes = 20 * 1024 * 1024
    try:
        declared_size = int(request.headers.get('content-length', '0'))
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid content length') from None
    if declared_size > max_bytes:
        raise HTTPException(status_code=413, detail='Sound file must not exceed 20 MiB')
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail='Sound file must not exceed 20 MiB')
        content.extend(chunk)
    if not content:
        raise HTTPException(status_code=400, detail='Empty sound file')
    try:
        with target.open('xb') as output:
            output.write(content)
    except FileExistsError:
        raise HTTPException(409, 'Звук с таким именем уже существует. Выберите другое имя') from None
    audit_change(admin,'sounds','Звук загружен',safe_filename,area_ids=[area_id] if area_id else [])
    return {'item': {'name': safe_filename, 'path': f'sound/{safe_filename}', 'url': f'/sound/{safe_filename}', 'size': target.stat().st_size}}


@app.delete('/api/admin/sounds/{filename}')
async def admin_delete_sound(filename: str, admin: dict = Depends(require_admin)) -> dict:
    require_global(admin)
    safe_filename = safe_sound_filename(filename)
    target = PROJECT_ROOT / 'sound' / safe_filename
    try:
        if target.resolve() == config.button_sound.resolve():
            raise HTTPException(status_code=409, detail='Default env sound cannot be deleted')
    except FileNotFoundError:
        pass
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail='Sound file not found')

    sound_path = f'sound/{safe_filename}'
    if any(city.sound_path == sound_path for city in cities.list(include_disabled=True)):
        raise HTTPException(409, 'Звук используется в локации. Сначала выберите другой звук')
    cleared = 0
    target.unlink()
    audit_change(admin,'sounds','Звук удалён',safe_filename)
    watcher.refresh_city_settings()

    async def reload_entities_in_background() -> None:
        try:
            await watcher.reload_entities()
        except Exception as exc:
            logger.warning('Deleted sound, but HA reload failed: %s', exc)

    asyncio.create_task(reload_entities_in_background())
    return {'deleted': sound_path, 'cleared_regions': cleared, 'items': list_sound_files()}


@app.get('/api/client-config')
async def client_config() -> dict:
    sound_url = None
    try:
        relative_sound = config.button_sound.relative_to(PROJECT_ROOT)
        sound_url = '/' + relative_sound.as_posix()
    except ValueError:
        logger.warning('BUTTON_SOUND is outside project root: %s', config.button_sound)

    return {
        'sound_url': sound_url,
        'support_url':history.get_runtime_state('support_url') or '',
        'ack_button_text': get_live_ack_button_text(),
        'sound_play_seconds': get_live_sound_play_seconds(),
        'status_code': config.ha_trigger_to,
        'poll_interval_seconds': 0,
        'sync_history_hours': config.ha_sync_history_hours,
        'app_download_url': '/download-app',
        'app_download_filename': config.app_download_filename,
    }


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket) -> None:
    await watcher.register(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        watcher.unregister(websocket)
