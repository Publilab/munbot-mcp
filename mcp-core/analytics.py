import json
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional

try:
    import psycopg2  # type: ignore
    from psycopg2.extras import Json  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psycopg2 = None  # type: ignore
    Json = None  # type: ignore

LOGGER = logging.getLogger("civic_analytics")


def _bool_env(name: str, default: bool = True) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y"}


class CivicAnalyticsRecorder:
    """
    Envía eventos de conversación y de servicios municipales a Postgres para reportería.
    La escritura se hace en un hilo dedicado para no bloquear el flujo del bot.
    """

    def __init__(self) -> None:
        self.enabled = _bool_env("CIVIC_ANALYTICS_ENABLED", True) and psycopg2 is not None
        self._dsn = os.getenv("CIVIC_ANALYTICS_DSN") or self._build_default_dsn()
        if not self._dsn:
            self.enabled = False
        self._queue: "queue.Queue[tuple[str, Dict[str, Any]]]" = queue.Queue(maxsize=2000)
        self._worker_thread: Optional[threading.Thread] = None
        self._drop_warned = False
        if self.enabled:
            self._start_worker()
        else:
            LOGGER.info("civic analytics disabled (psycopg2 missing or DSN not configured)")

    def _build_default_dsn(self) -> Optional[str]:
        host = os.getenv("POSTGRES_HOST")
        db = os.getenv("POSTGRES_DB")
        user = os.getenv("POSTGRES_USER")
        password = os.getenv("POSTGRES_PASSWORD")
        port = os.getenv("POSTGRES_PORT")
        if not all([host, db, user, password]):
            return None
        parts = [
            f"host={host}",
            f"dbname={db}",
            f"user={user}",
            f"password={password}",
        ]
        if port:
            parts.append(f"port={port}")
        return " ".join(parts)

    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(target=self._worker_loop, name="civic-analytics", daemon=True)
        self._worker_thread.start()

    def record_conversation_event(
        self,
        *,
        session_id: str,
        user_text: str,
        bot_response: str,
        channel: Optional[str],
        intent_action: Optional[str],
        intent_normalized: Optional[str],
        intent_category: Optional[str],
        tramite_id: Optional[str],
        response_type: Optional[str],
        resolution: Optional[str],
        fallback_used: bool,
        escalated: bool,
        latency_ms: Optional[int],
        suggested_replies: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "session_id": session_id,
            "channel": channel,
            "intent_action": intent_action,
            "intent_normalized": intent_normalized,
            "intent_category": intent_category,
            "tramite_id": tramite_id,
            "response_type": response_type,
            "resolution": resolution,
            "fallback_used": fallback_used,
            "escalated": escalated,
            "suggested_replies": suggested_replies,
            "latency_ms": latency_ms,
            "user_text": user_text,
            "bot_response": bot_response,
            "metadata": metadata or {},
        }
        self._enqueue(("conversation", payload))

    def record_service_event(
        self,
        *,
        session_id: Optional[str],
        event_type: str,
        reference_id: Optional[str] = None,
        department: Optional[str] = None,
        categoria: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "session_id": session_id,
            "event_type": event_type,
            "reference_id": reference_id,
            "department": department,
            "categoria": categoria,
            "priority": priority,
            "status": status,
            "extra": extra or {},
        }
        self._enqueue(("service", payload))

    # -- infraestructura interna --
    def _enqueue(self, item: tuple[str, Dict[str, Any]]) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            if not self._drop_warned:
                LOGGER.warning("civic analytics queue is full, dropping events")
                self._drop_warned = True

    def _worker_loop(self) -> None:  # pragma: no cover - hilo de fondo
        conn = None
        while True:
            try:
                kind, payload = self._queue.get()
                conn = self._ensure_connection(conn)
                if conn is None:
                    continue
                if kind == "conversation":
                    self._insert_conversation(conn, payload)
                elif kind == "service":
                    self._insert_service(conn, payload)
            except Exception as exc:
                LOGGER.error("civic analytics worker error: %s", exc, exc_info=True)
                time.sleep(2.0)

    def _ensure_connection(self, conn):
        if conn is not None and getattr(conn, "closed", 1) == 0:
            return conn
        try:
            conn = psycopg2.connect(self._dsn)  # type: ignore[call-arg]
            conn.autocommit = True
            return conn
        except Exception as exc:
            LOGGER.error("cannot connect to analytics database: %s", exc)
            time.sleep(5.0)
            return None

    def _insert_conversation(self, conn, payload: Dict[str, Any]) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO civic_conversation_events (
                    session_id, channel, intent_action, intent_normalized, intent_category,
                    tramite_id, response_type, resolution, fallback_used, escalated,
                    suggested_replies, latency_ms, user_text, bot_response, metadata
                )
                VALUES (%(session_id)s, %(channel)s, %(intent_action)s, %(intent_normalized)s,
                        %(intent_category)s, %(tramite_id)s, %(response_type)s, %(resolution)s,
                        %(fallback_used)s, %(escalated)s, %(suggested_replies)s, %(latency_ms)s,
                        %(user_text)s, %(bot_response)s, %(metadata)s)
                """,
                {
                    **payload,
                    "metadata": Json(payload.get("metadata", {})) if Json else json.dumps(payload.get("metadata", {})),
                },
            )

    def _insert_service(self, conn, payload: Dict[str, Any]) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO civic_service_events (
                    session_id, event_type, reference_id,
                    department, categoria, priority, status, extra
                )
                VALUES (%(session_id)s, %(event_type)s, %(reference_id)s,
                        %(department)s, %(categoria)s, %(priority)s, %(status)s, %(extra)s)
                """,
                {
                    **payload,
                    "extra": Json(payload.get("extra", {})) if Json else json.dumps(payload.get("extra", {})),
                },
            )


civic_analytics = CivicAnalyticsRecorder()

__all__ = ["civic_analytics", "CivicAnalyticsRecorder"]
