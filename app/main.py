import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.battery import collect_batteries
from app.config import PROJECT_ROOT, get_live_ack_button_text, get_live_sound_play_seconds, load_config
from app.ha_client import HomeAssistantClient
from app.history import HistoryStore
from app.logging_config import configure_logging
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
ha_client = HomeAssistantClient(config)
watcher = HomeAssistantWatcher(config, ha_client, history, logger)


class AcknowledgeRequest(BaseModel):
    ids: list[str]


@asynccontextmanager
async def lifespan(_: FastAPI):
    await watcher.start()
    yield
    await watcher.stop()


app = FastAPI(title='PereHomeLab', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=PROJECT_ROOT / 'static'), name='static')
app.mount('/sound', StaticFiles(directory=PROJECT_ROOT / 'sound'), name='sound')


@app.get('/')
async def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / 'static' / 'index.html')


@app.get('/api/status')
async def status() -> dict:
    return {
        'status': watcher.status_message,
        'watched_count': watcher.watched_count,
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
        'watcher_status': watcher.status_message,
        'watcher_last_error': watcher.last_error,
        'history_db_path': config.history_db_path,
        'history_db_exists': config.history_db_path.exists(),
        'button_sound': config.button_sound,
        'button_sound_exists': config.button_sound.exists(),
    })



@app.get('/api/batteries')
async def batteries(limit: int | None = Query(default=None, ge=1, le=500)) -> dict:
    states = await ha_client.get_states()
    items = collect_batteries(states)
    visible_items = items[:limit] if limit else items
    return {
        'items': visible_items,
        'total': len(items),
        'limit': limit,
    }


@app.get('/api/history')
async def press_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
) -> dict:
    return history.page(page, page_size)


@app.post('/api/acknowledge')
async def acknowledge(request: AcknowledgeRequest) -> dict:
    acknowledged_at = datetime.now()
    count = history.acknowledge(
        request.ids,
        acknowledged_at.isoformat(timespec='seconds'),
        acknowledged_at.strftime('%Y-%m-%d %H:%M:%S'),
    )
    return {'updated': count}


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
