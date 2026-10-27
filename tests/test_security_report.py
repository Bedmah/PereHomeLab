import io
import logging
import tempfile
import unittest
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from app.call_store import CallStore
from app.cities import CityStore
from app.employees import EmployeeStore
from app.ha_client import HomeAssistantError
from app.history import HistoryStore
from app.logging_config import RedactingFormatter
from app import logging_config
from app.redaction import redact_sensitive
from app.telegram_service import TelegramError
from app.telegram_store import TelegramStore


class SecretRedactionTest(unittest.TestCase):
    def test_urls_headers_and_unconfigured_bot_tokens(self):
        token = '123456789:' + 'z' * 32
        message = ('https://alice:p%40ss@ha.test/api?access_token=private-value '
                   'wss://user:password@ha.test/api Authorization: Bearer private-header '
                   'Basic cHJpdmF0ZTpwYXNz https://api.telegram.org/bot' + token + '/getUpdates')
        result = redact_sensitive(message)
        for secret in ('alice', 'p%40ss', 'user:password', 'private-value', 'private-header', 'cHJpdmF0ZTpwYXNz', token):
            self.assertNotIn(secret, result)
        self.assertIn('ha.test/api', result)
        self.assertIn('getUpdates', result)

    def test_environment_secrets_are_read_at_format_time(self):
        formatter = RedactingFormatter('%(message)s')
        with patch.dict('os.environ', {'HA_TOKEN':'sample-private-token','ADMIN_PASSWORD':'small'}, clear=True):
            record = logging.LogRecord('test',logging.ERROR,'test',1,'%s small',('sample-private-token',),None)
            self.assertEqual(formatter.format(record),'[redacted] [redacted]')
            self.assertEqual(record.getMessage(),'sample-private-token small')

    def test_tracebacks_and_percent_encoded_secrets_are_redacted(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter('%(message)s'))
        logger = logging.Logger('security-test'); logger.addHandler(handler)
        with patch.dict('os.environ',{'ADMIN_PASSWORD':'test/private+password'},clear=True):
            try:
                raise ValueError('test/private+password test%2Fprivate%2Bpassword')
            except ValueError:
                logger.exception('Operation failed')
        self.assertIn('Traceback',stream.getvalue())
        self.assertNotIn('test/private+password',stream.getvalue())
        self.assertNotIn('test%2Fprivate%2Bpassword',stream.getvalue())

    def test_integration_errors_do_not_expose_credentials(self):
        for exception in (HomeAssistantError,TelegramError):
            with self.subTest(exception=exception):
                error = exception('Connection to https://user:private@ha.test failed')
                self.assertNotIn('user:private',str(error))
                self.assertIn('ha.test',str(error))

    def test_all_configured_handlers_redact_without_root_propagation(self):
        loggers = {name:logging.Logger(name) for name in ('pere_home_lab','uvicorn.access','uvicorn.error')}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(logging_config,'PROJECT_ROOT',Path(temporary)), \
                 patch.object(logging_config.logging,'getLogger',side_effect=loggers.__getitem__), \
                 patch('sys.stderr',io.StringIO()), \
                 patch.dict('os.environ',{'HA_TOKEN':'handler-private-token'},clear=True):
                logging_config.configure_logging()
                try:
                    for logger in loggers.values():
                        self.assertFalse(logger.propagate)
                        logger.error('Failure: handler-private-token')
                    for name in ('app.log','access.log'):
                        text = (Path(temporary)/'logs'/name).read_text()
                        self.assertIn('[redacted]',text)
                        self.assertNotIn('handler-private-token',text)
                finally:
                    for handler in {handler for logger in loggers.values() for handler in logger.handlers}:
                        handler.close()

    def test_cryptography_contains_reported_fixes(self):
        self.assertGreaterEqual(tuple(int(part) for part in version('cryptography').split('.')[:2]),(50,0))


class SQLBindingTest(unittest.TestCase):
    def test_untrusted_filters_and_ids_remain_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'history.db'
            history = HistoryStore(path)
            employees = EmployeeStore(path)
            calls = CallStore(history,employees)
            cities = CityStore(path)
            store = TelegramStore(path,None)
            attack = "x'); DROP TABLE press_history; --"
            history.add_press('first','button-1','Room',datetime.now().isoformat(),'Now','spb','SPB')
            history.add_press(attack,'button-2','Other',datetime.now().isoformat(),'Now','msk','MSK')
            self.assertEqual(history.page(1,20,area_id=attack)['total'],0)
            self.assertEqual(history.page(1,20,query=attack)['total'],0)
            self.assertEqual([item['id'] for item in history.get_presses([attack])],[attack])
            self.assertEqual(calls.refresh_profile([attack]),[])
            employee = employees.observe({'id':123,'first_name':'Test'},'spb')
            self.assertEqual(employees.page(area_id=attack)['total'],0)
            self.assertEqual(employees.get(employee['id'])['telegram_user_id'],'123')
            self.assertEqual(store.settings(attack),[])
            self.assertIsNone(store.due({attack}))
            cities.upsert(attack,'Hostile name')
            self.assertTrue(any(city.area_id==attack for city in cities.list()))
            self.assertEqual(HistoryStore(path).page(1,20)['total'],2)
            self.assertEqual(EmployeeStore(path).get(employee['id'])['id'],employee['id'])


if __name__ == '__main__':
    unittest.main()
