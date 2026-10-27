from fastapi import HTTPException


def require_global(admin: dict) -> None:
    if admin.get('role') not in {'owner', 'admin'}:
        raise HTTPException(403, 'Доступно только общему или главному администратору')


def require_area(admin: dict, area_id: str) -> None:
    if admin.get('role') in {'owner', 'admin'}:
        admin['audit_area'] = area_id
        return
    if admin.get('role') != 'location' or area_id not in (admin.get('allowed_areas') or []):
        raise HTTPException(403, 'Нет доступа к этой локации')
    admin['audit_area'] = area_id


def visible_areas(admin: dict, items: list[dict]) -> list[dict]:
    if admin.get('role') in {'owner', 'admin'}:
        return items
    return [item for item in items if item['area_id'] in (admin.get('allowed_areas') or [])]


def require_user_control(admin: dict, target: dict) -> None:
    require_global(admin)
    if target['role'] == 'owner' or (admin['role'] != 'owner' and target['role'] != 'location'):
        raise HTTPException(403, 'Нет права изменять эту учётную запись')
