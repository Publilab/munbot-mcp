from __future__ import annotations

from datetime import date, time
import os
import sys
from typing import List
import logging
import json

from psycopg2.extras import RealDictCursor
from db import get_db
from utils.audit import audit_step


# ────────────────────────────────
# 2) Funciones
# ────────────────────────────────

audit_logger = logging.getLogger("audit")
if not audit_logger.handlers:
    audit_logger.addHandler(logging.StreamHandler())


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
    conn = get_db()
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

            audit_logger.debug(
                json.dumps(
                    {
                        "step": "execute_sql",
                        "trace_id": trace_id,
                        "sql": sql,
                        "params": [str(fecha), hora_str, hora_str],
                    }
                )
            )
            cur.execute(sql, (fecha, hora_str, hora_str))
            if hasattr(cur, "fetchall"):
                rows = cur.fetchall()
                audit_logger.debug(
                    json.dumps(
                        {
                            "step": "rows_fetched",
                            "trace_id": trace_id,
                            "rows": rows,
                        },
                        default=str,
                    )
                )
                return rows
            elif hasattr(cur, "fetchone"):
                row = cur.fetchone()
                audit_logger.debug(
                    json.dumps(
                        {
                            "step": "rows_fetched",
                            "trace_id": trace_id,
                            "rows": [row] if row else [],
                        },
                        default=str,
                    )
                )
                return [row] if row else []
            return []
        finally:
            if hasattr(cur, "close"):
                cur.close()

    finally:
        if hasattr(conn, "close"):
            conn.close()
