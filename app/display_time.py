"""Human-readable timestamps; stored instants retain their timezone information."""
from datetime import datetime, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')


def display_datetime(value):
    if not value:
        return None
    instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=LOCAL_TZ)
    return instant.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')
