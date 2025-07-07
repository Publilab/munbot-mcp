import os
import re
from datetime import date, datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, field_validator
from notifications import send_email, send_whatsapp
from rut_utils import validar_y_formatear_rut

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
    avlb: int
    usu_conf: int

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
    query = "SELECT * FROM appointments WHERE avlb=1 AND usu_conf=0"
    params = []
    if from_date and to_date:
        query += " AND fecha BETWEEN %s AND %s"
        params.extend([from_date, to_date])
    elif from_date:
        query += " AND fecha >= %s"
        params.append(from_date)
    query += " ORDER BY fecha, hora"
    cur.execute(query, tuple(params))
    citas = cur.fetchall()
    conn.close()
    return {"disponibles": citas}

@app.post("/appointments/reserve")
def reserve_appointment(appt: AppointmentCreate):
    """Reservar una cita"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Busca slot disponible
    cur.execute("""
        SELECT * FROM appointments
        WHERE func=%s AND cod_func=%s AND fecha=%s AND hora=%s AND avlb=1 AND usu_conf=0
        """, (appt.func, appt.cod_func, appt.fecha, appt.hora))
    slot = cur.fetchone()
    if not slot:
        conn.close()
        raise HTTPException(status_code=404, detail="Slot no disponible o ya reservado")
    # Reserva (marca como no disponible, confirma usuario)
    cur.execute("""
        UPDATE appointments
        SET avlb=0, usu_conf=0, motiv=%s, usu_name=%s, usu_mail=%s, usu_whatsapp=%s
        WHERE id=%s
    """, (appt.motiv, appt.usu_name, appt.usu_mail, appt.usu_whatsapp, slot["id"]))
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
    if not cita or cita["avlb"] == 1 or cita["usu_conf"] == 1:
        conn.close()
        raise HTTPException(status_code=404, detail="Cita no reservada o ya confirmada")
    # Confirmar
    cur.execute("UPDATE appointments SET usu_conf=1 WHERE id=%s", (body.id,))
    conn.commit()
    conn.close()
    # Notificación al usuario y funcionario
    send_email(
        cita["usu_mail"],
        "Cita confirmada",
        "email/confirm.html",
        usuario=cita["usu_name"],
        fecha_legible=str(cita["fecha"]),
        hora=cita["hora"],
    )
    send_whatsapp(cita["usu_whatsapp"], f"Su cita con {cita['func']} ha sido confirmada para el {cita['fecha']} a las {cita['hora']}.")
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
    # Cancelar: vuelve a disponible y limpia usuario
    cur.execute("""
        UPDATE appointments SET avlb=1, usu_conf=0,
        motiv='', usu_name='', usu_mail='', usu_whatsapp=''
        WHERE id=%s
    """, (body.id,))
    conn.commit()
    conn.close()
    # Notificar usuario
    if cita["usu_mail"]:
        send_email(
            cita["usu_mail"],
            "Cita cancelada",
            "email/reminder.html",
            usuario=cita["usu_name"],
            fecha_legible=str(cita["fecha"]),
            hora=cita["hora"],
        )
    if cita["usu_whatsapp"]:
        send_whatsapp(cita["usu_whatsapp"], f"Su cita ha sido cancelada. Motivo: {body.motivo}")
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
