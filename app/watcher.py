import asyncio
import json
import logging
import re
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
from app.dates import display_date


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
            'pressed_at_display': display_date(self.pressed_at),
            'area_id': self.area_id,
            'area_name': self.area_name,
            'sound_path': self.sound_path,
            'sound_repeat_mode': self.sound_repeat_mode,
            'sound_repeat_seconds': self.sound_repeat_seconds,
            'sound_repeat_count': self.sound_repeat_count,
            'ack_button_text': self.ack_button_text,
        }


class HomeAssistantWatcher:
    TUYA_GATEWAY_URLS = {
        'apigw.tuyaeu.com': 'https://apigw.tuyaeu.com',
        'apigw.iotbing.com': 'https://apigw.iotbing.com',
    }
    TUYA_ERROR_WARNING_SECONDS = 3600

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
        self.on_press = None
        self._clients: set[WebSocket] = set()
        self._entities: dict[str, WatchedEntity] = {}
        self._areas: list[dict[str, str]] = []
        self._task: asyncio.Task | None = None
        self._maintenance_task: asyncio.Task | None = None
        self._ha_websocket: Any | None = None
        self._history_sync_lock = asyncio.Lock()
        self._entity_refresh_lock = asyncio.Lock()
        self._healthcheck_lock = asyncio.Lock()
        self._manual_check_lock = asyncio.Lock()
        self._running = False
        self.status_message = 'Ожидание запуска'
        self.last_error: str | None = None
        self.last_error_at: str | None = None
        self.started_at = self._now_iso()
        self.ha_ws_connected = False
        self.ha_api_ok: bool | None = None
        self.last_api_check_at: str | None = None
        self.last_api_success_at: str | None = None
        self.last_ws_message_at: str | None = None
        self.last_history_sync_at = self._history.get_runtime_state('history_sync_checkpoint')
        self.last_history_sync_added = 0
        self.history_sync_error: str | None = None
        self.last_entity_refresh_at: str | None = None
        self.last_reconnect_at: str | None = None
        self.last_manual_check_at: str | None = None
        self.reconnect_count = 0
        self.last_tuya_check_at: str | None = None
        self.tuya_gateway_ok: bool | None = None
        self.tuya_gateway_status_code: int | None = None
        self.tuya_integration_loaded: bool | None = None
        self.tuya_entries_total = 0
        self.tuya_entries_loaded = 0
        self.tuya_last_error_at: str | None = None
        self.tuya_last_error: str | None = None
        self.tuya_recent_error = False
        self.tuya_check_error: str | None = None
        self.tuya_endpoints: dict[str, dict[str, Any]] = {
            host: {
                'url': url,
                'reachable': None,
                'status_code': None,
                'last_error_at': None,
                'last_error': None,
                'recent_error': False,
            }
            for host, url in self.TUYA_GATEWAY_URLS.items()
        }
        latest_press = self._history.latest_press()
        self.last_press_at = latest_press.get('pressed_at') if latest_press else None
        self.last_press_name = latest_press.get('device_name') if latest_press else None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec='seconds')

    @property
    def watched_count(self) -> int:
        return len(self._entities)

    @property
    def active_watched_count(self) -> int:
        return sum(1 for entity in self._entities.values() if entity.available)

    @property
    def unavailable_entities(self) -> list[dict[str, str | None]]:
        return [
            {
                'entity_id': entity.entity_id,
                'name': entity.name,
                'area_id': entity.area_id,
                'area_name': entity.area_name,
            }
            for entity in self._entities.values()
            if not entity.available
        ]

    def system_snapshot(self) -> dict[str, Any]:
        unavailable = self.unavailable_entities
        history_age = self._age_seconds(self.last_history_sync_at)
        history_stale = history_age is None or history_age > self._config.ha_history_reconcile_seconds * 2
        endpoint_warning = any(
            endpoint.get('reachable') is False or endpoint.get('recent_error')
            for endpoint in self.tuya_endpoints.values()
        )
        tuya_warning = (
            self.tuya_gateway_ok is False
            or self.tuya_integration_loaded is False
            or self.tuya_recent_error
            or endpoint_warning
            or bool(self.tuya_check_error)
        )
        if not self._running:
            overall = 'starting'
            summary = 'PereHomeLab запускается'
        elif not self.ha_ws_connected or self.ha_api_ok is False:
            overall = 'error'
            summary = 'Проблема с Home Assistant'
        elif tuya_warning:
            overall = 'warning'
            summary = 'Есть проблема или недавняя ошибка Tuya'
        elif self.history_sync_error or history_stale or unavailable:
            overall = 'warning'
            summary = 'Есть предупреждения'
        else:
            overall = 'ok'
            summary = 'Система работает'

        return {
            'overall': overall,
            'summary': summary,
            'app_version': self._config.app_version,
            'started_at': self.started_at,
            'browser_clients': len(self._clients),
            'ha_url': self._config.ha_url,
            'ha_ws_connected': self.ha_ws_connected,
            'ha_api_ok': self.ha_api_ok,
            'last_api_check_at': self.last_api_check_at,
            'last_api_success_at': self.last_api_success_at,
            'last_ws_message_at': self.last_ws_message_at,
            'last_history_sync_at': self.last_history_sync_at,
            'last_history_sync_added': self.last_history_sync_added,
            'history_stale': history_stale,
            'history_sync_error': self.history_sync_error,
            'last_entity_refresh_at': self.last_entity_refresh_at,
            'last_reconnect_at': self.last_reconnect_at,
            'last_manual_check_at': self.last_manual_check_at,
            'reconnect_count': self.reconnect_count,
            'last_tuya_check_at': self.last_tuya_check_at,
            'tuya_gateway_ok': self.tuya_gateway_ok,
            'tuya_gateway_status_code': self.tuya_gateway_status_code,
            'tuya_integration_loaded': self.tuya_integration_loaded,
            'tuya_entries_total': self.tuya_entries_total,
            'tuya_entries_loaded': self.tuya_entries_loaded,
            'tuya_last_error_at': self.tuya_last_error_at,
            'tuya_last_error': self.tuya_last_error,
            'tuya_recent_error': self.tuya_recent_error,
            'tuya_check_error': self.tuya_check_error,
            'tuya_endpoints': {
                host: dict(endpoint)
                for host, endpoint in self.tuya_endpoints.items()
            },
            'watched_count': self.watched_count,
            'active_watched_count': self.active_watched_count,
            'unavailable_count': len(unavailable),
            'unavailable_entities': unavailable[:20],
            'last_press_at': self.last_press_at,
            'last_press_name': self.last_press_name,
            'status': self.status_message,
            'last_error': self.last_error,
            'last_error_at': self.last_error_at,
        }

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
        await self._load_entities()
        await self._set_status(self._tracked_status())

    async def reload_entities(self) -> None:
        await self._load_entities()
        await self.reconcile_history(force=True)
        await self._set_status(self._tracked_status())

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())
        self._maintenance_task = asyncio.create_task(self._run_maintenance())

    async def stop(self) -> None:
        self._running = False
        tasks = [task for task in (self._task, self._maintenance_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
                'system': self.system_snapshot(),
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
                self.ha_ws_connected = False
                self.last_error = str(exc)
                self.last_error_at = self._now_iso()
                self.reconnect_count += 1
                self.last_reconnect_at = self._now_iso()
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
        websocket = await self._client.connect_websocket()
        self._ha_websocket = websocket
        try:
            await self._client.subscribe_state_changed(websocket)
            self.ha_ws_connected = True
            self.last_ws_message_at = self._now_iso()
            self.last_error = None
            self.last_error_at = None
            self._logger.info('Subscribed to Home Assistant state_changed events.')
            await self._set_status(self._tracked_status())
            # Подписываемся до сверки истории: события, пришедшие во время
            # REST-запроса, останутся в буфере WebSocket и не потеряются.
            await self.reconcile_history(force=True)
            async for raw_message in websocket:
                self.last_ws_message_at = self._now_iso()
                await self._handle_ws_message(raw_message)
            raise ConnectionError('Home Assistant websocket closed')
        finally:
            self.ha_ws_connected = False
            self._ha_websocket = None
            await websocket.close()
            if self._running:
                await self._set_status('Переподключение к Home Assistant...')

    @staticmethod
    def _age_seconds(value: str | None) -> float | None:
        parsed = HomeAssistantWatcher._parse_ha_datetime(value or '')
        if not parsed:
            return None
        now = datetime.now().astimezone()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return max(0.0, (now - parsed.astimezone(now.tzinfo)).total_seconds())

    async def _run_maintenance(self) -> None:
        await asyncio.sleep(min(5.0, self._config.ha_healthcheck_seconds))
        while self._running:
            try:
                api_age = self._age_seconds(self.last_api_check_at)
                if api_age is None or api_age >= self._config.ha_healthcheck_seconds:
                    await self._check_ha_api()

                tuya_age = self._age_seconds(self.last_tuya_check_at)
                if self.ha_api_ok and (tuya_age is None or tuya_age >= max(60, self._config.ha_healthcheck_seconds)):
                    await self._check_tuya()

                history_age = self._age_seconds(self.last_history_sync_at)
                if history_age is None or history_age >= self._config.ha_history_reconcile_seconds:
                    await self.reconcile_history()

                entity_age = self._age_seconds(self.last_entity_refresh_at)
                if entity_age is None or entity_age >= self._config.ha_entity_refresh_seconds:
                    await self._load_entities()
                    await self._broadcast_system('availability')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.last_error_at = self._now_iso()
                self._logger.warning('HA maintenance check failed: %s', exc)
                await self._broadcast_system('system_status')

            await asyncio.sleep(min(15.0, self._config.ha_healthcheck_seconds))

    async def _check_ha_api(self) -> bool:
        async with self._healthcheck_lock:
            self.last_api_check_at = self._now_iso()
            try:
                await self._client.health_check()
            except Exception as exc:
                self.ha_api_ok = False
                self.last_error = str(exc)
                self.last_error_at = self._now_iso()
                if self._ha_websocket is not None:
                    await self._ha_websocket.close()
                await self._broadcast_system('system_status')
                return False

            self.ha_api_ok = True
            self.last_api_success_at = self._now_iso()
            if self.ha_ws_connected and not self.history_sync_error:
                self.last_error = None
                self.last_error_at = None
            await self._broadcast_system('system_status')
            return True

    async def check_now(self) -> dict[str, Any]:
        async with self._manual_check_lock:
            if self._age_seconds(self.last_manual_check_at) is not None and self._age_seconds(self.last_manual_check_at) < 10:
                return self.system_snapshot()
            self.last_manual_check_at = self._now_iso()
            await self._check_ha_api()
            if self.ha_api_ok:
                await self._check_tuya()
                await self.reconcile_history(force=True)
            await self._broadcast_system('system_status')
            return self.system_snapshot()

    async def _check_tuya(self) -> None:
        self.last_tuya_check_at = self._now_iso()
        results = await asyncio.gather(
            self._client.get_config_entries('tuya'),
            self._client.get_error_log(),
            *(self._client.probe_url(url) for url in self.TUYA_GATEWAY_URLS.values()),
            return_exceptions=True,
        )
        errors: list[str] = []

        entries_result, log_result, *gateway_results = results
        if isinstance(entries_result, Exception):
            self.tuya_integration_loaded = None
            errors.append('не удалось проверить интеграцию Tuya в HA')
        else:
            self.tuya_entries_total = len(entries_result)
            self.tuya_entries_loaded = sum(1 for entry in entries_result if entry.get('state') == 'loaded')
            self.tuya_integration_loaded = self.tuya_entries_total > 0 and self.tuya_entries_loaded == self.tuya_entries_total

        if isinstance(log_result, Exception):
            errors.append('не удалось прочитать журнал Tuya в HA')
            overall_recent_error = False
        else:
            parsed_error = self._parse_tuya_error_log(log_result)
            self.tuya_last_error_at = parsed_error.get('at') if parsed_error else None
            self.tuya_last_error = parsed_error.get('message') if parsed_error else None
            error_age = self._age_seconds(self.tuya_last_error_at)
            overall_recent_error = error_age is not None and error_age <= self.TUYA_ERROR_WARNING_SECONDS

        endpoint_states: list[bool | None] = []
        for (host, url), gateway_result in zip(self.TUYA_GATEWAY_URLS.items(), gateway_results):
            endpoint = self.tuya_endpoints[host]
            endpoint['url'] = url
            if isinstance(log_result, Exception):
                parsed_endpoint_error = None
            else:
                parsed_endpoint_error = self._parse_tuya_error_log(log_result, host=host)
            endpoint['last_error_at'] = parsed_endpoint_error.get('at') if parsed_endpoint_error else None
            endpoint['last_error'] = parsed_endpoint_error.get('message') if parsed_endpoint_error else None
            endpoint_error_age = self._age_seconds(endpoint['last_error_at'])
            endpoint['recent_error'] = (
                endpoint_error_age is not None
                and endpoint_error_age <= self.TUYA_ERROR_WARNING_SECONDS
            )

            if isinstance(gateway_result, Exception):
                endpoint['reachable'] = None
                endpoint['status_code'] = None
                errors.append(f'не удалось проверить {host}')
            else:
                endpoint['reachable'] = bool(gateway_result.get('reachable'))
                endpoint['status_code'] = gateway_result.get('status_code')
                if not endpoint['reachable']:
                    errors.append(f'{host} не отвечает')
            endpoint_states.append(endpoint['reachable'])

        self.tuya_recent_error = overall_recent_error or any(
            bool(endpoint.get('recent_error')) for endpoint in self.tuya_endpoints.values()
        )
        if any(state is False for state in endpoint_states):
            self.tuya_gateway_ok = False
        elif endpoint_states and all(state is True for state in endpoint_states):
            self.tuya_gateway_ok = True
        else:
            self.tuya_gateway_ok = None
        primary_endpoint = self.tuya_endpoints.get('apigw.tuyaeu.com', {})
        self.tuya_gateway_status_code = primary_endpoint.get('status_code')

        self.tuya_check_error = '; '.join(errors) if errors else None
        await self._broadcast_system('system_status')

    @staticmethod
    def _parse_tuya_error_log(log_text: str, host: str | None = None) -> dict[str, str] | None:
        header_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(ERROR|WARNING)\b')
        blocks: list[tuple[str, str, list[str]]] = []
        current: tuple[str, str, list[str]] | None = None
        for line in log_text.splitlines():
            match = header_pattern.match(line)
            if match:
                if current:
                    blocks.append(current)
                current = (match.group(1), match.group(2), [line])
            elif current:
                current[2].append(line)
        if current:
            blocks.append(current)

        if host:
            relevant = [block for block in blocks if host.lower() in '\n'.join(block[2]).lower()]
        else:
            relevant = [
                block for block in blocks
                if re.search(r'tuya|apigw\.(?:tuyaeu|iotbing)\.com', '\n'.join(block[2]), re.IGNORECASE)
            ]
        if not relevant:
            return None
        timestamp, level, lines = relevant[-1]
        text = '\n'.join(lines).lower()
        if 'timed out' in text or 'timeout' in text:
            message = 'Таймаут соединения с облаком Tuya'
        elif 'auth' in text or 'unauthorized' in text:
            message = 'Ошибка авторизации интеграции Tuya'
        elif 'error setting up' in text or 'setup' in text:
            message = 'Ошибка запуска интеграции Tuya'
        else:
            message = 'Ошибка интеграции Tuya' if level == 'ERROR' else 'Предупреждение интеграции Tuya'
        parsed_at = datetime.fromisoformat(timestamp).astimezone().isoformat(timespec='seconds')
        return {'at': parsed_at, 'message': message}

    async def reconcile_history(self, force: bool = False) -> int:
        if not self._entities:
            return 0
        if self._history_sync_lock.locked() and not force:
            return 0

        async with self._history_sync_lock:
            try:
                added_count, checkpoint = await self._sync_recent_history()
            except Exception as exc:
                self.history_sync_error = str(exc)
                self.last_error = str(exc)
                self.last_error_at = self._now_iso()
                self._logger.warning('HA history sync failed: %s', exc)
                await self._broadcast_system('system_status')
                return 0

            self.last_history_sync_at = checkpoint
            self.last_history_sync_added = added_count
            self.history_sync_error = None
            if self.ha_ws_connected and self.ha_api_ok is not False:
                self.last_error = None
                self.last_error_at = None
            self._history.set_runtime_state('history_sync_checkpoint', checkpoint, checkpoint)
            latest_press = self._history.latest_press()
            if latest_press:
                self.last_press_at = latest_press.get('pressed_at')
                self.last_press_name = latest_press.get('device_name')

            await self._broadcast({
                'type': 'history_sync',
                'status': self._tracked_status(),
                'watched_count': self.watched_count,
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
                'last_error': self.last_error,
                'system': self.system_snapshot(),
            })
            self._logger.info('HA history sync completed: +%s press events.', added_count)
            return added_count

    async def _sync_recent_history(self) -> tuple[int, str]:
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
        checkpoint = self._parse_ha_datetime(self.last_history_sync_at or '')

        for hours, entity_ids in entity_groups.items():
            if checkpoint:
                start_time = checkpoint.astimezone(local_tz) - timedelta(seconds=self._config.ha_history_overlap_seconds)
                # Защита от ошибочного/слишком старого checkpoint.
                start_time = max(start_time, end_time - timedelta(days=7))
            else:
                start_time = end_time - timedelta(hours=hours)
            if start_time >= end_time:
                start_time = end_time - timedelta(seconds=max(1, self._config.ha_history_overlap_seconds))
            for chunk in self._chunks(entity_ids, 20):
                histories = await self._client.get_history(
                    start_time.isoformat(timespec='seconds'),
                    end_time.isoformat(timespec='seconds'),
                    chunk,
                )
                added_count += self._import_history_rows(histories, local_tz, start_time)

        return added_count, end_time.isoformat(timespec='seconds')

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
            added_count += self._import_history_rows(histories, local_tz, start_time)

        await self._broadcast({
            'type': 'history_sync',
            'status': f'История синхронизирована: +{added_count}',
            'watched_count': self.watched_count,
            'active_watched_count': self.active_watched_count,
            'areas': self.areas_payload(),
            'last_error': self.last_error,
            'system': self.system_snapshot(),
        })
        self._logger.info('Manual HA history sync for area %s added %s press events.', area_id, added_count)
        return {
            'area_id': area_id,
            'entity_count': len(selected_entities),
            'added': added_count,
            'hours': hours,
            'status': 'ok',
        }

    def _import_history_rows(
        self,
        histories: list[list[dict[str, Any]]],
        local_tz,
        period_start: datetime | None = None,
    ) -> int:
        added_count = 0
        for history_rows in histories:
            current_entity_id = ''
            previous_value: str | None = None
            for row in history_rows:
                if row.get('entity_id'):
                    current_entity_id = str(row.get('entity_id'))

                changed_at = self._parse_ha_datetime(str(row.get('last_changed') or row.get('last_updated') or ''))
                if not changed_at:
                    previous_value = str(row.get('state') or '').lower()
                    continue

                current_value = str(row.get('state') or '').lower()
                is_transition = previous_value is not None and self._is_trigger(previous_value, current_value)
                previous_value = current_value
                if not is_transition:
                    continue

                entity = self._entities.get(current_entity_id)
                if not entity:
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
        async with self._entity_refresh_lock:
            await self._load_entities_unlocked()

    async def _load_entities_unlocked(self) -> None:
        states = await self._client.get_states()
        registry = await self._load_registry_safe()
        area_names = self._area_names(registry)
        entity_area_ids = self._entity_area_ids(registry)
        previous_entities = self._entities
        entities: dict[str, WatchedEntity] = {}

        for state in states:
            if not self._is_watched_state(state):
                continue
            entity_id = str(state.get('entity_id'))
            previous_entity = previous_entities.get(entity_id)
            area_id = entity_area_ids.get(entity_id) or (previous_entity.area_id if previous_entity else None)
            area_name = area_names.get(area_id or '') if area_id else None
            if not area_name and previous_entity:
                area_name = previous_entity.area_name
            name = self._entity_name(state)
            entities[entity_id] = WatchedEntity(
                entity_id=entity_id,
                name=name,
                area_id=area_id,
                area_name=area_name,
                available=self._is_available_state(str(state.get('state') or '')),
            )
            if not previous_entity or (previous_entity.name, previous_entity.area_id) != (name, area_id):
                self._logger.info('Discovered HA button: %s (%s, area=%s)', name, entity_id, area_name or '-')

        self._entities = entities
        self.last_entity_refresh_at = self._now_iso()
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
                    'system': self.system_snapshot(),
                }
            )

        if not self._is_trigger(old_value, new_value):
            return

        changed_at = self._parse_ha_datetime(str(new_state.get('last_changed') or new_state.get('last_updated') or ''))
        press_event = self._create_press_event(entity, changed_at)
        if not press_event:
            return

        if self.on_press:
            self.on_press(press_event.to_dict())

        await self._broadcast(
            {
                'type': 'press',
                'events': [press_event.to_dict()],
                'status': 'Новых нажатий: 1',
                'watched_count': self.watched_count,
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
                'last_error': self.last_error,
                'system': self.system_snapshot(),
            }
        )

    def _is_trigger(self, old_value: str, new_value: str) -> bool:
        if new_value.lower() != self._config.ha_trigger_to.lower():
            return False
        if old_value.strip().lower() in {'', 'unknown', 'unavailable'}:
            return False
        if self._config.ha_trigger_from is None:
            return old_value.lower() != new_value.lower()
        return old_value.lower() == self._config.ha_trigger_from.lower()

    def _create_press_event(self, entity: WatchedEntity, ha_changed_at: datetime | None = None) -> PressEvent | None:
        local_tz = datetime.now().astimezone().tzinfo or ZoneInfo('Europe/Moscow')
        pressed_at = ha_changed_at.astimezone(local_tz).replace(tzinfo=None) if ha_changed_at else datetime.now()
        existing = self._history.nearby_press(entity.entity_id, pressed_at, self._config.history_dedup_seconds)
        if existing and (existing['live_notified'] or existing['acknowledged_at']):
            return None

        event = PressEvent(
            id=existing['id'] if existing else uuid4().hex,
            device_id=entity.entity_id,
            device_name=entity.name,
            pressed_at=existing['pressed_at'] if existing else pressed_at.isoformat(timespec='seconds'),
            pressed_at_display=existing['pressed_at_display'] if existing else pressed_at.strftime('%Y-%m-%d %H:%M:%S'),
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
        notified = self._history.mark_live_notified(event.id)
        if notified:
            self.last_press_at = event.pressed_at
            self.last_press_name = event.device_name
        return event if notified else None

    async def broadcast_acknowledged(self, items: list[dict]) -> None:
        await self._broadcast({'type': 'acknowledged', 'events': items})

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
                'system': self.system_snapshot(),
            }
        )

    async def _broadcast_system(self, event_type: str = 'system_status') -> None:
        await self._broadcast(
            {
                'type': event_type,
                'status': self.status_message,
                'watched_count': self.watched_count,
                'active_watched_count': self.active_watched_count,
                'areas': self.areas_payload(),
                'last_error': self.last_error,
                'system': self.system_snapshot(),
            }
        )

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        clients = list(self._clients)
        if not clients:
            return

        async def send(websocket: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(websocket.send_json(payload), timeout=5)
                return None
            except Exception:
                return websocket

        results = await asyncio.gather(*(send(websocket) for websocket in clients))
        for websocket in results:
            if websocket is not None:
                self.unregister(websocket)

    def _tracked_status(self) -> str:
        return 'HA подключен'
