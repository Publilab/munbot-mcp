CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DROP TABLE IF EXISTS complaints;

CREATE TABLE IF NOT EXISTS complaints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre TEXT NOT NULL,
    rut TEXT NOT NULL,
    mail TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    departamento SMALLINT NOT NULL,
    categoria SMALLINT NOT NULL DEFAULT 1,
    prioridad SMALLINT NOT NULL DEFAULT 1,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    ip INET,
    creado_en TIMESTAMPTZ DEFAULT now(),
    asignado_a TEXT
);

CREATE INDEX IF NOT EXISTS idx_complaints_departamento ON complaints (departamento);
CREATE INDEX IF NOT EXISTS idx_complaints_estado ON complaints (estado);
