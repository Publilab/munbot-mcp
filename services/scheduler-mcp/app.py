import os
import re
from datetime import date, datetime
from typing import Optional
import logging

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, field_validator
from notifications import send_email, send_whatsapp
from utils.rut_utils import validar_y_formatear_rut

# =====================
# Configuración de entorno y DB
# =====================
DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = int(os.getenv("POSTGRES_PORT"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")

def get_db():
    if os.getenv("TESTING") == "1":
        class Dummy:
            def cursor(self, *a, **k):
                class C:
                    def execute(self, *a, **k):
                        pass
                    def fetchall(self):
                        return []
                    def fetchone(self):
                        return None
                return C()
            def close(self):
                pass
        return Dummy()
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    return conn

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware para manejar peticiones inválidas
class RequestValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Lista de rutas válidas
        valid_routes = {
            "/appointments/available": ["GET"],
            "/appointments/reserve": ["POST"],
            "/appointments/confirm": ["POST"],
            "/appointments/cancel": ["POST"],
            "/appointments/{id}": ["GET"],
            "/tools/call": ["POST"],
            "/health": ["GET"],
            "/": ["GET"]
        }
        
        # Permitir siempre healthcheck y raíz
        if request.url.path in ["/health", "/"]:
            return await call_next(request)
            
        # Verificar si la ruta y método son válidos
        is_valid = False
        for route, methods in valid_routes.items():
            if request.url.path.startswith(route.replace("{id}", "")) and request.method in methods:
                is_valid = True
                break
                
        if not is_valid:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Invalid request",
                    "message": "Esta ruta o método no está soportado",
                    "valid_routes": list(valid_routes.keys())
                }
            )
            
        return await call_next(request)

app.add_middleware(RequestValidationMiddleware)

# =====================
# Endpoint /tools/call para compatibilidad con el orquestador
# =====================

@app.post("/tools/call")
async def tools_call(payload: dict):
    """Despacha herramientas usadas por el orquestador."""
    tool = payload.get("tool")
    params = payload.get("params", {})

    if tool == "scheduler-listar_horas_disponibles":
        fecha = params.get("fecha")
        if not fecha:
            raise HTTPException(status_code=400, detail="Se requiere 'fecha'")
        hora_rango = params.get("hora_rango", "%")
        cod_func = params.get("cod_func")
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = (
            "SELECT * FROM appointments "
            "WHERE fecha = %s AND hora_rango LIKE %s "
            "AND disponible = TRUE AND confirmada = FALSE"
        )
        qparams = [fecha, hora_rango]
        if cod_func:
            query += " AND funcionario_codigo = %s"
            qparams.append(cod_func)
        cur.execute(query, tuple(qparams))
        rows = cur.fetchall()
        conn.close()
        return {"data": rows}

    return JSONResponse(status_code=400, content={"detail": "Tool desconocida"})

# =====================
# Esquemas de Pydantic (para validación y autocompletado)
# =====================
class AppointmentCreate(BaseModel):
    func: str
    cod_func: str
    motiv: str = ""
    usu_name: str
    usu_mail: EmailStr
    usu_whatsapp: str
    rut: Optional[str] = None
    fecha: date
    hora: str

    @field_validator("rut")
    def _validar_rut(cls, v):
        if v is None:
            return v
        nuevo = validar_y_formatear_rut(v)
        if not nuevo:
            raise ValueError("RUT invalido")
        return nuevo

    @field_validator("usu_whatsapp")
    def _validar_whatsapp(cls, v):
        if not re.match(r"^\+[1-9]\d{1,14}$", v):
            raise ValueError("WhatsApp invalido")
        return v

class AppointmentOut(AppointmentCreate):
    id: str
    # disponible=True indica que el bloque está libre.
    disponible: bool
    confirmada: bool

class AppointmentConfirm(BaseModel):
    id: str

class AppointmentCancel(BaseModel):
    id: str
    motivo: str

# =====================
# Endpoints RESTful alineados a MCP
# =====================

@app.get("/")
def root():
    """Endpoint raíz con información del servicio"""
    return {
        "status": "MunBoT Scheduler MCP running",
        "endpoints": [
            "GET /appointments/available",
            "POST /appointments/reserve",
            "POST /appointments/confirm",
            "POST /appointments/cancel",
            "GET /appointments/{id}",
            "GET /health"
        ],
        "version": "1.0.0"
    }

