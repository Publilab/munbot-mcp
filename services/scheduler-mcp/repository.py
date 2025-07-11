from datetime import time, date
from psycopg2.extras import RealDictCursor
from .app import get_db
import os
import sys

# Ensure mcp-core is on path for audit utilities
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'mcp-core'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
try:
    from utils.audit import audit_step
except ImportError:
    from importlib import import_module
    audit_step = import_module('mcp_utils.audit').audit_step


@audit_step("build_sql_pattern")
def build_sql_pattern(hora_user: time, trace_id=None) -> str:
    hora_fmt = hora_user.strftime("%H:%M")
    return f"{hora_fmt}-%"


@audit_step("get_available_blocks")
def get_available_blocks(fecha: date, pattern: str, trace_id=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = (
        "SELECT * FROM appointments "
        "WHERE fecha = %s AND hora_rango LIKE %s "
        "AND disponible = TRUE AND confirmada = FALSE "
        "ORDER BY hora_rango"
    )
    cur.execute(query, (fecha.isoformat(), pattern))
    rows = cur.fetchall()
    conn.close()
    return rows
