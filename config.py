"""config.py — MahiTech configuration.
Locally: the hardcoded fallbacks are used.
On Vercel: environment variables (set in the Vercel dashboard) win.
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Vercel's filesystem is read-only except /tmp — the local upload fallback
# goes there (Cloudinary is the primary store, so this is rarely used).
_IS_VERCEL = bool(os.environ.get("VERCEL"))
_UPLOAD_BASE = "/tmp/mahitech-uploads" if _IS_VERCEL else os.path.join(BASE_DIR, "static", "uploads")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "mahitech-super-secret-key-change-me")

    # ---------------- Neon PostgreSQL ----------------
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://neondb_owner:npg_c2lW7snjfItF@"
        "ep-cool-paper-b7a5jqmt-pooler.c-13.us-east-1.aws.neon.tech/neondb"
        "?channel_binding=require&sslmode=require")
    # OLD SQLite file — only used by migrate_sqlite_to_postgres.py
    SQLITE_FILE = os.path.join(BASE_DIR, "mahitech.db")

    # Admin
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Mahesh")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Mahesh123")

    # ---------------- Razorpay ----------------
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_TlIH3jGp1woHCW")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "J4ELJiNqQ6XIIPHfv7sU03mw")

    DEMO_MODE = False
    RAZORPAY_TEST_MODE = not RAZORPAY_KEY_ID.startswith("rzp_live_")

    # ---------------- Cloudinary ----------------
    CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "qwx7mhnn")
    CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "712527662128967")
    CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "ClrjKXSYhheaf_IAlnbhdBMLU80")

    # Local fallback storage
    UPLOAD_FOLDER = _UPLOAD_BASE
    THUMBNAIL_FOLDER = os.path.join(_UPLOAD_BASE, "thumbnails")
    PDF_FOLDER = os.path.join(_UPLOAD_BASE, "pdfs")
    VIDEO_FOLDER = os.path.join(_UPLOAD_BASE, "videos")

    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    ALLOWED_PDF_EXTENSIONS = {"pdf"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "mkv"}

    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB (Vercel itself caps ~4.5 MB)

    CATEGORIES = [
        "Government Exams",
        "Competitive Exams",
        "Entrance Exams",
        "Academic Preparation",
        "Career Preparation",
    ]

    SEED_DEMO_DATA = True
