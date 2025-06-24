import redis
import json
from typing import Dict, Optional, Any, List
from datetime import datetime

class ConversationalContextManager:
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0):
        """Inicializa el gestor de contexto con valores por defecto."""
        self.redis_client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.session_expiry_seconds = 300  # 5 minutos

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

        # Registrar última actividad
        context["last_activity"] = datetime.now().isoformat()
        
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

    def get_last_activity(self, session_id: str) -> Optional[str]:
        """Devuelve la marca de tiempo de la última actividad."""
        context = self.get_context(session_id)
        return context.get("last_activity")

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

    def set_faq_clarification(self, session_id: str, data: Dict[str, Any]):
        """Guarda datos de una aclaración de FAQ pendiente."""
        context = self.get_context(session_id)
        context["faq_pending"] = data
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_faq_clarification(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene la aclaración de FAQ pendiente si existe."""
        context = self.get_context(session_id)
        return context.get("faq_pending")

    def clear_faq_clarification(self, session_id: str):
        """Elimina cualquier aclaración de FAQ pendiente."""
        context = self.get_context(session_id)
        if "faq_pending" in context:
            del context["faq_pending"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    # ---- Manejo de selección de documentos ----
    def set_document_options(self, session_id: str, options: List[str]):
        """Guarda en contexto una lista de documentos para que el usuario elija."""
        context = self.get_context(session_id)
        context["doc_options"] = options
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_document_options(self, session_id: str) -> Optional[List[str]]:
        """Obtiene la lista de documentos pendientes de selección."""
        context = self.get_context(session_id)
        return context.get("doc_options")

    def clear_document_options(self, session_id: str):
        """Elimina las opciones de documentos almacenadas."""
        context = self.get_context(session_id)
        if "doc_options" in context:
            del context["doc_options"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def set_selected_document(self, session_id: str, name: str):
        """Guarda el documento seleccionado por el usuario."""
        context = self.get_context(session_id)
        context["selected_document"] = name
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_selected_document(self, session_id: str) -> Optional[str]:
        """Obtiene el documento previamente seleccionado."""
        context = self.get_context(session_id)
        return context.get("selected_document")

    def clear_selected_document(self, session_id: str):
        """Elimina cualquier documento seleccionado del contexto."""
        context = self.get_context(session_id)
        if "selected_document" in context:
            del context["selected_document"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    # ---- Aclaración de documentos ----
    def set_doc_clarification(self, session_id: str, name: str, question: str):
        """Almacena un documento sugerido pendiente de confirmación."""
        context = self.get_context(session_id)
        context["doc_clarify"] = {"doc": name, "question": question}
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def get_doc_clarification(self, session_id: str) -> Optional[Dict[str, str]]:
        """Obtiene la aclaración de documento pendiente, si la hay."""
        context = self.get_context(session_id)
        return context.get("doc_clarify")

    def clear_doc_clarification(self, session_id: str):
        """Limpia la aclaración de documento pendiente."""
        context = self.get_context(session_id)
        if "doc_clarify" in context:
            del context["doc_clarify"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    # ---- Manejo de feedback de usuario ----
    def set_feedback_pending(self, session_id: str, pregunta_id: Optional[int]):
        """Marca una pregunta como pendiente de recibir feedback."""
        context = self.get_context(session_id)
        context["feedback_question"] = pregunta_id
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    def get_feedback_pending(self, session_id: str) -> Optional[int]:
        """Obtiene el ID de la pregunta pendiente de feedback."""
        context = self.get_context(session_id)
        return context.get("feedback_question")

    def clear_feedback_pending(self, session_id: str):
        """Limpia el indicador de feedback pendiente."""
        context = self.get_context(session_id)
        if "feedback_question" in context:
            del context["feedback_question"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds
        )

    # ---- Manejo de confirmaciones y flujo activo ----
    def set_pending_confirmation(self, session_id: str, value: bool = True):
        context = self.get_context(session_id)
        context["pending_confirmation"] = value
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def get_pending_confirmation(self, session_id: str) -> bool:
        context = self.get_context(session_id)
        return bool(context.get("pending_confirmation"))

    def clear_pending_confirmation(self, session_id: str):
        context = self.get_context(session_id)
        if "pending_confirmation" in context:
            del context["pending_confirmation"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def set_current_flow(self, session_id: str, flow: Optional[str]):
        context = self.get_context(session_id)
        if flow:
            context["current_flow"] = flow
        elif "current_flow" in context:
            del context["current_flow"]
        self.redis_client.set(
            f"session:{session_id}",
            json.dumps(context),
            ex=self.session_expiry_seconds,
        )

    def get_current_flow(self, session_id: str) -> Optional[str]:
        context = self.get_context(session_id)
        return context.get("current_flow")
