import redis
import json
from typing import Dict, Optional, Any, List

class ConversationalContextManager:
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0):
        self.redis_client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.session_expiry_seconds = 3600

    def get_context(self, session_id: str) -> Dict:
        context_json = self.redis_client.get(f"session:{session_id}")
        if context_json:
            return json.loads(context_json)
        return {"history": []}

    def update_context(self, session_id: str, user_query: str, bot_response: str):
        context = self.get_context(session_id)
        if len(context.get("history", [])) > 10:
            context["history"] = context["history"][-10:]
        context.setdefault("history", []).append({"role": "user", "content": user_query})
        context["history"].append({"role": "assistant", "content": bot_response})
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def get_history_as_string(self, history: list) -> str:
        return "\n".join([f"{turn['role']}: {turn['content']}" for turn in history])

    def get_fallback_count(self, session_id: str) -> int:
        context = self.get_context(session_id)
        return context.get("fallback_count", 0)

    def increment_fallback_count(self, session_id: str):
        context = self.get_context(session_id)
        context["fallback_count"] = context.get("fallback_count", 0) + 1
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def reset_fallback_count(self, session_id: str):
        context = self.get_context(session_id)
        context["fallback_count"] = 0
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def set_last_sentiment(self, session_id: str, sentiment: str):
        context = self.get_context(session_id)
        context["last_sentiment"] = sentiment
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def get_last_sentiment(self, session_id: str) -> str:
        context = self.get_context(session_id)
        return context.get("last_sentiment", "neutral")

    def update_pending_field(self, session_id: str, field_name: str):
        """Establece el campo pendiente que se está esperando del usuario."""
        context = self.get_context(session_id)
        context["pending_field"] = field_name
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )
        
    def clear_pending_field(self, session_id: str):
        """Limpia el campo pendiente cuando ya se ha completado."""
        context = self.get_context(session_id)
        if "pending_field" in context:
            del context["pending_field"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def update_complaint_state(self, session_id: str, state: str):
        """Actualiza el estado del reclamo en la sesión."""
        self.redis_client.hset(f"session:{session_id}", "complaint_state", state)
    
    def clear_complaint_state(self, session_id: str):
        """Limpia el estado del reclamo en la sesión."""
        self.redis_client.hdel(f"session:{session_id}", "complaint_state")
    
    def get_complaint_state(self, session_id: str) -> Optional[str]:
        """Obtiene el estado actual del reclamo."""
        return self.redis_client.hget(f"session:{session_id}", "complaint_state")
    
    def update_pending_field(self, session_id: str, field: Optional[str]):
        """Actualiza el campo pendiente en la sesión."""
        if field:
            self.redis_client.hset(f"session:{session_id}", "pending_field", field)
        else:
            self.redis_client.hdel(f"session:{session_id}", "pending_field")
    
    def get_pending_field(self, session_id: str) -> Optional[str]:
        """Obtiene el campo pendiente actual."""
        return self.redis_client.hget(f"session:{session_id}", "pending_field")
    
    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Obtiene el historial de la conversación."""
        history = self.redis_client.get(f"history:{session_id}")
        return json.loads(history) if history else []
