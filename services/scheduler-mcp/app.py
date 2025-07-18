import os
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional
import logging

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator
from notifications import send_email, send_whatsapp
from utils.rut_utils import validar_y_formatear_rut
from repository import get_available_blocks, build_sql_pattern
from service import select_exact_block

# =====================
# Configuración de entorno y DB
# =====================
DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = int(os.getenv("POSTGRES_PORT"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")

from db import get_conn


def get_available_block(fecha: date, hora: dtime, trace_id: str | None = None):
    """Devuelve el bloque disponible que coincide con la fecha y hora."""
    pattern = build_sql_pattern(hora, trace_id=trace_id)
    bloques = get_available_blocks(fecha, pattern, trace_id=trace_id)
    return select_exact_block(bloques, hora, trace_id=trace_id)

app = FastAPI()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit")
if not audit_logger.handlers:
    audit_logger.addHandler(logging.StreamHandler())

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
    trace_id = payload.get("trace_id")

    if tool == "scheduler-listar_horas_disponibles":
        fecha = params.get("fecha")
        hora = params.get("hora")
        if not fecha or not hora:
            raise HTTPException(status_code=400, detail="Se requiere 'fecha' y 'hora'")
        hora_time = dtime.fromisoformat(hora[:5])
        pattern = build_sql_pattern(hora_time, trace_id=trace_id)
        rows = get_available_blocks(
            date.fromisoformat(fecha), pattern, trace_id=trace_id
        )
        cod_func = params.get("cod_func")
        if cod_func:
            rows = [r for r in rows if r.get("funcionario_codigo") == cod_func]
        data = []
        for r in rows:
            try:
                data.append(AppointmentOut(**r).as_dict())
            except Exception:
                data.append(r)
        return {"data": data}

    if tool == "scheduler-reservar_hora":
        slot_id = params.get("slot_id")
        usuario_nombre = params.get("usuario_nombre")
        usuario_mail = params.get("usuario_mail") or params.get("usuario_email")
        usuario_whatsapp = params.get("usuario_whatsapp")
        motivo = params.get("motivo", "")
        usuario_rut = params.get("usuario_rut")
        depto_codigo = params.get("departamento_codigo")
        if not (slot_id and usuario_nombre and usuario_mail):
            raise HTTPException(status_code=400, detail="Faltan campos requeridos")
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM appointments WHERE id=%s AND disponible=TRUE AND confirmada=FALSE",
                    (slot_id,),
                )
                slot = cur.fetchone()
                if not slot:
                    raise HTTPException(status_code=404, detail="Slot no disponible o ya reservado")
                cur.execute(
                    "UPDATE appointments SET disponible=FALSE, confirmada=TRUE, usuario_nombre=%s, usuario_email=%s, usuario_whatsapp=%s, motivo=%s, usuario_rut=%s, departamento_codigo=%s WHERE id=%s",
                    (
                        usuario_nombre,
                        usuario_mail,
                        usuario_whatsapp,
                        motivo,
                        usuario_rut,
                        depto_codigo,
                        slot_id,
                    ),
                )
                conn.commit()

                hi = slot["hora_inicio"]
                hf = slot["hora_fin"]
                hi_str = hi.strftime("%H:%M") if isinstance(hi, dtime) else str(hi)[:5]
                hf_str = hf.strftime("%H:%M") if isinstance(hf, dtime) else str(hf)[:5]
                hora_str = f"{hi_str}-{hf_str}"
                send_email(
                    usuario_mail,
                    "Cita confirmada",
                    "email/confirm.html",
                    usuario=usuario_nombre,
                    funcionario=slot["funcionario_nombre"],
                    fecha_legible=str(slot["fecha"]),
                    hora=hora_str,
                )
        return {"id_reserva": slot_id, "estado": "pendiente", "mensaje": "Ya reservé tu cita. Recuerda que debes ser puntual y llegar antes de la hora estipulada. Debes llevar tu documentación actualizada y tus dudas bien estructuradas para que podamos ayudarte. Te esperamos."}

    if tool == "scheduler-confirmar_hora":
        reserva_id = params.get("id_reserva")
        if not reserva_id:
            raise HTTPException(status_code=400, detail="Se requiere id_reserva")
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM appointments WHERE id=%s", (reserva_id,))
                cita = cur.fetchone()
                if not cita:
                    raise HTTPException(status_code=404, detail="Cita no encontrada")
                cur.execute("UPDATE appointments SET confirmada=TRUE WHERE id=%s", (reserva_id,))
                conn.commit()
        hi = cita["hora_inicio"]
        hf = cita["hora_fin"]
        hi_str = hi.strftime("%H:%M") if isinstance(hi, dtime) else str(hi)[:5]
        hf_str = hf.strftime("%H:%M") if isinstance(hf, dtime) else str(hf)[:5]
        hora_str = f"{hi_str}-{hf_str}"
        send_email(
            cita["usuario_email"],
            "Cita confirmada",
            "email/confirm.html",
            usuario=cita["usuario_nombre"],
            funcionario=cita["funcionario_nombre"],
            fecha_legible=str(cita["fecha"]),
            hora=hora_str,
        )
        send_whatsapp(
            cita["usuario_whatsapp"],
            f"Su cita con {cita['funcionario_nombre']} ha sido confirmada para el {cita['fecha']} a las {hora_str}.",
        )
        return {
            "id_reserva": reserva_id,
            "estado": "confirmada",
            "mensaje": "Cita confirmada y notificada."
        }

    if tool == "scheduler-cancelar_hora":
        reserva_id = params.get("id_reserva")
        motivo_cancelacion = params.get("motivo_cancelacion", "")
        if not reserva_id:
            raise HTTPException(status_code=400, detail="Se requiere id_reserva")
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM appointments WHERE id=%s", (reserva_id,))
                cita = cur.fetchone()
                if not cita:
                    raise HTTPException(status_code=404, detail="Cita no encontrada")
                cur.execute(
                    "UPDATE appointments SET disponible=TRUE, confirmada=FALSE, motivo='', usuario_nombre='', usuario_email='', usuario_whatsapp='' WHERE id=%s",
                    (reserva_id,),
                )
                conn.commit()
        hi = cita["hora_inicio"]
        hf = cita["hora_fin"]
        hi_str = hi.strftime("%H:%M") if isinstance(hi, dtime) else str(hi)[:5]
        hf_str = hf.strftime("%H:%M") if isinstance(hf, dtime) else str(hf)[:5]
        hora_str = f"{hi_str}-{hf_str}"
        if cita["usuario_email"]:
            send_email(
                cita["usuario_email"],
                "Cita cancelada",
                "email/reminder.html",
                usuario=cita["usuario_nombre"],
                fecha_legible=str(cita["fecha"]),
                hora=hora_str,
            )
        if cita["usuario_whatsapp"]:
            send_whatsapp(
                cita["usuario_whatsapp"],
                f"Su cita ha sido cancelada. Motivo: {motivo_cancelacion}",
            )
        return {
            "id_reserva": reserva_id,
            "estado": "cancelada",
            "mensaje": "Cita cancelada."
        }

    return JSONResponse(status_code=400, content={"detail": "Tool desconocida"})

# =====================
# Esquemas de Pydantic (para validación y autocompletado)
# =====================
class AppointmentCreate(BaseModel):
    func: str = Field(default="", alias="funcionario_nombre")
    cod_func: str = Field(default="", alias="funcionario_codigo")
    motiv: str = Field(default="", alias="motivo")
    usu_name: str = Field(default="", alias="usuario_nombre")
    usu_mail: EmailStr = Field(default="user@example.com", alias="usuario_email")
    usu_whatsapp: str = Field(default="+10000000000", alias="usuario_whatsapp")
    rut: Optional[str] = Field(default=None, alias="usuario_rut")
    fecha: date
    hora_inicio: dtime
    hora_fin: dtime
    hora: Optional[str] = Field(default=None, alias="hora")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    def _parse_hora(cls, data):
        v = data.get("hora")
        if v and not data.get("hora_inicio"):
            hi = dtime.fromisoformat(v[:5])
            data["hora_inicio"] = hi
            data["hora_fin"] = (datetime.combine(date.today(), hi) + timedelta(minutes=30)).time()
        return data

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
        if v and not re.match(r"^\+[1-9]\d{1,14}$", v):
            raise ValueError("WhatsApp invalido")
        return v

class AppointmentOut(AppointmentCreate):
    id: str
    disponible: bool
    confirmada: bool

    # Serialización especial para compatibilidad con el front-end
    def as_dict(self):
        data = super().model_dump(by_alias=False)
        # reconstruye el string horario
        data['hora'] = f"{self.hora_inicio.strftime('%H:%M')}-{self.hora_fin.strftime('%H:%M')}"
        # Compatibilidad: exponer slot_id además de id
        data['slot_id'] = self.id
        return data

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
def list_available(request: Request):
    """Listar slots disponibles para fecha y hora exacta."""
    fecha = request.query_params.get("fecha")
    hora = request.query_params.get("hora")
    desde = request.query_params.get("from")
    hasta = request.query_params.get("to")
    if fecha and hora:
        hora_time = dtime.fromisoformat(hora[:5])
        pattern = build_sql_pattern(hora_time)
        rows = get_available_blocks(date.fromisoformat(fecha), pattern)
    elif desde and hasta:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM appointments WHERE fecha BETWEEN %s AND %s",
                    (desde, hasta),
                )
                rows = cur.fetchall()
    else:
        return {"disponibles": []}
    out = []
    for r in rows:
        try:
            out.append(AppointmentOut(**r).as_dict())
        except Exception:
            out.append(r)
    return {"disponibles": out}

