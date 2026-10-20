from datetime import datetime


def display_date(value: str | None) -> str:
    if not value:
        return ''
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%d.%m.%Y %H:%M:%S')
    except (ValueError, TypeError):
        return str(value)
