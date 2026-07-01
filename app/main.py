import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import secrets

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.cities import CityStore, slugify
from app.battery import collect_batteries
from app.config import PROJECT_ROOT, get_live_ack_button_text, get_live_sound_play_seconds, load_config
from app.ha_client import HomeAssistantClient
from app.history import HistoryStore
from app.logging_config import configure_logging
from app.users import UserStore
from app.watcher import HomeAssistantWatcher

logger = configure_logging()
config = load_config()
logger.info(
    'Config loaded: HA_URL=%s, token_length=%s, auto_discover=%s, domain=%s, device_class=%s, trigger=%s, history_sync_hours=%s, db=%s, sound=%s',
    config.ha_url,
    len(config.ha_token),
    config.ha_auto_discover,
    config.ha_entity_domain,
    config.ha_device_class or '-',
    config.ha_trigger_to,
    config.ha_sync_history_hours,
    config.history_db_path,
    config.button_sound,
)
history = HistoryStore(config.history_db_path)
cities = CityStore(config.history_db_path)
users = UserStore(config.history_db_path)
users.ensure_owner(config.admin_username, config.admin_password)
ha_client = HomeAssistantClient(config)
watcher = HomeAssistantWatcher(config, ha_client, history, cities, logger)




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
    ids: list[str]


security = HTTPBasic()


def _request_addr(request: Request) -> str | None:
    return request.client.host if request.client else None


def _request_user_agent(request: Request) -> str | None:
    return request.headers.get('user-agent')


def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> dict:
    env_username_ok = secrets.compare_digest(credentials.username, config.admin_username)
    env_password_ok = secrets.compare_digest(credentials.password, config.admin_password)
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
        return {'username': credentials.username, 'role': 'owner'}

    user = users.authenticate(credentials.username, credentials.password)
    if user:
        if request.url.path == '/admin':
            users.add_login_log(
                user['username'],
                True,
                user['role'],
                _request_addr(request),
                _request_user_agent(request),
                'Вход в админку',
            )
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
        headers={'WWW-Authenticate': 'Basic'},
    )


def require_owner(admin: dict = Depends(require_admin)) -> dict:
    if admin.get('role') != 'owner':
        raise HTTPException(
            status_code=403,
            detail='Управление пользователями доступно только главному администратору',
        )
    return admin


class CityUpdateRequest(BaseModel):
    area_id: str
    display_name: str
    enabled: bool = True
    color: str = '#60a5fa'
    slug: str | None = None
    sound_path: str | None = None
    sound_repeat_mode: str = 'seconds'
    sound_repeat_seconds: float | None = None
    sound_repeat_count: int | None = None
    history_sync_hours: float | None = None
    ack_button_text: str | None = None


class HistorySyncRequest(BaseModel):
    hours: float | None = None


class UserCreateRequest(BaseModel):
    username: str
    password: str


class UserPasswordRequest(BaseModel):
    password: str


class UserActiveRequest(BaseModel):
    is_active: bool


@asynccontextmanager
async def lifespan(_: FastAPI):
    await watcher.start()
    yield
    await watcher.stop()


app = FastAPI(title='PereHomeLab', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=PROJECT_ROOT / 'static'), name='static')
app.mount('/sound', StaticFiles(directory=PROJECT_ROOT / 'sound'), name='sound')


@app.get('/')
async def home() -> FileResponse:
    return FileResponse(PROJECT_ROOT / 'static' / 'home.html')


@app.get('/monitor')
async def monitor() -> FileResponse:
    return FileResponse(PROJECT_ROOT / 'static' / 'index.html')


@app.get('/admin')
async def admin_page(_: str = Depends(require_admin)) -> FileResponse:
    return FileResponse(PROJECT_ROOT / 'static' / 'admin.html')


@app.get('/{area_slug}')
async def monitor_area(area_slug: str) -> FileResponse:
    reserved = {'api', 'admin', 'monitor', 'static', 'sound', 'ws'}
    if area_slug in reserved:
        raise HTTPException(status_code=404, detail='Not found')
    return FileResponse(PROJECT_ROOT / 'static' / 'index.html')


@app.get('/api/status')
async def status() -> dict:
    return {
        'status': watcher.status_message,
        'watched_count': watcher.watched_count,
        'active_watched_count': watcher.active_watched_count,
        'last_error': watcher.last_error,
    }


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
        'all': {
            'area_id': '',
            'display_name': 'Все',
            'button_count': watcher.watched_count,
            'active_button_count': watcher.active_watched_count,
        },
    }


@app.get('/api/admin/areas')
async def admin_areas(_: str = Depends(require_admin)) -> dict:
    try:
        await asyncio.wait_for(watcher.refresh_areas_from_ha(), timeout=4)
    except Exception as exc:
        logger.warning('HA area refresh failed, returning cached areas: %s', exc)
    return {
        'items': watcher.areas_payload(include_disabled=True),
        'total': len(watcher.areas_payload(include_disabled=True)),
    }


