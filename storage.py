"""
storage.py — MahiTech file-storage layer.

Primary store:  Cloudinary, with DEDICATED folders:
                  products : mahitech/thumbnails   mahitech/pdfs   mahitech/videos
                  exams    : mahitech/exams/thumbnails   mahitech/exams/pdfs
                  data     : mahitech/exam-data   (readable JSON: questions,
                            attempts, results — the 'text' of the exams)
                  backups  : mahitech/exam-backups
Fallback store: local disk folders from Config.

FOLDER VISIBILITY: Cloudinary folders only appear in the console once the
first file lands in them — _ensure_cloudinary_folders() creates ALL folders
explicitly via the Admin API on startup so every folder (including
mahitech/exam-data) is visible immediately.

Image delivery: image_url() builds a SIGNED, optimized (f_auto,q_auto) URL
over the asset's REAL version.

PDF delivery: Cloudinary blocks UNSIGNED delivery of PDF/ZIP by default;
signed_url() signs over the REAL version so the URLs are accepted. The Flask
backend streams PDFs to authorized users, so the API secret never reaches
the client.
"""
import logging
import os
import tempfile
import uuid

from config import Config

try:
    import cloudinary
    import cloudinary.uploader
    import cloudinary.api
    _SDK = True
except ImportError:
    _SDK = False

log = logging.getLogger("mahitech.storage")

LAST_ERROR = None

# ---------------- local fallback folders ----------------
_DIRS = {
    "thumbnail": Config.THUMBNAIL_FOLDER,
    "pdf": Config.PDF_FOLDER,
    "video": Config.VIDEO_FOLDER,
}

# ---------------- Cloudinary folder map ----------------
_FOLDERS = {
    "thumbnail": "mahitech/thumbnails",
    "pdf": "mahitech/pdfs",
    "video": "mahitech/videos",
}
_EXAM_FOLDERS = {
    "thumbnail": "mahitech/exams/thumbnails",
    "pdf": "mahitech/exams/pdfs",
}
# readable exam DATA exports (papers, questions, attempts, results) — JSON
EXAM_DATA_FOLDER = "mahitech/exam-data"
# legacy full backups
BACKUP_FOLDER = "mahitech/exam-backups"

_RESOURCE = {"thumbnail": "image", "pdf": "raw", "video": "video"}


def _mask(s):
    """Mask a credential for safe printing: 'ClrjK...LU80' / 'EMPTY'."""
    if not s:
        return "EMPTY"
    return s[:4] + "..." + s[-4:] if len(s) > 8 else "SET"


def _ensure_cloudinary_folders():
    """
    Explicitly CREATE every folder in the Cloudinary account so all of them
    (including the exam tree, the DATA folder and the backup folder) are
    VISIBLE in the console's Media Library even BEFORE the first upload.
    Failures are ignored — an existing folder is harmless, and uploads
    create folders on demand anyway.
    """
    if not _cloud_ready:
        return
    folders = [
        _FOLDERS["thumbnail"], _FOLDERS["pdf"], _FOLDERS["video"],
        _EXAM_FOLDERS["thumbnail"], _EXAM_FOLDERS["pdf"],
        EXAM_DATA_FOLDER, BACKUP_FOLDER,
    ]
    ready = []
    for folder in folders:
        try:
            cloudinary.api.create_folder(folder)
            ready.append(folder)
        except Exception:
            pass   # already exists / not supported — harmless
    if ready:
        print("[MahiTech] Cloudinary folders present in console: " + ", ".join(ready))


_cloud_ready = bool(
    _SDK and Config.CLOUDINARY_CLOUD_NAME and Config.CLOUDINARY_API_KEY
    and Config.CLOUDINARY_API_SECRET
)

