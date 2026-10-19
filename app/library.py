"""
Cutter Library: persistent storage of generated cutters and their metadata.

Every saved cutter is a folder under LIBRARY_DIR:

    <LIBRARY_DIR>/<item_id>/
        meta.json                    # name, owner, params, stats, slicing info, file list
        cookie_cutter.stl
        cookie_cutter_<printer>.gcode  (only if slicing succeeded)
        original.<ext>               # the uploaded source image, unmodified
        mask.png                     # detected 2D mask
        thumb.jpg                    # small preview of the original image

LIBRARY_DIR is a plain directory so it works with a local Docker volume as well as
a Google Cloud Storage bucket mounted into Cloud Run (see README). meta.json is
written last, so a folder without it is an incomplete save and is ignored.
"""

import os
import re
import json
import uuid
import shutil
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from app.i18n import UserError

logger = logging.getLogger("library")

LIBRARY_DIR = Path(os.getenv("LIBRARY_DIR", str(Path(__file__).resolve().parent.parent / "data" / "library")))
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

JOB_META_FILENAME = "job.json"
META_FILENAME = "meta.json"
MAX_NAME_LENGTH = 80

_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Saved items are immutable, so metadata read once can be cached for the process lifetime.
_meta_cache: Dict[str, Dict[str, Any]] = {}

IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}


def image_extension(content_type: str, filename: Optional[str]) -> str:
    ext = IMAGE_EXTENSIONS.get((content_type or "").lower())
    if ext:
        return ext
    suffix = Path(filename or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ".img"


def make_thumbnail(image_bytes: bytes, max_side: int = 320) -> Optional[bytes]:
    """Returns a small JPEG preview of the original image (transparency flattened on white)."""
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else None


def is_valid_id(item_id: str) -> bool:
    return bool(_ID_RE.match(item_id or ""))


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "")).strip()
    if not name:
        raise UserError("name_empty")
    if len(name) > MAX_NAME_LENGTH:
        raise UserError("name_too_long", max=MAX_NAME_LENGTH)
    return name


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug[:60] or "cookie_cutter"


def save_from_job(job_dir: Path, name: str, owner: str, source_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Copies a finished generation job into the library and returns the new item's metadata.

    source_id links a cutter made from a library item's image back to that item. Links
    always point at the family's first cutter, so variants of variants stay one family.
    """
    # A source deleted in the meantime just drops the link rather than failing the save.
    source = get_item(source_id) if source_id else None
    if source:
        source = get_item(source.get("source_id")) or source

    job_meta_path = job_dir / JOB_META_FILENAME
    if not job_meta_path.exists():
        raise UserError("job_expired", status_code=404)
    job_meta = json.loads(job_meta_path.read_text(encoding="utf-8"))

    item_id = uuid.uuid4().hex
    item_dir = LIBRARY_DIR / item_id
    item_dir.mkdir(parents=True)
    try:
        files = {}
        for key, filename in job_meta.get("files", {}).items():
            if filename and (job_dir / filename).exists():
                shutil.copyfile(job_dir / filename, item_dir / filename)
                files[key] = filename
        if "stl" not in files:
            raise UserError("job_stl_missing", status_code=404)

        meta = {
            **job_meta,
            "id": item_id,
            "name": clean_name(name),
            "owner": owner,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": files,
        }
        if source:
            meta["source_id"] = source["id"]
            meta["source_name"] = source["name"]
        # Written last: its presence marks the item as complete.
        (item_dir / META_FILENAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(item_dir, ignore_errors=True)
        raise

    _meta_cache[item_id] = meta
    return meta


def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    if not is_valid_id(item_id):
        return None
    if item_id in _meta_cache:
        return _meta_cache[item_id]
    meta_path = LIBRARY_DIR / item_id / META_FILENAME
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning(f"Unreadable library item {item_id}: {e}")
        return None
    _meta_cache[item_id] = meta
    return meta


def list_items() -> List[Dict[str, Any]]:
    """Returns all complete library items, newest first."""
    ids = [p.name for p in LIBRARY_DIR.iterdir() if p.is_dir() and is_valid_id(p.name)]
    for stale in set(_meta_cache) - set(ids):
        _meta_cache.pop(stale, None)
    # Reads on a bucket mount are network round-trips, so fetch uncached metadata in parallel.
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = [m for m in pool.map(get_item, ids) if m]
    items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return items


def item_file_path(item_id: str, filename: str) -> Optional[Path]:
    """Resolves a file that belongs to a saved item; None if it is not one of its files."""
    meta = get_item(item_id)
    if not meta or filename not in meta.get("files", {}).values():
        return None
    path = LIBRARY_DIR / item_id / filename
    return path if path.exists() else None


def delete_item(item_id: str) -> None:
    _meta_cache.pop(item_id, None)
    item_dir = LIBRARY_DIR / item_id
    # Remove meta.json first so a half-deleted folder is never listed.
    (item_dir / META_FILENAME).unlink(missing_ok=True)
    shutil.rmtree(item_dir, ignore_errors=True)
