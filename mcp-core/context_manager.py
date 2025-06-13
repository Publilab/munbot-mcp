import redis
import json
from typing import Dict, Optional, Any, List
from datetime import datetime

class ConversationalContextManager:
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0):
        """Inicializa el gestor de contexto con valores por defecto."""
        self.redis_client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.session_expiry_seconds = 3600  # 1 hora

    def get_context(self, session_id: str) -> Dict[str, Any]:
        """Obtiene el contexto completo de la sesión."""
        context_str = self.redis_client.get(f"session:{session_id}")
        return json.loads(context_str) if context_str else {}

    def update_context(self, session_id: str, user_input: str, bot_response: str):
        """Actualiza el contexto de la conversación."""
        context = self.get_context(session_id)
        
        # Actualizar historial
        if "history" not in context:
            context["history"] = []
        context["history"].append({
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        })
        context["history"].append({
            "role": "assistant",
            "content": bot_response,
            "timestamp": datetime.now().isoformat()
        })
        
        # Mantener solo los últimos 10 mensajes
        context["history"] = context["history"][-10:]
        
        # Guardar contexto actualizado
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def update_complaint_state(self, session_id: str, state: str):
        """Actualiza el estado del reclamo en la sesión."""
        context = self.get_context(session_id)
        context["complaint_state"] = state
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def clear_complaint_state(self, session_id: str):
        """Limpia el estado del reclamo en la sesión."""
        context = self.get_context(session_id)
        if "complaint_state" in context:
            del context["complaint_state"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_complaint_state(self, session_id: str) -> Optional[str]:
        """Obtiene el estado actual del reclamo."""
        context = self.get_context(session_id)
        return context.get("complaint_state")

    def update_pending_field(self, session_id: str, field: Optional[str]):
        """Actualiza el campo pendiente en la sesión."""
        context = self.get_context(session_id)
        if field:
            context["pending_field"] = field
        elif "pending_field" in context:
            del context["pending_field"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_pending_field(self, session_id: str) -> Optional[str]:
        """Obtiene el campo pendiente actual."""
        context = self.get_context(session_id)
        return context.get("pending_field")

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Obtiene el historial de la conversación."""
        context = self.get_context(session_id)
        return context.get("history", [])

    def get_history_as_string(self, history: List[Dict[str, str]]) -> str:
        """Convierte el historial en una cadena de texto."""
        return "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in history[-5:]  # Últimos 5 mensajes
        ])

    def increment_fallback_count(self, session_id: str):
        """Incrementa el contador de fallbacks."""
        context = self.get_context(session_id)
        context["fallback_count"] = context.get("fallback_count", 0) + 1
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def reset_fallback_count(self, session_id: str):
        """Reinicia el contador de fallbacks."""
        context = self.get_context(session_id)
        context["fallback_count"] = 0
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_fallback_count(self, session_id: str) -> int:
        """Obtiene el contador de fallbacks."""
        context = self.get_context(session_id)
        return context.get("fallback_count", 0)

    def set_last_sentiment(self, session_id: str, sentiment: str):
        """Guarda el último sentimiento detectado."""
        context = self.get_context(session_id)
        context["last_sentiment"] = sentiment
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_last_sentiment(self, session_id: str) -> str:
        """Obtiene el último sentimiento detectado."""
        context = self.get_context(session_id)
        return context.get("last_sentiment", "neutral")

    def clear_pending_field(self, session_id: str):
        """Limpia el campo pendiente en la sesión."""
        context = self.get_context(session_id)
        if "pending_field" in context:
            del context["pending_field"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )
