import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from app.cities import CityStore
from app.config import AppConfig
from app.ha_client import HomeAssistantClient
from app.history import HistoryStore


@dataclass(frozen=True)
class WatchedEntity:
    entity_id: str
    name: str
    area_id: str | None = None
    area_name: str | None = None
    sound_path: str | None = None
    sound_repeat_mode: str | None = None
    sound_repeat_seconds: float | None = None
    sound_repeat_count: int | None = None
    ack_button_text: str | None = None
    available: bool = True


@dataclass(frozen=True)
class PressEvent:
    id: str
    device_id: str
    device_name: str
    pressed_at: str
    pressed_at_display: str
    area_id: str | None = None
    area_name: str | None = None
    sound_path: str | None = None
    sound_repeat_mode: str | None = None
    sound_repeat_seconds: float | None = None
    sound_repeat_count: int | None = None
    ack_button_text: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device_name,
            'pressed_at': self.pressed_at,
            'pressed_at_display': self.pressed_at_display,
            'area_id': self.area_id,
            'area_name': self.area_name,
            'sound_path': self.sound_path,
            'sound_repeat_mode': self.sound_repeat_mode,
            'sound_repeat_seconds': self.sound_repeat_seconds,
            'sound_repeat_count': self.sound_repeat_count,
            'ack_button_text': self.ack_button_text,
        }