@app.post("/appointments/reserve")
def reserve_appointment(appt: AppointmentCreate):
    """Reservar una cita"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Busca slot disponible
            cur.execute(
                "SELECT * FROM appointments "
                "WHERE funcionario_codigo=%s AND fecha=%s AND hora_inicio=%s AND hora_fin=%s "
                "AND disponible = TRUE AND confirmada = FALSE",
                (
                    appt.cod_func,
                    appt.fecha,
                    appt.hora_inicio.strftime('%H:%M'),
                    appt.hora_fin.strftime('%H:%M'),
                ),
            )
            slot = cur.fetchone()
            if not slot:
                raise HTTPException(status_code=404, detail="Slot no disponible o ya reservado")
            # Reserva (marca como no disponible, confirma usuario)
            cur.execute(
                "UPDATE appointments "
                "SET disponible = FALSE, confirmada = TRUE, "
                "    usuario_nombre = %s, usuario_email = %s, usuario_whatsapp = %s, motivo = %s, usuario_rut = %s "
                "WHERE id = %s",
                (
                    appt.usu_name,
                    appt.usu_mail,
                    appt.usu_whatsapp,
                    appt.motiv,
                    appt.rut,
                    slot["id"],
                ),
            )
            conn.commit()
            hi = slot["hora_inicio"]
            hf = slot["hora_fin"]
            hi_str = hi.strftime("%H:%M") if isinstance(hi, dtime) else str(hi)[:5]
            hf_str = hf.strftime("%H:%M") if isinstance(hf, dtime) else str(hf)[:5]
            hora_str = f"{hi_str}-{hf_str}"
            send_email(
                appt.usu_mail,
                "Cita confirmada",
                "email/confirm.html",
                usuario=appt.usu_name,
                funcionario=slot["funcionario_nombre"],
                fecha_legible=str(slot["fecha"]),
                hora=hora_str,
            )
    return {
        "id_reserva": slot["id"],
        "estado": "pendiente",
        "mensaje": "Ya reservé tu cita. Recuerda que debes ser puntual y llegar antes de la hora estipulada. Debes llevar tu documentación actualizada y tus dudas bien estructuradas para que podamos ayudarte. Te esperamos."
    }

@app.post("/appointments/confirm")
def confirm_appointment(body: AppointmentConfirm):
    """Confirmar una cita reservada"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM appointments WHERE id=%s", (body.id,))
            cita = cur.fetchone()
            if not cita:
                raise HTTPException(status_code=404, detail="Cita no encontrada")
            # Confirmar
            cur.execute("UPDATE appointments SET confirmada=TRUE WHERE id=%s", (body.id,))
            conn.commit()
    # Notificación al usuario y funcionario
    hi = cita["hora_inicio"]
    hf = cita["hora_fin"]
    hi_str = hi.strftime("%H:%M") if isinstance(hi, dtime) else str(hi)[:5]
    hf_str = hf.strftime("%H:%M") if isinstance(hf, dtime) else str(hf)[:5]
    hora_str = f"{hi_str}-{hf_str}"
    send_email(
        cita["usuario_email"],
        "Cita confirmada",
        "email/confirm.html",
        usuario=cita["usuario_nombre"],
        funcionario=cita["funcionario_nombre"],
        fecha_legible=str(cita["fecha"]),
        hora=hora_str,
    )
    send_whatsapp(cita["usuario_whatsapp"], f"Su cita con {cita['funcionario_nombre']} ha sido confirmada para el {cita['fecha']} a las {hora_str}.")
    return {
        "id_reserva": body.id,
        "estado": "confirmada",
        "mensaje": "Cita confirmada y notificada."
    }

