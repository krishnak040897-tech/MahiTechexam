"""models.py — Neon PostgreSQL data layer (psycopg2).

Drop-in replacement for the SQLite layer: every function keeps the SAME
name, signature and return shape (dict rows, "YYYY-MM-DD HH:MM:SS"
timestamp strings, new ids returned from inserts), so app.py, all
templates, Razorpay, Cloudinary and the exam-timer logic are unchanged.

Key protections built in:
  1. URL resolution is IDENTICAL in every process (Flask debug mode runs a
     parent + 'Restarting with stat' child; a stray/empty DATABASE_URL env
     var used to make the child dial localhost:5432 and kill the app). An
     env var is trusted ONLY if it is a real postgres:// URL that does not
     point at localhost; otherwise the hardcoded Neon URL from config.py
     wins. On Vercel your DATABASE_URL env var still wins.
  2. _ensure_columns() REPAIRS pre-existing tables with older shapes —
     CREATE TABLE IF NOT EXISTS never upgrades a table that already exists
     (this is what broke the migration: Neon's 'exams' table had no
     thumbnail/pdf_file columns). Additive + idempotent on every startup.
  3. Connection test runs the reference checks (current_database /
     current_user / SELECT 1) with retries for Neon cold starts.
  4. Timestamps: writes pass Python-LOCAL strings; reads convert datetime
     objects back to the same string — templates and the server-side exam
     timer behave exactly as with SQLite.
  5. Row access: RealDictCursor -> plain dicts (row["col"] and row.col).
     Ids: INSERT ... RETURNING id. Placeholders: %s.
"""
import os
import time
from datetime import datetime

import psycopg2
import psycopg2.extras

from config import Config

SCHEMA_VERSION = 5


# ---------------- bulletproof URL resolution (same in EVERY process) ----------------
def _resolve_database_url():
    """
    Returns (url, source_description).

    An environment DATABASE_URL is used ONLY when it is a non-empty
    postgres:// / postgresql:// URL that does NOT point at localhost —
    a stray/empty/localhost value (what broke the reloader child) is
    ignored and the hardcoded Neon URL from config.py is used instead.
    """
    env = (os.environ.get("DATABASE_URL") or "").strip()
    if (env.startswith(("postgres://", "postgresql://"))
            and "localhost" not in env and "127.0.0.1" not in env):
        return env, "environment variable DATABASE_URL"
    return Config.DATABASE_URL, "config.py (hardcoded Neon URL)"


DATABASE_URL, _URL_SOURCE = _resolve_database_url()


def _mask_url(url):
    """postgresql://user:pass@host/db -> postgresql://user:***@host/db"""
    try:
        scheme, rest = url.split("://", 1)
        at = rest.rfind("@")
        if at > 0:
            user = rest[:at].split(":", 1)[0]
            return f"{scheme}://{user}:***@{rest[at + 1:]}"
    except Exception:
        pass
    return "(hidden)"