class HomeAssistantWatcher:
    def __init__(
        self,
        config: AppConfig,
        client: HomeAssistantClient,
        history: HistoryStore,
        cities: CityStore,
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._client = client
        self._history = history
        self._cities = cities
        self._logger = logger
        self._clients: set[WebSocket] = set()
        self._entities: dict[str, WatchedEntity] = {}
        self._areas: list[dict[str, str]] = []
        self._task: asyncio.Task | None = None
        self._running = False
        self.status_message = 'Ожидание запуска'
        self.last_error: str | None = None
        self._history_synced = False

    @property
    def watched_count(self) -> int:
        return len(self._entities)

    @property
    def active_watched_count(self) -> int:
        return sum(1 for entity in self._entities.values() if entity.available)

    def area_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entity in self._entities.values():
            if entity.area_id:
                counts[entity.area_id] = counts.get(entity.area_id, 0) + 1
        return counts

    def active_area_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entity in self._entities.values():
            if entity.area_id and entity.available:
                counts[entity.area_id] = counts.get(entity.area_id, 0) + 1
        return counts

    def areas_payload(self, include_disabled: bool = False) -> list[dict]:
        return [
            city.to_dict()
            for city in self._cities.list(
                self.area_counts(),
                include_disabled=include_disabled,
                active_button_counts=self.active_area_counts(),
            )
        ]


    def entity_area_map(self) -> dict[str, dict[str, str | None]]:
        return {
            entity_id: {
                'area_id': entity.area_id,
                'area_name': entity.area_name,
                'sound_path': entity.sound_path,
                'sound_repeat_mode': entity.sound_repeat_mode,
                'sound_repeat_seconds': entity.sound_repeat_seconds,
                'sound_repeat_count': entity.sound_repeat_count,
                'ack_button_text': entity.ack_button_text,
            }
            for entity_id, entity in self._entities.items()
        }


    async def refresh_areas_from_ha(self) -> None:
        registry = await self._load_registry_safe()
        area_names = self._area_names(registry)
        if area_names:
            self._sync_cities(area_names)
            await self._set_status(self._tracked_status())

    async def reload_entities(self) -> None:
        await self._load_entities()
        self._history_synced = False
        await self._sync_recent_history_once()
        self.last_error = None
        await self._set_status(self._tracked_status())

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
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
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
        await self._set_status(self._tracked_status())

        websocket = await self._client.connect_websocket()
        try:
            await self._client.subscribe_state_changed(websocket)
            self._logger.info('Subscribed to Home Assistant state_changed events.')
            async for raw_message in websocket:
                await self._handle_ws_message(raw_message)
        finally:
            await websocket.close()


    async def _sync_recent_history_once(self) -> None:
        if self._history_synced or not self._entities:
            return

        await self._set_status('Синхронизация истории...')

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
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
                'last_error': self.last_error,
            })
            self._logger.info('HA history sync added %s press events.', added_count)
        else:
            self._logger.info('HA history sync completed: no new press events.')

    async def _sync_recent_history(self) -> int:
        local_tz = datetime.now().astimezone().tzinfo or ZoneInfo('Europe/Moscow')
        end_time = datetime.now(tz=local_tz)
        city_settings = {city.area_id: city for city in self._cities.list(self.area_counts(), include_disabled=True)}
        entity_groups: dict[float, list[str]] = {}
        for entity in self._entities.values():
            hours = self._config.ha_sync_history_hours
            if entity.area_id and entity.area_id in city_settings and city_settings[entity.area_id].history_sync_hours is not None:
                hours = city_settings[entity.area_id].history_sync_hours or 0
            if hours <= 0:
                continue
            entity_groups.setdefault(float(hours), []).append(entity.entity_id)

        added_count = 0

        for hours, entity_ids in entity_groups.items():
            start_time = end_time - timedelta(hours=hours)
            for chunk in self._chunks(entity_ids, 20):
                histories = await self._client.get_history(
                    start_time.isoformat(timespec='seconds'),
                    end_time.isoformat(timespec='seconds'),
                    chunk,
                )
                added_count += self._import_history_rows(histories, local_tz)

        return added_count

    async def sync_history_for_area(self, area_id: str, hours: float | None = None) -> dict[str, Any]:
        if not self._entities:
            await self._load_entities()

        selected_entities = [
            entity.entity_id
            for entity in self._entities.values()
            if entity.area_id == area_id
        ]
        if not selected_entities:
            return {
                'area_id': area_id,
                'entity_count': 0,
                'added': 0,
                'hours': hours,
                'status': 'no_entities',
            }

        if hours is None:
            city_settings = {city.area_id: city for city in self._cities.list(self.area_counts(), include_disabled=True)}
            city = city_settings.get(area_id)
            hours = city.history_sync_hours if city and city.history_sync_hours is not None else self._config.ha_sync_history_hours

        hours = max(0, float(hours or 0))
        if hours <= 0:
            return {
                'area_id': area_id,
                'entity_count': len(selected_entities),
                'added': 0,
                'hours': hours,
                'status': 'disabled',
            }

        local_tz = datetime.now().astimezone().tzinfo or ZoneInfo('Europe/Moscow')
        end_time = datetime.now(tz=local_tz)
        start_time = end_time - timedelta(hours=hours)
        added_count = 0

        for chunk in self._chunks(selected_entities, 20):
            histories = await self._client.get_history(
                start_time.isoformat(timespec='seconds'),
                end_time.isoformat(timespec='seconds'),
                chunk,
            )
            added_count += self._import_history_rows(histories, local_tz)

        await self._broadcast({
            'type': 'history_sync',
            'status': f'История синхронизирована: +{added_count}',
            'watched_count': self.watched_count,
            'active_watched_count': self.active_watched_count,
            'areas': self.areas_payload(),
            'last_error': self.last_error,
        })
        self._logger.info('Manual HA history sync for area %s added %s press events.', area_id, added_count)
        return {
            'area_id': area_id,
            'entity_count': len(selected_entities),
            'added': added_count,
            'hours': hours,
            'status': 'ok',
        }

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
                    entity.area_id,
                    entity.area_name,
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
        registry = await self._load_registry_safe()
        area_names = self._area_names(registry)
        entity_area_ids = self._entity_area_ids(registry)
        entities: dict[str, WatchedEntity] = {}

        for state in states:
            if not self._is_watched_state(state):
                continue
            entity_id = str(state.get('entity_id'))
            area_id = entity_area_ids.get(entity_id)
            area_name = area_names.get(area_id or '') if area_id else None
            name = self._entity_name(state)
            entities[entity_id] = WatchedEntity(
                entity_id=entity_id,
                name=name,
                area_id=area_id,
                area_name=area_name,
                available=self._is_available_state(str(state.get('state') or '')),
            )
            self._logger.info('Discovered HA button: %s (%s, area=%s)', name, entity_id, area_name or '-')

        self._entities = entities
        self._sync_cities(area_names)
        if not self._entities:
            self._logger.warning('No Home Assistant entities matched current filters.')

    async def _load_registry_safe(self) -> dict[str, list[dict[str, Any]]]:
        try:
            return await self._client.get_registry()
        except Exception as exc:
            self._logger.warning('HA registry unavailable; city mapping disabled for now: %s', exc)
            return {'areas': [], 'devices': [], 'entities': []}

    def _sync_cities(self, area_names: dict[str, str]) -> None:
        areas = [
            {'area_id': area_id, 'name': name}
            for area_id, name in area_names.items()
        ]
        self._cities.sync_from_ha_areas(areas, self.area_counts())
        self.refresh_city_settings()

    def refresh_city_settings(self) -> None:
        display_names = self._cities.names_by_area_id()
        city_settings = {city.area_id: city for city in self._cities.list(self.area_counts(), include_disabled=True)}
        self._entities = {
            entity_id: WatchedEntity(
                entity_id=entity.entity_id,
                name=entity.name,
                area_id=entity.area_id,
                area_name=display_names.get(entity.area_id or '', entity.area_name),
                sound_path=city_settings.get(entity.area_id or '').sound_path if city_settings.get(entity.area_id or '') else None,
                sound_repeat_mode=city_settings.get(entity.area_id or '').sound_repeat_mode if city_settings.get(entity.area_id or '') else None,
                sound_repeat_seconds=city_settings.get(entity.area_id or '').sound_repeat_seconds if city_settings.get(entity.area_id or '') else None,
                sound_repeat_count=city_settings.get(entity.area_id or '').sound_repeat_count if city_settings.get(entity.area_id or '') else None,
                ack_button_text=city_settings.get(entity.area_id or '').ack_button_text if city_settings.get(entity.area_id or '') else None,
                available=entity.available,
            )
            for entity_id, entity in self._entities.items()
        }
        updated_rows = self._history.backfill_areas({
            entity.entity_id: (entity.area_id, entity.area_name)
            for entity in self._entities.values()
        })
        if updated_rows:
            self._logger.info('Backfilled area data for %s history rows.', updated_rows)

    @staticmethod
    def _area_names(registry: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
        return {str(area.get('area_id')): str(area.get('name') or area.get('area_id')) for area in registry.get('areas', []) if area.get('area_id')}

    @staticmethod
    def _entity_area_ids(registry: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
        device_area_ids = {
            str(device.get('id')): str(device.get('area_id'))
            for device in registry.get('devices', [])
            if device.get('id') and device.get('area_id')
        }
        mapping: dict[str, str] = {}
        for entity in registry.get('entities', []):
            entity_id = str(entity.get('entity_id') or '')
            if not entity_id:
                continue
            area_id = entity.get('area_id')
            if not area_id and entity.get('device_id'):
                area_id = device_area_ids.get(str(entity.get('device_id')))
            if area_id:
                mapping[entity_id] = str(area_id)
        return mapping

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
    def _is_available_state(value: str) -> bool:
        return value.strip().lower() not in {'unavailable', 'unknown', ''}

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

        fresh_name = self._entity_name(new_state) if isinstance(new_state, dict) else entity.name
        new_available = self._is_available_state(new_value)
        if (fresh_name and fresh_name != entity.name) or new_available != entity.available:
            entity = WatchedEntity(
                entity_id=entity_id,
                name=fresh_name or entity.name,
                area_id=entity.area_id,
                area_name=entity.area_name,
                sound_path=entity.sound_path,
                sound_repeat_mode=entity.sound_repeat_mode,
                sound_repeat_seconds=entity.sound_repeat_seconds,
                sound_repeat_count=entity.sound_repeat_count,
                ack_button_text=entity.ack_button_text,
                available=new_available,
            )
            self._entities[entity_id] = entity
            await self._broadcast(
                {
                    'type': 'availability',
                    'status': self.status_message,
                    'watched_count': self.watched_count,
                    'active_watched_count': self.active_watched_count,
                    'areas': self.areas_payload(),
                    'last_error': self.last_error,
                }
            )

        if not self._is_trigger(old_value, new_value):
            return

        press_event = self._create_press_event(entity)
        if not press_event:
            return

        await self._broadcast(
            {
                'type': 'press',
                'events': [press_event.to_dict()],
                'status': 'Новых нажатий: 1',
                'watched_count': self.watched_count,
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
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
            area_id=entity.area_id,
            area_name=entity.area_name,
            sound_path=entity.sound_path,
            sound_repeat_mode=entity.sound_repeat_mode,
            sound_repeat_seconds=entity.sound_repeat_seconds,
            sound_repeat_count=entity.sound_repeat_count,
            ack_button_text=entity.ack_button_text,
        )
        added = self._history.add_press(
            event.id,
            event.device_id,
            event.device_name,
            event.pressed_at,
            event.pressed_at_display,
            event.area_id,
            event.area_name,
        )
        return event if added else None

    async def _set_status(self, message: str) -> None:
        self.status_message = message
        await self._broadcast(
            {
                'type': 'status',
                'status': message,
                'watched_count': self.watched_count,
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
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

    def _tracked_status(self) -> str:
        return 'HA подключен'