@app.post("/appointments/cancel")
def cancel_appointment(body: AppointmentCancel):
    """Cancelar una cita reservada o confirmada"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM appointments WHERE id=%s", (body.id,))
            cita = cur.fetchone()
            if not cita:
                raise HTTPException(status_code=404, detail="Cita no encontrada")
            # al cancelar, dejamos disponible=TRUE y confirmada=FALSE
            cur.execute(
                "UPDATE appointments "
                "SET disponible = TRUE, confirmada = FALSE, "
                "    motivo = '', usuario_nombre = '', usuario_email = '', usuario_whatsapp = '' "
                "WHERE id = %s",
                (body.id,),
            )
            conn.commit()
    # Notificar usuario
    hi = cita["hora_inicio"]
    hf = cita["hora_fin"]
    hi_str = hi.strftime("%H:%M") if isinstance(hi, dtime) else str(hi)[:5]
    hf_str = hf.strftime("%H:%M") if isinstance(hf, dtime) else str(hf)[:5]
    hora_str = f"{hi_str}-{hf_str}"
    if cita["usuario_email"]:
        send_email(
            cita["usuario_email"],
            "Cita cancelada",
            "email/reminder.html",
            usuario=cita["usuario_nombre"],
            fecha_legible=str(cita["fecha"]),
            hora=hora_str,
        )
    if cita["usuario_whatsapp"]:
        send_whatsapp(cita["usuario_whatsapp"], f"Su cita ha sido cancelada. Motivo: {body.motivo}")
    return {
        "id_reserva": body.id,
        "estado": "cancelada",
        "mensaje": "Cita cancelada."
    }

@app.get("/appointments/{id}")
def get_appointment(id: str):
    """Consultar detalles de una cita"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM appointments WHERE id=%s", (id,))
            cita = cur.fetchone()
        if not cita:
            raise HTTPException(status_code=404, detail="Cita no encontrada")
        return AppointmentOut(**cita).as_dict()

@app.get("/health")
def health():
    """Endpoint de salud para healthcheck"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": "disconnected", "error": str(e)}

# ==== END ====