_DictCursor = psycopg2.extras.RealDictCursor

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
    id           SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    category     TEXT NOT NULL,
    price        DOUBLE PRECISION NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT 'pdf',
    thumbnail    TEXT,
    pdf_file     TEXT,
    video_file   TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS purchases (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    product_id    INTEGER          REFERENCES products(id) ON DELETE SET NULL,
    product_title TEXT NOT NULL DEFAULT '',
    amount        DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_id    TEXT,
    order_id      TEXT,
    status        TEXT NOT NULL DEFAULT 'paid',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS exams (
    id               SERIAL PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT DEFAULT '',
    price            DOUBLE PRECISION NOT NULL DEFAULT 0,
    duration_minutes INTEGER NOT NULL DEFAULT 15,
    pass_percent     INTEGER NOT NULL DEFAULT 40,
    thumbnail        TEXT,
    pdf_file         TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS exam_purchases (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exam_id    INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    amount     DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_id TEXT,
    order_id   TEXT,
    status     TEXT NOT NULL DEFAULT 'paid',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS exam_questions (
    id       SERIAL PRIMARY KEY,
    exam_id  INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    opt_a    TEXT NOT NULL,
    opt_b    TEXT NOT NULL,
    opt_c    TEXT NOT NULL,
    opt_d    TEXT NOT NULL,
    correct  TEXT NOT NULL CHECK (correct IN ('a','b','c','d'))
);
CREATE TABLE IF NOT EXISTS exam_attempts (
    id           SERIAL PRIMARY KEY,
    exam_id      INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    score        INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,
    answers      TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'in_progress',
    started_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS exam_backups (
    id              SERIAL PRIMARY KEY,
    filename        TEXT NOT NULL,
    url             TEXT DEFAULT '',
    exam_count      INTEGER NOT NULL DEFAULT 0,
    question_count  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'cloud',
    kind            TEXT NOT NULL DEFAULT 'full',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DEMO_PRODUCTS = [
    ("SSC CGL 2025 Complete Study Kit",
     "Chapter-wise notes, 5000+ practice questions and previous-year papers.",
     "Government Exams", 499, "pdf"),
    ("Bank PO Banking Awareness Crasher",
     "Banking awareness notes with static GK and 20 mock sets.",
     "Competitive Exams", 299, "pdf"),
    ("NEET UG Physics Formula Handbook",
     "Every formula and shortcut for NEET Physics, with solved examples.",
     "Entrance Exams", 199, "both"),
    ("Class 12 Mathematics Chapterwise Notes",
     "NCERT-aligned notes with solved board questions and MCQs.",
     "Academic Preparation", 249, "pdf"),
    ("Placement Prep Video Bundle",
     "Aptitude, reasoning and interview video sessions.",
     "Career Preparation", 349, "video"),
]


# ---------------- schema repair (pre-existing/older table shapes) ----------------
# CREATE TABLE IF NOT EXISTS never upgrades a table that already exists.
# If a table was created earlier with an older shape (e.g. 'exams' without
# thumbnail/pdf_file), these ALTERs add the missing columns — additive and
# idempotent, so it is safe on every startup (locally and on Vercel).
_ENSURE_COLUMNS = [
    ("users",          "created_at",     "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ("products",       "thumbnail",      "TEXT"),
    ("products",       "pdf_file",       "TEXT"),
    ("products",       "video_file",     "TEXT"),
    ("products",       "created_at",     "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ("purchases",      "payment_id",     "TEXT"),
    ("purchases",      "order_id",       "TEXT"),
    ("purchases",      "created_at",     "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ("exams",          "thumbnail",      "TEXT"),
    ("exams",          "pdf_file",       "TEXT"),
    ("exams",          "created_at",     "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ("exam_purchases", "payment_id",     "TEXT"),
    ("exam_purchases", "order_id",       "TEXT"),
    ("exam_purchases", "created_at",     "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ("exam_attempts",  "submitted_at",   "TIMESTAMP"),
    ("exam_backups",   "url",            "TEXT DEFAULT ''"),
    ("exam_backups",   "exam_count",     "INTEGER NOT NULL DEFAULT 0"),
    ("exam_backups",   "question_count", "INTEGER NOT NULL DEFAULT 0"),
    ("exam_backups",   "status",         "TEXT NOT NULL DEFAULT 'cloud'"),
    ("exam_backups",   "kind",           "TEXT NOT NULL DEFAULT 'full'"),
    ("exam_backups",   "created_at",     "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
]


def _ensure_columns(conn):
    """Add any missing columns to existing tables. Prints what was fixed."""
    fixed = []
    with conn.cursor() as cur:
        for table, column, ddl in _ENSURE_COLUMNS:
            cur.execute(
                """SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""",
                (table, column))
            if cur.fetchone() is None:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                fixed.append(f"{table}.{column}")
    if fixed:
        print("[MahiTech] Schema repair: added missing column(s) -> "
              + ", ".join(fixed))
    else:
        print("[MahiTech] Schema columns verified — nothing to repair.")


# ---------------- connection + shared helpers ----------------
def get_db():
    """One PostgreSQL connection per call — the Neon POOLER endpoint
    (already in the URL) is built exactly for this usage pattern."""
    return psycopg2.connect(DATABASE_URL, connect_timeout=15)


def _now():
    """Local timestamp string — the exact format the old SQLite layer used."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_value(v):
    # PostgreSQL returns datetime objects; templates & strptime expect the
    # same "YYYY-MM-DD HH:MM:SS" strings SQLite used to return.
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def _query(sql, params=(), one=False):
    """Run a SELECT, return list of dict rows (or a single row)."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=_DictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = [{k: _norm_value(v) for k, v in r.items()}
                    for r in cur.fetchall()]
        return (rows[0] if rows else None) if one else rows
    finally:
        conn.close()          # read-only: closing discards the transaction


def _write(sql, params=(), returning_id=False):
    """Run an INSERT/UPDATE/DELETE + COMMIT. Returns the new id if asked."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=_DictCursor) as cur:
            cur.execute(sql, tuple(params))
            new_id = None
            if returning_id:
                row = cur.fetchone()
                new_id = row["id"] if row else None
        conn.commit()
        return new_id
    finally:
        conn.close()          # on error: closing without commit -> rollback


# ---------------- meta helpers ----------------
def _get_meta(conn, key):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM meta WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except psycopg2.Error:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _set_meta(conn, key, value):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO meta (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (key, value))


def _print_summary():
    try:
        row = _query(
            """SELECT (SELECT COUNT(*) FROM users)          AS users,
                      (SELECT COUNT(*) FROM products)       AS products,
                      (SELECT COUNT(*) FROM exams)          AS exams,
                      (SELECT COUNT(*) FROM exam_questions) AS questions,
                      (SELECT COUNT(*) FROM exam_attempts)  AS attempts,
                      (SELECT COUNT(*) FROM exam_backups)   AS backups""",
            one=True)
        print(f"[MahiTech] DB ready: {row['users']} users, {row['products']} products, "
              f"{row['exams']} exam paper(s), {row['questions']} exam question(s), "
              f"{row['attempts']} attempt(s), {row['backups']} cloud record(s) "
              f"(persisted in Neon PostgreSQL)")
    except psycopg2.Error:
        pass


# ---------------- connection test (with retries for Neon cold starts) ----------------
def _try_connect(attempts=3, wait=2):
    """Try to connect + run the reference checks. Returns (conn, info) or
    (None, last_error) after all attempts."""
    last_error = None
    for i in range(1, attempts + 1):
        conn = None
        try:
            print(f"[MahiTech] Connecting to Neon PostgreSQL "
                  f"(source: {_URL_SOURCE}, url: {_mask_url(DATABASE_URL)}, "
                  f"try {i}/{attempts})...")
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, version()")
                database, user, version = cur.fetchone()
                cur.execute("SELECT 1")
                if cur.fetchone()[0] != 1:
                    raise psycopg2.Error("SELECT 1 did not return 1")
            conn.commit()
            return conn, (database, user, version)
        except (psycopg2.OperationalError, psycopg2.Error) as error:
            last_error = error
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if i < attempts:
                print(f"[MahiTech]   attempt {i} failed: {error}")
                print(f"[MahiTech]   retrying in {wait}s (Neon computes sleep; "
                      "the first connection can take a few seconds)...")
                time.sleep(wait)
    return None, last_error


# ---------------- init: connection test + schema + repair ----------------
def init_db(seed=False):
    # ---- 1) connection test (retries included) ----
    conn, info = _try_connect()
    if conn is None:
        print("\n[MahiTech] ⚠ DATABASE CONNECTION FAILED (all retries)")
        print("[MahiTech] ⚠ Check your database URL, password, internet,")
        print("[MahiTech] ⚠ SSL settings, and Neon database availability.")
        print(f"[MahiTech] ⚠ Details: {info}\n")
        raise SystemExit(1)

    database, user, version = info
    print("\n[MahiTech] SUCCESS: Connected to Neon PostgreSQL!")
    print(f"[MahiTech]   Database: {database}")
    print(f"[MahiTech]   User:     {user}")
    print(f"[MahiTech]   Server:   {version.split(',')[0]}")
    conn.close()
    print("[MahiTech] Connection test closed.")

    # ---- 2) schema + repair + meta + optional demo seed ----
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)          # CREATE TABLE IF NOT EXISTS — idempotent
        _ensure_columns(conn)            # REPAIR: add columns missing from
                                         # pre-existing/older table shapes
        _set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        if seed:
            with conn.cursor(cursor_factory=_DictCursor) as cur:
                cur.execute("SELECT COUNT(*) AS c FROM products")
                if cur.fetchone()["c"] == 0:
                    cur.executemany(
                        """INSERT INTO products
                           (title, description, category, price, content_type, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        [(t, d, c, p, ct, _now())
                         for (t, d, c, p, ct) in DEMO_PRODUCTS])
        conn.commit()
        print(f"[MahiTech] PostgreSQL schema ready (schema_version={SCHEMA_VERSION}).")
    finally:
        conn.close()

    _print_summary()


# ---------------- USERS ----------------
def create_user(username, email, password_hash):
    return _write(
        """INSERT INTO users (username, email, password, created_at)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (username, email, password_hash, _now()), returning_id=True)


def get_user_by_id(user_id):
    return _query("SELECT * FROM users WHERE id = %s", (user_id,), one=True)


def get_user_by_username(username):
    return _query("SELECT * FROM users WHERE username = %s", (username,), one=True)


def get_user_by_email(email):
    return _query("SELECT * FROM users WHERE email = %s", (email,), one=True)


def get_user_by_username_or_email(identifier):
    return _query(
        "SELECT * FROM users WHERE username = %s OR LOWER(email) = LOWER(%s)",
        (identifier, identifier), one=True)


def get_all_users():
    return _query(
        """SELECT u.*, COUNT(p.id) AS purchase_count,
                  COALESCE(SUM(p.amount), 0) AS total_spent
           FROM users u LEFT JOIN purchases p ON p.user_id = u.id
           GROUP BY u.id ORDER BY u.id DESC""")


def delete_user(user_id):
    # purchases / exam_purchases / exam_attempts cascade via FKs (like before)
    _write("DELETE FROM users WHERE id = %s", (user_id,))


def count_users():
    return _query("SELECT COUNT(*) AS c FROM users", one=True)["c"]


# ---------------- PRODUCTS ----------------
def create_product(title, description, category, price, content_type,
                   thumbnail=None, pdf_file=None, video_file=None):
    return _write(
        """INSERT INTO products
           (title, description, category, price, content_type,
            thumbnail, pdf_file, video_file, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, description, category, price, content_type,
         thumbnail, pdf_file, video_file, _now()), returning_id=True)


def update_product(product_id, title, description, category, price, content_type,
                   thumbnail=None, pdf_file=None, video_file=None):
    _write(
        """UPDATE products SET title=%s, description=%s, category=%s, price=%s,
           content_type=%s, thumbnail=%s, pdf_file=%s, video_file=%s WHERE id=%s""",
        (title, description, category, price, content_type,
         thumbnail, pdf_file, video_file, product_id))


def get_product_by_id(product_id):
    return _query("SELECT * FROM products WHERE id = %s", (product_id,), one=True)


def get_products(search=None, category=None, limit=None):
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if search:
        sql += " AND (LOWER(title) LIKE %s OR LOWER(description) LIKE %s)"
        params += [f"%{search.lower()}%"] * 2
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return _query(sql, params)


def set_product_file(product_id, column, value):
    if column not in ("thumbnail", "pdf_file", "video_file"):
        raise ValueError("bad column")
    _write(f"UPDATE products SET {column}=%s WHERE id=%s", (value, product_id))


def delete_product(product_id):
    _write("DELETE FROM products WHERE id = %s", (product_id,))


def count_products():
    return _query("SELECT COUNT(*) AS c FROM products", one=True)["c"]


# ---------------- PURCHASES (PRODUCTS) ----------------
def create_purchase(user_id, product_id, product_title, amount,
                    payment_id, order_id, status="paid"):
    return _write(
        """INSERT INTO purchases
           (user_id, product_id, product_title, amount, payment_id, order_id,
            status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, product_id, product_title, amount, payment_id, order_id,
         status, _now()), returning_id=True)


def has_purchased(user_id, product_id):
    return _query(
        "SELECT 1 FROM purchases WHERE user_id = %s AND product_id = %s LIMIT 1",
        (user_id, product_id), one=True) is not None


def get_user_purchases(user_id):
    return _query(
        """SELECT pu.*, pr.category, pr.content_type, pr.thumbnail
           FROM purchases pu LEFT JOIN products pr ON pr.id = pu.product_id
           WHERE pu.user_id = %s ORDER BY pu.id DESC""", (user_id,))


def get_all_purchases(limit=None):
    sql = """SELECT pu.*, u.username, u.email FROM purchases pu
             LEFT JOIN users u ON u.id = pu.user_id ORDER BY pu.id DESC"""
    params = []
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return _query(sql, params)


def delete_purchase(purchase_id):
    _write("DELETE FROM purchases WHERE id = %s", (purchase_id,))


def count_purchases():
    return _query("SELECT COUNT(*) AS c FROM purchases", one=True)["c"]


def total_revenue():
    return _query("SELECT COALESCE(SUM(amount),0) AS s FROM purchases",
                  one=True)["s"]


# ---------------- EXAMS (standalone papers + Cloudinary files) ----------------
def create_exam(title, description, price, duration_minutes, pass_percent,
                thumbnail=None, pdf_file=None):
    return _write(
        """INSERT INTO exams (title, description, price, duration_minutes,
                              pass_percent, thumbnail, pdf_file, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, description, price, duration_minutes, pass_percent,
         thumbnail, pdf_file, _now()), returning_id=True)


def update_exam(exam_id, title, description, price, duration_minutes, pass_percent,
                thumbnail=None, pdf_file=None):
    _write(
        """UPDATE exams SET title=%s, description=%s, price=%s,
           duration_minutes=%s, pass_percent=%s, thumbnail=%s, pdf_file=%s
           WHERE id=%s""",
        (title, description, price, duration_minutes, pass_percent,
         thumbnail, pdf_file, exam_id))


def delete_exam(exam_id):
    _write("DELETE FROM exams WHERE id = %s", (exam_id,))


def get_exam_by_id(exam_id):
    return _query("SELECT * FROM exams WHERE id = %s", (exam_id,), one=True)


def get_exams():
    """All exam papers (public browse + admin), with question counts."""
    return _query(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM exam_questions q WHERE q.exam_id = e.id) AS q_count
           FROM exams e ORDER BY e.id DESC""")


def count_exams():
    return _query("SELECT COUNT(*) AS c FROM exams", one=True)["c"]


def set_exam_file(exam_id, column, value):
    if column not in ("thumbnail", "pdf_file"):
        raise ValueError("bad column")
    _write(f"UPDATE exams SET {column}=%s WHERE id=%s", (value, exam_id))


# ---------------- EXAM PURCHASES ----------------
def create_exam_purchase(user_id, exam_id, amount, payment_id, order_id, status="paid"):
    return _write(
        """INSERT INTO exam_purchases
           (user_id, exam_id, amount, payment_id, order_id, status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, exam_id, amount, payment_id, order_id, status, _now()),
        returning_id=True)


def has_purchased_exam(user_id, exam_id):
    return _query(
        "SELECT 1 FROM exam_purchases WHERE user_id = %s AND exam_id = %s LIMIT 1",
        (user_id, exam_id), one=True) is not None


def get_owned_exam_ids(user_id):
    rows = _query("SELECT exam_id FROM exam_purchases WHERE user_id = %s", (user_id,))
    return {r["exam_id"] for r in rows}


def get_purchased_exams(user_id):
    """Exams the user bought, with best score and active-attempt flag."""
    return _query(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM exam_questions q WHERE q.exam_id = e.id) AS q_count,
                  (SELECT MAX(a.score) FROM exam_attempts a
                    WHERE a.exam_id = e.id AND a.user_id = %s AND a.status = 'submitted'
                  ) AS best_score,
                  (SELECT MAX(a.total) FROM exam_attempts a
                    WHERE a.exam_id = e.id AND a.user_id = %s AND a.status = 'submitted'
                  ) AS best_total,
                  (SELECT COUNT(*) FROM exam_attempts a
                    WHERE a.exam_id = e.id AND a.user_id = %s AND a.status = 'in_progress'
                  ) AS has_active
           FROM exams e
           JOIN exam_purchases ep ON ep.exam_id = e.id AND ep.user_id = %s
           GROUP BY e.id ORDER BY e.id DESC""",
        (user_id, user_id, user_id, user_id))


# ---------------- EXAM QUESTIONS ----------------
def add_question(exam_id, question, a, b, c, d, correct):
    return _write(
        """INSERT INTO exam_questions
           (exam_id, question, opt_a, opt_b, opt_c, opt_d, correct)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (exam_id, question, a, b, c, d, correct), returning_id=True)


def update_question(qid, question, a, b, c, d, correct):
    _write(
        """UPDATE exam_questions SET question=%s, opt_a=%s, opt_b=%s, opt_c=%s,
           opt_d=%s, correct=%s WHERE id=%s""",
        (question, a, b, c, d, correct, qid))


def delete_question(qid):
    _write("DELETE FROM exam_questions WHERE id = %s", (qid,))


def get_question_by_id(qid):
    return _query("SELECT * FROM exam_questions WHERE id = %s", (qid,), one=True)


def get_questions(exam_id):
    """Full rows (includes 'correct') — admin preview + server-side grading."""
    return _query(
        "SELECT * FROM exam_questions WHERE exam_id = %s ORDER BY id", (exam_id,))


def get_questions_public(exam_id):
    """Client-safe rows for the exam-taking page (no correct answer)."""
    return _query(
        """SELECT id, exam_id, question, opt_a, opt_b, opt_c, opt_d
           FROM exam_questions WHERE exam_id = %s ORDER BY id""", (exam_id,))


def count_questions(exam_id):
    return _query(
        "SELECT COUNT(*) AS c FROM exam_questions WHERE exam_id = %s",
        (exam_id,), one=True)["c"]


# ---------------- EXAM ATTEMPTS ----------------
def create_attempt(exam_id, user_id):
    return _write(
        """INSERT INTO exam_attempts (exam_id, user_id, started_at)
           VALUES (%s, %s, %s) RETURNING id""",
        (exam_id, user_id, _now()), returning_id=True)


def get_attempt_by_id(attempt_id):
    return _query("SELECT * FROM exam_attempts WHERE id = %s", (attempt_id,), one=True)


def get_active_attempt(exam_id, user_id):
    return _query(
        """SELECT * FROM exam_attempts WHERE exam_id = %s AND user_id = %s
           AND status = 'in_progress' ORDER BY id DESC LIMIT 1""",
        (exam_id, user_id), one=True)


def finish_attempt(attempt_id, score, total, answers):
    _write(
        """UPDATE exam_attempts SET score=%s, total=%s, answers=%s,
           status='submitted', submitted_at=%s WHERE id=%s""",
        (score, total, answers, _now(), attempt_id))


def get_user_attempts(exam_id, user_id):
    return _query(
        """SELECT * FROM exam_attempts WHERE exam_id = %s AND user_id = %s
           AND status = 'submitted' ORDER BY id DESC""",
        (exam_id, user_id))


def get_all_attempts(limit=None):
    """All attempts with user + exam info — admin results page & cloud sync."""
    sql = """SELECT a.*, u.username, u.email,
                    e.title AS exam_title, e.pass_percent, e.duration_minutes
             FROM exam_attempts a
             LEFT JOIN users u ON u.id = a.user_id
             LEFT JOIN exams e ON e.id = a.exam_id
             ORDER BY a.id DESC"""
    params = []
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return _query(sql, params)


# ---------------- EXAM BACKUPS / DATA SYNC RECORDS ----------------
def record_exam_backup(filename, url, exam_count, question_count,
                       status="cloud", kind="full"):
    return _write(
        """INSERT INTO exam_backups
           (filename, url, exam_count, question_count, status, kind, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (filename, url, exam_count, question_count, status, kind, _now()),
        returning_id=True)


def get_exam_backups(limit=15):
    return _query("SELECT * FROM exam_backups ORDER BY id DESC LIMIT %s", (limit,))


def get_exam_backup_by_id(backup_id):
    return _query("SELECT * FROM exam_backups WHERE id = %s", (backup_id,), one=True)


def delete_exam_backup(backup_id):
    _write("DELETE FROM exam_backups WHERE id = %s", (backup_id,))