@app.post('/api/admin/areas')
async def admin_update_area(request: CityUpdateRequest, _: str = Depends(require_admin)) -> dict:
    clean_slug = slugify(request.slug or request.display_name, request.area_id)
    if clean_slug in {'api', 'admin', 'monitor', 'static', 'sound', 'ws'}:
        raise HTTPException(status_code=409, detail=f'Ссылка /{clean_slug} зарезервирована')
    for city in cities.list(include_disabled=True):
        if city.area_id != request.area_id and (city.slug or slugify(city.display_name, city.area_id)) == clean_slug:
            raise HTTPException(status_code=409, detail=f'Ссылка /{clean_slug} уже занята другим регионом')

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
    _: str = Depends(require_admin),
) -> dict:
    try:
        return await watcher.sync_history_for_area(area_id, request.hours)
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
) -> dict:
    payload = history.page(page, page_size, area_id=area_id or None)
    city_settings = {
        city.area_id: city
        for city in cities.list(watcher.area_counts(), include_disabled=True, active_button_counts=watcher.active_area_counts())
    }
    for item in payload.get('items', []):
        city = city_settings.get(item.get('area_id') or '')
        item['ack_button_text'] = city.ack_button_text if city else None
    return payload


@app.post('/api/acknowledge')
async def acknowledge(request: AcknowledgeRequest) -> dict:
    acknowledged_at = datetime.now()
    count = history.acknowledge(
        request.ids,
        acknowledged_at.isoformat(timespec='seconds'),
        acknowledged_at.strftime('%Y-%m-%d %H:%M:%S'),
    )
    return {'updated': count}




@app.get('/api/admin/sounds')
async def admin_sounds(_: str = Depends(require_admin)) -> dict:
    return {'items': list_sound_files()}


@app.get('/api/admin/logs')
async def admin_logs(limit: int = Query(default=120, ge=1, le=500), _: str = Depends(require_admin)) -> dict:
    items = recent_error_log_items(limit)
    return {'items': items, 'total': len(items), 'limit': limit}


@app.get('/api/admin/me')
async def admin_me(admin: dict = Depends(require_admin)) -> dict:
    return {'user': admin}


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
        return {'item': users.set_password(admin['username'], request.password)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден') from exc


@app.get('/api/admin/users')
async def admin_users(_: dict = Depends(require_owner)) -> dict:
    items = users.list_users()
    return {'items': items, 'total': len(items)}


@app.post('/api/admin/users')
async def admin_create_user(request: UserCreateRequest, _: dict = Depends(require_owner)) -> dict:
    try:
        user = users.create_user(request.username, request.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail='Пользователь с таким логином уже существует') from exc
    return {'item': user}


@app.post('/api/admin/users/{username}/password')
async def admin_change_user_password(
    username: str,
    request: UserPasswordRequest,
    _: dict = Depends(require_owner),
) -> dict:
    try:
        return {'item': users.set_password(username, request.password)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден') from exc


@app.post('/api/admin/users/{username}/active')
async def admin_set_user_active(
    username: str,
    request: UserActiveRequest,
    _: dict = Depends(require_owner),
) -> dict:
    try:
        return {'item': users.set_active(username, request.is_active)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден или это главный администратор') from exc


@app.delete('/api/admin/users/{username}')
async def admin_delete_user(username: str, _: dict = Depends(require_owner)) -> dict:
    try:
        users.delete_user(username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Пользователь не найден или это главный администратор') from exc
    return {'deleted': username}


@app.get('/api/admin/login-logs')
async def admin_login_logs(limit: int = Query(default=200, ge=1, le=500), _: dict = Depends(require_owner)) -> dict:
    items = users.login_logs(limit)
    return {'items': items, 'total': len(items), 'limit': limit}


@app.post('/api/admin/sounds/{filename}')
async def admin_upload_sound(filename: str, request: Request, _: str = Depends(require_admin)) -> dict:
    safe_filename = safe_sound_filename(filename)
    target = PROJECT_ROOT / 'sound' / safe_filename
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail='Empty sound file')
    target.write_bytes(content)
    return {'item': {'name': safe_filename, 'path': f'sound/{safe_filename}', 'url': f'/sound/{safe_filename}', 'size': target.stat().st_size}}


@app.delete('/api/admin/sounds/{filename}')
async def admin_delete_sound(filename: str, _: str = Depends(require_admin)) -> dict:
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
    cleared = cities.clear_sound_path(sound_path)
    target.unlink()
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
        'ack_button_text': get_live_ack_button_text(),
        'sound_play_seconds': get_live_sound_play_seconds(),
        'status_code': config.ha_trigger_to,
        'poll_interval_seconds': 0,
        'sync_history_hours': config.ha_sync_history_hours,
    }


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket) -> None:
    await watcher.register(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        watcher.unregister(websocket)
