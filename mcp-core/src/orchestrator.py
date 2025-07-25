import logging
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
logger = logging.getLogger(__name__)

DOC_SERVICE_HOST = "llm_docs_service"
DOC_USER = "user"
DOC_PASS = "pass"

@app.route('/orchestrate', methods=['POST'])
def orchestrate():
    data = request.json
    intent = data.get('tool')

    logger.info(f"[AUDIT] Intento detectado: '{intent}' para pregunta: '{data.get('input', {}).get('pregunta')}'")
    start_time = time.time()

    try:
        logger.info("[AUDIT] Llamando a llm_docs-mcp con herramienta doc-generar_respuesta_llm...")
        resp = requests.post(
            f"http://{DOC_SERVICE_HOST}:8000/tools/call",
            auth=(DOC_USER, DOC_PASS),
            json=data,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"[AUDIT] Respuesta del microservicio recibida (primeros 100 chars): {result.get('respuesta', '')[:100]}...")

    except Exception as e:
        logger.exception("[AUDIT] Falló la llamada al microservicio de documentos")
        return jsonify({"error": "Error interno al procesar la consulta."}), 500

    finally:
        elapsed = round(time.time() - start_time, 2)
        logger.info(f"[AUDIT] Tiempo total orquestación: {elapsed}s")

    return jsonify(result)
