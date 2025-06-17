CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DROP TABLE IF EXISTS complaints;

CREATE TABLE complaints (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre       TEXT NOT NULL,
    rut          TEXT NOT NULL,
    mail         TEXT NOT NULL,
    mensaje      TEXT NOT NULL,
    departamento SMALLINT NOT NULL,          -- 1 Alcaldía, 2 Social, 3 Vivienda, 4 Tesorería, 5 Obras, 6 Medio Ambiente, 7 Finanzas, 8 Otros
    categoria    SMALLINT NOT NULL,          -- 1 reclamo | 2 denuncia
    prioridad    SMALLINT NOT NULL DEFAULT 3,
    estado       TEXT NOT NULL DEFAULT 'pendiente',
    ip           INET,
    creado_en    TIMESTAMPTZ DEFAULT now(),
    asignado_a   TEXT
);

CREATE INDEX IF NOT EXISTS idx_complaints_departamento ON complaints (departamento);
CREATE INDEX IF NOT EXISTS idx_complaints_estado ON complaints (estado);
