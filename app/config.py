from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / 'secret.env'


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AppConfig:
    ha_url: str
    ha_token: str
    ha_auto_discover: bool
    ha_entity_ids: list[str]
    ha_entity_domain: str
    ha_device_class: str
    ha_entity_id_suffix: str | None
    ha_trigger_from: str | None
    ha_trigger_to: str
    ha_reconnect_seconds: float
    ha_refresh_entities_on_reconnect: bool
    ha_sync_history_hours: float
    ack_button_text: str
    button_sound: Path
    sound_play_seconds: float
    history_db_path: Path
    history_dedup_seconds: float
    admin_username: str
    admin_password: str
    app_download_dir: Path
    app_download_filename: str


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f'Missing required setting: {name}')
    return value


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == '':
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _csv(name: str) -> list[str]:
    value = os.getenv(name, '')
    return [item.strip() for item in value.split(',') if item.strip()]


def _path(name: str, default: str) -> Path:
    value = os.getenv(name, default)
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _filename(name: str, default: str) -> str:
    value = os.getenv(name, default).strip() or default
    clean = Path(value).name.strip()
    if not clean:
        return default
    return clean


def _live_env_value(name: str) -> str | None:
    if ENV_FILE.exists():
        values = dotenv_values(ENV_FILE, encoding='utf-8-sig')
        value = values.get(name)
    else:
        value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def get_live_ack_button_text() -> str:
    return _live_env_value('ACK_BUTTON_TEXT') or 'ФА'


def get_live_sound_play_seconds() -> float:
    value = _live_env_value('SOUND_PLAY_SECONDS') or '25'
    try:
        return max(0.1, float(value))
    except ValueError:
        return 25.0


def load_config() -> AppConfig:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=True, encoding='utf-8-sig')

    try:
        reconnect_seconds = float(os.getenv('HA_RECONNECT_SECONDS', '5'))
        sound_play_seconds = float(os.getenv('SOUND_PLAY_SECONDS', '25'))
        history_dedup_seconds = float(os.getenv('HISTORY_DEDUP_SECONDS', '3'))
        sync_history_hours = float(os.getenv('HA_SYNC_HISTORY_HOURS', '24'))
    except ValueError as exc:
        raise ConfigError('Numeric settings have invalid values.') from exc

    if reconnect_seconds <= 0:
        raise ConfigError('HA_RECONNECT_SECONDS must be greater than 0.')
    if sound_play_seconds <= 0:
        raise ConfigError('SOUND_PLAY_SECONDS must be greater than 0.')
    if history_dedup_seconds < 0:
        raise ConfigError('HISTORY_DEDUP_SECONDS must be 0 or greater.')
    if sync_history_hours < 0:
        raise ConfigError('HA_SYNC_HISTORY_HOURS must be 0 or greater.')

    return AppConfig(
        ha_url=_required('HA_URL').rstrip('/'),
        ha_token=_required('HA_TOKEN'),
        ha_auto_discover=_bool('HA_AUTO_DISCOVER', True),
        ha_entity_ids=_csv('HA_ENTITY_IDS'),
        ha_entity_domain=os.getenv('HA_ENTITY_DOMAIN', 'binary_sensor').strip(),
        ha_device_class=os.getenv('HA_DEVICE_CLASS', 'safety').strip(),
        ha_entity_id_suffix=_optional('HA_ENTITY_ID_SUFFIX'),
        ha_trigger_from=_optional('HA_TRIGGER_FROM'),
        ha_trigger_to=os.getenv('HA_TRIGGER_TO', 'on').strip(),
        ha_reconnect_seconds=reconnect_seconds,
        ha_refresh_entities_on_reconnect=_bool('HA_REFRESH_ENTITIES_ON_RECONNECT', False),
        ha_sync_history_hours=sync_history_hours,
        ack_button_text=get_live_ack_button_text(),
        button_sound=_path('BUTTON_SOUND', 'sound/1.mp3'),
        sound_play_seconds=sound_play_seconds,
        history_db_path=_path('HISTORY_DB_PATH', 'data/press_history.db'),
        history_dedup_seconds=history_dedup_seconds,
        admin_username=os.getenv('ADMIN_USERNAME', 'admin').strip(),
        admin_password=os.getenv('ADMIN_PASSWORD', 'admin').strip(),
        app_download_dir=_path('APP_DOWNLOAD_DIR', 'static/downloads'),
        app_download_filename=_filename('APP_DOWNLOAD_FILENAME', 'PereHomeLabKiosk.exe'),
    )
