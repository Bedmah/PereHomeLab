import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from app.config import AppConfig
from app.ha_client import HomeAssistantClient
from app.history import HistoryStore


@dataclass(frozen=True)
class WatchedEntity:
    entity_id: str
    name: str


@dataclass(frozen=True)
class PressEvent:
    id: str
    device_id: str
    device_name: str
    pressed_at: str
    pressed_at_display: str

    def to_dict(self) -> dict[str, str]:
        return {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device_name,
            'pressed_at': self.pressed_at,
            'pressed_at_display': self.pressed_at_display,
        }


class HomeAssistantWatcher:
    def __init__(
        self,
        config: AppConfig,
        client: HomeAssistantClient,
        history: HistoryStore,
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._client = client
        self._history = history
        self._logger = logger
        self._clients: set[WebSocket] = set()
        self._entities: dict[str, WatchedEntity] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self.status_message = 'Ожидание запуска'
        self.last_error: str | None = None
        self._history_synced = False

    @property
    def watched_count(self) -> int:
        return len(self._entities)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def register(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        await websocket.send_json(
            {
                'type': 'state',
                'status': self.status_message,
                'watched_count': self.watched_count,
                'last_error': self.last_error,
            }
        )

    def unregister(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.exception('Home Assistant watcher failed. Reconnecting.')
                self.last_error = str(exc)
                await self._set_status(f'Ошибка Home Assistant. Повтор через {self._config.ha_reconnect_seconds:g} сек.')
                await asyncio.sleep(self._config.ha_reconnect_seconds)

    async def _run_once(self) -> None:
        await self._set_status('Подключение к Home Assistant...')
        if not self._entities or self._config.ha_refresh_entities_on_reconnect:
            await self._load_entities()
        else:
            self._logger.info(
                'Using cached Home Assistant entities: %s',
                len(self._entities),
            )
        await self._sync_recent_history_once()
        await self._set_status(f'Отслеживается кнопок: {self.watched_count}')

        websocket = await self._client.connect_websocket()
        try:
            await self._client.subscribe_state_changed(websocket)
            self._logger.info('Subscribed to Home Assistant state_changed events.')
            async for raw_message in websocket:
                await self._handle_ws_message(raw_message)
        finally:
            await websocket.close()


    async def _sync_recent_history_once(self) -> None:
        if self._history_synced or self._config.ha_sync_history_hours <= 0 or not self._entities:
            return

        await self._set_status(f'Синхронизация истории за {self._config.ha_sync_history_hours:g} ч...')

        try:
            added_count = await self._sync_recent_history()
        except Exception as exc:
            self._logger.warning('HA history sync failed: %s', exc)
            self.last_error = str(exc)
            return

        self._history_synced = True
        if added_count:
            await self._broadcast({
                'type': 'history_sync',
                'status': f'История синхронизирована: +{added_count}',
                'watched_count': self.watched_count,
                'last_error': self.last_error,
            })
            self._logger.info('HA history sync added %s press events.', added_count)
        else:
            self._logger.info('HA history sync completed: no new press events.')

    async def _sync_recent_history(self) -> int:
        local_tz = datetime.now().astimezone().tzinfo or ZoneInfo('Europe/Moscow')
        end_time = datetime.now(tz=local_tz)
        start_time = end_time - timedelta(hours=self._config.ha_sync_history_hours)
        entity_ids = list(self._entities.keys())
        added_count = 0

        for chunk in self._chunks(entity_ids, 20):
            histories = await self._client.get_history(
                start_time.isoformat(timespec='seconds'),
                end_time.isoformat(timespec='seconds'),
                chunk,
            )
            added_count += self._import_history_rows(histories, local_tz)

        return added_count

    def _import_history_rows(self, histories: list[list[dict[str, Any]]], local_tz) -> int:
        added_count = 0
        for history_rows in histories:
            current_entity_id = ''
            previous_value: str | None = None
            for row in history_rows:
                if row.get('entity_id'):
                    current_entity_id = str(row.get('entity_id'))

                current_value = str(row.get('state') or '').lower()
                trigger_value = self._config.ha_trigger_to.lower()
                is_transition = previous_value is not None and previous_value != current_value
                previous_value = current_value

                if current_value != trigger_value or not is_transition:
                    continue

                entity = self._entities.get(current_entity_id)
                if not entity:
                    continue

                changed_at = self._parse_ha_datetime(str(row.get('last_changed') or row.get('last_updated') or ''))
                if not changed_at:
                    continue

                local_changed_at = changed_at.astimezone(local_tz)
                if self._history.has_nearby_press(entity.entity_id, local_changed_at.replace(tzinfo=None), self._config.history_dedup_seconds):
                    continue

                added = self._history.add_press(
                    uuid4().hex,
                    entity.entity_id,
                    entity.name,
                    local_changed_at.replace(tzinfo=None).isoformat(timespec='seconds'),
                    local_changed_at.strftime('%Y-%m-%d %H:%M:%S'),
                )
                if added:
                    added_count += 1
        return added_count

    @staticmethod
    def _parse_ha_datetime(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None

    @staticmethod
    def _chunks(items: list[str], size: int):
        for index in range(0, len(items), size):
            yield items[index:index + size]

    async def _load_entities(self) -> None:
        states = await self._client.get_states()
        entities: dict[str, WatchedEntity] = {}

        for state in states:
            if not self._is_watched_state(state):
                continue
            entity_id = str(state.get('entity_id'))
            name = self._entity_name(state)
            entities[entity_id] = WatchedEntity(entity_id=entity_id, name=name)
            self._logger.info('Discovered HA button: %s (%s)', name, entity_id)

        self._entities = entities
        if not self._entities:
            self._logger.warning('No Home Assistant entities matched current filters.')

    def _is_watched_state(self, state: dict[str, Any]) -> bool:
        entity_id = str(state.get('entity_id') or '')
        attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}

        if self._config.ha_entity_ids:
            return entity_id in self._config.ha_entity_ids

        if not self._config.ha_auto_discover:
            return False

        if not entity_id.startswith(f'{self._config.ha_entity_domain}.'):
            return False

        if self._config.ha_entity_id_suffix and not entity_id.endswith(self._config.ha_entity_id_suffix):
            return False

        if self._config.ha_device_class:
            return attributes.get('device_class') == self._config.ha_device_class

        return True

    @staticmethod
    def _entity_name(state: dict[str, Any]) -> str:
        attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        name = str(attributes.get('friendly_name') or state.get('entity_id'))
        return HomeAssistantWatcher._clean_entity_name(name)

    @staticmethod
    def _clean_entity_name(name: str) -> str:
        suffix = 'Безопасность'
        clean_name = name.strip()
        if clean_name.endswith(suffix):
            clean_name = clean_name[: -len(suffix)].strip()
        return clean_name

    async def _handle_ws_message(self, raw_message: str | bytes) -> None:
        payload = json.loads(raw_message)
        if payload.get('type') != 'event':
            return

        event = payload.get('event') or {}
        data = event.get('data') or {}
        entity_id = str(data.get('entity_id') or '')
        if entity_id not in self._entities:
            return

        old_state = data.get('old_state') or {}
        new_state = data.get('new_state') or {}
        old_value = str(old_state.get('state') or '')
        new_value = str(new_state.get('state') or '')
        entity = self._entities[entity_id]

        self._logger.info('HA state change: %s %s -> %s', entity.name, old_value, new_value)

        if not self._is_trigger(old_value, new_value):
            return

        fresh_name = self._entity_name(new_state) if isinstance(new_state, dict) else entity.name
        if fresh_name and fresh_name != entity.name:
            entity = WatchedEntity(entity_id=entity_id, name=fresh_name)
            self._entities[entity_id] = entity

        press_event = self._create_press_event(entity)
        if not press_event:
            return

        await self._broadcast(
            {
                'type': 'press',
                'events': [press_event.to_dict()],
                'status': 'Новых нажатий: 1',
                'watched_count': self.watched_count,
                'last_error': self.last_error,
            }
        )

    def _is_trigger(self, old_value: str, new_value: str) -> bool:
        if new_value.lower() != self._config.ha_trigger_to.lower():
            return False
        if self._config.ha_trigger_from is None:
            return old_value.lower() != new_value.lower()
        return old_value.lower() == self._config.ha_trigger_from.lower()

    def _create_press_event(self, entity: WatchedEntity) -> PressEvent | None:
        pressed_at = datetime.now()
        if self._history.has_nearby_press(entity.entity_id, pressed_at, self._config.history_dedup_seconds):
            self._logger.info('Duplicate HA press ignored for %s', entity.name)
            return None

        event = PressEvent(
            id=uuid4().hex,
            device_id=entity.entity_id,
            device_name=entity.name,
            pressed_at=pressed_at.isoformat(timespec='seconds'),
            pressed_at_display=pressed_at.strftime('%Y-%m-%d %H:%M:%S'),
        )
        added = self._history.add_press(
            event.id,
            event.device_id,
            event.device_name,
            event.pressed_at,
            event.pressed_at_display,
        )
        return event if added else None

    async def _set_status(self, message: str) -> None:
        self.status_message = message
        await self._broadcast(
            {
                'type': 'status',
                'status': message,
                'watched_count': self.watched_count,
                'last_error': self.last_error,
            }
        )

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        dead_clients: list[WebSocket] = []
        for websocket in self._clients:
            try:
                await websocket.send_json(payload)
            except Exception:
                dead_clients.append(websocket)

        for websocket in dead_clients:
            self.unregister(websocket)
