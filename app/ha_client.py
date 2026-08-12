import asyncio
import itertools
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
import websockets

from app.config import AppConfig


class HomeAssistantError(Exception):
    pass


class HomeAssistantClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._counter = itertools.count(1)

    @property
    def api_url(self) -> str:
        return self._config.ha_url

    @property
    def websocket_url(self) -> str:
        parsed = urlparse(self._config.ha_url)
        scheme = 'wss' if parsed.scheme == 'https' else 'ws'
        return urlunparse((scheme, parsed.netloc, '/api/websocket', '', '', ''))

    @property
    def headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self._config.ha_token}',
            'Content-Type': 'application/json',
        }

    async def health_check(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._health_check_sync)

    def _health_check_sync(self) -> dict[str, Any]:
        try:
            response = requests.get(
                f'{self._config.ha_url}/api/',
                headers=self.headers,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise HomeAssistantError(f'HA API is unreachable at {self._config.ha_url}: {exc}') from exc

        if response.status_code == 401:
            raise HomeAssistantError('HA API authentication failed: check HA_TOKEN.')
        if response.status_code != 200:
            raise HomeAssistantError(f'HA API health error {response.status_code}: {response.text[:300]}')

        return {
            'status_code': response.status_code,
            'message': response.text[:300],
        }


    async def get_registry(self) -> dict[str, list[dict[str, Any]]]:
        websocket = await self.connect_websocket()
        try:
            return {
                'areas': await self._registry_ws_request(websocket, 'config/area_registry/list'),
                'devices': await self._registry_ws_request(websocket, 'config/device_registry/list'),
                'entities': await self._registry_ws_request(websocket, 'config/entity_registry/list'),
            }
        finally:
            await websocket.close()

    async def get_config_entries(self, domain: str | None = None) -> list[dict[str, Any]]:
        websocket = await self.connect_websocket()
        try:
            entries = await self._registry_ws_request(websocket, 'config_entries/get')
        finally:
            await websocket.close()
        if not domain:
            return entries
        clean_domain = domain.strip().lower()
        return [entry for entry in entries if str(entry.get('domain') or '').lower() == clean_domain]

    async def get_error_log(self) -> str:
        return await asyncio.to_thread(self._get_error_log_sync)

    def _get_error_log_sync(self) -> str:
        try:
            response = requests.get(
                f'{self._config.ha_url}/api/error_log',
                headers=self.headers,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise HomeAssistantError(f'HA error log is unreachable: {exc}') from exc
        if response.status_code != 200:
            raise HomeAssistantError(f'HA error log returned {response.status_code}')
        return response.text

    async def probe_url(self, url: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._probe_url_sync, url)

    @staticmethod
    def _probe_url_sync(url: str) -> dict[str, Any]:
        try:
            response = requests.get(url, timeout=10)
        except requests.RequestException as exc:
            return {'reachable': False, 'status_code': None, 'error': str(exc)}
        return {
            'reachable': response.status_code < 500,
            'status_code': response.status_code,
            'error': None if response.status_code < 500 else f'HTTP {response.status_code}',
        }

    async def _registry_ws_request(self, websocket: Any, request_type: str) -> list[dict[str, Any]]:
        import json
        request_id = next(self._counter)
        await websocket.send(json.dumps({'id': request_id, 'type': request_type}))
        while True:
            payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if payload.get('id') != request_id:
                continue
            if not payload.get('success'):
                raise HomeAssistantError(f'HA registry command failed: {payload}')
            result = payload.get('result')
            if not isinstance(result, list):
                raise HomeAssistantError(f'Unexpected HA registry response for {request_type}')
            return result

    async def get_states(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_states_sync)

    def _get_states_sync(self) -> list[dict[str, Any]]:
        try:
            response = requests.get(
                f'{self._config.ha_url}/api/states',
                headers=self.headers,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise HomeAssistantError(f'HA states are unreachable at {self._config.ha_url}: {exc}') from exc

        if response.status_code == 401:
            raise HomeAssistantError('HA states authentication failed: check HA_TOKEN.')
        if response.status_code != 200:
            raise HomeAssistantError(f'HA states error {response.status_code}: {response.text[:300]}')
        payload = response.json()
        if not isinstance(payload, list):
            raise HomeAssistantError('Unexpected HA states response')
        return payload


    async def get_history(
        self,
        start_time: str,
        end_time: str,
        entity_ids: list[str],
    ) -> list[list[dict[str, Any]]]:
        return await asyncio.to_thread(self._get_history_sync, start_time, end_time, entity_ids)

    def _get_history_sync(
        self,
        start_time: str,
        end_time: str,
        entity_ids: list[str],
    ) -> list[list[dict[str, Any]]]:
        params = {
            'filter_entity_id': ','.join(entity_ids),
            'end_time': end_time,
        }
        try:
            response = requests.get(
                f'{self._config.ha_url}/api/history/period/{start_time}',
                headers=self.headers,
                params=params,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise HomeAssistantError(f'HA history is unreachable at {self._config.ha_url}: {exc}') from exc

        if response.status_code == 401:
            raise HomeAssistantError('HA history authentication failed: check HA_TOKEN.')
        if response.status_code != 200:
            raise HomeAssistantError(f'HA history error {response.status_code}: {response.text[:300]}')

        payload = response.json()
        if not isinstance(payload, list):
            raise HomeAssistantError('Unexpected HA history response')
        return payload

    async def connect_websocket(self) -> Any:
        try:
            websocket = await websockets.connect(
                self.websocket_url,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=10,
                close_timeout=5,
                max_queue=1024,
            )
        except Exception as exc:
            raise HomeAssistantError(f'HA websocket is unreachable at {self.websocket_url}: {exc}') from exc

        try:
            hello = await asyncio.wait_for(websocket.recv(), timeout=10)
            import json
            payload = json.loads(hello)
            if payload.get('type') != 'auth_required':
                raise HomeAssistantError(f'Unexpected HA websocket hello: {payload}')

            await websocket.send(json.dumps({'type': 'auth', 'access_token': self._config.ha_token}))
            auth_result = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
            if auth_result.get('type') != 'auth_ok':
                raise HomeAssistantError(f'HA websocket auth failed: {auth_result}')
            return websocket
        except Exception:
            await websocket.close()
            raise

    async def subscribe_state_changed(self, websocket: Any) -> int:
        import json
        request_id = next(self._counter)
        await websocket.send(json.dumps({'id': request_id, 'type': 'subscribe_events', 'event_type': 'state_changed'}))
        response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
        if response.get('id') != request_id or not response.get('success'):
            raise HomeAssistantError(f'HA subscribe failed: {response}')
        return request_id
