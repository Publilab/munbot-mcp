from datetime import datetime
import re
from dateparser.search import search_dates


def parse_nl_datetime(text: str, base_dt: datetime) -> tuple[datetime | None, str | None]:
    settings = {
        "RELATIVE_BASE": base_dt,
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    results = search_dates(text, languages=["es"], settings=settings)
    if not results:
        return None, None

    match_text, dt = results[0]
    if dt.hour == base_dt.hour and dt.minute == base_dt.minute and dt.second == base_dt.second:
        m = re.search(r"a las (\d{1,2})(?::(\d{2}))?", text)
        if m:
            hour = int(m.group(1)) % 24
            minute = int(m.group(2) or 0)
            dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt, match_text
