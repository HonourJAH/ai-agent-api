from datetime import datetime, timezone


def get_current_datetime() -> dict:
    """Return the real current date and time, read directly from the
    system clock. No LLM reasoning, no subprocess, no network call — just
    a clock read, so it's both instant and can never be wrong the way a
    stale web search result or a model's frozen training-data guess can be.
    """
    now = datetime.now(timezone.utc)
    return {
        "iso_datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "day_of_week": now.strftime("%A"),
        "time_utc": now.strftime("%H:%M:%S"),
        "timezone": "UTC",
    }
