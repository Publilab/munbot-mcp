from __future__ import annotations

from datetime import date, time
import os
import sys
from typing import List

from psycopg2.extras import RealDictCursor

# ────────────────────────────────
# 1) Dependencias internas
# ────────────────────────────────
from app import get_db                          # conexión a PostgreSQL

# Garantizar acceso a utils.audit cuando se ejecuta dentro del contenedor
BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mcp-core")
)
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from utils.audit import audit_step          # modo normal
except ImportError:
    # Si utils.audit no está disponible simplemente crea un decorador vacío
    def audit_step(_label):
        def _noop(fn):
            return fn
        return _noop


# ────────────────────────────────
# 2) Función principal
# ────────────────────────────────
@audit_step("get_available_blocks")
def get_available_blocks(
    fecha: date,
    hora_user: time,
    trace_id: str | None = None
) -> List[dict]:
    """
    Devuelve los bloques disponibles que *contienen* la hora solicitada
    (`hora_user`) en la fecha indicada.

    • Usa comparación con columnas TIME (`hora_inicio`, `hora_fin`).
    • Ordena por `hora_inicio` ascendente.
    """
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
            hora_str = hora_user.strftime("%H:%M:%S")

            cur.execute(sql, (fecha, hora_str, hora_str))
            return cur.fetchall()

    finally:
        conn.close()
