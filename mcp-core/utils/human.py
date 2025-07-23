import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def registrar_evento_humano(session_id: str, pregunta: str, trace_id: str | None = None) -> None:
    """Registra en un archivo de auditoría la derivación a un agente humano."""
    registro = {
        "session_id": session_id,
        "pregunta": pregunta,
        "timestamp": datetime.utcnow().isoformat(),
        "trace_id": trace_id,
    }
    try:
        path = os.getenv("HUMAN_ESCALATION_LOG", "human_escalations.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"No se pudo registrar evento humano: {e}")
    logger.info(json.dumps(registro, ensure_ascii=False))
