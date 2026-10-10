"""
migrate_sqlite_to_postgres.py — copy old SQLite data into Neon PostgreSQL.

    python migrate_sqlite_to_postgres.py            # MERGE: copies only rows
                                                    # whose id is missing in PG
    python migrate_sqlite_to_postgres.py --fresh    # wipe PG data tables first,
                                                    # then copy EVERYTHING from
                                                    # SQLite (exact copy)

RECOMMENDED NOW: use --fresh — your Neon DB currently has only test data
(1 exam / 1 question) while SQLite has the real data (3 exams / 3 questions).
"""
import os
import sqlite3
import sys

import psycopg2

import models

SQLITE_PATH = models.Config.SQLITE_FILE

TABLES = [
    ("users",          ["id", "username", "email", "password", "created_at"]),
    ("products",       ["id", "title", "description", "category", "price",
                        "content_type", "thumbnail", "pdf_file", "video_file",
                        "created_at"]),
    ("purchases",      ["id", "user_id", "product_id", "product_title", "amount",
                        "payment_id", "order_id", "status", "created_at"]),
    ("exams",          ["id", "title", "description", "price", "duration_minutes",
                        "pass_percent", "thumbnail", "pdf_file", "created_at"]),
    ("exam_purchases", ["id", "user_id", "exam_id", "amount", "payment_id",
                        "order_id", "status", "created_at"]),
    ("exam_questions", ["id", "exam_id", "question", "opt_a", "opt_b", "opt_c",
                        "opt_d", "correct"]),
    ("exam_attempts",  ["id", "exam_id", "user_id", "score", "total", "answers",
                        "status", "started_at", "submitted_at"]),
    ("exam_backups",   ["id", "filename", "url", "exam_count", "question_count",
                        "status", "kind", "created_at"]),
]


def _wipe(pg):
    """Delete all data rows (FK-safe order: children first)."""
    for table in ("exam_attempts", "exam_questions", "exam_backups",
                  "exam_purchases", "purchases", "exams", "products", "users"):
        with pg.cursor() as cur:
            cur.execute(f"DELETE FROM {table}")
    pg.commit()
    print("PostgreSQL data tables wiped (--fresh mode).")


def main():
    fresh = "--fresh" in sys.argv

    if not os.path.exists(SQLITE_PATH):
        print(f"No SQLite file at {SQLITE_PATH} — nothing to migrate.")
        return

    lite = sqlite3.connect(SQLITE_PATH)
    lite.row_factory = sqlite3.Row
    pg = psycopg2.connect(models.DATABASE_URL, connect_timeout=15)

    if fresh:
        _wipe(pg)

    total_copied = 0
    try:
        for table, cols in TABLES:
            # only copy columns that actually exist in the old SQLite db
            lite_cols = [r[1] for r in lite.execute(f"PRAGMA table_info({table})")]
            if not lite_cols:
                print(f"  – {table}: not present in SQLite — skipped")
                continue
            use_cols = [c for c in cols if c in lite_cols]

            rows = lite.execute(
                f"SELECT {', '.join(use_cols)} FROM {table}").fetchall()
            if not rows:
                print(f"  – {table}: empty in SQLite — nothing to copy")
                continue

            placeholders = ", ".join(["%s"] * len(use_cols))
            conflict = " ON CONFLICT (id) DO NOTHING" if "id" in use_cols else ""
            inserted = 0
            with pg.cursor() as cur:
                for r in rows:
                    cur.execute(
                        f"INSERT INTO {table} ({', '.join(use_cols)}) "
                        f"VALUES ({placeholders}){conflict}",
                        tuple(r[c] for c in use_cols))
                    inserted += cur.rowcount if cur.rowcount > 0 else 0
                # keep SERIAL ids correct for future inserts
                if "id" in use_cols:
                    cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
                    max_id = cur.fetchone()[0]
                    if max_id > 0:
                        cur.execute(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"{max_id})")
            pg.commit()
            total_copied += inserted
            print(f"  ✓ {table}: {inserted} new row(s) copied "
                  f"({len(rows) - inserted} already existed)")
    finally:
        pg.close()
        lite.close()

    print(f"\nDone. Total new rows copied: {total_copied}.")
    print("Start the app — your data is now served from Neon PostgreSQL.")


if __name__ == "__main__":
    main()