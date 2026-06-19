import asyncio

from app.config import load_config
from app.ha_client import HomeAssistantClient


async def main() -> None:
    config = load_config()
    client = HomeAssistantClient(config)
    states = await client.get_states()
    matched = []

    for state in states:
        entity_id = str(state.get('entity_id') or '')
        attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        if config.ha_entity_ids:
            ok = entity_id in config.ha_entity_ids
        else:
            ok = entity_id.startswith(f'{config.ha_entity_domain}.')
            ok = ok and (not config.ha_entity_id_suffix or entity_id.endswith(config.ha_entity_id_suffix))
            ok = ok and (not config.ha_device_class or attrs.get('device_class') == config.ha_device_class)
        if ok:
            matched.append((entity_id, state.get('state'), attrs.get('friendly_name')))

    print(f'Всего сущностей HA: {len(states)}')
    print(f'Найдено кнопок по фильтру: {len(matched)}')
    for entity_id, state, name in matched[:100]:
        print(f'- {entity_id} | {state} | {name}')


if __name__ == '__main__':
    asyncio.run(main())
