import importlib.util
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath('mcp-core'))
spec = importlib.util.spec_from_file_location('datetime_utils', os.path.join('mcp-core', 'utils', 'datetime_utils.py'))
datetime_utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(datetime_utils)


def test_manana_a_las_diez():
    base = datetime(2025, 7, 7, 15, 55)
    dt, _ = datetime_utils.parse_nl_datetime('mañana a las 10', base)
    assert dt == datetime(2025, 7, 8, 10, 0, tzinfo=dt.tzinfo)


def test_proximo_jueves():
    base = datetime(2025, 7, 7)
    dt, _ = datetime_utils.parse_nl_datetime('próximo jueves', base)
    assert dt == datetime(2025, 7, 10, 0, 0, tzinfo=dt.tzinfo)


def test_fecha_dia_mes():
    base = datetime(2025, 7, 7)
    dt, _ = datetime_utils.parse_nl_datetime('24/08', base)
    assert dt == datetime(2025, 8, 24, 0, 0, tzinfo=dt.tzinfo)


def test_last_business_day():
    base = datetime(2025, 8, 15)
    last = datetime_utils.compute_last_business_day(base)
    assert last == datetime(2025, 8, 29).date()
