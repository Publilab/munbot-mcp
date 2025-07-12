from __future__ import annotations

from datetime import date, time
import os
import sys
from typing import List

from psycopg2.extras import RealDictCursor
from importlib import import_module

# ────────────────────────────────
# 1) Dependencias internas
# ────────────────────────────────

# Garantizar acceso a utils.audit cuando se ejecuta dentro del contenedor
BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mcp-core")
)
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from utils.audit import audit_step  # modo normal
except ImportError:
    try:
        from mcp_utils.audit import audit_step  # tests
    except Exception:  # pragma: no cover - fallback seguro
        def audit_step(_label):
            def _noop(fn):
                return fn
            return _noop


# ────────────────────────────────
# 2) Funciones
# ────────────────────────────────


@audit_step("build_sql_pattern")
def build_sql_pattern(hora: time, trace_id: str | None = None) -> time:
    """Normaliza la hora para la consulta SQL."""
    return hora.replace(second=0, microsecond=0)


@audit_step("get_available_blocks")
def get_available_blocks(
    fecha: date,
    hora_pattern: time,
    trace_id: str | None = None,
) -> List[dict]:
    """
    Devuelve los bloques disponibles que *contienen* la hora solicitada
    (`hora_pattern`) en la fecha indicada. Se asume que la hora ya fue
    normalizada mediante `build_sql_pattern`.

    • Usa comparación con columnas TIME (`hora_inicio`, `hora_fin`).
    • Ordena por `hora_inicio` ascendente.
    """
    if "scheduler_app" in sys.modules:
        mod = sys.modules["scheduler_app"]
    else:
        mod = import_module("scheduler_mcp.app")
    conn = mod.get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            sql = """
                SELECT *
                FROM   appointments
                WHERE  fecha = %s
                  AND  disponible = TRUE
                  AND  confirmada = FALSE
                  AND  %s::time >= hora_inicio
                  AND  %s::time <  hora_fin
                ORDER BY hora_inicio
            """
            # HH:MM:SS satisface TIME en PostgreSQL
            hora_str = hora_pattern.strftime("%H:%M:%S")

            cur.execute(sql, (fecha, hora_str, hora_str))
            if hasattr(cur, "fetchall"):
                return cur.fetchall()
            elif hasattr(cur, "fetchone"):
                row = cur.fetchone()
                return [row] if row else []
            return []
        finally:
            if hasattr(cur, "close"):
                cur.close()

    finally:
        if hasattr(conn, "close"):
            conn.close()
