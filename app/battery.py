from typing import Any


def _clean_name(name: str) -> str:
    clean_name = name.strip()
    for suffix in ('Батарея', 'Battery', 'Безопасность', 'Safety'):
        if clean_name.lower().endswith(suffix.lower()):
            clean_name = clean_name[: -len(suffix)].strip()
    return clean_name or name.strip()


def _battery_percent(raw_value: Any) -> int | None:
    try:
        value = float(str(raw_value).replace(',', '.'))
    except (TypeError, ValueError):
        return None

    if value < 0 or value > 100:
        return None
    return int(round(value))


def _button_key(entity_id: str) -> str | None:
    if not entity_id.startswith('binary_sensor.') or not entity_id.endswith('_safety'):
        return None
    return entity_id.removeprefix('binary_sensor.').removesuffix('_safety')


def _battery_key(entity_id: str) -> str | None:
    if not entity_id.startswith('sensor.'):
        return None
    raw = entity_id.removeprefix('sensor.')
    if raw.endswith('_battery'):
        return raw.removesuffix('_battery')
    if raw.endswith('_battery_percentage'):
        return raw.removesuffix('_battery_percentage')
    return None


def collect_batteries(states: list[dict[str, Any]], entity_areas: dict[str, dict[str, str | None]] | None = None, area_id: str | None = None) -> list[dict[str, Any]]:
    button_names: dict[str, str] = {}

    for state in states:
        entity_id = str(state.get('entity_id') or '')
        key = _button_key(entity_id)
        if not key:
            continue
        attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        friendly_name = str(attributes.get('friendly_name') or entity_id)
        button_names[key] = _clean_name(friendly_name)

    entity_areas = entity_areas or {}
    batteries: list[dict[str, Any]] = []

    for state in states:
        entity_id = str(state.get('entity_id') or '')
        key = _battery_key(entity_id)
        if not key or key not in button_names:
            continue

        percent = _battery_percent(state.get('state'))
        if percent is None:
            continue

        attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        friendly_name = str(attributes.get('friendly_name') or button_names[key])

        button_entity_id = f'binary_sensor.{key}_safety'
        area = entity_areas.get(button_entity_id) or {}
        item_area_id = area.get('area_id')
        if area_id and item_area_id != area_id:
            continue

        batteries.append(
            {
                'entity_id': entity_id,
                'button_entity_id': button_entity_id,
                'name': _clean_name(friendly_name) or button_names[key],
                'area_id': item_area_id,
                'area_name': area.get('area_name'),
                'percent': percent,
                'state': str(state.get('state') or ''),
                'unit': str(attributes.get('unit_of_measurement') or '%'),
                'changed_at': str(state.get('last_changed') or ''),
                'updated_at': str(state.get('last_updated') or ''),
            }
        )

    batteries.sort(key=lambda item: (item['percent'], item['name'].lower()))
    return batteries