if _cloud_ready:
    cloudinary.config(
        cloud_name=Config.CLOUDINARY_CLOUD_NAME,
        api_key=Config.CLOUDINARY_API_KEY,
        api_secret=Config.CLOUDINARY_API_SECRET,
        secure=True,
    )
    print(f"[MahiTech] Storage cfg: cloud={Config.CLOUDINARY_CLOUD_NAME!r} "
          f"key={_mask(Config.CLOUDINARY_API_KEY)} "
          f"secret={_mask(Config.CLOUDINARY_API_SECRET)}")
    try:
        cloudinary.api.ping()
        print(f"[MahiTech] Cloudinary ping OK (cloud: {Config.CLOUDINARY_CLOUD_NAME})")
        _ensure_cloudinary_folders()
    except Exception as exc:
        LAST_ERROR = f"Cloudinary ping failed: {exc}"
        print(f"\n[MahiTech] ⚠ CLOUDINARY PROBLEM: {exc}\n"
              f"[MahiTech] ⚠ Check your API key/secret and cloud name.\n"
              f"[MahiTech] ⚠ Uploads will fall back to LOCAL storage.\n")
        _cloud_ready = False
elif not _SDK:
    print("[MahiTech] ⚠ cloudinary package NOT installed (pip install cloudinary) "
          "-> local storage mode.")
else:
    print("[MahiTech] ⚠ Cloudinary keys not configured -> local storage mode. "
          f"(cloud={Config.CLOUDINARY_CLOUD_NAME!r} "
          f"key={_mask(Config.CLOUDINARY_API_KEY)} "
          f"secret={_mask(Config.CLOUDINARY_API_SECRET)})")


def cloudinary_enabled():
    return _cloud_ready


def get_last_error():
    return LAST_ERROR


def health():
    return {
        "cloudinary_enabled": _cloud_ready,
        "cloud_name": Config.CLOUDINARY_CLOUD_NAME if _cloud_ready else None,
        "sdk_installed": _SDK,
        "last_error": LAST_ERROR,
        "folders": {
            "products": "mahitech/thumbnails | mahitech/pdfs | mahitech/videos",
            "exams": "mahitech/exams/thumbnails | mahitech/exams/pdfs",
            "exam_data": EXAM_DATA_FOLDER,
            "backups": BACKUP_FOLDER,
        },
    }


def _uniq_name(filename):
    from werkzeug.utils import secure_filename
    return f"{uuid.uuid4().hex[:10]}_{secure_filename(filename or 'file')}"


def _parse_cloudinary_url(url):
    """
    Split a Cloudinary delivery URL into its parts.
    Returns (resource_type, public_id, version_or_None).
    The public_id KEEPS its extension (required for raw files such as PDFs).
    """
    parts = url.split("/")
    if "upload" not in parts:
        return None, None, None
    i = parts.index("upload")
    rtype = parts[i - 1] if i and parts[i - 1] in ("image", "raw", "video") else "image"
    tail = parts[i + 1:]
    version = None
    if tail and tail[0].startswith("v") and tail[0][1:].isdigit():
        version = tail.pop(0)[1:]        # keep the REAL version (e.g. 1791520282)
    return rtype, "/".join(tail), version


def signed_url(reference, download=False):
    """
    Build a SIGNED Cloudinary URL for a stored reference (PDFs / videos /
    raw JSON data files). The signature covers the asset's REAL version.
    download=True adds fl_attachment (forces browser download).
    """
    if not reference or not reference.startswith("http") or not _cloud_ready:
        return reference
    rtype, public_id, version = _parse_cloudinary_url(reference)
    if not public_id:
        return reference
    try:
        import cloudinary.utils
        opts = {
            "resource_type": rtype or "raw",
            "type": "upload",
            "sign_url": True,
            "secure": True,
            "force_version": False,          # never inject a fake v1
        }
        if version:
            opts["version"] = version        # sign over the REAL version
        if download:
            opts["attachment"] = True        # fl_attachment
        url, _unused = cloudinary.utils.cloudinary_url(public_id, **opts)
        return url
    except Exception as exc:
        log.warning("Signed URL build failed for %s: %s", reference, exc)
        return reference


