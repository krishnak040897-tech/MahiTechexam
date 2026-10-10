"""config.py — MahiTech configuration."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "mahitech-super-secret-key-change-me")

    # ---------------- Neon PostgreSQL ----------------
    # Default connection (env var DATABASE_URL wins — set it on Vercel).
    DATABASE_URL = (
        "postgresql://neondb_owner:npg_c2lW7snjfItF@"
        "ep-cool-paper-b7a5jqmt-pooler.c-13.us-east-1.aws.neon.tech/neondb"
        "?channel_binding=require&sslmode=require"
    )
    # OLD SQLite file location — only used by migrate_sqlite_to_postgres.py
    SQLITE_FILE = os.path.join(BASE_DIR, "mahitech.db")

    # Admin — verified directly on the login page
    ADMIN_USERNAME = "Mahesh"
    ADMIN_PASSWORD = "Mahesh123"

    # ---------------- Razorpay ----------------
    RAZORPAY_KEY_ID = "rzp_test_TlIH3jGp1woHCW"
    RAZORPAY_KEY_SECRET = "J4ELJiNqQ6XIIPHfv7sU03mw"

    DEMO_MODE = False
    RAZORPAY_TEST_MODE = RAZORPAY_KEY_ID.startswith("rzp_test_")

    # ---------------- Cloudinary (file storage) ----------------
    CLOUDINARY_CLOUD_NAME = "qwx7mhnn"
    CLOUDINARY_API_KEY = "712527662128967"
    CLOUDINARY_API_SECRET = "ClrjKXSYhheaf_IAlnbhdBMLU80"

    # Local fallback storage (ephemeral on Vercel — Cloudinary is primary)
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    THUMBNAIL_FOLDER = os.path.join(UPLOAD_FOLDER, "thumbnails")
    PDF_FOLDER = os.path.join(UPLOAD_FOLDER, "pdfs")
    VIDEO_FOLDER = os.path.join(UPLOAD_FOLDER, "videos")

    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    ALLOWED_PDF_EXTENSIONS = {"pdf"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "mkv"}

    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB (note: Vercel caps requests ~4.5 MB)

    CATEGORIES = [
        "Government Exams",
        "Competitive Exams",
        "Entrance Exams",
        "Academic Preparation",
        "Career Preparation",
    ]

    SEED_DEMO_DATA = True