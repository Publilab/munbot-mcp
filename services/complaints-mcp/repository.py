import psycopg2
from typing import Optional
from uuid import uuid4

class ComplaintRepository:
    def __init__(self, conn):
        self.conn = conn

    def register_user(self, nombre: str, rut: str, ip: Optional[str] = None) -> str:
        """Guarda nombre y rut en la tabla users y devuelve su user_id."""
        user_id = str(uuid4())
        with self.conn.cursor() as cur:
            # Crear tabla si no existe
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                  id UUID PRIMARY KEY,
                  nombre VARCHAR NOT NULL,
                  rut VARCHAR NOT NULL,
                  ip TEXT,
                  creado_en TIMESTAMPTZ DEFAULT now()
                )
            """)
            # Insertar el usuario
            cur.execute("""
                INSERT INTO users (id, nombre, rut, ip, creado_en)
                VALUES (%s, %s, %s, %s, NOW())
            """, (user_id, nombre, rut, ip))
            self.conn.commit()
        return user_id

    def add_complaint(self, complaint, ip: Optional[str] = None) -> str:
        complaint_id = str(uuid4())
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO complaints (
                    id, nombre, rut, mail, mensaje, categoria, departamento, prioridad, estado, ip
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                complaint_id,
                complaint.nombre,
                complaint.rut,
                complaint.mail,
                complaint.mensaje,
                complaint.categoria,
                complaint.departamento,
                complaint.prioridad,
                'pendiente',
                ip
            ))
            self.conn.commit()
        return complaint_id

    def get_complaint(self, complaint_id: str):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT id, estado, creado_en, mensaje, nombre, categoria, departamento, prioridad
                FROM complaints
                WHERE id = %s
            """, (complaint_id,))
            row = cur.fetchone()
            if not row:
                return None
            keys = ['id', 'estado', 'creado_en', 'mensaje', 'nombre', 'categoria', 'departamento', 'prioridad']
            return dict(zip(keys, row))
