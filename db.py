"""
db.py — Neon PostgreSQL connection layer (psycopg2).

Connection strategy:
  - Inside Flask: ONE connection per request, cached on flask.g and closed
    by the teardown hook registered in app.py. Writes auto-commit; any
    exception rolls the transaction back.
  - Outside Flask (scripts like test_db.py / migrate_sqlite_to_postgres.py):
    each db_connection() opens a fresh connection, commits and closes it.
  - Neon's pooled endpoint (-pooler) makes short-lived connections cheap, and
    per-request connections are exactly what serverless (Vercel) needs.
"""
from contextlib import contextmanager

import psycopg2
from flask import g, has_app_context

from config import Config


def connect():
    """Open a new PostgreSQL connection (Neon requires SSL)."""
    if not Config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Put it in a .env file (see .env.example) "
            "or export the environment variable.")
    conn = psycopg2.connect(Config.DATABASE_URL, connect_timeout=15)
    conn.autocommit = False
    return conn


@contextmanager
def db_connection():
    if has_app_context():
        if "pg_conn" not in g:
            g.pg_conn = connect()
        conn = g.pg_conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    else:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def close_db(exc=None):
    """Flask teardown — release the request's connection."""
    conn = g.pop("pg_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def test_connection():
    """Health check used by test_db.py and /admin/db-status."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            database, user, version = cur.fetchone()
        conn.rollback()
        return {"ok": True, "database": database, "user": user,
                "server": version.split(",")[0]}
    finally:
        conn.close()