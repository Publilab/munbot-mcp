CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE complaints (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre       TEXT NOT NULL,
    mail         TEXT NOT NULL,
    mensaje      TEXT NOT NULL,
    categoria    SMALLINT NOT NULL,          -- 1 reclamo | 2 denuncia
    departamento SMALLINT NOT NULL,          -- 1 Alcaldía, 2 Social, 3 Vivienda, 4 Tesorería, 5 Obras, 6 Medio Ambiente, 7 Finanzas, 8 Otros
    prioridad    SMALLINT NOT NULL DEFAULT 3,-- 1 alta, 3 normal, 5 baja
    estado       TEXT NOT NULL DEFAULT 'pendiente',
    ip           INET,
    creado_en    TIMESTAMPTZ DEFAULT now(),
    asignado_a   TEXT
);

CREATE INDEX ON complaints (departamento);
CREATE INDEX ON complaints (estado);
