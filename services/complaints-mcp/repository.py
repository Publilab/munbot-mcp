import psycopg2
from typing import Optional
from uuid import uuid4

class ComplaintRepository:
    def __init__(self, conn):
        self.conn = conn

    def add_complaint(self, complaint, ip: Optional[str] = None) -> str:
        complaint_id = str(uuid4())
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO complaints (
                    id, nombre, mail, mensaje, categoria, departamento, prioridad, estado, ip
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                complaint_id,
                complaint.nombre,
                complaint.mail,
                complaint.mensaje,
                complaint.categoria,
                complaint.departamento,
                getattr(complaint, 'prioridad', 3),
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
