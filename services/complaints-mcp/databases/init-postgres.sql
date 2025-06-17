-- Script de inicialización para PostgreSQL
-- Base de datos: munbot

-- Crear extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Tabla de reclamos (complaints)
CREATE TABLE IF NOT EXISTS complaints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre VARCHAR(255) NOT NULL,
    rut VARCHAR(20),
    mail VARCHAR(255) NOT NULL,
    telefono VARCHAR(20),
    mensaje TEXT NOT NULL,
    departamento VARCHAR(100),
    ip_address INET,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para optimizar consultas
CREATE INDEX IF NOT EXISTS idx_complaints_created_at ON complaints(created_at);
CREATE INDEX IF NOT EXISTS idx_complaints_departamento ON complaints(departamento);
CREATE INDEX IF NOT EXISTS idx_complaints_mail ON complaints(mail);

-- Tabla de citas (appointments)
CREATE TABLE IF NOT EXISTS appointments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    func VARCHAR(255) NOT NULL,
    cod_func VARCHAR(50) NOT NULL,
    motiv TEXT DEFAULT '',
    usu_name VARCHAR(255) NOT NULL,
    usu_mail VARCHAR(255) NOT NULL,
    usu_whatsapp VARCHAR(20) NOT NULL,
    fecha DATE NOT NULL,
    hora VARCHAR(10) NOT NULL,
    avlb INTEGER DEFAULT 1,
    usu_conf INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para optimizar consultas de citas
CREATE INDEX IF NOT EXISTS idx_appointments_fecha ON appointments(fecha);
CREATE INDEX IF NOT EXISTS idx_appointments_func ON appointments(func);
CREATE INDEX IF NOT EXISTS idx_appointments_avlb ON appointments(avlb);
CREATE INDEX IF NOT EXISTS idx_appointments_usu_conf ON appointments(usu_conf);

-- Función para actualizar updated_at automáticamente
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers para actualizar updated_at
CREATE TRIGGER update_complaints_updated_at
    BEFORE UPDATE ON complaints
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_appointments_updated_at
    BEFORE UPDATE ON appointments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Insertar datos de ejemplo para citas disponibles
INSERT INTO appointments (func, cod_func, fecha, hora, avlb, usu_conf, usu_name, usu_mail, usu_whatsapp)
VALUES 
    ('Atención Ciudadana', 'AC001', CURRENT_DATE + INTERVAL '1 day', '09:00', 1, 0, '', '', ''),
    ('Atención Ciudadana', 'AC001', CURRENT_DATE + INTERVAL '1 day', '10:00', 1, 0, '', '', ''),
    ('Atención Ciudadana', 'AC001', CURRENT_DATE + INTERVAL '1 day', '11:00', 1, 0, '', '', ''),
    ('Atención Ciudadana', 'AC001', CURRENT_DATE + INTERVAL '2 days', '09:00', 1, 0, '', '', ''),
    ('Atención Ciudadana', 'AC001', CURRENT_DATE + INTERVAL '2 days', '10:00', 1, 0, '', '', ''),
    ('Obras Públicas', 'OP001', CURRENT_DATE + INTERVAL '1 day', '14:00', 1, 0, '', '', ''),
    ('Obras Públicas', 'OP001', CURRENT_DATE + INTERVAL '1 day', '15:00', 1, 0, '', '', ''),
    ('Obras Públicas', 'OP001', CURRENT_DATE + INTERVAL '2 days', '14:00', 1, 0, '', '', '')
ON CONFLICT DO NOTHING;

-- Tabla de documentos oficiales
CREATE TABLE IF NOT EXISTS documentos (
    id SERIAL PRIMARY KEY,
    id_documento VARCHAR(64) UNIQUE NOT NULL,
    nombre VARCHAR(255) NOT NULL,
    clase VARCHAR(64), -- certificado, licencia, permiso, etc
    aplica_a VARCHAR(64), -- ciudadano, empresa, etc
    descripcion TEXT
);

-- Tabla de requisitos de documentos
CREATE TABLE IF NOT EXISTS documento_requisitos (
    id SERIAL PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    requisito TEXT NOT NULL
);

-- Tabla de oficinas asociadas a documentos
CREATE TABLE IF NOT EXISTS documento_oficinas (
    id SERIAL PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    nombre VARCHAR(255) NOT NULL,
    direccion VARCHAR(255),
    horario VARCHAR(128),
    correo VARCHAR(128),
    holocom VARCHAR(32)
);

-- Tabla de duración/validez de documentos
CREATE TABLE IF NOT EXISTS documento_duracion (
    id SERIAL PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    duracion VARCHAR(128)
);

