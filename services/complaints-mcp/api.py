# api.py

import os
import psycopg2
from flask import Flask, request, jsonify
from models import ComplaintModel  # asume que el modelo Pydantic no cambió
from repository import ComplaintRepository
from utils.email import send_email
from utils.classifier import clasificar_departamento

app = Flask(__name__)

# ------------------------------------------------------------
#  1) CONEXIÓN A POSTGRES USANDO VARIABLES DE ENTORNO
# ------------------------------------------------------------
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "munbot")
DB_USER = os.getenv("POSTGRES_USER", "munbot_user")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "munbot_pass")

try:
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        host=DB_HOST,
        port=DB_PORT
    )
except psycopg2.OperationalError as e:
    # Si no se conecta, imprimimos el error y salimos
    # Flask levantará igualmente pero no servirá consultas a BD.
    app.logger.error(f"Error conectando a PostgreSQL: {e}")
    conn = None

repo = ComplaintRepository(conn)  # instancia del repositorio


# ------------------------------------------------------------
#  2) ENDPOINT /tools/call
#     - El Orquestador hace POST a /tools/call con JSON:
#         { "tool": "<tool_name>", "params": { ... } }
#     - Aquí manejamos únicamente "complaint-registrar_reclamo".
#     - Devolvemos JSON con "respuesta": "<texto>".
# ------------------------------------------------------------
@app.route("/tools/call", methods=["POST"])
def tools_call():
    payload = request.get_json(force=True)
    tool = payload.get("tool", "")
    params = payload.get("params", {})

    # Solo soportamos la herramienta de registrar reclamo
    if tool != "complaint-registrar_reclamo":
        return jsonify({
            "respuesta": f"Tool '{tool}' no está soportada por complaints-mcp."
        }), 400

    # Extraemos campos esperados desde params
    # Se asume que el orquestador envía params con:
    #   { "nombre": "...", "mail": "...", "mensaje": "...", "departamento": Optional[...] }
    nombre = params.get("nombre")
    mail = params.get("correo") or params.get("mail")  # posible key "mail" o "correo"
    mensaje = params.get("detalle_reclamo") or params.get("mensaje")
    departamento = params.get("departamento")

    # Validamos que vengan al menos los campos obligatorios
    missing = []
    if not nombre:
        missing.append("nombre")
    if not mail:
        missing.append("correo")
    if not mensaje:
        missing.append("detalle_reclamo")

    if missing:
        campo = missing[0]
        preguntas = {
            "nombre": "Por favor, proporciona tu nombre completo.",
            "correo": "Por favor, proporciona tu correo electrónico.",
            "detalle_reclamo": "Por favor, describe tu reclamo con más detalle."
        }
        return jsonify({
            "respuesta": preguntas.get(campo, f"Falta el campo {campo}."),
            "pending_field": campo
        }), 200

    # Si no se indicó departamento, lo clasificamos automáticamente
    if not departamento:
        departamento = clasificar_departamento(mensaje)

    # Obtenemos la IP remota para almacenarla
    ip = request.remote_addr or "unknown"

    # Construimos el objeto ComplaintModel para validar
    try:
        complaint_data = {
            "nombre": nombre,
            "mail": mail,
            "mensaje": mensaje,
            "departamento": departamento
        }
        complaint = ComplaintModel(**complaint_data)
    except Exception as e:
        return jsonify({"respuesta": f"Error en datos del reclamo: {e}"}), 400

    # Guardamos en la base de datos
    try:
        complaint_id = repo.add_complaint(complaint, ip)
    except Exception as e:
        app.logger.error(f"Error guardando reclamo en BD: {e}")
        return jsonify({"respuesta": "Error interno al registrar tu reclamo."}), 500

    # Enviamos correo de confirmación (en producción, esto podría ser asíncrono)
    try:
        send_email(
            to=complaint.mail,
            subject="Reclamo registrado",
            body=f"Su reclamo fue registrado con ID {complaint_id}."
        )
    except Exception as e:
        app.logger.warning(f"No se pudo enviar email: {e}")
        # No abortamos: el reclamo ya está en BD, devolvemos éxito al cliente

    return jsonify({"respuesta": f"Reclamo registrado con ID {complaint_id}."}), 201


# ------------------------------------------------------------
#  3) ENDPOINT ADICIONAL OPCIONAL: OBTENER RECLAMO POR ID
#     (para uso directo, no necesariamente llamado por el orquestador)
# ------------------------------------------------------------
@app.route("/complaint/<id>", methods=["GET"])
def get_complaint(id):
    try:
        record = repo.get_complaint(id)
    except Exception as e:
        app.logger.error(f"Error consultando reclamo {id}: {e}")
        return jsonify({"respuesta": "Error al buscar el reclamo."}), 500

    if not record:
        return jsonify({"respuesta": "Reclamo no encontrado"}), 404

    return jsonify(record), 200


# ------------------------------------------------------------
#  4) ENDPOINT DE HEALTHCHECK
# ------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    # Verificamos si la conexión a BD está OK
    if conn is None:
        return jsonify({"status": "error", "detail": "No hay conexión a BD"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        app.logger.error(f"Healthcheck BD fallido: {e}")
        return jsonify({"status": "error", "detail": "BD no responde"}), 500


# ------------------------------------------------------------
#  5) ARRANQUE DE LA APP
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000)