@app.get("/appointments/available")
def list_available(
    from_date: date = Query(None, alias="from"),
    to_date: date = Query(None, alias="to"),
):
    """Listar slots disponibles opcionalmente en un rango de fechas."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # ahora TRUE = libre, FALSE = no confirmada
    query = "SELECT * FROM appointments WHERE disponible = TRUE AND confirmada = FALSE"
    params = []
    logger.debug(
        f"[AUDIT] listar_horas recibidas: from={from_date}, to={to_date}"
    )
    if from_date and to_date:
        query += " AND fecha BETWEEN %s AND %s"
        params.extend([from_date, to_date])
    elif from_date:
        query += " AND fecha >= %s"
        params.append(from_date)
    # ordenar cronológicamente por fecha y hora_rango
    query += " ORDER BY fecha, hora_rango"
    cur.execute(query, tuple(params))
    citas = cur.fetchall()
    logger.debug(f"[AUDIT] filas devueltas: {citas}")
    conn.close()
    return {"disponibles": citas}

@app.post("/appointments/reserve")
def reserve_appointment(appt: AppointmentCreate):
    """Reservar una cita"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Busca slot disponible
    cur.execute(
        # buscamos bloques libres (disponible=TRUE) y no confirmados
        "SELECT * FROM appointments "
        "WHERE funcionario_codigo=%s AND fecha=%s AND hora_rango=%s "
        "AND disponible = TRUE AND confirmada = FALSE",
        (appt.cod_func, appt.fecha, appt.hora)
    )
    slot = cur.fetchone()
    if not slot:
        conn.close()
        raise HTTPException(status_code=404, detail="Slot no disponible o ya reservado")
    # Reserva (marca como no disponible, confirma usuario)
    cur.execute(
        # al reservar, marcamos disponible=FALSE y confirmada sigue FALSE
        "UPDATE appointments "
        "SET disponible = FALSE, confirmada = FALSE, "
        "    usuario_nombre = %s, usuario_email = %s, usuario_whatsapp = %s, motivo = %s "
        "WHERE id = %s",
        (appt.usu_name, appt.usu_mail, appt.usu_whatsapp, appt.motiv, slot["id"])
    )
    conn.commit()
    conn.close()
    # Notificación opcional aquí
    return {"id": slot["id"], "respuesta": "Cita reservada. Confirme para activar la reserva."}

@app.post("/appointments/confirm")
def confirm_appointment(body: AppointmentConfirm):
    """Confirmar una cita reservada"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM appointments WHERE id=%s", (body.id,))
    cita = cur.fetchone()
    if not cita or cita.get("disponible") is True or cita.get("confirmada") is True:
        conn.close()
        raise HTTPException(status_code=404, detail="Cita no reservada o ya confirmada")
    # Confirmar
    cur.execute("UPDATE appointments SET confirmada=TRUE WHERE id=%s", (body.id,))
    conn.commit()
    conn.close()
    # Notificación al usuario y funcionario
    send_email(
        cita["usuario_email"],
        "Cita confirmada",
        "email/confirm.html",
        usuario=cita["usuario_nombre"],
        fecha_legible=str(cita["fecha"]),
        hora=cita["hora_rango"],
    )
    send_whatsapp(cita["usuario_whatsapp"], f"Su cita con {cita['funcionario_nombre']} ha sido confirmada para el {cita['fecha']} a las {cita['hora_rango']}.")
    return {"id": body.id, "respuesta": "Cita confirmada y notificada."}

@app.post("/appointments/cancel")
def cancel_appointment(body: AppointmentCancel):
    """Cancelar una cita reservada o confirmada"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM appointments WHERE id=%s", (body.id,))
    cita = cur.fetchone()
    if not cita:
        conn.close()
        raise HTTPException(status_code=404, detail="Cita no encontrada")
    # al cancelar, dejamos disponible=TRUE y confirmada=FALSE
    cur.execute(
        "UPDATE appointments "
        "SET disponible = TRUE, confirmada = FALSE, "
        "    motivo = '', usuario_nombre = '', usuario_email = '', usuario_whatsapp = '' "
        "WHERE id = %s",
        (body.id,)
    )
    conn.commit()
    conn.close()
    # Notificar usuario
    if cita["usuario_email"]:
        send_email(
            cita["usuario_email"],
            "Cita cancelada",
            "email/reminder.html",
            usuario=cita["usuario_nombre"],
            fecha_legible=str(cita["fecha"]),
            hora=cita["hora_rango"],
        )
    if cita["usuario_whatsapp"]:
        send_whatsapp(cita["usuario_whatsapp"], f"Su cita ha sido cancelada. Motivo: {body.motivo}")
    return {"id": body.id, "respuesta": "Cita cancelada."}

@app.get("/appointments/{id}")
def get_appointment(id: str):
    """Consultar detalles de una cita"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM appointments WHERE id=%s", (id,))
    cita = cur.fetchone()
    conn.close()
    if not cita:
        raise HTTPException(status_code=404, detail="Cita no encontrada")
    return cita

@app.get("/health")
def health():
    """Endpoint de salud para healthcheck"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": "disconnected", "error": str(e)}

# ==== END ====
