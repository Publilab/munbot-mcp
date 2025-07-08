from datetime import datetime, date, timedelta
import re
from typing import Optional
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


WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "sabado": 5,
    "domingo": 6,
}


def compute_relative_date(base: date, texto: str) -> Optional[date]:
    """Calcula una fecha relativa a partir de un texto y un día base."""
    for name, wd in WEEKDAYS.items():
        if name in texto.lower():
            hoy_wd = base.weekday()
            diff = (wd - hoy_wd + 7) % 7
            if "próximo" in texto.lower() and diff == 0:
                diff = 7
            elif diff == 0:
                pass
            return base + timedelta(days=diff)
    return None


def compute_last_business_day(ref: datetime) -> date:
    """Return the last business day of the month for the given date."""
    # Start at the last calendar day of the month
    next_month = ref.replace(day=28) + timedelta(days=4)
    last_day = next_month - timedelta(days=next_month.day)
    # Move backwards until it's not Saturday/Sunday
    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)
    return last_day
