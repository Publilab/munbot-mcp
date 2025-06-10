import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from datetime import date, datetime
from notifications import send_email, send_whatsapp

# =====================
# Configuración de entorno y DB
# =====================
DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = int(os.getenv("POSTGRES_PORT"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")

def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    return conn

app = FastAPI()

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
    fecha: date
    hora: str

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

@app.get("/appointments/available")
def list_available(fecha: date = None):
    """Listar slots disponibles para una fecha"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = "SELECT * FROM appointments WHERE avlb=1 AND usu_conf=0"
    if fecha:
        query += " AND fecha = %s"
        cur.execute(query, (fecha,))
    else:
        cur.execute(query)
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
    send_email(cita["usu_mail"], "Cita confirmada", f"Su cita con {cita['func']} ha sido confirmada para el {cita['fecha']} a las {cita['hora']}.")
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
        send_email(cita["usu_mail"], "Cita cancelada", f"Su cita ha sido cancelada. Motivo: {body.motivo}")
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
