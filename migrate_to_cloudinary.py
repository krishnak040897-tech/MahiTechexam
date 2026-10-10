"""
migrate_to_cloudinary.py — one-time helper.
Uploads existing local uploads to Cloudinary for BOTH products AND exams,
updates the database, and removes the local file after success.

Products -> mahitech/thumbnails|pdfs|videos
Exams    -> mahitech/exams/thumbnails|pdfs   (own tree)

Run:  python migrate_to_cloudinary.py
"""
import os

import models
import storage
from config import Config

FOLDERS = {"thumbnail": Config.THUMBNAIL_FOLDER,
           "pdf": Config.PDF_FOLDER,
           "video": Config.VIDEO_FOLDER}


def _upload_row_files(row, label, columns, setter, exam=False):
    """Upload one DB row's local files to Cloudinary. Returns (migrated, failed)."""
    migrated = failed = 0
    for col, kind in columns:
        ref = row[col]
        if not ref or ref.startswith("http"):
            continue                      # already cloud / empty
        path = os.path.join(FOLDERS[kind], ref)
        if not os.path.exists(path):
            print(f"⚠ skip  {label} #{row['id']} {col}: file missing ({path})")
            continue
        print(f"→ uploading {label} #{row['id']} {col}: {ref} ...", end=" ")
        url = storage.upload_path(path, kind, exam=exam)
        if url:
            setter(row["id"], col, url)
            try:
                os.remove(path)
            except OSError:
                pass
            print("OK")
            migrated += 1
        else:
            print("FAILED")
            print("   ", storage.get_last_error())
            failed += 1
    return migrated, failed


def main():
    if not storage.cloudinary_enabled():
        print("❌ Cloudinary is not ready. Fix credentials / install the SDK first.")
        print("   Health:", storage.health())
        return

    print(f"✅ Cloudinary ready (cloud: {Config.CLOUDINARY_CLOUD_NAME}). Migrating...\n")

    total_migrated = total_failed = 0

    print("— Products (mahitech/thumbnails|pdfs|videos) —")
    for p in models.get_products():
        m, f = _upload_row_files(
            p, "product",
            (("thumbnail", "thumbnail"), ("pdf_file", "pdf"), ("video_file", "video")),
            models.set_product_file)
        total_migrated += m
        total_failed += f

    print("\n— Exams (mahitech/exams/thumbnails|pdfs) —")
    for e in models.get_exams():
        m, f = _upload_row_files(
            e, "exam",
            (("thumbnail", "thumbnail"), ("pdf_file", "pdf")),
            models.set_exam_file, exam=True)
        total_migrated += m
        total_failed += f

    print(f"\nDone. Migrated: {total_migrated}, failed: {total_failed}")
    if total_failed:
        print("Re-run this script after fixing the errors — it skips already-migrated files.")


if __name__ == "__main__":
    main()