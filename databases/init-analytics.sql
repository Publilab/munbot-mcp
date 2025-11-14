-- Schema para reportería cívica basada en interacciones del chatbot

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS civic_conversation_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id TEXT NOT NULL,
    channel TEXT,
    intent_action TEXT,
    intent_normalized TEXT,
    intent_category TEXT,
    tramite_id TEXT,
    response_type TEXT,
    resolution TEXT,
    fallback_used BOOLEAN DEFAULT FALSE,
    escalated BOOLEAN DEFAULT FALSE,
    suggested_replies INTEGER DEFAULT 0,
    latency_ms INTEGER,
    user_text TEXT,
    bot_response TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_civic_conversation_day
    ON civic_conversation_events ((date_trunc('day', created_at)));

CREATE INDEX IF NOT EXISTS idx_civic_conversation_intent
    ON civic_conversation_events (intent_action);

CREATE INDEX IF NOT EXISTS idx_civic_conversation_tramite
    ON civic_conversation_events (tramite_id);


CREATE TABLE IF NOT EXISTS civic_service_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id TEXT,
    event_type TEXT NOT NULL,
    reference_id TEXT,
    department TEXT,
    categoria TEXT,
    priority TEXT,
    status TEXT,
    extra JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_civic_service_day
    ON civic_service_events ((date_trunc('day', created_at)));

CREATE INDEX IF NOT EXISTS idx_civic_service_type
    ON civic_service_events (event_type);


CREATE OR REPLACE VIEW civic_daily_kpis AS
SELECT
    date_trunc('day', created_at) AS day_bucket,
    COALESCE(channel, 'desconocido') AS channel,
    COUNT(*) AS total_interactions,
    SUM(CASE WHEN NOT fallback_used THEN 1 ELSE 0 END) AS resolved_interactions,
    SUM(CASE WHEN fallback_used THEN 1 ELSE 0 END) AS fallbacks,
    SUM(CASE WHEN response_type = 'complaint_created' THEN 1 ELSE 0 END) AS complaints_registradas,
    SUM(CASE WHEN response_type = 'appointment_confirmed' THEN 1 ELSE 0 END) AS citas_confirmadas,
    AVG(latency_ms) AS avg_latency_ms
FROM civic_conversation_events
GROUP BY day_bucket, channel;


CREATE OR REPLACE VIEW civic_topic_trends AS
SELECT
    date_trunc('day', created_at) AS day_bucket,
    COALESCE(tramite_id, intent_category, intent_action, 'sin_clasificar') AS topic,
    COUNT(*) AS total_interacciones,
    SUM(CASE WHEN fallback_used THEN 1 ELSE 0 END) AS total_fallbacks
FROM civic_conversation_events
GROUP BY day_bucket, topic;


CREATE OR REPLACE VIEW civic_service_summary AS
SELECT
    date_trunc('day', created_at) AS day_bucket,
    event_type,
    COALESCE(department, 'sin_departamento') AS department,
    COALESCE(status, 'sin_estado') AS status,
    COUNT(*) AS total_eventos
FROM civic_service_events
GROUP BY day_bucket, event_type, department, status;

COMMIT;
