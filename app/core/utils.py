from datetime import datetime

import pytz

from app.core.config import settings


def get_current_timestamp() -> str:
    """
    Get current time in ISO format with configured timezone.

    Returns:
        str: Current time in ISO format (e.g., "2026-01-22T15:00:00+03:00")
    """
    tz = pytz.timezone(settings.time_zone)
    return datetime.now(tz).isoformat()