-- Tabla de sanciones asociadas a documentos
CREATE TABLE IF NOT EXISTS documento_sanciones (
    id SERIAL PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    sancion TEXT
);

-- Tabla de notas/contactos adicionales
CREATE TABLE IF NOT EXISTS documento_notas (
    id SERIAL PRIMARY KEY,
    documento_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    nota TEXT
);

-- Índices para optimizar búsquedas
CREATE INDEX IF NOT EXISTS idx_documentos_nombre ON documentos(nombre);
CREATE INDEX IF NOT EXISTS idx_documento_oficinas_docid ON documento_oficinas(documento_id);
CREATE INDEX IF NOT EXISTS idx_documento_requisitos_docid ON documento_requisitos(documento_id);
CREATE INDEX IF NOT EXISTS idx_documento_duracion_docid ON documento_duracion(documento_id);
CREATE INDEX IF NOT EXISTS idx_documento_sanciones_docid ON documento_sanciones(documento_id);
CREATE INDEX IF NOT EXISTS idx_documento_notas_docid ON documento_notas(documento_id);

-- Ejemplo: Documentos oficiales
INSERT INTO documentos (id_documento, nombre, clase, aplica_a, descripcion) VALUES
  ('DOC001', 'Patente de Alcoholes', 'patente', 'empresa', 'Permite la venta de bebidas alcohólicas en locales autorizados.'),
  ('DOC002', 'Certificado de Residencia Definitiva', 'certificado', 'ciudadano', 'Acredita residencia permanente en la comuna.'),
  ('DOC003', 'Ayuda Social', 'beneficio', 'ciudadano', 'Prestación de ayuda social para personas en situación de vulnerabilidad.');

-- Requisitos para documentos
INSERT INTO documento_requisitos (documento_id, requisito) VALUES
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Certificado de antecedentes para fines especiales'),
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Declaración jurada ante notario de no estar afecto a prohibiciones'),
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Título de uso del local'),
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Declaración jurada de capital propio'),
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Resolución del Servicio de Salud o informe sanitario'),
  ((SELECT id FROM documentos WHERE id_documento='DOC002'), 'Acreditar domicilio en la comuna'),
  ((SELECT id FROM documentos WHERE id_documento='DOC002'), 'Cédula de identidad vigente'),
  ((SELECT id FROM documentos WHERE id_documento='DOC003'), 'Solicitud por oficina de partes'),
  ((SELECT id FROM documentos WHERE id_documento='DOC003'), 'Acreditar residencia en la comuna por al menos 2 años');

-- Oficinas asociadas
INSERT INTO documento_oficinas (documento_id, nombre, direccion, horario, correo, holocom) VALUES
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Depto. Rentas y Patentes', 'Av. Principal 123', 'Lun-Vie 09:00-14:00', 'rentas@municipalidad.cl', '600123456'),
  ((SELECT id FROM documentos WHERE id_documento='DOC002'), 'Oficina de Atención Ciudadana', 'Plaza Central 1', 'Lun-Vie 08:30-13:30', 'atencion@municipalidad.cl', '600654321'),
  ((SELECT id FROM documentos WHERE id_documento='DOC003'), 'Depto. Social', 'Calle Ayuda 45', 'Lun-Vie 09:00-13:00', 'social@municipalidad.cl', '600987654');

-- Duración/validez
INSERT INTO documento_duracion (documento_id, duracion) VALUES
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), '1 año'),
  ((SELECT id FROM documentos WHERE id_documento='DOC002'), 'Indefinida'),
  ((SELECT id FROM documentos WHERE id_documento='DOC003'), 'Según evaluación social');

-- Sanciones
INSERT INTO documento_sanciones (documento_id, sancion) VALUES
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Multa de 1 a 5 UTM por infracción a la ordenanza'),
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'Suspensión de la patente por incumplimiento de requisitos'),
  ((SELECT id FROM documentos WHERE id_documento='DOC002'), 'N/A'),
  ((SELECT id FROM documentos WHERE id_documento='DOC003'), 'N/A');

-- Notas adicionales
INSERT INTO documento_notas (documento_id, nota) VALUES
  ((SELECT id FROM documentos WHERE id_documento='DOC001'), 'La patente debe estar visible en el local.'),
  ((SELECT id FROM documentos WHERE id_documento='DOC002'), 'El certificado es requerido para trámites de postulación a beneficios.'),
  ((SELECT id FROM documentos WHERE id_documento='DOC003'), 'La ayuda social está sujeta a evaluación y disponibilidad de recursos.');

COMMIT;