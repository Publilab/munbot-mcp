from datetime import datetime
from typing import Tuple, Optional
import re

from .datetime_utils import parse_nl_datetime
from .audit import audit_step


@audit_step("parse_date_time")
def parse_date_time(text: str, base_dt: Optional[datetime] = None, trace_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    base_dt = base_dt or datetime.now()
    dt, _ = parse_nl_datetime(text, base_dt)
    if dt:
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    # fallback simple ISO pattern
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    fecha = m.group(1) if m else None
    mh = re.search(r"(\d{1,2}:\d{2})", text)
    hora = mh.group(1) if mh else None
    return fecha, hora
