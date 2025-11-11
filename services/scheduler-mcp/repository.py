from __future__ import annotations

from datetime import date, time
import os
import sys
from typing import List
import logging
import json

from psycopg2.extras import RealDictCursor
from db import get_conn
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

@audit_step("get_first_available_block_of_month")
def get_first_available_block_of_month(
    month: int,
    year: int,
    offset: int = 0,
    trace_id: str | None = None,
) -> dict | None:
    """
    Devuelve el primer bloque disponible en un mes y año específicos, con un offset.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = """
                SELECT *
                FROM   appointments
                WHERE  EXTRACT(MONTH FROM fecha) = %s
                  AND  EXTRACT(YEAR FROM fecha) = %s
                  AND  disponible = TRUE
                  AND  confirmada = FALSE
                  AND (
                        fecha > CURRENT_DATE
                     OR (fecha = CURRENT_DATE AND hora_inicio >= CURRENT_TIME)
                  )
                ORDER BY fecha, hora_inicio
                LIMIT 1
                OFFSET %s
            """
            audit_logger.debug(
                json.dumps(
                    {
                        "step": "execute_sql",
                        "trace_id": trace_id,
                        "sql": sql,
                        "params": [month, year, offset],
                    }
                )
            )
            cur.execute(sql, (month, year, offset))
            if hasattr(cur, "fetchone"):
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
                return row
            return None

def get_available_blocks(
    fecha: date,
    hora_pattern: time,
    trace_id: str | None = None,
) -> List[dict]:
    """
    Devuelve los bloques disponibles que contienen `hora_pattern` en la fecha indicada.

    - `hora_pattern` debe estar normalizada con `build_sql_pattern` (HH:MM:SS).
    - Solo devuelve filas con `disponible = TRUE` y `confirmada = FALSE`.
    - Ordena por `hora_inicio` ascendente.
    """
    with get_conn() as conn:
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
            return rows or []
