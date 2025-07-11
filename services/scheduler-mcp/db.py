import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool

DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/munbot"  # Ajusta al DSN real si es necesario
)

_pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=DB_DSN)

def get_db():
    """Devuelve una conexión del pool."""
    return _pool.getconn()

def put_db(conn):
    _pool.putconn(conn)
