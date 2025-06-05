from flask import Flask, request, jsonify
from models import ComplaintModel, ComplaintOut
from repository import ComplaintRepository
from utils.email import send_email
from utils.classifier import clasificar_departamento
import psycopg2
import os

app = Flask(__name__)

# Conexión a BD
conn = psycopg2.connect(
    dbname=os.getenv("POSTGRES_DB"),
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
    host=os.getenv("POSTGRES_HOST"),
    port=os.getenv("POSTGRES_PORT", 5432)
)
repo = ComplaintRepository(conn)

@app.route('/complaint', methods=['POST'])
def register_complaint():
    data = request.json
    try:
        # Validación con Pydantic
        complaint = ComplaintModel(**data)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    # Clasificación automática si no viene departamento
    if complaint.departamento is None:
        complaint.departamento = clasificar_departamento(complaint.mensaje)

    ip = request.remote_addr
    try:
        complaint_id = repo.add_complaint(complaint, ip)
        # Enviar email (puede ir a un worker/cola en producción)
        send_email(
            to=complaint.mail,
            subject="Reclamo registrado",
            body=f"Su reclamo fue registrado con ID {complaint_id}."
        )
        return jsonify({'respuesta': 'Reclamo registrado', 'id': complaint_id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/complaint/<id>', methods=['GET'])
def get_complaint(id):
    record = repo.get_complaint(id)
    if not record:
        return jsonify({'error': 'Reclamo no encontrado'}), 404
    return jsonify(record), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7000)
