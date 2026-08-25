from datetime import datetime

import pytest
import pytz

from app.core.config import settings
from app.core.utils import get_current_timestamp


@pytest.mark.unit
def test_get_current_timestamp_format():
    """Test that get_current_timestamp returns ISO format."""
    timestamp = get_current_timestamp()
    try:
        if timestamp.endswith("Z"):
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        elif "+" in timestamp or timestamp[-6:].startswith("-"):
            datetime.fromisoformat(timestamp)
        else:
            datetime.fromisoformat(timestamp)
    except ValueError:
        pytest.fail(f"Timestamp {timestamp} is not in valid ISO format")


@pytest.mark.unit
def test_get_current_timestamp_contains_timezone():
    """Test that timestamp contains timezone information."""
    timestamp = get_current_timestamp()
    assert "+" in timestamp or timestamp.endswith("Z") or "-" in timestamp[-6:]


@pytest.mark.unit
def test_get_current_timestamp_uses_configured_timezone(monkeypatch):
    """Test that timestamp uses configured timezone."""
    test_timezone = "UTC"
    monkeypatch.setattr(settings, "time_zone", test_timezone)

    timestamp = get_current_timestamp()
    tz = pytz.timezone(test_timezone)
    expected_prefix = datetime.now(tz).isoformat()[:10]

    assert timestamp.startswith(expected_prefix)


@pytest.mark.unit
def test_get_current_timestamp_is_recent():
    """Test that timestamp is recent (within last minute)."""
    timestamp = get_current_timestamp()
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    now = datetime.now(pytz.timezone(settings.time_zone))

    time_diff = abs((now - parsed).total_seconds())
    assert time_diff < 60
