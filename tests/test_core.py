import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.cities import CityStore
from app.config import AppConfig, PROJECT_ROOT
from app.history import HistoryStore
from app.watcher import HomeAssistantWatcher, WatchedEntity


def make_config(root: Path) -> AppConfig:
    return AppConfig(
        ha_url='http://ha.test:8123',
        ha_token='test-token',
        ha_auto_discover=True,
        ha_entity_ids=[],
        ha_entity_domain='binary_sensor',
        ha_device_class='safety',
        ha_entity_id_suffix=None,
        ha_trigger_from=None,
        ha_trigger_to='on',
        ha_reconnect_seconds=1,
        ha_refresh_entities_on_reconnect=False,
        ha_sync_history_hours=24,
        ha_history_reconcile_seconds=300,
        ha_history_overlap_seconds=30,
        ha_healthcheck_seconds=30,
        ha_entity_refresh_seconds=900,
        ack_button_text='ФА',
        button_sound=root / 'sound.mp3',
        sound_play_seconds=25,
        history_db_path=root / 'history.db',
        history_dedup_seconds=3,
        admin_username='admin',
        admin_password='test-password',
        app_download_dir=root,
        app_download_filename='app.exe',
        app_version='test',
    )


class FakeClient:
    def __init__(self) -> None:
        self.history_calls = []

    async def get_history(self, start_time, end_time, entity_ids):
        self.history_calls.append((start_time, end_time, entity_ids))
        return [[]]

    async def health_check(self):
        return {'status_code': 200}


class WatcherTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_config(self.root)
        self.history = HistoryStore(self.config.history_db_path)
        self.cities = CityStore(self.config.history_db_path)
        self.client = FakeClient()
        self.watcher = HomeAssistantWatcher(
            self.config,
            self.client,
            self.history,
            self.cities,
            logging.getLogger('test'),
        )
        self.entity = WatchedEntity('binary_sensor.button_safety', 'Кнопка', 'spb', 'SPB')
        self.watcher._entities = {self.entity.entity_id: self.entity}

    def tearDown(self):
        self.temp.cleanup()

    def test_recovery_from_unavailable_is_not_a_press(self):
        self.assertFalse(self.watcher._is_trigger('unavailable', 'on'))
        self.assertFalse(self.watcher._is_trigger('unknown', 'on'))
        self.assertTrue(self.watcher._is_trigger('off', 'on'))

    def test_history_transition_is_imported_once(self):
        start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [[
            {'entity_id': self.entity.entity_id, 'state': 'off', 'last_changed': (start - timedelta(minutes=1)).isoformat()},
            {'entity_id': self.entity.entity_id, 'state': 'on', 'last_changed': (start + timedelta(seconds=5)).isoformat()},
            {'entity_id': self.entity.entity_id, 'state': 'off', 'last_changed': (start + timedelta(seconds=6)).isoformat()},
        ]]
        self.assertEqual(self.watcher._import_history_rows(rows, timezone.utc, start), 1)
        self.assertEqual(self.watcher._import_history_rows(rows, timezone.utc, start), 0)
        self.assertEqual(self.history.page(1, 10)['total'], 1)

    def test_first_on_inside_period_is_not_lost(self):
        start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [[{
            'entity_id': self.entity.entity_id,
            'state': 'on',
            'last_changed': (start + timedelta(seconds=2)).isoformat(),
        }]]
        self.assertEqual(self.watcher._import_history_rows(rows, timezone.utc, start), 1)

    def test_old_on_baseline_is_not_counted_as_a_new_press(self):
        start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [[{
            'entity_id': self.entity.entity_id,
            'state': 'on',
            'last_changed': (start - timedelta(minutes=5)).isoformat(),
        }]]
        self.assertEqual(self.watcher._import_history_rows(rows, timezone.utc, start), 0)

    def test_system_snapshot_explains_healthy_state(self):
        self.watcher._running = True
        self.watcher.ha_ws_connected = True
        self.watcher.ha_api_ok = True
        self.watcher.last_history_sync_at = datetime.now().astimezone().isoformat(timespec='seconds')
        snapshot = self.watcher.system_snapshot()
        self.assertEqual(snapshot['overall'], 'ok')
        self.assertEqual(snapshot['active_watched_count'], 1)
        self.assertEqual(snapshot['unavailable_count'], 0)

    def test_tuya_timeout_is_detected_in_ha_log(self):
        payload = self.watcher._parse_tuya_error_log(
            "2026-08-12 19:15:39.792 ERROR (MainThread) [homeassistant.config_entries] "
            "Error setting up entry for tuya\n"
            "requests.exceptions.ReadTimeout: apigw.tuyaeu.com timed out"
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload['message'], 'Таймаут соединения с облаком Tuya')
        self.assertIsNotNone(datetime.fromisoformat(payload['at']).tzinfo)

    def test_iotbing_timeout_is_detected_and_blocks_green_status(self):
        payload = self.watcher._parse_tuya_error_log(
            "2026-08-12 21:00:00 ERROR (MainThread) [tuya] request failed\n"
            "ReadTimeout: apigw.iotbing.com timed out",
            host='apigw.iotbing.com',
        )
        self.assertIsNotNone(payload)
        self.watcher._running = True
        self.watcher.ha_ws_connected = True
        self.watcher.ha_api_ok = True
        self.watcher.last_history_sync_at = datetime.now().astimezone().isoformat(timespec='seconds')
        self.watcher.tuya_endpoints['apigw.iotbing.com']['recent_error'] = True
        snapshot = self.watcher.system_snapshot()
        self.assertEqual(snapshot['overall'], 'warning')

    def test_tuya_connectivity_problem_changes_overall_status(self):
        self.watcher._running = True
        self.watcher.ha_ws_connected = True
        self.watcher.ha_api_ok = True
        self.watcher.last_history_sync_at = datetime.now().astimezone().isoformat(timespec='seconds')
        self.watcher.tuya_gateway_ok = False
        snapshot = self.watcher.system_snapshot()
        self.assertEqual(snapshot['overall'], 'warning')
        self.assertIn('Tuya', snapshot['summary'])

    async def test_reconcile_uses_durable_checkpoint_with_overlap(self):
        checkpoint = datetime.now().astimezone() - timedelta(minutes=10)
        self.watcher.last_history_sync_at = checkpoint.isoformat(timespec='seconds')
        _, new_checkpoint = await self.watcher._sync_recent_history()
        self.assertTrue(self.client.history_calls)
        requested_start = datetime.fromisoformat(self.client.history_calls[0][0])
        expected_start = checkpoint - timedelta(seconds=self.config.ha_history_overlap_seconds)
        self.assertLess(abs((requested_start - expected_start).total_seconds()), 2)
        self.assertGreater(datetime.fromisoformat(new_checkpoint), checkpoint)

    async def test_live_press_is_saved_and_sent_to_browser(self):
        broadcasts = []

        async def capture(payload):
            broadcasts.append(payload)

        self.watcher._broadcast = capture
        changed_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        await self.watcher._handle_ws_message(json.dumps({
            'type': 'event',
            'event': {
                'data': {
                    'entity_id': self.entity.entity_id,
                    'old_state': {'state': 'off'},
                    'new_state': {
                        'entity_id': self.entity.entity_id,
                        'state': 'on',
                        'last_changed': changed_at,
                        'attributes': {'friendly_name': 'Кнопка Безопасность'},
                    },
                },
            },
        }))

        self.assertEqual(self.history.page(1, 10)['total'], 1)
        press_payloads = [payload for payload in broadcasts if payload.get('type') == 'press']
        self.assertEqual(len(press_payloads), 1)
        self.assertEqual(press_payloads[0]['events'][0]['device_name'], 'Кнопка')

    def test_acknowledgement_is_idempotent(self):
        now = datetime.now().astimezone().replace(tzinfo=None).isoformat(timespec='seconds')
        self.history.add_press('press-1', self.entity.entity_id, self.entity.name, now, now, 'spb', 'SPB')
        self.history.acknowledge(['press-1'], now, 'first')
        self.history.acknowledge(['press-1'], now, 'second')
        item = self.history.page(1, 10)['items'][0]
        self.assertEqual(item['acknowledged_at_display'], 'first')

    def test_runtime_state_and_latest_press_survive_new_store(self):
        now = datetime.now().astimezone().isoformat(timespec='seconds')
        self.history.set_runtime_state('checkpoint', now, now)
        self.history.add_press('id', self.entity.entity_id, self.entity.name, now, now, 'spb', 'SPB')
        reopened = HistoryStore(self.config.history_db_path)
        self.assertEqual(reopened.get_runtime_state('checkpoint'), now)
        self.assertEqual(reopened.latest_press()['device_name'], self.entity.name)


class FrontendContractTestCase(unittest.TestCase):
    def test_status_dialog_and_responsive_rules_exist(self):
        html = (PROJECT_ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        js = (PROJECT_ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        css = (PROJECT_ROOT / 'static' / 'styles.css').read_text(encoding='utf-8')
        self.assertIn('id="systemStatusModal"', html)
        self.assertIn('function renderSystemStatus()', js)
        self.assertIn('@media (max-width: 900px)', css)
        self.assertIn('100dvh', css)
        self.assertIn('@media (prefers-reduced-motion: reduce)', css)
        self.assertIn('@media (max-width: 480px)', css)
        self.assertIn('const defaultVolumePercent = 100;', js)
        self.assertIn('if (rawValue === null || rawValue.trim() === "")', js)
        self.assertNotIn('button.innerHTML = `<span>${area.display_name}', js)
        self.assertIn('"Интеграция Tuya в HA"', js)
        self.assertIn('"apigw.tuyaeu.com"', js)
        self.assertIn('"apigw.iotbing.com"', js)
        self.assertIn('"Нет канала событий"', js)
        self.assertIn('/static/assets/yadro-logo.png', html)
        self.assertIn('class="brand-logo"', html)
        self.assertIn('@font-face', css)
        self.assertIn('JetBrainsMono-Variable.ttf', css)


if __name__ == '__main__':
    unittest.main()
