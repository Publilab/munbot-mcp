from datetime import time, date
from psycopg2.extras import RealDictCursor
from app import get_db
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



@audit_step("get_available_blocks")
def get_available_blocks(fecha: date, hora_user: time, trace_id=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = (
        "SELECT * FROM appointments "
        "WHERE fecha = %s "
        "AND disponible = TRUE AND confirmada = FALSE "
        "AND %s::time >= hora_inicio AND %s::time < hora_fin "
        "ORDER BY hora_inicio"
    )
    cur.execute(query, (fecha.isoformat(), hora_user.strftime('%H:%M'), hora_user.strftime('%H:%M')))
    rows = cur.fetchall()
    conn.close()
    return rows
