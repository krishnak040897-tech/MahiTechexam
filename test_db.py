"""
test_db.py — Neon PostgreSQL connection + CRUD test for every table.
Run:  python test_db.py
"""
import psycopg2

import models
from config import Config
from db import connect


def main():
    if not Config.DATABASE_URL:
        print("ERROR: DATABASE_URL is missing — create .env (see .env.example).")
        raise SystemExit(1)

    print("Connecting to Neon PostgreSQL...")
    try:
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            database, user, version = cur.fetchone()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
        conn.close()
        print(f"\nSUCCESS: Connected to Neon PostgreSQL!")
        print(f"Database: {database}\nUser: {user}\nServer: {version.split(',')[0]}")
    except psycopg2.OperationalError as error:
        print("\nCONNECTION FAILED — check the URL, password, SSL and Neon status.")
        print(f"Details: {error}")
        raise SystemExit(1)

    # ---- schema ----
    print("\n[1/7] Creating schema (idempotent)...")
    models.init_db(seed=False)
    print("      ✓ tables + indexes ready")

    # ---- CRUD on every table ----
    print("[2/7] users: create / read / delete...")
    uid = models.create_user("__test_user__", "testuser@mahitech.test",
                             "test-hash")
    assert models.get_user_by_id(uid)["username"] == "__test_user__"
    print(f"      ✓ user id={uid}")

    print("[3/7] products + purchases...")
    pid = models.create_product("__test product__", "test", "Government Exams",
                                10.0, "pdf")
    pr_id = models.create_purchase(uid, pid, "__test product__", 10.0, "pay_test", "ord_test")
    assert models.has_purchased(uid, pid)
    print(f"      ✓ product id={pid}, purchase id={pr_id}")

    print("[4/7] exams + questions...")
    eid = models.create_exam("__test exam__", "test", 0, 10, 50)
    qid = models.add_question(eid, "2+2?", "3", "4", "5", "6", "b")
    assert models.count_questions(eid) == 1
    assert models.get_questions_public(eid)[0]["question"] == "2+2?"
    print(f"      ✓ exam id={eid}, question id={qid}")

    print("[5/7] exam purchases + attempts + grading...")
    models.create_exam_purchase(uid, eid, 0, "free", "free")
    assert models.has_purchased_exam(uid, eid)
    aid = models.create_attempt(eid, uid)
    models.finish_attempt(aid, 1, 1, f"{qid}:b")
    a = models.get_attempt_by_id(aid)
    assert a["score"] == 1 and a["status"] == "submitted"
    assert models.get_active_attempt(eid, uid) is None
    print(f"      ✓ attempt id={aid} graded 1/1")

    print("[6/7] backup records...")
    bid = models.record_exam_backup("__test.json", "http://example/t.json",
                                     1, 1, "cloud", kind="papers")
    assert models.get_exam_backup_by_id(bid)["filename"] == "__test.json"
    print(f"      ✓ backup record id={bid}")

    print("[7/7] cleanup (cascades)...")
    models.delete_exam(eid)             # cascades questions/purchases/attempts
    models.delete_purchase(pr_id)
    models.delete_product(pid)
    models.delete_user(uid)             # cascades remaining purchases
    models.delete_exam_backup(bid)
    assert models.get_user_by_id(uid) is None
    print("      ✓ all test rows removed")

    print("\nALL DATABASE TESTS PASSED — every table works on Neon PostgreSQL.")


if __name__ == "__main__":
    main()