def image_url(reference):
    """
    Build a SIGNED, OPTIMIZED (f_auto,q_auto) delivery URL for a stored
    Cloudinary IMAGE (thumbnails). Returns None when unusable.
    """
    if not reference or not reference.startswith("http") or not _cloud_ready:
        return None
    rtype, public_id, version = _parse_cloudinary_url(reference)
    if not public_id:
        return None
    try:
        import cloudinary.utils
        opts = {
            "resource_type": "image",
            "type": "upload",
            "sign_url": True,
            "secure": True,
            "force_version": False,
            "fetch_format": "auto",       # -> f_auto
            "quality": "auto",            # -> q_auto
        }
        if version:
            opts["version"] = version     # sign over the REAL version
        url, _unused = cloudinary.utils.cloudinary_url(public_id, **opts)
        return url
    except Exception as exc:
        log.warning("Image URL build failed for %s: %s", reference, exc)
        return None


def upload_path(path, kind, exam=False):
    """
    Upload a file already on disk. Returns the secure URL, or None on failure.
    kind : 'thumbnail' | 'pdf' | 'video'
    exam : True -> Cloudinary folder mahitech/exams/...
    """
    global LAST_ERROR
    if not _cloud_ready:
        return None
    folders = _EXAM_FOLDERS if (exam and kind in _EXAM_FOLDERS) else _FOLDERS
    try:
        if kind == "thumbnail":
            res = cloudinary.uploader.upload(
                path, folder=folders[kind], resource_type="image")
        else:
            res = cloudinary.uploader.upload_large(
                path, folder=folders[kind], resource_type=_RESOURCE[kind])
        url = res.get("secure_url")
        if url:
            log.info("Uploaded %s -> %s", path, url)
            LAST_ERROR = None
            return url
        LAST_ERROR = "Cloudinary returned no URL."
        log.warning(LAST_ERROR)
        return None
    except Exception as exc:
        LAST_ERROR = f"Cloudinary upload failed: {exc}"
        log.warning(LAST_ERROR)
        print(f"[MahiTech] ⚠ {LAST_ERROR}")
        return None


def upload_bytes(data, filename, folder, resource_type="raw"):
    """
    Upload raw BYTES (JSON exam-data / backups) to Cloudinary.
    Returns the secure URL, or None on failure (caller should save locally).
    """
    global LAST_ERROR
    if not _cloud_ready:
        return None
    from werkzeug.utils import secure_filename
    safe = secure_filename(filename or "file")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_" + safe)
    try:
        tmp.write(data)
        tmp.close()
        res = cloudinary.uploader.upload(
            tmp.name, folder=folder, resource_type=resource_type,
            public_id=safe)
        url = res.get("secure_url")
        if url:
            log.info("Uploaded bytes -> %s", url)
            LAST_ERROR = None
            return url
        LAST_ERROR = "Cloudinary returned no URL."
        return None
    except Exception as exc:
        LAST_ERROR = f"Cloudinary upload failed: {exc}"
        print(f"[MahiTech] ⚠ {LAST_ERROR}")
        return None
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def save_file(fs, kind, exam=False):
    """
    Store an uploaded FileStorage.  kind: 'thumbnail' | 'pdf' | 'video'.
    exam=True -> stored under the mahitech/exams/... Cloudinary tree.
    Returns the DB reference (Cloudinary URL or local filename), or None.
    """
    global LAST_ERROR
    if fs is None or not fs.filename:
        return None
    LAST_ERROR = None

    if _cloud_ready:
        suffix = "_" + _uniq_name(fs.filename)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        try:
            fs.save(tmp_path)
            url = upload_path(tmp_path, kind, exam=exam)
            if url:
                return url
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # ---- local fallback ----
    os.makedirs(_DIRS[kind], exist_ok=True)
    name = _uniq_name(fs.filename)
    fs.save(os.path.join(_DIRS[kind], name))
    return name


def delete_file(kind, reference):
    """
    Delete a stored file (Cloudinary URL or local filename). Never raises.
    (For http references the cloud asset is destroyed regardless of kind.)
    """
    if not reference:
        return
    if reference.startswith("http"):
        if not _cloud_ready:
            return
        rtype, public_id, _version = _parse_cloudinary_url(reference)
        if not public_id:
            return
        try:
            cloudinary.uploader.destroy(
                public_id, resource_type=rtype or "image", invalidate=True)
        except Exception as exc:
            log.warning("Cloudinary delete failed for %s: %s", reference, exc)
        return
    path = os.path.join(_DIRS[kind], reference)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass