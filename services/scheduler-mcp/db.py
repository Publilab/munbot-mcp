import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool

DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/munbot"  # Ajusta al DSN real si es necesario
)

TESTING = os.getenv("TESTING") == "1" or os.getenv("AUDIT_SCHEDULER_DEBUG") == "true"

if not TESTING:
    _pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=DB_DSN)

    def get_db():
        """Devuelve una conexión del pool."""
        return _pool.getconn()

    def put_db(conn):
        _pool.putconn(conn)
else:
    class _Dummy:
        def cursor(self, *a, **k):
            class C:
                def execute(self, *a, **k):
                    pass

                def fetchall(self):
                    return []

                def fetchone(self):
                    return None

            return C()

        def close(self):
            pass

    def get_db():
        return _Dummy()

    def put_db(conn):
        pass
